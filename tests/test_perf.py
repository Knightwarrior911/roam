"""P4/P3.7: cache layer, output budgeting, prewarm, cold-start fast-fail."""
import pytest
import roam.server as srv
from roam import cache
from tests.conftest import FIXTURE


@pytest.fixture
async def _srv_ctl(tmp_path):
    from roam.config import Config
    from roam.browser import BrowserController
    srv._controller = BrowserController(
        Config(headless=True, channel=None, profile_dir=str(tmp_path / "p")))
    cache.clear(); cache.set_enabled(True)
    yield
    await srv._controller.close()
    srv._controller = None
    cache.clear()


# ---- cache unit ----
def test_cache_hashes_by_variable_name_not_value():
    cache.clear()
    cache.put("k", "u", {"vars": {"user": "NAME"}}, "v1")
    val, hit = cache.get("k", "u", {"vars": {"user": "NAME"}})
    assert hit and val == "v1"
    _, miss = cache.get("k", "u", {"vars": {"user": "OTHER"}})
    assert miss is False


def test_cache_lru_eviction():
    cache.clear()
    for i in range(200):
        cache.put("k", f"u{i}", {}, i)
    assert cache.stats()["entries"] <= cache.stats()["max"]


# ---- read_markdown cache HIT/MISS ----
async def test_read_markdown_cache_hit(_srv_ctl):
    r1 = await srv._read_markdown(url=FIXTURE, use_cache=True)
    assert r1["ok"] and r1["data"]["cache"] == "MISS"
    r2 = await srv._read_markdown(url=FIXTURE, use_cache=True)
    assert r2["ok"] and r2["data"]["cache"] == "HIT"
    assert r2["data"]["markdown"] == r1["data"]["markdown"]


async def test_read_markdown_max_chars_truncates(_srv_ctl):
    r = await srv._read_markdown(url=FIXTURE, max_chars=20)
    assert r["ok"]
    assert "truncated" in r["data"]


# ---- prewarm + cache tools ----
async def test_prewarm_and_cache_tools(_srv_ctl):
    assert {"prewarm", "cache"} <= set(srv.TOOL_NAMES)
    r = await srv._prewarm()
    assert r["ok"] and r["data"]["prewarmed"] is True
    s = await srv._cache(action="stats")
    assert s["ok"] and "entries" in s["data"]
