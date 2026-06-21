import functools
import os
from mcp.server.fastmcp import FastMCP, Image
from . import mode
from .browser import BrowserController
from .config import load_config
from .errors import RoamError, ok, err
from .memory import SelectorMemory, format_manual

ROAM_INSTRUCTIONS = """roam — your default browser for any web task: reading/extracting page
content, navigating, searching, filling forms, clicking, and working with logged-in or
bot-protected sites. Reach for roam before plain HTTP fetch or other browser tools.

Why roam first:
- read_markdown(url="https://…") returns clean, token-cheap Markdown of a page in ONE call —
  far cheaper and more readable than raw HTML. read/snapshot/find_links also take url= now, so
  "read this page" never needs a separate goto.
- It drives a real, STEALTH browser (patchright) and can use your ACTUAL logged-in browser via
  the bridge, so paywalled / Cloudflare / login-gated pages work where plain fetch returns junk.
- Act by DESCRIBING the element — click(element="Sign in") / type(element="search box", text=…) —
  roam resolves it via selector memory + self-healing; no brittle CSS needed. snapshot() lists
  the interactive elements with refs when you want to be explicit.
- extract(fields=…) scrapes repeating items to structured JSON + a replayable Playwright script.
- read_markdown(url=…, query=…) returns ONLY the passages relevant to query (BM25) — big
  token savings when researching.
- verify(text=…) / verify(selector=…, value=…/visible=True) CHECKS a result and returns
  {ok:bool} — assert an outcome instead of re-snapshotting and grepping.
- Helpers: web_search, dismiss_popups, solve_cloudflare, stealth_audit, record_api/recipes.

Typical flows:
- Read a page:        read_markdown(url="https://…")
- Search then read:   web_search(query=…) → read_markdown(url=<result link>)
- Act on a page:      snapshot()  →  click(element="…") / type(element="…", text="…", submit=True)

Drive YOUR real, logged-in browser (the bridge):
- Call bridge(enable=True) ONCE. It block-waits for the Roam Bridge extension to AUTO-connect
  — there is NO manual "click to connect" step; the extension connects on its own. You get
  back connected:true (with the browser identity) or an honest connected:false + install hint.
- Then set_mode("bridge") to pin every tool to your real browser. In bridge mode, if the
  extension drops, calls fail with BRIDGE_DISCONNECTED instead of silently opening another
  browser. set_mode("auto") falls back to the managed browser when the bridge is down;
  set_mode("managed") forces Roam's own browser.
- mode() shows which backend the next call hits. set_channel("msedge"|"chrome"|"chromium")
  picks the managed engine (Edge is auto-detected when Chrome is absent).

Every tool returns {ok, data} on success or {ok:false, error:{code, message, hint}} on failure —
never a raw traceback, so failures are actionable."""

mcp = FastMCP("roam", instructions=ROAM_INSTRUCTIONS)
_controller = None
_bridge_srv = None       # bridge.Bridge (WS server) when bridge mode is on
_bridge_browser = None   # bridge.BridgeBrowser, used while the extension is connected
_mem = None              # local selector/manual memory, shared across browser backends


def _memory():
    global _mem
    if _mem is None:
        cfg = load_config()
        _mem = SelectorMemory(os.path.join(os.path.dirname(cfg.profile_dir) or ".", "memory.db"))
    return _mem


async def _current_url(url=None):
    if url:
        return url
    try:
        return await _ctl().url()
    except Exception:
        return None


async def _nav_if(url, tab, wait="load"):
    """Navigate first when a read-family tool is given a url, so 'read this page'
    is a single call instead of goto + read."""
    if url:
        await _ctl().goto(url, wait, tab=tab)


def _bridge_ready():
    return (_bridge_browser is not None and _bridge_srv is not None
            and _bridge_srv.connected.is_set())


def _managed():
    global _controller
    if _controller is None:
        _controller = BrowserController(load_config())
    return _controller


def _ctl():
    """Pick the backend for this call based on the sticky session mode.
    - bridge:  always the extension; if not connected, fail LOUD (no silent Chrome).
    - managed: always the Playwright browser.
    - auto:    bridge if connected, else managed (historical behavior).
    """
    m = mode.get(load_config().mode_default)
    if m == "bridge":
        if _bridge_ready():
            return _bridge_browser
        raise RoamError(
            "BRIDGE_DISCONNECTED",
            "mode is 'bridge' but the Roam Bridge extension is not connected",
            "call bridge(enable=True) and ensure the extension is loaded + enabled in your "
            "browser (chrome://extensions or edge://extensions); or call set_mode('auto') "
            "to fall back to the managed browser")
    if m == "managed":
        return _managed()
    # auto
    return _bridge_browser if _bridge_ready() else _managed()


_autostart_done = False


async def _maybe_autostart_bridge():
    """Start the bridge WS listener once at first tool use (config.bridge_auto). This way the
    moment the user has the extension loaded, the bridge is the default backend in auto mode —
    no explicit bridge() call needed. Idempotent and best-effort (never blocks tools)."""
    global _autostart_done, _bridge_srv, _bridge_browser
    if _autostart_done:
        return
    _autostart_done = True
    if os.environ.get("ROAM_DISABLE_BRIDGE_AUTOSTART"):
        return   # test isolation: never auto-attach to the user's real browser under pytest
    try:
        cfg = load_config()
        if cfg.bridge_auto and _bridge_srv is None:
            from .bridge import Bridge, BridgeBrowser
            _bridge_srv = Bridge(8777)
            await _bridge_srv.start()
            _bridge_browser = BridgeBrowser(_bridge_srv)
    except Exception:
        # port busy / websockets missing: fall back silently to managed; bridge() still works
        pass


def tool(coro):
    """Wrap a controller call: return ok(data) or err(RoamError)."""
    @functools.wraps(coro)
    async def inner(*a, **k):
        await _maybe_autostart_bridge()
        try:
            return ok(await coro(*a, **k))
        except RoamError as e:
            return err(e)
        except Exception as e:  # last-resort: never leak a raw traceback to the agent
            return err(RoamError("INTERNAL", str(e), ""))
    return inner


# ---- underscore impls (unit-testable) ----
@tool
async def _open(url: str | None = None, tab: int | None = None):
    """Open/ensure the managed browser (optionally navigate to url). Usually you don't need
    this — goto/read_markdown(url=…) auto-start the browser."""
    return await _ctl().open(url, tab=tab)
@tool
async def _goto(url: str, wait: str = "load", tab: int | None = None):
    """Navigate to a URL. wait = load|domcontentloaded|networkidle. To just READ a page,
    prefer read_markdown(url=…) — it navigates and returns clean content in one call."""
    return await _ctl().goto(url, wait, tab=tab)
@tool
async def _back(tab: int | None = None): return await _ctl().back(tab=tab)
@tool
async def _forward(tab: int | None = None): return await _ctl().forward(tab=tab)
@tool
async def _reload(tab: int | None = None): return await _ctl().reload(tab=tab)
@tool
async def _snapshot(interactive_only: bool = True, selector: str | None = None, tab: int | None = None,
                    url: str | None = None, wait: str = "load"):
    """List the page's interactive elements (links, buttons, inputs) with a stable `ref`
    for each — call before click/type when you want to target an element explicitly. Pass
    url= to navigate first. interactive_only=False includes all elements."""
    await _nav_if(url, tab, wait)
    return await _ctl().snapshot(interactive_only, selector, tab=tab)
@tool
async def _click(element: str = "", ref: str | None = None, selector: str | None = None,
                 x: float | None = None, y: float | None = None,
                 button: str = "left", count: int = 1, tab: int | None = None,
                 timeout: int | None = None):
    """Click an element. Easiest: describe it — click(element="Sign in") — and roam resolves
    it via selector memory + self-healing. Or pass a ref from snapshot(), a CSS selector, or
    x/y. No brittle hand-written selectors needed. timeout=ms bounds the actionability wait;
    on timeout you get NOT_ACTIONABLE with a hint instead of a silent miss."""
    return await _ctl().click(element, ref, selector, x, y, button, count, tab=tab, timeout=timeout)
@tool
async def _type(element: str = "", ref: str | None = None, selector: str | None = None,
                text: str = "", submit: bool = False, tab: int | None = None):
    """Type into a field — describe it: type(element="search box", text="...", submit=True).
    Or target by ref/selector. submit=True presses Enter after typing."""
    return await _ctl().type_text(element, ref, selector, text, submit, tab=tab)
@tool
async def _press(key: str, tab: int | None = None): return await _ctl().press(key, tab=tab)
@tool
async def _select(element: str = "", ref: str | None = None, selector: str | None = None,
                  values: list | None = None, tab: int | None = None):
    return await _ctl().select(element, ref, selector, values, tab=tab)
@tool
async def _hover(element: str = "", ref: str | None = None, selector: str | None = None,
                 tab: int | None = None):
    return await _ctl().hover(element, ref, selector, tab=tab)
@tool
async def _scroll(direction: str | None = None, ref: str | None = None, tab: int | None = None):
    return await _ctl().scroll(direction, ref, tab=tab)
@tool
async def _read(selector: str | None = None, ref: str | None = None, tab: int | None = None,
                url: str | None = None, wait: str = "load"):
    """Read the visible text of the page (or one element by selector/ref). Pass url= to
    navigate there first. For article/document content prefer read_markdown."""
    await _nav_if(url, tab, wait)
    return await _ctl().read(selector, ref, tab=tab)
@tool
async def _read_markdown(selector: str | None = None, tab: int | None = None,
                         url: str | None = None, wait: str = "load",
                         query: str | None = None, readability: bool = False):
    """Clean, token-cheap Markdown of the page (or one element). Pass url= to navigate
    there first — "read this page" in a single call. The go-to way to read web content
    for an agent: cheaper and clearer than raw HTML, and it works on stealth/logged-in
    pages a plain fetch can't reach. Pass query= to get back ONLY the passages relevant to
    that query (BM25 query-focused extraction). Pass readability=True for trafilatura
    main-content extraction (best on news/blog/docs; managed browser only)."""
    await _nav_if(url, tab, wait)
    ctl = _ctl()
    try:
        return await ctl.read_markdown(selector, tab=tab, query=query, readability=readability)
    except TypeError:
        return await ctl.read_markdown(selector, tab=tab, query=query)   # bridge: no readability arg
@tool
async def _dismiss_popups(tab: int | None = None):
    return await _ctl().dismiss_popups(tab=tab)
@tool
async def _find_links(keywords: list | None = None, tab: int | None = None,
                      url: str | None = None, wait: str = "load"):
    """List the page's links (text + href), optionally filtered to those whose text/href
    contains any of `keywords`. Pass url= to navigate first. Handy to pick the next page to
    read after a search."""
    await _nav_if(url, tab, wait)
    return {"links": await _ctl().find_links(keywords, tab=tab)}
@tool
async def _scrape(urls: list, concurrency: int = 5, engine: str = "browser",
                  fmt: str = "markdown", eval: str | None = None,
                  wait: str = "load", timeout_ms: int | None = None):
    """Scrape many URLs in parallel -> list of {url, ok, data|error}. The fast way to read
    or extract from a batch of pages in one call.
    engine: browser (real render, logged-in, beats anti-bot) | fast (no-render, TLS-emulated
    HTTP, much faster for static/JS-light pages) | auto (try fast, fall back to browser if blocked).
    fmt: markdown | text | links | assets | html. eval=JS runs per page and overrides fmt."""
    return {"results": await _ctl().scrape_many(urls, concurrency, engine, fmt, eval, wait, timeout_ms)}
@tool
async def _assets(tab: int | None = None, url: str | None = None,
                  kinds: list | None = None, wait: str = "load"):
    """List every sub-resource the page references (images, scripts, styles, fonts, media,
    iframes, links) as absolute URLs, categorized + flattened. Pass url= to navigate first.
    kinds filters categories, e.g. kinds=["images","media"]."""
    await _nav_if(url, tab, wait)
    return await _ctl().assets(kinds=kinds, tab=tab)
@tool
async def _web_search(query: str, site: str | None = None, filetype: str | None = None,
                      intitle: str | None = None, engine: str = "duckduckgo", tab: int | None = None):
    """Search the web and return the result links (text + href). Optional `site`, `filetype`,
    `intitle` operators. Follow up with read_markdown(url=<a result href>) to read a result."""
    from .search import build_search_url, is_engine_link
    from urllib.parse import urlparse
    url = build_search_url(query, site, filetype, intitle, engine)
    await _ctl().goto(url, tab=tab)
    eng = urlparse(url).netloc
    links = await _ctl().find_links(None, tab=tab)
    res = [l for l in links if l.get("text") and l["href"].startswith("http")
           and not is_engine_link(l["href"], eng)]
    return {"query_url": url, "results": res[:20]}
@tool
async def _eval(js: str, tab: int | None = None): return await _ctl().eval_js(js, tab=tab)
@tool
async def _observe(instruction: str, scope: str | None = None, max_results: int = 8,
                   tab: int | None = None, url: str | None = None, wait: str = "load"):
    """Plan in one shot: snapshot the page and return the interactive elements most relevant
    to `instruction`, ranked, each with a ref + suggested method — feed them straight into
    act()/click()/type() without re-reading the whole outline. url= navigates first."""
    await _nav_if(url, tab, wait)
    return await _ctl().observe(instruction, scope=scope, max_results=max_results, tab=tab)
@tool
async def _act(instruction: str, text: str | None = None, variables: dict | None = None,
               tab: int | None = None, timeout: int | None = None,
               url: str | None = None, wait: str = "load"):
    """Do it in ONE call: pick the element best matching `instruction`, wait for it to be
    actionable, then click or type (inferred). %name% placeholders in text/instruction are
    substituted from `variables` locally so secrets never enter the element-picking step.
    Self-heals (re-observes once) on a miss. e.g. act("click Sign in") /
    act("type into the search box", text="roam"). url= navigates first."""
    await _nav_if(url, tab, wait)
    return await _ctl().act(instruction, text=text, variables=variables, tab=tab, timeout=timeout)
@tool
async def _verify(text: str | None = None, selector: str | None = None,
                  value: str | None = None, visible: bool = False, tab: int | None = None):
    """Assert a condition on the page and get back {ok: bool, ...} — so you can CHECK a
    result instead of re-snapshotting and grepping. verify(text="Saved") confirms text is
    present; verify(selector="#email", value="a@b.com") confirms a field's value;
    verify(selector=".toast", visible=True) confirms an element is visible."""
    return await _ctl().verify(text=text, selector=selector, value=value,
                               visible=visible, tab=tab)
@tool
async def _console(level: str | None = None, tail: int = 50, tab: int | None = None):
    return await _ctl().console(level, tail, tab=tab)
@tool
async def _wait(for_: str, value: str | None = None, timeout: int | None = None, tab: int | None = None):
    return await _ctl().wait(for_, value, timeout, tab=tab)
@tool
async def _wait_for_ref(ref: str | None = None, selector: str | None = None,
                        state: str = "visible", timeout: int | None = None, tab: int | None = None):
    """Wait until an element is actionable before acting: state = visible | hidden | attached |
    detached | enabled | editable | stable. Returns {ok, state}. Gate a click on 'spinner
    gone' / 'button enabled' instead of blind-retrying the whole action."""
    return await _ctl().wait_for_ref(ref=ref, selector=selector, state=state,
                                     timeout=timeout, tab=tab)
@tool
async def _last_dialog(tab: int | None = None):
    """The most recent native dialog (alert/confirm/prompt) Roam auto-accepted, or null.
    Check this when a click 'did nothing' — it may have triggered a confirm()."""
    return {"dialog": await _ctl().last_dialog(tab=tab)}
@tool
async def _tabs(): return await _ctl().tabs()
@tool
async def _new_tab(url: str | None = None): return await _ctl().new_tab(url)
@tool
async def _switch_tab(id: str): return await _ctl().switch_tab(id)
@tool
async def _close_tab(id: str): return await _ctl().close_tab(id)
@tool
async def _cdp(method: str, params: dict | None = None, tab: int | None = None):
    return await _ctl().cdp(method, params, tab=tab)
@tool
async def _recall(url: str | None = None, query: str | None = None):
    url = await _current_url(url)
    rows = _memory().recall(url=url, query=query)
    return {"manual": rows, "text": format_manual(rows)}
@tool
async def _forget(domain: str):
    return {"forgotten": _memory().forget(domain)}
@tool
async def _save_manual(name: str, steps: list, url: str | None = None):
    url = await _current_url(url)
    _memory().save_manual(url, name, steps)
    return {"saved": name, "steps": len(steps), "site": (url or "")}
@tool
async def _recall_manual(name: str | None = None, url: str | None = None):
    url = await _current_url(url)
    return {"manuals": _memory().get_manual(url=url, name=name)}
@tool
async def _forget_manual(domain: str, name: str | None = None):
    return {"forgotten": _memory().forget_manual(domain, name)}
@tool
async def _controlled(on: bool = True, label: str = "Roam controlling", tab: int | None = None):
    return await _ctl().set_controlled(on, label=label, tab=tab)
@tool
async def _stealth_audit(tab: int | None = None):
    return await _ctl().stealth_audit(tab=tab)
@tool
async def _solve_cloudflare(max_attempts: int = 3, tab: int | None = None):
    return await _ctl().solve_cloudflare(max_attempts=max_attempts, tab=tab)
@tool
async def _extract(fields: dict, item_selector: str | None = None, tab: int | None = None,
                   url: str | None = None, wait: str = "load"):
    """Scrape repeating items (products, rows, search results) into structured JSON. `fields`
    maps output keys to CSS selectors; `item_selector` is the repeating container. Pass url= to
    navigate first. Returns the data plus a replayable Playwright script."""
    await _nav_if(url, tab, wait)
    return await _ctl().extract(fields, item_selector=item_selector, tab=tab)
@tool
async def _extract_auto(item_selector: str | None = None, max_items: int = 30, tab: int | None = None,
                        url: str | None = None, wait: str = "load"):
    """Auto-detect the largest group of repeating items on the page (product cards, search
    results, table rows) and pull a field-per-leaf table — NO pre-written selectors. Returns
    {schema, count, fields, data}. Pass item_selector to force the container; url= navigates first."""
    await _nav_if(url, tab, wait)
    return await _ctl().extract_auto(item_selector=item_selector, max_items=max_items, tab=tab)
@tool
async def _structured_data(tab: int | None = None, url: str | None = None, wait: str = "load"):
    """Collect structured data already embedded in the page — JSON-LD > schema.org microdata >
    OpenGraph/meta, merged into one map (title/description/image/price/brand/author/...).
    Deterministic, no LLM; the cheap+reliable way to get a page's key facts. url= navigates first."""
    await _nav_if(url, tab, wait)
    return await _ctl().structured_data(tab=tab)
@tool
async def _pdf(path: str | None = None, tab: int | None = None):
    return await _ctl().pdf(path=path, tab=tab)
@tool
async def _download(ref: str | None = None, selector: str | None = None,
                    url: str | None = None, path: str | None = None, tab: int | None = None):
    return await _ctl().download(ref=ref, selector=selector, url=url, path=path, tab=tab)
@tool
async def _upload(files, ref: str | None = None, selector: str | None = None, tab: int | None = None):
    return await _ctl().upload(files, ref=ref, selector=selector, tab=tab)
@tool
async def _cookies(action: str = "get", domain: str | None = None, tab: int | None = None):
    return await _ctl().cookies(action, domain=domain, tab=tab)
@tool
async def _record_api(enable: bool = True, tab: int | None = None):
    return await _ctl().record_api(enable, tab=tab)
@tool
async def _recipes(url: str | None = None, query: str | None = None):
    from urllib.parse import urlparse
    url = await _current_url(url)
    domain = urlparse(url).netloc if url else None
    return {"recipes": _memory().get_recipes(domain=domain, query=query)}
@tool
async def _heal(role: str, name: str, tab: int | None = None):
    url = await _current_url()
    fp = _memory().fingerprint_for(url=url, role=role, name=name)
    if not fp:
        raise RoamError("NO_FINGERPRINT", f"no remembered fingerprint for {role} {name}",
                        "act on the element once first so it gets remembered")
    res = await _ctl().relocate(fp, tab=tab)
    if res.get("selector"):
        _memory().update_selector(url=url, role=role, name=name, selector=res["selector"])
    return res
@tool
async def _bypass(enable: bool = True, rules_dir: str | None = None):
    return _ctl().set_bypass(enable, rules_dir)
@tool
async def _import_cookies(domain: str, source: str = "edge"):
    return await _ctl().import_cookies(domain, source)
@tool
async def _bridge(enable: bool = True, port: int = 8777, wait: bool = True, timeout: int = 15):
    """Drive your real, logged-in browser via the Roam Bridge extension.

    Call once with enable=True. It starts the local WebSocket and (by default) block-waits
    up to `timeout`s for the extension to AUTO-connect — there is NO manual "click to
    connect" step; the extension connects on its own as soon as it's loaded + enabled.
    Returns connected:true with the browser identity on success, or connected:false with an
    honest hint (install/enable the extension once at chrome://extensions or
    edge://extensions) on timeout. Pass wait=False for a non-blocking start.
    Once connected, set_mode('bridge') makes every tool target your real browser."""
    global _bridge_srv, _bridge_browser
    from .bridge import Bridge, BridgeBrowser
    if enable:
        if _bridge_srv is None:
            _bridge_srv = Bridge(port)
            await _bridge_srv.start()
            _bridge_browser = BridgeBrowser(_bridge_srv)
        connected = _bridge_srv.connected.is_set()
        if wait and not connected:
            connected = await _bridge_srv.wait_ready(timeout)
        if connected:
            return {"bridge": "connected", "port": port, "connected": True,
                    "browser": _bridge_srv.hello,
                    "hint": "bridge is live; call set_mode('bridge') to pin all tools to your real browser"}
        return {"bridge": "listening", "port": port, "connected": False,
                "waited_s": timeout if wait else 0,
                "hint": "the extension auto-connects with NO manual click — if still "
                        "disconnected, load/enable the Roam Bridge extension once at "
                        "chrome://extensions (or edge://extensions), then call bridge() again"}
    if _bridge_srv is not None:
        await _bridge_srv.stop()
        _bridge_srv = None
        _bridge_browser = None
    return {"bridge": "stopped"}
@tool
async def _bridge_status():
    return {"listening": _bridge_srv is not None,
            "connected": bool(_bridge_srv and _bridge_srv.connected.is_set()),
            "browser": (_bridge_srv.hello if _bridge_srv else None)}
@tool
async def _set_mode(mode_name: str = "auto"):
    """Pin which browser every tool drives for this session: 'bridge' (your real browser via
    the extension — fails loud if not connected, never silently opens a different browser),
    'managed' (Roam's own Playwright browser), or 'auto' (bridge if connected, else managed).
    Sticky until changed."""
    m = mode.set_mode(mode_name)
    return {"mode": m, "bridge_connected": _bridge_ready()}
@tool
async def _mode():
    """Report the active backend: explicit session mode, what the next call will actually
    hit, the managed browser channel/headless, and whether the bridge is connected."""
    cfg = load_config()
    explicit = mode.get(cfg.mode_default)
    effective = ("bridge" if (explicit in ("bridge", "auto") and _bridge_ready())
                 else ("bridge" if explicit == "bridge" else "managed"))
    ch = None
    try:
        ch = _managed()._resolve_channel() if effective == "managed" else None
    except Exception:
        pass
    return {"explicit": explicit, "effective": effective,
            "bridge_connected": _bridge_ready(),
            "channel": ch or ("real browser" if effective == "bridge" else "chromium"),
            "headless": cfg.headless,
            "browser": (_bridge_srv.hello if (_bridge_srv and effective == "bridge") else None)}
@tool
async def _set_channel(channel: str = "auto", headless: bool = False):
    """Set the MANAGED browser engine: 'chrome', 'msedge' (Edge), 'chromium' (bundled), or
    'auto' (detect). Relaunches the managed browser so the change takes effect. Does not
    affect bridge mode (that's always your real browser)."""
    global _controller
    cfg = load_config()
    cfg.channel = channel
    cfg.headless = headless
    if _controller is not None:
        try:
            await _controller.close()
        except Exception:
            pass
    _controller = BrowserController(cfg)
    return {"channel": channel, "headless": headless,
            "resolved": _controller._resolve_channel() or "chromium"}


# ---- screenshot is special: returns an inline image to the agent ----
async def _screenshot_impl(full: bool = False, selector: str | None = None, tab: int | None = None):
    try:
        data = await _ctl().screenshot(full, selector, tab=tab)
        return Image(data=data, format="png")
    except RoamError as e:
        return err(e)


# ---- MCP registration (public tool names) ----
_REGISTRY = {
    "open": _open, "goto": _goto, "back": _back, "forward": _forward, "reload": _reload,
    "snapshot": _snapshot, "click": _click, "type": _type, "press": _press,
    "select": _select, "hover": _hover, "scroll": _scroll, "read": _read, "eval": _eval,
    "verify": _verify, "observe": _observe, "act": _act,
    "console": _console, "wait": _wait, "wait_for_ref": _wait_for_ref,
    "last_dialog": _last_dialog, "tabs": _tabs, "new_tab": _new_tab,
    "switch_tab": _switch_tab, "close_tab": _close_tab, "cdp": _cdp,
    "recall": _recall, "forget": _forget, "bypass": _bypass,
    "import_cookies": _import_cookies, "bridge": _bridge, "bridge_status": _bridge_status,
    "set_mode": _set_mode, "mode": _mode, "set_channel": _set_channel,
    "save_manual": _save_manual, "recall_manual": _recall_manual, "forget_manual": _forget_manual,
    "stealth_audit": _stealth_audit, "read_markdown": _read_markdown, "heal": _heal,
    "dismiss_popups": _dismiss_popups, "find_links": _find_links, "web_search": _web_search,
    "scrape": _scrape, "assets": _assets,
    "controlled": _controlled, "solve_cloudflare": _solve_cloudflare,
    "record_api": _record_api, "recipes": _recipes,
    "extract": _extract, "extract_auto": _extract_auto, "structured_data": _structured_data,
    "pdf": _pdf, "download": _download, "upload": _upload,
    "cookies": _cookies,
}
TOOL_NAMES = list(_REGISTRY) + ["screenshot"]

for _name, _fn in _REGISTRY.items():
    mcp.tool(name=_name)(_fn)
mcp.tool(name="screenshot")(_screenshot_impl)
