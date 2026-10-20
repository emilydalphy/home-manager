"""
A prep cut moves with its dinner on a nights swap (Emily, 2026-10-20).

Prep Tuesday for a Wednesday dinner that moves to Friday is prep Thursday —
the same number of days before the meal. Before this the swap
(weekly_plan._apply_dinner_nights_swap) deliberately left every prep cut on
its prep day. A carried day in the past lands on today; Undo puts every
row back exactly; a cut feeding meals that don't all move by the same
amount stays where it is.
"""
from __future__ import annotations

import datetime

from app import tools
from app.db import get_conn
from app.tools import prep_sessions
from app.tools.cooker import household_today


def _monday() -> datetime.date:
    today = datetime.date.today()
    return today - datetime.timedelta(days=today.weekday())


WEEK = (_monday() + datetime.timedelta(days=7)).isoformat()
MON, TUE, WED, THU, FRI, SAT, SUN = tools._week_dates(WEEK)


def _recipe(name):
    tools.add_recipe(name, ingredients=[{"item": "Onion", "qty": "1", "category": "produce"}],
                     prep_time_minutes=10, cook_time_minutes=20, default_servings=4)


def _cut(task_id) -> dict:
    conn = get_conn()
    row = conn.execute("SELECT task_date, description, status FROM prep_tasks WHERE id = ?",
                       (task_id,)).fetchone()
    conn.close()
    return dict(row)


def _two_dinners(day_a, day_b):
    _recipe("Chicken Bowls")
    _recipe("Bean Chili")
    plan = tools.create_weekly_plan(WEEK)["weekly_plan_id"]
    a = tools.plan_meal(day_a, "Chicken Bowls", slot="dinner", weekly_plan_id=plan)["entry_id"]
    b = tools.plan_meal(day_b, "Bean Chili", slot="dinner", weekly_plan_id=plan)["entry_id"]
    return plan, a, b


def test_a_prep_cut_keeps_its_days_before_the_dinner():
    """The decided example: prep Tuesday for Wednesday, dinner to Friday →
    prep Thursday. The other dinner's cut comes along the other way."""
    plan, bowls, chili = _two_dinners(WED, FRI)
    bowls_cut = prep_sessions.add_prep_cut(plan, TUE, "Get Chicken Bowls ready for Wednesday",
                                           [bowls])["tasks"][0]["prep_task_id"]
    chili_cut = prep_sessions.add_prep_cut(plan, THU, "Soak the beans", [chili])["tasks"][0]["prep_task_id"]

    out = tools.swap_dinner_nights(plan, WED, FRI)

    assert out["status"] == "swapped"
    assert _cut(bowls_cut)["task_date"] == THU
    assert _cut(bowls_cut)["description"] == "Get Chicken Bowls ready for Friday"
    assert _cut(chili_cut)["task_date"] == TUE
    assert out["prep_tasks_moved"] == 2


def test_undo_puts_every_prep_day_back_exactly():
    plan, bowls, chili = _two_dinners(WED, FRI)
    bowls_cut = prep_sessions.add_prep_cut(plan, TUE, "Get Chicken Bowls ready for Wednesday",
                                           [bowls])["tasks"][0]["prep_task_id"]
    chili_cut = prep_sessions.add_prep_cut(plan, FRI, "Soak the beans", [chili])["tasks"][0]["prep_task_id"]
    before = (_cut(bowls_cut), _cut(chili_cut))

    tools.swap_dinner_nights(plan, WED, FRI)
    assert _cut(chili_cut)["task_date"] == WED
    tools.undo_dinner_nights_swap(plan, WED, FRI)

    assert (_cut(bowls_cut), _cut(chili_cut)) == before


def test_a_carried_prep_day_in_the_past_lands_on_today_and_undo_restores_it():
    """Dinner two days out, prepped tomorrow; traded with tonight's dinner
    the cut would land on yesterday — it goes on today, never after the
    meal. Undo puts it back on tomorrow, not today + 2."""
    _recipe("Chicken Bowls")
    _recipe("Bean Chili")
    today = household_today()
    t0, t1, t2 = (today + datetime.timedelta(days=i) for i in range(3))
    plan = tools.create_weekly_plan(t0.isoformat())["weekly_plan_id"]
    tools.plan_meal(t0.isoformat(), "Bean Chili", slot="dinner", weekly_plan_id=plan)
    bowls = tools.plan_meal(t2.isoformat(), "Chicken Bowls", slot="dinner", weekly_plan_id=plan)["entry_id"]
    cut = prep_sessions.add_prep_cut(plan, t1.isoformat(), "Cut up romaine", [bowls])["tasks"][0]["prep_task_id"]

    out = tools.swap_dinner_nights(plan, t0.isoformat(), t2.isoformat())

    assert out["status"] == "swapped"
    assert _cut(cut)["task_date"] == t0.isoformat()
    tools.undo_dinner_nights_swap(plan, t0.isoformat(), t2.isoformat())
    assert _cut(cut)["task_date"] == t1.isoformat()


def test_a_cut_shared_with_a_meal_that_stays_is_left_where_it_is():
    plan, bowls, chili = _two_dinners(WED, FRI)
    sat = tools.plan_meal(SAT, "Chicken Bowls", slot="dinner", weekly_plan_id=plan)["entry_id"]
    ids = [t["prep_task_id"] for t in
           prep_sessions.add_prep_cut(plan, TUE, "Cut up romaine", [bowls, sat])["tasks"]]

    tools.swap_dinner_nights(plan, WED, FRI)

    assert [_cut(i)["task_date"] for i in ids] == [TUE, TUE]


def test_a_cut_shared_by_both_traded_dinners_is_left_where_it_is():
    plan, bowls, chili = _two_dinners(WED, FRI)
    ids = [t["prep_task_id"] for t in
           prep_sessions.add_prep_cut(plan, TUE, "Chop the onions", [bowls, chili])["tasks"]]

    out = tools.swap_dinner_nights(plan, WED, FRI)

    assert [_cut(i)["task_date"] for i in ids] == [TUE, TUE]
    assert out["prep_tasks_moved"] == 0


def test_a_ticked_cut_keeps_its_tick_when_it_moves():
    plan, bowls, chili = _two_dinners(WED, FRI)
    cut = prep_sessions.add_prep_cut(plan, TUE, "Cut up romaine", [bowls])["tasks"][0]["prep_task_id"]
    tools.check_off_prep_step(cut, status="done")

    tools.swap_dinner_nights(plan, WED, FRI)

    assert _cut(cut) == {"task_date": THU, "description": "Cut up romaine", "status": "done"}
