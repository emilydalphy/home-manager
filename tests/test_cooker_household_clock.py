"""
The Cook tab's staleness check runs on the HOUSEHOLD's day, not the
container's.

get_cooker_view refuses _current_weekly_plan_row's "newest plan on file"
fallback for itself when no weekly_plan_id was asked for, so a household
whose last approved week was weeks ago isn't shown August's dinners under
a heading that says "this week" (Loop Board, 2026-09-13). That guard is
right and unchanged. It was asking the wrong clock: `date.today()`, the
server's, in a container that runs UTC while households default to
America/Toronto. So on the LAST day of a plan's period, from 8pm local,
the plan's last day was already "before today" — the view collapsed to
the same empty shape as no-plan-at-all, and the Cook tab said "Nothing
planned this week yet · Last planned: Sep 7-13" for a week still running,
on the evening the household was most likely to be cooking from it. Now's
whole timeline is built off the same call, so it emptied with it.

Measured over HTTP before the fix, household a day behind the server:
    /api/cooker-view -> weekly_plan_id None, meals [], last_planned_label set
    /api/today/moves -> week_state "none", moves []

The fix is cooker.household_today() — the date half of household_now, the
module's own one reader of households.timezone. Both directions are
pinned here: a household BEHIND the server must keep a week the server
thinks has ended, and a household AHEAD of it must lose one the server
thinks is still running.

How the clock is frozen: household_now converts a UTC instant to the
household's zone, so these tests replace `cooker.datetime` with a datetime
subclass whose `now()` answers one fixed UTC instant — the same shape
tests/test_moves_household_clock.py uses. Only the clock is faked; the
zone lookup, the DB read and the ZoneInfo fallback all run for real.
`date.today()` is deliberately left alone, so the server's day and the
household's genuinely differ inside one test exactly as they do in
production at nine at night.

Each test says in its own docstring whether it is a CATCH (red without the
fix) or a NO-REGRESSION GUARD.
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from app import tools
from app.db import get_conn
from app.tools import cooker as _cooker
from app.tools import moves as _moves
from app.tools import weekly_plan as _wp


SERVER_TODAY = date.today()

# 01:30 UTC: Toronto is 21:30 the evening BEFORE, so the household is a
# whole day behind the server. That is the reported bug, the real Pomona
# households, and every evening between 8pm and midnight Eastern.
UTC_EVENING = datetime.combine(SERVER_TODAY, time(1, 30), tzinfo=timezone.utc)
# 23:30 UTC: Tokyo is 08:30 the morning AFTER — the household a day ahead,
# the same defect read from the other side.
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


def _a_recipe(name: str = "Bean Chili") -> None:
    tools.add_recipe(
        name,
        ingredients=[{"item": "Black Beans", "qty": "2 cans"}],
        prep_time_minutes=10,
        cook_time_minutes=40,
        default_servings=2,
    )


def _a_week_ending(last_day: date, dinner_on: date | None = None) -> int:
    """A seven-day approved plan whose LAST day is `last_day`."""
    start = last_day - timedelta(days=6)
    plan_id = tools.create_weekly_plan(
        start.isoformat(), content_start_date=start.isoformat(), day_count=7
    )["weekly_plan_id"]
    _a_recipe()
    tools.plan_meal(
        (dinner_on or last_day).isoformat(), "Bean Chili",
        slot="dinner", weekly_plan_id=plan_id,
    )
    tools.approve_weekly_plan(plan_id)
    return plan_id


# ---------- the household a day BEHIND the server (the reported bug) ----------

def test_the_last_day_of_the_week_at_nine_at_night_still_shows_the_week(monkeypatch):
    """
    CATCH. Toronto, 21:30 on the plan's last day; the server is already
    tomorrow. Before the fix the whole week vanished from Cook.
    """
    _set_timezone(TORONTO)
    _freeze(monkeypatch, UTC_EVENING)
    hh_today = _local_date(TORONTO, UTC_EVENING)
    assert hh_today != SERVER_TODAY, "the harness must actually straddle midnight"

    plan_id = _a_week_ending(hh_today)

    view = tools.get_cooker_view()
    assert view["weekly_plan_id"] == plan_id, "the week is still running where the household lives"
    assert view["last_planned_label"] is None
    assert [m["meal"] for m in view["meals"]] == ["Bean Chili"]


def test_now_is_not_emptied_on_the_last_evening_of_the_week(monkeypatch):
    """
    CATCH. Same bug from the screen that made it visible: /api/today/moves
    is built off the same get_cooker_view() call with no id, so it read
    "week_state: none" with an empty timeline.
    """
    _set_timezone(TORONTO)
    _freeze(monkeypatch, UTC_EVENING)
    hh_today = _local_date(TORONTO, UTC_EVENING)
    _a_week_ending(hh_today)

    payload = _moves.today_moves()
    assert payload["week_state"] == "set", "the badge must not read 'none' over a live week"
    assert "cook" in [m["kind"] for m in payload["moves"]]


def test_a_week_that_really_ended_where_the_household_lives_is_still_stale(monkeypatch):
    """
    NO-REGRESSION GUARD. The guard itself is wanted (2026-09-13) — only
    the clock it read was wrong. A plan whose last day is genuinely behind
    the household still collapses to "no current plan" and names its own
    dates for the honest line.
    """
    _set_timezone(TORONTO)
    _freeze(monkeypatch, UTC_EVENING)
    hh_today = _local_date(TORONTO, UTC_EVENING)
    start = hh_today - timedelta(days=7)  # a seven-day plan that ended yesterday
    _a_week_ending(start + timedelta(days=6))

    view = tools.get_cooker_view()
    assert view["weekly_plan_id"] is None
    assert view["meals"] == []
    assert view["last_planned_label"] == _wp._format_period_range(start.isoformat(), 7)


def test_both_halves_of_the_view_agree_about_what_day_it_is(monkeypatch):
    """
    NO-REGRESSION GUARD (green on the merge base too, at this hour). The
    staleness check and unplanned_meals_ahead are two halves of one view,
    and since 2026-09-14 the second has read the household's clock. What
    is pinned is that they now answer to the SAME date: the genuinely
    stale plan goes, and the loose dinner saved on the household's own
    evening — which the server's date would put in the past — stays.
    """
    _set_timezone(TORONTO)
    _freeze(monkeypatch, UTC_EVENING)
    hh_today = _local_date(TORONTO, UTC_EVENING)
    _a_week_ending(hh_today - timedelta(days=1))  # ended yesterday, household time

    tools.add_recipe("Tacos", ingredients=[{"item": "tortillas", "qty": "1 pack"}])
    tools.plan_meal(hh_today.isoformat(), "Tacos", slot="dinner")  # loose: no plan id

    view = tools.get_cooker_view()
    assert view["weekly_plan_id"] is None
    assert [m["meal"] for m in view["meals"]] == ["Tacos"]
    assert [m["date"] for m in view["meals"]] == [hh_today.isoformat()]


# ---------- the household a day AHEAD of the server (the same bug, mirrored) ----------

def test_a_week_the_server_thinks_is_live_is_stale_once_the_household_is_past_it(monkeypatch):
    """
    CATCH. Tokyo, 08:30 the morning after the server's date. A plan whose
    last day is the SERVER's today is the household's YESTERDAY, so it must
    read stale. Before the fix it was handed back as "this week", with a
    dinner dated the day before.
    """
    _set_timezone(TOKYO)
    _freeze(monkeypatch, UTC_LATE)
    hh_today = _local_date(TOKYO, UTC_LATE)
    assert hh_today != SERVER_TODAY, "the harness must actually straddle midnight"

    start = SERVER_TODAY - timedelta(days=6)
    _a_week_ending(SERVER_TODAY)

    view = tools.get_cooker_view()
    assert view["weekly_plan_id"] is None, "that week ended yesterday where the household lives"
    assert view["meals"] == []
    assert view["last_planned_label"] == _wp._format_period_range(start.isoformat(), 7)


def test_a_week_ending_on_the_households_own_today_is_kept_from_ahead_too(monkeypatch):
    """
    NO-REGRESSION GUARD, from the ahead side: the boundary is still
    strictly "already gone by", so a period whose last day IS the
    household's today is kept.
    """
    _set_timezone(TOKYO)
    _freeze(monkeypatch, UTC_LATE)
    hh_today = _local_date(TOKYO, UTC_LATE)
    plan_id = _a_week_ending(hh_today)

    assert tools.get_cooker_view()["weekly_plan_id"] == plan_id


# ---------- the two things the guard has always deliberately not done ----------

def test_a_future_draft_is_still_left_alone(monkeypatch):
    """
    NO-REGRESSION GUARD, and the one the 2026-09-13 entry is most careful
    about: the test is "has this plan's last day gone by", NOT "does it
    cover today". A plan generated ahead of time has not started, fails
    the second and passes the first, and must fall through untouched —
    cook mode opening next week's draft when today's week has nothing left
    is a real, wanted answer (test_is_current_plan_is_the_same_query_not_a_
    date_rule, TestAPlanThatDoesNotCoverToday).
    """
    _set_timezone(TORONTO)
    _freeze(monkeypatch, UTC_EVENING)
    hh_today = _local_date(TORONTO, UTC_EVENING)
    future_start = hh_today + timedelta(days=14)
    plan_id = tools.create_weekly_plan(
        future_start.isoformat(), content_start_date=future_start.isoformat(), day_count=7
    )["weekly_plan_id"]
    _a_recipe()
    tools.plan_meal(future_start.isoformat(), "Bean Chili", slot="dinner", weekly_plan_id=plan_id)

    view = tools.get_cooker_view()
    assert view["weekly_plan_id"] == plan_id, "a period that hasn't started is not stale"
    assert view["last_planned_label"] is None


def test_naming_a_plan_still_gets_that_plan_whatever_the_clock(monkeypatch):
    """
    NO-REGRESSION GUARD. The reduction only ever fires when the CALLER
    left weekly_plan_id out. An explicit ask — the Plan tab opening one
    week's meal — gets exactly what it asked for, stale or not, and
    resolves no clock to decide it.
    """
    _set_timezone(TORONTO)
    _freeze(monkeypatch, UTC_EVENING)
    hh_today = _local_date(TORONTO, UTC_EVENING)
    plan_id = _a_week_ending(hh_today - timedelta(days=30))

    direct = tools.get_cooker_view(plan_id)
    assert direct["weekly_plan_id"] == plan_id
    assert [m["meal"] for m in direct["meals"]] == ["Bean Chili"]
    assert direct["last_planned_label"] is None, "only the no-id fallback sets this"


# ---------- what must never happen because of the clock ----------

def test_a_clock_that_cannot_be_read_at_all_falls_back_to_the_server(monkeypatch):
    """
    GUARD on the merge base (which never reads the clock here at all), and
    a CATCH against a household_today written without the try/except —
    that version raises straight out of get_cooker_view. Reading
    households.timezone can fail; a Cook tab that 500s over it is worse
    than one that is four hours out, so it falls back to the server's
    date and the view still renders.
    """
    _set_timezone(TORONTO)
    plan_id = _a_week_ending(SERVER_TODAY)

    asked = []

    def _boom(*a, **kw):
        asked.append(1)
        raise RuntimeError("no clock")

    monkeypatch.setattr(_cooker, "household_now", _boom)

    view = tools.get_cooker_view()
    assert view["weekly_plan_id"] == plan_id, "the server's date, rather than a blank screen"
    assert asked, "the household's clock is what it tries first"


def test_an_unreadable_timezone_name_still_renders_the_week(monkeypatch):
    """
    CATCH. A stored zone name ZoneInfo can't read falls back to Toronto
    inside household_now, so the household is still a day behind the
    server at this instant and the week must still be there. Pins that a
    bad setting costs an hour, never a week.
    """
    _set_timezone("Mars/Olympus_Mons")
    _freeze(monkeypatch, UTC_EVENING)
    hh_today = _local_date(TORONTO, UTC_EVENING)
    plan_id = _a_week_ending(hh_today)

    assert tools.get_cooker_view()["weekly_plan_id"] == plan_id


def _clock_reads(monkeypatch) -> int:
    calls = []
    real = _cooker.household_now

    def _counted(*a, **kw):
        calls.append(1)
        return real(*a, **kw)

    monkeypatch.setattr(_cooker, "household_now", _counted)
    tools.get_cooker_view()
    monkeypatch.setattr(_cooker, "household_now", real)
    return len(calls)


def test_the_clock_is_read_once_per_view_not_once_per_card(monkeypatch):
    """
    GUARD on the cost, not on the behaviour. household_now opens its own
    connection, so the view resolves it ONCE and uses the answer — never
    per card, and never inside an open write transaction (see the
    "database is locked" entries in CLAUDE.md). Two reads for a whole
    cooker view: the staleness check here, and
    weekly_plan.unplanned_meals_ahead underneath it. What is pinned is
    that the number does not grow with the week.
    """
    _set_timezone(TORONTO)
    _freeze(monkeypatch, UTC_EVENING)
    hh_today = _local_date(TORONTO, UTC_EVENING)
    plan_id = _a_week_ending(hh_today)
    quiet = _clock_reads(monkeypatch)

    for slot in ("breakfast", "lunch", "snack"):
        tools.plan_meal(hh_today.isoformat(), "Bean Chili", slot=slot, weekly_plan_id=plan_id)
    for back in (1, 2, 3):
        tools.plan_meal(
            (hh_today - timedelta(days=back)).isoformat(), "Bean Chili",
            slot="dinner", weekly_plan_id=plan_id,
        )
    busy = _clock_reads(monkeypatch)

    assert len(tools.get_cooker_view()["meals"]) > 1, "the week really did get busier"
    assert busy == quiet
    # Both ends. The lower one is what makes this a test rather than a
    # tautology: `<= 3` alone is green at ONE, which is what the merge base
    # does here — it never reads the household's clock for this at all.
    assert 2 <= quiet <= 3
