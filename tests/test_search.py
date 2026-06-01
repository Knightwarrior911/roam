from roam.search import build_search_url, is_engine_link


def test_build_search_url_with_operators():
    u = build_search_url("revenue guidance", site="apple.com", filetype="pdf")
    assert u.startswith("https://duckduckgo.com/html/?q=")
    assert "site%3Aapple.com" in u
    assert "filetype%3Apdf" in u


def test_build_search_url_intitle_and_engine():
    u = build_search_url("10-K", intitle="annual report", engine="bing")
    assert u.startswith("https://www.bing.com/search?q=")
    assert "intitle%3Aannual" in u


def test_is_engine_link():
    assert is_engine_link("https://duckduckgo.com/l/?uddg=x", "duckduckgo.com")
    assert is_engine_link("", "duckduckgo.com")
    assert not is_engine_link("https://www.apple.com/investor/", "duckduckgo.com")
