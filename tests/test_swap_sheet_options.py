"""
The Swap sheet's three picks (Emily, 2026-09-18, boards 19b "swap picks"
and 12 "Check the week").

"Swap" on a Plan row (and "Swap the meal" on Check the week) opens a sheet
with THREE dishes to choose from, not one landing on the slot the moment
the button is tapped — a decision is saved on purpose (DESIGN_SYSTEM §2b
S10). Two halves on the server, both in swap_in_place beside the one-dish
swap they share everything with:

  * `swap_options` — one model call asking for three, each run through the
    same pick_gate before it reaches the sheet; nothing written.
  * `apply_swap_option` — the tap on one: gate again, then apply_pick, the
    same door "Swap · I'll pick" and the chat's change card use.

The model is mocked through the `picker` seam, the way test_swap_in_place
does it.
"""
from __future__ import annotations

import pytest

from app import tools
from app.tools import swap_in_place as sip
from test_swap_in_place import DAY1, DAY2, WEEK_START, _entry_id, _entry_rows, _pick, week  # noqa: F401


def _three():
    return [
        _pick(name="Sheet-Pan Sausages", reason="uses the sausages", prep_time_minutes=5, cook_time_minutes=25),
        _pick(name="Chicken Fajitas", reason="same tortillas", prep_time_minutes=10, cook_time_minutes=15),
        _pick(name="Veggie Quesadillas", reason="no shopping", prep_time_minutes=5, cook_time_minutes=10),
    ]


def _picker(options):
    seen = []

    def picker(context):
        seen.append(context)
        return options

    picker.contexts = seen
    return picker


# ---------- the three ----------

def test_three_picks_come_back_clean_with_minutes_and_nothing_written(week):
    picker = _picker(_three())
    out = tools.swap_options(week, _entry_id(week, DAY1), picker=picker)

    assert out["status"] == "options"
    assert out["replacing"] == "Pork Chops"
    assert [o["meal_name"] for o in out["options"]] == ["Sheet-Pan Sausages", "Chicken Fajitas", "Veggie Quesadillas"]
    assert [o["minutes"] for o in out["options"]] == [30, 25, 15]
    assert [o["reason"] for o in out["options"]] == ["uses the sausages", "same tortillas", "no shopping"]
    # The prompt is the one-dish swap's, with the outgoing dish on avoid.
    assert picker.contexts[0]["replacing"] == "Pork Chops"
    assert "Pork Chops" in picker.contexts[0]["avoid"]
    # Nothing written: the dinner is untouched and no recipe was saved.
    assert [r["meal"] for r in _entry_rows(week, DAY1)] == ["Pork Chops"]
    assert not any(r["name"] == "Sheet-Pan Sausages" for r in tools.list_recipes())


def test_a_pick_the_house_cannot_have_never_reaches_the_sheet(week):
    tools.set_member_dietary_restrictions("Vineeth", ["peanut allergy"])
    options = [_pick(name="Peanut Noodles", reason="quick")] + _three()
    out = tools.swap_options(week, _entry_id(week, DAY1), picker=_picker(options))

    names = [o["meal_name"] for o in out["options"]]
    assert "Peanut Noodles" not in names
    assert len(names) == 3, "the gate drops the clash and the next honest pick fills its place"


def test_a_duplicate_or_the_outgoing_dish_is_not_offered(week):
    options = [_pick(name="Pork Chops"), _pick(name="Chicken Fajitas"), _pick(name="chicken fajitas"),
               _pick(name="Veggie Quesadillas")]
    out = tools.swap_options(week, _entry_id(week, DAY1), picker=_picker(options))

    assert [o["meal_name"] for o in out["options"]] == ["Chicken Fajitas", "Veggie Quesadillas"]


def test_nothing_usable_is_a_plain_refusal(week):
    out = tools.swap_options(week, _entry_id(week, DAY1), picker=_picker([{}, {"reason": "no name"}]))

    assert out["status"] == "refused"
    assert out["message"] == sip.REFUSAL
    assert "!" not in out["message"]


# ---------- the tap ----------

def test_picking_one_swaps_that_meal_only_through_the_same_door(week):
    options = tools.swap_options(week, _entry_id(week, DAY1), picker=_picker(_three()))["options"]
    out = tools.apply_swap_option(week, _entry_id(week, DAY1), options[1])

    assert out["status"] == "swapped"
    assert out["meal"] == "Chicken Fajitas" and out["replaced"] == "Pork Chops"
    assert out["day"]["dinner"]["title"] == "Chicken Fajitas"
    assert [r["meal"] for r in _entry_rows(week, DAY1)] == ["Chicken Fajitas"]
    assert [r["meal"] for r in _entry_rows(week, DAY2)] == ["Chili"], "the other nights are untouched"
    # The same undo note apply_pick writes for "Swap · I'll pick".
    assert out["can_undo"] is True
    put_back = tools.undo_meal_swap(week, out["entry_id"])
    assert put_back["meal"] == "Pork Chops"


def test_the_gate_runs_again_on_the_tap(week):
    options = tools.swap_options(week, _entry_id(week, DAY1), picker=_picker(_three()))["options"]
    # The house changed between the sheet opening and the tap.
    tools.set_member_dietary_restrictions("Emily", ["chicken"])
    out = tools.apply_swap_option(week, _entry_id(week, DAY1), options[1])

    assert out["status"] == "refused"
    assert [r["meal"] for r in _entry_rows(week, DAY1)] == ["Pork Chops"]


# ---------- over the wire ----------

def test_the_routes_hand_the_picks_out_and_take_one_back(signed_in, week, monkeypatch):
    monkeypatch.setattr(tools, "swap_options",
                        lambda plan_id, entry_id: sip.swap_options(plan_id, entry_id, picker=_picker(_three())))
    entry_id = _entry_id(week, DAY1)
    res = signed_in.get(f"/api/week/{WEEK_START}/swap-options", params={"entry_id": entry_id})
    assert res.status_code == 200
    body = res.json()
    assert body["status"] == "options" and len(body["options"]) == 3

    res = signed_in.post(f"/api/week/{WEEK_START}/swap-pick",
                         json={"entry_id": entry_id, "option": body["options"][2]})
    assert res.status_code == 200
    assert res.json()["status"] == "swapped"
    assert res.json()["day"]["dinner"]["title"] == "Veggie Quesadillas"


def test_the_routes_404_on_a_meal_that_is_not_on_that_week(signed_in, week):
    assert signed_in.get(f"/api/week/{WEEK_START}/swap-options", params={"entry_id": 99999}).status_code == 404
    res = signed_in.post(f"/api/week/{WEEK_START}/swap-pick", json={"entry_id": 99999, "option": _pick()})
    assert res.status_code == 404


@pytest.mark.parametrize("bad", [None, "", "   "])
def test_a_pick_with_no_name_is_refused_on_the_tap(week, bad):
    out = tools.apply_swap_option(week, _entry_id(week, DAY1), {"meal_name": bad, "reason": "x"})
    assert out["status"] == "refused"
    assert [r["meal"] for r in _entry_rows(week, DAY1)] == ["Pork Chops"]
