from roam.config import Config
from roam.browser import BrowserController


async def test_record_api_captures_json_xhr(tmp_path):
    c = BrowserController(Config(headless=True, channel=None, profile_dir=str(tmp_path / "p")))
    try:
        await c.ensure()

        async def handler(route):
            url = route.request.url
            if url.rstrip("/").endswith("roam.test"):
                await route.fulfill(status=200, content_type="text/html",
                                    body="<html><body>hi</body></html>")
            else:
                await route.fulfill(status=200, content_type="application/json",
                                    body='{"results":[1,2],"ok":true}')

        await c._ctx.route("http://roam.test/**", handler)
        await c.record_api(True)
        await c.goto("http://roam.test/")
        page = await c.current_page()
        await page.evaluate("() => fetch('http://roam.test/api/search').then(r => r.json())")
        await page.wait_for_timeout(600)

        recs = c.memory.get_recipes(domain="roam.test")
        assert any(r["api_url"] == "/api/search" for r in recs)
        match = [r for r in recs if r["api_url"] == "/api/search"][0]
        assert match["method"] == "GET"
        assert "results" in match["resp_keys"] and "ok" in match["resp_keys"]
    finally:
        await c.close()


async def test_record_api_disable_stops_capture(tmp_path):
    c = BrowserController(Config(headless=True, channel=None, profile_dir=str(tmp_path / "p")))
    try:
        await c.ensure()
        assert (await c.record_api(True))["recording"] is True
        assert c._api_handler is not None
        assert (await c.record_api(False))["recording"] is False
        assert c._api_handler is None
    finally:
        await c.close()
