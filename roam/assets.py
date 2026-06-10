"""List every sub-resource a page references (images, scripts, styles, fonts, media,
iframes, links) as ABSOLUTE URLs, categorized + flattened. Two twins, same dict shape:
ASSETS_JS runs in the rendered page (sees JS-injected assets); extract_assets_str parses
raw HTML offline for the fast no-render engine (stdlib only — no bs4 dependency)."""
import re
from html.parser import HTMLParser
from urllib.parse import urljoin

# Run in a rendered page: returns {images, scripts, styles, fonts, media, iframes, links, flat}.
# Pass a selector to scope the walk to one subtree (else the whole document).
ASSETS_JS = r"""
(selector) => {
  const root = (selector && document.querySelector(selector)) || document;
  const abs = (u) => { try { return new URL(u, location.href).href; } catch (e) { return null; } };
  const cats = { images: new Set(), scripts: new Set(), styles: new Set(), fonts: new Set(),
                 media: new Set(), iframes: new Set(), links: new Set() };
  const add = (set, u) => { const h = u && abs(u); if (h) set.add(h); };
  const srcset = (set, v) => (v || '').split(',').forEach(c => add(set, c.trim().split(/\s+/)[0]));
  root.querySelectorAll('img[src]').forEach(e => add(cats.images, e.getAttribute('src')));
  root.querySelectorAll('img[srcset]').forEach(e => srcset(cats.images, e.getAttribute('srcset')));
  root.querySelectorAll('picture source').forEach(e => {
    add(cats.images, e.getAttribute('src')); srcset(cats.images, e.getAttribute('srcset'));
  });
  root.querySelectorAll('script[src]').forEach(e => add(cats.scripts, e.getAttribute('src')));
  root.querySelectorAll('link[rel~="stylesheet" i][href]').forEach(e => add(cats.styles, e.getAttribute('href')));
  root.querySelectorAll('link[as="font" i][href]').forEach(e => add(cats.fonts, e.getAttribute('href')));
  root.querySelectorAll('video[src],audio[src]').forEach(e => add(cats.media, e.getAttribute('src')));
  root.querySelectorAll('video source[src],audio source[src]').forEach(e => add(cats.media, e.getAttribute('src')));
  root.querySelectorAll('iframe[src]').forEach(e => add(cats.iframes, e.getAttribute('src')));
  root.querySelectorAll('a[href]').forEach(e => add(cats.links, e.getAttribute('href')));
  const out = {}; const flat = new Set();
  for (const [k, set] of Object.entries(cats)) {
    out[k] = [...set];
    out[k].forEach(u => flat.add(u));
  }
  out.flat = [...flat];
  return out;
}
"""

_CATEGORIES = ("images", "scripts", "styles", "fonts", "media", "iframes", "links")


class _AssetParser(HTMLParser):
    """Collect the same tag/attr pairs ASSETS_JS walks, off raw HTML."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.cats = {k: [] for k in _CATEGORIES}   # lists keep insertion order; dedupe later
        self._in_picture = 0
        self._in_media = 0

    def _add(self, cat, url):
        if url:
            self.cats[cat].append(url)

    def _add_srcset(self, cat, value):
        for cand in (value or "").split(","):
            url = cand.strip().split()[0] if cand.strip() else ""
            self._add(cat, url)

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "picture":
            self._in_picture += 1
        elif tag in ("video", "audio"):
            self._in_media += 1
            self._add("media", a.get("src"))
        elif tag == "img":
            self._add("images", a.get("src"))
            self._add_srcset("images", a.get("srcset"))
        elif tag == "source":
            if self._in_picture:
                self._add("images", a.get("src"))
                self._add_srcset("images", a.get("srcset"))
            elif self._in_media:
                self._add("media", a.get("src"))
        elif tag == "script":
            self._add("scripts", a.get("src"))
        elif tag == "link":
            rel = (a.get("rel") or "").lower().split()
            if "stylesheet" in rel:
                self._add("styles", a.get("href"))
            elif (a.get("as") or "").lower() == "font":
                self._add("fonts", a.get("href"))
        elif tag == "iframe":
            self._add("iframes", a.get("src"))
        elif tag == "a":
            self._add("links", a.get("href"))

    def handle_endtag(self, tag):
        if tag == "picture" and self._in_picture:
            self._in_picture -= 1
        elif tag in ("video", "audio") and self._in_media:
            self._in_media -= 1


def extract_assets_str(html, base_url=None):
    """OFFLINE twin of ASSETS_JS for raw HTML: same categorized dict + flat list, every URL
    absolutized against base_url. stdlib HTMLParser only (the fast lane must not need bs4)."""
    p = _AssetParser()
    try:
        p.feed(html or "")
    except Exception:
        pass   # malformed HTML: keep whatever parsed before the choke
    out = {}
    flat_seen, flat = set(), []
    for cat in _CATEGORIES:
        seen, urls = set(), []
        for u in p.cats[cat]:
            if base_url:
                try:
                    u = urljoin(base_url, u)
                except Exception:
                    continue
            if u in seen:
                continue
            seen.add(u)
            urls.append(u)
            if u not in flat_seen:
                flat_seen.add(u)
                flat.append(u)
        out[cat] = urls
    out["flat"] = flat
    return out
