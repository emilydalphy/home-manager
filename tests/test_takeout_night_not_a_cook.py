"""
A written-in takeout dinner is a meal, never a cook (overnight hunt,
2026-10-18; branch overnight/takeout-night-not-a-cook).

Plan has always read "Takeout pizza" (and the open slot's own "Takeout,
don't plan it" answer) as takeout — weekly_plan.written_in_not_a_cook. Today
did not: moves made it a `cook` move, so Now's card read "Cook this" over it,
the morning text could say "Start cooking at 6:00." and the evening nudge
texted "Tonight: Takeout pizza. Tap to start." at five o'clock.

Each test says whether it is a CATCH (red without the fix) or a GUARD.
"""
from __future__ import annotations

import datetime as dt
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from app import tools
from conftest import household_today

TODAY = household_today()
ISO_TODAY = TODAY.isoformat()
WEEK_START = (TODAY - dt.timedelta(days=2)).isoformat()
TORONTO = ZoneInfo("America/Toronto")


def _adult_opted_in():
    tools.add_member("Emily")
    tools.set_member_age_group("Emily", "adult")
    tools.set_morning_text(phone="416-555-0100", on=True, name="Emily")


def _plan(dinner: str) -> int:
    plan_id = tools.create_weekly_plan(WEEK_START)["weekly_plan_id"]
    tools.plan_meal(ISO_TODAY, dinner, slot="dinner", weekly_plan_id=plan_id,
                    add_ingredients_to_grocery_list=False)
    return plan_id


def _at(hour: int, minute: int = 0) -> datetime:
    return datetime.combine(TODAY, dt.time(hour, minute))


def _utc(hour: int, minute: int = 0) -> datetime:
    return datetime.combine(TODAY, dt.time(hour, minute), tzinfo=TORONTO).astimezone(timezone.utc)


def test_a_takeout_night_is_no_cook_no_nudge_and_named_in_the_morning():
    """CATCH. "Takeout pizza" written in for tonight: no cook move on Today,
    nothing sent at the nudge hour, and the morning text names it with no
    start clock."""
    _adult_opted_in()
    _plan("Takeout pizza")

    moves = tools.today_moves(day=TODAY, now=_at(17, 5))
    assert [m for m in moves["moves"] if m["kind"] == "cook"] == []
    assert moves["featured"] is None
    # Today's meals row still names the night.
    assert any("Takeout pizza" in row["dishes"] for row in moves["day_meals"])

    assert tools.build_evening_nudge(_at(17, 30)) is None
    calls = []
    assert tools.run_evening_nudges_once(
        now_utc=_utc(17, 31), send=lambda m, t: calls.append(t) or {"status": "ok"}
    ) == []
    assert calls == []

    morning = tools.build_morning_text(_at(7, 0), link=False, parts=["meals", "start"])
    assert morning == "Tonight: Takeout pizza."


def test_the_open_slots_takeout_answer_is_not_a_cook_either():
    """CATCH. Tapping "Takeout, don't plan it" on an open dinner wrote that
    label as the meal, and the nudge said "… Tap to start." over it."""
    _adult_opted_in()
    plan_id = _plan("Chicken Skewers")
    tools.plan_slot_open(plan_id, ISO_TODAY, "dinner", "I'd rather ask than guess tonight.")
    tools.resolve_open_slot(plan_id, ISO_TODAY, "dinner", "Takeout, don’t plan it")

    assert tools.build_evening_nudge(_at(17, 30)) is None
    assert [m for m in tools.today_moves(day=TODAY, now=_at(17, 5))["moves"] if m["kind"] == "cook"] == []


def test_a_saved_recipe_with_takeout_in_its_name_is_still_a_cook():
    """GUARD. The word check is for written-in meals only — a saved recipe
    called "Takeout-Style Pad Thai" is a real cook and still gets its nudge."""
    _adult_opted_in()
    tools.add_recipe(
        "Takeout-Style Pad Thai", ingredients=[{"item": "Rice noodles", "qty": "8 oz"}],
        prep_time_minutes=15, cook_time_minutes=15, default_servings=3,
    )
    _plan("Takeout-Style Pad Thai")

    cooks = [m for m in tools.today_moves(day=TODAY, now=_at(17, 5))["moves"] if m["kind"] == "cook"]
    assert [m["title"] for m in cooks] == ["Takeout-Style Pad Thai"]
    assert tools.build_evening_nudge(_at(17, 30), link=False) == "Tonight: Takeout-Style Pad Thai — 30 min. Tap to start."
