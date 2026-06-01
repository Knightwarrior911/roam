import json
from roam.config import Config, load_config


def test_defaults_when_no_file(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    cfg = load_config()
    assert cfg.headless is False
    assert cfg.channel == "chrome"
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
