import pytest
from roam.errors import RoamError


async def test_snapshot_lists_interactive_refs(ctl):
    out = await ctl.snapshot()
    assert "[ref=" in out
    # the textbox, button, link and select must appear
    assert "Query" in out or "textbox" in out
    assert "Search" in out
    assert "Jump" in out


async def test_resolve_known_ref(ctl):
    await ctl.snapshot()
    # first ref e1 must resolve to a locator that exists
    loc = await ctl._resolve("e1")
    assert await loc.count() == 1


async def test_snapshot_marks_offscreen_elements(ctl):
    # browser-use-style viewport awareness: the fixture's bottom div sits 1500px down
    out = await ctl.snapshot(interactive_only=False)
    assert "(below)" in out


async def test_stale_ref_raises(ctl):
    await ctl.snapshot()
    with pytest.raises(RoamError) as ei:
        await ctl._resolve("e999")
    assert ei.value.code == "REF_STALE"
