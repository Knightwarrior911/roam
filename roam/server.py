import functools
from mcp.server.fastmcp import FastMCP, Image
from .browser import BrowserController
from .config import load_config
from .errors import RoamError, ok, err

mcp = FastMCP("roam")
_controller = None
_bridge_srv = None       # bridge.Bridge (WS server) when bridge mode is on
_bridge_browser = None   # bridge.BridgeBrowser, used while the extension is connected


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
async def _open(url: str | None = None): return await _ctl().open(url)
@tool
async def _goto(url: str, wait: str = "load"): return await _ctl().goto(url, wait)
@tool
async def _back(): return await _ctl().back()
@tool
async def _forward(): return await _ctl().forward()
@tool
async def _reload(): return await _ctl().reload()
@tool
async def _snapshot(interactive_only: bool = True, selector: str | None = None):
    return await _ctl().snapshot(interactive_only, selector)
@tool
async def _click(element: str = "", ref: str | None = None, selector: str | None = None,
                 x: float | None = None, y: float | None = None,
                 button: str = "left", count: int = 1):
    return await _ctl().click(element, ref, selector, x, y, button, count)
@tool
async def _type(element: str = "", ref: str | None = None, selector: str | None = None,
                text: str = "", submit: bool = False):
    return await _ctl().type_text(element, ref, selector, text, submit)
@tool
async def _press(key: str): return await _ctl().press(key)
@tool
async def _select(element: str = "", ref: str | None = None, selector: str | None = None,
                  values: list | None = None):
    return await _ctl().select(element, ref, selector, values)
@tool
async def _hover(element: str = "", ref: str | None = None, selector: str | None = None):
    return await _ctl().hover(element, ref, selector)
@tool
async def _scroll(direction: str | None = None, ref: str | None = None):
    return await _ctl().scroll(direction, ref)
@tool
async def _read(selector: str | None = None, ref: str | None = None):
    return await _ctl().read(selector, ref)
@tool
async def _eval(js: str): return await _ctl().eval_js(js)
@tool
async def _console(level: str | None = None, tail: int = 50):
    return await _ctl().console(level, tail)
@tool
async def _wait(for_: str, value: str | None = None, timeout: int | None = None):
    return await _ctl().wait(for_, value, timeout)
@tool
async def _tabs(): return await _ctl().tabs()
@tool
async def _new_tab(url: str | None = None): return await _ctl().new_tab(url)
@tool
async def _switch_tab(id: str): return await _ctl().switch_tab(id)
@tool
async def _close_tab(id: str): return await _ctl().close_tab(id)
@tool
async def _cdp(method: str, params: dict | None = None):
    return await _ctl().cdp(method, params)
@tool
async def _recall(url: str | None = None): return await _ctl().recall(url)
@tool
async def _forget(domain: str): return await _ctl().forget(domain)
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
async def _screenshot_impl(full: bool = False, selector: str | None = None):
    try:
        data = await _ctl().screenshot(full, selector)
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
}
TOOL_NAMES = list(_REGISTRY) + ["screenshot"]

for _name, _fn in _REGISTRY.items():
    mcp.tool(name=_name)(_fn)
mcp.tool(name="screenshot")(_screenshot_impl)
