"""
Staples ran on the SERVER's clock, so a staple due tomorrow landed on the
shopping list tonight (Loop Board bug, filed 2026-09-21, built 2026-09-22).

The container runs UTC and `households.timezone` defaults to
America/Toronto, so from about 8pm local the server's date is already
tomorrow. `app/tools/staples.py` read `date.today()` at eleven call sites,
all funnelled through one `_today()`.

The one with teeth is `sync_due_staples`, because it is a WRITE and it is
called on every read of the grocery list: a staple whose `next_due_at` is
the household's TOMORROW was the server's TODAY, so it was pushed onto the
real list that evening — an extra line, in exactly the hours somebody
checks the list before a morning shop. `due` on the Staples card read a day
early for the same window.

`quantities._estimate_expiration_date` moves in the same commit. It is the
WRITE side of the half-conversion the 2026-09-18 `last-clock-pockets` entry
named and left, and a half-converted module is a new bug rather than a
smaller one (this repo's own rule).

The clock is frozen exactly the way `tests/test_last_clock_pockets.py`
freezes it: `cooker.datetime` is replaced with a subclass whose `now()`
answers one fixed UTC instant, so the zone lookup, the
`households.timezone` read and the ZoneInfo fallback all run for real and
only the wall clock is faked. `date.today()` is left alone, so the two
genuinely differ inside one test exactly as they do in production. Both
directions are pinned: Toronto 21:30 (the household a day BEHIND — the
reported bug, and production's own direction) and Tokyo 08:30 (a day
AHEAD, the same split from the other side).

Each test says in its own docstring whether it is a CATCH (red against
main) or a NO-REGRESSION GUARD (green either way, pinned by a named
mutation).
"""
from __future__ import annotations

import sqlite3
from datetime import date, datetime, time, timedelta, timezone

import pytest

from app import tools
from app.db import get_conn
from app.tools import cooker as _cooker
from app.tools import quantities as _quantities
from app.tools import staples as _staples


SERVER_TODAY = date.today()

# 01:30 UTC: Toronto is 21:30 the evening BEFORE, so the household is a
# whole day BEHIND the server — every evening between 8pm and midnight
# Eastern, which is the reported bug.
UTC_EVENING = datetime.combine(SERVER_TODAY, time(1, 30), tzinfo=timezone.utc)
# 23:30 UTC: Tokyo is 08:30 the morning AFTER — a day AHEAD.
UTC_LATE = datetime.combine(SERVER_TODAY, time(23, 30), tzinfo=timezone.utc)

TORONTO = "America/Toronto"
TOKYO = "Asia/Tokyo"


def _set_timezone(name: str) -> None:
    conn = get_conn()
    conn.execute("UPDATE households SET timezone = ? WHERE id = ?", (name, tools.household_id()))
    conn.commit()
    conn.close()


def _freeze(monkeypatch, instant: datetime) -> None:
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


def _due_on(item: str, when: date) -> int:
    """A staple whose next_due_at is exactly `when`. Returns its id."""
    row = _staples.add_staple(item, every_days=30)
    conn = get_conn()
    conn.execute(
        "UPDATE staples SET next_due_at = ? WHERE household_id = ? AND item = ?",
        (when.isoformat(), tools.household_id(), item),
    )
    conn.commit()
    conn.close()
    return row["id"]


def _listed(item: str) -> bool:
    conn = get_conn()
    hit = conn.execute(
        "SELECT 1 FROM grocery_items WHERE household_id = ? AND item = ? AND status = 'needed'",
        (tools.household_id(), item),
    ).fetchone()
    conn.close()
    return hit is not None


# --------------------------------------------------------------------------
# The reported bug: a staple due tomorrow, written onto the list tonight.
# --------------------------------------------------------------------------

class TestAStapleDueTomorrowStaysOffTonightsList:
    def test_the_household_is_a_day_behind_and_tomorrows_staple_is_not_listed(self, monkeypatch):
        """CATCH. Red against main: on the server's clock the household's
        TOMORROW is the server's TODAY, so sync_due_staples wrote the line."""
        household_today = _behind(monkeypatch)
        _due_on("Dish soap", household_today + timedelta(days=1))
        _staples.sync_due_staples()
        assert not _listed("Dish soap")

    def test_a_staple_due_today_is_still_listed(self, monkeypatch):
        """NO-REGRESSION GUARD. The fix must narrow what gets listed, not
        stop listing. Pinned by the mutation that makes _today() answer
        tomorrow, which reddens this by listing nothing at all."""
        household_today = _behind(monkeypatch)
        _due_on("Dish soap", household_today)
        _staples.sync_due_staples()
        assert _listed("Dish soap")

    def test_the_household_is_a_day_ahead_and_todays_staple_is_listed(self, monkeypatch):
        """CATCH from the other side. With the household a day AHEAD the
        server's today is a day EARLIER, so a staple due on the household's
        own today reads as not yet due and is withheld on the one day it
        should appear. The quiet direction rather than the loud one, and
        still wrong."""
        household_today = _ahead(monkeypatch)
        _due_on("Dish soap", household_today)
        _staples.sync_due_staples()
        assert _listed("Dish soap")


# --------------------------------------------------------------------------
# The Staples card's own "due" flag reads the same clock.
# --------------------------------------------------------------------------

class TestTheDueFlagOnTheCard:
    def test_due_tomorrow_does_not_read_as_due_tonight(self, monkeypatch):
        """CATCH. `due` is `next_due <= today` and drove the card a day
        early for the same four-hour window."""
        household_today = _behind(monkeypatch)
        _due_on("Dish soap", household_today + timedelta(days=1))
        row = next(s for s in _staples.list_staples() if s["item"] == "Dish soap")
        assert row["due"] is False
        assert row["due_words"] == "due tomorrow"

    def test_due_today_still_reads_due(self, monkeypatch):
        """NO-REGRESSION GUARD, same mutation as above."""
        household_today = _behind(monkeypatch)
        _due_on("Dish soap", household_today)
        row = next(s for s in _staples.list_staples() if s["item"] == "Dish soap")
        assert row["due"] is True
        assert row["due_words"] == "probably running low"


# --------------------------------------------------------------------------
# The expiry estimate — the write side of the same half-conversion.
# --------------------------------------------------------------------------

class TestTheExpiryEstimate:
    def test_it_counts_from_the_households_day(self, monkeypatch):
        """CATCH. Red against main: a dairy row added at Toronto 21:30
        landed a day late, counted from the server's already-tomorrow."""
        household_today = _behind(monkeypatch)
        days = _quantities._lookup_item_shelf_life_days("Milk", "dairy")
        got = _quantities._estimate_expiration_date("dairy", "Milk")
        assert got == (household_today + timedelta(days=days)).isoformat()

    def test_an_explicit_from_date_still_wins(self, monkeypatch):
        """NO-REGRESSION GUARD. Every caller that already knows the day
        must keep deciding. Pinned by the mutation that ignores from_date."""
        _behind(monkeypatch)
        base = date(2026, 3, 1)
        days = _quantities._lookup_item_shelf_life_days("Milk", "dairy")
        got = _quantities._estimate_expiration_date("dairy", "Milk", from_date=base)
        assert got == (base + timedelta(days=days)).isoformat()


# --------------------------------------------------------------------------
# The harness this module's own tests depend on, and the cost.
# --------------------------------------------------------------------------

class TestTheOverrideAndTheCost:
    def test_the_today_override_still_wins(self, monkeypatch):
        """NO-REGRESSION GUARD, and load-bearing: tests/test_staples.py
        pins a fixed Wednesday through _TODAY_OVERRIDE and travels from
        there. Pinned by the mutation that drops the override branch."""
        _behind(monkeypatch)
        pinned = date(2026, 9, 16)
        monkeypatch.setattr(_staples, "_TODAY_OVERRIDE", pinned)
        assert _staples._today() == pinned

    def test_the_override_reads_no_clock_at_all(self, monkeypatch):
        """GUARD on the cost of the override path: with it set, _today()
        must not open a connection to find a timezone it is not going to
        use. Counts sqlite3.connect globally, because a module-level patch
        cannot see a function-local `from ..db import get_conn`."""
        _behind(monkeypatch)
        monkeypatch.setattr(_staples, "_TODAY_OVERRIDE", date(2026, 9, 16))
        seen = {"n": 0}
        real = sqlite3.connect

        def counting(*a, **k):
            seen["n"] += 1
            return real(*a, **k)

        monkeypatch.setattr(sqlite3, "connect", counting)
        _staples._today()
        assert seen["n"] == 0

    def test_listing_staples_reads_the_clock_once_not_once_per_row(self, monkeypatch):
        """GUARD, and the reason _shape takes `today` at all. _today()
        opens a connection to read households.timezone, so a per-row read
        costs one connection per staple — the cost this repo has twice
        gone out of its way to avoid. Pinned by the mutation that drops
        the argument and lets _shape resolve its own day: with six staples
        that takes this from 2 connections to 13, measured.

        Counts sqlite3.connect globally rather than a module's own
        get_conn, for the reason the 2026-09-21 week-payload entry gives:
        a module-level patch cannot see a function-local import.
        """
        _behind(monkeypatch)
        for name in ("Dish soap", "Foil", "Bin bags", "Paper towels", "Cling film", "Sponges"):
            _staples.add_staple(name, every_days=30)

        seen = {"n": 0}
        real = sqlite3.connect

        def counting(*a, **k):
            seen["n"] += 1
            return real(*a, **k)

        monkeypatch.setattr(sqlite3, "connect", counting)
        rows = _staples.list_staples()
        assert len(rows) == 6
        # One for the clock, one for the rows. The number that matters is
        # that it does not grow with len(rows).
        assert seen["n"] <= 3, f"list_staples opened {seen['n']} connections for 6 staples"

    def test_the_clock_is_read_before_the_connection_opens(self, monkeypatch):
        """GUARD on the ORDERING. A nested get_conn inside an open write
        transaction is how this repo has twice earned an intermittent
        "database is locked", so the household's day is resolved above
        get_conn.

        RED AGAINST MAIN FOR A REASON OTHER THAN THE ONE IT IS NAMED FOR,
        and this log keeps having to unpick that statistic, so it is said
        here: main never reads the household clock in this module at all,
        so it dies on the "never read the household clock" assertion above
        rather than on the depth below it. Its real claim is pinned by
        MUTATION instead — move `today = _today()` below `conn =
        get_conn()` in list_staples and the depth assertion fails with 1.
        """
        _behind(monkeypatch)
        _staples.add_staple("Dish soap", every_days=30)

        open_connections = {"n": 0, "worst": 0}
        real = sqlite3.connect

        class _Tracking(sqlite3.Connection):
            def close(self):
                open_connections["n"] -= 1
                super().close()

        def counting(*a, **k):
            k.setdefault("factory", _Tracking)
            conn = real(*a, **k)
            open_connections["n"] += 1
            return conn

        monkeypatch.setattr(sqlite3, "connect", counting)

        seen_depth = []
        real_today = _cooker.household_today

        def watched():
            seen_depth.append(open_connections["n"])
            return real_today()

        monkeypatch.setattr(_cooker, "household_today", watched)
        _staples.list_staples()
        assert seen_depth, "list_staples never read the household clock"
        assert seen_depth[0] == 0, (
            f"the household clock was read with {seen_depth[0]} of this call's "
            "connections already open"
        )
