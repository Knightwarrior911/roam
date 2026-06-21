"""P3.3: BM25 query-focused read_markdown."""
from roam.relevance import bm25_filter, _split_chunks


_DOC = """# Annual Report

## Revenue

Total revenue grew 18% year over year to $4.2 billion, driven by cloud subscriptions.

## Interest Rate Swaps

The company entered interest rate swap agreements to hedge floating-rate debt exposure,
converting $1.0 billion of variable-rate borrowings to fixed rates.

## Employees

We had 12,400 full-time employees at year end across 14 offices.

## Marketing

Brand campaigns ran in Q3 across social and television channels.
"""


def test_split_chunks_keeps_headings_atomic():
    chunks = _split_chunks(_DOC)
    assert any(c.startswith("# Annual Report") for c in chunks)
    assert any("interest rate swap" in c.lower() for c in chunks)


def test_bm25_keeps_relevant_drops_irrelevant():
    out = bm25_filter(_DOC, "interest rate swap pricing hedge")
    assert "interest rate swap" in out.lower()
    # an unrelated section should be dropped
    assert "Brand campaigns" not in out


def test_bm25_empty_query_returns_full():
    assert bm25_filter(_DOC, "") == _DOC
    assert bm25_filter(_DOC, None) == _DOC


def test_bm25_short_doc_returns_full():
    short = "# Title\n\nOne paragraph only."
    assert bm25_filter(short, "anything") == short


def test_bm25_no_match_falls_back_to_full():
    # query terms absent from the doc -> return everything rather than nothing
    out = bm25_filter(_DOC, "zzz qqq xxx")
    assert out == _DOC


def test_readability_extracts_main_content():
    from roam.markdown import readability_markdown
    html = ("<html><body><nav>Home About Contact</nav>"
            "<article><h1>Big News</h1><p>This is the substantive article body that "
            "trafilatura keeps as the main content of the page, long enough to score.</p>"
            "<p>A second meaningful paragraph adds more detail and substance here.</p>"
            "</article><footer>copyright junk</footer></body></html>")
    md = readability_markdown(html, url="https://x.example/post")
    assert "substantive article body" in md
    assert "copyright junk" not in md
