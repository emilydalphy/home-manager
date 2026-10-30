"""
A weekday lunch answer is not a weekend one (defect hunt, 2026-10-29).

Setup's "What does each person need for weekday lunches?" asks Monday to
Friday. Its bridge writes each person's answer as their STANDING
rhythm.lunch_location ('out' for Cold packed / thermos / nut-free), and a
standing answer used to be read for all seven days. So a household that
said "Maya: cold packed, nut-free" for school got:

  - Plan a week's step 3 pre-ticking Saturday and Sunday as packed lunches
    ("Taking it with you" shows weekdays only, so nobody could untick
    them, and they were saved and sent to the planner as lunches that
    have to travel cold);
  - the very first week (no intake yet) told the same about the weekend;
  - Today's lunch row saying "everyone · out" on a Saturday at home.

The rule now (rhythm.lunch_location_on): a day's own override, else the
standing answer Monday to Friday, else nothing.
"""
from __future__ import annotations

import datetime

from app import agent, tools
from app.tools import cooker, day_meals
from conftest import household_today


def _next_monday() -> str:
    today = household_today()
    return (today - datetime.timedelta(days=today.weekday()) + datetime.timedelta(days=7)).isoformat()


def _school_household() -> None:
    tools.add_member("Emily")
    tools.add_member("Sam")
    tools.add_member("Maya")
    tools.set_member_age_group("Maya", "child")
    tools.save_member_needs(lunch_needs={
        "Emily": ["cold_packed"], "Sam": ["cold_packed"], "Maya": ["cold_packed", "nut_free"],
    })


def _packed_suggestions(signed_in, week: str) -> dict[str, bool]:
    r = signed_in.get(f"/api/week/{week}/intake")
    assert r.status_code == 200, r.text
    return {s["date"]: s["suggested_packed"] for s in r.json()["rhythm_packed_lunch_suggestions"]}


def test_plan_a_week_suggests_packed_lunches_on_school_days_only(signed_in):
    _school_household()
    week = _next_monday()
    days = tools._week_dates(week)
    suggested = _packed_suggestions(signed_in, week)
    assert [d for d, packed in suggested.items() if packed] == days[:5]
    assert days[5] not in suggested and days[6] not in suggested, \
        "Saturday and Sunday: nobody said anything about those lunches"


def test_the_first_week_tells_the_planner_weekday_lunches_only(monkeypatch):
    _school_household()
    week = _next_monday()
    seen = {}

    def fake(ctx):
        seen.update(ctx)
        return []

    monkeypatch.setattr(agent, "generate_weekly_plan_llm", fake)
    try:
        agent.generate_weekly_plan(week)
    except Exception:
        pass  # an empty week is refused; the context is what this reads
    assert seen["intake"]["packed_lunch_days"] == tools._week_dates(week)[:5]


def _lunch_where(day: str) -> list[tuple[str, str]]:
    view = cooker.get_cooker_view()
    row = next(r for r in day_meals.for_day(day, view) if r["slot"] == "lunch")
    return [(ln["who"], ln["where"]) for ln in row["lines"]]


def test_a_saturday_lunch_at_home_is_not_out():
    _school_household()
    week = _next_monday()
    days = tools._week_dates(week)
    plan = tools.create_weekly_plan(week)["weekly_plan_id"]
    for d in (days[0], days[5]):
        tools.plan_meal(d, "Pasta salad", slot="lunch", weekly_plan_id=plan)
    tools.approve_weekly_plan(plan, approved_by="Emily")
    assert _lunch_where(days[0]) == [("everyone", "out")], "Monday is a packed day"
    assert _lunch_where(days[5]) == [("everyone", "")]


def test_a_weekend_day_of_its_own_still_counts(signed_in):
    """EDGE. "Sam works Saturdays" is an override on Saturday, and that
    still says out — only the standing answer stops at Friday."""
    _school_household()
    tools.set_lunch_location("Sam", "home")
    tools.set_lunch_location("Emily", "home")
    tools.set_lunch_location("Sam", "out", weekday="Saturday")
    week = _next_monday()
    days = tools._week_dates(week)
    assert tools.effective_lunch_location("Sam", "Saturday") == "out"
    assert tools.effective_lunch_location("Sam", "Sunday") is None
    assert tools.effective_lunch_location("Sam", "Monday") == "home"
    suggested = _packed_suggestions(signed_in, week)
    assert suggested[days[5]] is True
    assert days[6] not in suggested


def test_a_weekday_override_still_beats_the_standing_answer():
    """EDGE. Mondays different from the rest is the bridge's own shape."""
    tools.add_member("Ravi")
    tools.save_member_needs(lunch_needs={"Ravi": {"needs": ["cold_packed"], "days": {"monday": ["reheat"]}}})
    assert tools.effective_lunch_location("Ravi", "Monday") == "home"
    assert tools.effective_lunch_location("Ravi", "Tuesday") == "out"
    assert tools.effective_lunch_location("Ravi", "Saturday") is None
