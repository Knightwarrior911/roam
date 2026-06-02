from roam.cue import CUE_JS


async def test_cue_shows_host(ctl):
    page = await ctl.page()
    res = await page.evaluate(CUE_JS, {"on": True, "label": "Roam controlling", "color": "#6c5ce7"})
    assert res["shown"] is True
    host = await page.evaluate("() => !!document.getElementById('__roam_cue_host__')")
    assert host


async def test_cue_does_not_pollute_page_text(ctl):
    # The badge label must live in a shadow root under <html>, never in body text —
    # otherwise read()/read_markdown/snapshot would pick it up. This is the key
    # improvement over a naive in-body banner.
    page = await ctl.page()
    await page.evaluate(CUE_JS, {"on": True, "label": "Roam controlling", "color": "#6c5ce7"})
    body_text = await page.evaluate("() => document.body.innerText")
    assert "Roam controlling" not in body_text


async def test_cue_not_in_snapshot(ctl):
    page = await ctl.page()
    await page.evaluate(CUE_JS, {"on": True, "label": "Roam controlling", "color": "#6c5ce7"})
    out = await ctl.snapshot(interactive_only=False)
    assert "Roam controlling" not in out


async def test_cue_does_not_block_clicks(ctl):
    # pointer-events:none so the cue never intercepts user OR automation clicks.
    page = await ctl.page()
    await page.evaluate(CUE_JS, {"on": True, "label": "x", "color": "#000"})
    pe = await page.evaluate(
        "() => getComputedStyle(document.getElementById('__roam_cue_host__')).pointerEvents")
    assert pe == "none"


async def test_cue_idempotent(ctl):
    page = await ctl.page()
    await page.evaluate(CUE_JS, {"on": True, "label": "a", "color": "#000"})
    await page.evaluate(CUE_JS, {"on": True, "label": "b", "color": "#111"})
    count = await page.evaluate("() => document.querySelectorAll('#__roam_cue_host__').length")
    assert count == 1


async def test_cue_removed_on_off(ctl):
    page = await ctl.page()
    await page.evaluate(CUE_JS, {"on": True, "label": "x", "color": "#000"})
    res = await page.evaluate(CUE_JS, {"on": False})
    assert res["shown"] is False
    host = await page.evaluate("() => !!document.getElementById('__roam_cue_host__')")
    assert not host


async def test_controller_set_controlled(ctl):
    await ctl.set_controlled(True, label="Roam controlling")
    page = await ctl.page()
    assert await page.evaluate("() => !!document.getElementById('__roam_cue_host__')")
    await ctl.set_controlled(False)
    assert not await page.evaluate("() => !!document.getElementById('__roam_cue_host__')")
