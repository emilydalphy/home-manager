"""
"Has this plan started?" is the HOUSEHOLD's day, on both sides.

`grocery.set_aside_carried_over_items` runs on the transition into
'approved', before a single ingredient lands, and decides which of the
household's still-unbought lines are LEFTOVERS from a week that has begun
— those get set aside so this week's amounts land on clean lines and the
Shop tab can ask "still on the list from last week — keep or drop?".

Every date on the other side of that comparison comes out of
`weekly_plan.plan_period`, i.e. from the days a planning period was
actually written in, which are household-relative. Until 2026-09-23 the
day it was compared against was `date.today()` — the container's. The
container runs UTC and `households.timezone` defaults to
America/Toronto, so the two are a different day for four hours of every
evening, and the error runs in BOTH directions:

  BEHIND (production's own, every evening after about eight): the server
  is already on tomorrow, so a plan beginning the household's TOMORROW
  reads as started. Its lines are set aside and the household approving a
  week that evening is asked to keep or drop NEXT week's shopping —
  groceries nobody has had the chance to buy, which is the one thing that
  function's own docstring says must never be asked.

  AHEAD (east of UTC): a plan that began the household's TODAY reads as
  not yet begun, so last week's leftovers are NOT set aside and this
  week's amounts land on top of them — the quantity inflation the
  function exists to prevent.

`_live_plan_ids` is the same defect one function up the file, and it is
the one with teeth: its caller `clear_stale_grocery_items` is a blunt
DELETE with no ledger behind it, so on the server's clock a plan whose
LAST day is the household's today read as finished from about 8pm local
and the ingredients for the dinner they were still cooking went off the
list. It moves in the same change, because a module half on one clock is
a new bug rather than a smaller one.

How the clock is frozen: `_freeze` replaces `cooker.datetime` with a
subclass whose `now()` answers one fixed UTC instant, so the household's
day moves without the server's. The `households.timezone` read, the
ZoneInfo lookup and the fallback all run for real.

Each test says in its own docstring whether it is a CATCH (red against
main's app/) or a GUARD (green either way, here to say what did not
change), and every GUARD names the mutation that pins it.
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone

import pytest

from app import tools
from app.db import get_conn
from app.tools import cooker as _cooker
from app.tools import grocery as _grocery


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


def _plan(start: date, days: int = 7, status: str = "approved") -> int:
    """A plan covering `days` from `start`, written the way the app writes one."""
    conn = get_conn()
    row = conn.execute(
        "INSERT INTO weekly_plans (household_id, week_start_date, content_start_date, day_count, status) "
        "VALUES (?, ?, ?, ?, ?)",
        (tools.household_id(), start.isoformat(), start.isoformat(), days, status),
    )
    plan_id = row.lastrowid
    conn.commit()
    conn.close()
    return plan_id


def _line(plan_id: int | None, item: str, status: str = "needed") -> int:
    conn = get_conn()
    row = conn.execute(
        "INSERT INTO grocery_items (household_id, item, quantity, status, source_weekly_plan_id) "
        "VALUES (?, ?, '3', ?, ?)",
        (tools.household_id(), item, status, plan_id),
    )
    line_id = row.lastrowid
    conn.commit()
    conn.close()
    return line_id


def _statuses() -> dict[str, str]:
    return {i["item"]: i["status"] for i in tools.list_grocery_list(status="all")}


# --------------------------------------------------------------------------
# set_aside_carried_over_items — has this plan started?
# --------------------------------------------------------------------------


def test_a_week_that_begins_tomorrow_is_not_asked_about_tonight(monkeypatch):
    """
    CATCH. The reported bug, in production's own direction.

    Approving this week at nine in the evening must not set aside the lines
    of a plan that begins the household's TOMORROW — nobody has had the
    chance to buy them, so there is nothing to keep or drop.
    """
    household_today = _behind(monkeypatch)
    next_week = _plan(household_today + timedelta(days=1))
    approving = _plan(household_today - timedelta(days=6))
    _line(next_week, "Next week onions")

    set_aside = _grocery.set_aside_carried_over_items(approving)

    assert set_aside == []
    assert _statuses()["Next week onions"] == "needed"


def test_a_week_that_began_today_is_set_aside_east_of_utc(monkeypatch):
    """
    CATCH. The same defect from the other side.

    A plan that began the household's TODAY has begun, so whatever is still
    unbought on it is a leftover and must be set aside — otherwise this
    week's amounts merge onto last week's line and the quantity climbs.
    """
    household_today = _ahead(monkeypatch)
    started = _plan(household_today)
    approving = _plan(household_today + timedelta(days=7))
    _line(started, "Last week carrots")

    set_aside = _grocery.set_aside_carried_over_items(approving)

    assert [s["item"] for s in set_aside] == ["Last week carrots"]
    assert _statuses()["Last week carrots"] == "carried"


def test_a_week_that_plainly_went_by_is_still_set_aside(monkeypatch):
    """
    GUARD. The ordinary case, which must not have moved.

    Pinned by the mutation that makes `_household_today` return a date far
    in the past: this then stops being set aside.
    """
    household_today = _behind(monkeypatch)
    last_week = _plan(household_today - timedelta(days=8))
    approving = _plan(household_today)
    _line(last_week, "Old spinach")

    set_aside = _grocery.set_aside_carried_over_items(approving)

    assert [s["item"] for s in set_aside] == ["Old spinach"]


def test_a_standing_want_is_never_set_aside(monkeypatch):
    """
    GUARD. A hand-added line (source NULL) is a person's own want.

    Said precisely, because the obvious claim is wrong and was measured
    rather than assumed: dropping `source_weekly_plan_id IS NOT NULL` from
    the query reddens NOTHING. SQLite evaluates `NULL != 5` as NULL, so the
    `!= ?` beside it already filters a standing want out, and `None` is
    never in `started` either. TWO independent things hold this up and no
    single-line mutation can redden it — which is what defence in depth
    looks like, and is worth knowing before anyone "simplifies" either one.
    Breaking BOTH (COALESCE the column in the query AND accepting None in
    the comprehension) does redden it; that was run.
    """
    household_today = _behind(monkeypatch)
    last_week = _plan(household_today - timedelta(days=8))
    approving = _plan(household_today)
    _line(None, "Oat milk")
    _line(last_week, "Old spinach")

    set_aside = _grocery.set_aside_carried_over_items(approving)

    assert [s["item"] for s in set_aside] == ["Old spinach"]
    assert _statuses()["Oat milk"] == "needed"


def test_next_weeks_unticked_spices_survive_the_evening_too(monkeypatch):
    """
    CATCH. The same date decides a DELETE, not just a status change.

    An earlier week's unticked spices are deleted outright. On the server's
    clock, next week's spices counted as an earlier week's after eight in
    the evening, so they were deleted before that week was ever shopped.
    """
    household_today = _behind(monkeypatch)
    next_week = _plan(household_today + timedelta(days=1))
    approving = _plan(household_today - timedelta(days=6))
    _line(next_week, "Cumin", status="spice")

    _grocery.set_aside_carried_over_items(approving)

    assert "Cumin" in _statuses()


# --------------------------------------------------------------------------
# _live_plan_ids — is this plan still holding a day from today onward?
# --------------------------------------------------------------------------


def test_tonights_own_week_is_still_live_at_nine_in_the_evening(monkeypatch):
    """
    CATCH, and the one with teeth.

    `clear_stale_grocery_items` is a blunt DELETE. A plan whose LAST day is
    the household's today is still being cooked from; on the server's clock
    it read as finished after eight and its ingredients were deleted off
    the list that evening.

    TWO plans, and that is the correction rather than the setup. The first
    draft of this test seeded only the ending one and called
    `clear_stale_grocery_items(current_weekly_plan_id=None)` — and with a
    single plan on file that function resolves `current_id` through
    `get_weekly_plan()` to THAT plan, which then lands in `live` and is
    spared whatever `_live_plan_ids` said. So the delete assertion passed
    on main too: it was vacuous, and only the `_live_plan_ids` line above
    it was a real catch. Found by review.

    The two-plan shape is also the realistic one — it is what a household
    has on the evening they take the nudge to plan next week while this
    week still has a night in it.
    """
    household_today = _behind(monkeypatch)
    ending_today = _plan(household_today - timedelta(days=6))
    next_week = _plan(household_today + timedelta(days=1))
    _line(ending_today, "Tonight's chicken")

    assert ending_today in _grocery._live_plan_ids(next_week)

    _grocery.clear_stale_grocery_items(current_weekly_plan_id=next_week)
    assert "Tonight's chicken" in _statuses()


def test_a_week_that_ended_yesterday_is_still_cleared(monkeypatch):
    """
    GUARD. The quantity-stacking bug this function exists to fix.

    Pinned by the mutation that makes `_live_plan_ids` return every plan:
    this line then survives.
    """
    household_today = _behind(monkeypatch)
    over = _plan(household_today - timedelta(days=7))
    current = _plan(household_today)
    _line(over, "Stale chicken")

    _grocery.clear_stale_grocery_items(current_weekly_plan_id=current)

    assert "Stale chicken" not in _statuses()


# --------------------------------------------------------------------------
# The clock is read on the caller's connection
# --------------------------------------------------------------------------


def test_the_clock_is_read_on_the_callers_connection(monkeypatch):
    """
    GUARD, and it is a real hazard rather than a tidiness rule.

    approve_weekly_plan holds an open write transaction across this call.
    SQLite gives one writer at a time, so a nested get_conn in that
    position is how this app has twice earned an intermittent "database is
    locked". Counting at `sqlite3.connect` rather than at a module's own
    `get_conn`, because a function-local `from ..db import get_conn` is
    invisible to a module-level patch and this package has that shape.

    Pinned by the mutation that drops `conn=conn` from the clock read:
    the count then goes up by one.
    """
    import sqlite3

    household_today = _behind(monkeypatch)
    last_week = _plan(household_today - timedelta(days=8))
    approving = _plan(household_today)
    _line(last_week, "Old spinach")

    opened = []
    real_connect = sqlite3.connect

    def counting(*a, **kw):
        opened.append(1)
        return real_connect(*a, **kw)

    conn = get_conn()
    monkeypatch.setattr(sqlite3, "connect", counting)
    try:
        _grocery.set_aside_carried_over_items(approving, conn=conn)
    finally:
        monkeypatch.setattr(sqlite3, "connect", real_connect)
        conn.commit()
        conn.close()

    assert opened == [], "handed a connection, this must open none of its own"
    assert _statuses()["Old spinach"] == "carried"


def test_the_module_reads_no_server_clock_anywhere(monkeypatch):
    """
    GUARD on the sweep, not on one reader.

    A module half on one clock is a new bug rather than a smaller one, and
    this file had exactly two PYTHON reads. Parsed rather than grepped, so
    the word surviving in the prose above both fixes is not a false
    positive — ast never sees a comment at all.

    TWO THINGS THIS DOES NOT COVER, named because the test's title is
    broader than its reach and this log has been bitten by a guard whose
    scope was assumed:

    1. The eight SQL `datetime('now')` reads in this module are NOT
       swept, and must not be. Every one of them WRITES or compares a UTC
       instant against another UTC instant — `removed_at`, `updated_at`,
       `inventory_added_at`, `list_built_at`'s window. There is no
       calendar day in any of them to be wrong about. The rule this file
       is about is a household DAY compared against a plan date, and
       there are exactly two of those (`:1175` and `:1321`), both
       converted.

    2. A module-qualified spelling — `_dt.date.today()` — walks straight
       past this, because the owner is an Attribute rather than a Name.
       Measured: reverting both reads that way leaves this green and only
       the four behaviour tests catch it. That limitation is INHERITED —
       `tests/test_last_clock_pockets.py`'s guard has the identical
       restriction — so it is not novel here, but it is real.

    The pair set matches that established guard exactly, `("time",
    "time")` included, rather than being quietly narrower than the thing
    it is modelled on.
    """
    import ast
    import inspect

    src = inspect.getsource(_grocery)
    tree = ast.parse(src)
    found = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        fn = node.func
        if not isinstance(fn, ast.Attribute):
            continue
        owner = fn.value
        if not isinstance(owner, ast.Name):
            continue
        if (owner.id, fn.attr) in {
            ("date", "today"), ("datetime", "now"), ("datetime", "today"),
            ("datetime", "utcnow"), ("time", "time"),
        }:
            found.append(f"{owner.id}.{fn.attr}()")

    assert found == [], f"server-clock reads left in grocery.py: {found}"
