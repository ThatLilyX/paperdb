import httpx
import pytest

from paperdb.metadata import (
    NotAPdfError,
    Paper,
    ResolveError,
    _openalex_pdf_url,
    classify,
    fetch_pdf,
    from_arxiv,
    from_doi,
)

ATOM = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom" xmlns:arxiv="http://arxiv.org/schemas/atom">
  <entry>
    <id>http://arxiv.org/abs/2401.01234v2</id>
    <published>2023-05-29T17:01:00Z</published>
    <title>A Paper
      Title</title>
    <summary>  An abstract with
      wrapped lines.  </summary>
    <author><name>Ada Lastname</name></author>
    <author><name>Bo Second</name></author>
    <arxiv:doi>10.48550/arXiv.2401.01234</arxiv:doi>
    <category term="cs.LG"/>
    <category term="cs.AI"/>
  </entry>
</feed>"""


def transport(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


@pytest.mark.parametrize(
    ("given", "expected"),
    [
        ("2401.01234", ("arxiv", "2401.01234")),
        ("arXiv:2401.01234v2", ("arxiv", "2401.01234v2")),
        ("https://arxiv.org/abs/2401.01234", ("arxiv", "2401.01234")),
        ("https://arxiv.org/pdf/2401.01234.pdf", ("arxiv", "2401.01234")),
        ("hep-th/9901001", ("arxiv", "hep-th/9901001")),
        ("10.48550/arXiv.2402.05678", ("arxiv", "2402.05678")),
        ("https://doi.org/10.48550/arXiv.2401.01234", ("arxiv", "2401.01234")),
        ("10.1038/nature12373", ("doi", "10.1038/nature12373")),
        ("doi:10.1038/nature12373", ("doi", "10.1038/nature12373")),
        ("https://doi.org/10.1038/nature12373", ("doi", "10.1038/nature12373")),
        ("https://example.com/paper", ("url", "https://example.com/paper")),
    ],
    ids=[
        "bare", "prefixed", "abs-url", "pdf-url", "old-style", "arxiv-doi",
        "arxiv-doi-url", "doi", "doi-prefixed", "doi-url", "other-url",
    ],
)
def test_classify(given, expected):
    assert classify(given) == expected


@pytest.mark.parametrize("given", ["", "   ", "not an id"], ids=["empty", "blank", "garbage"])
def test_classify_refuses_what_it_cannot_place(given):
    with pytest.raises(ResolveError):
        classify(given)


def test_from_arxiv_unwraps_the_title_and_abstract():
    paper = from_arxiv("2401.01234", client=transport(lambda r: httpx.Response(200, text=ATOM)))
    assert paper.title == "A Paper Title"
    assert paper.abstract == "An abstract with wrapped lines."
    assert paper.authors == ["Ada Lastname", "Bo Second"]
    assert paper.year == "2023"
    assert paper.arxiv_id == "2401.01234v2"
    assert paper.pdf_url == "https://arxiv.org/pdf/2401.01234v2"
    assert paper.topics == ["cs.LG", "cs.AI"]


def test_from_arxiv_raises_on_an_empty_feed():
    empty = '<feed xmlns="http://www.w3.org/2005/Atom"></feed>'
    with pytest.raises(ResolveError):
        from_arxiv("9999.99999", client=transport(lambda r: httpx.Response(200, text=empty)))


@pytest.mark.parametrize(
    ("work", "expected"),
    [
        ({"best_oa_location": {"pdf_url": "https://host/paper.pdf"}}, "https://host/paper.pdf"),
        ({"best_oa_location": {"pdf_url": None}}, ""),
        ({"best_oa_location": {"pdf_url": "https://doi.org/10.2139/ssrn.1"}}, ""),
        ({"best_oa_location": None, "locations": [{"pdf_url": "https://host/b.pdf"}]}, "https://host/b.pdf"),
        ({"best_oa_location": {"pdf_url": "https://doi.org/x"}, "locations": [{"pdf_url": "https://host/c.pdf"}]}, "https://host/c.pdf"),
        ({}, ""),
    ],
    ids=["real-pdf", "null-pdf", "doi-link-only", "falls-through-to-locations", "skips-doi-prefers-real", "nothing"],
)
def test_openalex_pdf_url_rejects_links_that_are_not_really_pdfs(work, expected):
    """`is_oa: true` is not evidence of a file.

    A work can be flagged open access while every location's `pdf_url` is null,
    or while it is the doi.org link that resolves to a publisher landing page.
    """
    assert _openalex_pdf_url(work) == expected


def test_from_doi_reads_unpaywall_s_url_for_pdf_not_openalex_s_pdf_url():
    """The two APIs name the field differently.

    Keying on `pdf_url` against Unpaywall reads a field that is never present, so
    every paper would be reported as having no PDF.
    """
    def handler(request: httpx.Request) -> httpx.Response:
        if "openalex" in str(request.url):
            return httpx.Response(200, json={
                "title": "A Paper", "publication_year": 2024,
                "best_oa_location": {"pdf_url": None},
                "authorships": [{"author": {"display_name": "Jane Smith"}}],
            })
        return httpx.Response(200, json={"best_oa_location": {"url_for_pdf": "https://repo/x.pdf"}})

    paper = from_doi("10.1/x", email="me@example.com", client=transport(handler))
    assert paper.pdf_url == "https://repo/x.pdf"
    assert paper.title == "A Paper"
    assert paper.authors == ["Jane Smith"]


def test_from_doi_404_is_a_resolve_error():
    with pytest.raises(ResolveError):
        from_doi("10.1/missing", client=transport(lambda r: httpx.Response(404, json={})))


@pytest.mark.parametrize(
    ("status", "body", "content_type"),
    [
        (200, b"<html>Sign in to continue</html>", "text/html"),
        (200, b"<html>Content Blocked</html>", "text/html"),
        (403, b"forbidden", "text/html"),
        (200, b"", "application/pdf"),
    ],
    ids=["login-wall", "bot-challenge", "forbidden", "empty-but-labelled-pdf"],
)
def test_fetch_pdf_refuses_anything_that_is_not_a_pdf(status, body, content_type):
    """The magic bytes decide, not the content type.

    A landing page, a paywall and a bot challenge all answer 200 with HTML, and
    some hosts mislabel. Writing any of them to disk as a .pdf is the failure this
    guards.
    """
    client = transport(lambda r: httpx.Response(status, content=body, headers={"content-type": content_type}))
    with pytest.raises(NotAPdfError):
        fetch_pdf("https://host/paper.pdf", client=client)


def test_fetch_pdf_accepts_a_real_pdf_even_when_mislabelled():
    pdf = b"%PDF-1.7\n trailer"
    client = transport(lambda r: httpx.Response(200, content=pdf, headers={"content-type": "application/octet-stream"}))
    assert fetch_pdf("https://host/paper.pdf", client=client) == pdf


@pytest.mark.parametrize(
    ("paper", "expected"),
    [
        (Paper(title="t", arxiv_id="2305.1"), "https://arxiv.org/abs/2305.1"),
        (Paper(title="t", doi="10.1/x"), "https://doi.org/10.1/x"),
        (Paper(title="t"), ""),
    ],
    ids=["arxiv", "doi", "neither"],
)
def test_source_url(paper, expected):
    assert paper.source_url == expected
