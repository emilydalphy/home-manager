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
from app.db import get_conn
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


def _fetched(urls, prefix="g"):
    out = []
    for i, u in enumerate(urls):
        tid = f"{prefix}{i}"
        out += [
            _ns(type="server_tool_use", name="web_fetch", id=tid, input={"url": u}),
            _ns(type="web_fetch_tool_result", tool_use_id=tid,
                content=_ns(type="web_fetch_result", url=u, content=_ns(type="document"))),
        ]
    return out


def _read(urls, tool_id="s1"):
    """Searched AND read — the shape a page that can lead comes in."""
    return _search(urls, tool_id) + _fetched(urls, prefix="g" + tool_id)


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
        (_search([A, B, C]) + _fetched([A, C]) + _fetch_refused(B) + [_submit([
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
        (_read([A]) + [_submit([{"name": "Cook A", "url": A, "rating": 4.9, "rating_count": 500}])], "tool_use"),
    ]))
    first = agent.research_dish("Chana Masala", "Indian", "")
    again = agent.research_dish("chana  masala!", "Indian", "")
    assert len(client.calls) == 1
    assert again["id"] == first["id"]


def test_research_is_per_household(monkeypatch):
    client = _use_client(monkeypatch, _FakeClient([
        (_read([A]) + [_submit([{"name": "Cook A", "url": A, "rating": 4.9, "rating_count": 500}])], "tool_use"),
    ]))
    agent.research_dish("Chana Masala", "Indian", "")
    with tools.use_household(2):
        assert rr.saved_research("Chana Masala") is None
    assert len(client.calls) == 1


def test_nothing_well_rated_falls_back_to_the_trusted_cooks_for_the_cuisine(monkeypatch):
    swasthi = "https://www.indianhealthyrecipes.com/chana-masala/"
    hebbar = "https://hebbarskitchen.com/chana-masala/"
    client = _use_client(monkeypatch, _FakeClient([
        (_read([A]) + [_submit([{"name": "Cook A", "url": A, "rating": 5.0, "rating_count": 2}])], "tool_use"),
        (_read([hebbar, swasthi], tool_id="s2") + [_submit([
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
        (_read([A]), "pause_turn"),
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
        (_read([A, C]) + [_submit([
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


def _filed(monkeypatch=None, name="Chana Masala"):
    if monkeypatch is not None:
        monkeypatch.setattr(rr, "start_link_check", lambda research: False)
    return rr.save_research(name, rr.pick_lead([
        {"name": "Cook A", "title": "", "url": A, "rating": 4.9, "rating_count": 1200},
        {"name": "Cook C", "title": "", "url": C, "rating": 4.6, "rating_count": 80},
    ]), "notes", False)


def _written_from(name, research):
    """A recipe written from this research — the link the recipe pass sets."""
    tools.add_recipe(name=name, ingredients=[{"item": "Chickpeas", "qty": "2 cans"}])
    recipe = tools.get_recipe(name)
    conn = get_conn()
    conn.execute("UPDATE recipes SET research_id = ? WHERE id = ?", (research["id"], recipe["id"]))
    conn.commit()
    conn.close()
    return recipe


def test_changed_a_lot_through_change_recipe_reads_originally_based_on(monkeypatch):
    recipe = _written_from("Chana Masala", _filed(monkeypatch))
    tools.record_recipe_change_request("Chana Masala", "Less spicy", recipe_id=recipe["id"])
    assert rr.recipe_research_for(recipe)["credit_prefix"] == "Based on"
    tools.record_recipe_change_request("Chana Masala", "No pressure cooker", recipe_id=recipe["id"])
    assert rr.recipe_research_for(recipe)["credit"].startswith("Originally based on Cook A")


# ---------- links checked now and then ----------

def test_a_gone_link_is_hidden_and_a_bot_blocking_site_is_not(monkeypatch):
    research = _filed(monkeypatch)
    answers = {A: None, C: False}  # A refused the checker (403); C is gone (404)
    monkeypatch.setattr(rr, "link_status", lambda url: answers[url])
    assert rr.check_research_links(research) == 1

    _written_from("Chana Masala", research)
    shown = rr.recipe_research_for(tools.get_recipe("Chana Masala"), check_links=False)
    assert [s["name"] for s in shown["sources"]] == ["Cook A"]
    # The credit still says what it was checked against; only the link goes.
    assert shown["credit"] == "Based on Cook A, checked against Cook C"
    # Checked just now, so nothing is due again for a month.
    assert not any(rr.due_for_check(s) for s in rr.saved_research("Chana Masala")["sources"])


# ---------- review round, 2026-10-06 ----------

def test_only_a_page_that_was_read_can_lead(monkeypatch):
    """A rating reported for a page only SEEN in a search snippet (or one
    injected into it) must not make it the lead: C was read, B was not."""
    _use_client(monkeypatch, _FakeClient([
        (_search([B, C]) + _fetched([C]) + [_submit([
            {"name": "Snippet only", "url": B, "rating": 5.0, "rating_count": 99999},
            {"name": "Read it", "url": C, "rating": 4.6, "rating_count": 300},
        ])], "tool_use"),
    ]))
    research = agent.research_dish("Chole", "Indian", "")
    assert research["sources"][0]["name"] == "Read it"
    assert research["sources"][0]["lead"] is True
    assert [s["lead"] for s in research["sources"][1:]] == [False]


def test_nothing_read_is_no_research_and_the_empty_result_is_remembered(monkeypatch):
    client = _use_client(monkeypatch, _FakeClient([
        (_search([A]) + [_submit([{"name": "A", "url": A, "rating": 4.9, "rating_count": 500}])], "tool_use"),
        (_search([B], tool_id="s2") + [_submit([{"name": "B", "url": B, "rating": None, "rating_count": None}])], "tool_use"),
    ]))
    assert agent.research_dish("Chana Masala", "Indian", "") is None
    calls = len(client.calls)
    # The dish comes back next week: nothing is paid for again.
    assert agent.research_dish("Chana Masala", "Indian", "") is None
    assert len(client.calls) == calls


def test_the_per_dish_budget_holds_across_continuations_and_the_fallback(monkeypatch):
    """Two searches and two fetches spent in the open pass (one paused round
    and one more) leave the fallback only what the dish has left, and a
    pass never runs more than _RESEARCH_MAX_ROUNDS rounds."""
    two_fetches = _fetched([A, B], prefix="x")
    client = _use_client(monkeypatch, _FakeClient([
        (_search([A], "s1") + _search([B], "s2"), "pause_turn"),
        (two_fetches, "pause_turn"),      # second and last round: never submits
        (_read([C], "s3") + [_submit([{"name": "C", "url": C, "rating": None, "rating_count": None}])], "tool_use"),
    ]))
    agent.research_dish("Chana Masala", "Indian", "")
    assert len(client.calls) == 3, "the open pass stops after its rounds; then one fallback call"
    first_tools = {t["name"]: t for t in client.calls[0]["tools"] if "max_uses" in t}
    assert first_tools["web_search"]["max_uses"] == agent._OPEN_SEARCH_SHARE["web_search"]
    # After the open pass's first round spent both its searches, its second
    # round is offered no search at all.
    assert "web_search" not in {t.get("name") for t in client.calls[1]["tools"]}
    fallback_tools = {t["name"]: t for t in client.calls[2]["tools"] if "max_uses" in t}
    assert fallback_tools["web_search"]["max_uses"] == agent.DISH_RESEARCH_BUDGET["web_search"] - 2
    assert fallback_tools["web_fetch"]["max_uses"] == agent.DISH_RESEARCH_BUDGET["web_fetch"] - 2


def test_reported_sources_with_no_result_urls_are_logged_loudly(monkeypatch, caplog):
    _use_client(monkeypatch, _FakeClient([
        ([_submit([{"name": "A", "url": A, "rating": 4.9, "rating_count": 500}])], "tool_use"),
        ([_submit([])], "tool_use"),
    ]))
    with caplog.at_level("WARNING", logger="home_manager"):
        agent.research_dish("Chana Masala", "Indian", "")
    assert any("no search/fetch result URL was read" in r.getMessage() for r in caplog.records)


def test_web_searches_are_recorded_and_priced(monkeypatch):
    usage = _usage()
    usage.server_tool_use = _ns(web_search_requests=3, web_fetch_requests=2)
    agent._record_api_call("research_dish_llm", agent.MODEL, _ns(usage=usage), 1.0)
    conn = get_conn()
    row = conn.execute("SELECT web_search_requests, web_fetch_requests FROM api_calls "
                       "WHERE call_site = 'research_dish_llm'").fetchone()
    conn.close()
    assert (row["web_search_requests"], row["web_fetch_requests"]) == (3, 2)
    cost = tools.get_month_to_date_cost()
    assert cost["by_call_site"]["research_dish_llm"]["cost"]["total"] == pytest.approx(0.03)
    assert cost["total_cost"]["total"] >= 0.03


def test_a_households_own_recipe_of_the_same_name_shows_no_research(monkeypatch):
    """Research is shown through the recipe it was written from, never by
    name: a hand-written "Chana Masala" is not "Based on" anything."""
    _filed(monkeypatch)
    tools.add_recipe(name="Chana Masala", ingredients=[{"item": "Chickpeas", "qty": "2 cans"}])
    assert rr.recipe_research_for(tools.get_recipe("Chana Masala")) is None


def test_the_cook_screens_fill_never_waits_on_a_web_search(monkeypatch):
    def _no_search(*a, **k):
        raise AssertionError("the live fill must not research")

    monkeypatch.setattr(agent, "research_dish_llm", _no_search)
    monkeypatch.setattr(agent, "generate_recipe_details_llm", _written)
    _pending("Rajma")
    out = agent.fill_in_recipe("Rajma")
    assert out["instructions"]


def test_research_off_sends_no_link_check_from_the_recipe_page(monkeypatch, signed_in):
    """RECIPE_RESEARCH=off stops every outbound call — the recipe page's
    link checks too. start_link_check itself is NOT stubbed here."""
    research = _filed()
    recipe = _written_from("Chana Masala", research)
    reached = []
    monkeypatch.setattr(rr, "_resolve", lambda host: reached.append(host) or ["93.184.216.34"])
    monkeypatch.setattr(rr, "_request", lambda *a: reached.append(a) or (200, ""))
    monkeypatch.setenv("RECIPE_RESEARCH", "off")
    body = signed_in.get(f"/api/recipes/{recipe['id']}").json()
    import threading
    for t in threading.enumerate():
        if t.name.startswith("recipe-links-"):
            t.join(5)
    assert body["research"]["credit"].startswith("Based on Cook A")
    assert reached == []


# ---------- the link checker can't be pointed inside ----------

def _checker(monkeypatch, addresses, answers):
    asked = []

    def _req(scheme, host, port, ip, method, path):
        asked.append((host, ip, method, path))
        return answers.pop(0)

    monkeypatch.setattr(rr, "_resolve", lambda host: addresses[host])
    monkeypatch.setattr(rr, "_request", _req)
    return asked


@pytest.mark.parametrize("ip", ["127.0.0.1", "10.0.0.5", "169.254.169.254", "::1", "192.168.1.1", "0.0.0.0"])
def test_a_private_or_local_address_is_never_requested(monkeypatch, ip):
    asked = _checker(monkeypatch, {"evil.example": [ip]}, [(200, "")])
    assert rr.link_status("https://evil.example/x") is None
    assert asked == []


def test_a_redirect_into_a_private_address_is_refused(monkeypatch):
    asked = _checker(monkeypatch, {"good.example": ["93.184.216.34"], "inside.example": ["10.1.2.3"]},
                     [(302, "http://inside.example/admin")])
    assert rr.link_status("https://good.example/recipe") is None
    assert [a[0] for a in asked] == ["good.example"]


def test_only_http_and_https_and_only_a_few_hops(monkeypatch):
    asked = _checker(monkeypatch, {"good.example": ["93.184.216.34"]},
                     [(301, "/a"), (301, "/b"), (301, "/c"), (301, "/d"), (301, "/e")])
    assert rr.link_status("ftp://good.example/file") is None
    assert rr.link_status("file:///etc/passwd") is None
    assert asked == []
    assert rr.link_status("https://good.example/start") is None
    assert len(asked) == rr._LINK_MAX_HOPS + 1


def test_gone_is_false_blocked_is_unknown_and_head_falls_back_to_get(monkeypatch):
    asked = _checker(monkeypatch, {"good.example": ["93.184.216.34"]},
                     [(404, ""), (403, ""), (405, ""), (200, "")])
    assert rr.link_status("https://good.example/gone") is False
    assert rr.link_status("https://good.example/bots-not-welcome") is None
    assert rr.link_status("https://good.example/no-head") is True
    assert [a[2] for a in asked][-2:] == ["HEAD", "GET"]
    assert all(a[1] == "93.184.216.34" for a in asked), "requests go to the checked address"
