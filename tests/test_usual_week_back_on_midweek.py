"""
A meal turned back on mid-week reaches the week already planned
(defect hunt, 2026-10-28; usual_week.reopen_slots_turned_back_on).

Walked over HTTP: Wednesday dinner "Don't plan" in the usual week, the week
approved (Wednesday planned_empty, usual_week_off), then Wednesday dinner
turned back on in Preferences on that Wednesday afternoon. Before: the row
kept saying "Not planned — you don’t plan this meal on this day", Today
asked nothing, chat's plan_meal refused it as "deliberately empty", and the
menu offered no Build a plan. After: it is an open question — nothing
bought until it's answered — on a day still ahead only.
"""
from __future__ import annotations

import datetime
import json

import pytest

from app import tools
from app.db import get_conn
from conftest import household_pin, household_today

MON = household_today() - datetime.timedelta(days=household_today().weekday()) + datetime.timedelta(days=7)
WEEK = MON.isoformat()
DATES = tools._week_dates(WEEK)
MONDAY, TUESDAY, WEDNESDAY, THURSDAY = DATES[:4]
DISHES = ["Chicken Stew", "Gnocchi", "Tacos", "Rice Bowls", "Turkey Chili", "Roast", "Lentil Soup"]

# Wednesday of that week, 4pm on the household's clock: Monday and Tuesday
# have gone by, Wednesday's dinner is still ahead. real_time_of_day: the
# hour is the point (conftest otherwise drafts at the start of the day).
pytestmark = [pytest.mark.today(household_pin(16, on=MON + datetime.timedelta(days=2))),
              pytest.mark.real_time_of_day]


def _rows(plan, day=None):
    conn = get_conn()
    rows = [dict(r) for r in conn.execute(
        "SELECT id, date, slot, slot_state, derived_from_json, open_reason FROM meal_plan_entries "
        "WHERE weekly_plan_id = ? ORDER BY date, slot, id", (plan,))]
    conn.close()
    return [r for r in rows if day is None or r["date"] == day]


def _constraint(row):
    return json.loads(row["derived_from_json"] or "{}").get("constraint")


def _week(off_days=(WEDNESDAY,)):
    plan = tools.create_weekly_plan(WEEK)["weekly_plan_id"]
    for day, name in zip(DATES, DISHES):
        tools.add_recipe(name, ingredients=[{"item": f"{name} thing", "qty": "1", "category": "produce"}],
                         default_servings=4)
        if day not in off_days:
            tools.plan_meal(day, name, slot="dinner", weekly_plan_id=plan)
    # What generation writes for a slot the usual week has off.
    from app.tools import usual_week
    usual_week.settle_off_slots(plan, [{"date": d, "slot": "dinner"} for d in off_days])
    tools.approve_weekly_plan(plan)
    return plan


def _groceries():
    return sorted(g["item"] for g in tools.list_grocery_list())


def test_a_dinner_turned_back_on_becomes_a_question_not_a_stuck_blank(signed_in):
    c = signed_in
    assert c.post("/api/usual-week", json={"grid": {"dinner": {"wednesday": "off"}}}).status_code == 200
    plan = _week()
    before = _groceries()
    (wed,) = _rows(plan, WEDNESDAY)
    assert wed["slot_state"] == "planned_empty" and _constraint(wed) == "usual_week_off"

    assert c.post("/api/usual-week", json={"grid": {"dinner": {"wednesday": "everyone"}}}).status_code == 200

    (wed,) = _rows(plan, WEDNESDAY)
    assert wed["slot_state"] == "open"
    assert _constraint(wed) == "usual_week_back_on"
    assert "Wednesday" in wed["open_reason"]
    assert _groceries() == before, "a question buys nothing"
    audit = tools.audit_plan_slots(plan)
    assert audit["duplicated"] == [] and audit["hollow"] == []
    # Today asks about it, and an answer lands — one row, its shopping added.
    titles = [i.get("title") for i in tools.get_needs_you_items()]
    assert any("dinner" in (t or "").lower() and "call" in (t or "").lower() for t in titles), titles
    tools.plan_meal_for_chat(WEDNESDAY, "Tacos", slot="dinner")
    (wed,) = _rows(plan, WEDNESDAY)
    assert wed["slot_state"] == "planned"
    assert "Tacos thing" in _groceries()


def test_a_day_already_gone_or_a_trip_is_left_alone(signed_in):
    c = signed_in
    c.post("/api/usual-week", json={"grid": {"dinner": {"monday": "off", "wednesday": "off"}}})
    plan = _week(off_days=(MONDAY, WEDNESDAY, THURSDAY))
    # Thursday is a trip, not the usual week: nobody home stays nobody home.
    tools.clear_plan_slot(plan, THURSDAY, "dinner")
    tools.plan_slot_empty(plan, THURSDAY, "dinner", reason="Out at the cottage.")

    c.post("/api/usual-week", json={"grid": {"dinner": {"monday": "everyone", "wednesday": "everyone",
                                                         "thursday": "everyone"}}})

    (mon,) = _rows(plan, MONDAY)
    assert mon["slot_state"] == "planned_empty", "Monday has gone by — history isn't rewritten"
    (thu,) = _rows(plan, THURSDAY)
    assert thu["slot_state"] == "planned_empty" and _constraint(thu) != "usual_week_back_on"
    (wed,) = _rows(plan, WEDNESDAY)
    assert wed["slot_state"] == "open"


@pytest.mark.today(household_pin(21, on=MON + datetime.timedelta(days=2)))
def test_tonights_dinner_already_over_is_not_asked(signed_in):
    c = signed_in
    c.post("/api/usual-week", json={"grid": {"dinner": {"wednesday": "off", "thursday": "off"}}})
    plan = _week(off_days=(WEDNESDAY, THURSDAY))
    c.post("/api/usual-week", json={"grid": {"dinner": {"wednesday": "everyone", "thursday": "everyone"}}})
    assert _rows(plan, WEDNESDAY)[0]["slot_state"] == "planned_empty"
    assert _rows(plan, THURSDAY)[0]["slot_state"] == "open"


def test_a_dinner_turned_off_mid_week_keeps_its_dish_and_shopping(signed_in):
    c = signed_in
    c.post("/api/usual-week", json={"grid": {"dinner": {"wednesday": "off"}}})
    plan = _week()
    before = _groceries()
    c.post("/api/usual-week", json={"grid": {"dinner": {"friday": "off"}}})
    (fri,) = _rows(plan, DATES[4])
    assert fri["slot_state"] == "planned"
    assert _groceries() == before
    assert _rows(plan, WEDNESDAY)[0]["slot_state"] == "planned_empty", "still off — nothing to reopen"


def test_a_count_above_zero_reopens_a_none_thanks_week(signed_in):
    # The older door: "no dinners" (count 0) written into the week, then a
    # count given again through the Different dishes stepper.
    tools.set_household_meal_preferences(dinners_per_week=0, mark_complete=False)
    plan = tools.create_weekly_plan(WEEK)["weekly_plan_id"]
    for day in DATES:
        tools.plan_slot_empty(plan, day, "dinner", reason="No dinners, thanks.",
                              derived_from={"constraint": "dinners_per_week:0"})
    tools.approve_weekly_plan(plan)
    tools.set_household_meal_preferences(dinners_per_week=4, mark_complete=False)
    states = {r["date"]: r["slot_state"] for r in _rows(plan)}
    assert states[TUESDAY] == "planned_empty"
    assert all(states[d] == "open" for d in DATES[2:]), states


def test_another_households_week_is_untouched(signed_in):
    from app import households
    c = signed_in
    c.post("/api/usual-week", json={"grid": {"dinner": {"wednesday": "off"}}})
    plan = _week()
    other = households.create_household("Neighbours", "a-safe-distinct-passphrase")
    with tools.use_household(other):
        tools.save_usual_week(grid={"dinner": {"wednesday": "off"}})
        tools.save_usual_week(grid={"dinner": {"wednesday": "everyone"}})
    assert _rows(plan, WEDNESDAY)[0]["slot_state"] == "planned_empty"
