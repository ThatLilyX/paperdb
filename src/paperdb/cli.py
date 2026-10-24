"""The command line: init, add, sync, bibtex."""

from __future__ import annotations

import sys
import time
from pathlib import Path

import click

from . import __version__, bibtex, config, identify, names, notion
from .annotations import extract
from .citekey import CitekeyError, is_citekey, mint
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


def _pdfs(folder: Path) -> list[Path]:
    """Every PDF in the folder, whatever the case of its extension."""
    return sorted(p for p in folder.iterdir() if p.suffix.lower() == ".pdf" and p.is_file())


def _by_key(pdfs: list[Path]) -> dict[str, list[Path]]:
    """The PDFs that carry a citekey, grouped by it. A group of two is a duplicate."""
    grouped: dict[str, list[Path]] = {}
    for pdf in pdfs:
        key = names.citekey_in(pdf.name)
        if key:
            grouped.setdefault(key, []).append(pdf)
    return grouped


def _primary(copies: list[Path], recorded: str = "", wanted: str = "") -> Path:
    """Which of several files carrying one key is the paper's own.

    The one the row records, then the one already at the right name, then the
    shortest name, which passes over a browser's ``(1)`` copy.
    """
    return min(copies, key=lambda p: (p.name != recorded, p.name != wanted, len(p.name), p.name))


class FolderNames:
    """The names in one folder, as a move into it has to see them.

    Matched without case, as on macOS, but every spelling is kept, so on a
    case-sensitive disk two names differing only in case still block each other.
    """

    def __init__(self, held):
        self._held: dict[str, set[str]] = {}
        for name in held:
            self._held.setdefault(name.casefold(), set()).add(name)

    def taken_by(self, wanted: str, current: str | None = None) -> str | None:
        """The other file already holding ``wanted``, if there is one.

        ``current`` is the moving file's own name when it is already in this
        folder, and None when it is arriving from another.
        """
        others = self._held.get(wanted.casefold(), set()) - {current}
        return min(others) if others else None

    def move(self, current: str | None, wanted: str | None) -> None:
        """Record a file leaving (``current``), arriving (``wanted``) or both."""
        if current:
            self._held[current.casefold()].discard(current)
        if wanted:
            self._held.setdefault(wanted.casefold(), set()).add(wanted)


def _move(source: Path, target: Path) -> None:
    """Rename, refusing to replace a file that is already there.

    ``Path.rename`` replaces silently on macOS and Linux. The check sits here,
    right before the rename, because a sync can run for minutes.
    """
    if target.exists() and not source.samefile(target):
        raise FileExistsError(target.name)
    source.rename(target)


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
    (papers_dir / config.INBOX_NAME).mkdir(exist_ok=True)

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

        on_disk = _by_key(_pdfs(settings.papers_dir))
        taken = set(_index(client, settings.database_id))
        if forced_key is not None:
            if not is_citekey(forced_key):
                fail(
                    f"{forced_key!r} is not a citekey. Use lowercase letters and digits "
                    "with a four-digit year, like smith2024graph."
                )
            if forced_key in taken:
                fail(f"{forced_key} is already in the database")
            paper.citekey = forced_key
        else:
            try:
                paper.citekey = mint(
                    paper.first_author, paper.year, paper.title, taken | set(on_disk)
                )
            except CitekeyError as error:
                fail(
                    f"{error}. Pass --citekey to set one by hand: the key is permanent, "
                    "so paperdb will not invent a placeholder."
                )

        filename = names.build(paper.citekey, paper.title)
        destination = settings.papers_dir / filename
        recorded = ""
        highlights: list[str] = []

        if paper.citekey in on_disk:
            # The file is already here, usually an orphan sync reported. Record it
            # rather than download over it and lose its highlights.
            recorded = _primary(on_disk[paper.citekey], wanted=filename).name
            highlights = extract(settings.papers_dir / recorded)
            click.echo(f"using {recorded}, already in the folder")
        elif not no_download:
            if not paper.pdf_url:
                click.secho(
                    "no open-access PDF is on offer for this paper. Recording the row "
                    "without a file; download it yourself into the folder as "
                    f"{filename!r} and run `paperdb sync`.",
                    fg="yellow",
                )
            else:
                try:
                    data = fetch_pdf(paper.pdf_url)
                    with destination.open("xb") as out:
                        out.write(data)
                    recorded = filename
                    click.echo(f"downloaded {filename}")
                    highlights = extract(destination)
                except NotAPdfError as error:
                    click.secho(f"could not download a PDF: {error}", fg="yellow")
                except FileExistsError:
                    click.secho(
                        f"{filename} is already in the folder, so it was left alone and the "
                        "row records no file. Check it, then run `paperdb sync`.",
                        fg="yellow",
                    )

        page_id = client.create_row(settings.database_id, paper, recorded, highlights)
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
    conflicts: list[str] = []

    folder = settings.papers_dir
    inbox = settings.inbox_dir

    with client:
        rows = _index(client, settings.database_id)
        seen: set[str] = set()
        pdfs = _pdfs(folder)
        keyed = _by_key(pdfs)
        waiting = _pdfs(inbox) if inbox.is_dir() else []
        held = FolderNames(p.name for p in pdfs)
        held_in_inbox = FolderNames(p.name for p in waiting)
        by_identity = {
            i: key for key, r in rows.items() for i in notion.row_paper(r).identities()
        }
        # Where each inbox file is shown as being. In a dry run a stray is never
        # moved, but the report describes it from where it would be.
        shown = {pdf: f"Inbox/{pdf.name}" for pdf in waiting}

        def name_of(pdf: Path) -> str:
            return shown.get(pdf, pdf.name)

        def taken(pdf: Path, wanted: str, holder: str) -> None:
            conflicts.append(f"{name_of(pdf)}  (wants {wanted}, already taken by {holder})")

        def place(pdf: Path, wanted: str, verb: str) -> Path | None:
            """Move ``pdf`` into the library as ``wanted``, or report why not."""
            current = pdf.name if pdf.parent == folder and pdf not in shown else None
            holder = held.taken_by(wanted, current)
            if holder is None and not dry_run:
                try:
                    _move(pdf, folder / wanted)
                except FileExistsError:
                    holder = wanted
            if holder:
                taken(pdf, wanted, holder)
                return None
            plan.append(f"{verb}{name_of(pdf)}  ->  {wanted}")
            held.move(current, wanted)
            return folder / wanted

        def settle(pdf: Path, key: str, verb: str) -> None:
            """Bind ``pdf`` to its row: name, Filename and highlights."""
            row = rows[key]
            seen.add(key)
            wanted = names.build(key, notion.row_text(row, "Name"))
            if pdf.name != wanted or pdf in shown:
                moved = place(pdf, wanted, verb)
                if moved is None:
                    return
                if not dry_run:
                    client.set_filename(row["id"], wanted)
                    pdf = moved
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

        # Anything unprocessed in the library goes to the inbox first, so the
        # library only ever holds filed papers.
        for pdf in pdfs:
            if names.citekey_in(pdf.name):
                continue
            holder = held_in_inbox.taken_by(pdf.name)
            if holder:
                conflicts.append(f"{pdf.name}  (wants Inbox/, already taken by Inbox/{holder})")
                continue
            if not dry_run:
                inbox.mkdir(exist_ok=True)
                try:
                    _move(pdf, inbox / pdf.name)
                except FileExistsError:
                    conflicts.append(
                        f"{pdf.name}  (wants Inbox/, already taken by Inbox/{pdf.name})"
                    )
                    continue
            plan.append(f"inbox   {pdf.name}  ->  Inbox/")
            held.move(pdf.name, None)
            held_in_inbox.move(None, pdf.name)
            moved = pdf if dry_run else inbox / pdf.name
            shown[moved] = f"Inbox/{pdf.name}"
            waiting.append(moved)

        # Keyed files next, so every row's own file is bound before an inbox copy
        # of the same paper can claim it.
        for key, copies in sorted(keyed.items()):
            if key not in rows:
                for pdf in copies:
                    orphan_files.append(f"{pdf.name}  (keyed {key}, no row in Notion)")
                continue
            row = rows[key]
            own = _primary(
                copies,
                recorded=notion.row_text(row, "Filename"),
                wanted=names.build(key, notion.row_text(row, "Name")),
            )
            for pdf in copies:
                if pdf != own:
                    conflicts.append(f"{pdf.name}  (another copy of {key}, which has {own.name})")
            settle(own, key, "rename  ")

        fetched_from_arxiv = False
        for pdf in waiting:
            key = names.citekey_in(pdf.name)
            if key:
                if key in seen:
                    conflicts.append(f"{name_of(pdf)}  (another copy of {key}, which has a file)")
                elif key in rows:
                    settle(pdf, key, "file    ")
                else:
                    orphan_files.append(f"{name_of(pdf)}  (keyed {key}, no row in Notion)")
                continue

            identifier = identify.identify(pdf)
            if not identifier:
                orphan_files.append(f"{name_of(pdf)}  (no identifier in the name or the file)")
                continue

            if classify(identifier)[0] == "arxiv" and fetched_from_arxiv:
                time.sleep(0 if dry_run else ARXIV_CRAWL_DELAY_SECONDS)
            try:
                paper = resolve(identifier, email=settings.email)
                fetched_from_arxiv = fetched_from_arxiv or classify(identifier)[0] == "arxiv"
            except ResolveError as error:
                orphan_files.append(f"{name_of(pdf)}  ({error})")
                continue

            known = next(
                (by_identity[i] for i in sorted(paper.identities()) if i in by_identity), None
            )
            if known in seen:
                conflicts.append(f"{name_of(pdf)}  (another copy of {known}, which has a file)")
                continue
            if known:
                # Already in the database: bind the file to that row, never a second one.
                settle(pdf, known, "adopt   ")
                continue

            try:
                paper.citekey = mint(
                    paper.first_author, paper.year, paper.title, set(rows) | seen | set(keyed)
                )
            except CitekeyError as error:
                orphan_files.append(f"{name_of(pdf)}  ({error})")
                continue

            wanted = names.build(paper.citekey, paper.title)
            holder = held.taken_by(wanted)
            if holder:
                taken(pdf, wanted, holder)
                continue
            plan.append(f"adopt   {name_of(pdf)}  ->  {wanted}")
            held.move(None, wanted)
            seen.add(paper.citekey)
            by_identity.update(dict.fromkeys(paper.identities(), paper.citekey))
            if not dry_run:
                # The row first: if Notion fails, the file stays in the inbox and
                # the next sync binds it to the row by its identifier.
                found = [] if no_highlights else extract(pdf)
                client.create_row(settings.database_id, paper, wanted, found)
                try:
                    _move(pdf, folder / wanted)
                except FileExistsError:
                    taken(pdf, wanted, wanted)

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
        click.echo(
            "  Rename one in Inbox/ to its arXiv id or DOI and sync again, "
            "or add it with: paperdb add <id>"
        )
    if conflicts:
        click.secho(
            f"\n{len(conflicts)} file(s) left alone, so nothing is overwritten:", fg="yellow"
        )
        for line in conflicts:
            click.echo(f"  {line}")
        click.echo("  Usually a duplicate download. Keep the one you want and delete the other.")
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
