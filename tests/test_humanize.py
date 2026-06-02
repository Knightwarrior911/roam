import math
import random
import pytest_asyncio
from roam.humanize import bezier_path
from roam.config import Config
from roam.browser import BrowserController
from tests.conftest import FIXTURE


# ---- pure Bezier-path algorithm (ghost-cursor) ----
def test_bezier_endpoints_are_exact():
    pts = bezier_path((0, 0), (100, 50), rng=random.Random(1))
    assert pts[0] == (0.0, 0.0)
    assert abs(pts[-1][0] - 100) < 1e-6 and abs(pts[-1][1] - 50) < 1e-6
    assert len(pts) >= 2
    assert all(math.isfinite(x) and math.isfinite(y) for x, y in pts)


def test_bezier_more_steps_for_longer_distance():
    short = bezier_path((0, 0), (15, 0), rng=random.Random(0))
    longp = bezier_path((0, 0), (1500, 0), rng=random.Random(0))
    assert len(longp) >= len(short)


def test_bezier_curves_off_the_straight_line():
    # control points must introduce curvature, not a robotic straight line
    pts = bezier_path((0, 0), (200, 0), rng=random.Random(3))
    assert max(abs(y) for _, y in pts) > 0.5


# ---- integration: humanized actions still produce correct results ----
@pytest_asyncio.fixture
async def hctl(tmp_path):
    c = BrowserController(Config(headless=True, channel=None, humanize=True,
                                 profile_dir=str(tmp_path / "p")))
    await c.open(FIXTURE)
    yield c
    await c.close()


async def test_human_type_enters_exact_text(hctl):
    # even with simulated typos+corrections, the final field value must be exact
    await hctl.type_text(selector="#q", text="hello world")
    page = await hctl.current_page()
    assert await page.input_value("#q") == "hello world"


async def test_human_click_triggers_real_click(hctl):
    await hctl.type_text(selector="#q", text="hi")
    await hctl.click(selector="#go")     # submit button -> onsubmit writes #out
    page = await hctl.current_page()
    assert await page.text_content("#out") == "submitted:hi"


async def test_human_click_offscreen_element(hctl):
    # #lnk (an in-page anchor to #section2 which sits 1500px down) — the link itself is at
    # the top, so click it and assert navigation to the anchor. Then verify a humanized click
    # on a below-the-fold element resolves via scroll-into-view rather than missing.
    page = await hctl.current_page()
    # move the link far down to force off-screen, then humanized-click it
    await page.evaluate("() => { const a=document.getElementById('lnk'); a.style.marginTop='2000px'; }")
    await hctl.click(selector="#lnk")
    # the anchor click navigates the hash to #section2
    assert page.url.endswith("#section2")


async def test_human_scroll_moves_viewport(hctl):
    await hctl.scroll(direction="down")
    page = await hctl.current_page()
    assert await page.evaluate("() => window.scrollY") > 0
