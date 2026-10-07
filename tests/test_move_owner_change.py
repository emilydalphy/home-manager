"""
Every move has an owner (slice 2) — change who's on it with one tap, or by
saying so in chat. app/tools/move_owner.set_move_owner / change_move_owner,
the `move_owner_overrides` table, POST /api/today/moves/{id}/owner, and the
"Who's on it?" door on a Today row.

Loop Board, 2026-10-07: *as an adult in the household, I want to change
whose cook or shop something is with one tap — "actually I've got tonight"
— so that the plan matches what's really happening without either of us
filling in a form.*

What is under test, and what it is evidence OF:

1. CHANGE, CHANGE BACK, NOBODY — the three things the chooser offers, each
   read back through the same resolver every reader goes through.
2. NOT A STANDING RULE — the next cook is untouched.
3. THE CHAT PATH — "Vineeth's cooking tonight", "me", an unknown name.
4. THE SCREEN — the door is drawn where there is something to change and
   nowhere else, and Cook stops naming the standing cook when Today said
   "Nobody yet".
"""
from __future__ import annotations

import json
import re

import pytest

import nodeharness
from app import agent, tools
from app.db import get_conn
from app.tools import _shared
from app.tools import moves as _moves
from app.tools import move_owner as _move_owner

from test_move_owner import (  # the slice-1 seeding, reused rather than copied
    DAYS, ISO_TODAY, _adults, _at, _cook_card, _cook_meal, _entry, _move,
    _needs_node, _node, _plan, _prep_row, _recipe,
)


def _ids() -> dict[str, int]:
    return {m["name"]: m["id"] for m in tools.list_members()}


def _cook(day: str) -> dict:
    return next(m for m in _moves.moves_for_day(day, now=_at()) if m["kind"] == "cook")


def _two_adults_one_cook():
    _adults("Emily", "Vineeth")
    _recipe()
    plan_id = _plan()
    tools.set_cooking_role("one_person", who="Emily")
    return plan_id


# ---------- 1. change, change back, nobody ----------

def test_one_tap_puts_somebody_else_on_tonights_cook():
    """CATCH. Emily cooks every night by default; Vineeth takes tonight."""
    _two_adults_one_cook()
    cook = _cook(ISO_TODAY)
    assert cook["owner_name"] == "Emily"

    out = tools.set_move_owner(cook["id"], _ids()["Vineeth"], ISO_TODAY)

    assert (out["owner"], out["owner_name"]) == (_ids()["Vineeth"], "Vineeth")
    assert _cook(ISO_TODAY)["owner_name"] == "Vineeth"
    assert out["said"] == "Vineeth’s on Chicken Skewers"
    # What Undo needs to put it back exactly.
    assert out["previous"] == {"owner": _ids()["Emily"], "owner_name": "Emily",
                               "had_override": False, "override_member_id": None}


def test_changing_it_back_and_clearing_it_both_land_on_emily():
    """CATCH. Picking Emily again names Emily; Undo's clear shows the
    household's default again, which is also Emily."""
    _two_adults_one_cook()
    cook_id = _cook(ISO_TODAY)["id"]
    ids = _ids()

    tools.set_move_owner(cook_id, ids["Vineeth"], ISO_TODAY)
    back = tools.set_move_owner(cook_id, ids["Emily"], ISO_TODAY)
    assert back["owner_name"] == "Emily"
    assert back["previous"]["had_override"] is True
    assert back["previous"]["override_member_id"] == ids["Vineeth"]

    tools.set_move_owner(cook_id, ids["Vineeth"], ISO_TODAY)
    cleared = tools.set_move_owner(cook_id, None, ISO_TODAY, clear=True)
    assert cleared["status"] == "cleared"
    assert _cook(ISO_TODAY)["owner_name"] == "Emily"
    conn = get_conn()
    left = conn.execute("SELECT COUNT(*) FROM move_owner_overrides").fetchone()[0]
    conn.close()
    assert left == 0


def test_nobody_yet_is_an_answer_and_beats_the_default():
    """CATCH. "Nobody yet" is said on purpose — different from no row."""
    _two_adults_one_cook()
    cook_id = _cook(ISO_TODAY)["id"]

    out = tools.set_move_owner(cook_id, None, ISO_TODAY)

    assert (out["owner"], out["owner_name"]) == (None, None)
    assert (_cook(ISO_TODAY)["owner"], _cook(ISO_TODAY)["owner_name"]) == (None, None)
    assert out["said"] == "Nobody’s on Chicken Skewers yet"


def test_the_fridge_move_for_a_cook_follows_whoever_took_the_cook():
    """CATCH. Slice 1 gave a thaw its cook's owner; a cook somebody took
    over takes its thaw with it, and a thaw's own word still wins."""
    plan_id = _two_adults_one_cook()
    ids = _ids()
    task_id = _prep_row(plan_id, DAYS[2], _entry(DAYS[3]))
    tools.set_move_owner(f"cook:{_entry(DAYS[3])}", ids["Vineeth"], DAYS[3])

    fridge = {m["id"]: m for m in _moves.moves_for_day(DAYS[2], now=_at())}[f"fridge:{task_id}"]
    assert fridge["owner_name"] == "Vineeth"

    tools.set_move_owner(f"fridge:{task_id}", ids["Emily"], DAYS[2])
    fridge = {m["id"]: m for m in _moves.moves_for_day(DAYS[2], now=_at())}[f"fridge:{task_id}"]
    assert fridge["owner_name"] == "Emily"


def test_somebody_can_be_put_on_the_shop():
    """The shop has no default (slice 1: there is no "who shops" answer),
    but a person SAYING they'll do it is not a guess."""
    _two_adults_one_cook()
    tools.add_grocery_item("Milk", "1")
    shop = next((m for m in _moves.moves_for_day(ISO_TODAY) if m["kind"] == "shop"), None)
    if shop is None:
        pytest.skip("no shop move on this day of the week for the seed")
    assert shop["owner_name"] is None
    tools.set_move_owner(shop["id"], _ids()["Vineeth"], ISO_TODAY)
    shop = next(m for m in _moves.moves_for_day(ISO_TODAY) if m["id"] == shop["id"])
    assert shop["owner_name"] == "Vineeth"


# ---------- 2. not a standing rule ----------

def test_it_changes_that_move_only_never_the_household_answer():
    """CATCH. Tomorrow is still Emily's, and the rhythm answer is untouched."""
    _two_adults_one_cook()
    tools.set_move_owner(_cook(ISO_TODAY)["id"], _ids()["Vineeth"], ISO_TODAY)

    assert [_cook(d)["owner_name"] for d in DAYS] == \
        ["Emily", "Emily", "Vineeth", "Emily", "Emily"]
    assert tools.get_household_rhythm()["cooking_role"]["who"] == "Emily"


def test_a_word_on_one_night_does_not_follow_the_dish_to_another_night():
    """GUARD. The row is keyed by the day the move sat on: the same move id
    read on another day does not pick it up."""
    _two_adults_one_cook()
    cook_id = _cook(ISO_TODAY)["id"]
    tools.set_move_owner(cook_id, _ids()["Vineeth"], ISO_TODAY)
    owners = _move_owner.resolve()
    assert owners.override(cook_id, ISO_TODAY) == (_ids()["Vineeth"], "Vineeth")
    assert owners.override(cook_id, DAYS[0]) is None


def test_a_reheat_and_a_stranger_are_refused():
    _two_adults_one_cook()
    cook_id = _cook(ISO_TODAY)["id"]
    with pytest.raises(ValueError):
        tools.set_move_owner("reheat:999", None, ISO_TODAY)
    with pytest.raises(ValueError):
        tools.set_move_owner(cook_id, 987654, ISO_TODAY)
    tools.add_member("Kid")  # not an adult
    with pytest.raises(ValueError):
        tools.set_move_owner(cook_id, _ids()["Kid"], ISO_TODAY)
    assert _cook(ISO_TODAY)["owner_name"] == "Emily", "nothing was written"


# ---------- 3. the chat path ----------

def test_the_chat_tool_is_registered_where_the_model_and_the_package_see_it():
    assert agent.TOOL_FUNCTIONS["change_move_owner"] is tools.change_move_owner
    assert any(t["name"] == "change_move_owner" for t in agent.TOOL_DEFINITIONS)
    assert "change_move_owner" not in agent.CHORES_TOOLS


def test_vineeths_cooking_tonight_said_in_chat():
    """CATCH — the card's own sentence."""
    _two_adults_one_cook()
    out = agent.TOOL_FUNCTIONS["change_move_owner"](who="Vineeth")
    assert out["owner_name"] == "Vineeth"
    assert _cook(ISO_TODAY)["owner_name"] == "Vineeth"
    # And back.
    agent.TOOL_FUNCTIONS["change_move_owner"](who="emily")
    assert _cook(ISO_TODAY)["owner_name"] == "Emily"


def test_ive_got_tonight_means_whoever_is_talking():
    _two_adults_one_cook()
    with _shared.use_member(_ids()["Vineeth"]):
        out = tools.change_move_owner("me")
    assert out["owner_name"] == "Vineeth"


def test_nobody_and_an_unknown_name_in_chat():
    _two_adults_one_cook()
    assert tools.change_move_owner("nobody")["owner_name"] is None
    with pytest.raises(ValueError) as e:
        tools.change_move_owner("Priya")
    assert "Emily" in str(e.value) and "Vineeth" in str(e.value)
    assert _cook(ISO_TODAY)["owner_name"] is None, "the unknown name changed nothing"


# ---------- over HTTP ----------

def test_the_tap_route_changes_it_and_hands_back_the_day(client, signed_in):
    _two_adults_one_cook()
    cook_id = _cook(ISO_TODAY)["id"]
    res = client.post(f"/api/today/moves/{cook_id}/owner",
                      json={"member_id": _ids()["Vineeth"], "date": ISO_TODAY})
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["change"]["owner_name"] == "Vineeth"
    assert next(m for m in body["moves"] if m["id"] == cook_id)["owner_name"] == "Vineeth"
    again = client.get("/api/today/moves").json()
    assert next(m for m in again["moves"] if m["id"] == cook_id)["owner_name"] == "Vineeth"

    bad = client.post(f"/api/today/moves/{cook_id}/owner", json={"member_id": 987654})
    assert bad.status_code == 400


# ---------- 4. the screen ----------

_ADULTS = "var shellWho = { adults: [{id: 1, name: 'Emily'}, {id: 2, name: 'Vineeth'}] };\n"


@_needs_node
def test_a_row_still_to_do_has_the_door_and_a_reheat_or_done_row_does_not():
    rows = {
        "cook": _move("cook", "cook:1", "Chicken Skewers", meta="35 min", owner=1, owner_name="Emily"),
        "cook_nobody": _move("cook", "cook:2", "Salad", meta="10 min"),
        "done": _move("cook", "cook:3", "Toast", meta="5 min", done=True),
        "reheat": _move("reheat", "reheat:4", "Leftovers", meta="reheat"),
    }
    out = _node(_ADULTS + "console.log(JSON.stringify({%s}));" % ",".join(
        "%s: dayStripNodeHtml(%s, %s)" % (k, json.dumps(m), "'done'" if m["done"] else "'later'")
        for k, m in rows.items()))
    assert 'data-move-who="cook:1"' in out["cook"]
    assert 'aria-label="Who’s on it? Emily"' in out["cook"]
    assert 'aria-label="Who’s on it? Nobody yet"' in out["cook_nobody"]
    assert "data-move-who" not in out["done"]
    assert "data-move-who" not in out["reheat"]
    # The name is still the meta line's, not the door's.
    assert re.search(r'<span class="day-node-meta">Emily’s cooking · 35 min</span>', out["cook"])


@_needs_node
def test_no_adults_on_record_no_door():
    move = _move("cook", "cook:1", "Chicken Skewers", meta="35 min")
    html = _node("var shellWho = { adults: [] };\n"
                 "console.log(JSON.stringify(dayStripNodeHtml(%s, 'later')));" % json.dumps(move))
    assert "data-move-who" not in html


@_needs_node
def test_a_shop_has_one_door_and_says_whose_it_is_on_its_first_stop():
    move = _move("shop", "shop:x", "Shop for tonight", tickable=False, owner=2, owner_name="Vineeth",
                 stops=[{"store": "Costco", "count": 2, "items": ["orzo", "salmon"]},
                        {"store": "Metro", "count": 1, "items": ["milk"]}])
    html = _node(_ADULTS + "console.log(JSON.stringify(todayShopRowsHtml(%s, 'later')));" % json.dumps(move))
    assert html.count("data-move-who") == 1
    assert "Vineeth’s · orzo, salmon" in html
    assert html.count("Vineeth’s") == 1


@_needs_node
def test_cook_does_not_paint_the_standing_cook_over_nobody_yet():
    """CATCH. Under one_person Cook's card fell back to data.cook_name
    whenever the move named nobody — which, once "Nobody yet" can be said,
    would undo it on the very next screen."""
    meals = [_cook_meal(12, "Chicken Skewers")]
    move = _move("cook", "cook:12", "Chicken Skewers", entry_id=12, meta="35 min",
                 chips=["35 min", "Start by 5:55"], time_label="6:30 tonight")
    html = _cook_card(meals, [move], {"cook_name": "Emily"})
    assert "EMILY" not in html
