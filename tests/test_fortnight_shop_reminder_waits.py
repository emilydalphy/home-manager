"""
The "plan it before you shop" reminder waits on a two-week plan the same
way the ordinary nudge does (Emily, 2026-10-20, follow-up to
overnight/fortnight-not-nudged-early). On a plan longer than seven days the
reminder only brings up "plan next" when two days or fewer of the current
plan are left; plans of seven days or fewer are unchanged.
"""
from __future__ import annotations

import datetime

import pytest

from conftest import pin_household_clock
from app import tools
from app.db import get_conn
from app.tools import weekly_plan as _weekly_plan

from test_fortnight_not_nudged_early import _FixedToday, _approved_plan, pin_today  # noqa: F401

START = "2026-10-16"  # Friday: a fortnight is Oct 16-29


def test_fortnight_week_one_reminder_day_shows_no_card(pin_today):
    _approved_plan(START, 14)
    tools.set_shop_days(shop_day="thursday")  # reminder lands on Tuesday
    pin_today("2026-10-20")  # Tuesday, nine days left
    assert tools.get_week_planning_nudge()["show"] is False


def test_fortnight_reminder_card_from_two_days_left(pin_today):
    _approved_plan(START, 14)
    tools.set_shop_days(shop_day="thursday")
    pin_today("2026-10-27")  # Tuesday, two days left
    nudge = tools.get_week_planning_nudge()
    assert nudge["show"] is True
    assert nudge["week_start"] == "2026-10-30"
    assert nudge["shop_day"] == "thursday"


def test_fortnight_reminder_three_days_left_still_waits(pin_today):
    _approved_plan(START, 14)
    tools.set_shop_days(shop_day="wednesday")  # reminder on Monday
    pin_today("2026-10-26")  # three days left
    assert tools.get_week_planning_nudge()["show"] is False


def test_seven_day_plan_reminder_unchanged(pin_today):
    _approved_plan("2026-10-19", 7)  # Mon-Sun
    tools.set_shop_days(shop_day="thursday")
    pin_today("2026-10-20")  # Tuesday, reminder day
    nudge = tools.get_week_planning_nudge()
    assert nudge["show"] is True
    assert nudge["week_start"] == "2026-10-26"
    assert nudge["shop_day"] == "thursday"


def test_three_day_plan_reminder_unchanged(pin_today):
    _approved_plan("2026-10-19", 3)  # Mon-Wed
    tools.set_shop_days(shop_day="thursday")
    pin_today("2026-10-20")
    nudge = tools.get_week_planning_nudge()
    assert nudge["show"] is True
    assert nudge["week_start"] == "2026-10-22"
