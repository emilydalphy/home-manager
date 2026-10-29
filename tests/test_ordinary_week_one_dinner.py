"""
"An ordinary week — one dinners across seven nights." (defect hunt,
2026-10-29, walking the week changeover.)

A draft that cooks one dish every night — here a Thursday-to-Wednesday
period taking over the end of an approved week, every dinner the same
bolognese — opened on Plan with that line. draft_opener._line_one put
"dinners" after any count, one included. Now one dish is "one dinner", and
one dinner on one night says only that ("none repeated" of a single dinner
says nothing).
"""
from __future__ import annotations

import datetime

from app import tools
from app.tools import draft_opener
from conftest import household_today


def _period(start: datetime.date, days: int) -> list[str]:
    return [(start + datetime.timedelta(days=i)).isoformat() for i in range(days)]


def _dinner(day: str, meal: str) -> dict:
    return {"date": day, "slot": "dinner", "slot_state": "planned", "meal": meal, "freeform_meal": ""}


def test_one_dish_every_night_is_one_dinner_on_the_draft():
    """The card's own walk: a seven-night draft, one dish, read off Plan."""
    tools.add_recipe("Bolognese", ingredients=[{"item": "Ground turkey", "qty": "2 lb", "category": "meat/seafood"}])
    start = household_today() + datetime.timedelta(days=1)
    plan = tools.create_weekly_plan(
        (start - datetime.timedelta(days=start.weekday())).isoformat(),
        content_start_date=start.isoformat(), day_count=7,
    )["weekly_plan_id"]
    for day in _period(start, 7):
        tools.plan_meal(day, "Bolognese", slot="dinner", weekly_plan_id=plan)
    opener = tools.get_week_menu(plan)["draft_opener"]
    assert opener[0] == "An ordinary week — one dinner across seven nights."
    assert not any("one dinners" in line for line in opener)


def test_one_dinner_on_one_night_says_only_that():
    period = _period(household_today(), 3)
    line = draft_opener._line_one([_dinner(period[0], "Chili")], None, period, [], None)
    assert line == "An ordinary week — one dinner."


def test_two_or_more_still_count_in_the_plural():
    """GUARD: the plural sentences are unchanged."""
    period = _period(household_today(), 7)
    two = [_dinner(d, "Chili" if i % 2 else "Tacos") for i, d in enumerate(period)]
    assert draft_opener._line_one(two, None, period, [], None) == "An ordinary week — two dinners across seven nights."
    three = [_dinner(d, m) for d, m in zip(period, ["Chili", "Tacos", "Salmon"])]
    assert draft_opener._line_one(three, None, period, [], None) == "An ordinary week — three dinners, none repeated."
