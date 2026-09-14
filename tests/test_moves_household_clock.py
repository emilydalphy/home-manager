"""
Today's moves are about the HOUSEHOLD's day, not the container's.

The deployed container runs in UTC (the Dockerfile is python:3.11-slim and
sets no TZ) and households default to America/Toronto, so from eight in the
evening Toronto time the server's date is already tomorrow. Until this,
moves.py read `date.today()` and `datetime.now()` — the SERVER's clock — so
for roughly four hours every evening, dinner time, Now's whole timeline was
about tomorrow while /api/today/tonight, which has read the household's
clock since it shipped, still said today. Two cards on one screen
disagreeing about what day it is; and Now would offer a cook that
/api/cooker/start then refused with "That's Monday's."

The fix is moves.today_moves / moves_for_day / featured_move_id resolving
the household's own today and now when the caller passes nothing, through
cooker.household_now — the one reader of households.timezone, which the
morning text (digest.build_morning_text) has always gone through.

How the clock is frozen here: cooker.household_now converts a UTC instant
to the household's zone, so these tests replace `cooker.datetime` with a
datetime subclass whose `now()` answers one fixed UTC instant. Only the
clock is faked — the zone lookup, the DB read of households.timezone and
the ZoneInfo fallback all run for real. `date.today()` (the server's date,
which get_cooker_view's staleness check still reads) is deliberately left
alone, so the two genuinely differ inside one test exactly as they do in
production at nine at night.

Each test says in its own docstring whether it is a CATCH (red without the
fix) or a NO-REGRESSION GUARD.
"""
from __future__ import annotations

import datetime as _dt
from datetime import date, datetime, time, timedelta, timezone

import pytest

from app import tools
from app.db import get_conn
from app.tools import cooker as _cooker
from app.tools import moves as _moves
from app.tools import tonight as _tonight


# The plan is anchored on the SERVER's today and spans it in both
# directions, so whichever side of midnight the household is on has a
# planned day, and get_cooker_view's own staleness check (which still reads
# the server's date, by design — it is not this module's) never fires.
SERVER_TODAY = date.today()
WEEK_START = (SERVER_TODAY - timedelta(days=3)).isoformat()
PLAN_DAYS = [(SERVER_TODAY + timedelta(days=n)).isoformat() for n in range(-3, 4)]

# 01:30 UTC: Toronto is 21:30 the evening BEFORE — the household is a day
# behind the server, which is the reported bug, the real Pomona households
# and every evening between 8pm and midnight Eastern.
UTC_EARLY = datetime.combine(SERVER_TODAY, time(1, 30), tzinfo=timezone.utc)
# 23:30 UTC: Tokyo is 08:30 the morning AFTER — the household is a day
# ahead of the server, the same defect read from the other side.
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

    # cooker.household_now is the one moves.py reads; tonight.py keeps its
    # own copy of the same conversion, and the agreement test needs both.
    monkeypatch.setattr(_cooker, "datetime", _Frozen)
    monkeypatch.setattr(_tonight, "datetime", _Frozen)


def _local_date(name: str, instant: datetime) -> str:
    from zoneinfo import ZoneInfo

    return instant.astimezone(ZoneInfo(name)).date().isoformat()


def _seed(dinner_on: list[str] | None = None) -> int:
    """A household, a recipe and a plan with a dinner on every day in it."""
    tools.add_member("Alex")
    tools.add_member("Sam")
    tools.add_recipe(
        "Bean Chili",
        ingredients=[{"item": "Black Beans", "qty": "2 cans"}],
        prep_time_minutes=10,
        cook_time_minutes=40,
        default_servings=2,
    )
    plan_id = tools.create_weekly_plan(WEEK_START)["weekly_plan_id"]
    for day in dinner_on if dinner_on is not None else PLAN_DAYS:
        tools.plan_meal(day, "Bean Chili", slot="dinner", weekly_plan_id=plan_id)
    return plan_id


def _entry_id(day: str, slot: str = "dinner") -> int:
    conn = get_conn()
    row = conn.execute(
        "SELECT id FROM meal_plan_entries WHERE household_id = ? AND date = ? AND slot = ?",
        (tools.household_id(), day, slot),
    ).fetchone()
    conn.close()
    return row["id"]


# ---------- the day ----------

def test_the_day_is_the_households_today_not_the_servers(monkeypatch):
    """
    CATCH. Toronto at half nine in the evening; the server's date has
    already rolled over. Today's moves are about the household's evening,
    which is the day before the server's.
    """
    _seed()
    _set_timezone(TORONTO)
    _freeze(monkeypatch, UTC_EARLY)

    local = _local_date(TORONTO, UTC_EARLY)
    assert local != SERVER_TODAY.isoformat()  # the whole premise of the bug
    assert tools.today_moves()["date"] == local


def test_a_household_ahead_of_the_server_gets_its_own_day_too(monkeypatch):
    """
    CATCH. The same defect from the other side: Tokyo is into tomorrow
    morning while the server is still on last night. A fix that only ever
    looked backwards would pass the test above and fail this one.
    """
    _seed()
    _set_timezone(TOKYO)
    _freeze(monkeypatch, UTC_LATE)

    local = _local_date(TOKYO, UTC_LATE)
    assert local != SERVER_TODAY.isoformat()
    assert tools.today_moves()["date"] == local


def test_moves_for_day_reads_the_same_clock(monkeypatch):
    """
    CATCH. moves_for_day is exported and called on its own (the Kitchen
    tests do), so its own `day=None` has to mean the household's today —
    not just today_moves'.
    """
    _seed()
    _set_timezone(TORONTO)
    _freeze(monkeypatch, UTC_EARLY)

    local = _local_date(TORONTO, UTC_EARLY)
    assert {m["date"] for m in _moves.moves_for_day()} == {local}


def test_the_card_is_ranked_from_the_households_hour(monkeypatch):
    """
    CATCH. `now` decides which move is the card, and the windows it is
    compared against are naive LOCAL times — so ranking them against a UTC
    server clock asked "what's next?" at the wrong hour of the wrong day.
    Tokyo is sitting down to breakfast; the server is still four hours
    short of that morning, so it saw nothing within its four-hour horizon
    and featured nothing at all.
    """
    _seed(dinner_on=[])
    tools.add_recipe(
        "Egg White Bites",
        ingredients=[{"item": "Eggs", "qty": "6"}],
        prep_time_minutes=5,
        cook_time_minutes=15,
        default_servings=2,
    )
    local = _local_date(TOKYO, UTC_LATE)
    plan_id = tools.get_weekly_plan()["weekly_plan_id"]
    tools.plan_meal(local, "Egg White Bites", slot="breakfast", weekly_plan_id=plan_id)
    _set_timezone(TOKYO)
    _freeze(monkeypatch, UTC_LATE)

    moves = _moves.moves_for_day(local)
    assert moves, "the household's morning has a cook on it"
    # Read straight off featured_move_id with no `now` — the argument the
    # UI never passes.
    assert _moves.featured_move_id(moves) == f"cook:{_entry_id(local, 'breakfast')}"


def test_the_moves_screen_and_the_tonight_card_name_the_same_day(monkeypatch):
    """
    CATCH. The reported symptom, pinned at every hour of the day and in
    both zone directions: /api/today/moves and /api/today/tonight must
    never disagree about what day it is.
    """
    _seed()
    for zone in (TORONTO, TOKYO):
        _set_timezone(zone)
        for hour in range(24):
            instant = datetime.combine(SERVER_TODAY, time(hour, 30), tzinfo=timezone.utc)
            _freeze(monkeypatch, instant)
            assert tools.today_moves()["date"] == tools.tonight_check()["date"], (
                f"{zone} at {hour:02d}:30 UTC"
            )


def test_a_cook_the_screen_offers_can_actually_be_started(monkeypatch):
    """
    CATCH. Now offered a cook and /api/cooker/start refused it — "That's
    Monday's — I'll note the start when you cook it Monday" — because the
    two read different clocks. start_cooking has always read the
    household's; the screen is the half that was wrong.
    """
    _seed()
    _set_timezone(TORONTO)
    _freeze(monkeypatch, UTC_EARLY)

    cooks = [m for m in tools.today_moves()["moves"] if m["kind"] == "cook"]
    assert cooks, "the household's today has a cook on it"
    result = _cooker.start_cooking(cooks[0]["entry_id"])
    assert result.get("status") != "refused", result.get("message")


def test_tomorrow_is_the_households_tomorrow(monkeypatch):
    """
    CATCH. With nothing left to do today the payload offers tomorrow's
    first move; tomorrow is counted from the household's today, so it is
    the day after their evening, not the day after the server's.
    """
    local = _local_date(TORONTO, UTC_EARLY)
    tomorrow = (date.fromisoformat(local) + timedelta(days=1)).isoformat()
    _seed(dinner_on=[tomorrow])
    _set_timezone(TORONTO)
    _freeze(monkeypatch, UTC_EARLY)

    payload = tools.today_moves()
    assert payload["date"] == local
    assert payload["featured"] is None
    assert payload["tomorrow"] is not None
    assert payload["tomorrow"]["date"] == tomorrow


# ---------- what must not have changed ----------

def test_an_explicit_day_still_wins(monkeypatch):
    """
    NO-REGRESSION GUARD. /api/today/moves?date= and the tick route both
    pass a day through; a caller that names one is answered about it,
    whatever either clock says.
    """
    _seed()
    _set_timezone(TOKYO)
    _freeze(monkeypatch, UTC_LATE)

    asked = PLAN_DAYS[0]
    assert tools.today_moves(asked)["date"] == asked
    assert {m["date"] for m in _moves.moves_for_day(asked)} == {asked}


def test_a_caller_that_passes_both_is_left_exactly_alone(monkeypatch):
    """
    NO-REGRESSION GUARD for digest.build_morning_text, which already reads
    the household's clock and passes `day=` and `now=` itself. Explicit
    arguments win over anything this module would have resolved.
    """
    _seed()
    _set_timezone(TOKYO)
    _freeze(monkeypatch, UTC_LATE)

    asked = PLAN_DAYS[1]
    at_seven = datetime.combine(date.fromisoformat(asked), time(7, 0))
    payload = tools.today_moves(day=asked, now=at_seven)
    assert payload["date"] == asked
    assert {m["date"] for m in payload["moves"]} == {asked}


def test_the_day_follows_a_now_the_caller_passed_on_its_own(monkeypatch):
    """
    CATCH, and the rule that keeps tests/test_moves.py honest. Passing
    `now` and leaving `day` out is what most of that file does; the day is
    now taken from that clock rather than from the server's, so a caller
    holding one clock can never be answered about another day. Those tests
    build `now` off the server's own today, so nothing there moves.
    """
    _seed()
    _set_timezone(TOKYO)
    _freeze(monkeypatch, UTC_LATE)

    asked = PLAN_DAYS[2]
    payload = tools.today_moves(now=datetime.combine(date.fromisoformat(asked), time(18, 0)))
    assert payload["date"] == asked


def test_an_unreadable_timezone_still_renders_the_screen(monkeypatch):
    """
    CATCH (for the fallback, not for the clock). A stored zone name
    ZoneInfo can't read must never stop Now loading — household_now falls
    back to Toronto, and this asks for the whole payload rather than the
    date alone to prove nothing downstream raised.
    """
    _seed()
    _set_timezone("Mars/Olympus_Mons")
    _freeze(monkeypatch, UTC_EARLY)

    payload = tools.today_moves()
    assert payload["date"] == _local_date(TORONTO, UTC_EARLY)
    assert payload["total"] >= 1


def test_a_clock_that_cannot_be_read_at_all_falls_back_to_the_server(monkeypatch):
    """
    CATCH. The last resort: if reading households.timezone itself raises,
    the screen renders off the server's clock rather than 500ing. A bad
    setting is worth a wrong hour, never a blank Now.
    """
    _seed()

    asked = []

    def _boom(*a, **kw):
        asked.append(1)
        raise RuntimeError("no clock")

    monkeypatch.setattr(_cooker, "household_now", _boom)
    assert tools.today_moves()["date"] == SERVER_TODAY.isoformat()
    assert asked, "the household's clock is what it tries first"


def _clock_reads(monkeypatch) -> int:
    calls = []
    real = _cooker.household_now

    def _counted(*a, **kw):
        calls.append(1)
        return real(*a, **kw)

    monkeypatch.setattr(_cooker, "household_now", _counted)
    tools.today_moves()
    monkeypatch.setattr(_cooker, "household_now", real)
    return len(calls)


def test_the_clock_is_read_a_fixed_number_of_times_not_once_per_move(monkeypatch):
    """
    GUARD on the cost, not on the behaviour. household_now opens its own
    connection, so each place that needs it resolves it ONCE at its entry
    point and threads it down — never per move, never inside an open write
    transaction (see the "database is locked" entries in CLAUDE.md). Two
    reads for a whole Today payload today: moves.today_moves, and
    weekly_plan.unplanned_meals_ahead underneath get_cooker_view. What is
    pinned is that the number does not grow with the day.
    """
    quiet_day = _local_date(TORONTO, UTC_EARLY)
    _seed(dinner_on=[quiet_day])
    _set_timezone(TORONTO)
    _freeze(monkeypatch, UTC_EARLY)
    one_move = _clock_reads(monkeypatch)

    for slot in ("breakfast", "lunch", "snack"):
        tools.plan_meal(
            quiet_day, "Bean Chili", slot=slot,
            weekly_plan_id=tools.get_weekly_plan()["weekly_plan_id"],
        )
    busy_day = _clock_reads(monkeypatch)

    assert len(_moves.moves_for_day(quiet_day)) > 1, "the day really did get busier"
    assert busy_day == one_move
    # Both ends. The upper one is the cost guard; the lower one is what
    # makes this a test rather than a tautology — `<= 3` alone is green at
    # ZERO, which is what `main` does (it never reads the household's clock
    # at all), so it would have passed on the very code this branch fixes.
    assert 1 <= one_move <= 3


# ---------- the other half of the clock: the card the screen answers ----------
#
# Now's timeline running on the household's day is only half a fix. The
# "Tonight needs a dinner" card carries a DATE, the client posts that date
# back verbatim (shell.js's `body: {date: mealDate, ...}`), and the meal is
# written on it. Leave that card on the server's clock and the answer lands
# on a day the timeline is not looking at — which is
# "a dinner answered on Now is saved and invisible" (2026-09-13) reopened
# from the other side, and strictly worse than main, where both halves were
# on the wrong day TOGETHER and so at least agreed.

def _needs_you_dinner_card() -> dict | None:
    from app.tools import weekly_plan as _weekly_plan

    cards = [c for c in _weekly_plan.get_needs_you_items() if c.get("slot") == "dinner"]
    return cards[0] if cards else None


def _seed_undecided(zone: str, instant: datetime) -> str:
    """A household with an approved week whose dinners are all undecided."""
    tools.add_member("Alex")
    tools.add_recipe(
        "Tacos",
        ingredients=[{"item": "Tortillas", "qty": "8"}],
        prep_time_minutes=10,
        cook_time_minutes=20,
        default_servings=2,
    )
    tools.create_weekly_plan(WEEK_START)
    _set_timezone(zone)
    return _local_date(zone, instant)


@pytest.mark.parametrize("zone,instant", [(TORONTO, UTC_EARLY), (TOKYO, UTC_LATE)])
def test_the_dinner_card_names_the_households_day(monkeypatch, zone, instant):
    """
    CATCH. "Tonight" has to mean the household's tonight, in both zone
    directions — the date on this card is the date the answer is written on.
    """
    local = _seed_undecided(zone, instant)
    _freeze(monkeypatch, instant)

    card = _needs_you_dinner_card()
    assert card is not None
    assert card["title"].startswith("Tonight")
    assert card["date"] == local != SERVER_TODAY.isoformat()


@pytest.mark.parametrize("zone,instant", [(TORONTO, UTC_EARLY), (TOKYO, UTC_LATE)])
def test_a_dinner_answered_on_now_is_on_nows_timeline(monkeypatch, zone, instant):
    """
    CATCH, and the whole point: answer the card with the date it carries,
    exactly as the client posts it, and the meal is on the timeline the
    screen then re-reads. Red with the card on one clock and the timeline on
    the other, whichever way round.
    """
    _seed_undecided(zone, instant)
    _freeze(monkeypatch, instant)

    card = _needs_you_dinner_card()
    tools.resolve_needs_you_dinner(card["date"], "Tacos")

    payload = tools.today_moves()
    assert payload["date"] == card["date"]
    assert "Tacos" in [m["title"] for m in payload["moves"] if m["kind"] == "cook"]


def test_a_loose_meal_on_the_households_today_is_not_behind_the_window(monkeypatch):
    """
    CATCH, one door over. unplanned_meals_ahead is what keeps a dinner with
    no plan behind it visible, and its window never looks back — so reading
    the SERVER's date dropped a meal saved on the household's own evening
    the moment the two dates differ, which is the same bug wearing the
    no-plan hat.
    """
    tools.add_member("Alex")
    tools.add_recipe(
        "Tacos", ingredients=[{"item": "Tortillas", "qty": "8"}],
        prep_time_minutes=10, cook_time_minutes=20, default_servings=2,
    )
    _set_timezone(TORONTO)
    local = _local_date(TORONTO, UTC_EARLY)
    tools.plan_meal(local, "Tacos", slot="dinner")  # no plan at all, so no plan link
    _freeze(monkeypatch, UTC_EARLY)

    from app.tools import weekly_plan as _weekly_plan

    assert [m["date"] for m in _weekly_plan.unplanned_meals_ahead()] == [local]
    assert "Tacos" in [
        m["title"] for m in tools.today_moves()["moves"] if m["kind"] == "cook"
    ]


def test_the_answer_still_attaches_to_the_plan_that_covers_that_day(monkeypatch):
    """
    NO-REGRESSION GUARD for the 2026-09-11 rule, which resolve_needs_you_dinner
    now reaches by asking which plan covers the day rather than which plan is
    "current": a day a live plan covers is linked to it, and a day no plan
    covers is saved loose rather than 500ing.
    """
    local = _seed_undecided(TORONTO, UTC_EARLY)
    _freeze(monkeypatch, UTC_EARLY)
    plan_id = tools.get_weekly_plan()["weekly_plan_id"]

    tools.resolve_needs_you_dinner(local, "Tacos")
    conn = get_conn()
    row = conn.execute(
        "SELECT weekly_plan_id FROM meal_plan_entries WHERE household_id = ? AND date = ? "
        "AND slot = 'dinner'",
        (tools.household_id(), local),
    ).fetchone()
    conn.close()
    assert row["weekly_plan_id"] == plan_id

    far = (date.fromisoformat(local) + timedelta(days=60)).isoformat()
    tools.resolve_needs_you_dinner(far, "Tacos")
    conn = get_conn()
    row = conn.execute(
        "SELECT weekly_plan_id FROM meal_plan_entries WHERE household_id = ? AND date = ?",
        (tools.household_id(), far),
    ).fetchone()
    conn.close()
    assert row["weekly_plan_id"] is None
