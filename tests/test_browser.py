import pytest
from roam.errors import RoamError
from tests.conftest import FIXTURE


async def test_open_and_title(ctl):
    page = await ctl.page()
    assert "Roam Fixture" == await page.title()
    assert "Roam Test Page" == await page.inner_text("#title")


async def test_goto_and_reload(ctl):
    await ctl.goto(FIXTURE)
    page = await ctl.page()
    assert page.url.endswith("page.html")
    await ctl.reload()
    assert "Roam Fixture" == await page.title()


async def test_no_browser_before_open(tmp_path):
    from roam.config import Config
    from roam.browser import BrowserController
    c = BrowserController(Config(headless=True, channel=None, profile_dir=str(tmp_path / "p")))
    with pytest.raises(RoamError) as ei:
        await c.current_page()  # raises NO_BROWSER when nothing open
    assert ei.value.code == "NO_BROWSER"


# ---- interaction ----
async def _ref_for(ctl, role, name_contains):
    out = await ctl.snapshot()
    for line in out.splitlines():
        if role in line and name_contains in line:
            return line.split("[ref=")[1].rstrip("]")
    raise AssertionError(f"no {role} containing {name_contains!r} in:\n{out}")


async def test_type_and_submit(ctl):
    ref = await _ref_for(ctl, "textbox", "Query")
    await ctl.type_text(element="query box", ref=ref, text="hello", submit=True)
    page = await ctl.current_page()
    assert "submitted:hello" == await page.inner_text("#out")


async def test_click_link(ctl):
    ref = await _ref_for(ctl, "link", "Jump")
    await ctl.click(element="jump link", ref=ref)
    page = await ctl.current_page()
    assert page.url.endswith("#section2")


async def test_select_option(ctl):
    ref = await _ref_for(ctl, "combobox", "")
    await ctl.select(element="fruit select", ref=ref, values=["b"])
    page = await ctl.current_page()
    assert "b" == await page.eval_on_selector("#sel", "el => el.value")


async def test_click_by_coordinates(ctl):
    # vision fallback: clicking the log button by box center fires its console.log
    ref = await _ref_for(ctl, "button", "Log")
    loc = await ctl._resolve(ref)
    box = await loc.bounding_box()
    await ctl.click(x=box["x"] + box["width"] / 2, y=box["y"] + box["height"] / 2)
    page = await ctl.current_page()
    await page.wait_for_timeout(200)  # let the page's console.log event propagate
    assert any("clicked-log" in t for _, t in ctl.console_buf)


# ---- observation ----
async def test_read_page_and_element(ctl):
    whole = await ctl.read()
    assert "Roam Test Page" in whole
    cell = await ctl.read(selector="#tbl tbody tr:first-child td:first-child")
    assert cell.strip() == "one"


async def test_eval_returns_value(ctl):
    assert await ctl.eval_js("document.title") == "Roam Fixture"
    assert await ctl.eval_js("return 6 * 7") == 42


async def test_screenshot_returns_png_bytes(ctl):
    data = await ctl.screenshot()
    assert isinstance(data, bytes) and data[:8] == b"\x89PNG\r\n\x1a\n"


async def test_console_capture(ctl):
    await ctl.eval_js("console.log('hi-there')")
    logs = await ctl.console(tail=10)
    assert any("hi-there" in line for line in logs)


# ---- tabs + wait + cdp ----
async def test_tabs_lifecycle(ctl):
    first = (await ctl.tabs())[0]["id"]
    info = await ctl.new_tab(FIXTURE)
    assert info["id"] != first
    assert len(await ctl.tabs()) == 2
    await ctl.switch_tab(first)
    assert ctl.active == first
    await ctl.close_tab(info["id"])
    assert len(await ctl.tabs()) == 1


async def test_switch_bad_tab_raises(ctl):
    with pytest.raises(RoamError) as ei:
        await ctl.switch_tab("t999")
    assert ei.value.code == "TAB_NOT_FOUND"


async def test_wait_for_selector(ctl):
    await ctl.wait(for_="selector", value="#title")  # already present -> returns fast
    page = await ctl.current_page()
    assert "Roam Test Page" in await page.inner_text("#title")


async def test_cdp_escape_hatch(ctl):
    res = await ctl.cdp("Runtime.evaluate", {"expression": "2+3", "returnByValue": True})
    assert res["result"]["value"] == 5


# ---- local selector memory (v2) ----
async def test_memory_records_verified_selector_on_click(ctl):
    ref = await _ref_for(ctl, "link", "Jump")
    await ctl.click(element="jump link", ref=ref)
    r = await ctl.recall()
    sels = [row["selector"] for row in r["manual"]]
    assert "#lnk" in sels          # durable selector captured from the element id
    assert "Jump" in r["text"]     # formatted manual mentions the element


async def test_recall_empty_before_any_action(ctl):
    r = await ctl.recall()
    assert r["manual"] == []
    assert "nothing remembered" in r["text"]
