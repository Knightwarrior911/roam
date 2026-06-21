"""P3.1/3.2: observe (plan) + act (fused do)."""
import roam.server as srv


async def test_observe_ranks_relevant_element(ctl):
    obs = await ctl.observe("click the Search button")
    assert obs["candidates"]
    top = obs["candidates"][0]
    assert "search" in (top["name"] or "").lower()
    assert top["method"] == "click"


async def test_observe_infers_type_for_textbox(ctl):
    obs = await ctl.observe("type into the Query box")
    assert obs["method"] == "type"


async def test_act_clicks_inferred_element(ctl):
    # the fixture's Search button submits the form, writing into #out
    await ctl.type_text(selector="#q", text="abc")
    r = await ctl.act("click Search")
    assert r["acted"] == "click"
    out = await ctl.read(selector="#out")
    assert "submitted:abc" in out


async def test_act_types_with_variables(ctl):
    # %query% placeholder substituted locally
    r = await ctl.act("type into the Query box", text="%query%", variables={"query": "hello"})
    assert r["acted"] == "type"
    v = await ctl.verify(selector="#q", value="hello")
    assert v["ok"] is True


def test_observe_act_registered():
    assert {"observe", "act"} <= set(srv.TOOL_NAMES)
