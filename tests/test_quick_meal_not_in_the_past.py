"""
The "quick" meal before a trip is one still ahead, and one that is eaten
(review, 2026-10-10).

slot_needs.set_away_stretch tags the slot just before a trip 'quick' —
"Last one before you head out — keeping it quick and grab-and-go." Recorded
on Saturday morning, a trip from Saturday breakfast tagged FRIDAY's dinner,
eaten the night before. The edge is now left off when that slot has already
gone by on the household's clock, or when the plan holds it planned_empty
(CLAUDE.md: a planned_empty slot is never offered as anything). It is not
walked further back — which earlier meal would count is a product call.

Every test is a CATCH — red on origin/main 1328b1a — unless its docstring
says GUARD.
"""
from __future__ import annotations

import datetime

from conftest import household_pin

from app import tools
from app.tools import slot_needs

MON = datetime.date(2026, 10, 5)
DAYS = [(MON + datetime.timedelta(days=i)).isoformat() for i in range(7)]
FRI, SAT, SUN = DAYS[4], DAYS[5], DAYS[6]


def _week(*, lunches: bool = False) -> None:
    """A dinner every night. Lunch planned (Pasta) or planned_empty ("asked
    for none"), breakfast always planned_empty."""
    tools.add_recipe("Pasta", ingredients=[{"item": "Pasta", "qty": "1 lb"}], default_servings=2)
    plan_id = tools.create_weekly_plan(DAYS[0])["weekly_plan_id"]
    for day in DAYS:
        tools.plan_slot_empty(plan_id, day, "breakfast", "You asked for none")
        if lunches:
            tools.plan_meal(day, "Pasta", slot="lunch", weekly_plan_id=plan_id)
        else:
            tools.plan_slot_empty(plan_id, day, "lunch", "You asked for none")
        tools.plan_meal(day, "Pasta", slot="dinner", weekly_plan_id=plan_id)
    tools.approve_weekly_plan(plan_id, "Emily")


def _on(frozen_today, day: str, hour: int, minute: int = 0) -> None:
    frozen_today(household_pin(hour, minute, on=datetime.date.fromisoformat(day)))


def test_a_trip_from_this_mornings_breakfast_does_not_tag_last_nights_dinner(frozen_today):
    """The reported shape, recorded at 7am Saturday."""
    _on(frozen_today, SAT, 7)
    _week()

    out = slot_needs.set_away_stretch(SAT, "breakfast", SAT, "lunch")

    assert out["quick_slot"] is None and out["quick_slots"] == []
    assert slot_needs.get_slot_need(FRI, "dinner")["need"] == "normal"


def test_an_earlier_meal_today_that_has_gone_by_is_not_tagged(frozen_today):
    """Recorded at 3pm for a trip from tonight's dinner: lunch was at noon."""
    _on(frozen_today, SAT, 15)
    _week(lunches=True)

    out = slot_needs.set_away_stretch(SAT, "dinner", SUN, "dinner")

    assert out["quick_slot"] is None
    assert slot_needs.get_slot_need(SAT, "lunch")["need"] == "normal"


def test_a_planned_empty_slot_is_not_the_quick_meal(frozen_today):
    """Lunch is "asked for none": nothing there to keep quick."""
    _on(frozen_today, FRI, 9)
    _week()

    out = slot_needs.set_away_stretch(SAT, "dinner", SUN, "dinner")

    assert out["quick_slot"] is None
    assert slot_needs.get_slot_need(SAT, "lunch")["need"] == "normal"


def test_a_real_meal_still_ahead_is_still_the_quick_one(frozen_today):
    """GUARD — green on both sides. Recorded Friday morning, Saturday's
    planned lunch is the last meal before a Saturday-dinner trip."""
    _on(frozen_today, FRI, 9)
    _week(lunches=True)

    out = slot_needs.set_away_stretch(SAT, "dinner", SUN, "dinner")

    assert out["quick_slot"] == {"date": SAT, "slot": "lunch"}
    assert slot_needs.get_slot_need(SAT, "lunch")["need"] == "quick"


def test_with_no_plan_yet_the_edge_still_lands(frozen_today):
    """GUARD — green on both sides. Intake time, before any plan: the need
    is what the generator will read."""
    _on(frozen_today, FRI, 9)

    out = slot_needs.set_away_stretch(SAT, "dinner", SUN, "dinner")

    assert out["quick_slot"] == {"date": SAT, "slot": "lunch"}
