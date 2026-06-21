"""P0: explicit sticky mode + truthful bridge() wait + Edge channel detection."""
import asyncio
import json

import pytest
import websockets

import roam.server as srv
from roam import mode
from roam.bridge import Bridge


@pytest.fixture(autouse=True)
def _reset_mode():
    mode.reset()
    yield
    mode.reset()


# ---- mode routing ----
def test_mode_tools_registered():
    assert {"set_mode", "mode", "set_channel"} <= set(srv.TOOL_NAMES)


def test_set_mode_validates():
    assert mode.set_mode("bridge") == "bridge"
    assert mode.set_mode("managed") == "managed"
    assert mode.set_mode("auto") == "auto"
    with pytest.raises(ValueError):
        mode.set_mode("nonsense")


async def test_explicit_bridge_fails_loud_when_disconnected(tmp_path):
    # mode=bridge with no bridge connected must NOT silently launch a managed browser
    srv._bridge_srv = None
    srv._bridge_browser = None
    await srv._set_mode("bridge")
    r = await srv._goto(url="https://example.com")
    assert r["ok"] is False
    assert r["error"]["code"] == "BRIDGE_DISCONNECTED"


async def test_set_mode_envelope():
    r = await srv._set_mode("auto")
    assert r["ok"] is True and r["data"]["mode"] == "auto"


# ---- truthful bridge() wait ----
async def _sim_extension(stop, port):
    while not stop.is_set():
        try:
            async with websockets.connect(f"ws://127.0.0.1:{port}") as ws:
                await ws.send(json.dumps({"type": "hello", "version": "sim"}))
                async for raw in ws:
                    m = json.loads(raw)
                    if m.get("type") == "ping":
                        await ws.send(json.dumps({"type": "pong"}))
        except Exception:
            await asyncio.sleep(0.05)


async def test_bridge_tool_reports_connected_true_after_wait():
    # start the bridge tool with a sim extension; it must block-wait and report connected:true
    srv._bridge_srv = None
    srv._bridge_browser = None
    port = 8791
    stop = asyncio.Event()
    t = asyncio.create_task(_sim_extension(stop, port))
    try:
        r = await srv._bridge(enable=True, port=port, wait=True, timeout=10)
        assert r["ok"] is True
        assert r["data"]["connected"] is True
        assert r["data"]["bridge"] == "connected"
        assert r["data"]["browser"]["version"] == "sim"
    finally:
        stop.set()
        t.cancel()
        try:
            await t
        except (asyncio.CancelledError, Exception):
            pass
        await srv._bridge(enable=False)


async def test_bridge_tool_honest_timeout_no_extension():
    srv._bridge_srv = None
    srv._bridge_browser = None
    r = await srv._bridge(enable=True, port=8792, wait=True, timeout=1)
    try:
        assert r["ok"] is True
        assert r["data"]["connected"] is False
        # hint must NOT imply a manual click step
        assert "auto-connect" in r["data"]["hint"]
        assert "click" not in r["data"]["hint"].lower() or "no manual click" in r["data"]["hint"].lower()
    finally:
        await srv._bridge(enable=False)


# ---- wait_ready primitive ----
async def test_wait_ready_true_then_false():
    br = Bridge(8793)
    await br.start()
    stop = asyncio.Event()
    t = asyncio.create_task(_sim_extension(stop, 8793))
    try:
        assert await br.wait_ready(10) is True
        assert br.attached.is_set()
    finally:
        stop.set()
        t.cancel()
        try:
            await t
        except (asyncio.CancelledError, Exception):
            pass
        await br.stop()
    br2 = Bridge(8794)
    await br2.start()
    try:
        assert await br2.wait_ready(1) is False   # nobody connects
    finally:
        await br2.stop()


# ---- Edge channel detection ----
def test_detect_default_browser_returns_valid():
    from roam.config import detect_default_browser
    assert detect_default_browser() in ("chrome", "msedge", "chromium")


def test_resolve_channel_auto_and_chromium(tmp_path):
    from roam.config import Config
    from roam.browser import BrowserController
    c1 = BrowserController(Config(channel="chromium", profile_dir=str(tmp_path / "a")))
    assert c1._resolve_channel() is None            # bundled -> omit channel
    c2 = BrowserController(Config(channel="msedge", profile_dir=str(tmp_path / "b")))
    assert c2._resolve_channel() == "msedge"
    c3 = BrowserController(Config(channel="auto", profile_dir=str(tmp_path / "c")))
    assert c3._resolve_channel() in ("chrome", "msedge", None)  # None if only chromium found
