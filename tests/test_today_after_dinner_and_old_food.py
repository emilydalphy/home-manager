"""
Two things Today and the morning text kept saying after they stopped being
true (defect hunt, 2026-10-22).

1. A prep step nobody ticked for a dinner already cooked stood as Today's
   next-up card — "Marinate the chicken · still to do" — from the moment the
   dinner was ticked until midnight, so "That's today handled" and
   tomorrow's first move never came.
2. Food already PAST its date was "turning soon": the use-it-up notification
   (and the morning text's kitchen line, which reads its title) took every
   expired inventory row, with no lower bound, every day for ever.
"""
from __future__ import annotations

import datetime as dt

import pytest

from conftest import household_pin, household_today
from app import tools
from app.db import get_conn
from app.tools import digest
from app.tools import notifications as _notifications

D = household_today()
MON = D - dt.timedelta(days=D.weekday())
T = D.isoformat()
T1 = (D + dt.timedelta(days=1)).isoformat()


def _week() -> dict:
    """Tonight's Chicken Skewers with a marinade step today, a lunch, and a
    dinner tomorrow; approved."""
    tools.add_member("Alex")
    for name in ("Chicken Skewers", "Turkey Sandwiches", "Fish Tacos"):
        tools.add_recipe(name, ingredients=[{"item": f"Greens for {name}", "qty": "1 bag", "category": "produce"}],
                         prep_time_minutes=10, cook_time_minutes=25, default_servings=2)
    plan = tools.create_weekly_plan(MON.isoformat())["weekly_plan_id"]
    tools.plan_meal(T, "Chicken Skewers", weekly_plan_id=plan)
    tools.plan_meal(T, "Turkey Sandwiches", slot="lunch", weekly_plan_id=plan)
    tools.plan_meal(T1, "Fish Tacos", weekly_plan_id=plan)
    tools.approve_weekly_plan(plan, approved_by="Alex")
    conn = get_conn()
    entry = {r["slot"] + r["date"]: r["id"] for r in conn.execute(
        "SELECT id, date, slot FROM meal_plan_entries WHERE household_id = 1").fetchall()}
    cur = conn.execute(
        "INSERT INTO prep_tasks (household_id, weekly_plan_id, task_date, description, related_meal, "
        "task_type, meal_plan_entry_id) VALUES (1, ?, ?, 'Marinate the chicken', 'Chicken Skewers', 'general', ?)",
        (plan, T, entry["dinner" + T]))
    # The shop is done, so no "Shop before tomorrow" competes for the card.
    conn.execute("UPDATE grocery_items SET status = 'purchased' WHERE household_id = 1")
    conn.commit()
    conn.close()
    return {"prep": f"prep:{cur.lastrowid}", "dinner": f"cook:{entry['dinner' + T]}",
            "lunch": f"cook:{entry['lunch' + T]}"}


@pytest.mark.real_time_of_day
def test_a_cooked_dinners_unticked_prep_is_not_still_to_do(frozen_today):
    """CATCH. Dinner and lunch ticked at 7:30pm, the marinade not: the
    marinade leaves Today, nothing is featured, and tomorrow's first move
    takes the card."""
    frozen_today(household_pin(6, 0, on=D))
    ids = _week()
    frozen_today(household_pin(19, 30, on=D))
    tools.set_move_done(ids["dinner"], True)
    tools.set_move_done(ids["lunch"], True)
    out = tools.today_moves()
    assert ids["prep"] not in [m["id"] for m in out["moves"]]
    assert out["featured"] is None
    assert out["tomorrow"] and out["tomorrow"]["title"] == "Fish Tacos"


@pytest.mark.real_time_of_day
def test_the_prep_for_a_dinner_not_yet_cooked_is_still_to_do(frozen_today):
    """GUARD. Same evening, dinner not ticked: the marinade is still late
    and still the card."""
    frozen_today(household_pin(6, 0, on=D))
    ids = _week()
    frozen_today(household_pin(19, 30, on=D))
    tools.set_move_done(ids["lunch"], True)
    out = tools.today_moves()
    prep = next(m for m in out["moves"] if m["id"] == ids["prep"])
    assert prep["overdue"] and not prep["done"]


@pytest.mark.real_time_of_day
def test_a_prep_step_ticked_before_the_cook_stays_as_the_record(frozen_today):
    """GUARD. Only an UNTICKED step goes; one the household did stays ticked."""
    frozen_today(household_pin(6, 0, on=D))
    ids = _week()
    frozen_today(household_pin(19, 30, on=D))
    tools.set_move_done(ids["prep"], True)
    tools.set_move_done(ids["dinner"], True)
    prep = next(m for m in tools.today_moves()["moves"] if m["id"] == ids["prep"])
    assert prep["done"]


# ---------- food past its date ----------

def _stock(item: str, days_from_today: int) -> None:
    conn = get_conn()
    conn.execute(
        "INSERT INTO inventory_items (household_id, item, quantity, category, expiration_date, location) "
        "VALUES (1, ?, '1', 'dairy', ?, 'fridge')",
        (item, (household_today() + dt.timedelta(days=days_from_today)).isoformat()),
    )
    conn.commit()
    conn.close()


def _use_it_up():
    return [n for n in _notifications.get_active_notifications() if n["type"] == "expiring_soon"]


def test_food_weeks_past_its_date_is_not_turning_soon():
    """CATCH. Milk dated three weeks ago: no "turns soon", in the feed or
    in the morning text's kitchen line."""
    tools.add_member("Alex")
    _stock("Milk", -21)
    assert _use_it_up() == []
    morning = dt.datetime.combine(household_today(), dt.time(7, 0))
    lines = digest.morning_text_preview(now_local=morning, parts=["kitchen"])["lines"]
    assert not any("turns soon" in line for line in lines), lines


def test_only_the_food_still_inside_its_date_is_named():
    """CATCH. Old milk beside yogurt that turns tomorrow: one item, the yogurt."""
    _stock("Milk", -3)
    _stock("Yogurt", 1)
    [note] = _use_it_up()
    assert note["title"] == "Your yogurt turns soon"


@pytest.mark.real_time_of_day
def test_a_step_ticked_after_its_cook_is_still_credited(frozen_today):
    """GUARD. The drop is Today's only: move_credits finds the move at tick
    time through moves_for_day, while the step is still pending."""
    from app.tools import move_credits as _credits
    frozen_today(household_pin(6, 0, on=D))
    ids = _week()
    frozen_today(household_pin(19, 30, on=D))
    tools.set_move_done(ids["dinner"], True)
    tools.set_move_done(ids["prep"], True)
    assert ids["prep"] in [c["id"] for c in _credits.credits(MON, kind="prep")]
