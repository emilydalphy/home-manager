"""
Whether a day is past is the SERVER's answer now, on the household's clock.

The screen used to work it out for itself — `classifyDay(day, todayStr)`
in static/shell.js, comparing the day against `todayLocalStr()`, i.e.
against the BROWSER's date. The server refuses a write into a night that
has gone by on `households.timezone` (`weekly_plan.night_has_gone`), and
that column is `America/Toronto` for every household whether they live
there or not, with nothing in the app prompting a change. So the two
disagreed for a few hours a night for any phone west of the stored zone.

Reproduced before anything was touched, on a throwaway database, with the
household's clock frozen at a Toronto evening and the "phone" a day behind
it (a Vancouver phone at 21:30, across the Toronto midnight):

    household today : 2026-09-20
    phone today     : 2026-09-19
    screen says     : isPast=False  isToday=True  status='Tonight'
    server says     : night_has_gone(2026-09-19) = True
    the Day step's Swap on that night ->
        {'status': 'refused', 'message': 'That night’s already gone.'}

So the screen drew "Tonight" over a night every one of its controls would
be refused on. After: `isPast=True  isToday=False  status='Served'`.

`is_past` and `is_today` move TOGETHER and that is not tidiness. They are
one line of arithmetic off one date, and a screen taking one from the
payload and the other from the phone would have said "Tonight" over a
night it had just greyed every control on — a new bug rather than a
smaller one, which is this repo's own rule about a half-converted screen.

Both clock directions are pinned, following
tests/test_weekly_plan_household_clock.py: Toronto 21:30 (the household a
day BEHIND the server — production's own direction) and Tokyo 08:30 (a day
AHEAD). Every test says in its own docstring whether it is a CATCH (red
against main) or a GUARD (green either way, pinned by a named mutation
that was actually run).

**Read the red-against-main count the honest way: 15 of the 25 are red
there, and only EIGHT of those are behaviour catches.** The other seven
die on something main has not got rather than on the claim they are named
for — six on `KeyError: 'is_past'` (the payload has no such key there) and
one on `AttributeError: _day_clock_flags`. Each of the seven says so in
its own docstring. The eight that genuinely fail on their own assertion
are the seven screen-side ones plus
`test_every_day_of_the_payload_carries_both_keys`, whose whole claim IS
the absence — and one of the eight,
`test_the_focus_target_carries_the_same_night_as_the_dish`, fails there on
an earlier assertion than the one it is named for and says so. Every GUARD
names the mutation that pins it, and all seventeen mutations listed across
these docstrings were run — each reddens at least one test.

One piece of test machinery exists for this accounting: `_tonight_furniture`
is built on demand rather than at import, because `tonightDinnerDay` is a
name this branch adds and extracting it at module scope would turn every
red-against-main measurement of this file into a single collection error.
"""
from __future__ import annotations

import json
import shutil
from datetime import date, datetime as dt, time, timedelta, timezone
from pathlib import Path

import nodeharness
import pytest

from app import tools
from app.db import get_conn
from app.tools import cooker as _cooker
from app.tools import weekly_plan as _wp


SERVER_TODAY = date.today()

# 01:30 UTC: Toronto is 21:30 the evening BEFORE, so the household is a
# whole day BEHIND the server. Production's own direction, every evening
# between 8pm and midnight Eastern.
UTC_EVENING = dt.combine(SERVER_TODAY, time(1, 30), tzinfo=timezone.utc)
# 23:30 UTC: Tokyo is 08:30 the morning AFTER — a day AHEAD, the same split
# read from the other side.
UTC_LATE = dt.combine(SERVER_TODAY, time(23, 30), tzinfo=timezone.utc)

TORONTO = "America/Toronto"
TOKYO = "Asia/Tokyo"

REPO = Path(__file__).resolve().parent.parent
SHELL_JS = (REPO / "static" / "shell.js").read_text(encoding="utf-8")


# ----------------------------------------------------------- the clock

def _set_timezone(name: str) -> None:
    conn = get_conn()
    conn.execute(
        "UPDATE households SET timezone = ? WHERE id = ?", (name, tools.household_id())
    )
    conn.commit()
    conn.close()


def _freeze(monkeypatch, instant: dt) -> None:
    """Every reading of the wall clock inside cooker answers `instant`.

    _household_today is a SHIFT applied to this module's own date.today(),
    so faking cooker's clock moves the household's day and leaves the
    server's alone — the zone lookup, the households.timezone read and the
    ZoneInfo fallback all still run for real.
    """

    class _Frozen(dt):
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


# ------------------------------------------------------------- the plan

def _plan(start: date, day_count: int = 7, component: bool = False) -> int:
    """A plan starting `start` with one dinner on each of its days."""
    plan_id = tools.create_weekly_plan(start.isoformat(), day_count=day_count)["weekly_plan_id"]
    for i in range(day_count):
        d = (start + timedelta(days=i)).isoformat()
        tools.plan_meal(d, "Bean Chili", slot="dinner", weekly_plan_id=plan_id)
    if component:
        conn = get_conn()
        conn.execute(
            "UPDATE weekly_plans SET planning_mode = 'component_based' WHERE id = ?", (plan_id,)
        )
        conn.commit()
        conn.close()
    return plan_id


def _flags(plan_id: int) -> dict[str, tuple[bool, bool]]:
    """{date: (is_past, is_today)} off the real payload."""
    menu = tools.get_week_menu(plan_id)
    return {d["date"]: (d["is_past"], d["is_today"]) for d in menu["days"]}


# =========================================================================
# The harness itself
# =========================================================================

class TestTheFreezeReallySplitsTheTwoClocks:
    def test_a_toronto_evening_puts_the_household_a_day_behind(self, monkeypatch):
        """GUARD on the harness, not on app behaviour. If the two clocks ever
        agreed here, every CATCH below would pass without testing anything."""
        household_today = _behind(monkeypatch)
        assert household_today != SERVER_TODAY
        assert _wp._household_today() == household_today

    def test_a_tokyo_morning_puts_the_household_a_day_ahead(self, monkeypatch):
        """GUARD, the other direction."""
        household_today = _ahead(monkeypatch)
        assert household_today != SERVER_TODAY
        assert _wp._household_today() == household_today


# =========================================================================
# The payload
# =========================================================================

class TestThePayloadSaysWhichDayIsPast:
    def test_a_household_behind_the_server_keeps_its_own_tonight(self, monkeypatch):
        """
        CATCH. The reported bug, in the production direction. The household's
        today is the server's yesterday, and the day the server has already
        rolled past must read as past — that is the night every write is
        refused on. (Red against main on `KeyError: 'is_past'` — the key is absent there — rather than on this assertion.)
        """
        household_today = _behind(monkeypatch)
        plan = _plan(household_today - timedelta(days=2))
        flags = _flags(plan)

        assert flags[household_today.isoformat()] == (False, True)
        assert flags[(household_today - timedelta(days=1)).isoformat()] == (True, False)
        assert flags[(household_today + timedelta(days=1)).isoformat()] == (False, False)
        # The server's own today is the household's TOMORROW here, and is
        # emphatically not past.
        assert flags[SERVER_TODAY.isoformat()] == (False, False)

    def test_a_household_ahead_of_the_server_has_already_lost_a_day(self, monkeypatch):
        """
        CATCH, the other direction. The server's today is the household's
        yesterday, so it must read as past — otherwise the screen offers
        controls on a night that is over where the household lives. (Red against main on `KeyError: 'is_past'` — the key is absent there — rather than on this assertion.)
        """
        household_today = _ahead(monkeypatch)
        plan = _plan(household_today - timedelta(days=2))
        flags = _flags(plan)

        assert flags[household_today.isoformat()] == (False, True)
        assert flags[SERVER_TODAY.isoformat()] == (True, False)

    def test_the_flag_agrees_with_the_write_that_refuses_the_night(self, monkeypatch):
        """
        CATCH, and the one that says what this is FOR. Every day the payload
        calls past is a day night_has_gone refuses, and every day it does not
        is a day night_has_gone allows. The screen and the server stop
        disagreeing by construction rather than by each doing its own
        arithmetic. (Red against main on `KeyError: 'is_past'` — the key is absent there — rather than on this assertion.)
        """
        household_today = _behind(monkeypatch)
        plan = _plan(household_today - timedelta(days=3))
        for day, (is_past, _is_today) in _flags(plan).items():
            assert is_past == _wp.night_has_gone(day), day

    def test_today_itself_is_never_past(self, monkeypatch):
        """
        CATCH. Strictly BEFORE, matching night_has_gone: changing tonight's
        dinner is the most ordinary thing anybody does here, and a flag that
        greyed it would be the same bug wearing the other hat. (Red against main on `KeyError: 'is_past'` — the key is absent there — rather than on this assertion.)
        """
        household_today = _behind(monkeypatch)
        plan = _plan(household_today)
        flags = _flags(plan)
        assert flags[household_today.isoformat()] == (False, True)

    def test_a_component_based_plan_carries_the_flags_too(self, monkeypatch):
        """
        CATCH. A component plan has no real per-day assignment, but it draws
        the same day cards off the same payload — so a key that exists on one
        branch and not the other is a screen that reads `undefined` and falls
        back to the phone for half its households. (Red against main on `KeyError: 'is_past'` — the key is absent there — rather than on this assertion.)
        """
        household_today = _behind(monkeypatch)
        plan = _plan(household_today - timedelta(days=2), component=True)
        menu = tools.get_week_menu(plan)
        assert menu.get("menu_is_suggested") is True
        flags = {d["date"]: (d["is_past"], d["is_today"]) for d in menu["days"]}
        assert flags[household_today.isoformat()] == (False, True)
        assert flags[(household_today - timedelta(days=1)).isoformat()] == (True, False)

    def test_every_day_of_the_payload_carries_both_keys(self, monkeypatch):
        """CATCH. A missing key is the fallback firing silently."""
        household_today = _behind(monkeypatch)
        plan = _plan(household_today - timedelta(days=2))
        for d in tools.get_week_menu(plan)["days"]:
            assert isinstance(d.get("is_past"), bool), d["date"]
            assert isinstance(d.get("is_today"), bool), d["date"]

    def test_before_plan_start_is_untouched(self, monkeypatch):
        """
        GUARD on what did NOT move. The flag this one sits beside answers a
        different question — "no rows were ever written for this day" — and a
        part-week's earlier days are both before_plan_start AND (usually)
        past, while the day that IS today is before_plan_start and not past.
        Pinned by the mutation that makes is_past read content_start instead
        of the clock. (Red against main on `KeyError: 'is_past'` — the key is
        absent there — rather than on this assertion, so it is not a
        behaviour catch.)
        """
        household_today = _behind(monkeypatch)
        # A real part-week: filed three days before its content starts, so
        # _menu_dates carries the filing days ahead of it. Counted off the
        # HOUSEHOLD's today rather than a weekday, so the shape is the same
        # on every one of CI's weekday pins: two filing days already gone,
        # one that is today, and the content starting tomorrow.
        filed = household_today - timedelta(days=2)
        content = household_today + timedelta(days=1)
        plan = _plan(filed, day_count=7)
        conn = get_conn()
        conn.execute(
            "UPDATE weekly_plans SET week_start_date = ?, content_start_date = ?, day_count = ? "
            "WHERE id = ?",
            (filed.isoformat(), content.isoformat(), 4, plan),
        )
        conn.commit()
        conn.close()

        menu = tools.get_week_menu(plan)
        before = [d for d in menu["days"] if d["before_plan_start"]]
        assert before, "expected a part-week with filing days ahead of its content"
        # A day before the plan's content but still AHEAD of the household is
        # not past — the two flags are answering different questions.
        assert any(d["before_plan_start"] and not d["is_past"] for d in menu["days"])
        assert any(d["before_plan_start"] and d["is_past"] for d in menu["days"])


# =========================================================================
# The clock is read once, before any connection, whatever the day count
# =========================================================================

class TestWhatItCosts:
    def _count_clock_reads(self, monkeypatch, plan_id: int) -> int:
        n = {"reads": 0}
        real = _cooker.household_now

        def counted(*a, **k):
            n["reads"] += 1
            return real(*a, **k)

        monkeypatch.setattr(_cooker, "household_now", counted)
        tools.get_week_menu(plan_id)
        return n["reads"]

    def test_a_longer_plan_costs_no_more_clock_reads(self, monkeypatch):
        """
        GUARD, green either way — and the one that says the flags may never
        become per-day work. _day_clock_flags takes the caller's one reading
        of _household_today rather than asking for itself, so a fourteen-day
        plan reads the clock exactly as often as a seven-day one. Pinned by
        the mutation that makes _day_clock_flags call _household_today()
        itself: that takes a 7-day payload from 1 read to 8 and reddens this.
        """
        _behind(monkeypatch)
        seven = self._count_clock_reads(monkeypatch, _plan(SERVER_TODAY, 7))
        fourteen = self._count_clock_reads(monkeypatch, _plan(SERVER_TODAY + timedelta(days=30), 14))
        assert seven == fourteen == 1

    def test_the_clock_is_read_before_the_first_connection(self, monkeypatch):
        """
        GUARD, green either way. _household_today opens a connection of its
        own (it reaches cooker.household_now), and a nested get_conn inside
        an open one has twice cost this repo an intermittent "database is
        locked" — the kind of failure no test sees until production. So the
        one read has to come before get_week_menu opens anything. Pinned by
        the mutation that moves the `today_str = ...` line below the first
        `conn = get_conn()`, which reddens this.

        **It counts `sqlite3.connect`, not `weekly_plan.get_conn`, and that
        is the whole reliability of it.** Patching a module's own pre-bound
        `get_conn` name cannot see a function-local `from ..db import
        get_conn` — which is a real shape in this package (`_shared.py`
        does exactly that, and the 2026-09-11 approve-race work found a
        stray connection through it that a module-level patch had missed).
        Counting at the driver is the one instrument nothing can slip past.
        """
        import sqlite3

        _behind(monkeypatch)
        plan = _plan(SERVER_TODAY)
        seen = {"conns": 0, "at_clock": None}
        real_connect = sqlite3.connect
        real_now = _cooker.household_now

        def counted_connect(*a, **k):
            seen["conns"] += 1
            return real_connect(*a, **k)

        def counted_now(*a, **k):
            if seen["at_clock"] is None:
                seen["at_clock"] = seen["conns"]
            return real_now(*a, **k)

        monkeypatch.setattr(sqlite3, "connect", counted_connect)
        monkeypatch.setattr(_cooker, "household_now", counted_now)
        tools.get_week_menu(plan)
        assert seen["at_clock"] == 0, "the household's clock was read after a connection was open"
        assert seen["conns"] > 0, "nothing was counted — the instrument is not on the path"

    def test_the_flag_builder_reads_no_clock_of_its_own(self, monkeypatch):
        """
        Red against main, but NOT a behaviour catch — it dies on
        `AttributeError: _day_clock_flags`, a name main has not got. What
        it really pins is a mutation: _day_clock_flags is pure arithmetic
        on the day and the caller's `today_str`, and any version that asks
        a clock for itself fails here (measured: that mutation also takes a
        7-day payload from 1 clock read to 8).
        """
        def boom(*a, **k):
            raise AssertionError("_day_clock_flags read a clock")

        monkeypatch.setattr(_cooker, "household_now", boom)
        assert _wp._day_clock_flags("2026-09-19", "2026-09-20") == {"is_past": True, "is_today": False}
        assert _wp._day_clock_flags("2026-09-20", "2026-09-20") == {"is_past": False, "is_today": True}
        assert _wp._day_clock_flags("2026-09-21", "2026-09-20") == {"is_past": False, "is_today": False}


# =========================================================================
# The screen's own functions, run
# =========================================================================

_needs_node = pytest.mark.skipif(
    shutil.which("node") is None, reason="node is needed to execute the screen's own functions"
)


def _extract(name: str) -> str:
    start = SHELL_JS.index(f"function {name}(")
    i = SHELL_JS.index("{", start)
    depth, j = 0, i
    while True:
        if SHELL_JS[j] == "{":
            depth += 1
        elif SHELL_JS[j] == "}":
            depth -= 1
            if depth == 0:
                break
        j += 1
    return SHELL_JS[start : j + 1]


def _run_node(harness: str):
    res = nodeharness.run_node(harness, timeout=30)
    assert res.returncode == 0, f"node failed: {res.stderr}"
    return json.loads(res.stdout.strip())


def _classify(day: dict, phone_today: str):
    harness = (
        "var WEEK_SLOTS = ['breakfast', 'lunch', 'dinner'];\n"
        + _extract("classifyDay") + "\n"
        + "console.log(JSON.stringify(classifyDay("
        + json.dumps(day) + ", " + json.dumps(phone_today) + ")));\n"
    )
    return _run_node(harness)


def _day(date_str, **kw):
    day = {"date": date_str, "before_plan_start": False,
           "breakfast": None, "lunch": None, "dinner": None}
    day.update(kw)
    return day


@_needs_node
def test_the_screen_takes_past_from_the_payload_not_from_the_phone():
    """
    CATCH, and the reproduction. A phone in Vancouver at 21:30 reads its
    own tonight as today while the server, on America/Toronto, has already
    rolled over and refuses every change to it. Before: isPast False,
    isToday True, "Tonight". After: the payload's answer.
    """
    out = _classify(_day("2026-09-19", is_past=True, is_today=False), "2026-09-19")
    assert out["isPast"] is True
    assert out["isToday"] is False
    assert out["status"] == "Served"


@_needs_node
def test_the_screen_takes_today_from_the_payload_too():
    """
    CATCH. The other half of the same line. A phone EAST of the stored zone
    reads the household's today as tomorrow; the payload says which day is
    really tonight.
    """
    out = _classify(_day("2026-09-20", is_past=False, is_today=True), "2026-09-21")
    assert out["isToday"] is True
    assert out["isPast"] is False
    assert out["status"] == "Tonight"
    assert out["ribbon"] == "today"


@_needs_node
def test_both_flags_come_from_one_source_or_neither():
    """
    CATCH against the half-conversion. A day that is past on the household's
    clock must never also read as today — that pairing is what would put
    "Tonight" over a night whose every control had just been greyed. Pinned
    against the mutation that takes is_past from the payload and is_today
    from the phone: with these arguments it gives isPast True AND isToday
    True, which this forbids.
    """
    out = _classify(_day("2026-09-19", is_past=True, is_today=False), "2026-09-19")
    assert not (out["isPast"] and out["isToday"])
    assert out["status"] == "Served"


@_needs_node
def test_a_day_with_no_flags_still_falls_back_to_the_phone():
    """
    GUARD, green either way. A day this screen is holding from before the
    flags existed. A wrong hour beats a screen that thinks no day has ever
    gone by — the same bargain _household_today makes with an unreadable
    timezone. Pinned by the mutation that drops the fallback and reads
    day.is_past outright, which makes both of these read false.
    """
    assert _classify(_day("2026-09-19"), "2026-09-20")["isPast"] is True
    assert _classify(_day("2026-09-20"), "2026-09-20")["isToday"] is True


@_needs_node
def test_a_past_day_is_not_a_decision_waiting():
    """
    CATCH — and it was labelled a guard on the first pass, which was wrong
    and is the labelling error this repo keeps having to unpick.
    needsDecision has always been gated on isPast, so moving isPast moves
    it: on main this day reads as today, every slot is empty, and the
    screen offers "Pick" and an urgent ribbon on a night that is over.
    Measured red there on `needsDecision is False`.
    """
    out = _classify(_day("2026-09-19", is_past=True, is_today=False), "2026-09-19")
    assert out["needsDecision"] is False
    assert out["ribbon"] == ""


# -------------------------------------------------- the control that greys

_ACTS_FURNITURE = (
    "var SWAP_LABEL = 'Swap \\u00b7 I\\u2019ll pick';\n"
    "var REHEAT_ACTION_LABEL = 'Mark eaten';\n"
    + _extract("escapeHtml") + "\n"
    + "function isSnackSlot(s){ return s === 'snack'; }\n"
    + "function daySlotEntry(d, s){ return d[s]; }\n"
    + "function swapLineHtml(){ return '<div class=\"wk-swap-line\"></div>'; }\n"
    + "function isRealCook(){ return true; }\n"
    + "function cookTimeChip(){ return ''; }\n"
    + "function planCookableNow(){ return true; }\n"
    + _extract("slotActionsHtml") + "\n"
)


def _acts(day: dict, slot: str = "dinner"):
    harness = (
        _ACTS_FURNITURE
        + "var html = slotActionsHtml(" + json.dumps(day) + ", " + json.dumps(slot) + ", true);\n"
        + "console.log(JSON.stringify({ html: html,"
          " swap: html.indexOf('data-wk-swap=') !== -1,"
          " pick: html.indexOf('data-wk-pick=') !== -1,"
          " line: html.indexOf('wk-swap-line') !== -1 }));\n"
    )
    return _run_node(harness)


_OPEN = {"title": "Your call", "state": "open", "source": "open", "entry_id": 9}


@_needs_node
def test_an_open_night_that_has_gone_no_longer_offers_swap():
    """
    CATCH. The one control on this screen still offered on a night whose
    only possible answer is a refusal: the `open` branch carried no isPast
    term at all, while `planned` and `planned_empty` either side of it both
    stood down. Reproduced — the screen offered Swap and
    swap_meal_in_place answered "That night's already gone."
    """
    out = _acts({"date": "2026-09-17", "isPast": True, "dinner": _OPEN})
    assert out["swap"] is False
    assert out["line"] is False, "the swap line rides with the button, per its own rule"


@_needs_node
def test_that_night_still_offers_pick():
    """
    GUARD, green either way, and the deliberate boundary. resolve_open_slot
    is NOT refused on a past night — whether settling an old question is
    wrong at all is a product question, still open — so Pick is a tap that
    does something and stays. Pinned by the mutation that greys the whole
    branch, which reddens this.
    """
    out = _acts({"date": "2026-09-17", "isPast": True, "dinner": _OPEN})
    assert out["pick"] is True


@_needs_node
def test_an_open_night_still_ahead_keeps_its_swap():
    """
    GUARD, green either way. The narrowing is the past and nothing else.
    Pinned by the mutation that drops swap from the open branch outright.
    """
    out = _acts({"date": "2026-09-25", "isPast": False, "dinner": _OPEN})
    assert out["swap"] is True
    assert out["pick"] is True
    assert out["line"] is True


@_needs_node
def test_the_other_two_branches_are_unchanged_on_a_past_night():
    """GUARD, green either way — what did NOT move. The `planned` and
    `planned_empty` branches either side of the one that changed already
    stood down on a past night, and still do. Pinned by the mutation that
    takes `if (day.isPast) return ''` off the planned branch, which
    reddens this and nothing else."""
    planned = {"title": "Bean Chili", "state": "planned", "source": "plan", "entry_id": 1}
    empty = {"title": "Out — nothing to cook", "state": "planned_empty", "entry_id": 2}
    assert _acts({"date": "2026-09-17", "isPast": True, "dinner": planned})["html"] == ""
    assert _acts({"date": "2026-09-17", "isPast": True, "dinner": empty})["html"] == ""


# ------------------------------------------- which day the app calls tonight

def _tonight_furniture() -> str:
    """Built on demand, not at import: `tonightDinnerDay` is a name this
    branch adds, and extracting it at module scope would turn every
    red-against-main measurement of this file into one collection error
    instead of the per-test accounting the header gives."""
    return (
        _extract("todayLocalStr") + "\n"
        + "function isSnackSlot(s){ return s === 'snack'; }\n"
        + _extract("recipeTargetForEntry") + "\n"
        # main has no tonightDinnerDay; there the two readers below carry
        # their own clock reads and this simply contributes nothing.
        + (_extract("tonightDinnerDay") + "\n" if "function tonightDinnerDay(" in SHELL_JS else "")
        + _extract("tonightDinnerEntry") + "\n"
        + _extract("tonightDinnerRecipeTarget") + "\n"
    )


def _tonight(days):
    harness = (
        "var weekState = { data: { days: " + json.dumps(days) + " } };\n"
        + _tonight_furniture()
        + "var e = tonightDinnerEntry();\n"
        + "console.log(JSON.stringify({ title: e ? e.title : null,"
          " target: tonightDinnerRecipeTarget() }));\n"
    )
    return _run_node(harness)


@_needs_node
def test_tonights_dinner_is_the_one_the_payload_calls_today():
    """
    CATCH. tonightDinnerEntry finds today's day in the Plan payload and it
    was finding it with the phone's date — so in the same window the
    shop-done handoff on the Grocery tab ("That's the shopping done.
    Tonight it's X.") named LAST night's dinner. It reads the payload's own
    is_today now; the day it reads is the day every other part of this
    screen has already agreed is tonight.

    Also pinned by the mutation that has tonightDinnerEntry read the clock
    for itself instead of asking tonightDinnerDay — the same
    half-conversion from the other side — which reddens this and the focus
    target's test.
    """
    days = [
        {"date": "2026-09-19", "is_today": False, "is_past": True,
         "dinner": {"title": "Yesterday’s Chili", "state": "planned"}},
        {"date": "2026-09-20", "is_today": True, "is_past": False,
         "dinner": {"title": "Chicken Tacos", "state": "planned"}},
    ]
    # The phone is a day behind, so the old comparison lands on 09-19.
    assert _tonight(days)["title"] == "Chicken Tacos"


@_needs_node
def test_the_focus_target_carries_the_same_night_as_the_dish():
    """
    CATCH against this branch's own first cut, which is the point of it.
    Moving tonightDinnerEntry onto the household's clock and leaving
    `recipeTargetForEntry(entry, todayLocalStr(), 'dinner')` beside it put
    the household's DISH under the phone's DATE — the exact pairing
    _day_clock_flags' docstring forbids, one function down, introduced by
    the fix for it. Inert today only because cookResolveFocusIndex tries
    entryId first; the date+slot fallback under it would go looking for a
    meal on the wrong night. Pinned by the mutation that puts
    `todayLocalStr()` back in that argument, which reddens this and
    nothing else — on `target['date']`, the assertion it is named for.
    (Against main it is red one assertion EARLIER, on `title`: main's
    tonightDinnerEntry looks for the node process's own today and finds
    neither seeded day at all. Same root, a different symptom, so the
    mutation is what pins the claim.)
    """
    days = [
        {"date": "2026-09-19", "is_today": False, "is_past": True,
         "dinner": {"title": "Yesterday’s Chili", "state": "planned", "entry_id": 1}},
        {"date": "2026-09-20", "is_today": True, "is_past": False,
         "dinner": {"title": "Chicken Tacos", "state": "planned", "entry_id": 2}},
    ]
    out = _tonight(days)
    assert out["title"] == "Chicken Tacos"
    # The dish and the date it is filed under name ONE night.
    assert out["target"]["date"] == "2026-09-20"
    assert out["target"]["entryId"] == 2


@_needs_node
def test_tonights_dinner_still_falls_back_to_the_phone_without_the_flag():
    """GUARD, green either way — the same fallback classifyDay keeps.
    Pinned by the mutation that reads `d.is_today` outright, which reddens
    this and nothing else."""
    today = date.today().isoformat()
    days = [{"date": today, "dinner": {"title": "Bean Chili", "state": "planned"}}]
    assert _tonight(days)["title"] == "Bean Chili"


# ------------------------------------------------- the derivation is one place

def test_the_screen_derives_past_in_exactly_one_place():
    """
    GUARD, green either way, and the reason this bug existed at all: one
    selector, in one function, is what made it fixable in one line. If a
    second `date < todayStr` ever appears on this screen, the next clock
    fix has two places to find. Pinned by the mutation that adds one,
    which reddens this and nothing else.
    """
    assert SHELL_JS.count("day.date < todayStr") == 1
