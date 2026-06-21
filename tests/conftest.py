import os
import pathlib
import pytest
import pytest_asyncio
from roam.config import Config
from roam.browser import BrowserController

FIXTURE = (pathlib.Path(__file__).parent / "fixtures" / "page.html").resolve().as_uri()

# Never let the server auto-attach to the user's REAL browser during tests — that would
# route server-level tool calls to the live extension instead of the headless fixture.
os.environ["ROAM_DISABLE_BRIDGE_AUTOSTART"] = "1"


@pytest.fixture(autouse=True)
def _isolate_bridge_autostart():
    import roam.server as srv
    srv._autostart_done = False
    yield


@pytest_asyncio.fixture
async def ctl(tmp_path):
    cfg = Config(headless=True, channel=None, profile_dir=str(tmp_path / "profile"))
    c = BrowserController(cfg)
    await c.open(FIXTURE)
    yield c
    await c.close()
