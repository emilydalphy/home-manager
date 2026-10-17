"""
Signing up late in the evening (Loop Board, 2026-10-17): the first week
opened on today, a day whose meals had all gone by — "0 meals · Nothing
planned" as the first card of the reveal.

Today stays IN the plan (a first week starting tomorrow left today in no
plan, and Today/Meals then asked to put a week together and for tonight's
dinner). Only the reveal skips the gone day; and the one-day floor in
main._first_plan_window counts that gone day as no day at all, carrying a
Saturday-11pm part-week through the next whole period rather than folding
forward and leaving Saturday out.

The clock is today_meals.household_now (conftest pins it to 00:01 for every
other test; these set the hour themselves).
"""
from __future__ import annotations

import datetime as _dt
import json

import freezegun

from app import main, tools
from app.tools import today_meals

import test_first_week_reveal_two_snacks_and_setup_pick as reveal

# A Monday week (conftest leaves the plan-ready day unanswered).
MONDAY = "2026-10-12"
THURSDAY = "2026-10-15"
SATURDAY = "2026-10-17"
NEXT_MONDAY = "2026-10-19"


def _window_at(monkeypatch, day: str, hour: int, minute: int = 0):
    at = _dt.datetime.fromisoformat(day).replace(hour=hour, minute=minute)
    monkeypatch.setattr(today_meals, "household_now", lambda: at)
    # Noon UTC: the same date in Toronto, so household_today agrees.
    with freezegun.freeze_time(f"{day}T16:00:00Z"):
        return main._first_plan_window(False)


def test_thursday_11pm_keeps_today_in_the_week_exactly_as_before(monkeypatch):
    # Unchanged from main: Today and Meals find tonight inside the plan.
    assert _window_at(monkeypatch, THURSDAY, 23) == (MONDAY, 4, THURSDAY)


def test_saturday_11pm_carries_on_through_next_week_instead_of_one_day(monkeypatch):
    assert _window_at(monkeypatch, SATURDAY, 23) == (MONDAY, 9, SATURDAY)


def test_saturday_afternoon_is_still_the_two_day_part_week(monkeypatch):
    assert _window_at(monkeypatch, SATURDAY, 15) == (MONDAY, 2, SATURDAY)


def test_sunday_still_folds_forward_whatever_the_hour(monkeypatch):
    assert _window_at(monkeypatch, "2026-10-18", 23) == (NEXT_MONDAY, 7, NEXT_MONDAY)


# ---------- the reveal, off the real week menu ----------

def _gone(plan_id, date, slot):
    tools.plan_slot_empty(
        weekly_plan_id=plan_id, meal_date=date, slot=slot, reason=today_meals.ALREADY_PAST_REASON,
        derived_from={"constraint": today_meals.ALREADY_PAST_CONSTRAINT},
    )


def _reveal_dates(menu_days) -> list[str]:
    out = reveal._run(reveal._reveal_harness() + f"""
console.log(JSON.stringify(revealDaysFromMenu({json.dumps(menu_days)}).map(d => d.date)));
""")
    return out


@reveal._needs_node
def test_the_reveal_opens_on_the_first_day_with_meals_on_it():
    plan_id = tools.create_weekly_plan(MONDAY)["weekly_plan_id"]
    friday = "2026-10-16"
    for slot in ("breakfast", "lunch", "dinner"):
        _gone(plan_id, THURSDAY, slot)
    tools.plan_meal(friday, "Tacos", slot="dinner", weekly_plan_id=plan_id)
    days = [d for d in tools.get_week_menu(plan_id)["days"] if d["date"] in (THURSDAY, friday)]
    assert _reveal_dates(days) == [friday]


@reveal._needs_node
def test_a_day_with_a_meal_still_ahead_keeps_its_card():
    # 3pm: breakfast and lunch gone, dinner still to come — the card stays,
    # gone rows and all, as on main.
    plan_id = tools.create_weekly_plan(MONDAY)["weekly_plan_id"]
    _gone(plan_id, THURSDAY, "breakfast")
    _gone(plan_id, THURSDAY, "lunch")
    tools.plan_meal(THURSDAY, "Tacos", slot="dinner", weekly_plan_id=plan_id)
    days = [d for d in tools.get_week_menu(plan_id)["days"] if d["date"] == THURSDAY]
    assert _reveal_dates(days) == [THURSDAY]


@reveal._needs_node
def test_saturday_11pm_first_plan_keeps_saturday_and_reveals_from_sunday(monkeypatch):
    # The whole path for the one shape this branch adds: a nine-day first
    # plan, generated, saved, and drawn by the reveal.
    from app import agent

    def fake_llm(ctx):
        return [
            {"date": d, "slot": slot, "meal_name": "Chili", "is_new_recipe": False, "reasoning": "fits"}
            for d in tools.period_dates(ctx["week_start_date"], ctx["day_count"])
            for slot in ("breakfast", "lunch", "dinner")
        ]

    monkeypatch.setattr(agent, "generate_weekly_plan_llm", fake_llm)
    at = _dt.datetime.fromisoformat(SATURDAY).replace(hour=23)
    monkeypatch.setattr(today_meals, "household_now", lambda: at)
    with freezegun.freeze_time(f"{SATURDAY}T16:00:00Z"):
        plan = main.onboarding_generate_first_plan()
        # Saturday is still inside a plan: nothing for Today or Meals to ask.
        assert tools.get_weekly_plan()["weekly_plan_id"] == plan["weekly_plan_id"]
    days = tools.get_week_menu(plan["weekly_plan_id"])["days"]
    saturday = next(d for d in days if d["date"] == SATURDAY)
    assert all(saturday[s]["past"] for s in ("breakfast", "lunch", "dinner"))
    shown = _reveal_dates(days)
    assert shown[0] == "2026-10-18" and shown[-1] == "2026-10-25" and len(shown) == 8
