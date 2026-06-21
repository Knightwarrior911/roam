"""P2.5/2.6/2.7/2.8: content-cleaner accuracy + single-source-of-truth parity."""
import pathlib
import re

import pytest

from roam import markdown as m

ROOT = pathlib.Path(__file__).resolve().parent.parent
BG = ROOT / "extension" / "background.js"


def _clean(html, base=None):
    return m.clean_html_str(html, base_url=base)


# ---- expanded blocklist (P2.6) ----
def test_blocklist_strips_adtech_and_consent():
    html = ('<article><p>Long real content paragraph here for sure.</p>'
            '<div class="ad-slot">ADX</div><div class="OUTBRAIN">OBX</div>'
            '<aside role="complementary">SIDEX</aside><div data-test="ad">DTX</div>'
            '<div class="cookie-banner">COOKIEX</div></article>')
    out = _clean(html)
    for junk in ("ADX", "OBX", "SIDEX", "DTX", "COOKIEX"):
        assert junk not in out, f"{junk} leaked: {out}"
    assert "Long real content" in out


def test_force_include_protects_article_in_junkish_class():
    # article wrapped in a class that the blocklist would otherwise hit
    html = '<div class="related article-body"><p>The real article body text is here.</p></div>'
    out = _clean(html)
    assert "real article body" in out


# ---- embeds + forms kept (P2.5) ----
def test_youtube_embed_becomes_link():
    html = '<article><iframe src="https://www.youtube.com/embed/abc"></iframe><p>x text body here</p></article>'
    out = _clean(html)
    assert "youtube.com/embed/abc" in out


def test_unknown_iframe_dropped():
    html = '<article><iframe src="https://tracker.evil/pixel"></iframe><p>x text body here</p></article>'
    out = _clean(html)
    assert "tracker.evil" not in out


def test_form_preserved_as_control_list():
    html = '<article><form action="/s"><input name="q"><button>Go</button></form><p>body text here ok</p></article>'
    out = _clean(html)
    assert "Form:" in out and "q (" in out


# ---- base href (P2.7) ----
def test_base_href_governs_link_resolution():
    html = ('<head><base href="https://cdn.example.com/"></head>'
            '<article><a href="/page">L</a><p>real content body sentence here</p></article>')
    out = _clean(html, base="https://origin.example.org/")
    assert "https://cdn.example.com/page" in out


# ---- single source of truth (P2.8) ----
def test_clean_fn_junk_in_sync_with_python():
    """The bridge CLEAN_FN junk list must equal the Python _JUNK_LIST. If this fails,
    run `py tools/sync_inject.py`."""
    bg = BG.read_text(encoding="utf-8")
    mobj = re.search(r"const junk = '([^']*)';", bg)
    assert mobj, "CLEAN_FN junk literal not found in background.js"
    js_list = mobj.group(1).split(",")
    assert js_list == m._JUNK_LIST, "CLEAN_FN drifted from roam/markdown.py _JUNK_LIST"


def test_clean_html_js_placeholders_substituted():
    assert "__JS_JUNK__" not in m.CLEAN_HTML_JS
    assert "__EMBED_RE__" not in m.CLEAN_HTML_JS
