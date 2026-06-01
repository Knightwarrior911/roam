import json
import os
from dataclasses import dataclass, field
from pathlib import Path


def _home() -> Path:
    base = os.environ.get("LOCALAPPDATA") or os.path.join(str(Path.home()), ".local", "share")
    return Path(base) / "Roam"


@dataclass
class Config:
    headless: bool = False
    channel: str | None = "chrome"
    profile_dir: str = ""
    default_timeout_ms: int = 15000
    viewport: dict = field(default_factory=lambda: {"width": 1280, "height": 800})
    mode: str = "logged-in"   # "logged-in" (Playwright) | "stealth" (patchright)
    executable_path: str | None = None  # stealth-Chromium binary (e.g. CloakBrowser); overrides channel
    extensions: list = field(default_factory=list)  # unpacked extension dirs to load (headed only)
    bypass: bool = False              # native paywall bypass (BPC engine)
    bypass_rules_dir: str | None = None  # path to Bypass Paywalls Clean source (for per-site rules)
    bypass_clear_cookies: bool = True    # clear cookies on known paywalled sites (BPC default; resets meters)


def load_config() -> Config:
    home = _home()
    cfg = Config(profile_dir=str(home / "profile"))
    f = home / "config.json"
    if f.exists():
        data = json.loads(f.read_text(encoding="utf-8"))
        for k in ("headless", "channel", "profile_dir", "default_timeout_ms",
                  "viewport", "mode", "executable_path", "extensions",
                  "bypass", "bypass_rules_dir", "bypass_clear_cookies"):
            if k in data:
                setattr(cfg, k, data[k])
    return cfg
