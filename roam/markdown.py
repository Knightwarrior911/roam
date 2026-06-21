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

# ---- single source of truth for the junk blocklist (no more JS/Python drift) ----
# Expanded from 28 -> ~70 entries (Firecrawl excludeNonMainTags + crawl4ai class set + the
# ad-tech long tail). Used to BUILD the JS string below AND the offline bs4 path, so the two
# can never diverge.
_JUNK_LIST = [
    # structural
    'header', 'footer', 'nav', 'aside',
    '[role="navigation"]', '[role="banner"]', '[role="contentinfo"]', '[role="complementary"]',
    '.header', '.top', '.footer', '.bottom', '#footer', '#header',
    '.nav', '.navbar', '.navigation', '#nav', '.menu', '.sidebar', '#sidebar', '.side', '.aside',
    '.breadcrumbs', '.breadcrumb', '.skip-link', '.skip-to-content',
    # ads / ad-tech
    '.ad', '.ads', '.advert', '.advertisement', '.ad-slot', '.ad-container', '.adsbygoogle',
    '.dfp', '[class*="outbrain" i]', '[class*="taboola" i]', '[class*="adslot" i]',
    '[aria-label="Advertisement"]', '[data-test*="ad" i]', '[data-testid*="ad" i]',
    # consent / banners / modals / overlays
    '.cookie', '.cookie-banner', '.gdpr', '.onetrust', '.cc-window', '#onetrust-banner-sdk',
    '.popup', '.modal', '.overlay', '.interstitial', '.app-banner', '.sticky-banner',
    '.paywall', '.subscribe-wall', '.signup-wall', '[class*="paywall" i]',
    # social / share / newsletter / promo
    '.social', '.social-media', '.social-links', '#social', '.share', '.share-buttons',
    '.newsletter', '.newsletter-signup', '.inline-newsletter', '.promo', '.intercom-launcher',
    # related / recirculation / comments
    '.related', '.related-stories', '.recommended', '.recirc', '.trending', '.most-popular',
    '.most-read', '.sibling-stories', '[id*="comments" i]', '[class*="comments" i]',
    '.comments-section', '[class*="lang-selector" i]', '.language-selector', '#language-selector',
    # misc widgets
    '.widget', '#cookie', '.toast-container', '.feedback-tab',
]
# selectors that SURVIVE any deny pass (the article was wrapped in a junk-ish class)
_FORCE_INCLUDE_LIST = [
    'article', '[role="main"]', 'main', '#main',
    '.article-body', '.post-content', '.entry-content', '[itemprop="articleBody"]',
    '[data-testid="article-content"]',
]
# embed iframes worth keeping as a markdown link instead of dropping
_EMBED_HOST_RE = (r"youtube\.com|youtu\.be|vimeo\.com|codepen\.io|jsfiddle\.net|codesandbox\.io|"
                  r"stackblitz\.com|figma\.com|miro\.com|docs\.google\.com|player\.|embed\.|"
                  r"twitter\.com|x\.com|reddit\.com|loom\.com|gist\.github\.com")

_JUNK_SELECTOR = ",".join(_JUNK_LIST)
_FORCE_INCLUDE_SELECTOR = ",".join(_FORCE_INCLUDE_LIST)
_JS_JUNK = ",".join(_JUNK_LIST)   # same list, embedded into the page-side cleaner

# Returns the cleaned main-content innerHTML of the live (JS-rendered) page.
# Built with the shared _JS_JUNK so it can't drift from the offline bs4 path.
CLEAN_HTML_JS = r"""
(selector) => {
  const doc = document.cloneNode(true);
  // base href governs URL resolution when present, else the page URL.
  const baseHref = (doc.querySelector('base[href]') && doc.querySelector('base[href]').getAttribute('href')) || location.href;
  // keep known embeds as links before nuking iframes; preserve <form> as a control list.
  const EMBED = /__EMBED_RE__/i;
  doc.querySelectorAll('iframe[src]').forEach(f => {
    let src = f.getAttribute('src') || '';
    try { src = new URL(src, baseHref).href; } catch (e) {}
    if (EMBED.test(src)) { const a = doc.createElement('a'); a.setAttribute('href', src); a.textContent = (f.getAttribute('title') || f.getAttribute('aria-label') || 'embed') + ' (embed)'; f.replaceWith(a); }
    else f.remove();
  });
  doc.querySelectorAll('form').forEach(fm => {
    const ctrls = [...fm.querySelectorAll('input,select,textarea,button')].map(c => {
      const nm = c.getAttribute('name') || c.getAttribute('aria-label') || c.getAttribute('placeholder') || c.type || c.tagName.toLowerCase();
      return '- ' + nm + ' (' + (c.type || c.tagName.toLowerCase()) + ')' + (c.required ? ' *' : '');
    });
    if (ctrls.length) { const pre = doc.createElement('pre'); pre.textContent = 'Form:\n' + ctrls.join('\n'); fm.replaceWith(pre); }
    else fm.remove();
  });
  doc.querySelectorAll('script,style,noscript,template,link').forEach(e => e.remove());
  // svg: keep only if it carries a title/aria-label (real icon), else drop decorative ones.
  doc.querySelectorAll('svg').forEach(s => { if (!s.querySelector('title') && !s.getAttribute('aria-label')) s.remove(); });
  const junk = '__JS_JUNK__';
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
  const abs = (el, attr) => { try { el.setAttribute(attr, new URL(el.getAttribute(attr), baseHref).href); } catch (e) {} };
  doc.querySelectorAll('a[href]').forEach(a => abs(a, 'href'));
  doc.querySelectorAll('img[src]').forEach(i => abs(i, 'src'));
  const root = (selector && doc.querySelector(selector))
    || doc.querySelector('article') || doc.querySelector('[role="main"]')
    || doc.querySelector('main') || doc.querySelector('#main') || doc.body;
  return root ? root.innerHTML : '';
}
""".replace("__JS_JUNK__", _JS_JUNK).replace("__EMBED_RE__", _EMBED_HOST_RE)

# last-resort cleaner when bs4 is somehow unavailable: drop obvious non-content blocks whole
_REGEX_STRIP = re.compile(
    r"(?is)<(script|style|noscript|template|svg|form|iframe|nav|header|footer|aside)\b.*?</\1\s*>")


def clean_html_str(html, base_url=None):
    """OFFLINE twin of CLEAN_HTML_JS for raw (un-rendered) HTML: same blocklist + tiny/
    link-dense pruning + main-root pick + absolutized a[href]/img[src]. Used by the fast
    no-render engine, where there is no live DOM to run the JS in. Honors <base href>,
    keeps known embeds as links, and preserves <form> as a control list."""
    if not html:
        return ""
    try:
        from bs4 import BeautifulSoup   # transitively available via markdownify
    except Exception:
        return _REGEX_STRIP.sub("", html)
    soup = BeautifulSoup(html, "html.parser")
    # <base href> overrides base_url for URL resolution when present
    base_el = soup.find("base", href=True)
    if base_el and base_el.get("href"):
        base_url = urljoin(base_url or "", base_el["href"]) if base_url else base_el["href"]
    # keep known embeds as links before dropping iframes
    embed_re = re.compile(_EMBED_HOST_RE, re.I)
    for fr in soup.find_all("iframe"):
        src = fr.get("src") or ""
        abss = urljoin(base_url, src) if base_url else src
        if src and embed_re.search(abss):
            a = soup.new_tag("a", href=abss)
            a.string = (fr.get("title") or fr.get("aria-label") or "embed") + " (embed)"
            fr.replace_with(a)
        else:
            fr.decompose()
    # preserve forms as a control list
    for fm in soup.find_all("form"):
        ctrls = []
        for c in fm.find_all(["input", "select", "textarea", "button"]):
            nm = c.get("name") or c.get("aria-label") or c.get("placeholder") or c.get("type") or c.name
            ctrls.append(f"- {nm} ({c.get('type') or c.name})" + (" *" if c.has_attr("required") else ""))
        if ctrls:
            pre = soup.new_tag("pre")
            pre.string = "Form:\n" + "\n".join(ctrls)
            fm.replace_with(pre)
        else:
            fm.decompose()
    for tag in soup(["script", "style", "noscript", "template", "link"]):
        tag.decompose()
    # svg: keep only semantic icons
    for s in soup.find_all("svg"):
        if not s.find("title") and not s.get("aria-label"):
            s.decompose()
    # force-include guard: never decompose a junk-matched node that is/contains the article
    keep = set()
    try:
        for inc in soup.select(_FORCE_INCLUDE_SELECTOR):
            keep.add(id(inc))
            for anc in inc.parents:
                keep.add(id(anc))
    except Exception:
        pass
    try:
        for el in soup.select(_JUNK_SELECTOR):
            if id(el) in keep:
                continue
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
