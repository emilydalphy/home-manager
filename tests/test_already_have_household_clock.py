"""
"Already have" decisions are scoped by the HOUSEHOLD's day, on both sides.

get_already_have_decisions answers the Review screen's confirmation
section: every line the household took off because they said they already
had it, since the current planning period began. Two things decide that
window, and until 2026-09-18 neither was the household's own clock.

1. The DAY. `date.today()` is the container's, and the container runs UTC
   while households.timezone defaults to America/Toronto — so the check
   "does the current plan still cover today?" could fail by one evening
   and drop the window back to a Monday cutoff, which is the very
   plan-shaped assumption that function's docstring was written to be rid
   of. Worse, _current_weekly_plan_row had already PICKED that plan on the
   household's clock, so one function was asking two clocks about one day.

2. The BOUNDARY. removed_at is a UTC instant (SQLite datetime('now')) and
   the cutoff was a calendar date, so the string comparison began the
   window at midnight UTC rather than midnight where the household lives.
   West of UTC that is merely generous — Toronto's window opened four
   hours early. EAST of UTC it is a loss: the window opens LATE, and every
   decision made in the first hours of the period's own first day is
   missing from the screen that exists to let the household undo it.

Both are fixed by reading the household's day and then turning that day
into the instant it began at, so both sides of the comparison are UTC
instants.

How the clock is frozen: `_freeze` replaces cooker.datetime with a
subclass whose now() answers one fixed UTC instant, so the household's day
moves without the server's. The households.timezone read, the ZoneInfo
lookup and the fallback all run for real. Both directions are pinned —
Toronto at 21:30 (the household a day BEHIND, which is production every
evening) and Tokyo at 08:30 (a day AHEAD, the same split from the other
side). `removed_at` is written explicitly rather than left to the wall
clock, because these tests are about which instants fall inside the
window, not about when the test happened to run.

Each test says in its own docstring whether it is a CATCH (red against
main's app/) or a GUARD (green either way, here to say what did not
change), and every GUARD names the mutation that pins it.
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from app import tools
from app.db import get_conn
from app.tools import cooker as _cooker
from app.tools import pre_shop as _pre_shop
from app.tools import weekly_plan as _wp


SERVER_TODAY = date.today()

# 01:30 UTC: Toronto is 21:30 the evening BEFORE, so the household is a
# whole day BEHIND the server — production, every evening after eight.
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


def _at(zone_name: str, day: date, hour: int, minute: int = 0) -> datetime:
    """That wall-clock moment where the household lives, as an instant."""
    return datetime.combine(day, time(hour, minute), tzinfo=ZoneInfo(zone_name))


def _decision_at(item: str, when: datetime) -> int:
    """
    A line the household took off because they said they already had it,
    decided at `when`. The removal goes through the real tool so the row
    is shaped exactly as one of Emily's is; only the stamp is then set,
    because the wall clock at test time is not what these tests are about.
    """
    tools.add_grocery_item(item, quantity="1", category="pantry")
    item_id = next(i["id"] for i in tools.list_grocery_list() if i["item"] == item)
    tools.drop_grocery_item_pre_shop(item_id, author="user")
    conn = get_conn()
    conn.execute(
        "UPDATE grocery_items SET removed_at = ? WHERE id = ?",
        (when.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"), item_id),
    )
    conn.commit()
    conn.close()
    return item_id


def _plan(start: date, day_count: int = 7) -> int:
    """An approved plan over `day_count` days from `start`, one dinner a day."""
    conn = get_conn()
    cur = conn.execute(
        "INSERT INTO weekly_plans (household_id, week_start_date, status, content_start_date, day_count) "
        "VALUES (1, ?, 'approved', ?, ?)",
        (start.isoformat(), start.isoformat(), day_count),
    )
    plan_id = cur.lastrowid
    for day in tools.period_dates(start.isoformat(), day_count):
        conn.execute(
            "INSERT INTO meal_plan_entries (household_id, weekly_plan_id, date, slot, freeform_meal) "
            "VALUES (1, ?, ?, 'dinner', 'Chili')",
            (plan_id, day),
        )
    conn.commit()
    conn.close()
    return plan_id


def _listed() -> list[str]:
    return sorted(d["item"] for d in tools.get_already_have_decisions())


# ---------- the clock the tests themselves depend on ----------

class TestTheFreezeReallySplitsTheTwoClocks:
    def test_a_toronto_evening_puts_the_household_a_day_behind(self, monkeypatch):
        """GUARD on the harness, not on app behaviour. If the two clocks
        ever agree here, every CATCH below passes without testing
        anything."""
        household_today = _behind(monkeypatch)
        assert household_today != SERVER_TODAY
        assert _cooker.household_today() == household_today

    def test_a_tokyo_morning_puts_the_household_a_day_ahead(self, monkeypatch):
        """GUARD, the other direction."""
        household_today = _ahead(monkeypatch)
        assert household_today != SERVER_TODAY
        assert _cooker.household_today() == household_today


# ---------- 1. the window opens where the household's day opens ----------

class TestTheWindowOpensAtTheHouseholdsMidnight:
    def test_the_periods_first_morning_is_listed_east_of_utc(self, monkeypatch):
        """CATCH, and the one with real teeth. Tokyo is nine hours ahead,
        so eight in the morning on the period's first day is still
        yesterday in UTC — and a cutoff read as midnight UTC threw the
        whole of that morning away. The household said 'we already have
        rice' before work and the screen that exists to let them take it
        back never listed it."""
        household_today = _ahead(monkeypatch)
        period_start = household_today - timedelta(days=3)
        _plan(period_start)

        _decision_at("rice", _at(TOKYO, period_start, 8))

        assert _listed() == ["rice"]

    def test_the_periods_first_morning_is_listed_west_of_utc_too(self, monkeypatch):
        """GUARD. The ordinary Toronto case, which was always right and
        must stay right. Pinned by mutation: have _household_day_start_utc
        return the start of the day AFTER and this goes red."""
        household_today = _behind(monkeypatch)
        period_start = household_today - timedelta(days=3)
        _plan(period_start)

        _decision_at("rice", _at(TORONTO, period_start, 9))

        assert _listed() == ["rice"]

    def test_the_evening_before_the_period_is_no_longer_listed(self, monkeypatch):
        """CATCH, and the one behaviour change every existing household
        sees. Nine at night the evening BEFORE a Toronto period starts is
        one in the morning UTC, so the old window swept it in. It is a
        decision made before the period began, and the window is the
        period."""
        household_today = _behind(monkeypatch)
        period_start = household_today - timedelta(days=3)
        _plan(period_start)

        _decision_at("rice", _at(TORONTO, period_start - timedelta(days=1), 21))

        assert _listed() == []

    def test_a_decision_from_long_before_the_period_is_still_left_out(self, monkeypatch):
        """GUARD. The window still has a floor — that is the whole reason
        it exists, so old decisions don't linger on the Review screen for
        ever. Pinned by mutation: drop the removed_at cutoff from the
        query and this goes red."""
        household_today = _behind(monkeypatch)
        period_start = household_today - timedelta(days=3)
        _plan(period_start)

        _decision_at("rice", _at(TORONTO, period_start - timedelta(days=14), 9))

        assert _listed() == []


# ---------- 2. which day the window is measured from ----------

class TestTheDayIsTheHouseholdsOwn:
    def test_a_period_ending_on_the_households_today_still_scopes_the_window(
        self, monkeypatch
    ):
        """CATCH. Nine at night on the last day of the household's own
        period: the server is already on tomorrow, so 'does this plan
        cover today?' answered no and the window fell back to a Monday
        cutoff — throwing away decisions made earlier in the very period
        the Review screen is confirming. The Monday fallback is always
        later than this period's start, whatever weekday the suite runs
        on, so the catch does not depend on one."""
        household_today = _behind(monkeypatch)
        period_start = household_today - timedelta(days=6)
        plan_id = _plan(period_start)

        _decision_at("rice", _at(TORONTO, period_start, 9))

        # The plan the app calls current and the plan this window follows
        # have to be the same plan; asking two clocks about one day is
        # exactly how they came apart.
        conn = get_conn()
        assert _wp._current_weekly_plan_row(conn)["id"] == plan_id
        conn.close()
        assert _listed() == ["rice"]

    def test_with_no_plan_the_monday_fallback_is_the_households_monday(
        self, monkeypatch
    ):
        """CATCH. Every household without a plan falls back to 'since this
        Monday', and that Monday is a household day like any other. Two
        decisions either side of the household's own midnight: exactly the
        later one belongs to the week."""
        household_today = _ahead(monkeypatch)
        monday = household_today - timedelta(days=household_today.weekday())

        _decision_at("rice", _at(TOKYO, monday, 8))
        _decision_at("flour", _at(TOKYO, monday - timedelta(days=1), 23))

        assert _listed() == ["rice"]


# ---------- 3. the premise, and the mechanics ----------

class TestThePremiseThisRestsOn:
    def test_removed_at_is_stamped_in_utc(self):
        """GUARD, on the fact the whole fix is built on: removed_at is an
        instant in UTC, from all six writers of it across this module,
        grocery.py and staples.py. If it were ever local, converting the
        cutoff into UTC would be the wrong move rather than the right one.
        Pinned by mutation: make drop_grocery_item_pre_shop stamp
        datetime('now', 'localtime') and this goes red in any process zone
        that is not UTC."""
        tools.add_grocery_item("rice", quantity="1", category="pantry")
        item_id = next(i["id"] for i in tools.list_grocery_list() if i["item"] == "rice")
        tools.drop_grocery_item_pre_shop(item_id, author="user")

        conn = get_conn()
        stamp = conn.execute(
            "SELECT removed_at FROM grocery_items WHERE id = ?", (item_id,)
        ).fetchone()["removed_at"]
        conn.close()

        written = datetime.strptime(stamp, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
        assert abs((written - datetime.now(timezone.utc)).total_seconds()) < 120

    def test_the_helper_turns_a_household_day_into_the_instant_it_began(self):
        """CATCH by name only — _household_day_start_utc does not exist on
        main, so this errors there rather than failing on its assertion,
        and it is the arithmetic it is really pinning. A fixed date, so the
        offsets are the same whenever the suite runs: Toronto is four hours
        behind UTC in September, Tokyo nine ahead."""
        day = date(2026, 9, 21)
        assert (
            _pre_shop._household_day_start_utc(day, ZoneInfo(TORONTO))
            == "2026-09-21 04:00:00"
        )
        assert (
            _pre_shop._household_day_start_utc(day, ZoneInfo(TOKYO))
            == "2026-09-20 15:00:00"
        )

    def test_the_clock_is_read_before_any_connection_opens(self, monkeypatch):
        """GUARD. Both clock reads open a connection of their own, and a
        nested get_conn inside an open one is how this repo has twice
        earned an intermittent 'database is locked' — a wrong answer would
        be easier to find.

        It IS red against main, and for a reason other than the one it is
        named after: main reads no household clock here at all, so it dies
        on an empty list rather than on a badly ordered one. What pins it
        is mutation — move either read below the first `conn = get_conn()`
        and it goes red saying so.

        It watches the reads pre_shop makes THROUGH ITS OWN `_cooker`
        reference rather than counting the deepest connection reached,
        because one nesting here is pre-existing and documented:
        _current_weekly_plan_row is handed a connection and reads
        _household_today inside it, deliberately, on a plain read."""
        _behind(monkeypatch)
        _plan(SERVER_TODAY - timedelta(days=4))
        _decision_at("rice", _at(TORONTO, SERVER_TODAY - timedelta(days=2), 9))

        open_now = 0
        real = get_conn

        class _Counted:
            """sqlite3.Connection.close is read-only, so count through a proxy."""

            def __init__(self, conn):
                self._conn = conn

            def __getattr__(self, name):
                return getattr(self._conn, name)

            def close(self):
                nonlocal open_now
                open_now -= 1
                self._conn.close()

        def _counting():
            nonlocal open_now
            conn = real()
            open_now += 1
            return _Counted(conn)

        for mod in (_pre_shop, _cooker, _wp):
            monkeypatch.setattr(mod, "get_conn", _counting)

        reads: list[tuple[str, int]] = []

        class _Watched:
            def __getattr__(self, name):
                attr = getattr(_cooker, name)
                if name not in ("household_today", "household_zone"):
                    return attr

                def _record(*args, **kwargs):
                    reads.append((name, open_now))
                    return attr(*args, **kwargs)

                return _record

        monkeypatch.setattr(_pre_shop, "_cooker", _Watched())

        assert tools.get_already_have_decisions()[0]["item"] == "rice"
        assert reads == [("household_today", 0), ("household_zone", 0)]
