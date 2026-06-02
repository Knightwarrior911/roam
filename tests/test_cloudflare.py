from roam.cloudflare import detect_challenge


def test_detect_managed():
    assert detect_challenge("window._cf = { cType: 'managed' }") == "managed"


def test_detect_interactive_double_quotes():
    assert detect_challenge('something cType: "interactive" more') == "interactive"


def test_detect_non_interactive():
    assert detect_challenge("a cType: 'non-interactive' b") == "non-interactive"


def test_detect_embedded_turnstile_script():
    html = '<script src="https://challenges.cloudflare.com/turnstile/v0/api.js"></script>'
    assert detect_challenge(html) == "embedded"


def test_detect_none_on_normal_page():
    assert detect_challenge("<html><body>hello world</body></html>") is None
    assert detect_challenge("") is None
    assert detect_challenge(None) is None


async def test_solve_returns_solved_when_no_challenge(ctl):
    # a clean page (the fixture) has no Cloudflare markers -> solved instantly, no clicks
    from roam.cloudflare import solve
    page = await ctl.page()
    r = await solve(page, max_attempts=2)
    assert r["solved"] is True
    assert r["attempts"] == 0
    assert r["type"] is None
