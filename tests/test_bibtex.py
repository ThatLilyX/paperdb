import pytest

from paperdb.bibtex import entry, escape, render
from paperdb.metadata import Paper


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("100%", r"100\%"), ("A & B", r"A \& B"), ("A_B", r"A\_B"), ("{x}", r"\{x\}"), ("$5", r"\$5")],
    ids=["percent", "ampersand", "underscore", "braces", "dollar"],
)
def test_escape_keeps_tex_readable(raw, expected):
    assert escape(raw) == expected


def test_a_preprint_with_no_venue_of_record_is_a_misc_with_its_eprint(preprint):
    text = entry(preprint)
    assert text.startswith("@misc{lastname2023paper,")
    assert "eprint = {2401.01234v2}" in text
    assert "archivePrefix = {arXiv}" in text
    assert "journal" not in text


def test_a_published_paper_is_an_article_with_its_journal(published):
    text = entry(published)
    assert text.startswith("@article{smith2024study,")
    assert "journal = {Nature}" in text
    assert "doi = {10.1038/example}" in text


def test_a_preprint_that_later_appeared_somewhere_becomes_an_article(preprint):
    preprint.venue = "Proceedings of Somewhere"
    text = entry(preprint)
    assert text.startswith("@article{")
    assert "journal = {Proceedings of Somewhere}" in text
    assert "eprint = {2401.01234v2}" in text


def test_entry_refuses_a_paper_with_no_citekey(published):
    published.citekey = ""
    with pytest.raises(ValueError):
        entry(published)


def test_render_sorts_by_citekey_so_the_diff_is_stable(preprint, published):
    text = render([published, preprint])
    assert text.index("lastname2023paper") < text.index("smith2024study")
    assert "% 2 entries." in text


def test_render_skips_unkeyed_rows(preprint):
    text = render([preprint, Paper(title="No key")])
    assert "% 1 entries." in text
    assert "No key" not in text


def test_render_of_nothing_is_still_a_valid_file():
    assert render([]).endswith("% 0 entries.\n")
