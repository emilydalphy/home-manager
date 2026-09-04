"""
Backend support for the focused single-meal Cook mode (Loop Board:
"Home Manager: A real cook mode — one meal on screen, not the whole week").

The screen itself is a shell.js/shell.css thing with no JS test harness (see
test_cooker_today.py's node-extraction workaround, not repeated here), but
two pieces of real data plumbing live in Python and are worth covering
directly:

1. get_cooker_view() now carries an `attendance` block per meal, so a
   focused meal can show "for 2 + 1 guest" without a second network round
   trip — "portions matter mid-cook" per the ticket.
2. Nothing changed about how prep_tasks (including defrost tasks, from the
   just-merged defrost flow) carry `meal_plan_entry_id` — this just pins
   down that the field the frontend needs to filter "prep for THIS meal" is
   really there, since get_cooker_view's own docstring lists prep_tasks as
   flowing through get_prep_schedule unchanged.
"""
import datetime

import pytest

from app import tools
from app.tools import defrost


def _today(offset_days: int = 0) -> str:
    return (datetime.date.today() + datetime.timedelta(days=offset_days)).isoformat()


def _week_start() -> str:
    today = datetime.date.today()
    return (today - datetime.timedelta(days=today.weekday())).isoformat()


# ---------- attendance flowing into get_cooker_view ----------

def test_a_meal_carries_the_household_headcount_by_default():
    tools.add_member("Alex")
    tools.add_member("Sam")
    plan_id = tools.create_weekly_plan(_week_start())["weekly_plan_id"]
    tools.add_recipe("Soup", ingredients=[{"item": "stock", "qty": "1 L"}])
    tools.plan_meal(_today(), "Soup", slot="dinner", weekly_plan_id=plan_id)

    meal = tools.get_cooker_view(plan_id)["meals"][0]

    assert meal["attendance"]["headcount"] == 2
    assert meal["attendance"]["present_count"] == 2
    assert meal["attendance"]["guest_count"] == 0
    assert meal["attendance"]["everyone_home"] is True


def test_a_guest_raises_the_headcount_without_changing_present_count():
    tools.add_member("Alex")
    plan_id = tools.create_weekly_plan(_week_start())["weekly_plan_id"]
    tools.add_recipe("Chili", ingredients=[{"item": "beans", "qty": "1 tin"}])
    tools.plan_meal(_today(), "Chili", slot="dinner", weekly_plan_id=plan_id)

    tools.set_guest_count(_today(), "dinner", 1)

    meal = tools.get_cooker_view(plan_id)["meals"][0]
    assert meal["attendance"]["present_count"] == 1
    assert meal["attendance"]["guest_count"] == 1
    assert meal["attendance"]["headcount"] == 2  # "for 1 + 1 guest"


def test_an_absent_member_is_named_and_lowers_the_headcount():
    tools.add_member("Alex")
    tools.add_member("Sam")
    plan_id = tools.create_weekly_plan(_week_start())["weekly_plan_id"]
    tools.add_recipe("Chili", ingredients=[{"item": "beans", "qty": "1 tin"}])
    tools.plan_meal(_today(), "Chili", slot="dinner", weekly_plan_id=plan_id)

    tools.set_member_attendance(_today(), "dinner", "Alex", present=False)

    meal = tools.get_cooker_view(plan_id)["meals"][0]
    assert meal["attendance"]["headcount"] == 1
    assert meal["attendance"]["present_count"] == 1
    assert meal["attendance"]["absent_names"] == ["Alex"]
    assert meal["attendance"]["everyone_home"] is False


def test_a_component_based_plans_placeholder_date_gets_no_attendance():
    """
    Component-based plans give every entry the same placeholder date (see
    get_weekly_plan) — there is no real "who's at dinner Tuesday" question
    to answer, so attendance should come back None rather than a number
    that looks precise and means nothing.
    """
    tools.add_member("Alex")
    tools.set_planning_mode("component_based")
    plan_id = tools.create_weekly_plan(_week_start())["weekly_plan_id"]
    tools.add_recipe("Chili", ingredients=[{"item": "beans", "qty": "1 tin"}])
    tools.plan_meal(_week_start(), "Chili", weekly_plan_id=plan_id, component_category="protein")

    meal = tools.get_cooker_view(plan_id)["meals"][0]
    assert meal["attendance"] is None


# ---------- prep tasks carry the meal_plan_entry_id a focused screen needs ----------

@pytest.fixture
def chicken_recipe():
    tools.add_recipe(
        "Chicken Skewers",
        ingredients=[{"item": "Chicken Thighs", "qty": "1 lb"}],
        prep_time_minutes=10, cook_time_minutes=15,
    )


def test_a_defrost_task_for_a_meal_carries_that_meals_entry_id(chicken_recipe):
    tools.update_inventory("Chicken Thighs", "add", quantity="1 lb", category="meat/seafood", location="freezer")
    week = _week_start()
    dates = tools._week_dates(week)
    plan = tools.create_weekly_plan(week)
    entry = tools.plan_meal(dates[3], "Chicken Skewers", slot="dinner", weekly_plan_id=plan["weekly_plan_id"])
    defrost.sync_defrost_tasks(plan["weekly_plan_id"])

    view = tools.get_cooker_view(plan["weekly_plan_id"])
    meal = next(m for m in view["meals"] if m["date"] == dates[3])
    defrost_tasks = [t for t in view["prep_tasks"] if t["task_type"] == "defrost"]

    assert defrost_tasks, "expected a defrost task for the frozen chicken"
    assert defrost_tasks[0]["meal_plan_entry_id"] == meal["entry_id"] == entry["entry_id"]
