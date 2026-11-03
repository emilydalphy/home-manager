"""
A can, tin or jar in the amount means the food was bought ready
(2026-11-03 overnight hunt; app/tools/prep_list.py `_CONTAINER_QTY`).

"Diced tomatoes · 1 can" with no category was listed under "Made on the
night" as "Better fresh. Cut tomato goes soft and watery overnight." —
words about a knife and a cutting board, for a tin.
"""
import datetime

from conftest import household_today

from app import tools
from app.tools import prep_sessions


def _d(n):
    return (household_today() + datetime.timedelta(days=n)).isoformat()


def _week():
    for n in ("Alex", "Sam"):
        tools.add_member(n)
    today = household_today()
    tools.set_prep_days([{"weekday": today.strftime("%A").lower(), "minutes": 120}])
    return tools.create_weekly_plan(
        today.isoformat(), content_start_date=today.isoformat(), day_count=7
    )["weekly_plan_id"]


def _chili(tomato_qty):
    tools.add_recipe("Weeknight Chili", ingredients=[
        {"item": "Yellow onion", "qty": "1", "category": "produce"},
        {"item": "Diced tomatoes", "qty": tomato_qty},
        {"item": "Kidney beans", "qty": "1 can"},
    ], instructions=["Dice the onion.", "Simmer everything for 30 minutes."],
        default_servings=2, prep_time_minutes=15, cook_time_minutes=30)


def _items(plan_id):
    [session] = prep_sessions.prep_sessions_for_plan(plan_id)
    return session["items"]


def test_a_can_of_diced_tomatoes_is_not_called_better_fresh():
    plan_id = _week()
    _chili("1 can")
    tools.plan_meal(_d(2), "Weeknight Chili", slot="dinner", weekly_plan_id=plan_id)
    items = _items(plan_id)
    assert not [i for i in items if "tomato" in i["title"].lower()], items
    # The onion for the same dish is still chopped ahead.
    assert any("onion" in i["title"].lower() and i["on_list"] for i in items)


def test_fresh_diced_tomatoes_are_still_better_fresh():
    plan_id = _week()
    _chili("2")
    tools.plan_meal(_d(2), "Weeknight Chili", slot="dinner", weekly_plan_id=plan_id)
    fresh = [i for i in _items(plan_id) if i["state"] == "better_fresh" and "tomato" in i["title"].lower()]
    assert fresh and "Cut tomato" in fresh[0]["off_line"]
