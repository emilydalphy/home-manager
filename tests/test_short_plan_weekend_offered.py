"""
The weekend after a short plan is still offered the morning it begins
(defect hunt 2026-10-18).

A household approves a Wednesday-to-Friday plan. On Friday, Plan says
"This plan ends today." and offers Sat 24–Fri 30 ("Plan from tomorrow"),
and so does the Now nudge. At midnight nothing covers today any more, so
suggest_planning_period's Friday rule read the week as UNPLANNED and
skipped to next Monday: Saturday's nudge, Plan's button and the plan-week
default all said Oct 26–Nov 1, and the weekend between — today and
tomorrow, no meals — was never offered again.

The rule's own approved branch already meant "this week is planned: offer
what follows the plan". It only looked at a plan covering TODAY; a plan
that held part of this week and ended before today counts too now.

Clock: weekly_plan.date is pinned to one date for both clocks (the
stale-draft file's shape — conftest.pin_household_clock), because these
are weekday rules said with one fixed week. 2026-10-19 is a Monday; the
household has never answered the ready day, so its weeks run Mon–Sun.
"""
from __future__ import annotations

import datetime

import pytest

from app import tools
from app.db import get_conn
from app.tools import weekly_plan as _weekly_plan
from conftest import pin_household_clock

WEDNESDAY = "2026-10-21"
FRIDAY = "2026-10-23"
SATURDAY = "2026-10-24"
SUNDAY = "2026-10-25"
NEXT_MONDAY = "2026-10-26"
LAST_MONDAY = "2026-10-12"


class _FixedToday(datetime.date):
    _value: "datetime.date | None" = None

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


def _approved(start: str, day_count: int) -> int:
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


def _offers() -> tuple[str, str | None, str]:
    """What Plan's default, Now's nudge and Plan's next-stretch button say."""
    period = tools.suggest_planning_period()
    nudge = tools.get_week_planning_nudge()
    menu = tools.get_week_menu()
    return period["start_date"], nudge.get("week_start") if nudge["show"] else None, menu["next_period"]["start_date"]


def test_saturday_after_a_wednesday_to_friday_plan_offers_from_today(pin_today):
    _approved(WEDNESDAY, 3)

    pin_today(FRIDAY)
    friday = _offers()
    pin_today(SATURDAY)
    saturday = _offers()

    assert friday == (SATURDAY, SATURDAY, SATURDAY)
    # Was (NEXT_MONDAY, NEXT_MONDAY, NEXT_MONDAY): the weekend skipped.
    assert saturday == (SATURDAY, SATURDAY, SATURDAY)
    assert tools.suggest_planning_period()["day_count"] == 7


def test_sunday_still_offers_the_rest_of_the_gap_from_today(pin_today):
    _approved(WEDNESDAY, 3)
    pin_today(SUNDAY)

    assert _offers() == (SUNDAY, SUNDAY, SUNDAY)


def test_a_week_with_nothing_approved_in_it_still_moves_on_to_next_week(pin_today):
    # Last week's plan ended before this week began: this week was never
    # planned, and the Friday rule (2026-09-11) is unchanged for it.
    _approved(LAST_MONDAY, 7)
    pin_today(SATURDAY)

    period = tools.suggest_planning_period()
    assert period["start_date"] == NEXT_MONDAY
    assert tools.get_week_planning_nudge()["week_start"] == NEXT_MONDAY


def test_no_offer_over_next_week_when_it_is_already_planned(pin_today):
    # The same as Friday evening: the stretch after the plan runs into an
    # approved week, so Now says nothing rather than offering to re-plan it.
    _approved(WEDNESDAY, 3)
    _approved(NEXT_MONDAY, 7)

    pin_today(FRIDAY)
    assert tools.get_week_planning_nudge()["show"] is False
    pin_today(SATURDAY)
    assert tools.get_week_planning_nudge()["show"] is False


def test_a_plan_that_ended_before_the_offer_moved_on_keeps_the_friday_rule(pin_today):
    # Mon–Wed: Wednesday never offered "Plan from tomorrow", so Saturday
    # reads this week as unplanned and offers next week, as it always has
    # (test_planning_periods' dismissal test pins the same).
    _approved("2026-10-19", 3)
    pin_today(SATURDAY)

    assert tools.suggest_planning_period()["start_date"] == NEXT_MONDAY
