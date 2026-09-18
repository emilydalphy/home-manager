"""
The freezer flow runs on the HOUSEHOLD's clock, not the container's.

`app/tools/defrost.py` had three `date.today()` reads and every one of them
decided a calendar day a household sees. The deployed container runs UTC and
households.timezone defaults to America/Toronto, so from 8pm Eastern the
server's date is already tomorrow — and the reported bug is the one with
teeth:

  `confirm_frozen_items`' "too late to thaw" test read the server's date, so
  a cook two nights out — whose move date is the household's own TODAY — was
  refused with the too-late note for the four hours every evening when
  somebody actually taps "Something in the freezer?". Measured before the
  fix, household Toronto, a 48h item, nights at H+1/H+2/H+3:

      Toronto 09:00  offered [H+2, H+3]   too-late [H+1]
      Toronto 21:30  offered [H+3]        too-late [H+1, H+2]   <- H+2 lost

  The household could genuinely have started that thaw that evening with
  about forty-five hours in hand.

The two read paths went with it in the same change, on this repo's own rule
that a half-converted module is a new bug rather than a smaller one:
`get_defrost_today` put TOMORROW's move on the Today tile and hid tonight's,
and `get_defrost_schedule`'s window lost today's own pending row off the
near edge while gaining an extra day at the far one.

How the clock is frozen, following test_weekly_plan_household_clock.py and
test_morning_text_household_clock.py: `cooker.household_now` converts a UTC
instant into the household's zone, so these tests swap `cooker.datetime` for
a subclass whose `now()` answers one fixed UTC instant. Only the clock is
faked — the zone lookup, the DB read of households.timezone and the ZoneInfo
fallback all run for real, and `date.today()` is deliberately left alone so
the server's day and the household's genuinely differ inside one test
exactly as they do in production at nine at night.

BOTH directions are pinned. Toronto 21:30 puts the household a day BEHIND
the server, which is the reported bug and the direction production actually
runs; Tokyo 08:30 puts it a day AHEAD, where the server's clock was wrong
the other way — booking a move the household had already run out of time
for.

Each test says in its own docstring whether it is a CATCH (red against the
unmodified app) or a NO-REGRESSION GUARD.
"""
from __future__ import annotations

import inspect
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from app import tools
from app.db import get_conn
from app.tools import cooker as _cooker
from app.tools import defrost as _defrost

TORONTO = "America/Toronto"
TOKYO = "Asia/Tokyo"

# The process's own day. Left real on purpose (see the module docstring):
# these instants are chosen so the household lands on a DIFFERENT one.
SERVER_TODAY = date.today()

# 01:30 UTC is 21:30 the evening BEFORE in Toronto — the household a day
# behind the server. Production's own direction, every evening between 8pm
# and midnight Eastern.
UTC_EVENING = datetime.combine(SERVER_TODAY, time(1, 30), tzinfo=timezone.utc)
# 23:30 UTC is 08:30 the morning AFTER in Tokyo — a day ahead of the server,
# the same trap read from the other side.
UTC_MORNING_AHEAD = datetime.combine(SERVER_TODAY, time(23, 30), tzinfo=timezone.utc)

# "Whole Chicken" is a _LARGE_KEYWORDS match, so 48h — and with no
# dinner_window answered _move_date falls back to whole-day counting, i.e.
# exactly two calendar days before the cook. Deterministic on every weekday,
# which matters under CI's `clock` matrix.
FROZEN_ITEM = "Whole Chicken"
DISH = "Roast Chicken Dinner"
LEAD_DAYS = 2


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


def _behind(monkeypatch) -> date:
    """Toronto at half nine in the evening: the household a day behind."""
    _set_timezone(TORONTO)
    _freeze(monkeypatch, UTC_EVENING)
    household_day = UTC_EVENING.astimezone(ZoneInfo(TORONTO)).date()
    assert household_day == SERVER_TODAY - timedelta(days=1)
    return household_day


def _ahead(monkeypatch) -> date:
    """Tokyo at half eight in the morning: the household a day ahead."""
    _set_timezone(TOKYO)
    _freeze(monkeypatch, UTC_MORNING_AHEAD)
    household_day = UTC_MORNING_AHEAD.astimezone(ZoneInfo(TOKYO)).date()
    assert household_day == SERVER_TODAY + timedelta(days=1)
    return household_day


def _seed_plan(nights: list[date]) -> int:
    """A plan whose every night cooks one whole chicken."""
    tools.add_member("Alex")
    tools.add_recipe(
        DISH,
        ingredients=[{"item": FROZEN_ITEM, "qty": "1", "category": "meat/seafood"}],
        prep_time_minutes=15,
        cook_time_minutes=90,
        default_servings=4,
    )
    start = min(nights) - timedelta(days=1)
    span = (max(nights) - start).days + 2
    plan_id = tools.create_weekly_plan(start.isoformat(), day_count=span)["weekly_plan_id"]
    for night in nights:
        tools.plan_meal(night.isoformat(), DISH, slot="dinner", weekly_plan_id=plan_id)
    return plan_id


def _defrost_row(plan_id: int, task_date: date) -> None:
    conn = get_conn()
    conn.execute(
        "INSERT INTO prep_tasks (household_id, weekly_plan_id, task_date, description, "
        "related_meal, status, task_type, quantity) "
        "VALUES (?, ?, ?, ?, 'dinner', 'pending', 'defrost', '')",
        (tools.household_id(), plan_id, task_date.isoformat(), f"Move the chicken — {task_date}."),
    )
    conn.commit()
    conn.close()


def _empty_plan(around: date) -> int:
    return tools.create_weekly_plan(
        (around - timedelta(days=2)).isoformat(), day_count=14
    )["weekly_plan_id"]


# ---------- 1. confirm_frozen_items: the reported bug ----------

class TestTheFreezerAskInTheEvening:
    def test_the_night_whose_thaw_starts_tonight_is_still_booked(self, monkeypatch):
        """
        CATCH, and the whole ticket. Toronto at half nine: the server's date
        is already tomorrow, so the cook two nights out — whose move date is
        the household's own today — read as already gone and came back with
        the too-late note instead of a task.
        """
        household_day = _behind(monkeypatch)
        night = household_day + timedelta(days=LEAD_DAYS)
        plan_id = _seed_plan([night])

        result = _defrost.confirm_frozen_items(plan_id, [FROZEN_ITEM])

        assert [c["date"] for c in result["created"]] == [night.isoformat()]
        assert [c["task_date"] for c in result["created"]] == [household_day.isoformat()]
        assert result["notes"] == []

    def test_only_the_night_that_has_really_run_out_of_time_is_refused(self, monkeypatch):
        """
        CATCH. The same three nights the reproduction used. On the server's
        clock two of the three were refused; on the household's only the
        cook happening today is, and its note is the one the household
        should read.
        """
        household_day = _behind(monkeypatch)
        nights = [household_day + timedelta(days=n) for n in (1, 2, 3)]
        plan_id = _seed_plan(nights)

        result = _defrost.confirm_frozen_items(plan_id, [FROZEN_ITEM])

        assert sorted(c["date"] for c in result["created"]) == [
            nights[1].isoformat(), nights[2].isoformat()
        ]
        assert [n["date"] for n in result["notes"]] == [nights[0].isoformat()]
        assert result["notes"][0]["note"] == _defrost.TOO_LATE_TO_THAW_NOTE

    def test_a_night_the_household_has_already_lost_is_refused(self, monkeypatch):
        """
        CATCH, from the other direction. Tokyo at half eight in the morning
        is a day AHEAD of the server, so a cook whose move date is the
        server's today is the household's YESTERDAY — genuinely too late,
        and the server's clock would have booked a move for a day that has
        gone.
        """
        household_day = _ahead(monkeypatch)
        # Its move lands on SERVER_TODAY, which the household passed last night.
        night = household_day + timedelta(days=LEAD_DAYS - 1)
        plan_id = _seed_plan([night])

        result = _defrost.confirm_frozen_items(plan_id, [FROZEN_ITEM])

        assert result["created"] == []
        assert [n["date"] for n in result["notes"]] == [night.isoformat()]

    def test_the_task_really_lands_in_the_table(self, monkeypatch):
        """
        CATCH. The return value is one thing and the row the Today tile
        reads is another; the evening's lost night left nothing behind at
        all.
        """
        household_day = _behind(monkeypatch)
        night = household_day + timedelta(days=LEAD_DAYS)
        plan_id = _seed_plan([night])

        _defrost.confirm_frozen_items(plan_id, [FROZEN_ITEM])

        conn = get_conn()
        rows = conn.execute(
            "SELECT task_date, status FROM prep_tasks WHERE task_type = 'defrost'"
        ).fetchall()
        conn.close()
        assert [(r["task_date"], r["status"]) for r in rows] == [
            (household_day.isoformat(), "pending")
        ]

    def test_a_cook_happening_today_is_still_refused(self, monkeypatch):
        """
        GUARD. The rule itself is unchanged — _move_date always leaves a
        full calendar day, so tonight's own dinner can never be thawed for
        and still gets the calm note. Only the clock it is measured against
        moved.
        """
        household_day = _behind(monkeypatch)
        plan_id = _seed_plan([household_day])

        result = _defrost.confirm_frozen_items(plan_id, [FROZEN_ITEM])

        assert result["created"] == []
        assert [n["date"] for n in result["notes"]] == [household_day.isoformat()]

    def test_an_ordinary_night_well_ahead_is_untouched(self, monkeypatch):
        """GUARD. Nothing about a night with days of room in hand changed."""
        household_day = _behind(monkeypatch)
        night = household_day + timedelta(days=5)
        plan_id = _seed_plan([night])

        result = _defrost.confirm_frozen_items(plan_id, [FROZEN_ITEM])

        assert [c["task_date"] for c in result["created"]] == [
            (night - timedelta(days=LEAD_DAYS)).isoformat()
        ]
        assert result["notes"] == []

    def test_the_clock_is_read_before_the_write_opens_its_connection(self, monkeypatch):
        """
        GUARD, pinned by MUTATION and not by redness. The ordering is
        already right on the unmodified app, because `date.today()` opens
        nothing — and against it this ERRORS on a ValueError rather than
        failing, because the name it looks for is not there at all, so its
        redness says nothing about its own claim. `cooker.household_today`
        DOES open its own connection, so resolving it after get_conn would
        put a second connection inside this function's write, which is how
        this codebase has twice earned an intermittent "database is
        locked". Move the assignment below `conn = get_conn()` and this
        fails.
        """
        src = inspect.getsource(_defrost.confirm_frozen_items)
        clock_at = src.index("_cooker.household_today()")
        conn_at = src.index("conn = get_conn()")
        assert clock_at < conn_at, "the household's clock must be read before the connection opens"


# ---------- 2. the ask and the write read the same clock ----------

class TestTheAskAndTheWriteAgree:
    def test_the_ask_leaves_off_only_the_night_the_household_has_run_out_of(self, monkeypatch):
        """
        CATCH, found at merge on 2026-09-17. This ticket fixed the WRITE's
        clock while a sibling branch taught the ASK to leave off a night
        it is already too late to thaw for (meat_items_for_plan, rule 4)
        — reading date.today() "to match the write". Merged, the ask was
        on the server's clock and the write on the household's, and from
        8pm Eastern the card dropped a night the write would have booked.
        Toronto at half nine: three cooks, tomorrow / the night after /
        the night after that, a two-day thaw. Tomorrow needed its move
        yesterday and is honestly gone; the other two are still open on
        the household's clock. On the server's clock, a day ahead, the
        middle one was dropped as well. Pinned by mutation: put
        date.today() back in _settled_nights and this fails.
        """
        household_day = _behind(monkeypatch)
        nights = [household_day + timedelta(days=n) for n in (1, 2, 3)]
        plan_id = _seed_plan(nights)

        offered = [n["date"] for n in _defrost.meat_items_for_plan(plan_id)[0]["nights"]]

        assert offered == [n.isoformat() for n in nights[1:]]

    def test_every_night_the_ask_offers_is_one_the_write_books(self, monkeypatch):
        """
        CATCH. §8 rule 7 the right way up: in the evening the card offered
        chips covering three nights and the write refused two of them, one
        of which the household had the time for. Now the card never offers
        a night the write would refuse — every chip it shows is booked — and
        the one refusal the write still has to make is for the night that
        really has run out, which the card already left off.
        """
        household_day = _behind(monkeypatch)
        nights = [household_day + timedelta(days=n) for n in (1, 2, 3)]
        plan_id = _seed_plan(nights)

        offered = {n["date"] for n in _defrost.meat_items_for_plan(plan_id)[0]["nights"]}
        result = _defrost.confirm_frozen_items(plan_id, [FROZEN_ITEM])
        booked = {c["date"] for c in result["created"]}
        refused = {n["date"] for n in result["notes"]}

        assert offered == booked
        assert refused == {nights[0].isoformat()}
        assert not (offered & refused)


# ---------- 3. get_defrost_today ----------

class TestTodaysFridgeMove:
    def test_tonights_move_is_on_the_tile_and_tomorrows_is_not(self, monkeypatch):
        """
        CATCH. Toronto at half nine: the Today tile showed TOMORROW's move
        and hid tonight's still-pending one, which is both a false positive
        and a false negative on the same card.
        """
        household_day = _behind(monkeypatch)
        plan_id = _empty_plan(household_day)
        _defrost_row(plan_id, household_day)
        _defrost_row(plan_id, household_day + timedelta(days=1))

        rows = _defrost.get_defrost_today()

        assert [r["task_date"] for r in rows] == [household_day.isoformat()]

    def test_the_tile_reads_the_households_day_when_it_is_ahead(self, monkeypatch):
        """CATCH. Tokyo: the household's today is the server's tomorrow."""
        household_day = _ahead(monkeypatch)
        plan_id = _empty_plan(household_day)
        _defrost_row(plan_id, household_day)
        _defrost_row(plan_id, household_day - timedelta(days=1))

        rows = _defrost.get_defrost_today()

        assert [r["task_date"] for r in rows] == [household_day.isoformat()]

    def test_a_move_already_done_is_still_left_off(self, monkeypatch):
        """GUARD. Only pending rows; the status filter is untouched."""
        household_day = _behind(monkeypatch)
        plan_id = _empty_plan(household_day)
        _defrost_row(plan_id, household_day)
        conn = get_conn()
        conn.execute("UPDATE prep_tasks SET status = 'done'")
        conn.commit()
        conn.close()

        assert _defrost.get_defrost_today() == []


# ---------- 4. get_defrost_schedule ----------

class TestTheDefrostSchedule:
    def test_tonights_move_is_still_in_the_window(self, monkeypatch):
        """
        CATCH. "What do I need to defrost?" asked in the evening dropped
        the move due tonight — the one being asked about — off the near
        edge of its own window.
        """
        household_day = _behind(monkeypatch)
        plan_id = _empty_plan(household_day)
        _defrost_row(plan_id, household_day)

        rows = _defrost.get_defrost_schedule(7)

        assert [r["task_date"] for r in rows] == [household_day.isoformat()]

    def test_both_edges_of_the_window_move_with_the_household(self, monkeypatch):
        """
        CATCH. Seven days from the household's today, not from the
        server's: on the server's clock the far edge reached a day too far
        as well as losing a day at the near one.
        """
        household_day = _behind(monkeypatch)
        plan_id = _empty_plan(household_day)
        for offset in (0, 7, 8):
            _defrost_row(plan_id, household_day + timedelta(days=offset))

        rows = _defrost.get_defrost_schedule(7)

        assert [r["task_date"] for r in rows] == [
            household_day.isoformat(),
            (household_day + timedelta(days=7)).isoformat(),
        ]

    def test_the_window_reads_the_households_day_when_it_is_ahead(self, monkeypatch):
        """CATCH. Tokyo: the server's today is the household's yesterday,
        and a move that day has gone rather than being due."""
        household_day = _ahead(monkeypatch)
        plan_id = _empty_plan(household_day)
        _defrost_row(plan_id, household_day - timedelta(days=1))
        _defrost_row(plan_id, household_day)

        rows = _defrost.get_defrost_schedule(7)

        assert [r["task_date"] for r in rows] == [household_day.isoformat()]

    def test_the_clock_is_read_before_each_reads_connection(self, monkeypatch):
        """
        GUARD, pinned by MUTATION for the same reason as the writer's own,
        and erroring rather than failing against the unmodified app for the
        same reason too: already right there, and a second connection
        opened inside one of these is the hazard the ordering exists for.
        Milder here than in the writer — these two only read — but one rule
        for the module beats two.
        """
        for fn in (_defrost.get_defrost_today, _defrost.get_defrost_schedule):
            src = inspect.getsource(fn)
            assert src.index("_cooker.household_today()") < src.index("conn = get_conn()"), fn.__name__


# ---------- 5. the read left on the server's clock, on purpose ----------

def test_the_ask_marker_is_a_gate_and_not_a_day(monkeypatch):
    """
    GUARD. mark_defrost_asked writes SQLite's UTC datetime('now') and that
    is deliberately left alone: defrost_asked_at is only ever read as "is it
    set" (weekly_plan passes it through; shell.js asks
    `!data.defrost_asked_at`). Nothing compares it against a calendar day,
    so there is no day for it to be wrong about — and this pins that it is
    still a timestamp rather than quietly becoming a date somebody reasons
    with.
    """
    household_day = _behind(monkeypatch)
    plan_id = _empty_plan(household_day)

    _defrost.mark_defrost_asked(plan_id)

    conn = get_conn()
    row = conn.execute(
        "SELECT defrost_asked_at FROM weekly_plans WHERE id = ?", (plan_id,)
    ).fetchone()
    conn.close()
    assert row["defrost_asked_at"]
    # A full timestamp, not a bare date: nothing reads the day off it.
    assert len(row["defrost_asked_at"]) > len("2026-09-17")
