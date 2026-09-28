# paperdb

Keep a folder of paper PDFs and a Notion database in step, with the citation key
as the name they share.

```
$ paperdb add 2401.01234
downloaded lastname2024keyword A Paper Title.pdf
lastname2024keyword  A Paper Title
  https://notion.so/1f2a...
```

The PDF lands in your folder under its citekey. The Notion row carries the
authors, venue, year, identifiers and the filename, and its page body carries the
abstract. `paperdb sync` walks the folder afterwards, so papers you dropped in by
hand get rows too, files whose row was retitled get renamed, and highlights you
made in a reader show up under the abstract.

No PDF is ever uploaded to Notion. The row records the filename, relative to your
folder, and the file stays where you put it.

## Install

```
pip install -e .
export NOTION_TOKEN=...            # https://www.notion.so/my-integrations
paperdb init ~/Papers --parent-page <notion page id>
```

Share the parent page with your integration first, or Notion refuses to create
the database under it.

## Commands

```
paperdb init [FOLDER]     point paperdb at a folder and a Notion database
paperdb add <IDENTIFIER>  add one paper by arXiv id or DOI
paperdb sync              reconcile the folder and the database, both ways
paperdb bibtex            write library.bib from the database
```

`paperdb sync --dry-run` prints every rename and every row it would create,
and changes nothing. Run it first on a folder that already has papers in it.

`paperdb <command> --help` has the rest. The full contract for each command,
the citekey rules and the design reasoning live in
[`docs/specs/2026-09-28-paperdb-design.md`](docs/specs/2026-09-28-paperdb-design.md),
which is the one place they are stated.

## Requires

Python 3.11 or newer, `httpx`, `click`, `pypdf`. A Notion integration token.
No account is needed for arXiv, OpenAlex or Unpaywall.
