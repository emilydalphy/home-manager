"""
After midnight on an approved week's last night, Today showed both
"Tomorrow needs a dinner · Pick" and "Shall I put Oct 26–Nov 1 together?"
— one gap asked about twice. Emily, 2026-10-31: when next week has no plan
at all, only the week card; "Tomorrow needs a dinner" stays for a night
with no plan the week card isn't offering, and an open night inside a plan
keeps its own "needs your call" card.
"""
from __future__ import annotations

import datetime

import pytest

from app import tools
from test_intake_fixes_2026_09_21b import _insert_plan

TODAY = "2026-10-25"  # Sunday, the last day of an approved Oct 19–25
NOON = datetime.datetime(2026, 10, 25, 12, 0)


def _setup():
    tools.add_recipe("Chili", ingredients=[{"item": "beans", "qty": "1 tin"}],
                     prep_time_minutes=10, cook_time_minutes=20)
    _insert_plan("2026-10-19", 7)  # approved, a dinner on every night


def _dinner_titles():
    return [i["title"] for i in tools.get_needs_you_items(now=NOON)
            if i["type"] in ("dinner_decision", "dinner_open")]


@pytest.mark.today(TODAY)
def test_no_plan_next_week_shows_only_the_week_card():
    _setup()
    nudge = tools.get_week_planning_nudge()
    assert nudge["show"] and nudge["week_start"] == "2026-10-26"
    assert _dinner_titles() == []


@pytest.mark.today(TODAY)
def test_with_the_week_card_dismissed_tomorrow_is_asked_again():
    _setup()
    tools.dismiss_notification("plan_week_nudge:2026-10-26")
    assert _dinner_titles() == ["Tomorrow needs a dinner"]


@pytest.mark.today(TODAY)
def test_an_open_night_in_next_weeks_plan_keeps_its_card():
    _setup()
    plan = tools.create_weekly_plan("2026-10-26", day_count=7)["weekly_plan_id"]
    tools.plan_meal("2026-10-26", "Chili", slot="dinner", weekly_plan_id=plan)
    from app.db import get_conn
    with get_conn() as conn:
        conn.execute("UPDATE meal_plan_entries SET slot_state = 'open', open_reason = 'Your call' "
                     "WHERE weekly_plan_id = ? AND date = '2026-10-26' AND slot = 'dinner'", (plan,))
    assert _dinner_titles() == ["Tomorrow’s dinner needs your call"]
