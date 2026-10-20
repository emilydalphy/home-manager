"""Taking a night's guests off reaches the table — overnight/guests-taken-off-reach-the-table.

The intake's guest steppers are pushed into attendance on every save
(week_intake._sync_guest_attendance), so "hosting 2 on Friday" is the same
fact as a bigger headcount. Taking them OFF was lost: a screen drops a
night's guests by deleting that date from guest_counts (the re-plan grid's
stepper at 0, a day taken off "Which days?"), and the sync only walked the
dates still in the map. Attendance kept "Dinner for 4 — 2 guests", and the
draft, the shopping list and the morning text all went on feeding four.
"""
import datetime

import pytest

from app import agent, tools
from conftest import household_today


def _week() -> str:
    today = household_today()
    monday = today - datetime.timedelta(days=today.weekday())
    return (monday + datetime.timedelta(days=7)).isoformat()


@pytest.fixture
def couple():
    tools.add_member("Emily")
    tools.add_member("Vineeth")


def _friday(week: str) -> str:
    return tools.period_dates(week, 7)[4]


def test_dropping_the_guests_from_the_answers_takes_them_off_the_table(couple, signed_in):
    week = _week()
    friday = _friday(week)
    res = signed_in.post(f"/api/week/{week}/intake", json={
        "night_tags": {friday: ["guests"]},
        "guest_counts": {friday: {"adults": 2, "children": 0}},
    })
    assert res.status_code == 200, res.text
    assert tools.get_slot_attendance(friday, "dinner")["headcount"] == 4

    # The household says nobody's coming after all: the date leaves the map.
    res = signed_in.post(f"/api/week/{week}/intake", json={"night_tags": {}, "guest_counts": {}})
    assert res.status_code == 200, res.text

    att = tools.get_slot_attendance(friday, "dinner")
    assert att["guest_count"] == 0
    assert att["headcount"] == 2
    assert "guest" not in att["summary"]
    shown = signed_in.get(f"/api/week/{week}/attendance").json()["attendance"]
    assert "guest" not in str((shown.get(friday) or {}).get("dinner", {}).get("summary", ""))


def test_taking_the_day_off_the_week_takes_its_guests_off_too(couple):
    week = _week()
    friday = _friday(week)
    tools.save_week_intake(week, night_tags={friday: ["guests"]},
                           guest_counts={friday: {"adults": 3, "children": 0}})
    assert tools.get_slot_attendance(friday, "dinner")["guest_count"] == 3

    saved = tools.save_week_intake(week, skipped_days=[friday])
    assert friday not in saved["guest_counts"]
    assert tools.get_slot_attendance(friday, "dinner")["guest_count"] == 0


def test_a_count_set_since_is_not_the_intakes_to_take_back(couple):
    """Chat or the day sheet changed the number after the intake wrote it:
    that count is somebody's newer answer, and an intake save that merely
    no longer carries the date must leave it alone."""
    week = _week()
    friday = _friday(week)
    tools.save_week_intake(week, night_tags={friday: ["guests"]},
                           guest_counts={friday: {"adults": 2, "children": 0}})
    tools.set_guest_count(friday, "dinner", 5)

    tools.save_week_intake(week, night_tags={}, guest_counts={})
    assert tools.get_slot_attendance(friday, "dinner")["guest_count"] == 5


def test_a_save_that_does_not_touch_guests_leaves_them(couple):
    week = _week()
    friday = _friday(week)
    tools.save_week_intake(week, night_tags={friday: ["guests"]},
                           guest_counts={friday: {"adults": 2, "children": 0}})
    tools.save_week_intake(week, moods=["Something warm"])  # Q2: guest_counts inherited
    assert tools.get_slot_attendance(friday, "dinner")["guest_count"] == 2


def test_the_next_draft_is_planned_for_the_household_not_the_guests(couple, monkeypatch):
    week = _week()
    friday = _friday(week)
    tools.add_recipe("Chili", ingredients=[{"item": "beans", "qty": "1 tin"}],
                     prep_time_minutes=10, cook_time_minutes=20)
    seen = {}

    def _fake(context):
        seen["attendance"] = context["attendance"]
        return [
            {"date": d, "slot": s, "meal_name": "Chili", "is_new_recipe": False, "reasoning": "fits"}
            for d in tools.period_dates(week, 7) for s in tools.WEEK_SLOTS
        ]

    monkeypatch.setattr(agent, "generate_weekly_plan_llm", _fake)
    tools.save_week_intake(week, night_tags={friday: ["guests"]},
                           guest_counts={friday: {"adults": 2, "children": 0}})
    tools.save_week_intake(week, night_tags={}, guest_counts={})
    agent.generate_weekly_plan(week)

    friday = [a for a in seen["attendance"]["slots_with_a_different_table"]
              if a["date"] == friday and a["slot"] == "dinner"]
    assert not any(a["guests"] or a["serves"] != 2 for a in friday), friday
