"""
On a week's last evening, Today's "Tomorrow starts with" names the first
move of the NEXT plan.

today_moves builds tomorrow's card off the cooker view, and that view is one
plan — the one covering today. On a plan's last day it has no tomorrow, so
with next week approved and starting in the morning Today read "That's
everything for today." over a day with a dinner on it. moves._view_for_day
reads the plan that does cover tomorrow (weekly_plan._live_plan_covering,
the current-plan resolver's own query).

Dated off the household's clock (conftest.household_today), per CLAUDE.md.
"""
from __future__ import annotations

import datetime as dt

from app import tools
from app.db import get_conn
from app.tools import moves as _moves
from conftest import household_today

TODAY = household_today()


def _plan(first_day: dt.date, dish: str, status: str = "approved") -> int:
    plan_id = tools.create_weekly_plan(first_day.isoformat())["weekly_plan_id"]
    for n in range(7):
        tools.plan_meal((first_day + dt.timedelta(days=n)).isoformat(), dish,
                        slot="dinner", weekly_plan_id=plan_id)
    conn = get_conn()
    conn.execute("UPDATE weekly_plans SET status = ? WHERE id = ?", (status, plan_id))
    conn.commit()
    conn.close()
    return plan_id


def _last_evening(next_status: str) -> dict:
    """This week ends today, tonight's dinner is cooked, next week starts tomorrow."""
    tools.add_member("Alex")
    tools.add_member("Sam")
    tools.add_recipe("Bean Chili", ingredients=[{"item": "Black Beans", "qty": "2 cans"}],
                     prep_time_minutes=10, cook_time_minutes=40, default_servings=2)
    tools.add_recipe("Lemon Pasta", ingredients=[{"item": "Spaghetti", "qty": "1 lb"}],
                     prep_time_minutes=10, cook_time_minutes=20, default_servings=2)
    _plan(TODAY - dt.timedelta(days=6), "Bean Chili")
    _plan(TODAY + dt.timedelta(days=1), "Lemon Pasta", status=next_status)
    evening = dt.datetime.combine(TODAY, dt.time(20, 0))
    for move in _moves.today_moves(now=evening)["moves"]:
        if move["kind"] == "cook":
            _moves.set_move_done(move["id"], True)
    return _moves.today_moves(now=evening)


def test_the_last_evening_of_a_week_names_next_weeks_first_dinner():
    """CATCH: red before _view_for_day — `tomorrow` was None."""
    payload = _last_evening("approved")
    assert payload["featured"] is None
    assert all(m["done"] for m in payload["moves"])
    assert payload["tomorrow"] is not None
    assert payload["tomorrow"]["title"] == "Lemon Pasta"
    assert payload["tomorrow"]["date"] == (TODAY + dt.timedelta(days=1)).isoformat()


def test_a_retired_plan_starting_tomorrow_is_not_named():
    """EDGE: a retired plan is nobody's answer to "what's for dinner" — the
    card stays empty rather than naming its dish."""
    payload = _last_evening("retired")
    assert payload["tomorrow"] is None
