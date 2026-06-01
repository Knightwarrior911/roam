"""Operator-aware web search URL builder. The precision lever for research: site:,
filetype:, intitle:, quoted phrases, OR. The skill/agent picks operators; Roam navigates."""
import urllib.parse

ENGINES = {
    "duckduckgo": "https://duckduckgo.com/html/?q=",   # automation-friendly results page
    "google": "https://www.google.com/search?q=",
    "bing": "https://www.bing.com/search?q=",
}


def build_search_url(query, site=None, filetype=None, intitle=None, engine="duckduckgo"):
    q = query.strip()
    if site:
        q += f" site:{site}"
    if filetype:
        q += f" filetype:{filetype}"
    if intitle:
        q += f" intitle:{intitle}"
    base = ENGINES.get(engine, ENGINES["duckduckgo"])
    return base + urllib.parse.quote(q)


def is_engine_link(href, engine_netloc):
    """True if a link points back into the search engine itself (drop these from results)."""
    try:
        host = urllib.parse.urlparse(href).netloc
    except Exception:
        return True
    key = engine_netloc.split(".")[-2] if "." in engine_netloc else engine_netloc
    return (not host) or (key and key in host)
