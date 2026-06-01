from roam.errors import RoamError, ok, err


def test_ok_envelope():
    assert ok({"x": 1}) == {"ok": True, "data": {"x": 1}}


def test_err_envelope():
    e = RoamError("REF_STALE", "ref e7 not found", "re-run snapshot")
    assert err(e) == {
        "ok": False,
        "error": {"code": "REF_STALE", "message": "ref e7 not found", "hint": "re-run snapshot"},
    }


def test_error_is_exception():
    try:
        raise RoamError("BAD_ARGS", "missing url")
    except RoamError as e:
        assert e.code == "BAD_ARGS"
        assert e.hint == ""
