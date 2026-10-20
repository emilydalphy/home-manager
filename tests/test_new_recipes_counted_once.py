"""
"N new recipes this week" counts RECIPES, not the meals they fill
(defect hunt, 2026-10-16).

The share page reads get_weekly_plan's new_recipe_count straight into
"14 new recipes this week", and the planning prompt has the assistant say
"two new recipes this week" off the same number. It was counted once per
MEAL: a new overnight-oats recipe on five breakfasts and a new curry that
also feeds Tuesday's leftovers read as seven new recipes for two. Found
driving the share link on a seeded week whose four recipes came out as
"14 new recipes this week".
"""
from __future__ import annotations

import datetime

from conftest import household_today

from app import tools
from app.db import get_conn


def _week_start() -> str:
    today = household_today()
    return (today - datetime.timedelta(days=today.weekday())).isoformat()


def _day(ws: str, n: int) -> str:
    return (datetime.date.fromisoformat(ws) + datetime.timedelta(days=n)).isoformat()


def _week_with_repeats() -> tuple[str, int]:
    ws = _week_start()
    # A dish the household already had before this week was planned.
    tools.add_recipe("Black Bean Tacos", [{"item": "black beans", "qty": "2 cans"}])
    conn = get_conn()
    conn.execute("UPDATE recipes SET created_at = '2026-01-01 00:00:00' WHERE name = 'Black Bean Tacos'")
    conn.commit()
    conn.close()

    plan_id = tools.create_weekly_plan(ws)["weekly_plan_id"]
    # Two the week brought in (written after the plan row, as a draft's are).
    tools.add_recipe("Chicken Curry", [{"item": "chicken thighs", "qty": "2 lb"}])
    tools.add_recipe("Overnight Oats", [{"item": "rolled oats", "qty": "2 cups"}])
    for n in range(5):
        tools.plan_meal(_day(ws, n), "Overnight Oats", slot="breakfast", weekly_plan_id=plan_id)
    tools.plan_meal(_day(ws, 0), "Chicken Curry", slot="dinner", weekly_plan_id=plan_id)
    tools.plan_meal(_day(ws, 1), "Chicken Curry", slot="dinner", weekly_plan_id=plan_id)
    tools.plan_meal(_day(ws, 2), "Black Bean Tacos", slot="dinner", weekly_plan_id=plan_id)
    tools.plan_meal(_day(ws, 3), "Black Bean Tacos", slot="dinner", weekly_plan_id=plan_id)
    return ws, plan_id


def test_a_recipe_on_several_days_is_one_new_recipe():
    _, plan_id = _week_with_repeats()
    plan = tools.get_weekly_plan(plan_id)
    # Nine meals; two recipes this week brought in, one it already had.
    assert len(plan["meals"]) == 9
    assert plan["new_recipe_count"] == 2
    assert plan["repeat_recipe_count"] == 1


def test_the_share_page_carries_no_new_recipe_count(client):
    """The "N new recipes this week" tag was written to the household, and a
    babysitter reads the share page, so it no longer goes there (Emily,
    2026-10-20). The count itself is still right on the household's own plan,
    above."""
    _week_with_repeats()
    token = tools.get_or_create_share_link()["token"]
    client.cookies.clear()
    plan = client.get(f"/api/share/{token}").json()
    assert "new_recipe_count" not in plan


def test_a_week_of_saved_dishes_and_takeout_has_nothing_new():
    """The other side: nothing new is still 0 (share.html hides the tag on
    0), and a freeform night is no recipe either way."""
    ws, _ = _week_with_repeats()
    plan_id = tools.create_weekly_plan(_day(ws, 7))["weekly_plan_id"]
    tools.plan_meal(_day(ws, 7), "Black Bean Tacos", slot="dinner", weekly_plan_id=plan_id)
    tools.plan_meal(_day(ws, 8), "Black Bean Tacos", slot="dinner", weekly_plan_id=plan_id)
    tools.plan_meal(_day(ws, 9), "Takeout", slot="dinner", weekly_plan_id=plan_id)
    plan = tools.get_weekly_plan(plan_id)
    assert plan["new_recipe_count"] == 0
    assert plan["repeat_recipe_count"] == 1
