"""
"Plan next week ›" under a plan offers the household's WHOLE next stretch,
sized by its rhythm — not by whatever length the plan on screen happens to
be. Loop Board "Planning on a Sunday offered only the next 2 days instead
of the week" (Bug, High). Emily, Sunday 2026-09-13.

What she saw: the Plan tab's link offered two days, her All set screen
read "Sep 14–15 is planned … 6 meals · 6 cooks", and Now's nudge — built
from the rhythm — asked about Sep 14–20. Two screens, two spans.

Root cause: the link (static/shell.js, `#wk-plan-next`) was built on the
CLIENT as "the plan's start + its day_count, for its day_count days". Her
plan on screen was a two-day one (a Saturday sign-up's "this week" is
Sat–Sun — main._first_plan_window, pinned below — and a custom range or a
takeover remnant does the same), so the week after it was two days too.

Now the server says: weekly_plan.next_period_after, carried on
get_week_menu as `next_period`, sized by suggest_planning_period's
day_count — the same length the nudge offers — and shortened only when
another live plan already holds part of it, with the reason written down.

"Today" is pinned the way tests/test_stale_draft_front_page.py pins it;
the suite is date-dependent and this bug fires on a Sunday.
"""
from __future__ import annotations

import datetime
import types
from pathlib import Path

import pytest

from app import main as main_module
from app import tools
from app.db import get_conn
from app.tools import weekly_plan as _weekly_plan

SHELL_JS = (Path(__file__).resolve().parent.parent / "static" / "shell.js").read_text(encoding="utf-8")

SATURDAY = "2026-09-12"
SUNDAY = "2026-09-13"
THIS_MONDAY = "2026-09-07"
NEXT_MONDAY = "2026-09-14"
THURSDAY = "2026-09-10"


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
        # main._first_plan_window reads datetime.date.today() through its
        # own module — same shape as test_onboarding_reveal_stream.py.
        monkeypatch.setattr(
            main_module,
            "datetime",
            types.SimpleNamespace(
                date=_FixedToday,
                timedelta=datetime.timedelta,
                datetime=datetime.datetime,
                timezone=datetime.timezone,
            ),
        )
    return _pin


def _insert_plan(
    content_start: str, day_count: int, status: str = "approved",
    week_start: str | None = None, household: int = 1, planning_mode: str = "day_based",
) -> int:
    """A plan with one dinner on each of its days."""
    conn = get_conn()
    cur = conn.execute(
        "INSERT INTO weekly_plans (household_id, week_start_date, status, content_start_date, "
        "day_count, planning_mode) VALUES (?, ?, ?, ?, ?, ?)",
        (household, week_start or content_start, status, content_start, day_count, planning_mode),
    )
    plan_id = cur.lastrowid
    for day in tools.period_dates(content_start, day_count):
        conn.execute(
            "INSERT INTO meal_plan_entries (household_id, weekly_plan_id, date, slot, freeform_meal) "
            "VALUES (?, ?, ?, 'dinner', 'Chili')",
            (household, plan_id, day),
        )
    conn.commit()
    conn.close()
    return plan_id


# ---------- the bug, as seen ----------

class TestASundayOffersTheWholeComingWeek:
    def test_a_saturday_sign_up_really_leaves_a_two_day_plan_behind(self, pin_today):
        # How Emily's database most likely got its two-day plan: "this
        # week" on a Saturday is the remainder, Sat–Sun, filed under the
        # Monday. Not a bug in itself (a two-day part-week is what was
        # asked for) — but it is the plan the link below was sizing from.
        pin_today(SATURDAY)
        assert main_module._first_plan_window(False) == (THIS_MONDAY, 2, SATURDAY)

    def test_the_link_under_a_two_day_plan_offers_seven_days_not_two(self, pin_today):
        # Emily's Sunday: a two-day approved plan Sat 12 – Sun 13 on screen.
        two_days = _insert_plan(SATURDAY, 2, week_start=THIS_MONDAY)
        pin_today(SUNDAY)

        menu = tools.get_week_menu()

        assert menu["weekly_plan_id"] == two_days
        assert menu["day_count"] == 2, "the plan on screen really is two days"
        nxt = menu["next_period"]
        assert nxt["start_date"] == NEXT_MONDAY
        assert nxt["day_count"] == 7, "the week after a two-day plan is a week, not two more days"
        assert nxt["label"] == "Sep 14–20"
        assert nxt["shortened_reason"] is None
        assert nxt["is_planned"] is False
        assert nxt["is_current_period"] is False

    def test_plan_and_now_offer_the_same_stretch(self, pin_today):
        # The 2026-09-11 rule, reaching this link: Now's nudge and the Plan
        # tab's link name one span.
        _insert_plan(SATURDAY, 2, week_start=THIS_MONDAY)
        pin_today(SUNDAY)
        nxt = tools.get_week_menu()["next_period"]
        nudge = tools.get_week_planning_nudge()
        assert nudge["show"] is True
        assert (nudge["week_start"], nudge["day_count"], nudge["week_label"]) == (
            nxt["start_date"], nxt["day_count"], nxt["label"]
        )

    def test_it_rides_the_http_payload_the_plan_tab_reads(self, signed_in, pin_today):
        _insert_plan(SATURDAY, 2, week_start=THIS_MONDAY)
        pin_today(SUNDAY)
        menu = signed_in.get("/api/week-menu").json()
        assert menu["next_period"]["start_date"] == NEXT_MONDAY
        assert menu["next_period"]["day_count"] == 7

    def test_an_ordinary_week_still_offers_the_week_after_it(self, pin_today):
        # No change for the common case: a seven-day plan's next is the
        # next seven.
        _insert_plan(THIS_MONDAY, 7)
        pin_today(SUNDAY)
        nxt = tools.get_week_menu()["next_period"]
        assert (nxt["start_date"], nxt["day_count"]) == (NEXT_MONDAY, 7)

    def test_a_custom_period_is_followed_by_the_rhythms_length(self, pin_today):
        # Thursday-to-Thursday (8 days) on screen: what follows is a week,
        # from the day after it — not another eight.
        _insert_plan(THURSDAY, 8)
        pin_today(SUNDAY)
        nxt = tools.get_week_menu()["next_period"]
        assert (nxt["start_date"], nxt["day_count"]) == ("2026-09-18", 7)

    def test_an_as_we_go_household_is_offered_its_three_days(self, pin_today):
        # The rhythm, not seven: a household planning as it goes gets the
        # three days after its plan, whatever length that plan was.
        tools.set_planning_anchor("as_we_go")
        _insert_plan(SATURDAY, 2, week_start=THIS_MONDAY)
        pin_today(SUNDAY)
        nxt = tools.get_week_menu()["next_period"]
        assert (nxt["start_date"], nxt["day_count"]) == (NEXT_MONDAY, 3)

    def test_a_component_plan_carries_it_too(self, pin_today):
        _insert_plan(SATURDAY, 2, week_start=THIS_MONDAY, planning_mode="component_based")
        pin_today(SUNDAY)
        menu = tools.get_week_menu()
        assert menu["menu_is_suggested"] is True
        assert (menu["next_period"]["start_date"], menu["next_period"]["day_count"]) == (NEXT_MONDAY, 7)


# ---------- a shorter span, with the reason on it ----------

class TestAShorterSpanSaysWhy:
    def test_days_already_planned_shorten_the_offer_and_say_so(self, pin_today):
        # This week on screen; Thu–Sun of next week already drafted. The
        # offer stops at Wednesday and says why in one line, rather than
        # quietly offering a week whose generation would take those four
        # days over.
        _insert_plan(THIS_MONDAY, 7)
        _insert_plan("2026-09-17", 4, status="draft")
        pin_today(SUNDAY)
        nxt = tools.get_week_menu()["next_period"]
        assert (nxt["start_date"], nxt["day_count"]) == (NEXT_MONDAY, 3)
        assert nxt["label"] == "Sep 14–16"
        assert nxt["shortened_reason"] == "Sep 17–20 is already planned."
        assert nxt["is_planned"] is False

    def test_the_earliest_held_day_is_the_one_that_counts(self, pin_today):
        _insert_plan(THIS_MONDAY, 7)
        _insert_plan("2026-09-19", 2, status="draft")   # Sat–Sun
        _insert_plan("2026-09-16", 1, status="approved")  # Wednesday, one day
        pin_today(SUNDAY)
        nxt = tools.get_week_menu()["next_period"]
        assert (nxt["start_date"], nxt["day_count"]) == (NEXT_MONDAY, 2)
        assert nxt["shortened_reason"] == "Sep 16 is already planned."

    def test_a_stretch_already_planned_from_its_first_day_is_a_replan(self, pin_today):
        # Next week planned ahead in full: offered whole, flagged as a
        # re-plan (the link says "Re-plan"), no reason line — nothing was
        # shortened.
        _insert_plan(THIS_MONDAY, 7)
        _insert_plan(NEXT_MONDAY, 7, status="draft")
        pin_today(SUNDAY)
        nxt = tools.get_week_menu()["next_period"]
        assert (nxt["start_date"], nxt["day_count"]) == (NEXT_MONDAY, 7)
        assert nxt["is_planned"] is True
        assert nxt["shortened_reason"] is None

    def test_a_retired_plan_does_not_shorten_anything(self, pin_today):
        _insert_plan(THIS_MONDAY, 7)
        _insert_plan("2026-09-17", 4, status="retired")
        pin_today(SUNDAY)
        nxt = tools.get_week_menu()["next_period"]
        assert nxt["day_count"] == 7
        assert nxt["shortened_reason"] is None

    def test_another_households_plan_does_not_shorten_anything(self, pin_today):
        conn = get_conn()
        conn.execute("INSERT INTO households (id, name) VALUES (2, 'Other')")
        conn.commit()
        conn.close()
        _insert_plan(THIS_MONDAY, 7)
        _insert_plan("2026-09-17", 4, status="draft", household=2)
        pin_today(SUNDAY)
        nxt = tools.get_week_menu()["next_period"]
        assert nxt["day_count"] == 7
        assert nxt["shortened_reason"] is None


# ---------- a plan that has already ended ----------

class TestAPlanWhoseDaysHavePassed:
    def test_the_offer_is_the_standing_suggestion_not_the_week_after_august(self, pin_today):
        # An approved week from August is still shown when nothing covers
        # today (the fallback, by design). "Next" after it is not Aug 31 —
        # it is the household's standing suggestion: on a Sunday with no
        # approved plan covering today, next week.
        _insert_plan("2026-08-24", 7)
        pin_today(SUNDAY)
        nxt = tools.get_week_menu()["next_period"]
        assert (nxt["start_date"], nxt["day_count"]) == (NEXT_MONDAY, 7)
        assert nxt["is_current_period"] is False

    def test_and_on_a_thursday_it_is_this_week(self, pin_today):
        _insert_plan("2026-08-24", 7)
        pin_today(THURSDAY)
        nxt = tools.get_week_menu()["next_period"]
        assert (nxt["start_date"], nxt["day_count"]) == (THIS_MONDAY, 7)
        assert nxt["is_current_period"] is True


# ---------- the front-end half ----------

class TestThePlanTabReadsTheServersAnswer:
    def test_the_link_is_built_from_next_period(self):
        assert "data.next_period" in SHELL_JS
        assert "function nextPeriodFor(" in SHELL_JS
        # The label and the click handler go through the same function, so
        # what the link says and what it opens can't drift apart.
        assert SHELL_JS.count("nextPeriodFor(") >= 3

    def test_the_click_handler_no_longer_does_its_own_arithmetic(self):
        handler = SHELL_JS[SHELL_JS.index("steps.querySelector('#wk-plan-next')"):][:600]
        assert "addDaysLocal(start, dayCount)" not in handler
        assert "startPlanningWeek(period.start_date, period.day_count)" in handler

    def test_the_reason_is_said_once_in_the_notes(self):
        assert "next.shortened_reason" in SHELL_JS
