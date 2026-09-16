"""
Add-a-night refuses a night that has already gone by.

`add_dish_day` — the Review stepper's "+" — took any target night inside
the plan's period, past ones included. The only thing keeping those days
off the screen was the picker's own `day.isPast` filter, which is a rule
the SCREEN follows and not one the week does: a tab drawn yesterday, a
retried POST or a direct call could all still send one, and the write went
straight through. Reproduced on both address forms before the fix — an
already-eaten Tuesday dinner rewritten to another dish, and an empty
Monday given a dinner out of nothing.

The clock is the whole subtlety. The container runs UTC and households
default to America/Toronto, so between 8pm local and midnight the server's
date is already tomorrow; a refusal on the server's date would refuse
TONIGHT for four hours every evening, which is worse than the bug it fixes.
So the check reads `weekly_plan._household_today()`, and both directions
are pinned here the way `tests/test_weekly_plan_household_clock.py` pins
them: `cooker.datetime` frozen at one UTC instant with `households.timezone`
set, so the household's day genuinely moves while the server's does not.
The zone lookup and the column read both run for real.

And today itself is never refused. Only a night strictly BEFORE the
household's today is — putting a dish on tonight's dinner is an ordinary
thing to ask for, and a check that took it away would be the same bug
wearing the other hat.

Each test says in its own docstring whether it is a CATCH (red on the
unmodified app) or a NO-REGRESSION GUARD (green either way, here to say
what did not change). Several of the guards are pinned by MUTATION
instead — swapping `_household_today()` for the server's `date.today()`
reddens them, and that is the failure they exist for.
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone

import pytest

from app import tools
from app.db import get_conn
from app.tools import cooker as _cooker
from app.tools import weekly_plan as _wp


SERVER_TODAY = date.today()

# 01:30 UTC is 21:30 the evening BEFORE in Toronto, so the household is a
# whole day BEHIND the server. That is the production direction, and every
# evening between 8pm and midnight Eastern.
UTC_EVENING = datetime.combine(SERVER_TODAY, time(1, 30), tzinfo=timezone.utc)
# 23:30 UTC is 08:30 the morning AFTER in Tokyo — the household a day
# AHEAD, the same split read from the other side.
UTC_LATE = datetime.combine(SERVER_TODAY, time(23, 30), tzinfo=timezone.utc)

TORONTO = "America/Toronto"
TOKYO = "Asia/Tokyo"

# The plan is deliberately wider than the window any one test needs, and
# filed from three days back, so a past target is inside its period and
# `plan_meal`'s own period guard is never what refuses anything here.
PLAN_START = SERVER_TODAY - timedelta(days=3)
PLAN_DAYS = 8

REFUSAL = "That night’s already gone."


# ---------------------------------------------------------------- helpers

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


def _day(offset: int) -> str:
    """A day of the plan, counted off the SERVER's today."""
    return (SERVER_TODAY + timedelta(days=offset)).isoformat()


def _plan() -> int:
    return tools.create_weekly_plan(
        PLAN_START.isoformat(),
        content_start_date=PLAN_START.isoformat(),
        day_count=PLAN_DAYS,
    )["weekly_plan_id"]


def _ids(day: str, slot: str = "dinner") -> list[int]:
    conn = get_conn()
    rows = conn.execute(
        "SELECT id FROM meal_plan_entries WHERE household_id = ? AND date = ? AND slot = ? "
        "ORDER BY id ASC", (tools.household_id(), day, slot),
    ).fetchall()
    conn.close()
    return [r["id"] for r in rows]


def _state(day: str, slot: str = "dinner") -> list[tuple]:
    conn = get_conn()
    rows = conn.execute(
        "SELECT mpe.slot_state, COALESCE(r.name, mpe.freeform_meal) AS meal "
        "FROM meal_plan_entries mpe LEFT JOIN recipes r ON r.id = mpe.recipe_id "
        "WHERE mpe.household_id = ? AND mpe.date = ? AND mpe.slot = ? ORDER BY mpe.id ASC",
        (tools.household_id(), day, slot),
    ).fetchall()
    conn.close()
    return [(r["slot_state"], r["meal"]) for r in rows]


def _seed(source_day: str, target_day: str | None = None) -> int:
    """A dish on `source_day`, and — when named — another on `target_day`."""
    plan = _plan()
    tools.plan_meal(source_day, "Chicken Tacos", slot="dinner", weekly_plan_id=plan)
    if target_day is not None:
        tools.plan_meal(target_day, "Bean Chili", slot="dinner", weekly_plan_id=plan)
    return plan


# -------------------------------------------- the harness's own two clocks

class TestTheFreezeReallySplitsTheTwoClocks:
    def test_a_toronto_evening_puts_the_household_a_day_behind_the_server(self, monkeypatch):
        """GUARD on the harness, not on the app. If the two clocks ever
        agree here, every clock test below passes without testing
        anything."""
        household_today = _behind(monkeypatch)
        assert household_today != SERVER_TODAY
        assert _wp._household_today() == household_today

    def test_a_tokyo_morning_puts_the_household_a_day_ahead(self, monkeypatch):
        """GUARD, the other direction."""
        household_today = _ahead(monkeypatch)
        assert household_today != SERVER_TODAY
        assert _wp._household_today() == household_today


# ------------------------------------------- 1. the target_entry_id path

class TestANightWithADishOnItThatHasGoneBy:
    def test_a_past_night_is_refused(self):
        """CATCH. The reported bug, half one: a stale strip sends
        yesterday's dinner as the day to spend, and the week rewrites a
        meal somebody has already had."""
        plan = _seed(_day(1), _day(-1))

        with pytest.raises(_wp.SlotRefused) as excinfo:
            tools.add_dish_day(plan, _ids(_day(1))[0], target_entry_id=_ids(_day(-1))[0])

        assert str(excinfo.value) == REFUSAL

    def test_nothing_is_written_when_it_is_refused(self):
        """CATCH. The refusal is checked before the swap, so yesterday is
        byte-for-byte as it was and the dish gained no day."""
        plan = _seed(_day(1), _day(-1))
        before = _state(_day(-1))

        with pytest.raises(_wp.SlotRefused):
            tools.add_dish_day(plan, _ids(_day(1))[0], target_entry_id=_ids(_day(-1))[0])

        assert _state(_day(-1)) == before == [("planned", "Bean Chili")]
        assert _state(_day(1)) == [("planned", "Chicken Tacos")]

    def test_the_refusal_is_a_slotrefused_so_the_route_can_tell_it_apart(self):
        """CATCH. A sentence written for a person, in the type the route
        answers 200 for — not the 404 an id or a Python exception gets."""
        plan = _seed(_day(1), _day(-1))

        with pytest.raises(_wp.SlotRefused) as excinfo:
            tools.add_dish_day(plan, _ids(_day(1))[0], target_entry_id=_ids(_day(-1))[0])

        # Still a ValueError underneath, so every existing `except
        # ValueError` around this call keeps catching it.
        assert isinstance(excinfo.value, ValueError)

    def test_a_night_days_back_is_refused_too(self):
        """CATCH. Not just yesterday — anything behind today."""
        plan = _seed(_day(1), _day(-3))

        with pytest.raises(_wp.SlotRefused) as excinfo:
            tools.add_dish_day(plan, _ids(_day(1))[0], target_entry_id=_ids(_day(-3))[0])

        assert str(excinfo.value) == REFUSAL
        assert _state(_day(-3)) == [("planned", "Bean Chili")]


# ----------------------------------------------- 2. the target_date path

class TestAnEmptyNightThatHasGoneBy:
    def test_a_past_empty_night_is_refused(self):
        """CATCH. The reported bug, half two: the strip's "Nothing yet"
        row for a day with no entry at all sends only the date, and a
        dinner appeared on a night that was over."""
        plan = _seed(_day(1))

        with pytest.raises(_wp.SlotRefused) as excinfo:
            tools.add_dish_day(plan, _ids(_day(1))[0], target_date=_day(-1))

        assert str(excinfo.value) == REFUSAL

    def test_nothing_is_written_when_it_is_refused(self):
        """CATCH. A night with no row must still have no row: this path
        CREATES an entry where there was none, so a refusal that ran late
        would leave a dinner on a day nobody can cook."""
        plan = _seed(_day(1))

        with pytest.raises(_wp.SlotRefused):
            tools.add_dish_day(plan, _ids(_day(1))[0], target_date=_day(-1))

        assert _state(_day(-1)) == []

    def test_a_past_night_that_has_since_gained_a_row_is_refused_the_same_way(self):
        """CATCH. The date path resolves the night fresh, so a day the
        screen drew as "Nothing yet" and that has since been filled in is
        refused on the same rule rather than falling through it."""
        plan = _seed(_day(1), _day(-2))

        with pytest.raises(_wp.SlotRefused) as excinfo:
            tools.add_dish_day(plan, _ids(_day(1))[0], target_date=_day(-2))

        assert str(excinfo.value) == REFUSAL
        assert _state(_day(-2)) == [("planned", "Bean Chili")]


# ------------------------------------------------- 3. today is still fine

class TestTodayItselfIsNeverRefused:
    def test_tonights_dinner_still_takes_a_dish(self):
        """GUARD. Green either way — here because refusing tonight would
        be the same bug wearing the other hat, and this is the assertion
        that would go red if the comparison were ever loosened to `<=`.
        Checked by mutation: it reddens under `<=`."""
        plan = _seed(_day(1), _day(0))

        out = tools.add_dish_day(plan, _ids(_day(1))[0], target_entry_id=_ids(_day(0))[0])

        assert out["status"] == "added"
        assert _state(_day(0)) == [("planned", "Chicken Tacos")]

    def test_an_empty_night_today_still_takes_a_dish(self):
        """GUARD, the date path's half of the same promise. Also reddens
        under `<=`."""
        plan = _seed(_day(1))

        out = tools.add_dish_day(plan, _ids(_day(1))[0], target_date=_day(0))

        assert out["status"] == "added"
        assert _state(_day(0)) == [("planned", "Chicken Tacos")]

    def test_a_night_still_ahead_takes_a_dish(self):
        """GUARD. The ordinary tap, on a plan that started three days ago
        — the shape a mid-week household is actually in."""
        plan = _seed(_day(1), _day(3))

        out = tools.add_dish_day(plan, _ids(_day(1))[0], target_entry_id=_ids(_day(3))[0])

        assert out["status"] == "added"
        assert out["replaced"] == "Bean Chili"
        assert _state(_day(3)) == [("planned", "Chicken Tacos")]


# --------------------------------- 4. whose clock, in both directions

class TestTheHouseholdsClockAndNotTheServers:
    def test_a_household_a_day_behind_can_still_fill_its_own_tonight(self, monkeypatch):
        """GUARD against main and a CATCH against the obvious wrong fix.

        Nine at night in Toronto: the server is already on tomorrow, so on
        its clock the household's TONIGHT is yesterday. Refusing on the
        server's date would take tonight's dinner away for four hours
        every evening. Green on the unmodified app (which refuses nothing)
        and green here; it goes red the moment `_household_today()` is
        swapped for `date.today()`, which is measured and is the whole
        reason it exists."""
        household_today = _behind(monkeypatch)
        assert household_today.isoformat() == _day(-1)
        plan = _seed(_day(1), household_today.isoformat())

        out = tools.add_dish_day(
            plan, _ids(_day(1))[0], target_entry_id=_ids(household_today.isoformat())[0]
        )

        assert out["status"] == "added"
        assert _state(household_today.isoformat()) == [("planned", "Chicken Tacos")]

    def test_a_household_a_day_behind_can_still_fill_its_own_empty_tonight(self, monkeypatch):
        """GUARD / anti-wrong-fix CATCH, the date path's half. Same
        mutation reddens it."""
        household_today = _behind(monkeypatch)
        plan = _seed(_day(1))

        out = tools.add_dish_day(plan, _ids(_day(1))[0], target_date=household_today.isoformat())

        assert out["status"] == "added"
        assert _state(household_today.isoformat()) == [("planned", "Chicken Tacos")]

    def test_a_household_a_day_behind_is_still_refused_its_own_yesterday(self, monkeypatch):
        """CATCH. The bug itself, read on the household's clock: the day
        before the household's today is gone wherever the server thinks
        it is."""
        household_today = _behind(monkeypatch)
        gone = (household_today - timedelta(days=1)).isoformat()
        plan = _seed(_day(1), gone)

        with pytest.raises(_wp.SlotRefused) as excinfo:
            tools.add_dish_day(plan, _ids(_day(1))[0], target_entry_id=_ids(gone)[0])

        assert str(excinfo.value) == REFUSAL
        assert _state(gone) == [("planned", "Bean Chili")]

    def test_a_household_a_day_ahead_is_refused_the_servers_today(self, monkeypatch):
        """CATCH, and the direction a server-clock fix gets wrong too.

        Half past eight on a Tokyo morning: the server is still on
        yesterday. The night the SERVER calls today is one the household
        has already had, so it is refused — on the unmodified app it is
        accepted, and on the server's date it would be accepted as well."""
        _ahead(monkeypatch)
        gone = _day(0)  # the server's today; the household's yesterday.
        plan = _seed(_day(2), gone)

        with pytest.raises(_wp.SlotRefused) as excinfo:
            tools.add_dish_day(plan, _ids(_day(2))[0], target_entry_id=_ids(gone)[0])

        assert str(excinfo.value) == REFUSAL
        assert _state(gone) == [("planned", "Bean Chili")]

    def test_a_household_a_day_ahead_is_refused_an_empty_one_too(self, monkeypatch):
        """CATCH. The date path, same instant, the night with no row."""
        _ahead(monkeypatch)
        plan = _seed(_day(2))

        with pytest.raises(_wp.SlotRefused) as excinfo:
            tools.add_dish_day(plan, _ids(_day(2))[0], target_date=_day(0))

        assert str(excinfo.value) == REFUSAL
        assert _state(_day(0)) == []

    def test_a_household_a_day_ahead_can_fill_the_day_the_server_calls_tomorrow(self, monkeypatch):
        """GUARD. The household's own today, which the server calls
        tomorrow, is still an ordinary night to add a dish to. Pinned by
        mutation: it reddens under `<=`."""
        household_today = _ahead(monkeypatch)
        plan = _seed(_day(2), household_today.isoformat())

        out = tools.add_dish_day(
            plan, _ids(_day(2))[0], target_entry_id=_ids(household_today.isoformat())[0]
        )

        assert out["status"] == "added"
        assert _state(household_today.isoformat()) == [("planned", "Chicken Tacos")]


# ------------------------------------- 5. the clock is read on nobody's lock

def test_the_clock_is_read_with_no_connection_of_this_functions_open(monkeypatch):
    """
    GUARD, and the one this repo has twice earned. `_household_today`
    reaches `cooker.household_now`, which opens a connection of its own;
    read while add_dish_day is holding one it would be a nested get_conn,
    and this app has twice paid for that with an intermittent "database is
    locked" rather than a wrong answer — the kind of failure no test sees
    until production. Green on the unmodified app (which never reads the
    clock at all) and pinned by mutation instead: moving the check above
    either branch's `conn.close()` reddens it.

    Both modules import get_conn by name, so both have to be patched — a
    patch of app.db.get_conn alone would watch a door neither of them
    uses. A proxy rather than a patched method: sqlite3.Connection.close
    is read-only.
    """
    depth = {"now": 0, "max": 0}
    real = _wp.get_conn

    class _Counted:
        def __init__(self, conn):
            object.__setattr__(self, "_conn", conn)

        def close(self):
            depth["now"] -= 1
            self._conn.close()

        def __getattr__(self, name):
            return getattr(object.__getattribute__(self, "_conn"), name)

        def __setattr__(self, name, value):
            setattr(object.__getattribute__(self, "_conn"), name, value)

    def tracking():
        conn = _Counted(real())
        depth["now"] += 1
        depth["max"] = max(depth["max"], depth["now"])
        return conn

    monkeypatch.setattr(_wp, "get_conn", tracking)
    monkeypatch.setattr(_cooker, "get_conn", tracking)

    plan = _seed(_day(1), _day(-1))
    depth["max"] = 0
    with pytest.raises(_wp.SlotRefused):
        tools.add_dish_day(plan, _ids(_day(1))[0], target_entry_id=_ids(_day(-1))[0])

    assert depth["max"] == 1


# ------------------------------------------------------------- 6. the route

class TestTheRouteAnswersItAsARefusal:
    def test_a_past_target_entry_is_a_200_that_says_no(self, signed_in):
        """CATCH. 200 with the sentence, the shape drop_dish_from_day and
        the chore rows already use — not the 404 and generic line a row
        id gets, which would report an app that did the right thing as
        broken."""
        plan = _seed(_day(1), _day(-1))

        res = signed_in.post(
            f"/api/week/{PLAN_START.isoformat()}/add-dish-day",
            json={"entry_id": _ids(_day(1))[0], "target_entry_id": _ids(_day(-1))[0]},
        )

        assert res.status_code == 200, res.text
        assert res.json() == {"status": "refused", "message": REFUSAL}
        assert _state(_day(-1)) == [("planned", "Bean Chili")]

    def test_a_past_target_date_is_a_200_that_says_no(self, signed_in):
        """CATCH. The date path over the same route."""
        _seed(_day(1))

        res = signed_in.post(
            f"/api/week/{PLAN_START.isoformat()}/add-dish-day",
            json={"entry_id": _ids(_day(1))[0], "target_date": _day(-1)},
        )

        assert res.status_code == 200, res.text
        assert res.json() == {"status": "refused", "message": REFUSAL}
        assert _state(_day(-1)) == []
