"""
A chat "forget X" that found no X draws no "updated" card (defect hunt
2026-10-24).

delete_preference already said deleted: false for a dislike or protein it
didn't have, but the action-card summary only read `status` and `found`, so
"forget cilantro" with no cilantro on the list still showed "Household info
updated · Updated cilantro". Cuisines and stores said deleted: true either
way. Driven through run_agent_turn with the model stubbed, then the real
summarize_chat_actions — the path /api/chat takes.
"""
import types

import pytest

from app import agent, tools
from app.main import summarize_chat_actions


class _Usage:
    input_tokens = cache_read_input_tokens = cache_creation_input_tokens = output_tokens = 0


def _turn(monkeypatch, tool_name, tool_input, reply="Done."):
    responses = [
        types.SimpleNamespace(
            content=[types.SimpleNamespace(type="tool_use", name=tool_name, input=tool_input, id="tu_1")],
            stop_reason="tool_use", usage=_Usage()),
        types.SimpleNamespace(
            content=[types.SimpleNamespace(type="text", text=reply)],
            stop_reason="end_turn", usage=_Usage()),
    ]
    fake = types.SimpleNamespace(create=lambda **kw: responses.pop(0))
    monkeypatch.setattr(agent, "_client", lambda: types.SimpleNamespace(messages=fake))
    _reply, history = agent.run_agent_turn([], "forget it")
    return summarize_chat_actions([], history)


@pytest.fixture
def prefs():
    tools.set_household_meal_preferences(cuisine_preferences=["Thai"])
    tools.add_food_dislikes(["olives"])
    tools.add_usual_stores(["Loblaws"])


@pytest.mark.parametrize("field,item", [
    ("dislikes", "cilantro"),
    ("cuisine_preferences", "Mexican"),
    ("usual_stores", "Costco"),
])
def test_forgetting_something_that_was_never_saved_draws_no_card(monkeypatch, prefs, field, item):
    assert _turn(monkeypatch, "delete_preference", {"field": field, "item": item}) == []


@pytest.mark.parametrize("field,item", [
    ("dislikes", "olives"),
    ("cuisine_preferences", "thai"),
    ("usual_stores", "Loblaws"),
])
def test_forgetting_something_saved_still_draws_its_card(monkeypatch, prefs, field, item):
    actions = _turn(monkeypatch, "delete_preference", {"field": field, "item": item})
    assert [a.kicker for a in actions] == ["Household info updated"]


def test_an_empty_list_add_is_not_mistaken_for_nothing():
    # add_grocery_items' `added` is a LIST; only a literal False is the answer.
    assert agent.result_changed_nothing('{"added": [], "merged_with_existing": ["milk"]}') is False
    assert agent.result_changed_nothing('{"added": false, "reason": "empty"}') is True
