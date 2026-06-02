"""Roam bridge server: a local WebSocket the Roam Bridge extension connects to, so Roam
can drive the user's real, logged-in browser tabs. Reliability is the point: the extension
auto-reconnects with backoff + heartbeat; this server treats the newest connection as live,
fails in-flight calls cleanly on drop, and times out rather than hanging.
"""
import asyncio
import itertools
import json

try:
    import websockets
except ImportError:  # optional dep; only needed for bridge mode
    websockets = None


class BridgeError(Exception):
    pass


class Bridge:
    def __init__(self, port=8777):
        self.port = port
        self._server = None
        self._conn = None          # the live extension websocket
        self._pending = {}         # request id -> Future
        self._ids = itertools.count(1)
        self.connected = asyncio.Event()
        self.hello = None

    async def start(self):
        if websockets is None:
            raise BridgeError("pip install websockets to use bridge mode")
        # raise max message size: full-page screenshots (base64 PNG) exceed the 1 MiB default
        self._server = await websockets.serve(self._handler, "127.0.0.1", self.port,
                                              max_size=64 * 1024 * 1024)
        return self

    async def stop(self):
        if self._server:
            self._server.close()
            await self._server.wait_closed()
            self._server = None

    async def _handler(self, ws, *args):   # path arg varies by websockets version
        self._conn = ws                     # newest connection wins
        try:
            async for raw in ws:
                try:
                    msg = json.loads(raw)
                except Exception:
                    continue
                t = msg.get("type")
                if t == "hello":
                    self.hello = msg
                    self.connected.set()
                    continue
                if t == "ping":
                    await ws.send(json.dumps({"type": "pong"}))
                    continue
                if t == "pong":
                    continue
                mid = msg.get("id")
                fut = self._pending.pop(mid, None)
                if fut and not fut.done():
                    fut.set_result(msg)
        finally:
            if self._conn is ws:
                self._conn = None
                self.connected.clear()
                # fail any in-flight calls so callers don't hang
                for fut in self._pending.values():
                    if not fut.done():
                        fut.set_exception(BridgeError("bridge connection dropped"))
                self._pending.clear()

    async def wait_connected(self, timeout=30):
        await asyncio.wait_for(self.connected.wait(), timeout)

    async def call(self, method, params=None, timeout=30):
        if self._conn is None:
            raise BridgeError("no browser connected to the bridge")
        mid = next(self._ids)
        fut = asyncio.get_event_loop().create_future()
        self._pending[mid] = fut
        await self._conn.send(json.dumps({"id": mid, "method": method, "params": params or {}}))
        try:
            msg = await asyncio.wait_for(fut, timeout)
        except asyncio.TimeoutError:
            self._pending.pop(mid, None)
            raise BridgeError(f"bridge call '{method}' timed out after {timeout}s")
        if msg.get("error"):
            raise BridgeError(msg["error"])
        return msg.get("result")


class BridgeBrowser:
    """Drives the user's real, logged-in browser through the Roam Bridge extension.
    Mirrors BrowserController's surface so the MCP tools work unchanged. Every method
    takes an optional `tab` (a real browser tab id) so Roam can drive many tabs at once;
    omit it to use the active tab. Concurrent calls on different tabs run in parallel."""

    def __init__(self, bridge: Bridge):
        self.bridge = bridge

    @staticmethod
    def _t(params, tab):
        if tab is not None:
            params["tabId"] = tab
        return params

    async def open(self, url=None, tab=None):
        return await self.goto(url, tab=tab) if url else await self.bridge.call("status", self._t({}, tab))

    async def goto(self, url, wait="load", tab=None):
        return await self.bridge.call("navigate", self._t({"url": url}, tab), timeout=60)

    async def back(self, tab=None):
        return await self.bridge.call("back", self._t({}, tab))

    async def forward(self, tab=None):
        return await self.bridge.call("forward", self._t({}, tab))

    async def reload(self, tab=None):
        return await self.bridge.call("reload", self._t({}, tab), timeout=60)

    async def snapshot(self, interactive_only=True, selector=None, tab=None):
        r = await self.bridge.call("snapshot", self._t({"interactive_only": interactive_only}, tab))
        return r["outline"]

    async def click(self, element=None, ref=None, selector=None, x=None, y=None,
                    button="left", count=1, tab=None):
        return await self.bridge.call("click", self._t({"ref": ref, "selector": selector}, tab))

    async def type_text(self, element=None, ref=None, selector=None, text="", submit=False, tab=None):
        return await self.bridge.call("type", self._t({"ref": ref, "selector": selector,
                                                       "text": text, "submit": submit}, tab))

    async def select(self, element=None, ref=None, selector=None, values=None, tab=None):
        return await self.bridge.call("select", self._t({"ref": ref, "selector": selector,
                                                         "values": values or []}, tab))

    async def hover(self, element=None, ref=None, selector=None, tab=None):
        return await self.bridge.call("hover", self._t({"ref": ref, "selector": selector}, tab))

    async def press(self, key, tab=None):
        return await self.bridge.call("press", self._t({"key": key}, tab))

    async def scroll(self, direction=None, ref=None, tab=None):
        return await self.bridge.call("scroll", self._t({"direction": direction, "ref": ref}, tab))

    async def read(self, selector=None, ref=None, tab=None):
        return (await self.bridge.call("text", self._t({"selector": selector}, tab)))["text"]

    async def read_markdown(self, selector=None, tab=None):
        from .markdown import to_markdown
        r = await self.bridge.call("clean_html", self._t({"selector": selector}, tab))
        return to_markdown(r.get("html", ""))

    async def dismiss_popups(self, tab=None):
        return await self.bridge.call("dismiss", self._t({}, tab))

    async def find_links(self, keywords=None, tab=None):
        r = await self.bridge.call("find_links", self._t({"keywords": keywords or []}, tab))
        return r.get("links", [])

    async def eval_js(self, js, tab=None):
        return (await self.bridge.call("eval", self._t({"js": js}, tab)))["value"]

    async def wait(self, for_, value=None, timeout=None, tab=None):
        return await self.bridge.call("wait", self._t({"for": for_, "value": value,
                                                       "timeout": timeout}, tab), timeout=(timeout or 15000) / 1000 + 10)

    async def cdp(self, method, params=None, tab=None):
        return (await self.bridge.call("cdp", self._t({"cdpMethod": method, "cdpParams": params or {}}, tab)))["result"]

    async def screenshot(self, full=False, selector=None, tab=None):
        import base64
        data_url = (await self.bridge.call("screenshot", self._t({"full": full}, tab), timeout=45))["dataUrl"]
        return base64.b64decode(data_url.split(",", 1)[1])

    async def console(self, level=None, tail=50, tab=None):
        return ["(console capture not available over the bridge; use eval to read state)"]

    async def url(self):
        return (await self.bridge.call("status"))["url"]

    async def set_controlled(self, on=True, label="Roam controlling", color="#6c5ce7", tab=None):
        # explicit cue toggle over the bridge (the extension also auto-cues on action)
        r = await self.bridge.call("cue", self._t({"on": on, "label": label, "color": color}, tab))
        shown = r.get("shown", bool(on)) if isinstance(r, dict) else bool(on)
        return {"controlled": bool(on), "shown": shown}

    async def stealth_audit(self, tab=None):
        from .stealth import audit_verdict
        return audit_verdict(await self.bridge.call("audit", self._t({}, tab)))

    async def solve_cloudflare(self, max_attempts=3, tab=None):
        # the bridge drives a real browser, which clears Cloudflare natively; there's no
        # automated cursor to coordinate-click with here. Be honest rather than pretend.
        return {"solved": None, "attempts": 0, "type": None,
                "note": "bridge uses your real browser — it passes Cloudflare natively; "
                        "if a challenge persists, click it once by hand"}

    async def record_api(self, enable=True, tab=None):
        # Bridge-side network capture (debugger Network domain in the extension) is the next
        # increment; today, capture API recipes by visiting the site in the managed browser.
        return {"recording": False,
                "note": "API-recipe capture currently runs on the managed browser; "
                        "bridge-side capture via the extension is a planned increment"}

    async def extract(self, fields, item_selector=None, tab=None):
        r = await self.bridge.call("extract", self._t({"fields": fields, "item": item_selector}, tab))
        return r.get("data") if isinstance(r, dict) else r

    async def pdf(self, path=None, tab=None):
        import base64, os
        data = (await self.bridge.call("pdf", self._t({}, tab), timeout=60))["data"]
        dest = path or os.path.join(os.getcwd(), "page.pdf")
        with open(dest, "wb") as f:
            f.write(base64.b64decode(data))
        return {"pdf": dest}

    async def download(self, ref=None, selector=None, url=None, path=None, tab=None):
        return {"downloaded": None,
                "note": "downloads land in your real browser's Downloads folder; trigger the "
                        "link normally — bridge-mediated save is a planned increment"}

    async def upload(self, files, ref=None, selector=None, tab=None):
        return {"uploaded": None,
                "note": "file-input upload over the bridge needs DOM.setFileInputFiles "
                        "(planned); use the managed browser for automated uploads"}

    async def relocate(self, fingerprint, tab=None):
        return await self.bridge.call("relocate", self._t({"fp": fingerprint}, tab))

    async def tabs(self):
        return (await self.bridge.call("tabs"))["tabs"]

    async def new_tab(self, url=None):
        return await self.bridge.call("open_tab", {"url": url})

    async def switch_tab(self, tab_id):
        return await self.bridge.call("switch_tab", {"tabId": tab_id})

    async def close_tab(self, tab_id):
        return await self.bridge.call("close_tab", {"tabId": tab_id})

    async def close(self):
        pass   # the bridge drives the user's own browser; never close it


async def _serve_forever(port=8777):
    br = await Bridge(port).start()
    print(f"Roam bridge listening on ws://127.0.0.1:{port} "
          f"— load the Roam Bridge extension in your browser to connect.")
    await asyncio.Future()   # run until killed


if __name__ == "__main__":
    import sys
    _port = int(sys.argv[1]) if len(sys.argv) > 1 else 8777
    asyncio.run(_serve_forever(_port))

