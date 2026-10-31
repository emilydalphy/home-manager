"""
A prep day comes round every week of a long period (defect hunt,
2026-10-31, walking rollovers).

prep_sessions_for_plan mapped each standing prep weekday onto the FIRST
date of that weekday in the period. A period runs up to 28 days, so in a
two-week period with Sunday prep the second Sunday had no session at all:
a cut dated for it was saved and shown on no screen.
"""
from __future__ import annotations

from datetime import timedelta

from app import tools
from app.db import get_conn
from app.tools import prep_sessions
from conftest import household_today


def _monday_ahead():
    today = household_today()
    return today + timedelta(days=7 - today.weekday())


def _two_week_plan(sundays_dinner: dict[int, str]):
    start = _monday_ahead()
    plan = tools.create_weekly_plan(start.isoformat(), content_start_date=start.isoformat(), day_count=14)["weekly_plan_id"]
    for offset, meal in sundays_dinner.items():
        tools.add_recipe(meal, ingredients=[{"item": meal + " base", "qty": "1 lb", "category": "pantry"}])
        tools.plan_meal((start + timedelta(days=offset)).isoformat(), meal, slot="dinner", weekly_plan_id=plan)
    conn = get_conn()
    entries = {r["date"]: r["id"] for r in conn.execute(
        "SELECT id, date FROM meal_plan_entries WHERE weekly_plan_id = ? AND slot = 'dinner' AND slot_state = 'planned'",
        (plan,),
    )}
    conn.close()
    return start, plan, entries


def test_the_second_sunday_of_a_two_week_period_has_its_session():
    """CATCH: red before the fix (only the first Sunday came back)."""
    start, plan, entries = _two_week_plan({7: "Chili", 14 - 1: "Tacos"})
    tools.set_prep_days([{"weekday": "sunday"}])
    first_sun = (start + timedelta(days=6)).isoformat()
    second_sun = (start + timedelta(days=13)).isoformat()
    prep_sessions.add_prep_cut(plan, first_sun, "Chop onions", [entries[(start + timedelta(days=7)).isoformat()]])
    prep_sessions.add_prep_cut(plan, second_sun, "Chop peppers", [entries[second_sun]])

    sessions = prep_sessions.prep_sessions_for_plan(plan)

    assert [s["date"] for s in sessions] == [first_sun, second_sun]
    assert [i["title"] for i in sessions[1]["items"]] == ["Chop peppers"]
    assert sessions[1]["weekday"] == "Sunday"


def test_a_one_week_period_still_has_one_session_per_prep_day():
    """GUARD: an ordinary week is unchanged — one Sunday, one session."""
    start = _monday_ahead()
    plan = tools.create_weekly_plan(start.isoformat(), content_start_date=start.isoformat(), day_count=7)["weekly_plan_id"]
    tools.add_recipe("Stew", ingredients=[{"item": "Beef", "qty": "1 lb", "category": "meat/seafood"}])
    sunday = (start + timedelta(days=6)).isoformat()
    tools.plan_meal(sunday, "Stew", slot="dinner", weekly_plan_id=plan)
    conn = get_conn()
    entry = conn.execute("SELECT id FROM meal_plan_entries WHERE weekly_plan_id = ? AND date = ? AND slot = 'dinner'",
                         (plan, sunday)).fetchone()["id"]
    conn.close()
    tools.set_prep_days([{"weekday": "sunday"}, {"weekday": "wednesday"}])
    prep_sessions.add_prep_cut(plan, sunday, "Trim beef", [entry])

    sessions = prep_sessions.prep_sessions_for_plan(plan)

    assert [s["date"] for s in sessions] == [sunday]
