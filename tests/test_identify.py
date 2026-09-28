import pytest

from paperdb.identify import SSRN_DOI_PREFIX, from_filename


@pytest.mark.parametrize(
    ("filename", "expected"),
    [
        ("2401.01234v1.pdf", "2401.01234v1"),
        ("2401.01234.pdf", "2401.01234"),
        ("ssrn-1234567.pdf", SSRN_DOI_PREFIX + "1234567"),
        ("SSRN_7654321.pdf", SSRN_DOI_PREFIX + "7654321"),
        ("01 some paper title.pdf", None),
        ("03 short name.pdf", None),
        ("Proceedings-2023-a-paper-title-Conference.pdf", None),
    ],
    ids=["arxiv-versioned", "arxiv-plain", "ssrn-dash", "ssrn-underscore", "hand-numbered", "hand-short", "proceedings"],
)
def test_from_filename(filename, expected):
    assert from_filename(filename) == expected


def test_a_year_in_a_proceedings_name_is_not_mistaken_for_an_arxiv_id():
    """`2023` is four digits but has no `.NNNN` tail, so it must not match."""
    assert from_filename("Proceedings-2023-something.pdf") is None
