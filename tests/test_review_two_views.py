"""
Review a week, part 1: the two views (Emily's approved design, 2026-09-09,
Option A). SINCE 2026-09-18 the two views are gone — Check the week is a
carousel of day cards (tests/test_plan_cards_2026_09_18.py) — and this file
keeps only the server half: drop-dish-day, which the stepper used to call.

A fourth step of the Meals tab — "Check the week" — that reads one week two
ways rather than putting more of it on one screen. Julia's report was that a
week is too much to take in: three meals and two snacks across seven days is
35 things, and a list of all 35 is the problem rather than the answer.

  * WHAT WE'RE EATING  grouped by meal type, each dish ONCE with the number
    of days it covers, a stepper per row and a Change button.
  * WHICH DAYS         one tile per night carrying dinner (seven tiles
    since 2026-09-12, see tests/test_week_seven_tiles.py), and a night
    nobody is home saying so and offering nothing.

Two kinds of test, and the split is the one tests/test_meals_week_day_meal.py
already draws:

  * BEHAVIOUR, for the one new write — taking a day away from a dish, which
    must never leave a slot absent — and for the route in front of it.
  * THE SCREEN'S OWN FUNCTIONS, RUN, under node against plain dicts. Not
    source markers: the whole risk in a "dish appears once with an accurate
    count" screen is the counting, and a test that greps shell.js for the
    right words cannot see a number at all.
"""
from __future__ import annotations

import datetime
import json
import re
import shutil

import nodeharness
from pathlib import Path

import pytest

from conftest import household_today

from app import tools
from app.db import get_conn


# The HOUSEHOLD's today, never the process's. Both halves of the Review
# stepper refuse a night that has already gone by — add_dish_day since
# 2026-09-16, drop_dish_from_day with the sibling card that closed the same
# hole in the "−" — and both read the household's clock. The two clocks are
# different days for part of every UTC day, so seeded off date.today() D1 is
# the household's YESTERDAY under a straddling runner and every drop below
# is correctly refused: the app right, the harness wrong. Measured at
# TZ=Pacific/Niue — 6 red here before this line, 0 after.
TODAY = household_today()
WEEK_START = (TODAY - datetime.timedelta(days=1)).isoformat()
D0 = WEEK_START
D1 = (TODAY + datetime.timedelta(days=0)).isoformat()
D2 = (TODAY + datetime.timedelta(days=1)).isoformat()

REPO = Path(__file__).resolve().parent.parent
SHELL_JS = (REPO / "static" / "shell.js").read_text(encoding="utf-8")
SHELL_CSS = (REPO / "static" / "shell.css").read_text(encoding="utf-8")


# ---------------------------------------------------------------- helpers

def _plan() -> int:
    return tools.create_weekly_plan(WEEK_START)["weekly_plan_id"]


def _entry_id(day: str, slot: str) -> int:
    conn = get_conn()
    row = conn.execute(
        "SELECT id FROM meal_plan_entries WHERE household_id = ? AND date = ? AND slot = ? "
        "ORDER BY id DESC",
        (tools.household_id(), day, slot),
    ).fetchone()
    conn.close()
    return row["id"]


def _snack_ids(day: str) -> list:
    conn = get_conn()
    rows = conn.execute(
        "SELECT id FROM meal_plan_entries WHERE household_id = ? AND date = ? AND slot = 'snack' "
        "ORDER BY id ASC", (tools.household_id(), day)).fetchall()
    conn.close()
    return [r["id"] for r in rows]


def _entry_id_asc(day: str, slot: str) -> int:
    conn = get_conn()
    row = conn.execute(
        "SELECT id FROM meal_plan_entries WHERE household_id = ? AND date = ? AND slot = ? "
        "ORDER BY id ASC", (tools.household_id(), day, slot)).fetchone()
    conn.close()
    return row["id"]


def _slot_state(day: str, slot: str) -> str:
    conn = get_conn()
    row = conn.execute(
        "SELECT slot_state FROM meal_plan_entries WHERE household_id = ? AND date = ? AND slot = ?",
        (tools.household_id(), day, slot),
    ).fetchone()
    conn.close()
    return row["slot_state"] if row else ""


def _rows_on(day: str, slot: str) -> int:
    conn = get_conn()
    n = conn.execute(
        "SELECT COUNT(*) c FROM meal_plan_entries WHERE household_id = ? AND date = ? AND slot = ?",
        (tools.household_id(), day, slot),
    ).fetchone()["c"]
    conn.close()
    return n


# ------------------------------------------- the one new write: one fewer day

def test_taking_a_day_off_a_dish_leaves_the_slot_open_and_never_absent():
    """
    The whole hazard in a stepper that goes down. A slot is one of three
    states, never present-or-missing, and three separate bugs in this repo
    have come from a slot that stopped existing. So the day the household
    takes back is handed to them as a question, not deleted.
    """
    plan = _plan()
    tools.plan_meal(D1, "Chicken Traybake", slot="dinner", weekly_plan_id=plan)
    out = tools.drop_dish_from_day(plan, _entry_id(D1, "dinner"))

    assert out["status"] == "dropped"
    assert out["dish"] == "Chicken Traybake"
    assert _rows_on(D1, "dinner") == 1, "the slot must still hold exactly one row"
    assert _slot_state(D1, "dinner") == "open"


def test_the_open_slot_names_the_household_s_own_instruction_as_the_reason():
    """plan_slot_open refuses a reasonless open slot on purpose — the reason
    is what the Day step shows in place of a meal, and it has to read as the
    constraint that caused this, not as an apology."""
    plan = _plan()
    tools.plan_meal(D1, "Chicken Traybake", slot="dinner", weekly_plan_id=plan)
    out = tools.drop_dish_from_day(plan, _entry_id(D1, "dinner"))

    assert "Chicken Traybake" in out["open_reason"]
    assert "sorry" not in out["open_reason"].lower()
    day = [d for d in tools.get_week_menu(plan)["days"] if d["date"] == D1][0]
    assert day["dinner"]["state"] == "open"
    assert day["dinner"]["open_reason"] == out["open_reason"]


def test_it_hands_back_the_changed_day_in_the_shape_the_screen_already_reads():
    """get_week_menu's own day dict, exactly as the in-place swap answers —
    which is what lets the Review screen splice one day into the week it is
    holding and have BOTH views right with no refetch."""
    plan = _plan()
    tools.plan_meal(D1, "Chicken Traybake", slot="dinner", weekly_plan_id=plan)
    tools.plan_meal(D1, "Oatmeal", slot="breakfast", weekly_plan_id=plan)
    out = tools.drop_dish_from_day(plan, _entry_id(D1, "dinner"))

    assert out["day"]["date"] == D1
    assert out["day"]["dinner"]["state"] == "open"
    # The rest of the day comes back untouched, not just the slot that moved.
    assert out["day"]["breakfast"]["title"] == "Oatmeal"


def test_a_slot_with_nothing_on_it_is_not_something_to_take_a_day_off():
    plan = _plan()
    tools.plan_slot_empty(plan, D1, "dinner", "You're out — nothing planned.")
    with pytest.raises(ValueError):
        tools.drop_dish_from_day(plan, _entry_id(D1, "dinner"))
    # And the deliberate empty row is still there, untouched.
    assert _slot_state(D1, "dinner") == "planned_empty"


def test_an_entry_from_another_week_is_refused_rather_than_quietly_dropped():
    """Household- and plan-scoped both, the same rule the in-place swap
    follows — an id from somewhere else is a refusal, not an edit of
    somebody else's dinner."""
    plan_a = _plan()
    tools.plan_meal(D1, "Chicken Traybake", slot="dinner", weekly_plan_id=plan_a)
    entry = _entry_id(D1, "dinner")
    plan_b = tools.create_weekly_plan(
        (TODAY + datetime.timedelta(days=14)).isoformat()
    )["weekly_plan_id"]
    with pytest.raises(ValueError):
        tools.drop_dish_from_day(plan_b, entry)
    assert _slot_state(D1, "dinner") == "planned"


def test_taking_the_reheat_night_off_a_chain_leaves_the_cook_night_alone():
    """
    A dish cooked once and eaten twice is ONE dish covering two days, so its
    stepper goes 2 -> 1 by taking back the reheat. clear_plan_slot unlinks
    the chain on the way (that is its job, not this function's) — the point
    of the test is that the night somebody actually cooks survives.
    """
    plan = _plan()
    tools.plan_meal(D1, "Beef Chili", slot="dinner", weekly_plan_id=plan)
    tools.plan_meal(D2, "Leftovers from Beef Chili", slot="dinner", weekly_plan_id=plan)
    out = tools.drop_dish_from_day(plan, _entry_id(D2, "dinner"))

    assert out["date"] == D2
    assert _slot_state(D2, "dinner") == "open"
    assert _slot_state(D1, "dinner") == "planned"


def test_stepping_one_snack_down_leaves_the_day_s_OTHER_snack_alone():
    """
    BLOCKER, found in review and reproduced through the real route. A day
    holds TWO rows at slot='snack' by default, and the first version of this
    composed clear_plan_slot, which deletes every row in a slot — so one tap
    on the Apple row's minus destroyed the Greek yogurt beside it, grocery
    reversal and all, and left the day on one open slot when the household
    had asked for two snacks. swap_meal_in_plan's own docstring had already
    written the rule down; removal is by ID now, the way that function does
    it.
    """
    plan = _plan()
    tools.plan_meal(D1, "Apple and peanut butter", slot="snack", weekly_plan_id=plan)
    tools.plan_meal(D1, "Greek yogurt", slot="snack", weekly_plan_id=plan)
    apple = _snack_ids(D1)[0]

    tools.drop_dish_from_day(plan, apple)

    day = [d for d in tools.get_week_menu(plan)["days"] if d["date"] == D1][0]
    titles = [(s["title"], s["state"]) for s in day["snacks"]]
    # The day still has two snack slots: one real snack, one handed back.
    assert ("Greek yogurt", "planned") in titles, titles
    assert sum(1 for _, state in titles if state == "open") == 1, titles
    assert not any(t == "Apple and peanut butter" for t, _ in titles), titles


def test_a_dish_that_feeds_another_night_is_cooked_on_that_night_instead():
    """
    INVERTED 2026-09-24. This used to assert the refusal — "Beef Chili on
    Monday also feeds Tuesday — change that first and I'll take this one
    off" — and nothing written. Emily's standing rule of 2026-09-22 is that
    there is no such answer ("the job of Pomona is to do all that planning
    work"), so the "−" re-plans the fed night itself, by the same rule the
    night off follows for the identical shape: the cook moves onto the
    first night it was feeding and the stepped-down night comes back as a
    question. The gap the old refusal was protecting against — a night left
    holding a reheat of a batch nobody cooks — is closed by the move rather
    than by declining to move.
    """
    plan = _plan()
    tools.plan_meal(D1, "Beef Chili", slot="dinner", weekly_plan_id=plan)
    tools.plan_meal(D2, "Beef Chili", slot="dinner", weekly_plan_id=plan)
    source, target = _entry_id_asc(D1, "dinner"), _entry_id_asc(D2, "dinner")
    tools.set_cook_ahead(source, [target])

    out = tools.drop_dish_from_day(plan, source)

    assert out["status"] == "dropped"
    assert out["moved_to"] == D2
    assert "change that first" not in out.get("said", "")
    assert _slot_state(D1, "dinner") == "open"
    # The cook is on Tuesday now — the row that was reheating there is gone,
    # and the dish is on exactly one night rather than none.
    assert _slot_state(D2, "dinner") == "planned"
    assert _rows_on(D2, "dinner") == 1


def test_the_route_takes_one_day_off_a_dish(signed_in):
    plan = _plan()
    tools.plan_meal(D1, "Chicken Traybake", slot="dinner", weekly_plan_id=plan)
    res = signed_in.post(
        f"/api/week/{WEEK_START}/drop-dish-day",
        json={"entry_id": _entry_id(D1, "dinner")},
    )
    assert res.status_code == 200
    assert res.json()["status"] == "dropped"
    assert _slot_state(D1, "dinner") == "open"


def test_the_route_refuses_an_entry_that_is_not_on_this_household_s_plan(signed_in):
    plan = _plan()
    # The good request first, so a 404 on the second one is provably a
    # refusal and not simply a route that isn't there.
    tools.plan_meal(D1, "Chicken Traybake", slot="dinner", weekly_plan_id=plan)
    ok = signed_in.post(
        f"/api/week/{WEEK_START}/drop-dish-day",
        json={"entry_id": _entry_id(D1, "dinner")},
    )
    assert ok.status_code == 200
    res = signed_in.post(f"/api/week/{WEEK_START}/drop-dish-day", json={"entry_id": 999999})
    assert res.status_code == 404


# The screen's own functions — the grouped dish list with its stepper and
# Change button, and the seven day tiles — went on 2026-09-18: Check the
# week is a carousel of day cards now, see tests/test_plan_cards_2026_09_18.py.
# What is left above is the server's drop-dish-day, which still holds.
