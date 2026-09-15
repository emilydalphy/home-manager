"""
"Something you run out of, mentioned in chat, is offered as a staple"
(Loop Board, Medium, Phase 1.5 -- Paid-ready, Improvement).

Observed 2026-09-15: "add dish soap to the list" -> "Dish soap is on your
list." + a Grocery updated card, and nothing else -- no offer to keep an
eye on it, so the same gap notices it again in six weeks.

The fix lives in app/tools/staples.py:
  - `_is_supply` reuses section_for's own pantry/household classifier to
    tell a running-low SUPPLY (dish soap, coffee, toilet paper) from a
    recipe ingredient for a specific meal (shrimp, parsley) -- never a
    separate keyword list.
  - `offer_for_chat_grocery_add` decides whether THIS grocery line is
    worth asking about, and marks it asked (grocery_items.
    staple_offer_made) in the same breath so it is never asked twice for
    the same line, on any answer (yes, no, or silence).
  - `add_grocery_item_for_chat` is the chat tool's own add_grocery_item --
    identical to grocery.add_grocery_item, plus the `staple_offer` key.
    The Shop tab's own add route still calls grocery.add_grocery_item
    directly and is untouched.

app/main.py's `_staple_offer_from_turn` (mirroring `_proposal_from_turn`)
pulls the last genuine offer out of a chat turn's tool results onto
ChatResponse.staple_offer, which is None whenever the item was already a
staple -- there is nothing to tap in that case, only a sentence to say.
"""
from __future__ import annotations

import json
import types

import pytest

from app import agent, tools
from app.db import get_conn
from app.tools import staples as st
from app import main as app_main


def _staples_count() -> int:
    conn = get_conn()
    n = conn.execute("SELECT COUNT(*) FROM staples").fetchone()[0]
    conn.close()
    return n


def _staple_offer_made(item_id: int) -> bool:
    conn = get_conn()
    row = conn.execute(
        "SELECT staple_offer_made FROM grocery_items WHERE id = ?", (item_id,)
    ).fetchone()
    conn.close()
    return bool(row["staple_offer_made"])


# --------------------------------------------------------- _is_supply ----


def test_household_item_is_a_supply():
    assert st._is_supply("Dish soap", "household") is True


def test_pantry_basic_is_a_supply():
    assert st._is_supply("Coffee", "pantry") is True


def test_fridge_category_ingredient_is_not_a_supply():
    assert st._is_supply("Shrimp", "meat/seafood") is False
    assert st._is_supply("Parsley", "produce") is False


def test_spice_is_not_offered_here_it_already_staples_itself():
    # seed_spice_staples/add_grocery_item already turns a spice into its
    # own staple the moment it's added (2026-09-13 decision) -- offering
    # it again here would be a second, redundant ask.
    assert st._is_supply("Cumin", "pantry") is False


# ------------------------------------------------- add_grocery_item_for_chat ----


def test_supply_item_with_no_staple_is_offered():
    out = tools.add_grocery_item_for_chat("Dish soap", category="household")
    assert out["staple_offer"] == {"item": "Dish soap", "already_staple": False}
    assert _staple_offer_made(out["item_id"]) is True
    # The offer is a question, not a write -- nothing is on the staples
    # table just because it was asked about.
    assert _staples_count() == 0


def test_supply_item_already_a_staple_says_so_and_adds_nothing_twice():
    tools.add_staple("Dish soap", category="household")
    assert _staples_count() == 1

    out = tools.add_grocery_item_for_chat("dish soap", category="household")

    assert out["staple_offer"] == {"item": out["item"], "already_staple": True}
    assert _staples_count() == 1, "already being watched -- nothing duplicated"


def test_recipe_ingredient_is_never_offered():
    shrimp = tools.add_grocery_item_for_chat("Shrimp", category="meat/seafood")
    parsley = tools.add_grocery_item_for_chat("Parsley", category="produce")
    assert "staple_offer" not in shrimp
    assert "staple_offer" not in parsley


def test_offer_is_asked_once_per_grocery_line():
    first = tools.add_grocery_item_for_chat("Dish soap", category="household")
    assert "staple_offer" in first

    # Same line (still 'needed'), asked about again in the same message,
    # or a different message later in the same trip -- merges into the
    # same row and must not raise it a second time.
    again = tools.add_grocery_item_for_chat("Dish soap", category="household")
    assert again["item_id"] == first["item_id"]
    assert "staple_offer" not in again


def test_a_new_trip_after_the_line_is_bought_can_be_offered_again():
    first = tools.add_grocery_item_for_chat("Dish soap", category="household")
    tools.mark_grocery_item(first["item_id"], status="purchased")

    # Bought and off the list -- a fresh mention starts a brand new line,
    # which has never been asked about.
    second = tools.add_grocery_item_for_chat("Dish soap", category="household")
    assert second["item_id"] != first["item_id"]
    assert second["staple_offer"] == {"item": "Dish soap", "already_staple": False}


def test_declining_or_ignoring_the_offer_changes_nothing():
    out = tools.add_grocery_item_for_chat("Dish soap", category="household")
    item_id = out["item_id"]
    # "No answer, or No, changes nothing" -- there is no decline tool to
    # call; the offer having been raised is itself what suppresses the
    # next ask (see test above). Confirm the grocery line and the
    # (non-)existence of a staple are exactly as a plain add would leave
    # them.
    assert _staples_count() == 0
    row = tools.list_grocery_list(status="needed")
    assert [r["item"] for r in row] == ["Dish soap"]
    assert _staple_offer_made(item_id) is True


def test_the_yes_path_creates_the_staple_exactly_like_the_staples_card():
    out = tools.add_grocery_item_for_chat("Dish soap", category="household")
    assert out["staple_offer"]["already_staple"] is False

    # "Yes, keep an eye on dish soap." -> the agent calls add_staple, same
    # as the Staples card's own add flow.
    created = tools.add_staple("Dish soap", category="household")
    assert created["created"] is True
    assert _staples_count() == 1
    # Already on the list this trip (added directly, not because it's
    # "due"), so the cadence starts fresh rather than firing again
    # immediately -- see add_staple's own running_low default.
    assert created["due"] is False


def test_shop_tab_route_is_never_offered_anything():
    """grocery.add_grocery_item -- what the direct-add HTTP route calls --
    must behave exactly as before: no staple_offer key, ever, for any
    caller other than the chat tool wrapper."""
    out = tools.add_grocery_item("Dish soap", category="household")
    assert "staple_offer" not in out
    # And a second Shop-tab add of the very same supply item never
    # retroactively grows one either.
    out2 = tools.add_grocery_item("Dish soap", category="household")
    assert "staple_offer" not in out2


# ------------------------------------------- app.main: turn -> ChatResponse ----


def _turn_with_tool_result(name: str, args: dict, result: dict):
    before = [{"role": "user", "content": "add dish soap"}]
    after = before + [
        {"role": "assistant", "content": [
            {"type": "tool_use", "id": "t1", "name": name, "input": args},
        ]},
        {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "t1", "content": json.dumps(result), "is_error": False},
        ]},
        {"role": "assistant", "content": [{"type": "text", "text": "On the list."}]},
    ]
    return before, after


def test_staple_offer_from_turn_surfaces_a_genuine_offer():
    result = {"item_id": 1, "item": "Dish soap", "quantity": "", "merged": False,
              "units_reconciled": True, "staple_offer": {"item": "Dish soap", "already_staple": False}}
    before, after = _turn_with_tool_result("add_grocery_item", {"item": "Dish soap"}, result)
    assert app_main._staple_offer_from_turn(before, after) == {"item": "Dish soap"}


def test_staple_offer_from_turn_is_none_when_already_a_staple():
    result = {"item_id": 1, "item": "Dish soap", "quantity": "", "merged": False,
              "units_reconciled": True, "staple_offer": {"item": "Dish soap", "already_staple": True}}
    before, after = _turn_with_tool_result("add_grocery_item", {"item": "Dish soap"}, result)
    assert app_main._staple_offer_from_turn(before, after) is None


def test_staple_offer_from_turn_is_none_with_no_offer_at_all():
    result = {"item_id": 1, "item": "Shrimp", "quantity": "", "merged": False, "units_reconciled": True}
    before, after = _turn_with_tool_result("add_grocery_item", {"item": "Shrimp"}, result)
    assert app_main._staple_offer_from_turn(before, after) is None


# ---------------------------------------------------------- end to end ----


class _Usage:
    def __init__(self, output_tokens=0):
        self.input_tokens = 0
        self.cache_read_input_tokens = 0
        self.cache_creation_input_tokens = 0
        self.output_tokens = output_tokens


def _text_block(text):
    return types.SimpleNamespace(type="text", text=text)


def _tool_block(name, tool_input, block_id="tu_1"):
    return types.SimpleNamespace(type="tool_use", name=name, input=tool_input, id=block_id)


class _FakeMessages:
    def __init__(self, responses):
        self._responses = list(responses)

    def create(self, **kwargs):
        return self._responses.pop(0)


def _stub_client(monkeypatch, responses):
    fake = types.SimpleNamespace(messages=_FakeMessages(responses))
    monkeypatch.setattr(agent, "_client", lambda: fake)


def test_the_real_tool_mapping_reaches_the_chat_wrapper():
    # The whole point of swapping the TOOLS entry (app/agent.py) is that
    # Claude's tool name never changes -- only what it dispatches to.
    assert agent.TOOL_FUNCTIONS["add_grocery_item"] is tools.add_grocery_item_for_chat


def test_end_to_end_chat_turn_carries_the_staple_offer(signed_in, monkeypatch):
    _stub_client(monkeypatch, [
        types.SimpleNamespace(
            content=[_tool_block("add_grocery_item", {"item": "Dish soap", "category": "household"})],
            stop_reason="tool_use",
            usage=_Usage(),
        ),
        types.SimpleNamespace(
            content=[_text_block("On the list. Want me to keep an eye on dish soap so you don't have to?")],
            stop_reason="end_turn",
            usage=_Usage(),
        ),
    ])

    res = signed_in.post("/api/chat", json={"session_id": "default", "message": "add dish soap to the list"})
    assert res.status_code == 200
    assert res.json()["staple_offer"] == {"item": "Dish soap"}


def test_end_to_end_chat_turn_has_no_offer_for_a_recipe_ingredient(signed_in, monkeypatch):
    _stub_client(monkeypatch, [
        types.SimpleNamespace(
            content=[_tool_block("add_grocery_item", {"item": "Shrimp", "category": "meat/seafood"})],
            stop_reason="tool_use",
            usage=_Usage(),
        ),
        types.SimpleNamespace(
            content=[_text_block("Shrimp is on your list.")],
            stop_reason="end_turn",
            usage=_Usage(),
        ),
    ])

    res = signed_in.post("/api/chat", json={"session_id": "default", "message": "add shrimp"})
    assert res.status_code == 200
    assert res.json()["staple_offer"] is None
