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

def grease_brands(version, grease_version="99"):
    """Build a correctly-GREASE'd Sec-CH-UA brand list (matches Chromium's own algorithm:
    deterministic permutation from the major version + the fake 'Not A Brand' entry). A
    hand-rolled brand list with the wrong order/grease is itself a detection signal."""
    seed = int(str(version).split(".")[0])
    order = [[0, 1, 2], [0, 2, 1], [1, 0, 2], [1, 2, 0], [2, 0, 1], [2, 1, 0]][seed % 6]
    esc = [" ", " ", ";"]
    greasey = f"{esc[order[0]]}Not{esc[order[1]]}A{esc[order[2]]}Brand"
    out = [None, None, None]
    out[order[0]] = {"brand": greasey, "version": grease_version}
    out[order[1]] = {"brand": "Chromium", "version": str(version)}
    out[order[2]] = {"brand": "Google Chrome", "version": str(version)}
    return out


def should_apply_uach(cfg):
    """Only fix UA-CH on bundled Chromium under hardening. Real Chrome channel and
    patchright already emit a consistent UA-CH (and patchright explicitly should NOT have a
    custom UA), so we must not touch those — overriding them would CREATE a mismatch."""
    if cfg.mode == "stealth" or cfg.executable_path:
        return False
    if (cfg.channel or "").lower() in ("chrome", "msedge", "msedge-beta", "msedge-dev", "chrome-beta"):
        return False
    return bool(cfg.stealth_harden)


_UACH_READ_JS = r"""
async () => {
  const ua = navigator.userAgent;
  let h = {};
  try {
    if (navigator.userAgentData)
      h = await navigator.userAgentData.getHighEntropyValues(
        ['platform','platformVersion','architecture','model','uaFullVersion','bitness']);
  } catch (e) {}
  return { ua, h };
}
"""


async def apply_uach(page):
    """Best-effort: fix the UA-CH brand list (and de-headless the UA) via CDP, REUSING the
    browser's own high-entropy hints so everything stays internally consistent. Returns a
    small status dict; never raises (stealth is best-effort)."""
    import re
    try:
        info = await page.evaluate(_UACH_READ_JS)
        ua = (info.get("ua") or "").replace("HeadlessChrome", "Chrome").replace("Headless", "")
        m = re.search(r"Chrome/(\d+)", ua)
        if not m:
            return {"applied": False, "reason": "not a Chrome UA"}
        major = m.group(1)
        h = info.get("h") or {}
        full = h.get("uaFullVersion") or f"{major}.0.0.0"
        uach_platform = h.get("platform") or "Windows"
        nav_platform = {"Windows": "Win32", "macOS": "MacIntel", "Mac OS X": "MacIntel",
                        "Linux": "Linux x86_64", "Android": "Linux armv8l"}.get(uach_platform, "Win32")
        client = await page.context.new_cdp_session(page)
        await client.send("Network.setUserAgentOverride", {
            "userAgent": ua,
            "platform": nav_platform,
            "userAgentMetadata": {
                "brands": grease_brands(major),
                "fullVersionList": grease_brands(full, "99.0.0.0"),
                "fullVersion": full,
                "platform": uach_platform,
                "platformVersion": h.get("platformVersion") or "",
                "architecture": h.get("architecture") or "x86",
                "model": h.get("model") or "",
                "bitness": h.get("bitness") or "64",
                "mobile": False,
            },
        })
        return {"applied": True, "brand_count": 3, "major": major}
    except Exception as e:
        return {"applied": False, "reason": str(e)}


def build_stealth_args(cfg):
    """Launch args for hardened/stealth launches. STEALTH_ARGS is the always-on safe base;
    the rest are GATED by explicit config (off by default) because they change the
    fingerprint or behavior and shouldn't fire for ordinary logged-in browsing.

    canvas_noise uses Chromium's NATIVE per-session canvas noise flag — not a JS hook. This
    is deliberate: JS canvas hooks are themselves detectable (toString + per-call variance);
    the native flag adds consistent, undetectable noise (the CloakBrowser/Scrapling approach).
    """
    args = list(STEALTH_ARGS)
    if getattr(cfg, "canvas_noise", False):
        args.append("--fingerprinting-canvas-image-data-noise")
    if getattr(cfg, "block_webrtc", False):
        args += ["--webrtc-ip-handling-policy=disable_non_proxied_udp",
                 "--force-webrtc-ip-handling-policy"]
    return args


STEALTH_JS = r"""
(() => {
  try {
    // ---- 1) toString tamper-proofing (install FIRST so our spoof getters read native) ----
    const patched = new WeakMap();         // fn -> reported name
    // also fix fn.name: a native getter is named e.g. "get hardwareConcurrency", a native
    // method "query"; leaving our function-expression names ("pq","patchedGp") is a tell.
    const setName = (fn, name) => {
      try { Object.defineProperty(fn, 'name', { value: name, configurable: true }); } catch (e) {}
    };
    const mark = (fn, reported) => { patched.set(fn, reported); setName(fn, reported); return fn; };
    const origToString = Function.prototype.toString;
    const myToString = function () {
      if (patched.has(this)) return 'function ' + patched.get(this) + '() { [native code] }';
      return origToString.call(this);
    };
    mark(myToString, 'toString');
    // route Function.prototype.toString through our proxy without changing descriptor flags
    Object.defineProperty(Function.prototype, 'toString', {
      value: myToString, writable: true, configurable: true, enumerable: false,
    });
    const defineNative = (obj, prop, getter, name) => {
      mark(getter, 'get ' + name);   // native accessors report "get <prop>" for name + toString
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
      mark(pq, 'query');
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
        mark(patchedGp, 'getParameter');
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

  // are our spoof getters undetectable? the prototype getter's source AND .name must read
  // native ("get hardwareConcurrency"). Checking only toString misses the .name tell.
  let spoofNative = false, spoofNameNative = false, tostrNameNative = false;
  try {
    const d = Object.getOwnPropertyDescriptor(Object.getPrototypeOf(n), 'hardwareConcurrency');
    spoofNative = !!(d && d.get) && ('' + d.get).includes('[native code]');
    spoofNameNative = !!(d && d.get) && d.get.name === 'get hardwareConcurrency';
    tostrNameNative = Function.prototype.toString.name === 'toString';
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
    spoof_name_native: spoofNameNative,
    tostring_name_native: tostrNameNative,
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
        "spoof_name_native": bool(r.get("spoof_name_native")),
        "tostring_name_native": bool(r.get("tostring_name_native")),
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
