"""
"We skipped it" on a dinner cooked double moves the dish to the night it
was feeding (Loop Board, Emily's option A, 2026-10-12).

Found 2026-10-10: Friday's tacos cooked double for Saturday, answered
"skipped" on Saturday morning — Today, the morning text and the reheat
move all still said "Tacos, leftovers from Friday", a batch nobody cooked.
"""
from __future__ import annotations

import datetime
import json

from conftest import household_today

from app import tools
from app.db import get_conn
from app.tools import leftovers, moves


def _plan(days: int = 3):
    """An approved plan from yesterday through tomorrow (household clock)."""
    today = household_today()
    yday = today - datetime.timedelta(days=1)
    plan = tools.create_weekly_plan(yday.isoformat(), day_count=days)["weekly_plan_id"]
    conn = get_conn()
    conn.execute("UPDATE weekly_plans SET status = 'approved' WHERE id = ?", (plan,))
    conn.commit()
    conn.close()
    tools.add_recipe("Tacos", ingredients=[{"item": "Tortillas", "qty": "8"}],
                     prep_time_minutes=10, cook_time_minutes=20, default_servings=4)
    return plan, today, yday.isoformat()


def _chain(plan, yday, fed_days):
    keys = [f"{d}:dinner" for d in fed_days]
    cook = tools.plan_meal(yday, "Tacos", slot="dinner", weekly_plan_id=plan,
                           derived_from={"make_double_for": keys})["entry_id"]
    fed = [tools.plan_meal(d, "Tacos", slot="dinner", weekly_plan_id=plan,
                           derived_from={"links_to": f"{yday}:dinner"})["entry_id"] for d in fed_days]
    chains = leftovers.plan_leftover_chains(plan)
    assert set(chains["leftovers"]) == set(fed), "premise: a real cook-once-eat-twice chain"
    return cook, fed


def _rows(plan, day):
    conn = get_conn()
    rows = [dict(r) for r in conn.execute(
        "SELECT id, slot_state, recipe_id, skipped_at, reasoning, derived_from_json FROM meal_plan_entries "
        "WHERE weekly_plan_id = ? AND date = ? AND slot = 'dinner'", (plan, day))]
    conn.close()
    return rows


def test_skipping_the_double_cook_moves_the_dish_to_the_night_it_fed():
    plan, today, yday = _plan()
    cook, (saturday,) = _chain(plan, yday, [today.isoformat()])

    out = tools.answer_yesterday(cook, "skipped", today)

    assert out["moved_to"]["date"] == today.isoformat()
    # Tonight is the cook now — the dish itself, not a reheat of it.
    [tonight] = _rows(plan, today.isoformat())
    assert tonight["id"] == cook and tonight["slot_state"] == "planned"
    assert tonight["skipped_at"] is None
    assert leftovers.plan_leftover_chains(plan)["leftovers"] == {}
    assert "make_double_for" not in json.loads(tonight["derived_from_json"] or "{}")
    payload = moves.today_moves(day=today.isoformat(),
                                now=datetime.datetime.combine(today, datetime.time(8, 0)))
    dinner = [m for m in payload["moves"] if m["slot"] == "dinner"]
    assert [m["kind"] for m in dinner] == ["cook"]
    assert "leftovers" not in dinner[0]["title"].lower()
    # Yesterday is never an absent slot: an empty night saying where it went.
    [last_night] = _rows(plan, yday)
    assert last_night["slot_state"] == "planned_empty"
    assert "moved to" in last_night["reasoning"]
    audit = tools.audit_plan_slots(plan, day_count=3)
    assert not [d for d in audit["duplicated"] if d["slot"] == "dinner"]
    # Answered: the card has nothing left to ask.
    assert out["meals"] == []


def test_a_second_fed_night_now_eats_from_the_new_cook_night():
    plan, today, yday = _plan()
    tomorrow = (today + datetime.timedelta(days=1)).isoformat()
    cook, (_sat, sunday) = _chain(plan, yday, [today.isoformat(), tomorrow])

    tools.answer_yesterday(cook, "skipped", today)

    chains = leftovers.plan_leftover_chains(plan)
    assert list(chains["leftovers"]) == [sunday]
    assert chains["leftovers"][sunday]["source"]["entry_id"] == cook
    assert chains["leftovers"][sunday]["source"]["date"] == today.isoformat()


def test_an_ordinary_dinner_skipped_is_only_stamped():
    plan, today, yday = _plan()
    entry = tools.plan_meal(yday, "Tacos", slot="dinner", weekly_plan_id=plan)["entry_id"]
    out = tools.answer_yesterday(entry, "skipped", today)
    assert "moved_to" not in out
    [row] = _rows(plan, yday)
    assert row["id"] == entry and row["skipped_at"] is not None
