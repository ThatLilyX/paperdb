# paperdb

Keep a folder of paper PDFs and a Notion database in step, with the citation key as
the name they share.

`paperdb add <arXiv id>` resolves the paper's metadata, downloads the open-access PDF
into your folder as `lastname2024keyword Paper Title.pdf`, and creates a Notion row
carrying the abstract, the authors, the venue and that same `lastname2024keyword`.
`paperdb sync` walks the folder afterwards, so papers you dropped in by hand get rows
too, and rows whose title you edited get their file renamed.

No PDF is ever uploaded to Notion. The row records the filename; the file stays where
you put it.

## Status

Design stage. Nothing is implemented. The design lives in
[`docs/specs/2026-09-28-paperdb-design.md`](docs/specs/2026-09-28-paperdb-design.md).

## Commands

| Command | What it does |
|---|---|
| `paperdb init [folder]` | Point paperdb at a papers folder and a Notion database, once. Creates the database if it is not there. Defaults to `~/Papers`. |
| `paperdb add <arXiv id \| DOI \| URL>` | Resolve, mint the citekey, download the PDF, create the row, write the page body. |
| `paperdb sync` | Reconcile folder and database in both directions. Renames drifted files, creates rows for new PDFs, refreshes highlights, reports orphans. |
| `paperdb bibtex` | Emit `library.bib` from the database. |

## Configuration

`init` writes `~/.config/paperdb/config.toml`:

```toml
papers_dir  = "~/Papers"
database_id = "..."
```

The Notion token comes from `NOTION_TOKEN` in the environment, never the config file.
