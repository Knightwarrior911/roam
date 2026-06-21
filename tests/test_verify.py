"""P3.5: assertion tool."""
import pytest
import roam.server as srv
from tests.conftest import FIXTURE


@pytest.fixture
async def _srv_ctl(tmp_path):
    from roam.config import Config
    from roam.browser import BrowserController
    srv._controller = BrowserController(
        Config(headless=True, channel=None, profile_dir=str(tmp_path / "p")))
    yield
    await srv._controller.close()
    srv._controller = None


async def test_verify_text_present(ctl):
    r = await ctl.verify(text="Roam Test Page")
    assert r["ok"] is True and r["present"] is True


async def test_verify_text_absent(ctl):
    r = await ctl.verify(text="this is definitely not on the page zzz")
    assert r["ok"] is False


async def test_verify_selector_found(ctl):
    r = await ctl.verify(selector="#title")
    assert r["ok"] is True and r["found"] is True


async def test_verify_selector_missing(ctl):
    r = await ctl.verify(selector="#nope-not-here")
    assert r["ok"] is False and r["found"] is False


async def test_verify_value_match(ctl):
    # type into the query box, then verify its value
    await ctl.type_text(selector="#q", text="hello")
    r = await ctl.verify(selector="#q", value="hello")
    assert r["ok"] is True and r["actual"] == "hello"


async def test_verify_tool_registered_and_envelope(_srv_ctl):
    assert "verify" in srv.TOOL_NAMES
    await srv._open(url=FIXTURE)
    r = await srv._verify(text="Roam Test Page")
    assert r["ok"] is True and r["data"]["present"] is True
