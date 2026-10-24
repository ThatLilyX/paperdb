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
~/Papers/
  db/                          filed papers, each named by its citekey
    smith2024graph Graph Neural Networks at Scale.pdf
  Inbox/                       drop new PDFs here; anything paperdb can't file stays here
    2305.18290.pdf
  library.bib
```

## Setup

Requires Python 3.11 or newer and a Notion integration token. `summarize` also
needs [Claude Code](https://claude.com/claude-code), logged in.

```
pip install -e .
export NOTION_TOKEN=...            # https://www.notion.so/my-integrations
paperdb init ~/Papers --parent-page <notion page id>
```

Share the parent page with your integration first, or Notion refuses to create
the database under it. To use a database you already have, pass
`--database <id>` instead of `--parent-page`. `init` also creates `db/` and
`Inbox/` inside the folder.

## Commands

| Command | What it does |
| --- | --- |
| `paperdb init [FOLDER]` | Set up the papers folder and the Notion database |
| `paperdb add <ID>` | Add one paper by arXiv id, DOI, or arxiv.org / doi.org link |
| `paperdb sync` | Bring the folder and the database back in step |
| `paperdb summarize [CITEKEY...]` | Have Claude read each paper and fill in "What to focus on" |
| `paperdb bibtex` | Write `library.bib` into `~/Papers` |

`paperdb <command> --help` lists every option.

### add

Downloads the open-access PDF into `db/` if there is one and creates the Notion
row. If no free PDF exists, the row is still created and paperdb tells you the
filename to save the PDF under in `Inbox/`.

- `--citekey <key>` sets the key by hand. A key already in use is refused. If a
  file carrying that key is already in the folder, it is recorded instead of
  downloading a new copy.
- `--no-download` records the paper without fetching the PDF.

If two papers would get the same key, the second becomes `smith2024graph-a`.
Keys never change once given, even if you retitle the paper.

### sync

| You did this | sync does this |
| --- | --- |
| Dropped a PDF into `Inbox/` | Identifies it, adds a row and files it into `db/` |
| Dropped a PDF anywhere else in `~/Papers` | Moves it to `Inbox/`, then files it the same way |
| Downloaded a second copy of a filed paper | Deletes the copy (moves it to the Trash) and lists it |
| Highlighted a second copy, not the filed one | Keeps the copy in `Inbox/` and lists it, so no highlight is lost |
| Retitled a paper in Notion | Renames the file to match |
| Highlighted a PDF in your reader | Copies the highlights to the page's `Highlights` section |
| Have a PDF it can't identify | Leaves it in `Inbox/` and lists it. Rename it to its arXiv id (`2305.18290.pdf`) or its DOI with `_` for `/` (`10.1038_nature12373.pdf`) and sync again |
| Deleted a row | Lists the file left behind and changes nothing. `paperdb add <id> --citekey <key>` gives it a row again |
| Deleted a file | Lists the row left behind and changes nothing |

Run `paperdb sync --dry-run` first on a folder that already has papers in it. It
prints every rename, move and deletion, and makes none.

sync never overwrites a file. If a name is already taken, the file stays where
it is and is listed.

> **Note:** sync rewrites the `Highlights` section from the PDF every time, so
> anything you type under that heading is lost. Keep your own notes above it.

`--no-highlights` skips reading annotations, which is the slow part on a large
folder.

### summarize

Claude reads the whole PDF, every page, and writes 3–6 one-sentence bullets on
what is new in the paper under the page's **What to focus on** heading.

- With no citekeys, it fills every page whose section is still empty. A section
  that already has text is kept; `--force` replaces it.
- `--dry-run` prints the bullets and writes nothing.
- `--model <name>` picks the Claude model; otherwise your Claude Code default.

Each paper prints its time and cost. A 5-page paper took about 30 seconds and
cost about $0.58 on Opus at list price; on a Claude subscription it counts
against your usage instead. Claude can only read files in `db/`, never change
them, and stops at $3 per paper. sync never touches this section.

### bibtex

Writes `library.bib` into `~/Papers`, one entry per row, keyed by citekey, so
`\cite{smith2024graph}` works in LaTeX. Rerun it after adding papers.
`--out <path>` writes elsewhere; `--stdout` prints it.

## Limits

- A paper needs an arXiv id or a DOI. One published only on OpenReview, with
  neither, stays in `Inbox/`.
- A preprint is cited as a preprint, even if a published version exists.
