"""Native paywall bypass: replicate Bypass Paywalls Clean's core tactics with
Playwright/CDP (no extension). Two levers that publishers honor and that work
headless: (1) present as a search-engine crawler (Googlebot), which many metered
sites serve full content to, and (2) block the paywall/metering vendor scripts.

Reads BPC's own per-domain rules from its `sites.js` when available (config
bypass_rules_dir), augmenting a curated default that handles the common vendors.
Cookies are left alone by default so logged-in sessions are preserved.
"""
import os
import re
from urllib.parse import urlparse

GOOGLEBOT = "Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)"
BINGBOT = "Mozilla/5.0 (compatible; bingbot/2.0; +http://www.bing.com/bingbot.htm)"
FACEBOOKBOT = "facebookexternalhit/1.1 (+http://www.facebook.com/externalhit_uatext.php)"
UA_MAP = {"googlebot": GOOGLEBOT, "bingbot": BINGBOT, "facebookbot": FACEBOOKBOT}

# Common metering/paywall vendor script patterns (Python regex), applied to every site.
DEFAULT_BLOCK = [
    r"\.poool\.fr/", r"cdn\.tinypass\.com", r"\.tinypass\.com", r"\bpiano\.io",
    r"cxense", r"pelcro", r"getsitecontrol", r"blueconic", r"\bzephr\b",
    r"ampproject\.org/v0/amp-(access|subscriptions)", r"qualtrics", r"npttech\.com",
]


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
        # Best-effort: find a flat { ... } entry whose domain matches the host.
        for m in re.finditer(r"\{[^{}]*?domain:\s*[\"']([^\"']+)[\"'][^{}]*?\}", self.text, re.S):
            dom = m.group(1)
            if not dom or dom.startswith("#"):
                continue
            if host == dom or host.endswith("." + dom):
                return m.group(0)
        return None

    def rule_for(self, url):
        host = urlparse(url).netloc.lower()
        if host.startswith("www."):
            host = host[4:]
        ua = GOOGLEBOT                      # default: look like a crawler
        blocks = list(DEFAULT_BLOCK)
        clear_cookies = False               # preserve logins by default
        entry = self._find_entry(host) if self.text else None
        if entry:
            m = re.search(r"useragent:\s*[\"']([^\"']+)[\"']", entry)
            if m:
                ua = UA_MAP.get(m.group(1).lower(), ua)
            for bm in re.finditer(r"block_regex:\s*/(.+?)/[a-z]*\s*[,}]", entry):
                blocks.append(bm.group(1))
            if not re.search(r"allow_cookies:\s*1", entry):
                clear_cookies = True        # this site needs a cookie reset (e.g. metered)
        return {"ua": ua, "block": blocks, "clear_cookies": clear_cookies, "host": host}

    @staticmethod
    def compile_blocks(patterns):
        out = []
        for p in patterns:
            try:
                out.append(re.compile(p))
            except re.error:
                pass
        return out
