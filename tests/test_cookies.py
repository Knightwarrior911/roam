from roam.config import Config
from roam.browser import BrowserController
from tests.conftest import FIXTURE


async def test_cookies_get_and_clear(tmp_path):
    c = BrowserController(Config(headless=True, channel=None, profile_dir=str(tmp_path / "p")))
    try:
        await c.open(FIXTURE)
        await c._ctx.add_cookies([{"name": "sid", "value": "1", "url": "https://roam.test/"}])
        got = await c.cookies("get")
        assert any(k["name"] == "sid" for k in got["cookies"])
        # domain filter
        only = await c.cookies("get", domain="roam.test")
        assert all("roam.test" in (k.get("domain") or "") for k in only["cookies"])
        # clear
        assert (await c.cookies("clear"))["cleared"] is True
        assert all(k["name"] != "sid" for k in (await c.cookies("get"))["cookies"])
    finally:
        await c.close()
