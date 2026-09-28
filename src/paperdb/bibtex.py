"""Rendering the database as a .bib file.

Zotero and LaTeX are consumers here, never the source of truth: the citekey in
the emitted entry is the one already stored in Notion, so a citation written
against a filename resolves to the same key.
"""

from __future__ import annotations

from .metadata import Paper

# Characters TeX reads as markup. Escaped rather than dropped so a title keeps
# its meaning.
_ESCAPES = {
    "\\": r"\textbackslash{}",
    "{": r"\{",
    "}": r"\}",
    "$": r"\$",
    "&": r"\&",
    "%": r"\%",
    "#": r"\#",
    "_": r"\_",
    "~": r"\textasciitilde{}",
    "^": r"\textasciicircum{}",
}


def escape(value: str) -> str:
    return "".join(_ESCAPES.get(c, c) for c in value)


def entry(paper: Paper) -> str:
    """One BibTeX entry.

    A paper with a journal or conference venue is a ``@article``; an arXiv
    preprint with no venue of record is a ``@misc`` with its ``eprint``, which is
    what keeps a preprint citation honest about what it is.
    """
    if not paper.citekey:
        raise ValueError(f"{paper.title!r} has no citekey")

    is_preprint = paper.arxiv_id and paper.venue in ("", "arXiv")
    kind = "misc" if is_preprint else "article"

    fields: list[tuple[str, str]] = [
        ("title", escape(paper.title)),
        ("author", escape(" and ".join(paper.authors))),
        ("year", paper.year),
    ]
    if not is_preprint and paper.venue:
        fields.append(("journal", escape(paper.venue)))
    if paper.doi:
        fields.append(("doi", paper.doi))
    if paper.arxiv_id:
        fields.append(("eprint", paper.arxiv_id))
        fields.append(("archivePrefix", "arXiv"))

    body = ",\n".join(f"  {name} = {{{value}}}" for name, value in fields if value)
    return f"@{kind}{{{paper.citekey},\n{body}\n}}"


def render(papers) -> str:
    """A whole .bib file, entries sorted by citekey so the diff is stable."""
    entries = sorted((p for p in papers if p.citekey), key=lambda p: p.citekey)
    header = (
        "% Written by paperdb. Do not edit: `paperdb bibtex` overwrites this file.\n"
        f"% {len(entries)} entries.\n"
    )
    return header + "\n\n".join(entry(p) for p in entries) + ("\n" if entries else "")
