import pytest

from paperdb.names import MAX_NAME_BYTES, build, citekey_in, sanitize_title, title_in


def test_build_is_citekey_space_title():
    assert build("lastname2023paper", "A Paper Title") == (
        "lastname2023paper A Paper Title.pdf"
    )


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("A/B Testing", "A B Testing"),
        ("Notes: draft <2>", "Notes draft 2"),
        ("  spaced   out  ", "spaced out"),
        ("..hidden", "hidden"),
        ("trailing.", "trailing"),
    ],
    ids=["slash", "colon-and-angles", "whitespace", "leading-dots", "trailing-dot"],
)
def test_sanitize_title(raw, expected):
    assert sanitize_title(raw) == expected


def test_long_title_is_truncated_to_the_byte_cap_on_a_word_boundary():
    name = build("smith2024graph", " ".join(["word"] * 200))
    assert len(name.encode()) <= MAX_NAME_BYTES
    assert not name.removesuffix(".pdf").endswith(" ")


def test_non_ascii_title_respects_bytes_not_characters():
    name = build("smith2024graph", "结构" * 200)
    assert len(name.encode()) <= MAX_NAME_BYTES


def test_build_rejects_a_non_citekey():
    with pytest.raises(ValueError):
        build("01", "Some Paper")


@pytest.mark.parametrize(
    ("filename", "expected"),
    [
        ("lastname2023paper A Paper Title.pdf", "lastname2023paper"),
        ("smith2024graph-a Graph Theory.pdf", "smith2024graph-a"),
        ("smith2024graph.pdf", "smith2024graph"),
        ("2401.01234v1.pdf", None),
        ("01 some paper title.pdf", None),
        ("ssrn-1234567.pdf", None),
        ("Proceedings-2023-a-paper-title.pdf", None),
    ],
    ids=["keyed", "suffixed-key", "key-only", "arxiv-id", "numeric-prefix", "ssrn", "proceedings"],
)
def test_citekey_in_reads_the_first_token_exactly(filename, expected):
    assert citekey_in(filename) == expected


def test_a_key_does_not_claim_a_suffixed_key_s_file():
    """Exact-token matching, not prefix matching."""
    assert citekey_in("smith2024graph-a Graph Theory.pdf") == "smith2024graph-a"
    assert citekey_in("smith2024graph Graph Networks.pdf") == "smith2024graph"


@pytest.mark.parametrize(
    ("filename", "expected"),
    [
        ("lastname2023paper A Paper Title.pdf", "A Paper Title"),
        ("smith2024graph.pdf", ""),
        ("ssrn-1234567.pdf", "ssrn-1234567"),
    ],
    ids=["keyed", "key-only", "unkeyed-returns-stem"],
)
def test_title_in(filename, expected):
    assert title_in(filename) == expected
