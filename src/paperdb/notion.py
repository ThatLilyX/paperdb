"""The Notion side: one database of papers, one page body per paper.

No PDF is ever uploaded. The row records ``Filename``, relative to the configured
folder, so moving the folder or opening the database from another machine leaves
every row still correct.
"""

from __future__ import annotations

import httpx

from .metadata import Paper

API = "https://api.notion.com/v1"
NOTION_VERSION = "2022-06-28"

# Notion rejects a request body over 2000 characters in a single rich-text run,
# and caps children per append.
RUN_LIMIT = 2000
CHILDREN_PER_REQUEST = 100

HIGHLIGHTS_HEADING = "Highlights"
ABSTRACT_HEADING = "Abstract"
FOCUS_HEADING = "What to focus on"


class NotionError(RuntimeError):
    """Raised when Notion refuses a request."""


def _rich(text: str) -> list[dict]:
    """Rich text, split into runs Notion will accept."""
    text = text or ""
    return [
        {"type": "text", "text": {"content": text[i : i + RUN_LIMIT]}}
        for i in range(0, max(len(text), 1), RUN_LIMIT)
    ]


def heading(text: str) -> dict:
    return {
        "object": "block",
        "type": "heading_2",
        "heading_2": {"rich_text": _rich(text)},
    }


def paragraph(text: str) -> dict:
    return {
        "object": "block",
        "type": "paragraph",
        "paragraph": {"rich_text": _rich(text)},
    }


def quote(text: str) -> dict:
    return {"object": "block", "type": "quote", "quote": {"rich_text": _rich(text)}}


def bullet(text: str) -> dict:
    return {
        "object": "block",
        "type": "bulleted_list_item",
        "bulleted_list_item": {"rich_text": _rich(text)},
    }


class Notion:
    """A small client over the endpoints paperdb needs."""

    def __init__(self, token: str, client: httpx.Client | None = None):
        self._client = client or httpx.Client(timeout=60.0)
        self._client.headers.update(
            {
                "Authorization": f"Bearer {token}",
                "Notion-Version": NOTION_VERSION,
                "Content-Type": "application/json",
            }
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

    def _call(self, method: str, path: str, **kwargs) -> dict:
        response = self._client.request(method, f"{API}{path}", **kwargs)
        if response.status_code >= 400:
            try:
                detail = response.json().get("message", response.text)
            except ValueError:
                detail = response.text
            raise NotionError(f"{method} {path} -> {response.status_code}: {detail}")
        return response.json()

    # -- schema ----------------------------------------------------------------

    SCHEMA = {
        "Name": {"title": {}},
        "Citekey": {"rich_text": {}},
        "Authors": {"rich_text": {}},
        "Venue": {"rich_text": {}},
        "Year": {"number": {"format": "number"}},
        "DOI": {"url": {}},
        "arXiv ID": {"rich_text": {}},
        "Filename": {"rich_text": {}},
        "Topics": {"multi_select": {}},
    }

    def create_database(self, parent_page_id: str, title: str = "Papers") -> str:
        """Create the papers database under a page, and return its id."""
        payload = {
            "parent": {"type": "page_id", "page_id": parent_page_id},
            "icon": {"type": "emoji", "emoji": "\N{BOOKS}"},
            "title": [{"type": "text", "text": {"content": title}}],
            "properties": self.SCHEMA,
        }
        return self._call("POST", "/databases", json=payload)["id"]

    def add_schedule_relation(
        self, database_id: str, schedule_database_id: str
    ) -> None:
        """Relate the papers database to an existing reading-schedule database.

        Two-way, so the schedule gains a ``Paper`` property and a scheduled row
        can be opened from the paper and the paper from the row.
        """
        self._call(
            "PATCH",
            f"/databases/{database_id}",
            json={
                "properties": {
                    "Plan": {
                        "relation": {
                            "database_id": schedule_database_id,
                            "type": "dual_property",
                            "dual_property": {},
                        }
                    }
                }
            },
        )

    # -- rows ------------------------------------------------------------------

    def _properties(self, paper: Paper, filename: str) -> dict:
        properties: dict = {
            "Name": {"title": _rich(paper.title)},
            "Citekey": {"rich_text": _rich(paper.citekey)},
            "Authors": {"rich_text": _rich(", ".join(paper.authors))},
            "Venue": {"rich_text": _rich(paper.venue)},
            "Filename": {"rich_text": _rich(filename)},
            "Topics": {
                "multi_select": [{"name": t[:100]} for t in paper.topics[:10]]
            },
            "arXiv ID": {"rich_text": _rich(paper.arxiv_id)},
        }
        if paper.year.isdigit():
            properties["Year"] = {"number": int(paper.year)}
        if paper.doi:
            properties["DOI"] = {"url": f"https://doi.org/{paper.doi}"}
        return properties

    def body_blocks(self, paper: Paper, highlights=()) -> list[dict]:
        """The page body: abstract, a focus heading, and any highlights."""
        blocks = [heading(ABSTRACT_HEADING)]
        blocks.append(
            paragraph(
                " · ".join(
                    part
                    for part in (
                        ", ".join(paper.authors),
                        paper.venue,
                        paper.year,
                        paper.source_url,
                    )
                    if part
                )
            )
        )
        blocks.append(quote(paper.abstract or "No abstract available."))
        blocks.append(heading(FOCUS_HEADING))
        blocks.append(paragraph(""))
        if highlights:
            blocks.append(heading(HIGHLIGHTS_HEADING))
            blocks.extend(bullet(h) for h in highlights)
        return blocks

    def create_row(self, database_id: str, paper: Paper, filename: str, highlights=()) -> str:
        """Create the row and write its body. Returns the page id."""
        page = self._call(
            "POST",
            "/pages",
            json={
                "parent": {"database_id": database_id},
                "icon": {"type": "emoji", "emoji": "\N{PAGE FACING UP}"},
                "properties": self._properties(paper, filename),
            },
        )
        self.append(page["id"], self.body_blocks(paper, highlights))
        return page["id"]

    def update_row(self, page_id: str, properties: dict) -> None:
        self._call("PATCH", f"/pages/{page_id}", json={"properties": properties})

    def set_filename(self, page_id: str, filename: str) -> None:
        self.update_row(page_id, {"Filename": {"rich_text": _rich(filename)}})

    def append(self, page_id: str, blocks: list[dict]) -> None:
        for start in range(0, len(blocks), CHILDREN_PER_REQUEST):
            self._call(
                "PATCH",
                f"/blocks/{page_id}/children",
                json={"children": blocks[start : start + CHILDREN_PER_REQUEST]},
            )

    def rows(self, database_id: str):
        """Every row, paged."""
        cursor = None
        while True:
            payload = {"page_size": 100}
            if cursor:
                payload["start_cursor"] = cursor
            data = self._call("POST", f"/databases/{database_id}/query", json=payload)
            yield from data["results"]
            if not data.get("has_more"):
                return
            cursor = data["next_cursor"]

    # -- highlights ------------------------------------------------------------

    def replace_highlights(self, page_id: str, highlights) -> bool:
        """Rewrite only the Highlights section of a page body.

        Everything above the ``## Highlights`` heading is left alone, so a focus
        note written by hand survives. Within the section the rewrite is a
        replace, not an append: the PDF is the source of truth for what is
        highlighted, so a note typed under that heading in Notion is lost. Returns
        whether anything changed.
        """
        children = self._call("GET", f"/blocks/{page_id}/children?page_size=100")["results"]
        existing_start = None
        for index, block in enumerate(children):
            if block["type"] == "heading_2":
                text = "".join(
                    run["plain_text"] for run in block["heading_2"]["rich_text"]
                )
                if text.strip() == HIGHLIGHTS_HEADING:
                    existing_start = index
                    break

        current: list[str] = []
        if existing_start is not None:
            for block in children[existing_start + 1 :]:
                if block["type"] == "bulleted_list_item":
                    current.append(
                        "".join(
                            run["plain_text"]
                            for run in block["bulleted_list_item"]["rich_text"]
                        )
                    )
        if current == list(highlights):
            return False

        if existing_start is not None:
            for block in children[existing_start:]:
                self._call("DELETE", f"/blocks/{block['id']}")
        if highlights:
            self.append(page_id, [heading(HIGHLIGHTS_HEADING)] + [bullet(h) for h in highlights])
        return True


def row_text(row: dict, name: str) -> str:
    """A rich-text or title property's plain value.

    Notion's responses carry ``plain_text`` on every run; the payloads paperdb
    sends carry only ``text.content``. Reading both means a property can be read
    back from what was written to it, which is what makes the round trip
    testable without a recorded response.
    """
    prop = (row.get("properties") or {}).get(name) or {}
    runs = prop.get("rich_text") or prop.get("title") or []
    return "".join(
        run.get("plain_text") or (run.get("text") or {}).get("content") or ""
        for run in runs
    )


def row_paper(row: dict) -> Paper:
    """Turn a row back into a :class:`Paper`, for the bibtex export."""
    properties = row.get("properties") or {}
    year = properties.get("Year", {}).get("number")
    doi_url = properties.get("DOI", {}).get("url") or ""
    return Paper(
        title=row_text(row, "Name"),
        authors=[a.strip() for a in row_text(row, "Authors").split(",") if a.strip()],
        year=str(year) if year else "",
        venue=row_text(row, "Venue"),
        doi=doi_url.removeprefix("https://doi.org/"),
        arxiv_id=row_text(row, "arXiv ID"),
        citekey=row_text(row, "Citekey"),
    )
