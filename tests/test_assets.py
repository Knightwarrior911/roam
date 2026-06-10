"""Unit tests for assets listing: the offline extractor (extract_assets_str) carries the
assertions — the in-page ASSETS_JS twin shares the shape and is sanity-checked as a string."""
import pytest
from roam.assets import ASSETS_JS, extract_assets_str

HTML = """<!doctype html>
<html><head>
  <script src="/js/app.js"></script>
  <script>inline()</script>
  <link rel="stylesheet" href="/css/site.css">
  <link rel="preload" as="font" href="/fonts/inter.woff2">
</head>
<body>
  <img src="img/one.png">
  <img srcset="img/two-1x.png 1x, img/two-2x.png 2x">
  <picture><source srcset="img/three.webp"><img src="img/three.png"></picture>
  <video src="/vid/clip.mp4"></video>
  <audio><source src="/aud/track.mp3"></audio>
  <iframe src="https://other.example/embed"></iframe>
  <a href="/page">Page</a>
  <a href="/page">Page duplicate</a>
</body></html>"""

BASE = "https://ex.com/dir/index.html"


def test_assets_categories_and_absolutization():
    a = extract_assets_str(HTML, base_url=BASE)
    assert "https://ex.com/dir/img/one.png" in a["images"]
    assert "https://ex.com/dir/img/two-1x.png" in a["images"]      # srcset parsed
    assert "https://ex.com/dir/img/two-2x.png" in a["images"]
    assert "https://ex.com/dir/img/three.webp" in a["images"]      # picture source srcset
    assert "https://ex.com/dir/img/three.png" in a["images"]
    assert a["scripts"] == ["https://ex.com/js/app.js"]            # inline script skipped
    assert a["styles"] == ["https://ex.com/css/site.css"]
    assert a["fonts"] == ["https://ex.com/fonts/inter.woff2"]
    assert "https://ex.com/vid/clip.mp4" in a["media"]
    assert "https://ex.com/aud/track.mp3" in a["media"]            # source inside audio
    assert a["iframes"] == ["https://other.example/embed"]
    assert a["links"] == ["https://ex.com/page"]                   # deduped


def test_assets_flat_is_union_without_duplicates():
    a = extract_assets_str(HTML, base_url=BASE)
    every = [u for k, v in a.items() if k != "flat" for u in v]
    assert set(a["flat"]) == set(every)
    assert len(a["flat"]) == len(set(a["flat"]))


def test_assets_shape_on_empty_input():
    a = extract_assets_str("")
    assert set(a) == {"images", "scripts", "styles", "fonts", "media", "iframes", "links", "flat"}
    assert all(v == [] for v in a.values())


def test_assets_relative_kept_without_base():
    a = extract_assets_str('<img src="x.png">')
    assert a["images"] == ["x.png"]


def test_assets_js_twin_covers_same_categories():
    # can't run JS headless here; assert the in-page twin walks every category + srcset
    for key in ("images", "scripts", "styles", "fonts", "media", "iframes", "links", "flat"):
        assert key in ASSETS_JS
    assert "srcset" in ASSETS_JS and "picture source" in ASSETS_JS


def test_assets_tool_in_registry():
    import roam.server as srv
    assert "assets" in srv.TOOL_NAMES
    assert (srv._REGISTRY["assets"].__doc__ or "").strip()


async def test_assets_kinds_filter(tmp_path, monkeypatch):
    # kinds filtering happens controller-side: feed a canned page.evaluate result
    from roam.browser import BrowserController
    from roam.config import Config
    c = BrowserController(Config(headless=True, channel=None, profile_dir=str(tmp_path / "p")))
    canned = {"images": ["i"], "scripts": ["s"], "styles": [], "fonts": [], "media": [],
              "iframes": [], "links": ["l"], "flat": ["i", "s", "l"]}

    class FakePage:
        async def evaluate(self, js, arg=None):
            return dict(canned)

    async def fake_current(tab=None):
        return FakePage()

    monkeypatch.setattr(c, "current_page", fake_current)
    a = await c.assets(kinds=["images"])
    assert set(a) == {"images", "flat"}
    assert a["images"] == ["i"] and a["flat"] == ["i", "s", "l"]   # flat always kept whole
