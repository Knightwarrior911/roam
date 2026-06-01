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
        self._server = await websockets.serve(self._handler, "127.0.0.1", self.port)
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
