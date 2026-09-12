"""
A draft whose week has ended is no longer the front page, and Plan and Now
name the same week — Emily, 2026-09-11.

Seen on Friday 2026-09-11: the Plan tab opened on "This week · Aug 24–30 ·
a draft, your turn" with an Approve button, for a week that had ended
twelve days earlier, while Now asked "Shall I put Sep 7–13 together?" — a
week with two days left. Two screens, two weeks, and the older one won.

Three things are pinned here:

1.  **An expired draft is retired, not led with.** The lazy sweep
    (retire_expired_drafts) runs when the Plan tab or the nudge is read;
    _current_weekly_plan_row's fallback refuses such a draft on its own
    too, so chat resolves the same way between sweeps. The data stays.
2.  **One source for "which week".** suggest_planning_period is what the
    nudge, the Plan tab's empty state and the planning-period endpoint all
    read, and with a fixed "today" they are asserted equal.
3.  **From Friday, "this week" means next week when this week has no
    approved plan** (PLAN_AHEAD_FROM_WEEKDAY = 4). Thursday still offers
    this week. A household with an approved plan covering today keeps it
    and is offered next week as NEXT WEEK.

"Today" is pinned the way tests/test_planning_periods.py pins it — by
replacing weekly_plan's `date` with a subclass whose today() is fixed —
since _current_weekly_plan_row and the nudge call date.today() directly.
"""
from __future__ import annotations

import datetime
from pathlib import Path

import pytest

from app import tools
from app.db import get_conn
from app.tools import weekly_plan as _weekly_plan

SHELL_JS = (Path(__file__).resolve().parent.parent / "static" / "shell.js").read_text(encoding="utf-8")

# The week it was seen in. 2026-09-07 is a Monday.
THURSDAY = "2026-09-10"
FRIDAY = "2026-09-11"
THIS_MONDAY = "2026-09-07"
NEXT_MONDAY = "2026-09-14"
STALE_DRAFT_START = "2026-08-24"  # the draft on Emily's own database


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
    return _pin


def _insert_plan(start: str, status: str, day_count: int = 7, meal: str = "Chili") -> int:
    """A plan with one dinner on each of its days, filed under its start."""
    conn = get_conn()
    cur = conn.execute(
        "INSERT INTO weekly_plans (household_id, week_start_date, status, content_start_date, day_count) "
        "VALUES (1, ?, ?, ?, ?)",
        (start, status, start, day_count),
    )
    plan_id = cur.lastrowid
    for day in tools.period_dates(start, day_count):
        conn.execute(
            "INSERT INTO meal_plan_entries (household_id, weekly_plan_id, date, slot, freeform_meal) "
            "VALUES (1, ?, ?, 'dinner', ?)",
            (plan_id, day, meal),
        )
    conn.commit()
    conn.close()
    return plan_id


def _plan_row(plan_id: int) -> dict:
    conn = get_conn()
    row = conn.execute("SELECT * FROM weekly_plans WHERE id = ?", (plan_id,)).fetchone()
    conn.close()
    return dict(row)


# ---------- 1. the expired draft ----------

class TestAnExpiredDraftIsNotTheFrontPage:
    def test_the_plan_tab_does_not_open_on_a_draft_whose_week_has_ended(self, pin_today):
        # The exact shape of Emily's database on 2026-09-11: one draft,
        # never approved, for Aug 24–30, and nothing else.
        stale = _insert_plan(STALE_DRAFT_START, "draft")
        pin_today(FRIDAY)

        menu = tools.get_week_menu()

        assert menu["weekly_plan_id"] is None, "a draft from twelve days ago was handed back as the current week"
        assert menu["days"] == []
        # And the screen is told which week to name instead.
        assert menu["suggested_period"]["start_date"] == NEXT_MONDAY
        # The sweep retired it, with the reason written down.
        row = _plan_row(stale)
        assert row["status"] == "retired"
        assert row["retired_reason"] == "expired_draft"

    def test_retiring_keeps_the_draft_and_its_meals(self, pin_today):
        stale = _insert_plan(STALE_DRAFT_START, "draft")
        pin_today(FRIDAY)
        tools.get_week_menu()
        row = _plan_row(stale)
        # "Don't lead with it", not "delete it": the period is intact and
        # the meals are still readable by id.
        assert tools.plan_period(row) == (STALE_DRAFT_START, 7)
        assert len(tools.get_weekly_plan(stale)["meals"]) == 7

    def test_chat_does_not_resolve_to_an_expired_draft_even_before_the_sweep(self, pin_today):
        # get_weekly_plan() with no id is what the chat tools call. It goes
        # through _current_weekly_plan_row's fallback, which used to return
        # the newest non-retired plan whatever its dates. No sweep runs on
        # this path, so this pins the query itself.
        stale = _insert_plan(STALE_DRAFT_START, "draft")
        pin_today(FRIDAY)
        assert tools.get_weekly_plan()["weekly_plan_id"] is None
        assert _plan_row(stale)["status"] == "draft", "this path must not write"

    def test_the_sweep_runs_when_the_nudge_is_read(self, pin_today):
        stale = _insert_plan(STALE_DRAFT_START, "draft")
        pin_today(FRIDAY)
        tools.get_week_planning_nudge()
        assert _plan_row(stale)["status"] == "retired"

    def test_a_draft_ending_yesterday_is_retired_and_one_ending_today_is_not(self, pin_today):
        # The threshold is the morning after the last day — no grace
        # period. A draft still holding today is still somebody's decision.
        ended_yesterday = _insert_plan("2026-09-04", "draft")   # Fri Sep 4 – Thu Sep 10
        ends_today = _insert_plan("2026-09-05", "draft")        # Sat Sep 5 – Fri Sep 11
        pin_today(FRIDAY)
        retired = tools.retire_expired_drafts()
        assert retired == [ended_yesterday]
        assert _plan_row(ends_today)["status"] == "draft"

    def test_an_approved_plan_whose_week_has_passed_is_left_alone(self, pin_today):
        # Only drafts. An approved week was the household's real week: it
        # was shopped for and cooked from, and it is not this sweep's
        # business to rewrite that.
        old = _insert_plan(STALE_DRAFT_START, "approved")
        pin_today(FRIDAY)
        assert tools.retire_expired_drafts() == []
        assert _plan_row(old)["status"] == "approved"

    def test_a_legacy_row_with_unset_period_columns_is_read_as_seven_days(self, pin_today):
        # A plan written before periods existed carries the '' / 0
        # sentinel; the sweep's SQL has to resolve it the way plan_period
        # does, or it would retire (or spare) the wrong rows.
        conn = get_conn()
        legacy = conn.execute(
            "INSERT INTO weekly_plans (household_id, week_start_date, status) VALUES (1, ?, 'draft')",
            (STALE_DRAFT_START,),
        ).lastrowid
        conn.commit()
        conn.close()
        pin_today("2026-08-30")  # its last day: still live
        assert tools.retire_expired_drafts() == []
        pin_today("2026-08-31")  # the morning after
        assert tools.retire_expired_drafts() == [legacy]

    def test_a_takeover_records_its_own_reason(self, pin_today):
        # The other way a plan retires. Both reasons are legible in the
        # column, so a retired row is never a mystery.
        old = _insert_plan(THIS_MONDAY, "draft")
        new = _insert_plan(THIS_MONDAY, "draft", meal="Katsu")
        tools.retire_overlapping_plans(new, THIS_MONDAY, 7)
        assert _plan_row(old)["status"] == "retired"
        assert _plan_row(old)["retired_reason"] == "superseded"

    def test_the_sweep_is_scoped_to_the_household(self, pin_today):
        # Another household's expired draft is not this household's to
        # retire.
        conn = get_conn()
        conn.execute("INSERT INTO households (id, name) VALUES (2, 'Other')")
        other = conn.execute(
            "INSERT INTO weekly_plans (household_id, week_start_date, status, content_start_date, day_count) "
            "VALUES (2, ?, 'draft', ?, 7)",
            (STALE_DRAFT_START, STALE_DRAFT_START),
        ).lastrowid
        conn.commit()
        conn.close()
        pin_today(FRIDAY)
        assert tools.retire_expired_drafts() == []
        assert _plan_row(other)["status"] == "draft"


# ---------- 2. one week, named the same on Plan and Now ----------

class TestPlanAndNowNameTheSameWeek:
    @pytest.mark.parametrize("today", [THURSDAY, FRIDAY])
    def test_the_three_endpoints_agree_with_nothing_planned(self, signed_in, pin_today, today):
        _insert_plan(STALE_DRAFT_START, "draft")  # the stale draft, as on Emily's database
        pin_today(today)

        menu = signed_in.get("/api/week-menu").json()
        nudge = signed_in.get("/api/week/plan-nudge").json()
        period = signed_in.get("/api/week/planning-period").json()

        assert menu["weekly_plan_id"] is None
        assert nudge["show"] is True
        assert menu["suggested_period"]["start_date"] == nudge["week_start"] == period["start_date"]
        assert menu["suggested_period"]["label"] == nudge["week_label"] == period["label"]
        assert menu["suggested_period"]["day_count"] == nudge["day_count"] == period["day_count"]

    def test_the_eyebrow_and_the_suggestion_agree_on_whether_it_is_this_week(self, pin_today):
        pin_today(FRIDAY)
        assert tools.get_week_planning_nudge()["is_current_week"] is tools.suggest_planning_period()["is_current_period"] is False
        pin_today(THURSDAY)
        assert tools.get_week_planning_nudge()["is_current_week"] is tools.suggest_planning_period()["is_current_period"] is True

    def test_the_plan_tab_reads_the_suggestion_the_nudge_is_built_from(self):
        # The front-end half: the empty Plan root names the server's
        # suggested period and says "Next week" when it is one, rather than
        # a bare "This week" that could mean either of two on a Friday.
        assert "data.suggested_period" in SHELL_JS
        assert "planningPeriodDefault = data.suggested_period" in SHELL_JS
        assert "is_current_period === false" in SHELL_JS
        assert "'Next week'" in SHELL_JS


# ---------- 3. from Friday, "this week" means next week ----------

class TestFromFridayThisWeekMeansNextWeek:
    def test_the_threshold_is_friday(self):
        assert tools.PLAN_AHEAD_FROM_WEEKDAY == 4  # 0 = Monday

    def test_friday_offers_next_week_when_nothing_is_approved(self, pin_today):
        pin_today(FRIDAY)
        nudge = tools.get_week_planning_nudge()
        assert nudge["show"] is True
        assert nudge["week_start"] == NEXT_MONDAY
        assert nudge["is_current_week"] is False
        suggestion = tools.suggest_planning_period()
        assert suggestion["start_date"] == NEXT_MONDAY
        assert suggestion["is_current_period"] is False

    def test_thursday_still_offers_this_week(self, pin_today):
        pin_today(THURSDAY)
        nudge = tools.get_week_planning_nudge()
        assert nudge["show"] is True
        assert nudge["week_start"] == THIS_MONDAY
        assert nudge["is_current_week"] is True
        assert tools.suggest_planning_period()["start_date"] == THIS_MONDAY

    def test_a_draft_covering_today_is_not_an_approved_plan(self, pin_today):
        # The draft stays where it is on Plan (it covers today, and it is
        # somebody's turn), but "this week is planned" means approved: the
        # suggestion and the nudge move on to next week.
        draft = _insert_plan(THIS_MONDAY, "draft")
        pin_today(FRIDAY)
        assert tools.get_week_menu()["weekly_plan_id"] == draft
        assert tools.suggest_planning_period()["start_date"] == NEXT_MONDAY
        nudge = tools.get_week_planning_nudge()
        assert nudge["show"] is True
        assert nudge["week_start"] == NEXT_MONDAY
        assert nudge["is_current_week"] is False

    def test_an_approved_week_keeps_today_and_is_offered_next_week(self, pin_today):
        approved = _insert_plan(THIS_MONDAY, "approved")
        pin_today(FRIDAY)
        # Plan shows the approved week; the planning-period default stays
        # on it (the "Re-plan this week" button still means this week).
        assert tools.get_week_menu()["weekly_plan_id"] == approved
        suggestion = tools.suggest_planning_period()
        assert suggestion["start_date"] == THIS_MONDAY
        assert suggestion["is_current_period"] is True
        # And Now offers the week after, as NEXT WEEK — the existing flag.
        nudge = tools.get_week_planning_nudge()
        assert nudge["show"] is True
        assert nudge["week_start"] == NEXT_MONDAY
        assert nudge["is_current_week"] is False

    def test_an_approved_week_is_not_nudged_about_next_week_on_thursday(self, pin_today):
        _insert_plan(THIS_MONDAY, "approved")
        pin_today(THURSDAY)
        assert tools.get_week_planning_nudge()["show"] is False

    def test_next_week_already_planned_ahead_means_nothing_to_offer(self, pin_today):
        # Friday, nothing covers today, but next week is already drafted:
        # "shall I put Sep 14–20 together?" would be an offer to redo it.
        ahead = _insert_plan(NEXT_MONDAY, "draft")
        pin_today(FRIDAY)
        assert tools.get_week_planning_nudge()["show"] is False
        # And Plan shows the week that was planned ahead, not an empty week.
        assert tools.get_week_menu()["weekly_plan_id"] == ahead

    def test_an_as_we_go_household_is_never_shifted(self, pin_today):
        tools.set_planning_anchor("as_we_go")
        pin_today(FRIDAY)
        suggestion = tools.suggest_planning_period()
        assert suggestion["start_date"] == FRIDAY
        assert suggestion["is_current_period"] is True

    def test_a_saturday_start_household_moves_on_the_same_distance_in(self, pin_today):
        # "Ready by Friday" is a Saturday-start week (Sep 5–11). The rule
        # is a distance into the period, not a calendar Friday: its fifth
        # day is Wednesday, so Tuesday still offers Sep 5–11 and Wednesday
        # offers Sep 12–18.
        tools.set_planning_anchor("friday")
        pin_today("2026-09-08")  # Tuesday, the period's fourth day
        assert tools.suggest_planning_period()["start_date"] == "2026-09-05"
        pin_today("2026-09-09")  # Wednesday, its fifth
        suggestion = tools.suggest_planning_period()
        assert suggestion["start_date"] == "2026-09-12"
        assert suggestion["is_current_period"] is False
