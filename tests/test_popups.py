import pathlib
from roam.config import Config
from roam.browser import BrowserController

POPUP = (pathlib.Path(__file__).parent / "fixtures" / "popup.html").resolve().as_uri()


async def _open(tmp_path):
    c = BrowserController(Config(headless=True, channel=None, profile_dir=str(tmp_path / "p")))
    await c.open(POPUP)
    return c


async def test_dismiss_popups_clicks_and_removes(tmp_path):
    c = await _open(tmp_path)
    try:
        page = await c.current_page()
        assert await page.locator("#cookie").count() == 1
        r = await c.dismiss_popups()
        assert "Accept all" in r["clicked"]               # consent button clicked by text
        assert await page.locator("#cookie").count() == 0  # full-screen overlay removed
        overflow = await page.evaluate("getComputedStyle(document.body).overflow")
        assert overflow != "hidden"                        # scroll restored
    finally:
        await c.close()


async def test_find_links_filters_by_intent(tmp_path):
    c = await _open(tmp_path)
    try:
        links = await c.find_links(keywords=["investor", "annual", ".pdf", "press"])
        hrefs = [l["href"] for l in links]
        assert any("investor-relations" in h for h in hrefs)
        assert any(".pdf" in h for h in hrefs)
        assert any("press-release" in h for h in hrefs)
        assert not any(h.endswith("/about") for h in hrefs)   # 'About Us' not matched
        assert len(await c.find_links()) >= 4                  # no filter -> all links
    finally:
        await c.close()
