import httpx
import pytest

from paperdb.metadata import Paper
from paperdb.notion import RUN_LIMIT, Notion, NotionError, _rich, row_paper, row_text


@pytest.fixture
def client() -> Notion:
    return Notion("test-token", client=httpx.Client(transport=httpx.MockTransport(
        lambda r: httpx.Response(200, json={"id": "page-1", "results": [], "has_more": False})
    )))


def test_rich_text_is_split_into_runs_notion_will_accept():
    runs = _rich("x" * (RUN_LIMIT * 2 + 5))
    assert len(runs) == 3
    assert all(len(run["text"]["content"]) <= RUN_LIMIT for run in runs)
    assert "".join(run["text"]["content"] for run in runs) == "x" * (RUN_LIMIT * 2 + 5)


def test_an_empty_string_still_produces_one_run():
    assert len(_rich("")) == 1


def test_the_schema_has_no_file_property(client):
    """No PDF is ever uploaded; the row records a filename."""
    assert "Filename" in Notion.SCHEMA
    assert not any(
        "files" in definition for definition in Notion.SCHEMA.values()
    )


def test_the_schema_splits_the_two_identifier_kinds(client):
    assert "DOI" in Notion.SCHEMA
    assert "arXiv ID" in Notion.SCHEMA
    assert "DOI/arXiv" not in Notion.SCHEMA


def test_properties_carry_the_filename_and_the_year_as_a_number(client, preprint):
    properties = client._properties(preprint, "lastname2023title Direct.pdf")
    assert row_text({"properties": properties}, "Filename") == "lastname2023title Direct.pdf"
    assert properties["Year"] == {"number": 2023}
    assert properties["arXiv ID"]["rich_text"][0]["text"]["content"] == "2401.01234v2"


def test_a_paper_with_no_year_simply_omits_it(client, preprint):
    preprint.year = ""
    assert "Year" not in client._properties(preprint, "x.pdf")


def test_the_body_omits_the_highlights_heading_when_there_are_none(client, preprint):
    types = [block["type"] for block in client.body_blocks(preprint)]
    assert types == ["heading_2", "paragraph", "quote", "heading_2", "paragraph"]


def test_the_body_adds_one_bullet_per_highlight(client, preprint):
    blocks = client.body_blocks(preprint, ["first", "second"])
    bullets = [b for b in blocks if b["type"] == "bulleted_list_item"]
    assert len(bullets) == 2


def test_a_paper_with_no_abstract_says_so_rather_than_leaving_a_blank_quote(client, preprint):
    preprint.abstract = ""
    quote = next(b for b in client.body_blocks(preprint) if b["type"] == "quote")
    assert quote["quote"]["rich_text"][0]["text"]["content"] == "No abstract available."


def test_row_paper_round_trips_what_create_row_wrote(client, published):
    properties = client._properties(published, "smith2024study A Study.pdf")
    restored = row_paper({"properties": properties})
    assert restored.citekey == published.citekey
    assert restored.title == published.title
    assert restored.authors == published.authors
    assert restored.year == published.year
    assert restored.venue == published.venue
    assert restored.doi == published.doi


def test_an_error_body_reaches_the_caller():
    bad = Notion("t", client=httpx.Client(transport=httpx.MockTransport(
        lambda r: httpx.Response(400, json={"message": "Citekey is not a property"})
    )))
    with pytest.raises(NotionError, match="Citekey is not a property"):
        bad._call("POST", "/pages", json={})
