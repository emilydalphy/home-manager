"""
weekly_plan.py's last two server-clock reads, moved onto the household's.

Three fixes on 2026-09-14 put the SCREENS on the household's clock —
moves.py's timeline, this module's needs-you card and loose-meal window,
and cooker.py's stale-plan guard. Each was scoped to one module so the
reviews stayed tractable, and the cooker.py sweep reported these two next
door rather than sweeping them in:

1. `retire_expired_drafts` — the lazy sweep that marks a draft whose
   period has ended 'retired'. This is the one with teeth: it is a WRITE,
   it is what stops a plan being the Plan tab's front page, and it happens
   silently inside an ordinary read. On the server's clock, a container in
   UTC and a household in America/Toronto part company from 8pm local — so
   a draft could be retired at 9pm on the evening of its own last day, out
   from under a household still cooking from it.

2. `_current_weekly_plan_row` — a plan failing "covers today" by one
   evening. Mostly masked, because the fallback underneath usually hands
   back the same plan anyway. With a FUTURE draft on file it does not:
   the fallback prefers the newest non-retired plan, which is that draft,
   so the household is shown next week's draft during this week's last
   evening.

How the clock is frozen: `_household_today()` is a SHIFT —
`date.today() + (cooker.household_now().date() - datetime.now().date())` —
so replacing `cooker.datetime` with a subclass whose `now()` answers one
fixed UTC instant moves the household's day without touching the server's.
The zone lookup, the households.timezone read and the ZoneInfo fallback
all run for real; only the wall clock is faked. Both directions are
pinned: a household BEHIND the server must keep a draft the server thinks
has expired, and a household AHEAD of it must lose one the server thinks
is still running.

Each test says in its own docstring whether it is a CATCH (red without the
fix) or a NO-REGRESSION GUARD (green either way, here to say what did not
change).
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone

import pytest

from app import tools
from app.db import get_conn
from app.tools import cooker as _cooker
from app.tools import weekly_plan as _wp


SERVER_TODAY = date.today()

# 01:30 UTC: Toronto is 21:30 the evening BEFORE, so the household is a
# whole day BEHIND the server. That is the reported bug, and every evening
# between 8pm and midnight Eastern.
UTC_EVENING = datetime.combine(SERVER_TODAY, time(1, 30), tzinfo=timezone.utc)
# 23:30 UTC: Tokyo is 08:30 the morning AFTER — the household a day AHEAD,
# the same split read from the other side.
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


def _insert_plan(start: date, status: str, day_count: int = 7, meal: str = "Chili") -> int:
    """A plan with one dinner on each of its days, filed under its start."""
    conn = get_conn()
    cur = conn.execute(
        "INSERT INTO weekly_plans (household_id, week_start_date, status, content_start_date, day_count) "
        "VALUES (1, ?, ?, ?, ?)",
        (start.isoformat(), status, start.isoformat(), day_count),
    )
    plan_id = cur.lastrowid
    for day in tools.period_dates(start.isoformat(), day_count):
        conn.execute(
            "INSERT INTO meal_plan_entries (household_id, weekly_plan_id, date, slot, freeform_meal) "
            "VALUES (1, ?, ?, 'dinner', ?)",
            (plan_id, day, meal),
        )
    conn.commit()
    conn.close()
    return plan_id


def _status(plan_id: int) -> str:
    conn = get_conn()
    row = conn.execute("SELECT status FROM weekly_plans WHERE id = ?", (plan_id,)).fetchone()
    conn.close()
    return row["status"]


def _current_id() -> int | None:
    conn = get_conn()
    row = _wp._current_weekly_plan_row(conn)
    conn.close()
    return row["id"] if row else None


# ---------- the clock the tests themselves depend on ----------

class TestTheFreezeReallySplitsTheTwoClocks:
    def test_a_toronto_evening_puts_the_household_a_day_behind_the_server(self, monkeypatch):
        """GUARD on the harness, not on app behaviour. If this ever goes
        green-by-accident — the two clocks agreeing — every CATCH below
        would pass without testing anything."""
        household_today = _behind(monkeypatch)
        assert household_today != SERVER_TODAY
        assert _wp._household_today() == household_today

    def test_a_tokyo_morning_puts_the_household_a_day_ahead(self, monkeypatch):
        """GUARD, the other direction."""
        household_today = _ahead(monkeypatch)
        assert household_today != SERVER_TODAY
        assert _wp._household_today() == household_today


# ---------- 1. retire_expired_drafts ----------

class TestADraftSurvivesItsOwnLastEvening:
    def test_a_draft_ending_on_the_households_today_is_not_retired(self, monkeypatch):
        """CATCH. The reported bug. Nine at night in Toronto, the server
        already on tomorrow: a draft whose last day is TODAY where the
        household lives must still be the front page."""
        household_today = _behind(monkeypatch)
        # A seven-day draft whose last day is the household's today.
        draft = _insert_plan(household_today - timedelta(days=6), "draft")

        assert tools.retire_expired_drafts() == []
        assert _status(draft) == "draft"

    def test_a_one_day_draft_for_the_households_today_is_not_retired(self, monkeypatch):
        """CATCH. The narrowest version: a single-day plan for tonight.
        On the server's clock its one day was already behind us."""
        household_today = _behind(monkeypatch)
        draft = _insert_plan(household_today, "draft", day_count=1)

        assert tools.retire_expired_drafts() == []
        assert _status(draft) == "draft"

    def test_a_draft_whose_last_day_has_really_passed_is_still_retired(self, monkeypatch):
        """GUARD. The sweep's whole job, unchanged: yesterday-where-the-
        household-lives is genuinely over."""
        household_today = _behind(monkeypatch)
        draft = _insert_plan(household_today - timedelta(days=7), "draft")

        assert tools.retire_expired_drafts() == [draft]
        assert _status(draft) == "retired"

    def test_a_household_a_day_AHEAD_loses_a_draft_the_server_thinks_is_live(self, monkeypatch):
        """CATCH, from the other side. Tokyo has woken up on the day after
        the draft's last; the server has not. The draft is over where the
        household lives, so it goes."""
        household_today = _ahead(monkeypatch)
        draft = _insert_plan(household_today - timedelta(days=7), "draft")
        # Its last day is SERVER_TODAY, so the server's clock would keep it.
        assert (household_today - timedelta(days=1)) == SERVER_TODAY

        assert tools.retire_expired_drafts() == [draft]
        assert _status(draft) == "retired"

    def test_an_approved_week_is_never_retired_by_the_sweep(self, monkeypatch):
        """GUARD. Only drafts. An approved plan whose week has passed was
        the household's real week and is left alone."""
        household_today = _behind(monkeypatch)
        approved = _insert_plan(household_today - timedelta(days=30), "approved")

        assert tools.retire_expired_drafts() == []
        assert _status(approved) == "approved"

    def test_an_explicit_today_argument_still_wins(self, monkeypatch):
        """GUARD. The parameter is how callers and tests name the day
        themselves; the household's clock is only the default."""
        household_today = _behind(monkeypatch)
        draft = _insert_plan(household_today - timedelta(days=6), "draft")

        # Named a day well past the draft's last: it goes, clock or no clock.
        assert tools.retire_expired_drafts(
            (household_today + timedelta(days=5)).isoformat()
        ) == [draft]

    def test_the_clock_is_read_before_the_sweeps_own_write_transaction(self, monkeypatch):
        """GUARD against the shape the fix could have taken wrongly, and
        a source-marker one rather than a behaviour one — on main it
        errors because the helper is not called there at all, so read it
        as a mutation check, not as a catch. _household_today opens its
        own connection; resolving it after get_conn (and after the
        UPDATE) would put a second connection inside this function's own
        write transaction, which is how this codebase has twice earned a
        "database is locked". Pinned by where the clock is read relative
        to the connection."""
        import inspect

        src = inspect.getsource(_wp.retire_expired_drafts)
        clock_at = src.index("_household_today()")
        conn_at = src.index("get_conn()")
        assert clock_at < conn_at, "the household's clock must be read before the connection opens"


# ---------- 2. _current_weekly_plan_row ----------

class TestTheCurrentPlanOnTheLastEvening:
    def test_a_future_draft_does_not_take_over_on_this_weeks_last_evening(self, monkeypatch):
        """CATCH. The case the fallback does not mask. This week's
        approved plan ends on the household's today; next week's draft is
        already on file. On the server's clock nothing "covers today", the
        fallback prefers the newest plan — the draft — and the household is
        shown next week during this week's last evening."""
        household_today = _behind(monkeypatch)
        this_week = _insert_plan(household_today - timedelta(days=6), "approved")
        _insert_plan(household_today + timedelta(days=1), "draft", meal="Next week")

        assert _current_id() == this_week

    def test_a_plan_covering_the_households_today_is_the_current_one(self, monkeypatch):
        """GUARD, and it is worth saying why it is not a catch: it is
        GREEN on main. With only these two plans on file the fallback
        happens to hand back the same answer the covers-today branch
        does, because this week's plan is also the newest. That is the
        masking the card describes — which is why the future-draft test
        above, where the newest plan is NOT the right one, is the real
        catch. This one pins that the ordinary case did not change."""
        household_today = _behind(monkeypatch)
        older = _insert_plan(household_today - timedelta(days=40), "approved", meal="Old")
        this_week = _insert_plan(household_today - timedelta(days=6), "approved")
        assert older != this_week

        assert _current_id() == this_week

    def test_a_household_a_day_AHEAD_moves_on_to_the_week_that_has_started(self, monkeypatch):
        """CATCH, the other direction. Tokyo is already on the new week's
        first day; the server still thinks the old week is running."""
        household_today = _ahead(monkeypatch)
        _insert_plan(household_today - timedelta(days=7), "approved", meal="Last week")
        new_week = _insert_plan(household_today, "approved", meal="This week")

        assert _current_id() == new_week

    def test_with_nothing_covering_today_the_newest_plan_is_still_the_fallback(self, monkeypatch):
        """GUARD. The fallback itself is unchanged: an approved week whose
        period has passed is still handed back when nothing covers today —
        it was the household's real week and the only thing left to show."""
        household_today = _behind(monkeypatch)
        old = _insert_plan(household_today - timedelta(days=40), "approved")

        assert _current_id() == old

    def test_an_expired_draft_is_still_refused_by_the_fallback(self, monkeypatch):
        """GUARD. Emily's 2026-09-11 rule, unchanged: the fallback refuses
        a draft whose last day has passed, so the chat tools resolving
        through here between sweeps agree with the screens."""
        household_today = _behind(monkeypatch)
        _insert_plan(household_today - timedelta(days=40), "draft")

        assert _current_id() is None


# ---------- 3. the third reader of the same predicate ----------

class TestTheDraftIsAlsoTheFrontPageThatEvening:
    """
    Found on review of this branch's first commit. `_SQL_EXPIRED_BEFORE`
    has THREE readers and its own comment named two; moving only those
    two left `_pending_draft_over` binding it with the server's date. So
    the draft survived retirement (the fix worked) and was STILL not the
    Plan tab's front page for the same four hours — retiring is not the
    only thing that stops a plan leading the tab.

    The lone-draft shape was fully fixed by the first commit; this is the
    draft-over-an-approved-week shape, which the 2026-09-13 "a draft
    waits for approval" work made ordinary and which "Pick my own days"
    produces.
    """

    def test_a_draft_over_an_approved_week_still_leads_the_tab_on_its_last_evening(self, monkeypatch):
        """CATCH. Red on this branch's own first commit, not just on main."""
        household_today = _behind(monkeypatch)
        approved = _insert_plan(household_today - timedelta(days=6), "approved", meal="Approved")
        draft = _insert_plan(household_today - timedelta(days=3), "draft", day_count=4, meal="Draft")

        # The fix from commit one: the draft is not retired.
        assert tools.retire_expired_drafts() == []
        assert _status(draft) == "draft"

        # And the fix from this one: the tab leads with it.
        approved_row = _wp.get_weekly_plan(approved)
        assert _wp._pending_draft_over(approved_row) == draft
        assert _wp.get_week_menu()["weekly_plan_id"] == draft

    def test_a_draft_whose_week_really_has_passed_still_does_not_lead(self, monkeypatch):
        """GUARD. The predicate's whole job, unchanged."""
        household_today = _behind(monkeypatch)
        approved = _insert_plan(household_today - timedelta(days=6), "approved", meal="Approved")
        _insert_plan(household_today - timedelta(days=20), "draft", meal="Old draft")

        approved_row = _wp.get_weekly_plan(approved)
        assert _wp._pending_draft_over(approved_row) is None

    def test_a_household_a_day_AHEAD_stops_leading_with_a_draft_that_is_over(self, monkeypatch):
        """CATCH, the other direction."""
        household_today = _ahead(monkeypatch)
        approved = _insert_plan(household_today - timedelta(days=3), "approved", meal="Approved")
        _insert_plan(household_today - timedelta(days=7), "draft", day_count=7, meal="Draft")

        approved_row = _wp.get_weekly_plan(approved)
        assert _wp._pending_draft_over(approved_row) is None


# ---------- 4. get_week_menu answering about ONE day ----------

def test_the_pick_rows_are_offered_on_the_households_own_today(monkeypatch):
    """
    CATCH (red on main and on this branch's first commit). Pre-existing,
    and fixed here because this branch moved the other half of
    get_week_menu's clock — a function answering about two different days
    is a new bug, not a smaller one. On the server's date a household a
    day behind lost the "Pick" rows on tonight's empty dinner, in the
    evening they would reach for them.
    """
    household_today = _behind(monkeypatch)
    plan = _insert_plan(household_today - timedelta(days=3), "approved", day_count=7)
    # _suggest_quick_dinners has nothing to offer from an empty library,
    # so seed two fast recipes — otherwise this passes vacuously on an
    # empty list whether the date gate fired or not.
    for name in ("Quick Omelette", "Fast Noodles"):
        tools.add_recipe(
            name, ingredients=[{"item": "Egg", "qty": "2", "category": "dairy"}],
            prep_time_minutes=5, cook_time_minutes=10, default_servings=4,
        )

    # Empty tonight's dinner, which on the server's clock is "yesterday".
    conn = get_conn()
    conn.execute(
        "DELETE FROM meal_plan_entries WHERE weekly_plan_id = ? AND date = ? AND slot = 'dinner'",
        (plan, household_today.isoformat()),
    )
    conn.commit()
    conn.close()

    menu = _wp.get_week_menu(plan)
    tonight = [d for d in menu["days"] if d["date"] == household_today.isoformat()][0]
    assert tonight["dinner"] is None, "the premise: tonight's dinner really is empty"
    assert tonight.get("dinner_suggestions"), \
        "the household's today must still be offered something to tap"
    assert {r["meal"] for r in tonight["dinner_suggestions"]} <= {"Quick Omelette", "Fast Noodles"}


def test_a_day_genuinely_in_the_past_is_still_offered_nothing(monkeypatch):
    """GUARD. The rule itself is unchanged: a past day's empty dinner is
    just 'not planned', with nothing to suggest into it."""
    household_today = _behind(monkeypatch)
    plan = _insert_plan(household_today - timedelta(days=3), "approved", day_count=7)
    yesterday = (household_today - timedelta(days=1)).isoformat()

    conn = get_conn()
    conn.execute(
        "DELETE FROM meal_plan_entries WHERE weekly_plan_id = ? AND date = ? AND slot = 'dinner'",
        (plan, yesterday),
    )
    conn.commit()
    conn.close()

    menu = _wp.get_week_menu(plan)
    day = [d for d in menu["days"] if d["date"] == yesterday][0]
    assert day["dinner"] is None
    assert not day.get("dinner_suggestions")
