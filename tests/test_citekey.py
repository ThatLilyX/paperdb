import pytest

from paperdb.citekey import CitekeyError, is_citekey, keyword_of, mint, surname_of


@pytest.mark.parametrize(
    ("author", "expected"),
    [
        ("Ada Lastname", "lastname"),
        ("Lastname, Ada", "lastname"),
        ("van den Berg, Piet", "vandenberg"),
        ("Piet van den Berg", "vandenberg"),
        ("Müller, Anna", "muller"),
        ("Plato", "plato"),
        ("O'Neill, Kate", "oneill"),
        ("Thomas N. Kipf", "kipf"),
        ("Petter N Kolm", "kolm"),
        ("J. R. R. Tolkien", "tolkien"),
        ("Jan C. van den Berg", "vandenberg"),
        ("Abdul Fatir Ansari", "ansari"),
        ("Ludwig van Beethoven", "vanbeethoven"),
        ("Leonardo da Vinci", "davinci"),
        ("Jean-Pierre Smith-Jones", "smithjones"),
        ("Kipf T. N.", "kipf"),
    ],
    ids=[
        "given-family", "family-comma", "particle-comma", "particle-plain", "accent", "mononym",
        "apostrophe", "middle-initial", "initial-no-dot", "initials-only-given", "initial-and-particle",
        "two-given-names", "particle-von", "particle-da", "hyphenated", "trailing-initials",
    ],
)
def test_surname_is_stable_across_conventions(author, expected):
    assert surname_of(author) == expected


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        ("A Paper Title Here", "paper"),
        ("On the Graph Neural Network", "graph"),
        ("A Study of Things", "study"),
        ("On The Other Hand", "other"),
        ("The, of, and", "the"),
        ("3D Reconstruction", "3d"),
    ],
    ids=["leading-article", "leading-stopword", "article", "all-but-one-skipped", "all-skipped", "digit-word"],
)
def test_keyword_skips_stopwords_but_always_returns_something(title, expected):
    assert keyword_of(title) == expected


def test_mint_composes_surname_year_keyword():
    assert mint("Ada Lastname", 2023, "A Paper Title") == "lastname2023paper"


def test_collision_suffix_is_separated_so_no_key_prefixes_another():
    """The bug this separator exists for.

    A bare letter suffix would make ``smith2024graph`` a prefix of
    ``smith2024grapha``, which breaks substring search in Notion and prefix
    matching on filenames. Both keys must be findable on their own.
    """
    first = mint("Smith, Jane", 2024, "Graph Networks")
    second = mint("Smith, Jane", 2024, "Graph Theory", taken={first})
    third = mint("Smith, Jane", 2024, "Graph Colouring", taken={first, second})

    assert (first, second, third) == ("smith2024graph", "smith2024graph-a", "smith2024graph-b")
    assert not second.startswith(first + "a")
    assert second.removeprefix(first).startswith("-")


def test_collision_suffix_rolls_past_z():
    taken = {"smith2024graph"} | {f"smith2024graph-{c}" for c in "abcdefghijklmnopqrstuvwxyz"}
    assert mint("Smith, Jane", 2024, "Graph", taken=taken) == "smith2024graph-aa"


@pytest.mark.parametrize(
    ("author", "year", "title"),
    [("", 2024, "Title"), ("Smith", "", "Title"), ("Smith", "24", "Title"), ("Smith", 2024, "")],
    ids=["no-author", "no-year", "two-digit-year", "no-title"],
)
def test_mint_refuses_rather_than_inventing_a_placeholder(author, year, title):
    with pytest.raises(CitekeyError):
        mint(author, year, title)


@pytest.mark.parametrize(
    ("text", "valid"),
    [
        ("smith2024graph", True),
        ("smith2024graph-a", True),
        ("vandenberg1999x", True),
        ("Smith2024graph", False),
        ("2024graph", False),
        ("smith2024", False),
        ("smith2024graph-", False),
        ("01", False),
        ("2401.01234v1", False),
    ],
    ids=["plain", "suffixed", "particle", "capital", "no-surname", "no-keyword", "dangling-dash", "number", "arxiv-id"],
)
def test_is_citekey(text, valid):
    assert is_citekey(text) is valid
