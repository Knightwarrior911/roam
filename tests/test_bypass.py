import pathlib
from roam.bypass import PaywallBypass, GOOGLEBOT, BINGBOT
from roam.config import Config
from roam.browser import BrowserController

FIXTURE = (pathlib.Path(__file__).parent / "fixtures" / "page.html").resolve().as_uri()


def test_default_rule_is_googlebot_with_blocks():
    r = PaywallBypass().rule_for("https://example.com/article")
    assert r["ua"] == GOOGLEBOT
    assert any("poool" in p for p in r["block"])
    assert r["clear_cookies"] is False     # preserve logins by default


def test_reads_bpc_per_site_rule(tmp_path):
    (tmp_path / "sites.js").write_text(
        'var defaultSites = {\n'
        '  "Example": {\n'
        '    domain: "example.com",\n'
        '    block_regex: /\\.paywall\\.js/,\n'
        '    useragent: "bingbot"\n'
        '  }\n};\n', encoding="utf-8")
    r = PaywallBypass(str(tmp_path)).rule_for("https://www.example.com/x")
    assert r["ua"] == BINGBOT
    assert any("paywall" in p for p in r["block"])
    assert r["clear_cookies"] is True      # no allow_cookies -> reset


def test_bypass_off_when_no_match_still_googlebot():
    # unknown domain still gets the crawler UA + default blocks
    r = PaywallBypass().rule_for("https://news.example.org/story")
    assert r["ua"] == GOOGLEBOT


async def test_bypass_applies_googlebot_ua_live(tmp_path):
    cfg = Config(headless=True, channel=None, bypass=True, profile_dir=str(tmp_path / "p"))
    c = BrowserController(cfg)
    try:
        await c.open(FIXTURE)   # open -> goto applies bypass before navigation
        page = await c.current_page()
        ua = await page.evaluate("navigator.userAgent")
        assert "Googlebot" in ua                       # UA override took effect
        assert await page.title() == "Roam Fixture"    # navigation still works
    finally:
        await c.close()


async def test_set_bypass_toggle(tmp_path):
    c = BrowserController(Config(headless=True, channel=None, profile_dir=str(tmp_path / "p")))
    assert c.bypass_on is False
    assert c.set_bypass(True)["bypass"] is True
    assert c._bypass is not None
    await c.close()
