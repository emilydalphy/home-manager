"""
A two-week plan is not nudged about the period after it on its fifth day
(defect hunt 2026-10-20, branch overnight/fortnight-not-nudged-early).

Driven: an approved plan Oct 16-29, today Tue Oct 20. Today's card read
NEXT WEEK / "Shall I put Oct 30-Nov 5 together?" with nine days still
planned after today, because `_attention_moves_on` counted the fifth day
of ANY period as "only the weekend left". The rule is how much of the
period is left (PLAN_AHEAD_FROM_WEEKDAY's own comment), so a long period
moves on when two days are left, as a seven-day week does on its Friday.
"""
from __future__ import annotations

import datetime

import pytest

from conftest import pin_household_clock
from app import tools
from app.db import get_conn
from app.tools import weekly_plan as _weekly_plan

START = "2026-10-16"  # a Friday: Oct 16-29


class _FixedToday(datetime.date):
    _value = None

    @classmethod
    def today(cls):
        return cls._value


@pytest.fixture
def pin_today(monkeypatch):
    def _pin(iso_date: str):
        _FixedToday._value = datetime.date.fromisoformat(iso_date)
        monkeypatch.setattr(_weekly_plan, "date", _FixedToday)
        pin_household_clock(monkeypatch)
    return _pin


def _approved_plan(start: str, day_count: int) -> int:
    conn = get_conn()
    cur = conn.execute(
        "INSERT INTO weekly_plans (household_id, week_start_date, status, content_start_date, day_count) "
        "VALUES (1, ?, 'approved', ?, ?)",
        (start, start, day_count),
    )
    plan_id = cur.lastrowid
    for day in tools.period_dates(start, day_count):
        conn.execute(
            "INSERT INTO meal_plan_entries (household_id, weekly_plan_id, date, slot, freeform_meal) "
            "VALUES (1, ?, ?, 'dinner', 'Chili')",
            (plan_id, day),
        )
    conn.commit()
    conn.close()
    return plan_id


def test_the_fifth_day_of_a_fortnight_offers_nothing(signed_in, pin_today):
    _approved_plan(START, 14)
    pin_today("2026-10-20")
    nudge = signed_in.get("/api/week/plan-nudge").json()
    assert nudge["show"] is False


def test_a_fortnight_is_followed_once_two_days_are_left(pin_today):
    _approved_plan(START, 14)
    pin_today("2026-10-26")  # three days left after today: not yet
    assert tools.get_week_planning_nudge()["show"] is False
    pin_today("2026-10-27")  # two left, as a week's Friday has
    nudge = tools.get_week_planning_nudge()
    assert nudge["show"] is True
    assert nudge["week_start"] == "2026-10-30"


@pytest.mark.parametrize("day_count", [3, 5, 6, 7])
def test_a_period_of_a_week_or_less_moves_on_the_day_it_always_did(day_count):
    # Unchanged for every period seven days or shorter: the old rule,
    # day by day, against the new one.
    start = datetime.date(2026, 10, 19)
    for offset in range(day_count):
        today = start + datetime.timedelta(days=offset)
        old = offset >= _weekly_plan.PLAN_AHEAD_FROM_WEEKDAY or (day_count - 1 - offset) <= 1
        assert _weekly_plan._attention_moves_on(today, start.isoformat(), day_count) is old, (day_count, offset)
