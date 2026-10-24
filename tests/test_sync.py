"""End-to-end `sync` and `add`, against a fake Notion and a real folder.

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

    assert not (folder / "03 short name.pdf").exists()
    assert (folder / "Inbox" / "03 short name.pdf").exists()
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


def test_a_duplicate_download_never_overwrites_the_original(wired, folder):
    """A browser's ``(1)`` copy must not be renamed over the annotated original."""
    original = folder / "smith2024graph Graph Things.pdf"
    copy = folder / "smith2024graph Graph Things (1).pdf"
    original.write_bytes(PDF + b"annotated")
    copy.write_bytes(PDF + b"fresh")
    fake = wired([row("smith2024graph", "Graph Things", original.name)])

    result = run()

    assert original.read_bytes() == PDF + b"annotated"
    assert copy.read_bytes() == PDF + b"fresh"
    assert fake.filenames == {}
    assert "Graph Things (1).pdf  (another copy of smith2024graph" in result.output


def test_two_files_renamed_to_the_same_name_keep_both(wired, folder):
    """A retitle that points two copies at one name renames only the first."""
    (folder / "smith2024graph Old Title.pdf").write_bytes(PDF + b"one")
    (folder / "smith2024graph Old Title (1).pdf").write_bytes(PDF + b"two")
    wired([row("smith2024graph", "New Title", "smith2024graph Old Title.pdf")])

    for args in (["--dry-run"], []):
        result = run(*args)
        assert result.output.count("rename") == 1

    assert len(list(folder.glob("*.pdf"))) == 2


def test_a_retitle_that_only_changes_case_still_renames(wired, folder):
    """On a case-insensitive disk the file is its own target, not a conflict."""
    (folder / "smith2024graph graph things.pdf").write_bytes(PDF)
    wired([row("smith2024graph", "Graph Things", "smith2024graph graph things.pdf")])

    result = run()

    assert [p.name for p in folder.glob("*.pdf")] == ["smith2024graph Graph Things.pdf"]
    assert "already taken" not in result.output


def test_a_failed_row_create_leaves_the_file_unkeyed_so_the_next_run_retries(wired, folder):
    (folder / "2401.01234.pdf").write_bytes(PDF)
    paper = Paper(title="A Paper Title", authors=["Ada Lastname"], year="2023")
    fake = wired([], resolver=lambda identifier, email="", client=None: paper)

    def outage(*_args, **_kwargs):
        raise RuntimeError("Notion 502")

    fake.create_row = outage
    run()
    assert (folder / "Inbox" / "2401.01234.pdf").exists()

    del fake.create_row
    result = run()

    assert fake.created == [("lastname2023paper", "lastname2023paper A Paper Title.pdf")]
    assert "adopt" in result.output


def test_a_paper_already_in_the_database_is_not_added_twice(wired, folder):
    """Dropping in a second copy of a known paper binds it to the existing row."""
    (folder / "2401.01234v1.pdf").write_bytes(PDF)
    existing = row("lastname2023paper", "A Paper Title")
    existing["properties"]["arXiv ID"] = {"rich_text": _rich("2401.01234v2")}
    paper = Paper(
        title="A Paper Title", authors=["Ada Lastname"], year="2023", arxiv_id="2401.01234v1"
    )
    fake = wired([existing], resolver=lambda identifier, email="", client=None: paper)

    result = run()

    assert fake.created == []
    assert (folder / "lastname2023paper A Paper Title.pdf").exists()
    assert fake.filenames == {"page-lastname2023paper": "lastname2023paper A Paper Title.pdf"}
    assert "row with no file" not in result.output


def test_add_refuses_a_hand_set_citekey_with_the_wrong_shape(wired, folder):
    paper = Paper(title="A Paper Title", authors=["Ada Lastname"], year="2023")
    fake = wired([], resolver=lambda identifier, email="", client=None: paper)

    result = CliRunner().invoke(
        cli.main, ["add", "2401.01234", "--citekey", "Smith2024", "--no-download"]
    )

    assert result.exit_code == 1
    assert "Smith2024" in result.output
    assert fake.created == []


def test_adopting_never_takes_the_key_of_a_file_already_in_the_folder(wired, folder):
    """A keyed file with no row still owns its key on disk."""
    orphan = folder / "lastname2023paper A Paper Title.pdf"
    orphan.write_bytes(PDF + b"annotated")
    (folder / "2401.01234.pdf").write_bytes(PDF + b"fresh")
    paper = Paper(title="A Paper Title", authors=["Ada Lastname"], year="2023")
    fake = wired([], resolver=lambda identifier, email="", client=None: paper)

    run()

    assert orphan.read_bytes() == PDF + b"annotated"
    assert fake.created == [("lastname2023paper-a", "lastname2023paper-a A Paper Title.pdf")]


def test_add_with_the_key_of_a_file_in_the_folder_records_that_file(wired, folder, monkeypatch):
    """The fix sync suggests for an orphan must not download over it."""
    orphan = folder / "lastname2023paper My Copy.pdf"
    orphan.write_bytes(PDF + b"annotated")
    paper = Paper(
        title="A Paper Title", authors=["Ada Lastname"], year="2023",
        pdf_url="https://example.org/a.pdf",
    )
    fake = wired([], resolver=lambda identifier, email="", client=None: paper)
    monkeypatch.setattr(cli, "fetch_pdf", lambda url: pytest.fail("downloaded over the file"))

    result = CliRunner().invoke(
        cli.main, ["add", "2401.01234", "--citekey", "lastname2023paper"]
    )

    assert result.exit_code == 0, result.output
    assert orphan.read_bytes() == PDF + b"annotated"
    assert fake.created == [("lastname2023paper", "lastname2023paper My Copy.pdf")]


def test_names_differing_only_in_case_are_both_held(wired):
    """On a case-sensitive disk both names exist; neither may be renamed onto the other."""
    held = cli.FolderNames(["k2024a Graph Things.pdf", "k2024a graph things.pdf"])

    assert held.taken_by("k2024a Graph Things.pdf", "k2024a graph things.pdf") == (
        "k2024a Graph Things.pdf"
    )
    alone = cli.FolderNames(["k2024a graph things.pdf"])
    assert alone.taken_by("k2024a Graph Things.pdf", "k2024a graph things.pdf") is None


def test_an_uppercase_pdf_extension_is_seen_and_never_overwritten(wired, folder):
    annotated = folder / "smith2024graph New Title.PDF"
    annotated.write_bytes(PDF + b"annotated")
    (folder / "smith2024graph Old Title.pdf").write_bytes(PDF + b"fresh")
    wired([row("smith2024graph", "New Title", annotated.name)])

    run()

    assert sorted(p.read_bytes() for p in folder.iterdir()) == [
        PDF + b"annotated", PDF + b"fresh"
    ]


def test_the_file_the_row_names_wins_over_a_browser_copy(wired, folder):
    """On a retitle, the row's own file is renamed and the (1) copy is reported."""
    original = folder / "smith2024graph Old Title.pdf"
    original.write_bytes(PDF + b"annotated")
    (folder / "smith2024graph Old Title (1).pdf").write_bytes(PDF + b"fresh")
    fake = wired([row("smith2024graph", "New Title", original.name)])

    for args in (["--dry-run"], []):
        result = run(*args)
        assert "rename  smith2024graph Old Title.pdf  ->" in result.output
        assert "smith2024graph Old Title (1).pdf  (another copy of smith2024graph" in result.output

    assert (folder / "smith2024graph New Title.pdf").read_bytes() == PDF + b"annotated"
    assert fake.filenames == {"page-smith2024graph": "smith2024graph New Title.pdf"}


def test_an_unkeyed_copy_never_takes_the_row_from_its_keyed_file(wired, folder):
    original = folder / "lastname2023paper My Copy.pdf"
    original.write_bytes(PDF + b"annotated")
    (folder / "2401.01234.pdf").write_bytes(PDF + b"fresh")
    existing = row("lastname2023paper", "A Paper Title", original.name)
    existing["properties"]["arXiv ID"] = {"rich_text": _rich("2401.01234v1")}
    paper = Paper(
        title="A Paper Title", authors=["Ada Lastname"], year="2023", arxiv_id="2401.01234v1"
    )
    fake = wired([existing], resolver=lambda identifier, email="", client=None: paper)

    result = run()

    assert (folder / "lastname2023paper A Paper Title.pdf").read_bytes() == PDF + b"annotated"
    assert (folder / "Inbox" / "2401.01234.pdf").read_bytes() == PDF + b"fresh"
    assert "2401.01234.pdf  (another copy of lastname2023paper" in result.output
    assert fake.created == []


def test_binding_a_copy_to_an_existing_row_reports_its_highlights(wired, folder, monkeypatch):
    (folder / "2401.01234v1.pdf").write_bytes(PDF)
    existing = row("lastname2023paper", "A Paper Title")
    existing["properties"]["arXiv ID"] = {"rich_text": _rich("2401.01234v2")}
    paper = Paper(
        title="A Paper Title", authors=["Ada Lastname"], year="2023", arxiv_id="2401.01234v1"
    )
    wired([existing], resolver=lambda identifier, email="", client=None: paper)
    monkeypatch.setattr(cli, "extract", lambda pdf: ["a mark"])

    assert "check   highlights for lastname2023paper (1 found)" in run("--dry-run").output
    assert "update  highlights for lastname2023paper (1)" in run().output


def test_a_rename_never_replaces_a_file_that_appeared_mid_run(wired, folder, monkeypatch):
    """The folder is checked again right before each rename, not only at the start."""
    (folder / "smith2024graph Old Title.pdf").write_bytes(PDF + b"one")
    wired([row("smith2024graph", "New Title", "smith2024graph Old Title.pdf")])
    late = folder / "smith2024graph New Title.pdf"

    real_names = cli.FolderNames.__init__

    def snapshot_then_drop(self, held):
        real_names(self, held)
        late.write_bytes(PDF + b"late")

    monkeypatch.setattr(cli.FolderNames, "__init__", snapshot_then_drop)

    result = run()

    assert late.read_bytes() == PDF + b"late"
    assert (folder / "smith2024graph Old Title.pdf").read_bytes() == PDF + b"one"
    assert "already taken" in result.output


def _add(*args):
    return CliRunner().invoke(cli.main, ["add", "2401.01234", *args])


def test_add_never_downloads_over_a_file_with_the_same_name(wired, folder, monkeypatch):
    there = folder / "lastname2023paper A Paper Title.pdf"
    paper = Paper(
        title="A Paper Title", authors=["Ada Lastname"], year="2023",
        pdf_url="https://example.org/a.pdf",
    )
    wired([], resolver=lambda identifier, email="", client=None: paper)

    def fetch_after_a_file_lands(url):
        there.write_bytes(PDF + b"annotated")
        return PDF + b"download"

    monkeypatch.setattr(cli, "fetch_pdf", fetch_after_a_file_lands)

    result = _add()

    assert result.exit_code == 0, result.output
    assert there.read_bytes() == PDF + b"annotated"
    assert "already in the folder" in result.output


def test_add_with_a_key_on_two_files_records_the_original_not_the_copy(wired, folder):
    (folder / "lastname2023paper A Paper Title.pdf").write_bytes(PDF + b"annotated")
    (folder / "lastname2023paper A Paper Title (1).pdf").write_bytes(PDF + b"fresh")
    paper = Paper(title="A Paper Title", authors=["Ada Lastname"], year="2023")
    fake = wired([], resolver=lambda identifier, email="", client=None: paper)

    _add("--citekey", "lastname2023paper")

    assert fake.created == [("lastname2023paper", "lastname2023paper A Paper Title.pdf")]


def test_add_never_records_a_folder_as_the_paper(wired, folder):
    (folder / "lastname2023paper Dir.pdf").mkdir()
    paper = Paper(title="A Paper Title", authors=["Ada Lastname"], year="2023")
    fake = wired([], resolver=lambda identifier, email="", client=None: paper)

    _add("--citekey", "lastname2023paper", "--no-download")

    assert fake.created == [("lastname2023paper", "")]


def test_add_refuses_an_empty_citekey(wired, folder):
    paper = Paper(title="A Paper Title", authors=["Ada Lastname"], year="2023")
    fake = wired([], resolver=lambda identifier, email="", client=None: paper)

    result = _add("--citekey", "", "--no-download")

    assert result.exit_code == 1
    assert fake.created == []


def test_a_case_variant_is_never_renamed_over_on_a_case_sensitive_disk(wired, folder):
    (folder / "probe").write_bytes(b"")
    if (folder / "PROBE").exists():
        pytest.skip("this disk is case-insensitive")
    annotated = folder / "smith2024graph Graph Things.pdf"
    annotated.write_bytes(PDF + b"annotated")
    (folder / "smith2024graph graph things.pdf").write_bytes(PDF + b"fresh")
    wired([row("smith2024graph", "Graph Things", annotated.name)])

    run()

    assert annotated.read_bytes() == PDF + b"annotated"
    assert (folder / "smith2024graph graph things.pdf").read_bytes() == PDF + b"fresh"


# -- Inbox ---------------------------------------------------------------------


def _paper(identifier="", email="", client=None):
    return Paper(title="A Paper Title", authors=["Ada Lastname"], year="2023")


def test_a_pdf_dropped_in_the_inbox_is_filed_into_the_library(wired, folder):
    inbox = folder / "Inbox"
    inbox.mkdir()
    (inbox / "2401.01234.pdf").write_bytes(PDF + b"dropped")
    fake = wired([], resolver=_paper)

    result = run()

    assert (folder / "lastname2023paper A Paper Title.pdf").read_bytes() == PDF + b"dropped"
    assert list(inbox.iterdir()) == []
    assert fake.created == [("lastname2023paper", "lastname2023paper A Paper Title.pdf")]
    assert "adopt   Inbox/2401.01234.pdf  ->  lastname2023paper A Paper Title.pdf" in result.output


def test_a_pdf_dropped_in_the_library_goes_through_the_inbox(wired, folder):
    (folder / "2401.01234.pdf").write_bytes(PDF)
    fake = wired([], resolver=_paper)

    result = run()

    assert [p.name for p in folder.glob("*.pdf")] == ["lastname2023paper A Paper Title.pdf"]
    assert list((folder / "Inbox").iterdir()) == []
    assert fake.created == [("lastname2023paper", "lastname2023paper A Paper Title.pdf")]
    assert "inbox   2401.01234.pdf" in result.output


def test_an_unidentified_pdf_in_the_inbox_stays_there(wired, folder):
    inbox = folder / "Inbox"
    inbox.mkdir()
    (inbox / "8631_Counting.pdf").write_bytes(PDF)
    fake = wired([])

    result = run()

    assert (inbox / "8631_Counting.pdf").exists()
    assert fake.created == []
    assert "Inbox/8631_Counting.pdf  (no identifier" in result.output


def test_dry_run_shows_every_move_and_makes_none(wired, folder):
    inbox = folder / "Inbox"
    inbox.mkdir()
    (inbox / "2402.00001.pdf").write_bytes(PDF)
    (folder / "2401.01234.pdf").write_bytes(PDF)
    papers = {
        "2401.01234": Paper(title="First Paper", authors=["Ada Lastname"], year="2023"),
        "2402.00001": Paper(title="Second Paper", authors=["Bo Other"], year="2024"),
    }
    fake = wired([], resolver=lambda identifier, email="", client=None: papers[identifier])

    result = run("--dry-run")

    assert "inbox   2401.01234.pdf" in result.output
    assert "adopt   Inbox/2401.01234.pdf  ->  lastname2023first First Paper.pdf" in result.output
    assert "adopt   Inbox/2402.00001.pdf  ->  other2024second Second Paper.pdf" in result.output
    assert sorted(p.name for p in folder.rglob("*.pdf")) == ["2401.01234.pdf", "2402.00001.pdf"]
    assert fake.created == []


def test_an_inbox_copy_of_a_filed_paper_never_replaces_it(wired, folder):
    """Same name in both folders: the library copy is the paper's, the inbox one waits."""
    inbox = folder / "Inbox"
    inbox.mkdir()
    name = "smith2024graph Graph Things.pdf"
    (folder / name).write_bytes(PDF + b"annotated")
    (inbox / name).write_bytes(PDF + b"fresh")
    fake = wired([row("smith2024graph", "Graph Things", name)])

    result = run()

    assert (folder / name).read_bytes() == PDF + b"annotated"
    assert (inbox / name).read_bytes() == PDF + b"fresh"
    assert fake.filenames == {}
    assert f"Inbox/{name}  (another copy of smith2024graph" in result.output


def test_a_keyed_pdf_in_the_inbox_is_filed_when_its_row_has_no_file(wired, folder):
    inbox = folder / "Inbox"
    inbox.mkdir()
    (inbox / "smith2024graph Old Name.pdf").write_bytes(PDF)
    fake = wired([row("smith2024graph", "Graph Things")])

    run()

    assert (folder / "smith2024graph Graph Things.pdf").exists()
    assert list(inbox.iterdir()) == []
    assert fake.filenames == {"page-smith2024graph": "smith2024graph Graph Things.pdf"}


def test_a_stray_never_replaces_a_same_named_file_in_the_inbox(wired, folder):
    inbox = folder / "Inbox"
    inbox.mkdir()
    (folder / "notes.pdf").write_bytes(PDF + b"library")
    (inbox / "notes.pdf").write_bytes(PDF + b"inbox")
    wired([])

    result = run()

    assert (folder / "notes.pdf").read_bytes() == PDF + b"library"
    assert (inbox / "notes.pdf").read_bytes() == PDF + b"inbox"
    assert "already taken by Inbox/notes.pdf" in result.output


def test_init_creates_the_inbox(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "token", lambda: "token")
    monkeypatch.setattr(cli.notion, "Notion", lambda token: FakeNotion([]))
    monkeypatch.setattr(config, "save", lambda settings: tmp_path / "config.toml")

    result = CliRunner().invoke(cli.main, ["init", str(tmp_path / "Papers"), "--database", "db"])

    assert result.exit_code == 0, result.output
    assert (tmp_path / "Papers" / "Inbox").is_dir()
