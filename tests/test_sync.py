"""End-to-end `sync`, against a fake Notion and a real folder.

This is where the reconciliation rules are pinned: which file adopts a key, which
file is left alone and reported, and which row is reported as having no file.
"""

from __future__ import annotations

import pytest
from click.testing import CliRunner

from paperdb import cli, config
from paperdb.config import Config
from paperdb.metadata import Paper, ResolveError
from paperdb.notion import _rich

PDF = b"%PDF-1.7\n%%EOF\n"


def row(citekey: str, title: str, filename: str = "", page_id: str = "") -> dict:
    return {
        "id": page_id or f"page-{citekey}",
        "properties": {
            "Name": {"title": _rich(title)},
            "Citekey": {"rich_text": _rich(citekey)},
            "Filename": {"rich_text": _rich(filename)},
        },
    }


class FakeNotion:
    """Records what sync asked it to do, so the test can assert on intent."""

    def __init__(self, rows: list[dict]):
        self._rows = rows
        self.filenames: dict[str, str] = {}
        self.created: list[tuple[str, str]] = []
        self.highlights: dict[str, list[str]] = {}

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def close(self):
        pass

    def rows(self, _database_id):
        return list(self._rows)

    def set_filename(self, page_id, filename):
        self.filenames[page_id] = filename

    def create_row(self, _database_id, paper, filename, highlights=()):
        self.created.append((paper.citekey, filename))
        return f"page-{paper.citekey}"

    def replace_highlights(self, page_id, highlights):
        # Defaults to [] rather than None so an empty-to-empty pass reports "no
        # change", which is what the real client does: a page with no Highlights
        # section and a PDF with no annotations are already in agreement.
        changed = self.highlights.get(page_id, []) != list(highlights)
        self.highlights[page_id] = list(highlights)
        return changed


@pytest.fixture
def folder(tmp_path):
    papers = tmp_path / "Papers"
    papers.mkdir()
    return papers


@pytest.fixture
def wired(monkeypatch, folder, tmp_path):
    """Point the CLI at the temp folder and a fake Notion."""

    def install(rows: list[dict], resolver=None) -> FakeNotion:
        fake = FakeNotion(rows)
        monkeypatch.setattr(
            config, "load", lambda path=None: Config(papers_dir=folder, database_id="db")
        )
        monkeypatch.setattr(config, "token", lambda: "token")
        monkeypatch.setattr(cli.notion, "Notion", lambda token: fake)
        if resolver is not None:
            monkeypatch.setattr(cli, "resolve", resolver)
        return fake

    return install


def run(*args):
    return CliRunner().invoke(cli.main, ["sync", *args])


def test_a_matching_file_and_row_are_left_alone(wired, folder):
    name = "lastname2023paper A Paper Title.pdf"
    (folder / name).write_bytes(PDF)
    fake = wired([row("lastname2023title", "A Paper Title", name)])

    result = run()

    assert result.exit_code == 0
    assert (folder / name).exists()
    assert fake.filenames == {}
    assert "(nothing)" in result.output


def test_a_row_whose_title_changed_renames_its_file(wired, folder):
    (folder / "smith2024graph Old Title.pdf").write_bytes(PDF)
    fake = wired([row("smith2024graph", "New Title", "smith2024graph Old Title.pdf")])

    result = run()

    assert (folder / "smith2024graph New Title.pdf").exists()
    assert not (folder / "smith2024graph Old Title.pdf").exists()
    assert fake.filenames["page-smith2024graph"] == "smith2024graph New Title.pdf"
    assert "rename" in result.output


def test_dry_run_reports_the_rename_and_changes_nothing(wired, folder):
    (folder / "smith2024graph Old Title.pdf").write_bytes(PDF)
    fake = wired([row("smith2024graph", "New Title", "smith2024graph Old Title.pdf")])

    result = run("--dry-run")

    assert (folder / "smith2024graph Old Title.pdf").exists()
    assert not (folder / "smith2024graph New Title.pdf").exists()
    assert fake.filenames == {}
    assert "would make" in result.output


def test_a_suffixed_key_does_not_capture_the_unsuffixed_row_s_file(wired, folder):
    """The collision-suffix regression, end to end.

    Both files are keyed and both rows exist; each file must bind to its own row.
    A prefix match would rename one of them to the other's title.
    """
    (folder / "smith2024graph Graph Networks.pdf").write_bytes(PDF)
    (folder / "smith2024graph-a Graph Theory.pdf").write_bytes(PDF)
    fake = wired([
        row("smith2024graph", "Graph Networks", "smith2024graph Graph Networks.pdf"),
        row("smith2024graph-a", "Graph Theory", "smith2024graph-a Graph Theory.pdf"),
    ])

    result = run()

    assert (folder / "smith2024graph Graph Networks.pdf").exists()
    assert (folder / "smith2024graph-a Graph Theory.pdf").exists()
    assert fake.filenames == {}
    assert "(nothing)" in result.output


def test_an_unkeyed_file_with_a_resolvable_id_is_adopted_and_renamed(wired, folder):
    (folder / "2401.01234.pdf").write_bytes(PDF)
    paper = Paper(title="A Paper Title", authors=["Ada Lastname"], year="2023")
    fake = wired([], resolver=lambda identifier, email="", client=None: paper)

    result = run()

    assert (folder / "lastname2023paper A Paper Title.pdf").exists()
    assert not (folder / "2401.01234.pdf").exists()
    assert fake.created == [("lastname2023paper", "lastname2023paper A Paper Title.pdf")]
    assert "adopt" in result.output


def test_an_unkeyed_file_with_no_identifier_is_reported_not_guessed(wired, folder):
    (folder / "03 short name.pdf").write_bytes(PDF)
    fake = wired([])

    result = run()

    assert (folder / "03 short name.pdf").exists()
    assert fake.created == []
    assert "no identifier" in result.output
    assert "paperdb add" in result.output


def test_a_file_whose_id_will_not_resolve_is_reported_with_the_reason(wired, folder):
    (folder / "ssrn-1234567.pdf").write_bytes(PDF)

    def refuse(identifier, email="", client=None):
        raise ResolveError("OpenAlex has no record for 10.2139/ssrn.1234567")

    fake = wired([], resolver=refuse)

    result = run()

    assert fake.created == []
    assert "OpenAlex has no record" in result.output


def test_a_row_with_no_file_is_reported(wired, folder):
    wired([row("smith2024graph", "Graph Networks", "smith2024graph Graph Networks.pdf")])

    result = run()

    assert "1 row(s) with no file" in result.output
    assert "smith2024graph" in result.output


def test_a_keyed_file_with_no_row_is_reported_and_not_adopted(wired, folder):
    (folder / "smith2024graph Graph Networks.pdf").write_bytes(PDF)
    fake = wired([])

    result = run()

    assert fake.created == []
    assert "no row in Notion" in result.output


def test_a_drifted_filename_property_is_repaired_without_touching_the_file(wired, folder):
    name = "smith2024graph Graph Networks.pdf"
    (folder / name).write_bytes(PDF)
    fake = wired([row("smith2024graph", "Graph Networks", "stale.pdf")])

    result = run()

    assert (folder / name).exists()
    assert fake.filenames["page-smith2024graph"] == name
    assert "repair" in result.output


def test_no_highlights_skips_the_annotation_pass(wired, folder):
    name = "smith2024graph Graph Networks.pdf"
    (folder / name).write_bytes(PDF)
    fake = wired([row("smith2024graph", "Graph Networks", name)])

    run("--no-highlights")

    assert fake.highlights == {}
