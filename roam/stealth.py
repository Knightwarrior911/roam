"""Stealth hardening + a detectability self-audit.

Note the honest hierarchy of trust:
  1. The bridge (drives your real browser) is the gold standard — it IS a real browser,
     so nothing flags it. Use it for hard, bot-protected sites.
  2. Roam's own launched browser is more detectable. This module reduces the obvious
     automation leaks (the puppeteer-stealth / actionbook-stealth subset that is safe to
     apply without breaking sites), and `AUDIT_JS` lets you MEASURE how detectable it is
     so you can trust it rather than guess.
  3. For the strongest launched-browser stealth, swap in a stealth Chromium binary via
     config `executable_path` (e.g. CloakBrowser), or use `mode: "stealth"` (patchright).

STEALTH_JS is injected at document-start (before page scripts) so detection sees the
patched values. It deliberately avoids aggressive fingerprint spoofing that can itself be
a detection signal or break rendering.
"""

# Launch flags that reduce automation tells (the safe, broadly-compatible subset).
STEALTH_ARGS = [
    "--disable-blink-features=AutomationControlled",   # drops webdriver + the "controlled by automation" banner
    "--disable-features=IsolateOrigins,site-per-process",
]

STEALTH_JS = r"""
(() => {
  try {
    // navigator.webdriver -> undefined (the single biggest automation tell)
    Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
    // strip the HeadlessChrome tell from the JS-visible UA (HTTP header still needs a
    // CDP override for full coverage; this fixes client-side detection)
    if (/Headless/.test(navigator.userAgent)) {
      const ua = navigator.userAgent.replace(/HeadlessChrome/g, 'Chrome').replace(/Headless/g, '');
      Object.defineProperty(navigator, 'userAgent', { get: () => ua });
    }
    // a real Chrome has window.chrome
    if (!window.chrome) window.chrome = {};
    if (!window.chrome.runtime) window.chrome.runtime = {};
    // notifications permission shouldn't read as 'denied by automation'
    if (navigator.permissions && navigator.permissions.query) {
      const q = navigator.permissions.query.bind(navigator.permissions);
      navigator.permissions.query = (p) => (p && p.name === 'notifications')
        ? Promise.resolve({ state: Notification.permission })
        : q(p);
    }
    // non-empty plugins + languages (headless has none)
    Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3, 4, 5] });
    Object.defineProperty(navigator, 'languages', { get: () => ['en-US', 'en'] });
    // scrub known automation globals
    for (const k of Object.keys(window)) {
      if (/cdc_|\$cdc|selenium|webdriver|__driver|__nightmare|domAutomation|__playwright|__puppeteer/i.test(k)) {
        try { delete window[k]; } catch (e) {}
      }
    }
    // WebGL vendor/renderer: avoid the 'SwiftShader'/'Google' headless giveaway
    const proto = window.WebGLRenderingContext && WebGLRenderingContext.prototype;
    if (proto && proto.getParameter) {
      const gp = proto.getParameter;
      proto.getParameter = function (p) {
        if (p === 37445) return 'Intel Inc.';                  // UNMASKED_VENDOR_WEBGL
        if (p === 37446) return 'Intel Iris OpenGL Engine';    // UNMASKED_RENDERER_WEBGL
        return gp.call(this, p);
      };
    }
  } catch (e) {}
})();
"""

# Probes the browser's OWN automation tells (runs anywhere; doesn't need a special site).
AUDIT_JS = r"""
() => {
  const n = navigator, w = window;
  let webglVendor = null;
  try {
    const gl = document.createElement('canvas').getContext('webgl');
    const ext = gl && gl.getExtension('WEBGL_debug_renderer_info');
    webglVendor = ext ? gl.getParameter(ext.UNMASKED_VENDOR_WEBGL) : null;
  } catch (e) {}
  const autovars = Object.keys(w).filter(k =>
    /cdc_|\$cdc|selenium|webdriver|__driver|__nightmare|domAutomation|__playwright|__puppeteer/i.test(k));
  return {
    webdriver: n.webdriver === undefined ? "undefined" : n.webdriver,
    has_chrome: !!w.chrome,
    plugins: n.plugins ? n.plugins.length : 0,
    languages: n.languages || [],
    webgl_vendor: webglVendor,
    automation_vars: autovars,
    headless_ua: / HeadlessChrome/.test(n.userAgent),
    ua: (n.userAgent || "").slice(0, 90),
  };
}
"""


def audit_verdict(r):
    checks = {
        "webdriver_hidden": r.get("webdriver") in (None, False, "undefined"),
        "has_chrome": bool(r.get("has_chrome")),
        "has_plugins": (r.get("plugins") or 0) > 0,
        "has_languages": len(r.get("languages") or []) > 0,
        "no_automation_vars": len(r.get("automation_vars") or []) == 0,
        "not_headless_ua": not r.get("headless_ua"),
        "webgl_not_swiftshader": "swiftshader" not in str(r.get("webgl_vendor") or "").lower(),
    }
    score = sum(1 for v in checks.values() if v)
    total = len(checks)
    verdict = "clean" if score == total else ("ok" if score >= total - 1 else "leaky")
    return {"raw": r, "checks": checks, "score": score, "of": total, "verdict": verdict}
