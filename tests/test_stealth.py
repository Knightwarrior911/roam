import json
import pathlib
from roam.config import Config, load_config
from roam.browser import BrowserController

FIXTURE = (pathlib.Path(__file__).parent / "fixtures" / "page.html").resolve().as_uri()


def test_mode_default_and_override(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    assert load_config().mode == "logged-in"
    d = tmp_path / "Roam"
    d.mkdir(parents=True)
    (d / "config.json").write_text(json.dumps({"mode": "stealth"}))
    assert load_config().mode == "stealth"


def test_stealth_uses_separate_profile(tmp_path):
    c = BrowserController(Config(mode="stealth", profile_dir=str(tmp_path / "profile")))
    assert c._profile_dir().endswith("_stealth")
    c2 = BrowserController(Config(mode="logged-in", profile_dir=str(tmp_path / "profile")))
    assert not c2._profile_dir().endswith("_stealth")


async def test_audit_hardened_hides_webdriver(tmp_path):
    cfg = Config(headless=True, channel=None, stealth_harden=True, profile_dir=str(tmp_path / "p"))
    c = BrowserController(cfg)
    try:
        await c.open(FIXTURE)
        a = await c.stealth_audit()
        assert a["checks"]["webdriver_hidden"] is True
        assert a["checks"]["has_chrome"] is True
        assert a["verdict"] in ("clean", "ok")
    finally:
        await c.close()


async def test_audit_unhardened_leaks_webdriver(tmp_path):
    cfg = Config(headless=True, channel=None, stealth_harden=False, profile_dir=str(tmp_path / "p"))
    c = BrowserController(cfg)
    try:
        await c.open(FIXTURE)
        a = await c.stealth_audit()
        assert a["checks"]["webdriver_hidden"] is False   # vanilla automation leaks it
    finally:
        await c.close()


async def test_audit_reports_fingerprint_and_cdp_probes(tmp_path):
    cfg = Config(headless=True, channel=None, stealth_harden=True, profile_dir=str(tmp_path / "p"))
    c = BrowserController(cfg)
    try:
        await c.open(FIXTURE)
        a = await c.stealth_audit()
        # new raw probes are present (rebrowser-derived)
        for k in ("hardware_concurrency", "device_memory", "runtime_enable_leak",
                  "source_url_leak", "pw_init_scripts", "navigator_own_props"):
            assert k in a["raw"], k
        # split verdicts: fingerprint (core) + driver/CDP
        assert a["verdict"] in ("clean", "ok")
        assert a["cdp_verdict"] in ("clean", "ok", "leaky")
        # hardening leaves NO own property on navigator (flag, not a detectable JS override)
        assert a["checks"]["no_navigator_own_props"] is True
        # hw/device spoofed to our consistent value, and the spoof getter reads as native
        assert a["raw"]["hardware_concurrency"] == 8
        assert a["raw"]["device_memory"] == 8
        assert a["checks"]["spoof_tostring_native"] is True
    finally:
        await c.close()


async def test_hardening_does_not_use_detectable_webdriver_override(tmp_path):
    # the improvement over puppeteer-stealth: webdriver must read `false` (native), never
    # `undefined`, and navigator must carry no own 'webdriver' property.
    cfg = Config(headless=True, channel=None, stealth_harden=True, profile_dir=str(tmp_path / "p"))
    c = BrowserController(cfg)
    try:
        await c.open(FIXTURE)
        a = await c.stealth_audit()
        assert a["raw"]["webdriver"] is False              # not the string "undefined"
        assert "webdriver" not in a["raw"]["navigator_own_props"]
    finally:
        await c.close()


def test_grease_brands_has_chrome_chromium_notabrand():
    from roam.stealth import grease_brands
    b = grease_brands("120")
    brands = {x["brand"] for x in b}
    assert "Google Chrome" in brands and "Chromium" in brands
    assert any("Not" in x and "Brand" in x for x in brands)
    assert len(b) == 3
    assert all(x["version"] == "120" for x in b if x["brand"] in ("Google Chrome", "Chromium"))


def test_grease_brands_deterministic():
    from roam.stealth import grease_brands
    assert grease_brands("120") == grease_brands("120")


def test_should_apply_uach_only_on_bundled_chromium_harden():
    from roam.stealth import should_apply_uach
    assert should_apply_uach(Config(channel=None, stealth_harden=True)) is True
    assert should_apply_uach(Config(channel="chrome", stealth_harden=True)) is False   # real chrome ok
    assert should_apply_uach(Config(channel=None, mode="stealth", stealth_harden=True)) is False  # patchright
    assert should_apply_uach(Config(channel=None, stealth_harden=False)) is False


async def test_uach_override_adds_google_chrome_brand(tmp_path):
    # on bundled chromium the brand list lacks "Google Chrome"; after the override it's present
    cfg = Config(headless=True, channel=None, stealth_harden=True, profile_dir=str(tmp_path / "p"))
    c = BrowserController(cfg)
    try:
        await c.open(FIXTURE)
        page = await c.current_page()
        brands = await page.evaluate(
            "() => (navigator.userAgentData ? navigator.userAgentData.brands.map(b=>b.brand) : [])")
        # userAgentData may be absent in some bundled builds; only assert when present
        if brands:
            assert "Google Chrome" in brands
    finally:
        await c.close()


def test_build_stealth_args_base_has_automation_flag():
    from roam.stealth import build_stealth_args
    a = build_stealth_args(Config())
    assert "--disable-blink-features=AutomationControlled" in a


def test_build_stealth_args_canvas_noise_is_gated():
    from roam.stealth import build_stealth_args
    assert "--fingerprinting-canvas-image-data-noise" not in build_stealth_args(Config())
    assert "--fingerprinting-canvas-image-data-noise" in build_stealth_args(Config(canvas_noise=True))


def test_build_stealth_args_webrtc_is_gated():
    from roam.stealth import build_stealth_args
    assert not any("webrtc" in x for x in build_stealth_args(Config()))
    assert any("webrtc-ip-handling" in x for x in build_stealth_args(Config(block_webrtc=True)))


async def test_stealth_backend_drives_full_surface(tmp_path):
    # the patchright backend must drive the entire tool surface unchanged
    cfg = Config(headless=True, channel=None, mode="stealth",
                 profile_dir=str(tmp_path / "profile"))
    c = BrowserController(cfg)
    try:
        await c.open(FIXTURE)
        out = await c.snapshot()
        assert "[ref=" in out and "Search" in out
        await c.type_text(element="query", selector="#q", text="zz", submit=False)
        page = await c.current_page()
        assert await page.input_value("#q") == "zz"
    finally:
        await c.close()
