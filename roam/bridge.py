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
    Mirrors BrowserController's core surface so the MCP tools work unchanged."""

    def __init__(self, bridge: Bridge):
        self.bridge = bridge

    async def open(self, url=None):
        return await self.goto(url) if url else await self.bridge.call("status")

    async def goto(self, url, wait="load"):
        return await self.bridge.call("navigate", {"url": url})

    async def back(self):
        return await self.bridge.call("back")

    async def forward(self):
        return await self.bridge.call("forward")

    async def reload(self):
        return await self.bridge.call("reload")

    async def snapshot(self, interactive_only=True, selector=None):
        r = await self.bridge.call("snapshot", {"interactive_only": interactive_only})
        return r["outline"]

    async def click(self, element=None, ref=None, selector=None, x=None, y=None,
                    button="left", count=1):
        return await self.bridge.call("click", {"ref": ref, "selector": selector})

    async def type_text(self, element=None, ref=None, selector=None, text="", submit=False):
        return await self.bridge.call("type", {"ref": ref, "selector": selector,
                                               "text": text, "submit": submit})

    async def read(self, selector=None, ref=None):
        return (await self.bridge.call("text", {"selector": selector}))["text"]

    async def eval_js(self, js):
        return (await self.bridge.call("eval", {"js": js}))["value"]

    async def screenshot(self, full=False, selector=None):
        import base64
        data_url = (await self.bridge.call("screenshot"))["dataUrl"]
        return base64.b64decode(data_url.split(",", 1)[1])

    async def tabs(self):
        return (await self.bridge.call("tabs"))["tabs"]

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

