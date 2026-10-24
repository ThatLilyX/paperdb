# paperdb

Keep a folder of paper PDFs and a Notion database in step.

```
$ paperdb add 2401.01234
downloaded smith2024graph Graph Neural Networks at Scale.pdf
smith2024graph  Graph Neural Networks at Scale
  https://notion.so/1f2a...
```

Each paper gets a citation key such as `smith2024graph` (first author's surname,
year, first meaningful title word). The PDF is saved under that key, and a row in
Notion records the authors, venue, year, identifiers and abstract. PDFs stay in
your folder; nothing is uploaded to Notion.

```
~/Papers/                      filed papers, each named by its citekey
  smith2024graph Graph Neural Networks at Scale.pdf
  library.bib
  Inbox/                       drop new PDFs here; anything paperdb can't file stays here
    2305.18290.pdf
```

## Setup

Requires Python 3.11 or newer and a Notion integration token.

```
pip install -e .
export NOTION_TOKEN=...            # https://www.notion.so/my-integrations
paperdb init ~/Papers --parent-page <notion page id>
```

Share the parent page with your integration first, or Notion refuses to create
the database under it. To use a database you already have, pass
`--database <id>` instead of `--parent-page`.

## Commands

| Command | What it does |
| --- | --- |
| `paperdb init [FOLDER]` | Set up the papers folder and the Notion database |
| `paperdb add <ID>` | Add one paper by arXiv id, DOI or URL |
| `paperdb sync` | Bring the folder and the database back in step |
| `paperdb bibtex` | Write `library.bib` beside your PDFs |

`paperdb <command> --help` lists every option.

### add

Downloads the open-access PDF if there is one and creates the Notion row. If no
free PDF exists, the row is still created and paperdb tells you the filename to
save the PDF under yourself.

- `--citekey <key>` sets the key by hand. A key already in use is refused.
- `--no-download` records the paper without fetching the PDF.

If two papers would get the same key, the second becomes `smith2024graph-a`.
Keys never change once given, even if you retitle the paper.

### sync

| You did this | sync does this |
| --- | --- |
| Dropped a PDF into `Inbox/` | Identifies it, adds a row and files it into the library |
| Dropped a PDF straight into the library | Moves it to `Inbox/`, then files it the same way |
| Retitled a paper in Notion | Renames the file to match |
| Highlighted a PDF in your reader | Copies the highlights to the page's `Highlights` section |
| Have a PDF it can't identify | Leaves it in `Inbox/` and lists it. Rename it to its arXiv id or DOI (e.g. `2305.18290.pdf`) and sync again |
| Deleted a row, or a file | Lists the one left behind, and changes neither |

Run `paperdb sync --dry-run` first on a folder that already has papers in it. It
prints every change and makes none.

> **Note:** sync rewrites the `Highlights` section from the PDF every time, so
> anything you type under that heading is lost. Keep your own notes above it.

`--no-highlights` skips reading annotations, which is the slow part on a large
folder.

### bibtex

Writes `library.bib` into your papers folder. `--out <path>` writes elsewhere;
`--stdout` prints it.
