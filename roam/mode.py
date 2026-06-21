"""Session backend mode: which browser the next tool call drives.

Three modes:
  - "auto"    : bridge (the user's real browser) if the extension is connected, else the
                managed Playwright browser. This is the historical behavior.
  - "bridge"  : ALWAYS the bridge. If it isn't connected, the call fails LOUD with
                BRIDGE_DISCONNECTED instead of silently launching a fresh Chrome — so the
                user who said "use my Edge" never gets a surprise Chromium window.
  - "managed" : ALWAYS the managed browser, even if the bridge happens to be connected.

The mode is sticky for the process (set via the set_mode MCP tool) and seeds from
Config.mode_default. This module holds only the tiny state + validation; server._ctl()
does the actual routing.
"""

VALID = ("auto", "bridge", "managed")

_mode = None   # None until first read; resolved from config then


def get(default="auto"):
    global _mode
    if _mode is None:
        _mode = default if default in VALID else "auto"
    return _mode


def set_mode(mode):
    global _mode
    if mode not in VALID:
        raise ValueError(f"mode must be one of {VALID}, got {mode!r}")
    _mode = mode
    return _mode


def reset():
    """Test hook: forget the cached mode so the next get() re-seeds from config."""
    global _mode
    _mode = None
