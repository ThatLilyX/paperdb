"""Building and reading the filenames that carry the citekey.

A file is named ``<citekey> <title>.pdf``. The citekey is everything before the
first space, which makes the match between file and row exact rather than a
prefix test: ``smith2024graph`` never claims ``smith2024graph-a``'s file.
"""

from __future__ import annotations

import re
import unicodedata
from pathlib import Path

from .citekey import is_citekey

SUFFIX = ".pdf"

# Illegal on some filesystem or shell somewhere, so out everywhere: the folder is
# meant to be readable on a Mac, a Linux box and a phone's file browser alike.
_ILLEGAL = r'/\\:*?"<>|'
_CONTROL = "".join(chr(c) for c in range(32)) + chr(127)

# HFS+ and ext4 both cap a single component at 255 bytes. Leave room for the
# extension and for a copy marker a syncing client might append.
MAX_NAME_BYTES = 200


def sanitize_title(title: str) -> str:
    """A title reduced to something safe to put in a filename.

    Collapses whitespace, drops path separators and control characters, and
    normalises to NFC so the same title produces the same bytes on macOS as on
    Linux.
    """
    title = unicodedata.normalize("NFC", title)
    cleaned = "".join(" " if c in _ILLEGAL or c in _CONTROL else c for c in title)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    # A leading dot would hide the file; a trailing dot or space is dropped
    # silently by some filesystems, which makes a rename look like a no-op.
    return cleaned.strip(". ")


def _truncate_to_bytes(text: str, limit: int) -> str:
    """Trim ``text`` so its UTF-8 form fits ``limit`` bytes, on a word boundary."""
    if len(text.encode()) <= limit:
        return text
    trimmed = text
    while len(trimmed.encode()) > limit:
        cut = trimmed.rfind(" ")
        trimmed = trimmed[:cut] if cut > 0 else trimmed[:-1]
    return trimmed.rstrip(". ")


def build(citekey: str, title: str) -> str:
    """The filename for a paper: ``<citekey> <title>.pdf``."""
    if not is_citekey(citekey):
        raise ValueError(f"not a citekey: {citekey!r}")
    safe = sanitize_title(title)
    room = MAX_NAME_BYTES - len(citekey.encode()) - len(SUFFIX.encode()) - 1
    if room < 1:
        return citekey + SUFFIX
    safe = _truncate_to_bytes(safe, room)
    return f"{citekey} {safe}{SUFFIX}".strip() if safe else citekey + SUFFIX


def citekey_in(name: str | Path) -> str | None:
    """The citekey a filename carries, or ``None`` if it carries none.

    Reads the first space-delimited token and requires it to have the citekey
    shape, so a hand-dropped ``2401.01234v1.pdf`` is correctly reported as
    unkeyed rather than mistaken for a key.
    """
    stem = Path(name).stem
    token = stem.split(" ", 1)[0]
    return token if is_citekey(token) else None


def title_in(name: str | Path) -> str:
    """The title part of a keyed filename, or the whole stem if it is unkeyed."""
    stem = Path(name).stem
    if citekey_in(name) is None:
        return stem
    _, _, rest = stem.partition(" ")
    return rest
