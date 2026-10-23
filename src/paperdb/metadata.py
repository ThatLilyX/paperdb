"""Resolving an identifier to a paper, and fetching its PDF.

Two sources, both free and keyless: arXiv's Atom API for arXiv ids, OpenAlex for
DOIs. OpenAlex is asked with a ``mailto`` so the request lands in its polite
pool; without one it is rate-limited more aggressively.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field

import httpx

ARXIV_API = "https://export.arxiv.org/api/query"
OPENALEX_API = "https://api.openalex.org/works"
UNPAYWALL_API = "https://api.unpaywall.org/v2"

USER_AGENT = "paperdb/0.1 (+https://github.com/ThatLilyX/paperdb)"

# arXiv asks for 15 seconds between automated requests in its robots.txt. One
# interactive `add` is well inside that; a backfill must pace itself, which is
# what `sync` uses this for.
ARXIV_CRAWL_DELAY_SECONDS = 15

_ARXIV_NEW = re.compile(r"^(\d{4}\.\d{4,5})(v\d+)?$")
_ARXIV_OLD = re.compile(r"^([a-z-]+(?:\.[A-Z]{2})?/\d{7})(v\d+)?$")
_DOI = re.compile(r"^10\.\d{4,9}/\S+$", re.IGNORECASE)
# arXiv mints a DOI for every preprint. It is a DOI, but routing it to OpenAlex
# throws away the abstract and the categories that the arXiv API returns, so it
# is unwrapped back to the id it contains.
_ARXIV_DOI = re.compile(r"^10\.48550/arxiv\.(.+)$", re.IGNORECASE)


class ResolveError(RuntimeError):
    """Raised when an identifier cannot be turned into a paper."""


class NotAPdfError(RuntimeError):
    """Raised when a URL advertised as a PDF does not serve one.

    This is its own error because the failure is common and specific: OpenAlex
    and Unpaywall both report ``is_oa: true`` for papers whose only "PDF" link is
    a DOI that resolves to a publisher landing page.
    """


@dataclass
class Paper:
    """What paperdb knows about one paper. Every field may be empty but ``title``."""

    title: str
    authors: list[str] = field(default_factory=list)
    year: str = ""
    venue: str = ""
    doi: str = ""
    arxiv_id: str = ""
    abstract: str = ""
    topics: list[str] = field(default_factory=list)
    pdf_url: str = ""
    citekey: str = ""

    @property
    def first_author(self) -> str:
        return self.authors[0] if self.authors else ""

    def identities(self) -> set[str]:
        """Strings that two records share exactly when they are the same paper.

        arXiv versions are dropped, so ``v1`` and ``v2`` match, and DOIs are
        compared without case, as the DOI system does.
        """
        ids = set()
        if self.arxiv_id:
            versioned = _ARXIV_NEW.match(self.arxiv_id) or _ARXIV_OLD.match(self.arxiv_id)
            ids.add("arxiv:" + (versioned.group(1) if versioned else self.arxiv_id).lower())
        if self.doi:
            ids.add("doi:" + self.doi.lower())
        return ids

    @property
    def source_url(self) -> str:
        if self.arxiv_id:
            return f"https://arxiv.org/abs/{self.arxiv_id}"
        if self.doi:
            return f"https://doi.org/{self.doi}"
        return ""


def classify(identifier: str) -> tuple[str, str]:
    """Sort a user-supplied string into ``("arxiv"|"doi"|"url", value)``."""
    text = identifier.strip()
    if not text:
        raise ResolveError("empty identifier")

    lowered = text.lower()
    if lowered.startswith("arxiv:"):
        text = text.split(":", 1)[1]
    elif "arxiv.org/" in lowered:
        tail = re.split(r"arxiv\.org/(?:abs|pdf|html)/", text, flags=re.I)[-1]
        text = tail.removesuffix(".pdf")
    elif lowered.startswith(("http://doi.org/", "https://doi.org/")):
        text = text.split("doi.org/", 1)[1]
    elif lowered.startswith("doi:"):
        text = text.split(":", 1)[1]

    arxiv_doi = _ARXIV_DOI.match(text)
    if arxiv_doi:
        text = arxiv_doi.group(1)

    if _ARXIV_NEW.match(text) or _ARXIV_OLD.match(text):
        return "arxiv", text
    if _DOI.match(text):
        return "doi", text
    if lowered.startswith(("http://", "https://")):
        return "url", identifier.strip()
    raise ResolveError(
        f"cannot tell what {identifier!r} is: expected an arXiv id, a DOI or a URL"
    )


def _client(timeout: float = 30.0) -> httpx.Client:
    return httpx.Client(
        timeout=timeout, follow_redirects=True, headers={"User-Agent": USER_AGENT}
    )


def from_arxiv(arxiv_id: str, client: httpx.Client | None = None) -> Paper:
    """Resolve an arXiv id through the Atom API."""
    owned = client is None
    client = client or _client()
    try:
        response = client.get(ARXIV_API, params={"id_list": arxiv_id})
        response.raise_for_status()
        root = ET.fromstring(response.text)
    finally:
        if owned:
            client.close()

    ns = {"a": "http://www.w3.org/2005/Atom", "arxiv": "http://arxiv.org/schemas/atom"}
    entry = root.find("a:entry", ns)
    if entry is None or entry.find("a:title", ns) is None:
        raise ResolveError(f"arXiv has no entry for {arxiv_id}")

    def text_of(path: str) -> str:
        node = entry.find(path, ns)
        return " ".join(node.text.split()) if node is not None and node.text else ""

    published = text_of("a:published")
    journal_ref = text_of("arxiv:journal_ref")
    doi = text_of("arxiv:doi")
    version_id = text_of("a:id").rsplit("/", 1)[-1] or arxiv_id

    return Paper(
        title=text_of("a:title"),
        authors=[
            " ".join(n.text.split())
            for n in entry.findall("a:author/a:name", ns)
            if n.text
        ],
        year=published[:4],
        venue=journal_ref or "arXiv",
        doi=doi,
        arxiv_id=version_id,
        abstract=text_of("a:summary"),
        topics=[
            c.attrib["term"]
            for c in entry.findall("a:category", ns)
            if "term" in c.attrib
        ],
        pdf_url=f"https://arxiv.org/pdf/{version_id}",
    )


def _openalex_pdf_url(work: dict) -> str:
    """The first OA location that offers a real PDF url.

    ``is_oa`` alone is not evidence of a downloadable file: a work can be flagged
    open access while every location's ``pdf_url`` is null, or while it is the
    ``https://doi.org/...`` link that resolves to a landing page. Those are
    filtered here so the caller never sees them.
    """
    locations = [work.get("best_oa_location")] + list(work.get("locations") or [])
    for location in locations:
        url = (location or {}).get("pdf_url") or ""
        if url and "doi.org/" not in url:
            return url
    return ""


def from_doi(doi: str, email: str = "", client: httpx.Client | None = None) -> Paper:
    """Resolve a DOI through OpenAlex, falling back to Unpaywall for a PDF."""
    owned = client is None
    client = client or _client()
    try:
        params = {"mailto": email} if email else {}
        response = client.get(f"{OPENALEX_API}/doi:{doi}", params=params)
        if response.status_code == 404:
            raise ResolveError(f"OpenAlex has no record for {doi}")
        response.raise_for_status()
        work = response.json()

        pdf_url = _openalex_pdf_url(work)
        if not pdf_url and email:
            pdf_url = _unpaywall_pdf_url(doi, email, client)
    finally:
        if owned:
            client.close()

    primary = work.get("primary_location") or {}
    source = primary.get("source") or {}
    return Paper(
        title=work.get("title") or work.get("display_name") or "",
        authors=[
            (a.get("author") or {}).get("display_name", "")
            for a in (work.get("authorships") or [])
            if (a.get("author") or {}).get("display_name")
        ],
        year=str(work.get("publication_year") or ""),
        venue=source.get("display_name") or "",
        doi=doi,
        abstract=work.get("abstract") or "",
        topics=[t["display_name"] for t in (work.get("topics") or []) if t.get("display_name")],
        pdf_url=pdf_url,
    )


def _unpaywall_pdf_url(doi: str, email: str, client: httpx.Client) -> str:
    """Unpaywall's PDF link, if it has one.

    Unpaywall's field is ``url_for_pdf``, not OpenAlex's ``pdf_url``. Keying on
    the wrong name reads a field that is never present and silently reports every
    paper as having no PDF.
    """
    try:
        response = client.get(f"{UNPAYWALL_API}/{doi}", params={"email": email})
        response.raise_for_status()
        best = response.json().get("best_oa_location") or {}
    except (httpx.HTTPError, ValueError):
        return ""
    url = best.get("url_for_pdf") or ""
    return "" if "doi.org/" in url else url


def resolve(identifier: str, email: str = "", client: httpx.Client | None = None) -> Paper:
    """Resolve any supported identifier to a :class:`Paper`."""
    kind, value = classify(identifier)
    if kind == "arxiv":
        return from_arxiv(value, client=client)
    if kind == "doi":
        return from_doi(value, email=email, client=client)
    raise ResolveError(
        f"cannot resolve a bare URL yet: {value}. Pass its arXiv id or DOI, or "
        "download the PDF into the folder and run `paperdb sync`."
    )


def fetch_pdf(url: str, client: httpx.Client | None = None) -> bytes:
    """Download ``url``, insisting that what comes back is really a PDF.

    A publisher landing page, a login wall and a bot-challenge page all answer
    200 with HTML. Both the declared content type and the file's own magic bytes
    are checked, because some hosts serve a PDF as ``application/octet-stream``
    and others serve HTML labelled as a PDF.
    """
    owned = client is None
    client = client or _client(timeout=120.0)
    try:
        response = client.get(url)
        response.raise_for_status()
        body = response.content
    except httpx.HTTPStatusError as error:
        raise NotAPdfError(
            f"{url} answered {error.response.status_code}"
        ) from error
    finally:
        if owned:
            client.close()

    if not body.startswith(b"%PDF-"):
        content_type = response.headers.get("content-type", "unknown")
        raise NotAPdfError(
            f"{url} served {content_type}, not a PDF "
            "(a landing page, a paywall or a bot challenge)"
        )
    return body
