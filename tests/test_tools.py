import pytest
import roam.server as srv
from tests.conftest import FIXTURE


@pytest.fixture(autouse=True)
async def _reset(tmp_path):
    # force a fresh, headless, bundled-chromium controller per test
    from roam.config import Config
    from roam.browser import BrowserController
    srv._controller = BrowserController(
        Config(headless=True, channel=None, profile_dir=str(tmp_path / "p")))
    yield
    await srv._controller.close()
    srv._controller = None


async def test_open_and_snapshot_envelope():
    r = await srv._open(url=FIXTURE)
    assert r["ok"] is True and "url" in r["data"]
    s = await srv._snapshot()
    assert s["ok"] is True and "[ref=" in s["data"]


async def test_error_surfaces_as_envelope():
    await srv._open(url=FIXTURE)
    r = await srv._switch_tab(id="t999")
    assert r["ok"] is False and r["error"]["code"] == "TAB_NOT_FOUND"


def test_browsermcp_parity_present():
    names = set(srv.TOOL_NAMES)
    parity = {"goto", "snapshot", "click", "hover", "type", "select", "press",
              "screenshot", "console", "back", "forward", "wait"}
    assert parity <= names
