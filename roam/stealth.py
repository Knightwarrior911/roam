"""Stealth hardening + a detectability self-audit.

Honest hierarchy of trust:
  1. The bridge (drives your real browser) is the gold standard — it IS a real browser.
  2. mode="stealth" (patchright) closes the driver-level CDP leaks (Runtime.enable /
     Console.enable / init-script exposure) that plain Playwright cannot.
  3. stealth_harden on the plain-Playwright managed browser reduces the *fingerprint*
     tells below. It canNOT close the Runtime.enable CDP leak (that's a driver patch) —
     the audit reports that honestly via a separate `cdp_verdict`.

Design notes (where we deliberately diverge from puppeteer-stealth to avoid its tells):
  - navigator.webdriver: we do NOT override it in JS. The launch flag
    `--disable-blink-features=AutomationControlled` makes it natively `false` with NO
    own-property on `navigator` (verified). A JS override would itself be detectable
    (own property + returns `undefined` instead of `false`) — the classic stealth tell.
  - hardwareConcurrency / deviceMemory / userAgent / WebGL getters are installed on the
    PROTOTYPE (never the navigator instance, which must stay own-property-free) and routed
    through a Function.prototype.toString proxy so the spoof getters read as native code.
  - We do not fake navigator.plugins with bogus values (a badly-faked PluginArray is worse
    than an honest one). Real Chrome channel + patchright already have real plugins.
"""

# Launch flags that reduce automation tells (the safe, broadly-compatible subset).
STEALTH_ARGS = [
    "--disable-blink-features=AutomationControlled",   # webdriver -> false, no own-prop tell
    "--disable-features=IsolateOrigins,site-per-process",
]

STEALTH_JS = r"""
(() => {
  try {
    // ---- 1) toString tamper-proofing (install FIRST so our spoof getters read native) ----
    const patched = new WeakMap();         // fn -> reported name
    const origToString = Function.prototype.toString;
    const myToString = function () {
      if (patched.has(this)) return 'function ' + patched.get(this) + '() { [native code] }';
      return origToString.call(this);
    };
    patched.set(myToString, 'toString');
    // route Function.prototype.toString through our proxy without changing descriptor flags
    Object.defineProperty(Function.prototype, 'toString', {
      value: myToString, writable: true, configurable: true, enumerable: false,
    });
    const defineNative = (obj, prop, getter, name) => {
      patched.set(getter, name);
      const d = Object.getOwnPropertyDescriptor(obj, prop) || { configurable: true, enumerable: true };
      Object.defineProperty(obj, prop, { get: getter, configurable: true, enumerable: d.enumerable !== false });
    };

    const Nproto = Object.getPrototypeOf(navigator);   // Navigator.prototype

    // ---- 2) webdriver: intentionally NOT touched (the flag handles it cleanly) ----

    // ---- 3) hardwareConcurrency + deviceMemory on the prototype, consistent + native ----
    defineNative(Nproto, 'hardwareConcurrency', function () { return 8; }, 'hardwareConcurrency');
    defineNative(Nproto, 'deviceMemory', function () { return 8; }, 'deviceMemory');

    // ---- 4) notifications permission shouldn't read as denied-by-automation ----
    if (navigator.permissions && navigator.permissions.query) {
      const q = navigator.permissions.query.bind(navigator.permissions);
      const pq = function (p) {
        return (p && p.name === 'notifications')
          ? Promise.resolve({ state: Notification.permission }) : q(p);
      };
      patched.set(pq, 'query');
      navigator.permissions.query = pq;
    }

    // ---- 5) a real Chrome has window.chrome ----
    if (!window.chrome) window.chrome = {};
    if (!window.chrome.runtime) window.chrome.runtime = {};

    // ---- 6) strip the HeadlessChrome tell from the JS-visible UA (HTTP header needs a
    //         CDP override for full coverage; this fixes client-side string detection) ----
    if (/Headless/.test(navigator.userAgent)) {
      const ua = navigator.userAgent.replace(/HeadlessChrome/g, 'Chrome').replace(/Headless/g, '');
      defineNative(Nproto, 'userAgent', function () { return ua; }, 'userAgent');
    }

    // ---- 7) WebGL vendor/renderer on BOTH context prototypes (avoid SwiftShader tell) ----
    for (const Ctx of [window.WebGLRenderingContext, window.WebGL2RenderingContext]) {
      if (Ctx && Ctx.prototype && Ctx.prototype.getParameter) {
        const gp = Ctx.prototype.getParameter;
        const patchedGp = function (p) {
          if (p === 37445) return 'Intel Inc.';                 // UNMASKED_VENDOR_WEBGL
          if (p === 37446) return 'Intel Iris OpenGL Engine';   // UNMASKED_RENDERER_WEBGL
          return gp.call(this, p);
        };
        patched.set(patchedGp, 'getParameter');
        Ctx.prototype.getParameter = patchedGp;
      }
    }

    // ---- 8) scrub known automation globals ----
    for (const k of Object.keys(window)) {
      if (/cdc_|\$cdc|selenium|webdriver|__driver|__nightmare|domAutomation|__playwright|__puppeteer/i.test(k)) {
        try { delete window[k]; } catch (e) {}
      }
    }
  } catch (e) {}
})();
"""

# Probes the browser's OWN automation tells. Async because the Runtime.enable probe needs a
# tick to observe whether CDP read Error.stack. Runs anywhere; needs no special site.
AUDIT_JS = r"""
async () => {
  const n = navigator, w = window;
  let webglVendor = null;
  try {
    const gl = document.createElement('canvas').getContext('webgl');
    const ext = gl && gl.getExtension('WEBGL_debug_renderer_info');
    webglVendor = ext ? gl.getParameter(ext.UNMASKED_VENDOR_WEBGL) : null;
  } catch (e) {}
  const autovars = Object.keys(w).filter(k =>
    /cdc_|\$cdc|selenium|webdriver|__driver|__nightmare|domAutomation|__playwright|__puppeteer/i.test(k));

  // Runtime.enable leak (rebrowser): if CDP enabled the Runtime domain, serializing a
  // console arg reads Error.stack -> our getter fires.
  let stackLookups = 0;
  try {
    const e = new Error();
    Object.defineProperty(e, 'stack', { configurable: false, get() { stackLookups += 1; return ''; } });
    console.debug(e);
    await new Promise(r => setTimeout(r, 120));
  } catch (e) {}

  // sourceURL leak: unpatched Playwright/Puppeteer leave telltale frames in Error.stack.
  let srcLeak = false;
  try { const s = (new Error('p').stack || '').toString(); srcLeak = s.includes('pptr:') || s.includes('UtilityScript.'); } catch (e) {}

  // are our spoof getters undetectable? the prototype getter's source must read native.
  let spoofNative = false;
  try {
    const d = Object.getOwnPropertyDescriptor(Object.getPrototypeOf(n), 'hardwareConcurrency');
    spoofNative = !!(d && d.get) && ('' + d.get).includes('[native code]');
  } catch (e) {}

  return {
    webdriver: n.webdriver === undefined ? 'undefined' : n.webdriver,
    navigator_own_props: Object.getOwnPropertyNames(n),
    has_chrome: !!w.chrome,
    plugins: n.plugins ? n.plugins.length : 0,
    languages: n.languages || [],
    webgl_vendor: webglVendor,
    hardware_concurrency: n.hardwareConcurrency,
    device_memory: n.deviceMemory === undefined ? null : n.deviceMemory,
    automation_vars: autovars,
    headless_ua: / HeadlessChrome/.test(n.userAgent),
    runtime_enable_leak: stackLookups > 0,
    source_url_leak: srcLeak,
    pw_init_scripts: typeof w.__pwInitScripts !== 'undefined',
    spoof_tostring_native: spoofNative,
    ua: (n.userAgent || '').slice(0, 90),
  };
}
"""


def _verdict(checks):
    score = sum(1 for v in checks.values() if v)
    total = len(checks)
    label = "clean" if score == total else ("ok" if score >= total - 1 else "leaky")
    return label, score, total


def audit_verdict(r):
    # CORE = fingerprint tells that stealth_harden (JS + flags) can actually fix.
    core = {
        "webdriver_hidden": r.get("webdriver") in (None, False, "undefined"),
        "no_navigator_own_props": len(r.get("navigator_own_props") or []) == 0,
        "has_chrome": bool(r.get("has_chrome")),
        "has_languages": len(r.get("languages") or []) > 0,
        "no_automation_vars": len(r.get("automation_vars") or []) == 0,
        "not_headless_ua": not r.get("headless_ua"),
        "webgl_not_swiftshader": "swiftshader" not in str(r.get("webgl_vendor") or "").lower(),
        "spoof_tostring_native": bool(r.get("spoof_tostring_native")),
    }
    # CDP = driver-level leaks only patchright/bridge can close (plain Playwright leaks them).
    cdp = {
        "runtime_enable_clean": not r.get("runtime_enable_leak"),
        "no_source_url_leak": not r.get("source_url_leak"),
        "no_pw_init_scripts": not r.get("pw_init_scripts"),
    }
    cv, cs, ct = _verdict(core)
    dv, ds, dt = _verdict(cdp)
    return {
        "raw": r, "checks": {**core, **cdp},
        "score": cs, "of": ct, "verdict": cv,
        "cdp_score": ds, "cdp_of": dt, "cdp_verdict": dv,
    }
