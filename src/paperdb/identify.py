"""Guessing what an unkeyed PDF in the folder actually is.

A file dropped in by hand carries its identifier in one of three places: its
name, its embedded metadata, or the first page of its text. All three are tried
before the file is reported as an orphan, because an orphan is work for the
reader and a guess that can be verified is not.
"""

from __future__ import annotations

import re
from pathlib import Path

from pypdf import PdfReader

# `2401.01234v1.pdf`, and the pre-2007 `hep-th9901001` form.
_ARXIV_IN_NAME = re.compile(r"(?<!\d)(\d{4}\.\d{4,5})(v\d+)?(?!\d)")
# SSRN exports are named for the abstract id, and SSRN mints its DOIs from it.
_SSRN_IN_NAME = re.compile(r"ssrn[-_ ]?(\d{4,})", re.IGNORECASE)
_DOI_ANYWHERE = re.compile(r"\b10\.\d{4,9}/[-._;()/:a-z0-9]+", re.IGNORECASE)
_ARXIV_ANYWHERE = re.compile(r"arxiv[:\s]*(\d{4}\.\d{4,5})(v\d+)?", re.IGNORECASE)

SSRN_DOI_PREFIX = "10.2139/ssrn."


def from_filename(name: str) -> str | None:
    """An identifier read out of a filename, if one is there."""
    stem = Path(name).stem
    ssrn = _SSRN_IN_NAME.search(stem)
    if ssrn:
        return SSRN_DOI_PREFIX + ssrn.group(1)
    arxiv = _ARXIV_IN_NAME.search(stem)
    if arxiv:
        return arxiv.group(1) + (arxiv.group(2) or "")
    doi = _DOI_ANYWHERE.search(stem.replace("_", "/"))
    if doi:
        return doi.group(0)
    return None


def _trim_doi(doi: str) -> str:
    """Drop trailing punctuation a line break or sentence end left behind."""
    return doi.rstrip(".,;:)pdf" if doi.lower().endswith(".pdf") else ".,;:)")


def from_pdf(pdf_path: str | Path, pages: int = 2) -> str | None:
    """An identifier read out of a PDF's metadata or first pages."""
    try:
        reader = PdfReader(Path(pdf_path))
    except Exception:
        return None

    haystacks: list[str] = []
    try:
        info = reader.metadata or {}
        haystacks.extend(str(v) for v in info.values() if v)
    except Exception:
        pass
    for page in reader.pages[:pages]:
        try:
            haystacks.append(page.extract_text() or "")
        except Exception:
            continue

    for text in haystacks:
        arxiv = _ARXIV_ANYWHERE.search(text)
        if arxiv:
            return arxiv.group(1) + (arxiv.group(2) or "")
        doi = _DOI_ANYWHERE.search(text)
        if doi:
            return _trim_doi(doi.group(0))
    return None


def identify(pdf_path: str | Path) -> str | None:
    """The best identifier for a PDF: its name first, then its contents."""
    path = Path(pdf_path)
    return from_filename(path.name) or from_pdf(path)
