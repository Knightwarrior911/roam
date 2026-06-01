import pathlib
from roam.bypass import PaywallBypass, GOOGLEBOT, BINGBOT, GOOGLEBOT_IP
from roam.config import Config
from roam.browser import BrowserController

FIXTURES = pathlib.Path(__file__).parent / "fixtures"
PAYWALL = (FIXTURES / "paywall.html").resolve().as_uri()


def _bpc(tmp_path, body):
    (tmp_path / "sites.js").write_text("var defaultSites = {\n" + body + "\n};\n", encoding="utf-8")
    return PaywallBypass(str(tmp_path))


def test_unknown_site_is_left_alone():
    # no rules at all
    assert PaywallBypass().rule_for("https://example.com/x") is None


def test_unknown_site_with_rules_loaded_is_none(tmp_path):
    b = _bpc(tmp_path, '  "Foo": { domain: "foo.com", allow_cookies: 1 }')
    assert b.rule_for("https://bar.com/x") is None


def test_googlebot_site_gets_full_triad(tmp_path):
    b = _bpc(tmp_path, '  "Ex": { domain: "example.com", useragent: "googlebot" }')
    r = b.rule_for("https://www.example.com/article")
    assert r["ua"] == GOOGLEBOT
    assert r["headers"]["Referer"] == "https://www.google.com/"
    assert r["headers"]["X-Forwarded-For"] == GOOGLEBOT_IP
    assert r["clear_cookies"] is True          # no allow_cookies -> reset meter


def test_allow_cookies_block_drop_lclstrg(tmp_path):
    b = _bpc(tmp_path,
             '  "Ex": { domain: "example.com", allow_cookies: 1, useragent: "bingbot",\n'
             '    block_regex: /\\.paywall\\.js/, cs_clear_lclstrg: 1,\n'
             '    remove_cookies_select_drop: ["sess","meter"] }')
    r = b.rule_for("https://example.com/x")
    assert r["ua"] == BINGBOT
    assert "Referer" not in r["headers"]       # only googlebot gets the referer/IP
    assert r["clear_cookies"] is False
    assert any("paywall" in p for p in r["block"])
    assert r["clear_lclstrg"] is True
    assert r["drop_cookies"] == ["sess", "meter"]


def test_set_bypass_toggle(tmp_path):
    c = BrowserController(Config(headless=True, channel=None, profile_dir=str(tmp_path / "p")))
    assert c.bypass_on is False
    assert c.set_bypass(True)["bypass"] is True
    assert c._bypass is not None


async def test_cleanup_removes_overlay_and_unblurs(tmp_path):
    c = BrowserController(Config(headless=True, channel=None, profile_dir=str(tmp_path / "p")))
    try:
        await c.open(PAYWALL)
        page = await c.current_page()
        assert await page.locator("#wall").count() == 1     # overlay present before
        c._bypass_rule = {"clear_lclstrg": False}
        await c._run_cleanup(page)
        assert await page.locator("#wall").count() == 0     # overlay removed
        overflow = await page.evaluate("getComputedStyle(document.body).overflow")
        assert overflow != "hidden"                          # scroll unlocked
        article_filter = await page.evaluate(
            "getComputedStyle(document.getElementById('art')).filter")
        assert "blur" not in article_filter                  # unblurred
    finally:
        await c.close()
