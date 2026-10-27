"""
A double batch's spare portion shows in the freezer list only once the
batch is cooked (Loop Board card 4001f4c0523181e9886fe357c6279ac0; Emily
2026-10-26 "go with your pick").

Tonight's Garlic Shrimp was cooked double to feed Friday. Tonight off: the
cook moves onto Friday and tonight's share is counted as a portion for the
freezer. Before, the Kitchen's freezer listed that portion straight away,
two days before anybody had cooked it.
"""
from __future__ import annotations

import datetime

from conftest import household_today, on_the_weeks_monday

from app import tools
from app.db import get_conn
from app.tools import tonight as _tonight

ON_MONDAY = on_the_weeks_monday()


def _monday() -> datetime.date:
    today = household_today()
    return today - datetime.timedelta(days=today.weekday())


WEEK = _monday().isoformat()
DAYS = tools._week_dates(WEEK)
MON, TUE, WED, THU, FRI, SAT, SUN = DAYS
TONIGHT = WED
AFTERNOON = datetime.datetime.fromisoformat(f"{TONIGHT}T15:50:00")
DISHES = ["Toast Soldiers", "Bean Chili", "Garlic Shrimp",
          "Lentil Soup", "Fish Tacos", "Pizza Night", "Roast Veg"]


def _recipe(name):
    tools.add_recipe(
        name,
        ingredients=[{"item": f"Greens for {name}", "qty": "1 bag", "category": "produce"}],
        prep_time_minutes=10, cook_time_minutes=20, default_servings=4,
    )


def _freezer_items():
    by_loc = tools.get_inventory_by_location()
    for loc in by_loc["locations"]:
        if loc["location"] == "freezer":
            return [i["item"] for i in loc["items"]]
    return []


def _cooked_double_and_called_off():
    tools.add_member("Emily")
    plan = tools.create_weekly_plan(WEEK)["weekly_plan_id"]
    for day, dish in zip(DAYS, DISHES):
        _recipe(dish)
        if day == FRI:
            tools.plan_meal(day, DISHES[2], slot="dinner", weekly_plan_id=plan,
                            derived_from={"links_to": f"{TONIGHT}:dinner"})
            continue
        tools.plan_meal(day, dish, slot="dinner", weekly_plan_id=plan)
    tools.repair_leftover_chains(plan)
    tools.approve_weekly_plan(plan)
    tapped = _tonight.tonight_night_off(now=AFTERNOON)
    assert tapped["status"] == "night_off" and tapped["kind"] == "cook_on_fed", tapped
    assert tapped["frozen"], "premise: the night off counted a portion for the freezer"
    return plan, tapped


def _cook_on(plan, day):
    conn = get_conn()
    row = conn.execute(
        "SELECT id FROM meal_plan_entries WHERE weekly_plan_id = ? AND date = ? AND slot = 'dinner'",
        (plan, day),
    ).fetchone()
    conn.close()
    return row["id"]


@ON_MONDAY
def test_the_portion_is_not_in_the_freezer_before_the_batch_is_cooked():
    plan, tapped = _cooked_double_and_called_off()
    assert tapped["frozen"]["item"] not in _freezer_items()
    # The row is still there: Undo and the cook's own bookkeeping read it.
    conn = get_conn()
    n = conn.execute("SELECT COUNT(*) AS n FROM inventory_items WHERE item = ?",
                     (tapped["frozen"]["item"],)).fetchone()["n"]
    conn.close()
    assert n == 1


@ON_MONDAY
def test_it_shows_once_the_batch_is_ticked_cooked():
    plan, tapped = _cooked_double_and_called_off()
    tools.check_off_meal(_cook_on(plan, FRI), "done")
    assert tapped["frozen"]["item"] in _freezer_items()


@ON_MONDAY
def test_it_shows_once_the_cooks_night_has_gone_by(monkeypatch):
    plan, tapped = _cooked_double_and_called_off()
    from app.tools import cooker
    saturday = datetime.date.fromisoformat(SAT)
    monkeypatch.setattr(cooker, "household_today", lambda *a, **k: saturday)
    assert tapped["frozen"]["item"] in _freezer_items()


@ON_MONDAY
def test_other_freezer_food_is_untouched():
    plan, tapped = _cooked_double_and_called_off()
    tools.update_inventory("Chicken thighs", "add", quantity="2 lbs", category="meat", location="freezer")
    assert "Chicken thighs" in _freezer_items()
