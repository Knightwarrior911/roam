import functools
import os
from mcp.server.fastmcp import FastMCP, Image
from .browser import BrowserController
from .config import load_config
from .errors import RoamError, ok, err
from .memory import SelectorMemory, format_manual

mcp = FastMCP("roam")
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


def _ctl():
    # when the browser extension is connected, drive the user's real browser
    if _bridge_browser is not None and _bridge_srv is not None and _bridge_srv.connected.is_set():
        return _bridge_browser
    global _controller
    if _controller is None:
        _controller = BrowserController(load_config())
    return _controller


def tool(coro):
    """Wrap a controller call: return ok(data) or err(RoamError)."""
    @functools.wraps(coro)
    async def inner(*a, **k):
        try:
            return ok(await coro(*a, **k))
        except RoamError as e:
            return err(e)
        except Exception as e:  # last-resort: never leak a raw traceback to the agent
            return err(RoamError("INTERNAL", str(e), ""))
    return inner


# ---- underscore impls (unit-testable) ----
@tool
async def _open(url: str | None = None, tab: int | None = None): return await _ctl().open(url, tab=tab)
@tool
async def _goto(url: str, wait: str = "load", tab: int | None = None): return await _ctl().goto(url, wait, tab=tab)
@tool
async def _back(tab: int | None = None): return await _ctl().back(tab=tab)
@tool
async def _forward(tab: int | None = None): return await _ctl().forward(tab=tab)
@tool
async def _reload(tab: int | None = None): return await _ctl().reload(tab=tab)
@tool
async def _snapshot(interactive_only: bool = True, selector: str | None = None, tab: int | None = None):
    return await _ctl().snapshot(interactive_only, selector, tab=tab)
@tool
async def _click(element: str = "", ref: str | None = None, selector: str | None = None,
                 x: float | None = None, y: float | None = None,
                 button: str = "left", count: int = 1, tab: int | None = None):
    return await _ctl().click(element, ref, selector, x, y, button, count, tab=tab)
@tool
async def _type(element: str = "", ref: str | None = None, selector: str | None = None,
                text: str = "", submit: bool = False, tab: int | None = None):
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
async def _read(selector: str | None = None, ref: str | None = None, tab: int | None = None):
    return await _ctl().read(selector, ref, tab=tab)
@tool
async def _read_markdown(selector: str | None = None, tab: int | None = None):
    return await _ctl().read_markdown(selector, tab=tab)
@tool
async def _dismiss_popups(tab: int | None = None):
    return await _ctl().dismiss_popups(tab=tab)
@tool
async def _find_links(keywords: list | None = None, tab: int | None = None):
    return {"links": await _ctl().find_links(keywords, tab=tab)}
@tool
async def _web_search(query: str, site: str | None = None, filetype: str | None = None,
                      intitle: str | None = None, engine: str = "duckduckgo", tab: int | None = None):
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
async def _console(level: str | None = None, tail: int = 50, tab: int | None = None):
    return await _ctl().console(level, tail, tab=tab)
@tool
async def _wait(for_: str, value: str | None = None, timeout: int | None = None, tab: int | None = None):
    return await _ctl().wait(for_, value, timeout, tab=tab)
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
async def _extract(fields: dict, item_selector: str | None = None, tab: int | None = None):
    return await _ctl().extract(fields, item_selector=item_selector, tab=tab)
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
async def _bridge(enable: bool = True, port: int = 8777):
    global _bridge_srv, _bridge_browser
    from .bridge import Bridge, BridgeBrowser
    if enable:
        if _bridge_srv is None:
            _bridge_srv = Bridge(port)
            await _bridge_srv.start()
            _bridge_browser = BridgeBrowser(_bridge_srv)
        return {"bridge": "listening", "port": port,
                "connected": _bridge_srv.connected.is_set(),
                "hint": "load the Roam Bridge extension in your browser; tools then drive it"}
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
    "console": _console, "wait": _wait, "tabs": _tabs, "new_tab": _new_tab,
    "switch_tab": _switch_tab, "close_tab": _close_tab, "cdp": _cdp,
    "recall": _recall, "forget": _forget, "bypass": _bypass,
    "import_cookies": _import_cookies, "bridge": _bridge, "bridge_status": _bridge_status,
    "save_manual": _save_manual, "recall_manual": _recall_manual, "forget_manual": _forget_manual,
    "stealth_audit": _stealth_audit, "read_markdown": _read_markdown, "heal": _heal,
    "dismiss_popups": _dismiss_popups, "find_links": _find_links, "web_search": _web_search,
    "controlled": _controlled, "solve_cloudflare": _solve_cloudflare,
    "record_api": _record_api, "recipes": _recipes,
    "extract": _extract, "pdf": _pdf, "download": _download, "upload": _upload,
    "cookies": _cookies,
}
TOOL_NAMES = list(_REGISTRY) + ["screenshot"]

for _name, _fn in _REGISTRY.items():
    mcp.tool(name=_name)(_fn)
mcp.tool(name="screenshot")(_screenshot_impl)
