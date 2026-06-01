"""Native paywall bypass: faithfully capture Bypass Paywalls Clean's engine with
Playwright/CDP (no extension; works headless), since Chrome 146 blocks loading the
real extension.

Captures BPC's two halves:
  1. REQUEST layer (the biggest lever) -- per BPC's background.js: spoof the crawler
     User-Agent, and for googlebot also send Referer=google + X-Forwarded-For=66.249.66.1;
     honour referer / random_ip / headers_custom; block the paywall/metering vendor
     scripts (block_regex + a curated general list); strip cookies unless allow_cookies
     (resets metered counters), with remove_cookies_select_drop.
  2. DOM layer -- per BPC's contentScript.js generic cleanup: clear scroll locks, remove
     blur filters and fixed full-screen overlays, delete common paywall elements, reveal
     hidden article text, and clear localStorage when cs_clear_lclstrg.

Per-site rules are read from BPC's own sites.js when bypass_rules_dir is set; otherwise a
curated default covers the common vendors. (Bespoke per-site article reconstruction --
ld_json/cs_code DOMPurify rebuilds for a minority of sites -- is approximated by the generic
reveal, not ported verbatim.)
"""
import os
import re
from urllib.parse import urlparse

# BPC's crawler user-agents (background.js).
GOOGLEBOT = "Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)"
BINGBOT = "Mozilla/5.0 (compatible; bingbot/2.0; +http://www.bing.com/bingbot.htm)"
FACEBOOKBOT = "facebookexternalhit/1.1 (+http://www.facebook.com/externalhit_uatext.php)"
UA_MAP = {"googlebot": GOOGLEBOT, "bingbot": BINGBOT, "facebookbot": FACEBOOKBOT}
GOOGLEBOT_IP = "66.249.66.1"   # the IP BPC sends as X-Forwarded-For for googlebot
REFERERS = {"google": "https://www.google.com/", "facebook": "https://www.facebook.com/",
            "twitter": "https://t.co/"}

# Common metering/paywall vendor scripts, blocked on every site (Python regex).
DEFAULT_BLOCK = [
    r"\.poool\.fr/", r"cdn\.tinypass\.com", r"\.tinypass\.com", r"\bpiano\.io",
    r"cxense", r"pelcro", r"getsitecontrol", r"blueconic", r"\bzephr\b",
    r"ampproject\.org/v0/amp-(access|subscriptions)", r"qualtrics", r"npttech\.com",
    r"tinypass", r"\bpianalytics\b", r"poool", r"outbrain", r"connatix",
]

# BPC's generic content-script cleanup, distilled: unlock scroll, kill blur + fixed
# full-screen overlays, delete common paywall elements, reveal hidden article text.
CLEANUP_JS = r"""
(opts) => {
  try {
    // 1. unlock scroll (paywalls set overflow:hidden / position:fixed on html/body)
    for (const el of [document.documentElement, document.body]) {
      if (!el) continue;
      el.style.setProperty('overflow', 'auto', 'important');
      el.style.setProperty('position', 'static', 'important');
      el.classList.remove('no-scroll','noscroll','no_scroll','overflow-hidden','modal-open',
        'paywall','locked','is-locked','fixed','disable-scroll');
    }
    // 2. remove blur/filter on article containers
    document.querySelectorAll('[style*="blur"], [style*="filter"]').forEach(e => {
      e.style.removeProperty('filter');
      e.style.removeProperty('-webkit-filter');
    });
    document.querySelectorAll('*').forEach(e => {
      const cs = getComputedStyle(e);
      if (cs && cs.filter && cs.filter.includes('blur')) e.style.setProperty('filter','none','important');
    });
    // 3. remove fixed/sticky full-screen overlays with high z-index
    document.querySelectorAll('div,section,aside,dialog').forEach(e => {
      const cs = getComputedStyle(e);
      const z = parseInt(cs.zIndex) || 0;
      if ((cs.position === 'fixed' || cs.position === 'sticky') && z >= 100 &&
          e.offsetHeight > window.innerHeight * 0.5) {
        e.remove();
      }
    });
    // 4. delete common paywall elements
    const sel = [
      '[class*="paywall" i]','[id*="paywall" i]','[class*="piano" i]','[class*="poool" i]',
      '[class*="subscribe-wall" i]','[class*="regwall" i]','[class*="metering" i]',
      '[class*="article-gate" i]','[class*="premium-overlay" i]','[data-paywall]',
      '.tp-modal','.tp-backdrop','.piano-modal','#piano-id','.pelcro-modal'
    ].join(',');
    document.querySelectorAll(sel).forEach(e => e.remove());
    // 5. reveal hidden article body
    document.querySelectorAll('article, [class*="article-body" i], [class*="post-content" i], [itemprop="articleBody"]').forEach(e => {
      e.style.setProperty('display','block','important');
      e.style.setProperty('visibility','visible','important');
      e.style.setProperty('max-height','none','important');
      e.style.setProperty('height','auto','important');
      e.style.setProperty('-webkit-line-clamp','unset','important');
    });
    document.documentElement.classList.remove('paywall','offscreen','noscroll');
    if (opts && opts.clear_lclstrg) { try { localStorage.clear(); sessionStorage.clear(); } catch(e){} }
    return true;
  } catch (e) { return false; }
}
"""


class PaywallBypass:
    def __init__(self, rules_dir=None):
        self.text = ""
        if rules_dir:
            sites = os.path.join(rules_dir, "sites.js")
            if os.path.exists(sites):
                try:
                    self.text = open(sites, encoding="utf-8", errors="ignore").read()
                except Exception:
                    self.text = ""

    def _find_entry(self, host):
        for m in re.finditer(r"\{[^{}]*?domain:\s*[\"']([^\"']+)[\"'][^{}]*?\}", self.text, re.S):
            dom = m.group(1)
            if not dom or dom.startswith("#"):
                continue
            if host == dom or host.endswith("." + dom):
                return m.group(0)
        return None

    def rule_for(self, url):
        """Return a bypass rule ONLY for sites BPC knows (faithful: unknown sites are
        left untouched, so normal logged-in browsing is never disrupted). None = no-op."""
        host = urlparse(url).netloc.lower()
        if host.startswith("www."):
            host = host[4:]
        entry = self._find_entry(host) if self.text else None
        if not entry:
            return None
        rule = {
            "host": host, "ua": None, "headers": {}, "block": list(DEFAULT_BLOCK),
            "allow": [], "clear_cookies": True, "drop_cookies": [], "clear_lclstrg": False,
        }
        m = re.search(r"useragent:\s*[\"']([^\"']+)[\"']", entry)
        if m:
            ua_key = m.group(1).lower()
            rule["ua"] = UA_MAP.get(ua_key, GOOGLEBOT)
            if ua_key == "googlebot":
                rule["headers"]["Referer"] = REFERERS["google"]
                rule["headers"]["X-Forwarded-For"] = GOOGLEBOT_IP
        m = re.search(r"referer:\s*[\"']([^\"']+)[\"']", entry)
        if m and m.group(1).lower() in REFERERS:
            rule["headers"]["Referer"] = REFERERS[m.group(1).lower()]
        if re.search(r"random_ip", entry):
            rule["headers"]["X-Forwarded-For"] = GOOGLEBOT_IP
        for bm in re.finditer(r"block_regex(?:_general)?:\s*/(.+?)/[a-z]*\s*[,}]", entry):
            rule["block"].append(bm.group(1))
        em = re.search(r"exception:\s*/(.+?)/[a-z]*\s*[,}]", entry)
        if em:
            rule["allow"].append(em.group(1))
        if re.search(r"allow_cookies:\s*1", entry):
            rule["clear_cookies"] = False
        dm = re.search(r"remove_cookies_select_drop:\s*\[([^\]]*)\]", entry)
        if dm:
            rule["drop_cookies"] = re.findall(r"[\"']([^\"']+)[\"']", dm.group(1))
        if re.search(r"cs_clear_lclstrg:\s*1", entry):
            rule["clear_lclstrg"] = True
        return rule

    @staticmethod
    def compile_patterns(patterns):
        out = []
        for p in patterns:
            try:
                out.append(re.compile(p))
            except re.error:
                pass
        return out
