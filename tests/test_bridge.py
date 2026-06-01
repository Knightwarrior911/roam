import asyncio
import json

import pytest
import websockets

from roam.bridge import Bridge, BridgeError


async def _sim(stop, port, behavior="echo"):
    """Stand-in for the browser extension: connect, say hello, answer/ignore commands."""
    while not stop.is_set():
        try:
            async with websockets.connect(f"ws://127.0.0.1:{port}") as ws:
                await ws.send(json.dumps({"type": "hello", "version": "sim"}))
                async for raw in ws:
                    m = json.loads(raw)
                    if m.get("type") == "ping":
                        await ws.send(json.dumps({"type": "pong"}))
                        continue
                    if m.get("id") and behavior == "echo":
                        await ws.send(json.dumps({"id": m["id"], "result": {"echo": m["method"]}}))
                    # behavior == "silent": never reply (exercises call timeout)
        except Exception:
            await asyncio.sleep(0.1)


async def test_connect_call_and_auto_reconnect():
    br = Bridge(8799)
    await br.start()
    stop = asyncio.Event()
    t = asyncio.create_task(_sim(stop, 8799))
    try:
        await br.wait_connected(10)
        assert br.connected.is_set()
        assert await br.call("ping") == {"echo": "ping"}
        await br._conn.close()              # force a drop
        await br.wait_connected(10)          # extension auto-reconnects
        assert (await br.call("status"))["echo"] == "status"
    finally:
        stop.set()
        t.cancel()
        try:
            await t
        except (asyncio.CancelledError, Exception):
            pass
        await br.stop()


async def test_call_without_connection_raises():
    br = Bridge(8798)
    await br.start()
    try:
        with pytest.raises(BridgeError):
            await br.call("ping", timeout=1)
    finally:
        await br.stop()


async def test_call_times_out_on_silent_peer():
    br = Bridge(8797)
    await br.start()
    stop = asyncio.Event()
    t = asyncio.create_task(_sim(stop, 8797, behavior="silent"))
    try:
        await br.wait_connected(10)
        with pytest.raises(BridgeError):
            await br.call("ping", timeout=1)
    finally:
        stop.set()
        t.cancel()
        try:
            await t
        except (asyncio.CancelledError, Exception):
            pass
        await br.stop()
