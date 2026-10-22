"""
Prep cuts on two doors that lost them (defect hunt, 2026-10-22).

1. The Move sheet carries a cut that would fall after its meal earlier by
   the days the meal moved — and could carry it into the past, where Today
   (today's prep only) and the Kitchen root (no past prep day) never show
   it. It lands on today now, the nights swap's rule; Undo puts it back.
2. Swap a dinner in place, then Undo: the dinner came back with none of
   the prep the household had added for it, ticked or not. It comes back
   as it was now — on a one-day swap and on a whole-dish swap.
"""
from __future__ import annotations

import datetime

from app import tools
from app.db import get_conn
from app.tools import meal_move, prep_sessions, swap_in_place
from conftest import household_today


def _recipe(name):
    tools.add_recipe(name, ingredients=[{"item": "Onion", "qty": "1", "category": "produce"}],
                     prep_time_minutes=10, cook_time_minutes=20, default_servings=4)


def _days(n=6):
    today = household_today()
    return [(today + datetime.timedelta(days=i)).isoformat() for i in range(n)]


def _cuts() -> list[dict]:
    conn = get_conn()
    rows = conn.execute(
        "SELECT task_date, description, status, meal_plan_entry_id FROM prep_tasks "
        "WHERE task_type = 'prep_cut' ORDER BY id").fetchall()
    conn.close()
    return [dict(r) for r in rows]


def _pick(name):
    return lambda ctx: {"meal_name": name, "reason": "Something different."}


def test_a_moved_dinners_cut_lands_on_today_not_yesterday_and_undo_restores_it():
    _recipe("Chicken Bowls")
    _recipe("Bean Chili")
    d = _days()
    plan = tools.create_weekly_plan(d[0])["weekly_plan_id"]
    tools.plan_meal(d[0], "Bean Chili", slot="dinner", weekly_plan_id=plan)
    bowls = tools.plan_meal(d[2], "Chicken Bowls", slot="dinner", weekly_plan_id=plan)["entry_id"]
    prep_sessions.add_prep_cut(plan, d[1], "Cut up romaine", [bowls])

    out = meal_move.move_meal(plan, bowls, d[0])

    assert out["status"] == "moved"
    assert _cuts()[0]["task_date"] == d[0]
    meal_move.undo_meal_move(plan, out["move_id"])
    assert _cuts()[0]["task_date"] == d[1]


def test_a_cut_still_ahead_of_the_move_keeps_its_days_before_the_meal():
    """Not every carried cut is clamped: one that stays in the future keeps
    the meal's own shift."""
    _recipe("Chicken Bowls")
    _recipe("Bean Chili")
    d = _days()
    plan = tools.create_weekly_plan(d[0])["weekly_plan_id"]
    tools.plan_meal(d[2], "Bean Chili", slot="dinner", weekly_plan_id=plan)
    bowls = tools.plan_meal(d[5], "Chicken Bowls", slot="dinner", weekly_plan_id=plan)["entry_id"]
    prep_sessions.add_prep_cut(plan, d[4], "Cut up romaine", [bowls])

    assert meal_move.move_meal(plan, bowls, d[2])["status"] == "moved"
    assert _cuts()[0]["task_date"] == d[1]


def test_undo_of_a_swap_puts_the_ticked_prep_back_on_the_dinner():
    _recipe("Chicken Bowls")
    d = _days()
    plan = tools.create_weekly_plan(d[0])["weekly_plan_id"]
    bowls = tools.plan_meal(d[3], "Chicken Bowls", slot="dinner", weekly_plan_id=plan)["entry_id"]
    cut = prep_sessions.add_prep_cut(plan, d[2], "Cut up romaine", [bowls])["tasks"][0]["prep_task_id"]
    tools.check_off_prep_step(cut, status="done")

    swapped = swap_in_place.swap_meal_in_place(plan, bowls, picker=_pick("Fish Tacos"))
    assert swapped["status"] == "swapped"
    assert _cuts() == []  # the swap still releases it: romaine is no use to tacos
    # A second swap still undoes to the ORIGINAL, prep and all.
    swapped = swap_in_place.swap_meal_in_place(plan, swapped["entry_id"], picker=_pick("Pad Thai"))
    back = swap_in_place.undo_meal_swap(plan, swapped["entry_id"])

    assert back["meal"] == "Chicken Bowls"
    assert _cuts() == [{"task_date": d[2], "description": "Cut up romaine", "status": "done",
                        "meal_plan_entry_id": back["entry_id"]}]
    week = prep_sessions.prep_sessions_for_plan(plan)
    assert week == [] or all(i["entry_id"] == back["entry_id"]
                             for s in week for i in s["items"] if i.get("prep_task_id"))


def test_undo_of_a_whole_dish_swap_puts_each_days_prep_back():
    _recipe("Chicken Bowls")
    d = _days()
    plan = tools.create_weekly_plan(d[0])["weekly_plan_id"]
    a = tools.plan_meal(d[2], "Chicken Bowls", slot="dinner", weekly_plan_id=plan)["entry_id"]
    b = tools.plan_meal(d[5], "Chicken Bowls", slot="dinner", weekly_plan_id=plan)["entry_id"]
    prep_sessions.add_prep_cut(plan, d[1], "Cut up romaine", [a])
    prep_sessions.add_prep_cut(plan, d[4], "Cut up romaine", [b])

    swapped = swap_in_place.swap_meal_in_place(plan, a, picker=_pick("Fish Tacos"), whole_dish=True)
    assert swapped["status"] == "swapped" and len(swapped["entry_ids"]) == 2
    assert _cuts() == []
    back = swap_in_place.undo_meal_swap(plan, swapped["entry_ids"][0])

    by_entry = {c["meal_plan_entry_id"]: c["task_date"] for c in _cuts()}
    assert sorted(by_entry.values()) == [d[1], d[4]]
    assert set(by_entry) == set(back["entry_ids"])


def test_undo_after_the_dinner_moved_puts_the_cut_on_or_before_it():
    """Review, same night: swap, move the new dinner earlier, Undo — the
    cut came back two days AFTER the restored dinner."""
    _recipe("Chicken Bowls")
    d = _days()
    plan = tools.create_weekly_plan(d[0])["weekly_plan_id"]
    bowls = tools.plan_meal(d[4], "Chicken Bowls", slot="dinner", weekly_plan_id=plan)["entry_id"]
    prep_sessions.add_prep_cut(plan, d[3], "Cut up romaine", [bowls])

    swapped = swap_in_place.swap_meal_in_place(plan, bowls, picker=_pick("Fish Tacos"))
    assert meal_move.move_meal(plan, swapped["entry_id"], d[1])["status"] == "moved"
    back = swap_in_place.undo_meal_swap(plan, swapped["entry_id"])

    assert back["meal"] == "Chicken Bowls"
    [cut] = _cuts()
    assert cut["task_date"] <= d[1] and cut["task_date"] >= d[0]
