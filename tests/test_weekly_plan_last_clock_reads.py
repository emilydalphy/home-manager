"""
The last six server-clock reads in weekly_plan.py, moved onto the
household's — and get_week_menu's clock resolved once and threaded down.

`overnight/weekly-plan-household-clock` (2026-09-15) moved the two reads
that had reproduced symptoms (`retire_expired_drafts`,
`_current_weekly_plan_row`) and filed the rest on their own card. These
are the rest:

1. `suggest_planning_period` — which week the Plan tab and the nudge offer.
2. `get_week_planning_nudge` — which read THREE clocks: the household's
   (inside retire_expired_drafts), the server's (its own `date.today()`),
   and the server's again underneath suggest_planning_period. One now.
3. `next_period_after` — whether "Plan next week ›" offers the day after
   this plan or falls back to the standing suggestion.
4. `discard_draft_plan` — which approved week the "Sep 14–20 is still your
   week." toast names, when a draft straddles two.
5. `week_receipt` — the weekday in "Nothing to thaw before Thursday."
6. `get_week_menu`'s component_based branch — whether an empty dinner gets
   the two-quick-dinner "Pick" rows. The day-based branch of the same
   function was moved on 2026-09-15; this is its twin, and a function
   answering about two different days is a new bug rather than a smaller
   one.

**None of these had a reproduced symptom before this branch.** The card
says so and it is worth repeating: a reviewer compared nudge payloads for
a Toronto and a UTC household at one straddling instant and got identical
output, because the nudge and the Plan tab both read
`suggest_planning_period` and so moved together. Self-consistent is not
the same as right, and the tests below are what turn "probably fine" into
a measured answer either way.

How the clock is frozen, and both directions: exactly the pattern of
tests/test_weekly_plan_household_clock.py. `_household_today()` is a SHIFT
— `date.today() + (cooker.household_now().date() - datetime.now().date())`
— so replacing `cooker.datetime` with a subclass whose `now()` answers one
fixed UTC instant moves the household's day and leaves the server's alone.
The zone lookup, the households.timezone read and the ZoneInfo fallback
all run for real. A Toronto evening puts the household a day BEHIND the
server (the production direction, and the reported class of bug); a Tokyo
morning puts it a day AHEAD.

Each test says in its own docstring whether it is a CATCH (red against the
unmodified app/) or a NO-REGRESSION GUARD (green either way, here to say
what did not change).
"""
from __future__ import annotations

import inspect
from datetime import date, datetime, time, timedelta, timezone

from app import tools
from app.db import get_conn
from app.tools import cooker as _cooker
from app.tools import weekly_plan as _wp


SERVER_TODAY = date.today()

# 01:30 UTC: Toronto is 21:30 the evening BEFORE, so the household is a
# whole day BEHIND the server — every evening between 8pm and midnight
# Eastern, and the direction production actually has.
UTC_EVENING = datetime.combine(SERVER_TODAY, time(1, 30), tzinfo=timezone.utc)
# 23:30 UTC: Tokyo is 08:30 the morning AFTER — the household a day AHEAD.
UTC_LATE = datetime.combine(SERVER_TODAY, time(23, 30), tzinfo=timezone.utc)

TORONTO = "America/Toronto"
TOKYO = "Asia/Tokyo"


def _set_timezone(name: str) -> None:
    conn = get_conn()
    conn.execute(
        "UPDATE households SET timezone = ? WHERE id = ?", (name, tools.household_id())
    )
    conn.commit()
    conn.close()


def _freeze(monkeypatch, instant: datetime) -> None:
    """Every reading of the wall clock inside cooker answers `instant`."""

    class _Frozen(datetime):
        @classmethod
        def now(cls, tz=None):
            if tz is None:
                return instant.astimezone(timezone.utc).replace(tzinfo=None)
            return instant.astimezone(tz)

    monkeypatch.setattr(_cooker, "datetime", _Frozen)


def _behind(monkeypatch) -> date:
    """Household a day behind the server. Returns the household's today."""
    _set_timezone(TORONTO)
    _freeze(monkeypatch, UTC_EVENING)
    return SERVER_TODAY - timedelta(days=1)


def _ahead(monkeypatch) -> date:
    """Household a day ahead of the server. Returns the household's today."""
    _set_timezone(TOKYO)
    _freeze(monkeypatch, UTC_LATE)
    return SERVER_TODAY + timedelta(days=1)


def _insert_plan(
    start: date,
    status: str,
    day_count: int = 7,
    meal: str = "Chili",
    cook_days: list[date] | None = None,
) -> int:
    """
    A plan filed under its own start. A dinner on each of its days by
    default; `cook_days` narrows that to the days named.
    """
    conn = get_conn()
    cur = conn.execute(
        "INSERT INTO weekly_plans (household_id, week_start_date, status, content_start_date, day_count) "
        "VALUES (1, ?, ?, ?, ?)",
        (start.isoformat(), status, start.isoformat(), day_count),
    )
    plan_id = cur.lastrowid
    wanted = None if cook_days is None else {d.isoformat() for d in cook_days}
    for day in tools.period_dates(start.isoformat(), day_count):
        if wanted is not None and day not in wanted:
            continue
        conn.execute(
            "INSERT INTO meal_plan_entries (household_id, weekly_plan_id, date, slot, freeform_meal) "
            "VALUES (1, ?, ?, 'dinner', ?)",
            (plan_id, day, meal),
        )
    conn.commit()
    conn.close()
    return plan_id


def _as_we_go() -> None:
    """
    The one planning anchor whose answer is a different DATE every day
    rather than a different week once a week: 'as_we_go' starts the period
    at today and runs three days. It is what makes these tests land the
    same way on every weekday, instead of only on the weekdays where the
    server's Monday and the household's Monday happen to differ.
    """
    tools.set_planning_anchor("as_we_go")


# ---------- the clock the tests themselves depend on ----------

class TestTheFreezeReallySplitsTheTwoClocks:
    def test_a_toronto_evening_puts_the_household_a_day_behind_the_server(self, monkeypatch):
        """GUARD on the harness. If the two clocks ever agree here, every
        CATCH below passes without testing anything."""
        household_today = _behind(monkeypatch)
        assert household_today != SERVER_TODAY
        assert _wp._household_today() == household_today

    def test_a_tokyo_morning_puts_the_household_a_day_ahead(self, monkeypatch):
        """GUARD, the other direction."""
        household_today = _ahead(monkeypatch)
        assert household_today != SERVER_TODAY
        assert _wp._household_today() == household_today


# ---------- 1. suggest_planning_period ----------

class TestTheWeekTheAppOffers:
    def test_an_as_we_go_period_starts_on_the_households_today(self, monkeypatch):
        """CATCH. 'as we go' means "three days from today", so the period
        this household is offered names the day it starts out loud. On the
        server's clock a Toronto household at nine at night is offered a
        stretch starting tomorrow."""
        household_today = _behind(monkeypatch)
        _as_we_go()

        assert tools.suggest_planning_period()["start_date"] == household_today.isoformat()

    def test_a_household_a_day_ahead_is_offered_its_own_today(self, monkeypatch):
        """CATCH, the other direction: Tokyo has already reached tomorrow,
        so a stretch starting on the server's date starts in its past."""
        household_today = _ahead(monkeypatch)
        _as_we_go()

        assert tools.suggest_planning_period()["start_date"] == household_today.isoformat()

    def test_a_named_from_date_still_wins(self, monkeypatch):
        """GUARD. `from_date` is how main._first_plan_window and
        chores._chores_week name the day themselves — and it is what keeps
        those two callers at no clock read of their own. The household's
        clock is only the default."""
        _behind(monkeypatch)
        _as_we_go()
        named = SERVER_TODAY + timedelta(days=10)

        assert tools.suggest_planning_period(
            from_date=named.isoformat()
        )["start_date"] == named.isoformat()

    def test_the_monday_anchored_default_reads_the_households_own_week(self, monkeypatch):
        """GUARD. The ordinary household — never asked, so 'sunday', so a
        Monday-start week. Moving the clock must not move where a week
        begins, only which week it is.

        It used to assert the start day is a Monday, and that went red on
        main every Wednesday, Thursday and Friday — including the pinned
        `clock (friday)` job, on every push — from the day "today, never
        yesterday" shipped (Emily, 2026-09-20: "the days are showing from
        yesterday"). Since that change the suggestion begins on the
        household's own today when Plan is opened mid-period, so a bare
        weekday assertion tests the weekday the suite happened to run on
        rather than this class's subject, which is the clock.

        So it asserts the docstring's own claim directly: asking with the
        household's day NAMED gives the same answer as letting the default
        read it. That is "the clock chooses which week, never where one
        begins", and it holds on all seven weekdays. Non-vacuous — point
        the default back at the server's clock and the two answers differ.
        """
        household_today = _behind(monkeypatch)

        period = tools.suggest_planning_period()
        assert period["day_count"] == 7

        named = tools.suggest_planning_period(from_date=household_today.isoformat())
        assert period["start_date"] == named["start_date"]

        # And it is still the household's OWN week that is offered, not a
        # week the server's date wandered into: the start sits between the
        # household's Monday and the Monday after it (today mid-period, or
        # the next Monday once the plan-ahead rule has skipped forward).
        monday = household_today - timedelta(days=household_today.weekday())
        start = date.fromisoformat(period["start_date"])
        assert monday <= start <= monday + timedelta(days=7)

        # `is_monday_anchored` is computed straight off the start day, so
        # it is the same stale claim in another form and goes with it: on
        # the household's own Monday, and once the plan-ahead rule has
        # skipped to the next one, it is True; mid-period the period
        # honestly no longer begins on a Monday and says so.
        assert period["is_monday_anchored"] is (start.weekday() == 0)


# ---------- 2. get_week_planning_nudge, on ONE clock ----------

class TestTheNudgeRunsOnOneClock:
    def test_the_nudge_offers_the_households_own_day(self, monkeypatch):
        """CATCH. Nothing planned at all, so the nudge offers the standing
        suggestion — which for an 'as we go' household is the day it is
        actually on. This is the half that used to come from
        suggest_planning_period on the server's clock while
        retire_expired_drafts, three lines above it, was on the
        household's."""
        household_today = _behind(monkeypatch)
        _as_we_go()

        nudge = tools.get_week_planning_nudge()
        assert nudge["show"] is True
        assert nudge["week_start"] == household_today.isoformat()

    def test_a_household_a_day_ahead_is_nudged_about_its_own_day(self, monkeypatch):
        """CATCH, the other direction."""
        household_today = _ahead(monkeypatch)
        _as_we_go()

        nudge = tools.get_week_planning_nudge()
        assert nudge["show"] is True
        assert nudge["week_start"] == household_today.isoformat()

    def test_a_draft_the_sweep_just_kept_is_not_then_called_an_unplanned_week(self, monkeypatch):
        """CATCH, and the clearest statement of "two clocks in one
        function". The nudge retires expired drafts on the HOUSEHOLD's
        clock and then asked what covers today on the SERVER's — so a
        draft whose last day is the household's today survived the sweep
        at the top of the function and was invisible to the question
        underneath it. The household was told, in the same answer, that
        this week has no plan (`is_current_week` True) about a week the
        same function had just decided was still live."""
        household_today = _behind(monkeypatch)
        _as_we_go()
        draft = _insert_plan(household_today - timedelta(days=6), "draft")

        nudge = tools.get_week_planning_nudge()
        assert tools.get_weekly_plan(draft)["status"] == "draft", "the sweep kept it"
        assert nudge["is_current_week"] is False
        assert nudge["week_start"] == (household_today + timedelta(days=1)).isoformat()

    def test_the_nudge_and_the_standing_suggestion_still_name_the_same_week(self, monkeypatch):
        """GUARD on Emily's 2026-09-11 rule (one source of "which week"),
        which is what this whole function is for. Green either way — before
        the fix both halves read the server's clock, so they agreed about
        the wrong day. Here to say the agreement survived the move."""
        _behind(monkeypatch)

        nudge = tools.get_week_planning_nudge()
        assert nudge["show"] is True
        assert nudge["week_start"] == tools.suggest_planning_period()["start_date"]


# ---------- 3. next_period_after ----------

class TestWhatPlanNextWeekOffers:
    def test_a_plan_ending_tonight_is_still_something_to_plan_after(self, monkeypatch):
        """CATCH. The link under a plan whose last day is the household's
        today. On the server's clock the plan has already ended, so the
        link stops being "the stretch after this one" and falls back to the
        standing suggestion — which says `is_current_period` True, i.e. the
        screen offers to plan THIS week over the week being cooked from."""
        household_today = _behind(monkeypatch)
        _as_we_go()
        _insert_plan(household_today - timedelta(days=6), "approved")

        nxt = tools.next_period_after(tools.get_weekly_plan())
        assert nxt["start_date"] == (household_today + timedelta(days=1)).isoformat()
        assert nxt["is_current_period"] is False

    def test_a_plan_that_really_has_ended_falls_back_to_the_suggestion(self, monkeypatch):
        """CATCH, the other direction. Tokyo has woken up on the day after
        the plan's last: the day after IT is today, so "plan the stretch
        after this one" is the standing suggestion, and the link should say
        so rather than offering a period starting in the household's
        past."""
        household_today = _ahead(monkeypatch)
        _as_we_go()
        _insert_plan(household_today - timedelta(days=7), "approved")

        nxt = tools.next_period_after(tools.get_weekly_plan())
        assert nxt["start_date"] == household_today.isoformat()
        assert nxt["is_current_period"] is True

    def test_a_named_today_wins_over_the_clock(self, monkeypatch):
        """GUARD on the parameter get_week_menu threads in — it is what
        keeps the Plan tab at one clock read rather than three. A day named
        explicitly must be the day used. It goes red against the unmodified
        app/ because the parameter does not exist there, which is an error
        and not a failure, so read it as a mutation check rather than as a
        catch: drop the parameter and read the clock instead and it fails
        with the plan's own following day."""
        household_today = _behind(monkeypatch)
        _as_we_go()
        _insert_plan(household_today - timedelta(days=6), "approved")

        far = (household_today + timedelta(days=30)).isoformat()
        nxt = tools.next_period_after(tools.get_weekly_plan(), today=far)
        # Well past this plan's last day, so it falls back to the
        # suggestion for the named day rather than to the day after it.
        assert nxt["start_date"] == far
        assert nxt["is_current_period"] is True

    def test_a_running_plan_still_offers_the_day_after_it(self, monkeypatch):
        """GUARD. The ordinary case — a plan with days still ahead of it —
        is unchanged, on either clock."""
        household_today = _behind(monkeypatch)
        _insert_plan(household_today - timedelta(days=2), "approved")

        nxt = tools.next_period_after(tools.get_weekly_plan())
        assert nxt["start_date"] == (household_today + timedelta(days=5)).isoformat()
        assert nxt["is_current_period"] is False


# ---------- 4. discard_draft_plan ----------

class TestWhichWeekTheDroppedToastNames:
    def test_it_names_the_week_the_household_is_actually_in(self, monkeypatch):
        """CATCH. A draft straddling two approved weeks — "Pick my own
        days" produces exactly this. The rule (2026-09-14) is that the week
        containing TODAY is the one that is theirs again, and on the
        server's clock a Toronto evening picks the week they have not
        reached yet."""
        household_today = _behind(monkeypatch)
        this_week = household_today - timedelta(days=6)
        next_week = household_today + timedelta(days=1)
        _insert_plan(this_week, "approved")
        _insert_plan(next_week, "approved", meal="Next week")
        draft = _insert_plan(household_today, "draft", day_count=3, meal="Draft")

        dropped = tools.discard_draft_plan(draft)
        assert dropped["approved_week_label"] == tools._format_period_range(
            this_week.isoformat(), 7
        )

    def test_a_household_a_day_ahead_is_named_its_own_week(self, monkeypatch):
        """CATCH, the other direction: Tokyo has already crossed into the
        later of the two weeks, and the toast should name that one."""
        household_today = _ahead(monkeypatch)
        last_week = household_today - timedelta(days=7)
        this_week = household_today
        _insert_plan(last_week, "approved", meal="Last week")
        _insert_plan(this_week, "approved")
        draft = _insert_plan(household_today - timedelta(days=1), "draft", day_count=2, meal="Draft")

        dropped = tools.discard_draft_plan(draft)
        assert dropped["approved_week_label"] == tools._format_period_range(
            this_week.isoformat(), 7
        )

    def test_the_draft_is_still_retired_and_the_approved_week_untouched(self, monkeypatch):
        """GUARD. The clock only decides which week gets NAMED. What the
        function does — retire the draft, write nothing to the approved
        plan underneath — is unchanged."""
        household_today = _behind(monkeypatch)
        approved = _insert_plan(household_today - timedelta(days=6), "approved")
        draft = _insert_plan(household_today, "draft", day_count=3, meal="Draft")

        dropped = tools.discard_draft_plan(draft)
        assert dropped["status"] == "retired"
        assert tools.get_weekly_plan(approved)["status"] == "approved"

    def test_the_clock_is_read_before_the_connection_opens(self, monkeypatch):
        """GUARD against the shape the fix could have taken wrongly, and a
        SOURCE-MARKER one rather than a behaviour one — against the
        unmodified app/ it errors on the missing marker rather than
        failing, so read it as a mutation check and not as a catch.
        _household_today opens its own connection, this function writes,
        and a second connection inside a write transaction is how this
        codebase has twice earned a "database is locked"."""
        src = inspect.getsource(_wp.discard_draft_plan)
        body = src[src.index('"""', src.index('"""') + 3):]
        assert body.index("_household_today()") < body.index("get_conn()"), (
            "the household's clock must be read before the connection opens"
        )


# ---------- 5. week_receipt ----------

class TestTheNoThawLine:
    def _days(self) -> list[dict]:
        return tools.get_week_menu()["days"]

    def test_it_names_tonights_cook_and_not_tomorrows(self, monkeypatch):
        """CATCH. "Nothing to thaw before <the week's next cook>", read off
        the days from today on. On the server's clock a household a day
        behind skipped its own evening's cook and was told about the next
        one — in the evening the receipt is being read."""
        household_today = _behind(monkeypatch)
        plan = _insert_plan(
            household_today - timedelta(days=3), "approved",
            cook_days=[household_today, household_today + timedelta(days=1)],
        )

        receipt = tools.week_receipt(self._days(), plan)
        assert receipt["thaw_count"] == 0
        assert receipt["thaw_line"] == f"Nothing to thaw before {household_today.strftime('%A')}."

    def test_a_household_a_day_ahead_is_past_yesterdays_cook(self, monkeypatch):
        """CATCH, the other direction. The server still thinks today's cook
        is ahead of us; where the household lives it happened last night,
        and the line should name the next one."""
        household_today = _ahead(monkeypatch)
        plan = _insert_plan(
            household_today - timedelta(days=3), "approved",
            cook_days=[SERVER_TODAY, household_today],
        )

        receipt = tools.week_receipt(self._days(), plan)
        assert receipt["thaw_line"] == f"Nothing to thaw before {household_today.strftime('%A')}."

    def test_the_menus_own_receipt_says_the_same_thing(self, monkeypatch):
        """CATCH. The threaded half: get_week_menu resolves the clock once
        and hands it down, so the receipt on the payload and a direct call
        to week_receipt cannot name two different days."""
        household_today = _behind(monkeypatch)
        _insert_plan(
            household_today - timedelta(days=3), "approved",
            cook_days=[household_today, household_today + timedelta(days=1)],
        )

        menu = tools.get_week_menu()
        assert menu["receipt"]["thaw_line"] == (
            f"Nothing to thaw before {household_today.strftime('%A')}."
        )

    def test_a_named_today_wins_over_the_clock(self, monkeypatch):
        """GUARD on the parameter get_week_menu threads in. Red against the
        unmodified app/ only because the parameter is not there — an error,
        not a failure — so it is a mutation check rather than a catch."""
        household_today = _behind(monkeypatch)
        later = household_today + timedelta(days=1)
        plan = _insert_plan(
            household_today - timedelta(days=3), "approved",
            cook_days=[household_today, later],
        )

        receipt = tools.week_receipt(self._days(), plan, today=later.isoformat())
        assert receipt["thaw_line"] == f"Nothing to thaw before {later.strftime('%A')}."

    def test_a_week_with_no_cook_left_says_so_without_naming_a_day(self, monkeypatch):
        """GUARD. Nothing ahead to name, so the sentence drops the clause
        rather than inventing a weekday — unchanged by the clock."""
        household_today = _behind(monkeypatch)
        plan = _insert_plan(
            household_today - timedelta(days=3), "approved",
            cook_days=[household_today - timedelta(days=3)],
        )

        assert tools.week_receipt(self._days(), plan)["thaw_line"] == "Nothing to thaw this week."


# ---------- 6. get_week_menu's component_based branch ----------

class TestTheComponentBranchesPickRows:
    def _a_recipe(self) -> None:
        tools.add_recipe("Quick Chili", ingredients=[{"item": "Beans", "qty": "1 can"}],
                         prep_time_minutes=5, cook_time_minutes=10)

    def _suggested_on(self, menu: dict, day: date) -> bool:
        for d in menu["days"]:
            if d["date"] == day.isoformat():
                return bool(d.get("dinner_suggestions"))
        raise AssertionError(f"{day} is not in this menu")

    def test_tonights_empty_dinner_still_offers_a_pick(self, monkeypatch):
        """CATCH. The component branch's twin of the day-based fix
        (2026-09-15): on the server's date a household a day behind lost
        the two-quick-dinner rows on tonight's empty dinner, in the very
        evening they would reach for them."""
        household_today = _behind(monkeypatch)
        self._a_recipe()
        tools.set_planning_mode("component_based")
        tools.create_weekly_plan((household_today - timedelta(days=3)).isoformat())
        tools.approve_weekly_plan(tools.get_weekly_plan()["weekly_plan_id"])

        menu = tools.get_week_menu()
        assert menu["menu_is_suggested"] is True
        assert self._suggested_on(menu, household_today)

    def test_a_day_the_household_has_already_left_offers_nothing(self, monkeypatch):
        """CATCH, the other direction. Tokyo is on tomorrow, so the
        server's today is a day gone by — "not planned", nothing to suggest
        into."""
        household_today = _ahead(monkeypatch)
        self._a_recipe()
        tools.set_planning_mode("component_based")
        tools.create_weekly_plan((household_today - timedelta(days=3)).isoformat())
        tools.approve_weekly_plan(tools.get_weekly_plan()["weekly_plan_id"])

        menu = tools.get_week_menu()
        assert not self._suggested_on(menu, SERVER_TODAY)
        assert self._suggested_on(menu, household_today)

    def test_a_past_day_is_never_offered_a_pick(self, monkeypatch):
        """GUARD. The rule the gate exists for, unchanged: a past day's
        empty dinner is just "not planned"."""
        household_today = _behind(monkeypatch)
        self._a_recipe()
        tools.set_planning_mode("component_based")
        tools.create_weekly_plan((household_today - timedelta(days=3)).isoformat())
        tools.approve_weekly_plan(tools.get_weekly_plan()["weekly_plan_id"])

        menu = tools.get_week_menu()
        assert not self._suggested_on(menu, household_today - timedelta(days=3))


# ---------- the cost of all this ----------

def _clock_reads(monkeypatch, fn) -> int:
    calls = []
    real = _cooker.household_now

    def _counted(*a, **kw):
        calls.append(1)
        return real(*a, **kw)

    monkeypatch.setattr(_cooker, "household_now", _counted)
    try:
        fn()
    finally:
        monkeypatch.setattr(_cooker, "household_now", real)
    return len(calls)


class TestTheClockIsStillReadAFixedNumberOfTimes:
    def test_the_plan_tabs_payload_reads_it_three_times_however_long_the_week(self, monkeypatch):
        """
        GUARD on the cost, the sibling of
        test_moves_household_clock.py's own — and the one guard here that
        is red against the unmodified app/ for a reason that is not a bug
        there: the ceiling moved DOWN, from four to three. That is the
        honest way to record a number that improved, the same way the
        moves.py guard recorded one that grew.

        household_now opens a
        connection and runs a SELECT, so a payload resolves the day ONCE at
        its entry point and threads it down rather than each part asking
        again. THREE for a whole Plan tab payload: get_week_menu's own
        (which it hands to retire_expired_drafts, next_period_after,
        week_receipt and both branches' "is this day still ahead of us"),
        _current_weekly_plan_row underneath get_weekly_plan, and
        _pending_draft_over.

        The number went DOWN, from four: before this branch the sweep and
        the Pick-row gate each resolved their own, and the four things
        added here would have taken it to seven. The two that are still
        separate are reached through other callers as well and are named
        here rather than threaded — if a fourth appears, name it or thread
        it.

        Both ends are asserted. The upper one is the ceiling; the lower one
        is what stops `<= 3` being green at zero, which is what a version
        of this file that never read the household's clock would give.
        """
        household_today = _behind(monkeypatch)
        _insert_plan(household_today - timedelta(days=3), "approved")
        short = _clock_reads(monkeypatch, tools.get_week_menu)

        conn = get_conn()
        conn.execute("UPDATE weekly_plans SET day_count = 21")
        conn.commit()
        conn.close()
        long = _clock_reads(monkeypatch, tools.get_week_menu)

        assert len(tools.get_week_menu()["days"]) == 21, "the week really did get longer"
        assert short == long
        assert 1 <= short <= 3

    def test_the_nudge_still_reads_it_once(self, monkeypatch):
        """
        GUARD. It read one before this branch and one after — but a
        different one: the household's, resolved at the top and handed to
        both retire_expired_drafts and suggest_planning_period, in place of
        retire_expired_drafts resolving its own while the other two halves
        of the answer used the server's.
        """
        _behind(monkeypatch)
        assert _clock_reads(monkeypatch, tools.get_week_planning_nudge) == 1

    def test_the_component_payload_reads_it_once_too(self, monkeypatch):
        """
        GUARD, and the pin on the component branch's own
        `week_receipt(..., today=...)` thread. That thread cannot be pinned
        by VALUE — see the class below for why — so what says it is still
        there is the count: drop it and this payload asks a fourth time.
        """
        household_today = _behind(monkeypatch)
        tools.add_recipe("Quick Chili", ingredients=[{"item": "Beans", "qty": "1 can"}],
                         prep_time_minutes=5, cook_time_minutes=10)
        tools.set_planning_mode("component_based")
        tools.create_weekly_plan((household_today - timedelta(days=3)).isoformat())
        tools.approve_weekly_plan(tools.get_weekly_plan()["weekly_plan_id"])

        assert _clock_reads(monkeypatch, tools.get_week_menu) == 3

    def test_a_payload_with_no_plan_at_all_reads_it_twice(self, monkeypatch):
        """
        GUARD, and the pin on the no-plan branch's
        `suggest_planning_period(from_date=...)` thread — the Plan tab's
        empty state, and the one shape the guard above never reaches,
        because it seeds a plan. Two: this function's own, and
        _current_weekly_plan_row's underneath get_weekly_plan. Drop the
        thread and it is three.
        """
        _behind(monkeypatch)
        assert tools.get_week_menu()["weekly_plan_id"] is None, "really no plan"
        assert _clock_reads(monkeypatch, tools.get_week_menu) == 2

    def test_a_read_pinned_to_one_plan_costs_a_single_read(self, monkeypatch):
        """
        GUARD. `get_week_menu(id)` is the public share page's shape, and it
        resolves neither _current_weekly_plan_row nor _pending_draft_over —
        it was told which plan. One read, which is the honest cost of the
        branch's "is this day still ahead of us" and of the receipt.

        This seeds a DAY-BASED plan, where it was already one before this
        branch (that branch's Pick-row gate, 2026-09-15) — so read it as a
        pin on the receipt's thread, which it catches, and not as evidence
        of a cost change. The shape that really goes 0 -> 1 is the same
        call on a COMPONENT plan, whose Pick rows and receipt were both on
        the server's date; measured 0 on the merge base and 1 here.
        """
        household_today = _behind(monkeypatch)
        plan = _insert_plan(household_today - timedelta(days=3), "approved")

        assert _clock_reads(monkeypatch, lambda: tools.get_week_menu(plan)) == 1


# ---------- one payload, one day ----------
#
# The cost guards above say the clock is READ once. They do not say the
# whole payload is ABOUT one day, and the difference matters: every callee
# threaded here defaults to `_household_today()` itself, so dropping a
# thread changes no value while the clock is still — which is exactly why
# three of the four threads pass their own value tests unmutated, and why
# the counts above are what actually pin them.
#
# What the threading buys beyond cost is that a payload straddling midnight
# cannot answer about two days. That is not reachable with a single frozen
# instant, so it is reached the only honest way: a clock that answers a
# different day each time it is asked. Artificial as a wall clock, exact as
# a statement of the property — "ask once, use that answer everywhere".

class TestOnePayloadAnswersAboutOneDay:
    def test_the_plan_tab_uses_the_day_it_first_asked_for(self, monkeypatch):
        """
        CATCH against the mutation, not against main (main reads the
        server's clock everywhere, so it is trivially self-consistent and
        this passes there for the wrong reason — a GUARD there, a mutation
        check here). Drop either `today=` on the day-based branch and the
        two halves of one Plan tab payload describe two different days:
        "Plan next week ›" stops offering the stretch after this plan and
        falls back to the standing suggestion, and the receipt loses the
        cook it was meant to name.
        """
        _as_we_go()
        first = SERVER_TODAY
        plan = _insert_plan(first - timedelta(days=6), "approved")

        rolling = iter(first + timedelta(days=n) for n in range(100))
        monkeypatch.setattr(_wp, "_household_today", lambda: next(rolling))

        menu = tools.get_week_menu()
        assert menu["weekly_plan_id"] == plan
        # The stretch after this plan, sized by the rhythm — computed from
        # the day the payload started on, not from whatever the clock said
        # by the time this line was reached.
        assert menu["next_period"]["start_date"] == (first + timedelta(days=1)).isoformat()
        assert menu["next_period"]["is_current_period"] is False
        # And the receipt names this plan's next cook from that same day.
        assert menu["receipt"]["thaw_line"] == f"Nothing to thaw before {first.strftime('%A')}."

    def test_the_empty_state_names_the_day_it_first_asked_for(self, monkeypatch):
        """
        CATCH against the mutation, the same shape on the no-plan branch:
        the week the Plan tab offers a household that has never planned.
        """
        _as_we_go()
        first = SERVER_TODAY
        rolling = iter(first + timedelta(days=n) for n in range(100))
        monkeypatch.setattr(_wp, "_household_today", lambda: next(rolling))

        menu = tools.get_week_menu()
        assert menu["weekly_plan_id"] is None
        assert menu["suggested_period"]["start_date"] == first.isoformat()
