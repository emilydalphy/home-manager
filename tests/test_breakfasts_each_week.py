"""
"Should breakfasts change every week?" — Emily, 2026-10-31: ask each
household. Plan a week's Breakfast screen carries "Week to week": "The same
breakfasts each week" / "Something different", saved on the week's answers
(week_intake.breakfasts_each_week, append-only) and remembered into the
next week. "Something different" puts breakfast in the two-week no-repeat
window (meal_variety.no_repeat_slots); "the same" and never-asked keep
today's rhythm — breakfast may come back.
"""
from __future__ import annotations

import datetime
from pathlib import Path

from conftest import household_today

from app import tools
from app.tools import meal_variety

PLAN_WEEK = Path(__file__).resolve().parents[1] / "static" / "plan-week.html"


def _monday(weeks_on: int = 0) -> str:
    today = household_today()
    return (today - datetime.timedelta(days=today.weekday()) + datetime.timedelta(weeks=weeks_on)).isoformat()


def test_something_different_puts_breakfast_in_the_window_and_the_same_does_not():
    week = _monday()
    tools.save_week_intake(week, breakfasts_each_week="different")
    assert "breakfast" in meal_variety.no_repeat_slots(tools.get_week_intake(week))

    tools.save_week_intake(week, breakfasts_each_week="same")
    assert meal_variety.no_repeat_slots(tools.get_week_intake(week)) == ("dinner", "lunch")


def test_never_asked_is_todays_behaviour():
    week = _monday()
    saved = tools.save_week_intake(week, moods=["Comfort food"])
    assert saved["breakfasts_each_week"] == ""
    assert meal_variety.no_repeat_slots(saved) == ("dinner", "lunch")
    assert meal_variety.no_repeat_slots(None) == ("dinner", "lunch")


def test_the_answer_is_remembered_into_next_week(signed_in):
    this_week, next_week = _monday(), _monday(1)
    res = signed_in.post(f"/api/week/{this_week}/intake", json={"breakfasts_each_week": "different"})
    assert res.status_code == 200, res.text
    assert res.json()["breakfasts_each_week"] == "different"

    # Next week's screen opens on it ...
    prefill = signed_in.get(f"/api/week/{next_week}/intake").json()
    assert prefill["last_intake"]["breakfasts_each_week"] == "different"
    # ... and a first save that never opened the Breakfast screen keeps it.
    saved = tools.save_week_intake(next_week, moods=["Something quick"])
    assert saved["breakfasts_each_week"] == "different"
    assert "breakfast" in meal_variety.no_repeat_slots(saved)


def test_a_week_with_no_answers_still_holds_the_standing_answer():
    # Chat's generate_weekly_plan drafts a week nobody answered questions
    # for: no intake row, but last week's "Something different" still holds.
    tools.save_week_intake(_monday(), breakfasts_each_week="different")
    next_week = _monday(1)
    assert tools.get_week_intake(next_week) is None
    assert "breakfast" in meal_variety.no_repeat_slots(None, next_week)
    assert meal_variety.no_repeat_slots(None) == ("dinner", "lunch")

    tools.save_week_intake(_monday(), breakfasts_each_week="same")
    assert meal_variety.no_repeat_slots(None, next_week) == ("dinner", "lunch")


def test_a_new_revision_keeps_it_and_the_old_row_is_untouched():
    week = _monday()
    first = tools.save_week_intake(week, breakfasts_each_week="different")
    tools.save_week_intake(week, freeform="tacos on Friday")
    history = tools.get_week_intake_history(week)
    assert [h["breakfasts_each_week"] for h in history] == ["different", "different"]
    assert history[0]["intake_id"] == first["intake_id"]


def test_an_unknown_answer_is_refused(signed_in):
    res = signed_in.post(f"/api/week/{_monday()}/intake", json={"breakfasts_each_week": "sometimes"})
    assert res.status_code == 400
    assert tools.get_week_intake(_monday()) is None


def test_the_line_is_on_plan_a_week():
    html = PLAN_WEEK.read_text(encoding="utf-8")
    assert "breakfastSame: 'The same breakfasts each week'" in html
    assert "breakfastDifferent: 'Something different'" in html
    assert "payload.breakfasts_each_week = answers.breakfasts_each_week" in html
    # Shown only for a week that has a breakfast.
    assert "mealOnThisWeek(weekCells(), 'breakfast')" in html
