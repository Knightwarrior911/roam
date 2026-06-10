"""Clean a rendered page into LLM-ready markdown (the Firecrawl approach, done client-side):
strip boilerplate via a curated blocklist, prune tiny link-dense blocks (the crawl4ai trick),
resolve links/images to absolute, pick the main content, then convert to markdown. Roughly
5x fewer tokens than raw HTML and far more useful to an agent than document.body.innerText
(it keeps headings, lists, tables, links).

Also hosts the OFFLINE twins of the in-page extractors (clean_html_str / strip_to_text /
extract_links_str) so the fast no-render engine can produce the same representations from
raw HTML without a browser.
"""
import re
from html.parser import HTMLParser
from urllib.parse import urljoin

from markdownify import markdownify as _md

# Returns the cleaned main-content innerHTML of the live (JS-rendered) page.
CLEAN_HTML_JS = r"""
(selector) => {
  const doc = document.cloneNode(true);
  doc.querySelectorAll('script,style,noscript,iframe,svg,template,link,form').forEach(e => e.remove());
  const junk = [
    'header','footer','nav','aside','[role="navigation"]','[role="banner"]','[role="contentinfo"]',
    '.nav','.navbar','.sidebar','.menu','.ad','.ads','.advert','.advertisement','.social','.share',
    '.breadcrumbs','.cookie','.popup','.modal','.newsletter','.promo','.related','.recommended',
    '[class*="paywall" i]','[id*="comments" i]','[class*="comments" i]'
  ].join(',');
  doc.querySelectorAll(junk).forEach(e => e.remove());
  // crawl4ai-style pruning: blocks that are tiny AND mostly link text are leftover nav /
  // boilerplate the blocklist missed (menus, breadcrumbs, tag clouds). Defensive: never throw.
  try {
    doc.querySelectorAll('div,section,article,p,li').forEach(el => {
      const text = (el.textContent || '').trim();
      const words = text ? text.split(/\s+/).length : 0;
      if (words >= 5) return;
      let linkLen = 0;
      el.querySelectorAll('a').forEach(a => { linkLen += ((a.textContent || '').trim()).length; });
      if (text.length && linkLen / text.length > 0.8) el.remove();
    });
  } catch (e) {}
  const abs = (el, attr) => { try { el.setAttribute(attr, new URL(el.getAttribute(attr), location.href).href); } catch (e) {} };
  doc.querySelectorAll('a[href]').forEach(a => abs(a, 'href'));
  doc.querySelectorAll('img[src]').forEach(i => abs(i, 'src'));
  const root = (selector && doc.querySelector(selector))
    || doc.querySelector('article') || doc.querySelector('[role="main"]')
    || doc.querySelector('main') || doc.querySelector('#main') || doc.body;
  return root ? root.innerHTML : '';
}
"""

# same curated junk as CLEAN_HTML_JS (soupsieve handles the `i` case-insensitive flags)
_JUNK_SELECTOR = ",".join([
    'header', 'footer', 'nav', 'aside', '[role="navigation"]', '[role="banner"]',
    '[role="contentinfo"]', '.nav', '.navbar', '.sidebar', '.menu', '.ad', '.ads', '.advert',
    '.advertisement', '.social', '.share', '.breadcrumbs', '.cookie', '.popup', '.modal',
    '.newsletter', '.promo', '.related', '.recommended',
    '[class*="paywall" i]', '[id*="comments" i]', '[class*="comments" i]',
])

# last-resort cleaner when bs4 is somehow unavailable: drop obvious non-content blocks whole
_REGEX_STRIP = re.compile(
    r"(?is)<(script|style|noscript|template|svg|form|iframe|nav|header|footer|aside)\b.*?</\1\s*>")


def clean_html_str(html, base_url=None):
    """OFFLINE twin of CLEAN_HTML_JS for raw (un-rendered) HTML: same blocklist + tiny/
    link-dense pruning + main-root pick + absolutized a[href]/img[src]. Used by the fast
    no-render engine, where there is no live DOM to run the JS in."""
    if not html:
        return ""
    try:
        from bs4 import BeautifulSoup   # transitively available via markdownify
    except Exception:
        return _REGEX_STRIP.sub("", html)
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript", "iframe", "svg", "template", "link", "form"]):
        tag.decompose()
    try:
        for el in soup.select(_JUNK_SELECTOR):
            el.decompose()
    except Exception:
        pass
    # crawl4ai-style pruning, mirroring the JS: tiny + link-dense blocks are boilerplate
    try:
        for el in soup.select("div,section,article,p,li"):
            # decompose() clears the element's __dict__, so probe with getattr — a plain
            # el.parent raises AttributeError on an already-decomposed descendant
            if getattr(el, "decomposed", False) or getattr(el, "parent", None) is None:
                continue
            text = el.get_text().strip()
            words = len(text.split()) if text else 0
            if words >= 5:
                continue
            link_len = sum(len(a.get_text().strip()) for a in el.find_all("a"))
            if text and link_len / len(text) > 0.8:
                el.decompose()
    except Exception:
        pass
    if base_url:
        for a in soup.select("a[href]"):
            try:
                a["href"] = urljoin(base_url, a["href"])
            except Exception:
                pass
        for i in soup.select("img[src]"):
            try:
                i["src"] = urljoin(base_url, i["src"])
            except Exception:
                pass
    root = (soup.select_one("article") or soup.select_one('[role="main"]')
            or soup.select_one("main") or soup.select_one("#main") or soup.body or soup)
    try:
        return root.decode_contents()
    except Exception:
        return str(root)


class _TextParser(HTMLParser):
    _SKIP = {"script", "style", "noscript", "template"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self._skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in self._SKIP:
            self._skip += 1

    def handle_endtag(self, tag):
        if tag in self._SKIP and self._skip:
            self._skip -= 1

    def handle_data(self, data):
        if not self._skip:
            self.parts.append(data)


def strip_to_text(html):
    """Raw HTML to plain visible text: drop tags (and script/style contents), collapse
    whitespace. The no-render counterpart of read(fmt='text')."""
    if not html:
        return ""
    p = _TextParser()
    try:
        p.feed(html)
    except Exception:
        pass   # malformed HTML: keep whatever parsed before the choke
    return re.sub(r"\s+", " ", " ".join(p.parts)).strip()


class _LinkParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.links = []     # [href, [text parts]] in document order
        self._open = None

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            href = dict(attrs).get("href")
            if href:
                self._open = [href, []]

    def handle_endtag(self, tag):
        if tag == "a" and self._open:
            self.links.append(self._open)
            self._open = None

    def handle_data(self, data):
        if self._open:
            self._open[1].append(data)


def extract_links_str(html, base_url=None):
    """List of {text, href} from raw HTML, hrefs absolutized against base_url. Mirrors
    FIND_LINKS_JS (skips javascript:/mailto:, dedupes by href) for the no-render engine."""
    p = _LinkParser()
    try:
        p.feed(html or "")
    except Exception:
        pass
    seen, out = set(), []
    for href, parts in p.links:
        if base_url:
            try:
                href = urljoin(base_url, href)
            except Exception:
                continue
        if href.startswith(("javascript:", "mailto:")) or href in seen:
            continue
        seen.add(href)
        text = re.sub(r"\s+", " ", "".join(parts)).strip()[:120]
        out.append({"text": text, "href": href})
    return out


def to_markdown(html):
    if not html:
        return ""
    return _md(html, heading_style="ATX", strip=["script", "style"]).strip()
