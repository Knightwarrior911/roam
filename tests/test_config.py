import json
from roam.config import Config, load_config


def test_defaults_when_no_file(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    cfg = load_config()
    assert cfg.headless is False
    assert cfg.channel == "auto"   # resolved to chrome/msedge/chromium at launch
    assert cfg.default_timeout_ms == 15000
    assert cfg.viewport == {"width": 1280, "height": 800}
    assert cfg.profile_dir.endswith("profile")


def test_file_overrides(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    d = tmp_path / "Roam"
    d.mkdir(parents=True)
    (d / "config.json").write_text(json.dumps({"headless": True, "channel": None}))
    cfg = load_config()
    assert cfg.headless is True
    assert cfg.channel is None


def test_mode_and_executable_path_load(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    d = tmp_path / "Roam"
    d.mkdir(parents=True)
    (d / "config.json").write_text(json.dumps(
        {"mode": "stealth", "executable_path": "C:/cloak/chrome.exe"}))
    cfg = load_config()
    assert cfg.mode == "stealth"
    assert cfg.executable_path == "C:/cloak/chrome.exe"


def test_defaults_have_no_executable_and_logged_in(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    cfg = load_config()
    assert cfg.mode == "logged-in"
    assert cfg.executable_path is None
