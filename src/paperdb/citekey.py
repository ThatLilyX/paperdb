"""Minting citation keys.

The citekey is the primary key on both sides: the ``Citekey`` property in Notion
and the filename prefix on disk. It is minted once and never rewritten, so the
rules here are the ones that must stay stable.
"""

from __future__ import annotations

import re
import string
import unicodedata

# Words skipped when picking the title keyword. Deliberately short: the goal is a
# recognisable key, not a stopword list with opinions.
SKIP_WORDS = frozenset(
    """a an the on of for and or to in into with without by from at as is are
    be being been do does doing how what why when where which who whom this
    that these those we our you your it its""".split()
)

# Lowercase words that belong to the family name that follows them.
PARTICLES = frozenset(
    """van von der den de del della di da das dos du la le ter ten bin al el""".split()
)

_ALLOWED = frozenset(string.ascii_lowercase + string.digits)

# Collisions get a separated suffix rather than a bare letter. A bare letter
# makes the first key a prefix of the second, which breaks both substring search
# in Notion and prefix matching on filenames.
SUFFIX_SEPARATOR = "-"


class CitekeyError(ValueError):
    """Raised when a key cannot honestly be minted."""


def _fold(text: str) -> str:
    """Lowercase, strip accents, keep only ascii letters and digits."""
    decomposed = unicodedata.normalize("NFKD", text)
    stripped = "".join(c for c in decomposed if not unicodedata.combining(c))
    return "".join(c for c in stripped.lower() if c in _ALLOWED)


def surname_of(author: str) -> str:
    """The surname part of an author string, in either order convention.

    ``"Lastname, Ada"`` and ``"Ada Lastname"`` both give ``lastname``.
    Particles are folded in rather than dropped, so ``"van den Berg"`` gives
    ``vandenberg`` and stays stable however the source formats it.
    """
    author = author.strip()
    if not author:
        raise CitekeyError("author is empty")
    if "," in author:
        family = author.split(",", 1)[0]
    else:
        # Initials are never the family name: "Kipf T. N." is kipf.
        parts = [w for w in author.split() if len(_fold(w)) > 1] or author.split()
        # The family name is the last word plus any particles right before it:
        # "Piet van den Berg" is vandenberg, "Abdul Fatir Ansari" is ansari.
        family_words = [parts[-1]]
        for word in reversed(parts[1:-1]):
            if word.lower() not in PARTICLES:
                break
            family_words.insert(0, word)
        family = " ".join(family_words)
    folded = _fold(family)
    if not folded:
        raise CitekeyError(f"no usable surname in {author!r}")
    return folded


def keyword_of(title: str) -> str:
    """The first significant word of a title.

    Falls back to the first word of any kind when every word is skippable, so a
    title like "On the Other Hand" still yields a key.
    """
    words = re.findall(r"[^\W_]+", title, flags=re.UNICODE)
    folded = [w for w in (_fold(w) for w in words) if w]
    if not folded:
        raise CitekeyError(f"no usable word in title {title!r}")
    for word in folded:
        if word not in SKIP_WORDS:
            return word
    return folded[0]


def _suffixes():
    """``-a`` … ``-z``, then ``-aa`` … , forever."""
    letters = string.ascii_lowercase
    width = 1
    while True:
        from itertools import product

        for combo in product(letters, repeat=width):
            yield SUFFIX_SEPARATOR + "".join(combo)
        width += 1


def mint(author: str, year: int | str, title: str, taken=()) -> str:
    """Mint ``lastname2024keyword``, disambiguated against ``taken``.

    ``taken`` is any container of keys already in use. A collision appends
    ``-a``, ``-b`` and so on.

    Raises :class:`CitekeyError` when author, year or title cannot supply their
    part. A placeholder key is never invented: an unresolvable paper is reported
    as an orphan instead.
    """
    year_text = str(year).strip()
    if not re.fullmatch(r"\d{4}", year_text):
        raise CitekeyError(f"year must be four digits, got {year!r}")
    base = f"{surname_of(author)}{year_text}{keyword_of(title)}"
    taken = set(taken)
    if base not in taken:
        return base
    for suffix in _suffixes():
        candidate = base + suffix
        if candidate not in taken:
            return candidate
    raise AssertionError("unreachable: suffixes are infinite")


CITEKEY_RE = re.compile(r"^[a-z][a-z0-9]*\d{4}[a-z0-9]+(?:-[a-z]+)?$")


def is_citekey(text: str) -> bool:
    """Whether ``text`` has the shape :func:`mint` produces."""
    return bool(CITEKEY_RE.match(text))
