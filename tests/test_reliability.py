"""P1.3/1.5/1.7: wait_for_ref states, dialog auto-handling, click timeout error."""
import pytest
import roam.server as srv


async def test_wait_for_ref_visible(ctl):
    r = await ctl.wait_for_ref(selector="#title", state="visible", timeout=3000)
    assert r["ok"] is True


async def test_wait_for_ref_enabled(ctl):
    r = await ctl.wait_for_ref(selector="#go", state="enabled", timeout=3000)
    assert r["ok"] is True


async def test_wait_for_ref_hidden_for_missing(ctl):
    r = await ctl.wait_for_ref(selector="#does-not-exist", state="hidden", timeout=1000)
    assert r["ok"] is True   # absent == hidden


async def test_wait_for_ref_timeout_on_missing_visible(ctl):
    r = await ctl.wait_for_ref(selector="#nope", state="visible", timeout=600)
    assert r["ok"] is False


async def test_click_timeout_surfaces_not_actionable(ctl):
    # clicking a selector that never resolves to an actionable element times out cleanly
    from roam.errors import RoamError
    # an element that exists but we give an absurdly short timeout on a detached selector
    with pytest.raises(RoamError):
        await ctl.click(selector="#definitely-not-present-xyz", timeout=300)


async def test_dialog_auto_handled(ctl):
    # trigger an alert via eval; the page-level handler must auto-accept (not hang)
    await ctl.eval_js("setTimeout(() => alert('hi from roam'), 0); 'queued'")
    await (await ctl.current_page()).wait_for_timeout(300)
    d = await ctl.last_dialog()
    assert d is not None and d["type"] == "alert" and "hi from roam" in d["message"]


def test_reliability_tools_registered():
    assert {"wait_for_ref", "last_dialog"} <= set(srv.TOOL_NAMES)
