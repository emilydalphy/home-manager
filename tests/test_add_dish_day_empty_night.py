"""
QA card, 2026-09-15: "Check the week: '+ nights' says there's no other
dinner when five nights are empty." (The "+" picker itself went on
2026-09-18; the server half below still holds.)

reviewAddDayOptions (static/shell.js) only ever offered a night that
already held a `planned` or `open` entry — a night with NO row at all (the
day card's own "Nothing yet") was skipped outright, same as a day the dish
already covers. A household with one cook and six empty dinners got "There
's no other dinner this week to put it on," which was false: six nights
were free, the picker just never looked at them.

Two halves, same as every other file in this batch:
  1. THE WRITE. tools.add_dish_day gets a `target_date` path alongside its
     existing `target_entry_id` one, for a target with no row to name by
     id. It resolves the (date, slot) fresh and applies exactly the same
     refusals (planned_empty, cooked, already-this-dish) a target_entry_id
     would get, so a night that filled in between the screen drawing and
     the tap is never silently overwritten.
  2. THE SCREEN'S OWN FUNCTIONS. reviewAddDayOptions now offers a slot with
     no entry, labelled "Nothing yet", and puts every such night ahead of
     the nights that already hold a meal (the card's own acceptance
     criterion) — proven under node against plain objects, not by grepping
     shell.js for the right words.
"""
from __future__ import annotations

import datetime
import json
import shutil

import nodeharness
from pathlib import Path

import pytest

from app import tools
from app.db import get_conn


TODAY = datetime.date.today()
WEEK_START = TODAY.isoformat()
D0 = WEEK_START
D1 = (TODAY + datetime.timedelta(days=1)).isoformat()
D2 = (TODAY + datetime.timedelta(days=2)).isoformat()
D3 = (TODAY + datetime.timedelta(days=3)).isoformat()
D4 = (TODAY + datetime.timedelta(days=4)).isoformat()
D5 = (TODAY + datetime.timedelta(days=5)).isoformat()
D6 = (TODAY + datetime.timedelta(days=6)).isoformat()

REPO = Path(__file__).resolve().parent.parent
SHELL_JS = (REPO / "static" / "shell.js").read_text(encoding="utf-8")


# ---------------------------------------------------------------- helpers

def _plan() -> int:
    return tools.create_weekly_plan(WEEK_START)["weekly_plan_id"]


def _ids(day: str, slot: str) -> list[int]:
    conn = get_conn()
    rows = conn.execute(
        "SELECT id FROM meal_plan_entries WHERE household_id = ? AND date = ? AND slot = ? "
        "ORDER BY id ASC", (tools.household_id(), day, slot),
    ).fetchall()
    conn.close()
    return [r["id"] for r in rows]


def _state(day: str, slot: str) -> list[tuple]:
    conn = get_conn()
    rows = conn.execute(
        "SELECT mpe.slot_state, COALESCE(r.name, mpe.freeform_meal) AS meal "
        "FROM meal_plan_entries mpe LEFT JOIN recipes r ON r.id = mpe.recipe_id "
        "WHERE mpe.household_id = ? AND mpe.date = ? AND mpe.slot = ? ORDER BY mpe.id ASC",
        (tools.household_id(), day, slot),
    ).fetchall()
    conn.close()
    return [(r["slot_state"], r["meal"]) for r in rows]


# --------------------------------------------------- THE WRITE (server side)

def test_a_genuinely_empty_night_has_no_row_to_begin_with():
    """The premise the rest of this file leans on: create_weekly_plan makes
    no meal_plan_entries rows at all, so a day nobody has planned into is
    not a stand-in row in some 'open' or 'empty' state — it is nothing."""
    _plan()
    assert _ids(D1, "dinner") == []


def test_linking_an_empty_night_creates_the_entry():
    """The heart of the card: stretching a roast over an empty Friday is
    one tap, and that tap has to actually write something."""
    plan = _plan()
    tools.plan_meal(D0, "Sunday Roast", slot="dinner", weekly_plan_id=plan)

    out = tools.add_dish_day(plan, _ids(D0, "dinner")[0], target_date=D3)

    assert out["status"] == "added"
    assert out["dish"] == "Sunday Roast"
    assert _state(D3, "dinner") == [("planned", "Sunday Roast")]
    # The night it was stretched FROM is untouched.
    assert _state(D0, "dinner") == [("planned", "Sunday Roast")]


def test_nothing_was_displaced_by_filling_a_blank():
    """An empty night is not a dish being lost — the toast must not invent
    one, the same guarantee an open slot already gets."""
    plan = _plan()
    tools.plan_meal(D0, "Sunday Roast", slot="dinner", weekly_plan_id=plan)
    out = tools.add_dish_day(plan, _ids(D0, "dinner")[0], target_date=D3)
    assert out["replaced"] is None
    assert out["unchained"] == []


def test_the_shape_is_the_same_the_id_path_already_hands_back():
    """get_week_menu's own day dict, so the Review screen can splice this
    one in exactly as it does the target_entry_id path."""
    plan = _plan()
    tools.plan_meal(D0, "Sunday Roast", slot="dinner", weekly_plan_id=plan)
    out = tools.add_dish_day(plan, _ids(D0, "dinner")[0], target_date=D3)
    assert out["day"]["date"] == D3
    assert out["day"]["dinner"]["title"] == "Sunday Roast"


def test_the_ingredients_follow_the_same_rule_an_approved_week_already_has():
    """No second implementation of "buy for the new meal" — this still
    composes swap_meal_in_plan/plan_meal, so an approved week's grocery
    list picks up the extra night exactly as it would for any other add."""
    plan = _plan()
    tools.add_recipe("Sunday Roast", ingredients=[{"item": "Chicken", "qty": "1"}])
    tools.plan_meal(D0, "Sunday Roast", slot="dinner", weekly_plan_id=plan)
    tools.approve_weekly_plan(plan, approved_by="Emily")

    tools.add_dish_day(plan, _ids(D0, "dinner")[0], target_date=D3)

    items = {i["item"] for i in tools.list_grocery_list()}
    assert "Chicken" in items


def test_a_night_nobody_is_home_is_never_a_day_to_plan_into_even_by_date():
    """The rule that has already caused three bugs when it was got wrong,
    checked from the OTHER side: a caller naming the night by date must be
    refused exactly like one naming it by id."""
    plan = _plan()
    tools.plan_meal(D0, "Sunday Roast", slot="dinner", weekly_plan_id=plan)
    tools.plan_slot_empty(plan, D3, "dinner", "You're out — I've planned nothing.")

    with pytest.raises(tools.SlotRefused):
        tools.add_dish_day(plan, _ids(D0, "dinner")[0], target_date=D3)
    assert _state(D3, "dinner") == [("planned_empty", None)]


def test_a_night_that_filled_in_since_the_screen_drew_it_is_not_overwritten():
    """The picker was drawn from a snapshot. If a chat swap or another tab
    planted a real dish on that night in the gap, tapping "Nothing yet"
    must not silently clobber it — same "already has it" refusal a stale
    target_entry_id tap already gets."""
    plan = _plan()
    tools.plan_meal(D0, "Sunday Roast", slot="dinner", weekly_plan_id=plan)
    tools.plan_meal(D3, "Sunday Roast", slot="dinner", weekly_plan_id=plan)

    with pytest.raises(tools.SlotRefused) as exc:
        tools.add_dish_day(plan, _ids(D0, "dinner")[0], target_date=D3)
    assert "already has it" in str(exc.value)
    assert _state(D3, "dinner") == [("planned", "Sunday Roast")]


def test_passing_neither_or_both_targets_is_refused():
    plan = _plan()
    tools.plan_meal(D0, "Sunday Roast", slot="dinner", weekly_plan_id=plan)
    entry = _ids(D0, "dinner")[0]
    with pytest.raises(ValueError):
        tools.add_dish_day(plan, entry)
    with pytest.raises(ValueError):
        tools.add_dish_day(plan, entry, target_entry_id=entry, target_date=D3)


def test_the_route_creates_the_entry_from_a_date_alone(signed_in):
    plan = _plan()
    tools.plan_meal(D0, "Sunday Roast", slot="dinner", weekly_plan_id=plan)

    res = signed_in.post(
        f"/api/week/{WEEK_START}/add-dish-day",
        json={"entry_id": _ids(D0, "dinner")[0], "target_date": D3},
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["status"] == "added"
    assert body["replaced"] is None
    assert body["day"]["dinner"]["title"] == "Sunday Roast"
    assert _state(D3, "dinner") == [("planned", "Sunday Roast")]


# The "+" picker (reviewAddDayOptions / reviewAddPickerHtml) went with the
# stepper on 2026-09-18 — Check the week is a carousel of day cards now. The
# server's add-dish-day above still takes an empty night by date.
