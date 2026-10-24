"""Reading the highlights out of a PDF you annotated.

Uses pypdf rather than the pdfannots command line tool so paperdb has no
out-of-process dependency. The extraction is deliberately plain: highlighted text
and the contents of notes, in page order, with nothing inferred.
"""

from __future__ import annotations

from pathlib import Path

from pypdf import PdfReader
from pypdf.errors import PdfReadError

# The annotation subtypes that carry something a reader wrote or marked. Links,
# widgets and popups are skipped: a popup duplicates its parent's contents.
WANTED_SUBTYPES = frozenset({"/Highlight", "/Underline", "/Squiggly", "/Text", "/FreeText"})


def _quad_text(page, annotation) -> str:
    """The text a markup annotation covers, via its quad points.

    Extracted by intersecting the page's text layout with the annotation's
    rectangle. Returns an empty string when the page has no text layer, which is
    the normal case for a scan.
    """
    rect = annotation.get("/Rect")
    if not rect:
        return ""
    x0, y0, x1, y1 = (float(v) for v in rect)
    lines: list[str] = []

    def visitor(text, _cm, tm, _font, _size):
        if not text.strip():
            return
        x, y = tm[4], tm[5]
        if x0 - 2 <= x <= x1 + 2 and y0 - 2 <= y <= y1 + 2:
            lines.append(text.strip())

    try:
        page.extract_text(visitor_text=visitor)
    except Exception:
        return ""
    return " ".join(" ".join(lines).split())


def extract(pdf_path: str | Path) -> list[str]:
    """Every highlight and note in a PDF, in page order.

    Returns an empty list for a PDF with no annotations, which is what a freshly
    downloaded paper looks like. Unreadable or encrypted files also return an
    empty list rather than raising: a missing highlight must never stop a paper
    from being recorded.
    """
    path = Path(pdf_path)
    try:
        reader = PdfReader(path)
    except (PdfReadError, OSError, ValueError):
        return []

    found: list[str] = []
    for page in reader.pages:
        for reference in page.get("/Annots") or []:
            try:
                annotation = reference.get_object()
            except Exception:
                continue
            if annotation.get("/Subtype") not in WANTED_SUBTYPES:
                continue
            note = str(annotation.get("/Contents") or "").strip()
            marked = _quad_text(page, annotation)
            text = marked or note
            if marked and note and note not in marked:
                text = f"{marked} [note: {note}]"
            text = " ".join(text.split())
            if text and text not in found:
                found.append(text)
    return found
