async def test_extract_single_object_text(ctl):
    data = await ctl.extract(fields={"title": "#title"})
    assert data == {"title": "Roam Test Page"}


async def test_extract_attribute_absolute_href(ctl):
    data = await ctl.extract(fields={"link": {"selector": "#lnk", "attr": "href"}})
    assert data["link"].endswith("#section2")
    assert data["link"].startswith("file:")


async def test_extract_list_over_table_rows(ctl):
    rows = await ctl.extract(
        fields={"k": "td:nth-child(1)", "v": "td:nth-child(2)"},
        item_selector="#tbl tbody tr",
    )
    assert rows == [{"k": "one", "v": "1"}, {"k": "two", "v": "2"}]


async def test_extract_all_returns_list(ctl):
    data = await ctl.extract(fields={"cells": {"selector": "#tbl td", "all": True}})
    assert data["cells"] == ["one", "1", "two", "2"]


async def test_pdf_writes_a_pdf_file(ctl, tmp_path):
    out = str(tmp_path / "p.pdf")
    r = await ctl.pdf(path=out)
    assert r["pdf"] == out
    with open(out, "rb") as f:
        head = f.read(5)
    assert head == b"%PDF-"


async def test_upload_sets_file_input(ctl, tmp_path):
    f = tmp_path / "up.txt"
    f.write_text("hello")
    await ctl.upload(str(f), selector="#file")
    page = await ctl.current_page()
    assert await page.evaluate("() => document.getElementById('file').files.length") == 1


async def test_download_saves_file(ctl, tmp_path):
    out = str(tmp_path / "got.txt")
    r = await ctl.download(selector="#dl", path=out)
    assert r["downloaded"] == out
    with open(out, "r", encoding="utf-8") as fh:
        assert "roam-download-body" in fh.read()
