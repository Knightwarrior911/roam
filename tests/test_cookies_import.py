import pytest
from roam import cookies_import as ci


def test_unknown_browser_raises():
    with pytest.raises(KeyError):
        ci._user_data_dir("netscape")


def test_known_browsers_registered():
    assert "edge" in ci.BROWSERS and "chrome" in ci.BROWSERS


def test_decrypt_empty_is_empty():
    # v20 (app-bound) and empty values decrypt to "" and are skipped, never crash
    assert ci._decrypt(b"", b"\x00" * 32) == ""
    assert ci._decrypt(b"v20" + b"\x00" * 40, b"\x00" * 32) == ""
