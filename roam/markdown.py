"""Clean a rendered page into LLM-ready markdown (the Firecrawl approach, done client-side):
strip boilerplate via a curated blocklist, resolve links/images to absolute, pick the main
content, then convert to markdown. Roughly 5x fewer tokens than raw HTML and far more
useful to an agent than document.body.innerText (it keeps headings, lists, tables, links).
"""
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
  const abs = (el, attr) => { try { el.setAttribute(attr, new URL(el.getAttribute(attr), location.href).href); } catch (e) {} };
  doc.querySelectorAll('a[href]').forEach(a => abs(a, 'href'));
  doc.querySelectorAll('img[src]').forEach(i => abs(i, 'src'));
  const root = (selector && doc.querySelector(selector))
    || doc.querySelector('article') || doc.querySelector('[role="main"]')
    || doc.querySelector('main') || doc.querySelector('#main') || doc.body;
  return root ? root.innerHTML : '';
}
"""


def to_markdown(html):
    if not html:
        return ""
    return _md(html, heading_style="ATX", strip=["script", "style"]).strip()
