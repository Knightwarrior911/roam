import pathlib
import pytest_asyncio
from roam.config import Config
from roam.browser import BrowserController

FIXTURE = (pathlib.Path(__file__).parent / "fixtures" / "page.html").resolve().as_uri()


@pytest_asyncio.fixture
async def ctl(tmp_path):
    cfg = Config(headless=True, channel=None, profile_dir=str(tmp_path / "profile"))
    c = BrowserController(cfg)
    await c.open(FIXTURE)
    yield c
    await c.close()
