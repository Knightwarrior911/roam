"""P3.6: auto-schema extraction + structured-data merge."""
import roam.server as srv


async def test_extract_auto_detects_product_cards(ctl):
    r = await ctl.extract_auto(item_selector="#products .card")
    assert r["count"] == 4
    assert any("widget a" in str(v).lower() for row in r["data"] for v in row.values())
    # a price-shaped field should be typed number
    assert "number" in r["schema"].values()


async def test_extract_auto_without_selector_finds_a_group(ctl):
    r = await ctl.extract_auto()
    assert r["count"] >= 3        # the product list is the largest uniform group


async def test_structured_data_merges_sources(ctl):
    d = await ctl.structured_data()
    # JSON-LD headline + author, OpenGraph title, meta description all present
    assert d.get("headline") == "LD Headline"
    assert d.get("author") == "Jane Roam"
    assert d.get("title") in ("Roam OG Title", "Roam Fixture")
    assert "extraction" in (d.get("description") or "")


def test_extract_tools_registered():
    assert {"extract_auto", "structured_data"} <= set(srv.TOOL_NAMES)
