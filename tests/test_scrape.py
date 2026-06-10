"""Unit tests for the bulk-scrape feature: the pure fast-lane helpers (_looks_blocked,
_extract_static, string extractors) plus scrape_many's ordering/active-tab contract.
No browser is launched — engine='fast' with a monkeypatched _fast_fetch."""
import pytest
from roam.browser import BrowserController, _extract_static, _looks_blocked
from roam.config import Config
from roam.errors import RoamError
from roam.markdown import clean_html_str, extract_links_str, strip_to_text

PAGE = """<!doctype html>
<html><head><title>T</title>
<script src="/app.js"></script><style>.x{color:red}</style></head>
<body>
  <nav><a href="/home">Home</a> <a href="/about">About</a></nav>
  <div class="sidebar"><a href="/promo">Promo</a></div>
  <article>
    <h1>Big Story</h1>
    <p>This is the real article content with plenty of words to keep around.</p>
    <a href="/next">Read the follow-up piece</a>
    <img src="img/pic.png">
  </article>
  <footer><a href="/legal">Legal</a></footer>
</body></html>"""


# ---- _looks_blocked ----
def test_looks_blocked_on_block_status():
    assert _looks_blocked("<html>fine content</html>", 403)
    assert _looks_blocked("", 429)
    assert _looks_blocked("", 503)


def test_looks_blocked_on_cloudflare_marker():
    assert _looks_blocked("<title>Just a moment...</title>" + "x" * 600, 200)
    assert _looks_blocked("see /cdn-cgi/challenge for details" + "x" * 600, 200)


def test_looks_blocked_on_tiny_js_shell():
    assert _looks_blocked('<html><body id="root"></body></html>', 200)


def test_not_blocked_on_real_content():
    assert not _looks_blocked(PAGE + "x" * 600, 200)


# ---- _extract_static dispatch ----
def test_extract_static_markdown_drops_boilerplate():
    md = _extract_static(PAGE, "markdown", "https://ex.com/a/b")
    assert "Big Story" in md and "real article content" in md
    assert "Legal" not in md and "Promo" not in md      # footer/sidebar stripped
    assert "https://ex.com/next" in md                   # link absolutized


def test_extract_static_html_passthrough():
    assert _extract_static(PAGE, "html", "https://ex.com/") == PAGE


def test_extract_static_text():
    t = _extract_static(PAGE, "text", "https://ex.com/")
    assert "Big Story" in t and "real article content" in t
    assert "color:red" not in t and "app.js" not in t   # style/script contents dropped


def test_extract_static_links_absolutized():
    links = _extract_static(PAGE, "links", "https://ex.com/a/b")
    hrefs = [l["href"] for l in links]
    assert "https://ex.com/next" in hrefs and "https://ex.com/home" in hrefs


def test_extract_static_assets():
    a = _extract_static(PAGE, "assets", "https://ex.com/a/b")
    assert "https://ex.com/a/img/pic.png" in a["images"]
    assert "https://ex.com/app.js" in a["scripts"]


def test_extract_static_bad_fmt_raises():
    with pytest.raises(RoamError) as ei:
        _extract_static(PAGE, "yaml", "https://ex.com/")
    assert ei.value.code == "BAD_ARGS"


# ---- string extractors directly ----
def test_clean_html_str_prunes_link_dense_blocks():
    # tiny + almost-all-link-text block (a nav the blocklist misses) must be pruned
    html = ('<body><div class="widget"><a href="/x">One</a> <a href="/y">Two</a></div>'
            "<p>Real paragraph with enough words to clearly stay in the output text.</p></body>")
    out = clean_html_str(html)
    assert "Real paragraph" in out
    assert "One" not in out and "Two" not in out


def test_clean_html_str_keeps_wordy_blocks_with_links():
    html = ("<body><p>A long sentence of normal prose that happens to contain "
            '<a href="/in">one inline link</a> among many other words.</p></body>')
    out = clean_html_str(html)
    assert "inline link" in out and "normal prose" in out


def test_clean_html_str_picks_article_root():
    out = clean_html_str(PAGE, base_url="https://ex.com/")
    assert "Big Story" in out and "Home" not in out     # nav outside <article> gone


def test_strip_to_text_collapses_whitespace():
    assert strip_to_text("<p>a\n   b</p>\t<p>c</p>") == "a b c"
    assert strip_to_text("") == ""


def test_extract_links_str_dedupes_and_skips_js():
    html = ('<a href="/a">A</a><a href="/a">A again</a>'
            '<a href="javascript:void(0)">js</a><a href="mailto:x@y.z">mail</a>')
    links = extract_links_str(html, base_url="https://ex.com/")
    assert links == [{"text": "A", "href": "https://ex.com/a"}]


# ---- scrape_many contract (no browser: engine='fast' + canned _fast_fetch) ----
async def test_scrape_many_order_aligned_and_restores_active(tmp_path, monkeypatch):
    c = BrowserController(Config(headless=True, channel=None, profile_dir=str(tmp_path / "p")))

    async def fake_ensure():
        pass

    async def fake_fast(url, fmt="markdown", timeout_ms=None):
        if "boom" in url:
            return {"url": url, "ok": False, "error": "fast fetch blocked/failed (http 403)"}
        return {"url": url, "ok": True, "data": f"md:{url}"}

    monkeypatch.setattr(c, "ensure", fake_ensure)
    monkeypatch.setattr(c, "_fast_fetch", fake_fast)
    c.pages = {"t1": object()}
    c.active = "t1"
    urls = ["https://a.example/", "https://boom.example/", "https://c.example/"]
    res = await c.scrape_many(urls, concurrency=2, engine="fast")
    assert [r["url"] for r in res] == urls               # order-aligned with input
    assert res[0]["ok"] and res[2]["ok"] and not res[1]["ok"]
    assert res[0]["data"] == "md:https://a.example/"
    assert c.active == "t1"                              # batch never steals the active tab


async def test_scrape_many_fast_engine_reports_missing_dep(tmp_path, monkeypatch):
    # if curl_cffi import fails, engine='fast' must surface an actionable error per url
    c = BrowserController(Config(headless=True, channel=None, profile_dir=str(tmp_path / "p")))

    async def fake_ensure():
        pass

    monkeypatch.setattr(c, "ensure", fake_ensure)
    import builtins
    real_import = builtins.__import__

    def block_curl(name, *a, **k):
        if name.startswith("curl_cffi"):
            raise ImportError("nope")
        return real_import(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", block_curl)
    res = await c.scrape_many(["https://a.example/"], engine="fast")
    assert res[0]["ok"] is False and "curl_cffi" in res[0]["error"]
    assert "pip install curl_cffi" in res[0]["hint"]


def test_scrape_tool_in_registry():
    import roam.server as srv
    assert "scrape" in srv.TOOL_NAMES
    assert (srv._REGISTRY["scrape"].__doc__ or "").strip()
