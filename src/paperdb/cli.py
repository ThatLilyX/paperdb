"""The command line: init, add, sync, bibtex."""

from __future__ import annotations

import sys
import time
from pathlib import Path

import click

from . import __version__, bibtex, config, identify, names, notion
from .annotations import extract
from .citekey import CitekeyError, mint
from .config import Config, ConfigError
from .metadata import (
    ARXIV_CRAWL_DELAY_SECONDS,
    NotAPdfError,
    Paper,
    ResolveError,
    classify,
    fetch_pdf,
    resolve,
)


def fail(message: str) -> None:
    click.secho(f"error: {message}", fg="red", err=True)
    sys.exit(1)


def _loaded() -> tuple[Config, notion.Notion]:
    try:
        settings = config.load()
        token = config.token()
    except ConfigError as error:
        fail(str(error))
    if not settings.database_id:
        fail("no database_id in the config. Run `paperdb init` again.")
    if not settings.papers_dir.is_dir():
        fail(f"{settings.papers_dir} does not exist. Run `paperdb init` again.")
    return settings, notion.Notion(token)


def _index(client: notion.Notion, database_id: str) -> dict[str, dict]:
    """Every row by citekey. A row with no citekey is skipped and counted."""
    rows: dict[str, dict] = {}
    for row in client.rows(database_id):
        key = notion.row_text(row, "Citekey")
        if key:
            rows[key] = row
    return rows


@click.group(context_settings={"help_option_names": ["-h", "--help"]})
@click.version_option(__version__)
def main() -> None:
    """Keep a folder of paper PDFs and a Notion database in step."""


@main.command()
@click.argument("folder", required=False, type=click.Path(file_okay=False, path_type=Path))
@click.option("--parent-page", help="Notion page id to create the database under.")
@click.option("--database", "database_id", help="Use an existing database instead.")
@click.option("--schedule-db", help="Relate the papers database to this reading-schedule database.")
@click.option("--email", default="", help="Sent to OpenAlex and Unpaywall for the polite pool.")
def init(folder, parent_page, database_id, schedule_db, email) -> None:
    """Point paperdb at a papers folder and a Notion database."""
    papers_dir = (folder or config.DEFAULT_PAPERS_DIR).expanduser().resolve()
    papers_dir.mkdir(parents=True, exist_ok=True)

    if not database_id and not parent_page:
        fail("pass --parent-page <notion page id> to create the database, or --database <id>.")

    try:
        client = notion.Notion(config.token())
    except ConfigError as error:
        fail(str(error))

    with client:
        if not database_id:
            database_id = client.create_database(parent_page)
            click.echo(f"created the Papers database: {database_id}")
        if schedule_db:
            client.add_schedule_relation(database_id, schedule_db)
            click.echo("related it to your reading schedule as `Plan`")

    path = config.save(Config(papers_dir=papers_dir, database_id=database_id, email=email))
    click.echo(f"papers folder: {papers_dir}")
    click.echo(f"wrote {path}")


@main.command()
@click.argument("identifier")
@click.option("--citekey", "forced_key", help="Use this key instead of minting one.")
@click.option("--no-download", is_flag=True, help="Record the paper without fetching a PDF.")
def add(identifier, forced_key, no_download) -> None:
    """Add one paper by arXiv id, DOI or URL."""
    settings, client = _loaded()
    with client:
        try:
            paper = resolve(identifier, email=settings.email)
        except ResolveError as error:
            fail(str(error))

        taken = set(_index(client, settings.database_id))
        if forced_key:
            if forced_key in taken:
                fail(f"{forced_key} is already in the database")
            paper.citekey = forced_key
        else:
            try:
                paper.citekey = mint(paper.first_author, paper.year, paper.title, taken)
            except CitekeyError as error:
                fail(
                    f"{error}. Pass --citekey to set one by hand: the key is permanent, "
                    "so paperdb will not invent a placeholder."
                )

        filename = names.build(paper.citekey, paper.title)
        destination = settings.papers_dir / filename
        highlights: list[str] = []

        if not no_download:
            if not paper.pdf_url:
                click.secho(
                    "no open-access PDF is on offer for this paper. Recording the row "
                    "without a file; download it yourself into the folder as "
                    f"{filename!r} and run `paperdb sync`.",
                    fg="yellow",
                )
            else:
                try:
                    destination.write_bytes(fetch_pdf(paper.pdf_url))
                    click.echo(f"downloaded {filename}")
                    highlights = extract(destination)
                except NotAPdfError as error:
                    click.secho(f"could not download a PDF: {error}", fg="yellow")

        page_id = client.create_row(
            settings.database_id, paper, filename if destination.exists() else "", highlights
        )
        click.secho(f"{paper.citekey}  {paper.title}", fg="green")
        click.echo(f"  https://notion.so/{page_id.replace('-', '')}")


@main.command()
@click.option("--dry-run", is_flag=True, help="Report what would change and change nothing.")
@click.option("--no-highlights", is_flag=True, help="Skip re-reading annotations.")
def sync(dry_run, no_highlights) -> None:
    """Reconcile the folder and the database, in both directions."""
    settings, client = _loaded()
    plan: list[str] = []
    orphan_files: list[str] = []
    orphan_rows: list[str] = []

    with client:
        rows = _index(client, settings.database_id)
        seen: set[str] = set()
        pdfs = sorted(p for p in settings.papers_dir.glob("*.pdf") if p.is_file())
        fetched_from_arxiv = False

        for pdf in pdfs:
            key = names.citekey_in(pdf.name)

            if key and key in rows:
                seen.add(key)
                row = rows[key]
                wanted = names.build(key, notion.row_text(row, "Name"))
                if pdf.name != wanted:
                    plan.append(f"rename  {pdf.name}  ->  {wanted}")
                    if not dry_run:
                        pdf.rename(settings.papers_dir / wanted)
                        client.set_filename(row["id"], wanted)
                        pdf = settings.papers_dir / wanted
                elif notion.row_text(row, "Filename") != pdf.name:
                    plan.append(f"repair  Filename for {key}")
                    if not dry_run:
                        client.set_filename(row["id"], pdf.name)
                if not no_highlights:
                    found = extract(pdf)
                    if dry_run:
                        if found:
                            plan.append(f"check   highlights for {key} ({len(found)} found)")
                    elif client.replace_highlights(row["id"], found):
                        plan.append(f"update  highlights for {key} ({len(found)})")
                continue

            if key:
                orphan_files.append(f"{pdf.name}  (keyed {key}, no row in Notion)")
                continue

            identifier = identify.identify(pdf)
            if not identifier:
                orphan_files.append(f"{pdf.name}  (no identifier in the name or the file)")
                continue

            if classify(identifier)[0] == "arxiv" and fetched_from_arxiv:
                time.sleep(0 if dry_run else ARXIV_CRAWL_DELAY_SECONDS)
            try:
                paper = resolve(identifier, email=settings.email)
                fetched_from_arxiv = fetched_from_arxiv or classify(identifier)[0] == "arxiv"
            except ResolveError as error:
                orphan_files.append(f"{pdf.name}  ({error})")
                continue

            try:
                paper.citekey = mint(
                    paper.first_author, paper.year, paper.title, set(rows) | seen
                )
            except CitekeyError as error:
                orphan_files.append(f"{pdf.name}  ({error})")
                continue

            wanted = names.build(paper.citekey, paper.title)
            plan.append(f"adopt   {pdf.name}  ->  {wanted}")
            seen.add(paper.citekey)
            if not dry_run:
                pdf.rename(settings.papers_dir / wanted)
                found = [] if no_highlights else extract(settings.papers_dir / wanted)
                client.create_row(settings.database_id, paper, wanted, found)

        for key, row in rows.items():
            if key not in seen:
                orphan_rows.append(f"{key}  {notion.row_text(row, 'Name')[:60]}")

    verb = "would" if dry_run else "did"
    click.echo(f"{len(plan)} change(s) paperdb {verb} make:")
    for line in plan or ["  (nothing)"]:
        click.echo(f"  {line}")

    if orphan_files:
        click.secho(f"\n{len(orphan_files)} file(s) with no row:", fg="yellow")
        for line in orphan_files:
            click.echo(f"  {line}")
        click.echo("  Add one by hand with: paperdb add <id> --citekey <key>")
    if orphan_rows:
        click.secho(f"\n{len(orphan_rows)} row(s) with no file:", fg="yellow")
        for line in orphan_rows:
            click.echo(f"  {line}")


@main.command(name="bibtex")
@click.option("--out", type=click.Path(dir_okay=False, path_type=Path), help="Where to write.")
@click.option("--stdout", "to_stdout", is_flag=True, help="Print instead of writing.")
def bibtex_command(out, to_stdout) -> None:
    """Write library.bib from the database."""
    settings, client = _loaded()
    with client:
        papers = [notion.row_paper(row) for row in client.rows(settings.database_id)]

    keyed = [p for p in papers if p.citekey]
    text = bibtex.render(keyed)
    if to_stdout:
        click.echo(text, nl=False)
        return
    destination = out or settings.bib_path
    destination.write_text(text)
    click.echo(f"wrote {len(keyed)} entries to {destination}")
    if len(keyed) != len(papers):
        click.secho(
            f"{len(papers) - len(keyed)} row(s) have no citekey and were skipped", fg="yellow"
        )


if __name__ == "__main__":
    main()
