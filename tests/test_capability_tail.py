"""P5 tail: pdf_text, storage, tool annotations."""
import pathlib
import pytest
import roam.server as srv


async def test_storage_set_get_clear(ctl):
    await ctl.storage(action="set", which="local", key="roam_k", value="roam_v")
    r = await ctl.storage(action="get", which="local", key="roam_k")
    assert r["value"] == "roam_v"
    dump = await ctl.storage(action="get", which="local")
    assert dump["local"].get("roam_k") == "roam_v"
    await ctl.storage(action="clear", which="local")
    r2 = await ctl.storage(action="get", which="local", key="roam_k")
    assert r2["value"] in (None, "")


async def test_pdf_text_extracts(ctl, tmp_path):
    # build a tiny text PDF with pypdf + reportlab if available; else skip
    pdf = tmp_path / "doc.pdf"
    try:
        from reportlab.pdfgen import canvas
        c = canvas.Canvas(str(pdf))
        c.drawString(72, 720, "Roam PDF extraction works")
        c.save()
    except Exception:
        pytest.skip("reportlab not available to build a test PDF")
    r = await ctl.pdf_text(url=pdf.as_uri())
    assert r["pages"] >= 1
    assert "Roam PDF extraction" in r["text"]


def test_capability_tools_registered():
    assert {"pdf_text", "storage"} <= set(srv.TOOL_NAMES)


def test_readonly_annotations_present():
    # the annotation helper marks reads read-only and writes not
    assert srv._annotations("read_markdown")["readOnlyHint"] is True
    assert srv._annotations("click")["readOnlyHint"] is False
    assert srv._annotations("close_tab")["destructiveHint"] is True
