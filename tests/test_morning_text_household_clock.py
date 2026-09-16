"""
The morning text's own default clock is the HOUSEHOLD's, not the server's.

`build_morning_text(now_local=None)` fell back to `datetime.now()` while its
own docstring called the argument "the household's own clock". The container
runs UTC and households default to America/Toronto, so from eight in the
evening those are different days: a caller that omitted the argument got a
text reasoning about a day no screen in the app agrees with.

It was never a live defect and this file does not pretend otherwise. The
sending loop at digest.py's `run_morning_texts_once` has always passed the
household's now; the function is not in agent.TOOL_FUNCTIONS and sits behind
no route, so nothing in production ever took the default. What it was is the
next caller's trap — and this app produced four separate
server-clock-vs-household-clock defects in two days, so the trap was worth
disarming rather than documenting.

How the clock is frozen: `cooker.household_now` converts a UTC instant to
the household's zone, so these tests replace `cooker.datetime` with a
datetime subclass whose `now()` answers one fixed UTC instant. Only the
clock is faked — the zone lookup, the DB read of households.timezone and the
ZoneInfo fallback all run for real. `date.today()` is deliberately left
alone, so the server's day and the household's genuinely differ inside one
test exactly as they do in production at nine at night.

Each test says in its own docstring whether it is a CATCH (red without the
fix) or a NO-REGRESSION GUARD.
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from app import tools
from app.db import get_conn
from app.tools import cooker as _cooker
from app.tools import digest as _digest

# The week starts three days before the server's today, so its default
# seven-day period spans midnight in both directions: whichever side of it
# the household is on has a planned day, and get_cooker_view's own
# staleness check never fires.
SERVER_TODAY = date.today()
WEEK_START = (SERVER_TODAY - timedelta(days=3)).isoformat()

# 01:30 UTC: Toronto is 21:30 the evening BEFORE — the household a day behind
# the server, which is the production direction and every evening between
# 8pm and midnight Eastern.
UTC_EARLY = datetime.combine(SERVER_TODAY, time(1, 30), tzinfo=timezone.utc)
# 23:30 UTC: Tokyo is 08:30 the morning AFTER — a day ahead of the server,
# the same trap read from the other side.
UTC_LATE = datetime.combine(SERVER_TODAY, time(23, 30), tzinfo=timezone.utc)

TORONTO = "America/Toronto"
TOKYO = "Asia/Tokyo"

HOUSEHOLD_DISH = "Bean Chili"
SERVER_DISH = "Sheet Pan Salmon"


def _set_timezone(name: str) -> None:
    conn = get_conn()
    conn.execute(
        "UPDATE households SET timezone = ? WHERE id = ?", (name, tools.household_id())
    )
    conn.commit()
    conn.close()


def _freeze(monkeypatch, instant: datetime) -> None:
    """Every reading of the wall clock answers `instant`, in UTC."""

    class _Frozen(datetime):
        @classmethod
        def now(cls, tz=None):
            if tz is None:
                return instant.astimezone(timezone.utc).replace(tzinfo=None)
            return instant.astimezone(tz)

    monkeypatch.setattr(_cooker, "datetime", _Frozen)


def _local_date(name: str, instant: datetime) -> date:
    return instant.astimezone(ZoneInfo(name)).date()


def _seed(household_day: date) -> None:
    """
    Two dinners with different names: one on the day the HOUSEHOLD is
    living, one on the day the SERVER thinks it is. The text names one of
    them, and which one is the whole question.
    """
    tools.add_member("Alex")
    # Something to buy, so today_moves builds a shop move and compares the
    # clock against its window. Without that comparison an aware `now`
    # never reaches the arithmetic that the tzinfo-stripping protects, and
    # the guard below passes with the stripping deleted.
    tools.add_grocery_item("Black Beans", quantity="2 cans")
    for name in (HOUSEHOLD_DISH, SERVER_DISH):
        tools.add_recipe(
            name,
            ingredients=[{"item": "Black Beans", "qty": "2 cans"}],
            prep_time_minutes=10,
            cook_time_minutes=40,
            default_servings=2,
        )
    plan_id = tools.create_weekly_plan(WEEK_START)["weekly_plan_id"]
    tools.plan_meal(
        household_day.isoformat(), HOUSEHOLD_DISH, slot="dinner", weekly_plan_id=plan_id
    )
    tools.plan_meal(
        SERVER_TODAY.isoformat(), SERVER_DISH, slot="dinner", weekly_plan_id=plan_id
    )


# ---------- the default ----------

def test_the_bare_default_is_the_households_day_behind_the_server(monkeypatch):
    """
    CATCH. Toronto at half nine in the evening; the server's date has
    already rolled over. A bare build_morning_text() is about the
    household's evening, not the server's tomorrow.
    """
    local = _local_date(TORONTO, UTC_EARLY)
    assert local != SERVER_TODAY  # the whole premise
    _seed(local)
    _set_timezone(TORONTO)
    _freeze(monkeypatch, UTC_EARLY)

    text = _digest.build_morning_text()
    assert text is not None
    assert HOUSEHOLD_DISH in text
    assert SERVER_DISH not in text


def test_the_bare_default_is_the_households_day_ahead_of_the_server(monkeypatch):
    """
    CATCH. Tokyo at half eight in the morning; the server is still on
    yesterday. The same trap from the other side — a household ahead of the
    container, not behind it.
    """
    local = _local_date(TOKYO, UTC_LATE)
    assert local != SERVER_TODAY  # the whole premise
    _seed(local)
    _set_timezone(TOKYO)
    _freeze(monkeypatch, UTC_LATE)

    text = _digest.build_morning_text()
    assert text is not None
    assert HOUSEHOLD_DISH in text
    assert SERVER_DISH not in text


# ---------- what must not move ----------

def test_an_explicit_clock_still_wins_over_the_default(monkeypatch):
    """
    NO-REGRESSION GUARD. The sending loop passes the household's now
    explicitly and always has; an argument must still beat the default,
    or this change would have moved the one caller that was already right.
    """
    local = _local_date(TORONTO, UTC_EARLY)
    _seed(local)
    _set_timezone(TORONTO)
    _freeze(monkeypatch, UTC_EARLY)

    # Ask about the SERVER's day on purpose. The caller's word is final.
    text = _digest.build_morning_text(
        datetime.combine(SERVER_TODAY, time(7, 0))
    )
    assert text is not None
    assert SERVER_DISH in text
    assert HOUSEHOLD_DISH not in text


def test_an_aware_clock_is_still_read_as_the_households_wall_time(monkeypatch):
    """
    NO-REGRESSION GUARD. today_moves compares naive timestamps, so an aware
    argument has always been stripped rather than converted. Unchanged.
    """
    local = _local_date(TORONTO, UTC_EARLY)
    _seed(local)
    _set_timezone(TORONTO)
    _freeze(monkeypatch, UTC_EARLY)

    aware = datetime.combine(SERVER_TODAY, time(7, 0), tzinfo=ZoneInfo(TORONTO))
    assert _digest.build_morning_text(aware) == _digest.build_morning_text(
        datetime.combine(SERVER_TODAY, time(7, 0))
    )


def test_the_default_reads_the_households_stored_zone_not_a_constant(monkeypatch):
    """
    CATCH-by-mutation. Pinned by pointing one household at each zone at the
    SAME UTC instant and requiring the two texts to be about different days.
    A default hard-wired to Toronto — or to the server — fails this.
    """
    local = _local_date(TOKYO, UTC_LATE)
    _seed(local)
    _freeze(monkeypatch, UTC_LATE)

    _set_timezone(TOKYO)
    tokyo_text = _digest.build_morning_text()
    _set_timezone(TORONTO)
    toronto_text = _digest.build_morning_text()

    # Tokyo is on the household day (Bean Chili); Toronto is still on the
    # server's day (Sheet Pan Salmon). One instant, two households, two days.
    assert tokyo_text is not None and HOUSEHOLD_DISH in tokyo_text
    assert toronto_text is not None and SERVER_DISH in toronto_text
