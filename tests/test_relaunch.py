"""Regression: a closed/crashed browser must transparently relaunch.

Before the fix, once the managed window was closed the stale context lingered and
every tool failed forever with NO_BROWSER until the server restarted."""

import pytest


@pytest.mark.asyncio
async def test_relaunch_after_context_close(ctl):
    # baseline: headless browser is up with a live page
    assert await ctl.current_page() is not None

    # simulate the user closing the browser window (or a crash)
    await ctl._ctx.close()

    # the next open() must detect the dead context and relaunch, not raise NO_BROWSER
    await ctl.open("about:blank")
    assert ctl._ctx is not None
    page = await ctl.current_page()
    assert page is not None


@pytest.mark.asyncio
async def test_ctx_alive_false_after_close(ctl):
    assert ctl._ctx_alive() is True
    await ctl._ctx.close()
    # either the close event fired (ctx None) or the liveness probe reports dead
    assert ctl._ctx is None or ctl._ctx_alive() is False
