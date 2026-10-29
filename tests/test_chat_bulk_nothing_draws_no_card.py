"""
A chat write that changed nothing draws no "updated" card — the two shapes
the 2026-10-24 sweep missed (defect hunt 2026-10-29).

1. update_inventory_items wraps one update_inventory answer per name in
   {"updated": [...]}. update_inventory alone saying found: false already
   drew no card, but the batch did: "we used up the saffron" with no
   saffron in the kitchen drew "Kitchen updated · Updated saffron", and a
   half-found batch named the missing item as changed too.
2. The grocery list's bulk tidies answer with a count. "Clear the list" on
   an empty list (removed_count 0), consolidate with nothing to merge
   (lines_merged_away 0), repair with nothing mangled (fixed_count 0) each
   drew "Grocery updated · Your list has changed".

Driven through run_agent_turn with the model stubbed, then the real
summarize_chat_actions — and once through POST /api/chat itself.
"""
from __future__ import annotations

import types

import pytest

from app import agent, main, tools


class _Usage:
    input_tokens = cache_read_input_tokens = cache_creation_input_tokens = output_tokens = 0


def _stub(monkeypatch, name, args, reply):
    responses = [
        types.SimpleNamespace(
            content=[types.SimpleNamespace(type="tool_use", name=name, input=args, id="tu_1")],
            stop_reason="tool_use", usage=_Usage()),
        types.SimpleNamespace(
            content=[types.SimpleNamespace(type="text", text=reply)],
            stop_reason="end_turn", usage=_Usage()),
    ]
    fake = types.SimpleNamespace(create=lambda **kw: responses.pop(0))
    monkeypatch.setattr(agent, "_client", lambda: types.SimpleNamespace(messages=fake))


def _turn(monkeypatch, name, args, reply="Right."):
    _stub(monkeypatch, name, args, reply)
    text, conversation = agent.run_agent_turn([], "x")
    return text, main.summarize_chat_actions([], conversation)


def test_a_batch_of_names_the_kitchen_doesnt_hold_draws_no_card(monkeypatch):
    text, actions = _turn(monkeypatch, "update_inventory_items",
                          {"items": ["saffron", "truffle oil"], "action": "remove"},
                          reply="I've removed those from the kitchen.")
    assert actions == []
    # And the reply's claim is not backed by a write that never happened.
    assert text == agent.CHANGE_CLAIM_RETRACTION


def test_a_half_found_batch_names_only_what_it_changed(monkeypatch):
    tools.update_inventory("milk", "add", quantity="1 l")
    _text, actions = _turn(monkeypatch, "update_inventory_items",
                           {"items": ["milk", "saffron"], "action": "remove"})
    assert [(a.kicker, a.change, a.tab) for a in actions] == [("Kitchen updated", "Updated milk", "home")]


def test_a_batch_that_wrote_still_draws_its_card(monkeypatch):
    _text, actions = _turn(monkeypatch, "update_inventory_items",
                           {"items": ["rice", "flour"], "action": "add"})
    assert [(a.change, a.tab) for a in actions] == [("Updated rice and flour", "home")]


@pytest.mark.parametrize("name", [
    "clear_grocery_list", "clear_stale_grocery_items", "consolidate_grocery_list", "repair_grocery_quantities",
])
def test_a_bulk_tidy_that_found_nothing_draws_no_card(monkeypatch, name):
    text, actions = _turn(monkeypatch, name, {}, reply="I've cleared the list.")
    assert actions == []
    assert text == agent.CHANGE_CLAIM_RETRACTION


def test_clearing_a_list_with_something_on_it_still_draws_its_card(monkeypatch):
    tools.add_grocery_item("milk")
    _text, actions = _turn(monkeypatch, "clear_grocery_list", {})
    assert [(a.kicker, a.tab) for a in actions] == [("Grocery updated", "grocery")]


def test_a_count_key_that_is_not_zero_or_not_a_number_is_not_nothing():
    assert agent.result_changed_nothing('{"removed_count": 2}') is False
    assert agent.result_changed_nothing('{"removed_count": false}') is False
    # Other writes' `updated` is a count or a bool, never the batch's list.
    assert agent.result_changed_nothing('{"updated": 0, "requested": 1}') is False
    assert agent.result_changed_nothing('{"updated": true}') is False


def test_the_chat_endpoint_carries_no_card_for_an_empty_clear(monkeypatch, signed_in):
    _stub(monkeypatch, "clear_grocery_list", {}, reply="Your list was already empty.")
    res = signed_in.post("/api/chat", json={"session_id": "default", "message": "wipe the list"})
    assert res.status_code == 200
    assert res.json().get("actions") == []
