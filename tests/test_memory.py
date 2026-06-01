from roam.memory import SelectorMemory, format_manual


def _mem(tmp_path):
    return SelectorMemory(str(tmp_path / "mem.db"))


def test_record_and_recall_by_url(tmp_path):
    m = _mem(tmp_path)
    m.record("https://airbnb.com/rooms", "textbox", "Search", "#search", ts=1)
    rows = m.recall(url="https://airbnb.com/rooms")
    assert len(rows) == 1
    assert rows[0]["selector"] == "#search" and rows[0]["hits"] == 1
    assert rows[0]["domain"] == "airbnb.com" and rows[0]["path"] == "/rooms"


def test_record_twice_increments_hits_and_updates_selector(tmp_path):
    m = _mem(tmp_path)
    m.record("https://x.com/", "button", "Go", "button.old", ts=1)
    m.record("https://x.com/", "button", "Go", "button.new", ts=2)
    rows = m.recall(domain="x.com")
    assert len(rows) == 1
    assert rows[0]["hits"] == 2 and rows[0]["selector"] == "button.new"


def test_recall_by_domain_spans_paths(tmp_path):
    m = _mem(tmp_path)
    m.record("https://x.com/a", "button", "A", "#a", ts=1)
    m.record("https://x.com/b", "link", "B", "#b", ts=1)
    assert len(m.recall(domain="x.com")) == 2
    assert len(m.recall(url="https://x.com/a")) == 1


def test_forget_removes_domain(tmp_path):
    m = _mem(tmp_path)
    m.record("https://x.com/", "button", "A", "#a", ts=1)
    m.record("https://y.com/", "button", "B", "#b", ts=1)
    assert m.forget("x.com") == 1
    assert m.recall(domain="x.com") == []
    assert len(m.recall(domain="y.com")) == 1


def test_empty_selector_is_ignored(tmp_path):
    m = _mem(tmp_path)
    m.record("https://x.com/", "button", "A", "", ts=1)
    assert m.recall(domain="x.com") == []


def test_format_manual(tmp_path):
    m = _mem(tmp_path)
    m.record("https://x.com/p", "textbox", "Email", "#email", ts=1)
    out = format_manual(m.recall(domain="x.com"))
    assert "x.com" in out and "#email" in out and "textbox" in out


# ---- action manuals ----
def test_save_and_get_manual(tmp_path):
    m = _mem(tmp_path)
    steps = [{"action": "goto", "url": "x"}, {"action": "click", "ref": "e1"}]
    m.save_manual("https://x.com/p", "login", steps, ts=1)
    got = m.get_manual(url="https://x.com/p", name="login")
    assert got["steps"] == steps and got["hits"] == 1


def test_save_manual_increments_and_updates(tmp_path):
    m = _mem(tmp_path)
    m.save_manual("https://x.com/", "go", [{"a": 1}], ts=1)
    m.save_manual("https://x.com/", "go", [{"a": 2}], ts=2)
    g = m.get_manual(domain="x.com", name="go")
    assert g["hits"] == 2 and g["steps"] == [{"a": 2}]


def test_list_manuals_by_domain(tmp_path):
    m = _mem(tmp_path)
    m.save_manual("https://x.com/", "a", [{}], ts=1)
    m.save_manual("https://x.com/", "b", [{}], ts=1)
    assert len(m.get_manual(domain="x.com")) == 2


def test_recall_by_query_ranks_by_intent(tmp_path):
    m = _mem(tmp_path)
    m.record("https://x.com/", "button", "Submit order", "#submit", ts=1)
    m.record("https://x.com/", "textbox", "Search products", "#search", ts=1)
    r = m.recall(domain="x.com", query="search box")
    assert r and r[0]["selector"] == "#search"   # overlap on 'search' wins
    # no overlap -> falls back to all (hits order), never empty
    assert m.recall(domain="x.com", query="zzz") != []


def test_forget_manual(tmp_path):
    m = _mem(tmp_path)
    m.save_manual("https://x.com/", "a", [{}], ts=1)
    m.save_manual("https://x.com/", "b", [{}], ts=1)
    assert m.forget_manual("x.com", "a") == 1
    assert len(m.get_manual(domain="x.com")) == 1
    assert m.forget_manual("x.com") == 1
    assert m.get_manual(domain="x.com") == []
