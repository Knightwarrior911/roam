import json
from roam.config import Config, load_config
from roam.browser import BrowserController


def test_ext_args_empty(tmp_path):
    c = BrowserController(Config(profile_dir=str(tmp_path / "p")))
    assert c._ext_args() == []


def test_ext_args_builds_flags(tmp_path):
    ext = str(tmp_path / "ext")
    c = BrowserController(Config(profile_dir=str(tmp_path / "p"), extensions=[ext]))
    args = c._ext_args()
    assert any(a.startswith("--load-extension=") and ext in a for a in args)
    assert any(a.startswith("--disable-extensions-except=") and ext in a for a in args)


def test_extensions_load_from_config(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    d = tmp_path / "Roam"
    d.mkdir(parents=True)
    (d / "config.json").write_text(json.dumps({"extensions": ["C:/ext/one", "C:/ext/two"]}))
    assert load_config().extensions == ["C:/ext/one", "C:/ext/two"]
