"""
Research first, then write (Loop Board "Recipes people trust", route 3,
slice 2 — locked by Emily 2026-10-05).

What is pinned here, against the card's acceptance criteria:
  * the LEAD is the version with the top rating and the most ratings, chosen
    in code from the numbers, and a version with few ratings can't lead;
  * when the open search has nothing well rated, the trusted cooks for the
    dish's cuisine are searched instead (allowed_domains on both web tools);
  * a site that refused the reader is skipped, and a URL the call never saw
    is never saved — a link on the recipe page has to be a real page;
  * the research is saved per household and reused: the dish coming back
    makes no second call;
  * the writer is handed the research, and its "Changed for your household"
    lines are saved; the recipe route serves both in the shape Builder B's
    page reads (scratchpad/recipe-research-shape.md);
  * "Originally based on" once the household has changed it a lot;
  * a link found gone is hidden, a site that only blocks bots is not;
  * research that fails never costs the household the recipe.

No test reaches the network: the model is a fake client, the link checker a
stub. RECIPE_RESEARCH is off for the suite (conftest) and on here.
"""
from __future__ import annotations

import types

import pytest

from app import agent, tools
from app.tools import recipe_research as rr


@pytest.fixture(autouse=True)
def _research_on(monkeypatch):
    monkeypatch.setenv("RECIPE_RESEARCH", "on")


def _usage():
    return types.SimpleNamespace(
        input_tokens=0, cache_read_input_tokens=0, cache_creation_input_tokens=0,
        output_tokens=0, server_tool_use=None,
    )


def _ns(**kw):
    return types.SimpleNamespace(**kw)


def _search(urls, tool_id="s1"):
    return [
        _ns(type="server_tool_use", name="web_search", id=tool_id, input={"query": "x"}),
        _ns(type="web_search_tool_result", tool_use_id=tool_id,
            content=[_ns(type="web_search_result", url=u, title=u) for u in urls]),
    ]


def _fetch_refused(url, tool_id="f1"):
    return [
        _ns(type="server_tool_use", name="web_fetch", id=tool_id, input={"url": url}),
        _ns(type="web_fetch_tool_result", tool_use_id=tool_id,
            content=_ns(type="web_fetch_tool_error", error_code="url_not_accessible")),
    ]


def _submit(sources, notes="Bloom whole spices, onion-tomato base, simmer 20 min."):
    return _ns(type="tool_use", name="submit_dish_research", id="t1",
               input={"sources": sources, "method_notes": notes})


class _FakeClient:
    """Answers each messages.create with the next canned content list and
    records what it was asked."""

    def __init__(self, turns):
        self.turns = list(turns)
        self.calls = []
        self.messages = self

    def create(self, **kwargs):
        self.calls.append(kwargs)
        content, stop = self.turns.pop(0)
        return _ns(content=content, stop_reason=stop, usage=_usage(), model=kwargs.get("model"))


def _use_client(monkeypatch, client):
    monkeypatch.setattr(agent, "_client", lambda: client)
    return client


A = "https://www.example-a.com/chana-masala/"
B = "https://example-b.com/chana"
C = "https://example-c.com/chole"
GONE = "https://example-d.com/never-seen"


# ---------- the lead ----------

def test_the_lead_is_the_top_rating_and_among_equal_stars_the_most_ratings():
    sources = [
        {"name": "A", "url": A, "rating": 4.8, "rating_count": 300},
        {"name": "B", "url": B, "rating": 4.9, "rating_count": 40},
        {"name": "C", "url": C, "rating": 4.9, "rating_count": 2100},
    ]
    ordered = rr.pick_lead(sources)
    assert [s["name"] for s in ordered] == ["C", "A", "B"]
    assert [s["lead"] for s in ordered] == [True, False, False]


def test_a_version_with_few_ratings_cannot_lead_however_many_stars():
    sources = [
        {"name": "Five stars, three votes", "url": A, "rating": 5.0, "rating_count": 3},
        {"name": "Well rated", "url": B, "rating": 4.7, "rating_count": 900},
    ]
    assert rr.pick_lead(sources)[0]["name"] == "Well rated"
    assert rr.needs_trusted_cooks(sources[:1]) is True
    assert rr.needs_trusted_cooks(sources) is False


# ---------- research: real pages only, saved, reused ----------

def test_research_keeps_only_pages_it_saw_skips_a_refused_site_and_is_saved(monkeypatch):
    client = _use_client(monkeypatch, _FakeClient([
        (_search([A, B, C]) + _fetch_refused(B) + [_submit([
            {"name": "Cook A", "title": "Chana Masala", "url": A, "rating": 4.9, "rating_count": 1200},
            {"name": "Cook B", "title": "Chana", "url": B, "rating": 5.0, "rating_count": 5000},
            {"name": "Cook C", "title": "Chole", "url": C, "rating": 4.6, "rating_count": 80},
            {"name": "Made up", "title": "?", "url": GONE, "rating": 5.0, "rating_count": 9999},
        ])], "tool_use"),
    ]))

    research = agent.research_dish("Chana Masala", "Indian", "")

    names = [s["name"] for s in research["sources"]]
    # B blocked the reader, GONE was never in any result: neither is kept,
    # so neither can lead, whatever numbers were reported for them.
    assert names == ["Cook A", "Cook C"]
    assert research["sources"][0]["lead"] is True
    assert research["fallback_used"] is False
    # The web tools were offered, open (no domain limit) on the first pass.
    tool_types = [t.get("type") for t in client.calls[0]["tools"]]
    assert "web_search_20260209" in tool_types and "web_fetch_20260209" in tool_types
    assert "allowed_domains" not in client.calls[0]["tools"][0]


def test_a_dish_that_comes_back_is_not_researched_again(monkeypatch):
    client = _use_client(monkeypatch, _FakeClient([
        (_search([A]) + [_submit([{"name": "Cook A", "url": A, "rating": 4.9, "rating_count": 500}])], "tool_use"),
    ]))
    first = agent.research_dish("Chana Masala", "Indian", "")
    again = agent.research_dish("chana  masala!", "Indian", "")
    assert len(client.calls) == 1
    assert again["id"] == first["id"]


def test_research_is_per_household(monkeypatch):
    client = _use_client(monkeypatch, _FakeClient([
        (_search([A]) + [_submit([{"name": "Cook A", "url": A, "rating": 4.9, "rating_count": 500}])], "tool_use"),
    ]))
    agent.research_dish("Chana Masala", "Indian", "")
    with tools.use_household(2):
        assert rr.saved_research("Chana Masala") is None
    assert len(client.calls) == 1


def test_nothing_well_rated_falls_back_to_the_trusted_cooks_for_the_cuisine(monkeypatch):
    swasthi = "https://www.indianhealthyrecipes.com/chana-masala/"
    hebbar = "https://hebbarskitchen.com/chana-masala/"
    client = _use_client(monkeypatch, _FakeClient([
        (_search([A]) + [_submit([{"name": "Cook A", "url": A, "rating": 5.0, "rating_count": 2}])], "tool_use"),
        (_search([hebbar, swasthi], tool_id="s2") + [_submit([
            {"name": "Hebbar's Kitchen", "url": hebbar, "rating": None, "rating_count": None},
            {"name": "Swasthi's Recipes", "url": swasthi, "rating": None, "rating_count": None},
        ])], "tool_use"),
    ]))

    research = agent.research_dish("Chana Masala", "North Indian", "")

    assert len(client.calls) == 2
    second_tools = {t.get("name"): t for t in client.calls[1]["tools"]}
    allowed = second_tools["web_search"]["allowed_domains"]
    assert "indianhealthyrecipes.com" in allowed and "hebbarskitchen.com" in allowed
    assert second_tools["web_fetch"]["allowed_domains"] == allowed
    assert research["fallback_used"] is True
    # Neither is rated, so the earliest cook on the trusted list leads.
    assert research["sources"][0]["name"] == "Swasthi's Recipes"


def test_a_paused_search_turn_is_carried_on(monkeypatch):
    client = _use_client(monkeypatch, _FakeClient([
        (_search([A]), "pause_turn"),
        ([_submit([{"name": "Cook A", "url": A, "rating": 4.9, "rating_count": 500}])], "tool_use"),
    ]))
    research = agent.research_dish("Chana Masala", "Indian", "")
    assert len(client.calls) == 2
    assert client.calls[1]["messages"][-1]["role"] == "assistant"
    assert research["sources"][0]["url"] == A


def test_research_off_or_failing_never_costs_the_recipe(monkeypatch):
    def _boom():
        raise RuntimeError("no network")

    monkeypatch.setattr(agent, "_client", _boom)
    assert agent.research_dish("Chana Masala", "Indian", "") is None
    monkeypatch.setenv("RECIPE_RESEARCH", "off")
    assert agent.research_dish("Chana Masala", "Indian", "") is None


# ---------- the writer is handed it; the page is served it ----------

def _pending(name="Chana Masala"):
    tools.add_recipe(name=name, ingredients=[], details_pending=True,
                     dish_note="chickpeas in an onion-tomato masala", cuisine="Indian")
    return tools.get_recipe(name)


def _written(spec):
    return {
        "ingredients": [
            {"item": "Chickpeas", "qty": "2 cans", "category": "pantry"},
            {"item": "Onion", "qty": "1", "category": "produce"},
        ],
        "instructions": ["Soften the onion until it smells sweet.",
                         "Add the chickpeas and simmer 20 minutes."],
        "default_servings": 4,
        "household_changes": ["No cashews — Asha can't have tree nuts; sunflower seeds instead"],
    }


def test_the_writer_gets_the_research_and_the_page_gets_the_sources(monkeypatch, signed_in):
    _use_client(monkeypatch, _FakeClient([
        (_search([A, C]) + [_submit([
            {"name": "Cook A", "title": "Chana Masala", "url": A, "rating": 4.9, "rating_count": 1200},
            {"name": "Cook C", "title": "Chole", "url": C, "rating": 4.6, "rating_count": 80},
        ])], "tool_use"),
    ]))
    seen_specs = []

    def _writer(spec):
        seen_specs.append(spec)
        return _written(spec)

    monkeypatch.setattr(agent, "generate_recipe_details_llm", _writer)
    monkeypatch.setattr(rr, "start_link_check", lambda research: False)
    recipe = _pending()
    out = agent._write_one_pending_recipe(recipe, "dinner", {"serves": 4, "must_not_contain": []}, [])
    assert out["ok"] is True

    research = seen_specs[0]["research"]
    assert research["lead"]["name"] == "Cook A"
    assert [o["name"] for o in research["others"]] == ["Cook C"]
    assert "url" not in research["lead"], "the writer gets no page it could copy from"
    assert "onion-tomato" in research["what_they_agree_and_differ_on"]

    body = signed_in.get(f"/api/recipes/{recipe['id']}").json()
    assert body["research"]["credit"] == "Based on Cook A, checked against Cook C"
    assert body["research"]["credit_prefix"] == "Based on"
    assert [s["lead"] for s in body["research"]["sources"]] == [True, False]
    assert body["research"]["sources"][0]["rating_count"] == 1200
    assert body["household_changes"] == ["No cashews — Asha can't have tree nuts; sunflower seeds instead"]


def test_a_recipe_written_without_research_reads_as_none(signed_in):
    tools.add_recipe(name="Toast", ingredients=[{"item": "Bread", "qty": "1 loaf"}])
    body = signed_in.get(f"/api/recipes/{tools.get_recipe('Toast')['id']}").json()
    assert body["research"] is None
    assert body["household_changes"] == []


def _filed(monkeypatch, name="Chana Masala"):
    monkeypatch.setattr(rr, "start_link_check", lambda research: False)
    return rr.save_research(name, rr.pick_lead([
        {"name": "Cook A", "title": "", "url": A, "rating": 4.9, "rating_count": 1200},
        {"name": "Cook C", "title": "", "url": C, "rating": 4.6, "rating_count": 80},
    ]), "notes", False)


def test_changed_a_lot_through_change_recipe_reads_originally_based_on(monkeypatch):
    _filed(monkeypatch)
    tools.add_recipe(name="Chana Masala", ingredients=[{"item": "Chickpeas", "qty": "2 cans"}])
    recipe = tools.get_recipe("Chana Masala")
    tools.record_recipe_change_request("Chana Masala", "Less spicy", recipe_id=recipe["id"])
    assert rr.recipe_research_for(recipe)["credit_prefix"] == "Based on"
    tools.record_recipe_change_request("Chana Masala", "No pressure cooker", recipe_id=recipe["id"])
    assert rr.recipe_research_for(recipe)["credit"].startswith("Originally based on Cook A")


# ---------- links checked now and then ----------

def test_a_gone_link_is_hidden_and_a_bot_blocking_site_is_not(monkeypatch):
    research = _filed(monkeypatch)
    answers = {A: None, C: False}  # A refused the checker (403); C is gone (404)
    monkeypatch.setattr(rr, "link_status", lambda url, timeout=6.0: answers[url])
    assert rr.check_research_links(research) == 1

    tools.add_recipe(name="Chana Masala", ingredients=[{"item": "Chickpeas", "qty": "2 cans"}])
    shown = rr.recipe_research_for(tools.get_recipe("Chana Masala"), check_links=False)
    assert [s["name"] for s in shown["sources"]] == ["Cook A"]
    # The credit still says what it was checked against; only the link goes.
    assert shown["credit"] == "Based on Cook A, checked against Cook C"
    # Checked just now, so nothing is due again for a month.
    assert not any(rr.due_for_check(s) for s in rr.saved_research("Chana Masala")["sources"])
