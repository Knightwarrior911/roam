import json
import os
from dataclasses import dataclass, field
from pathlib import Path


def _home() -> Path:
    base = os.environ.get("LOCALAPPDATA") or os.path.join(str(Path.home()), ".local", "share")
    return Path(base) / "Roam"


def detect_default_browser() -> str:
    """Pick a sensible managed-browser channel for THIS machine instead of hardcoding
    'chrome'. Prefer Chrome when present, else Edge (the Windows default), else fall back
    to Playwright's bundled Chromium. Keeps Edge-primary boxes from hitting a
    CHROME_LAUNCH_FAILED cliff on first run."""
    pf = os.environ.get("PROGRAMFILES", "")
    pf86 = os.environ.get("PROGRAMFILES(X86)", "")
    local = os.environ.get("LOCALAPPDATA", "")
    chrome = [
        os.path.join(pf, "Google", "Chrome", "Application", "chrome.exe"),
        os.path.join(pf86, "Google", "Chrome", "Application", "chrome.exe"),
        os.path.join(local, "Google", "Chrome", "Application", "chrome.exe"),
    ]
    edge = [
        os.path.join(pf, "Microsoft", "Edge", "Application", "msedge.exe"),
        os.path.join(pf86, "Microsoft", "Edge", "Application", "msedge.exe"),
    ]
    if any(c and os.path.exists(c) for c in chrome):
        return "chrome"
    if any(e and os.path.exists(e) for e in edge):
        return "msedge"
    return "chromium"


@dataclass
class Config:
    headless: bool = False
    # "auto" = pick at launch (chrome -> msedge -> chromium). "chrome"/"msedge"/"chromium"
    # pin it. None = Playwright bundled chromium (tests use this).
    channel: str | None = "auto"
    mode_default: str = "auto"   # session backend when unset: "auto" | "bridge" | "managed"
    bridge_auto: bool = True     # auto-start the bridge listener at server boot
    profile_dir: str = ""
    default_timeout_ms: int = 15000
    viewport: dict = field(default_factory=lambda: {"width": 1280, "height": 800})
    mode: str = "logged-in"   # "logged-in" (Playwright) | "stealth" (patchright)
    executable_path: str | None = None  # stealth-Chromium binary (e.g. CloakBrowser); overrides channel
    extensions: list = field(default_factory=list)  # unpacked extension dirs to load (headed only)
    stealth_harden: bool = False      # inject anti-automation evasions into the launched browser
    humanize: bool = False            # human-like mouse paths / keystroke cadence / scroll (slower)
    canvas_noise: bool = False        # native Chromium per-session canvas noise (flag, not a JS hook)
    block_webrtc: bool = False        # disable non-proxied-UDP WebRTC (stops local/public IP leak)
    bypass: bool = False              # native paywall bypass (BPC engine)
    bypass_rules_dir: str | None = None  # path to Bypass Paywalls Clean source (for per-site rules)
    bypass_clear_cookies: bool = True    # clear cookies on known paywalled sites (BPC default; resets meters)


def load_config() -> Config:
    home = _home()
    cfg = Config(profile_dir=str(home / "profile"))
    f = home / "config.json"
    if f.exists():
        data = json.loads(f.read_text(encoding="utf-8"))
        for k in ("headless", "channel", "mode_default", "bridge_auto",
                  "profile_dir", "default_timeout_ms",
                  "viewport", "mode", "executable_path", "extensions", "stealth_harden",
                  "humanize", "canvas_noise", "block_webrtc",
                  "bypass", "bypass_rules_dir", "bypass_clear_cookies"):
            if k in data:
                setattr(cfg, k, data[k])
    return cfg
