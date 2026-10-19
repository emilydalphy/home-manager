"""
Recipe research's input budget (Loop Board, 2026-10-18: "Recipe research
reads ~134,000 tokens per dish though each page is capped at 3,000").

Live, Tester Home: 5 calls, 669,582 input tokens, 5 searches — one request a
dish, ~134K each. The page cap held; what didn't was everything around it:
the web tools ran from a code-execution sandbox (dynamic filtering, the
_20260209 default) and one request searched AND read, so every server-side
step re-billed the search results, the pages and the sandbox's own output.

Pinned here, with no network and no live spend:
  * the web tools are called directly — no code-execution layer;
  * the search and the reading are separate requests, and the reading one
    never carries the search results;
  * a dish's requests, at their worst case, fit the per-dish budget — this
    is the test that goes red if a prompt, a page cap or a lookup grows;
  * no request starts once a dish has spent its budget, the trusted-cooks
    fallback included;
  * every request's input breakdown can be read off it.
"""
from __future__ import annotations

import types

import pytest

from app import agent
from app.tools import recipe_research as rr


@pytest.fixture(autouse=True)
def _research_on(monkeypatch):
    monkeypatch.setenv("RECIPE_RESEARCH", "on")


def _ns(**kw):
    return types.SimpleNamespace(**kw)


SWASTHI = "https://www.indianhealthyrecipes.com/chana-masala-recipe-chole-masala/"
TIN = "https://www.recipetineats.com/chana-masala/"
OTHERS = [f"https://site-{i}.example.com/chana-masala/" for i in range(6)]
PAGE = "Chana masala. " * 857  # ~12,000 characters: a page read to the 3,000-token cap


def _found():
    results = [_ns(type="web_search_result", url=u, title=f"Chana Masala {i}", encrypted_content="e" * 2000)
               for i, u in enumerate([SWASTHI, TIN] + OTHERS)]
    return [_ns(type="server_tool_use", name="web_search", id="s1", input={"query": "chana masala"}),
            _ns(type="web_search_tool_result", tool_use_id="s1", content=results),
            _ns(type="tool_use", name="choose_pages_to_read", id="p1", input={"urls": [SWASTHI, TIN, OTHERS[0]]})]


def _read():
    out = []
    for i, u in enumerate([SWASTHI, TIN]):
        out += [_ns(type="server_tool_use", name="web_fetch", id=f"g{i}", input={"url": u}),
                _ns(type="web_fetch_tool_result", tool_use_id=f"g{i}",
                    content=_ns(type="web_fetch_result", url=u,
                                content=_ns(type="document", source=_ns(type="text", data=PAGE))))]
    out.append(_ns(type="tool_use", name="submit_dish_research", id="t1", input={
        "sources": [{"name": "Swasthi's Recipes", "url": SWASTHI, "rating": 4.9, "rating_count": 1400},
                    {"name": "RecipeTin Eats", "url": TIN, "rating": 4.9, "rating_count": 300}],
        "method_notes": "Onion-tomato masala, chana masala powder, simmer 20 minutes."}))
    return out


class _Client:
    """Answers each request with the next canned turn, billed `billed` input
    tokens, and keeps what it was asked."""

    def __init__(self, turns, billed=0):
        self.turns, self.billed, self.calls = list(turns), billed, []
        self.messages = self

    def create(self, **kwargs):
        self.calls.append(kwargs)
        content, stop = self.turns.pop(0)
        usage = _ns(input_tokens=self.billed, cache_read_input_tokens=0, cache_creation_input_tokens=0,
                    output_tokens=0, server_tool_use=None)
        return _ns(content=content, stop_reason=stop, usage=usage, model=kwargs.get("model"))


def _research(monkeypatch, turns, billed=0, **kw):
    client = _Client(turns, billed)
    monkeypatch.setattr(agent, "_client", lambda: client)
    return client, agent.research_dish("Chana Masala", "North Indian",
                                       "chickpeas in an onion-tomato masala, weeknight, mild", **kw)


def test_a_dishs_research_fits_its_input_budget_at_its_worst(monkeypatch):
    client, research = _research(monkeypatch, [(_found(), "tool_use"), (_read(), "tool_use")])
    assert research["sources"][0]["name"] == "Swasthi's Recipes"
    assert len(client.calls) == 2
    ceilings = [agent._research_request_ceiling(c["tools"], c["messages"]) for c in client.calls]
    # Each step at its worst: every lookup its own billed step, the search's
    # results at _SEARCH_RESULT_TOKENS, each page at the cap. Was ~134,000.
    assert sum(ceilings) <= rr.dish_input_tokens() == 25000, ceilings


def test_a_bigger_page_cap_breaks_the_budget(monkeypatch):
    """The test above is the tripwire: raise what a page may bring and it fails."""
    monkeypatch.setenv("RECIPE_RESEARCH_FETCH_TOKENS", "8000")
    client, _ = _research(monkeypatch, [(_found(), "tool_use"), (_read(), "tool_use")])
    assert sum(agent._research_request_ceiling(c["tools"], c["messages"]) for c in client.calls) > 25000


def test_the_web_tools_are_called_directly_and_reading_never_carries_the_search(monkeypatch):
    client, _ = _research(monkeypatch, [(_found(), "tool_use"), (_read(), "tool_use")])
    for call in client.calls:
        web = [t for t in call["tools"] if "type" in t]
        assert web and all(t["allowed_callers"] == ["direct"] for t in web)
        assert not any("code_execution" in t["type"] for t in web)
    find, read = client.calls
    assert [t["name"] for t in find["tools"]] == ["web_search", "choose_pages_to_read"]
    assert [t["name"] for t in read["tools"]] == ["web_fetch", "submit_dish_research"]
    # The read request is one short user message naming the chosen pages:
    # no search results, and only as many reads as pages (the search's
    # third pick was cut to the dish's two reads).
    assert len(read["messages"]) == 1 and isinstance(read["messages"][0]["content"], str)
    assert SWASTHI in read["messages"][0]["content"] and OTHERS[0] not in read["messages"][0]["content"]
    assert read["tools"][0]["max_uses"] == 2


def test_no_request_starts_once_the_dish_has_spent_its_budget(monkeypatch, caplog):
    with caplog.at_level("WARNING", logger="home_manager"):
        client, research = _research(monkeypatch, [(_found(), "tool_use"), (_read(), "tool_use")],
                                     billed=26000)
    assert len(client.calls) == 1, "the find step overran: nothing more is paid for"
    assert research is None
    assert any("input budget spent" in r.getMessage() for r in caplog.records)


def test_the_trusted_cooks_fallback_shares_the_dishs_budget(monkeypatch):
    monkeypatch.setenv("RECIPE_RESEARCH_SEARCHES", "4")
    monkeypatch.setenv("RECIPE_RESEARCH_FETCHES", "6")
    unrated = _read()
    unrated[-1].input["sources"] = [{"name": "Few", "url": SWASTHI, "rating": 5.0, "rating_count": 2}]
    # The open pass (find + read) bills 12,500 each: 25,000, the whole budget.
    client, _ = _research(monkeypatch, [(_found(), "tool_use"), (unrated, "tool_use"),
                                        (_found(), "tool_use"), (_read(), "tool_use")], billed=12500)
    assert len(client.calls) == 2, "nothing well rated, but nothing left to search the trusted cooks with"


def test_a_pass_with_no_search_left_makes_no_request(monkeypatch):
    """At the default 1 search + 2 reads the fallback has reads but no
    search — and a read can only open a page a search found."""
    client = _Client([])
    monkeypatch.setattr(agent, "_client", lambda: client)
    out = agent.research_dish_llm("Chana Masala", budget={"web_search": 0, "web_fetch": 2})
    assert client.calls == [] and "sources" not in out


def test_each_requests_input_breakdown_is_readable(monkeypatch):
    usage = _ns(input_tokens=9000, cache_read_input_tokens=500, cache_creation_input_tokens=0)
    tools_now = agent._research_tools({"web_search": 0, "web_fetch": 2}, None, search=False, fetch=True)
    out = agent._research_input_breakdown(tools_now, [{"role": "user", "content": "x" * 400}], _read(), usage)
    assert out["billed"] == 9500
    assert out["page_tokens"] == 2 * len(PAGE) // 4
    assert out["server_steps"] == 3, "two reads, then the submit: each a billed step"
    assert out["sent"] > 100
