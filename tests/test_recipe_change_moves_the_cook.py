"""
Change recipe on a night already ticked eaten moves the cook with it
(defect hunt, 2026-10-31).

"We had tacos instead": tonight's Chicken Curry ticked eaten, then changed
to Beef Tacos. The curry kept "made it once" and its last-cooked date, the
tacos read never made, and unticking the night afterwards took the cook off
the TACOS (floored at 0) — the curry's phantom count could never come off.
"""
from __future__ import annotations

import datetime

from app import tools
from app.db import get_conn
from conftest import household_today


def _counts() -> dict:
    conn = get_conn()
    try:
        return {r["name"]: (r["times_cooked"], r["last_cooked_date"])
                for r in conn.execute("SELECT name, times_cooked, last_cooked_date FROM recipes")}
    finally:
        conn.close()


def _night(day: datetime.date, dish: str) -> int:
    plan = tools.create_weekly_plan(day.isoformat(), day_count=1)["weekly_plan_id"]
    entry = tools.plan_meal(day.isoformat(), dish, slot="dinner", weekly_plan_id=plan)["entry_id"]
    tools.approve_weekly_plan(plan, approved_by="Emily", confirm_hard_conflicts=True)
    return entry


def _setup():
    tools.add_member("Emily")
    tools.add_recipe("Chicken Curry", [{"item": "Chicken thighs", "qty": "2 lbs", "category": "meat-seafood"}],
                     instructions=["Cook."], default_servings=4)
    tacos = tools.add_recipe("Beef Tacos", [{"item": "Beef mince", "qty": "1 lb", "category": "meat-seafood"}],
                             instructions=["Brown."], default_servings=4)
    return tacos["recipe_id"]


def test_changing_an_eaten_night_moves_its_cook_to_the_new_recipe(signed_in):
    tacos = _setup()
    today = household_today()
    entry = _night(today, "Chicken Curry")
    assert signed_in.post("/api/cooker/check-meal", json={"entry_id": entry, "status": "done"}).status_code == 200

    res = signed_in.post("/api/meal-recipe", json={"entry_id": entry, "recipe_id": tacos})
    assert res.json()["status"] == "changed"
    assert _counts() == {"Chicken Curry": (0, None), "Beef Tacos": (1, today.isoformat())}

    # And the untick now takes it off the dish that was eaten.
    signed_in.post("/api/cooker/check-meal", json={"entry_id": entry, "status": "pending"})
    assert _counts() == {"Chicken Curry": (0, None), "Beef Tacos": (0, None)}


def test_an_earlier_cook_of_the_old_recipe_still_stands(signed_in):
    tacos = _setup()
    today = household_today()
    last_week = today - datetime.timedelta(days=7)
    earlier = _night(last_week, "Chicken Curry")
    tools.check_off_meal(earlier, "done")
    entry = _night(today, "Chicken Curry")
    tools.check_off_meal(entry, "done")
    assert _counts()["Chicken Curry"] == (2, today.isoformat())

    tools.change_meal_recipe(entry, tacos)
    assert _counts()["Chicken Curry"] == (1, last_week.isoformat())


def test_a_night_still_ahead_moves_no_count(signed_in):
    tacos = _setup()
    entry = _night(household_today(), "Chicken Curry")
    tools.change_meal_recipe(entry, tacos)
    assert _counts() == {"Chicken Curry": (0, None), "Beef Tacos": (0, None)}
