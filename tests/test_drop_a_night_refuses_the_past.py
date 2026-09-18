"""
Drop-a-night refuses a night that has already gone by.

`drop_dish_from_day` — the Review stepper's "−" — took any night of the
dish, past ones included. Unlike its sibling the "+", whose picker at least
filters past days out on the client, NOTHING kept them off this control:
"−" simply takes the LAST day the dish covers, so a dish whose days are all
behind today has a live, enabled button that hands one over. Reproduced
before the fix, over the tool and over the route: yesterday's uncooked
dinner went straight through, its grocery contribution was reversed — for
food that was in all likelihood already bought — and the night came back as
an `open` question reading "You cut Bean Chili back, so this one is yours
to fill", a decision handed back on a day nobody can act on.

This is the exact sibling of `tests/test_add_a_night_refuses_the_past.py`,
which closed the same hole in `add_dish_day` on 2026-09-16. The "−" was
deliberately left alone then so that branch stayed the size of its ticket.
Everything that file says about the clock holds here word for word: the
container runs UTC and households default to America/Toronto, so between
8pm local and midnight the server's date is already tomorrow, and a refusal
on the SERVER's date would refuse TONIGHT for four hours every evening —
worse than the bug it fixes. So the check reads
`weekly_plan._household_today()`, and both directions are pinned here the
way `tests/test_weekly_plan_household_clock.py` pins them: `cooker.datetime`
frozen at one UTC instant with `households.timezone` set, so the household's
day genuinely moves while the server's does not. The zone lookup and the
column read both run for real.

And today itself is never refused. Only a night strictly BEFORE the
household's today is — taking tonight's dinner off the week is an ordinary
thing to ask for, and a check that took it away would be the same bug
wearing the other hat.

Each test says in its own docstring whether it is a CATCH (red on the
unmodified app, on the assertion it is named for — the three that read
`out["message"]` assert `status` first, so they fail on the claim rather
than on a KeyError a dropped result has no reason to avoid) or a
NO-REGRESSION GUARD (green either way, here to say
what did not change). The guards are pinned by MUTATION instead, and five
were run: swapping `_household_today()` for the server's `date.today()`,
`<` for `<=`, moving the new refusal above the cooked one, moving the clock
read inside this function's write transaction, and repointing this file's
own `_day` at the process's clock.

That last one is the trap the sibling file fell into once already.
Everything UNFROZEN counts its days off `conftest.household_today()`, never
off `date.today()`: the two are different days for part of every UTC day,
in both directions, so a file seeded on the process's clock that then asks
the app about "today" is asserting the two agree. `_server_day` exists for
the frozen tests alone, where naming a day in the process's own terms is
the entire point.
"""
from __future__ import annotations

import json
from datetime import date, datetime, time, timedelta, timezone

import pytest

from conftest import household_today

from app import tools
from app.db import get_conn
from app.tools import cooker as _cooker
from app.tools import weekly_plan as _wp


SERVER_TODAY = date.today()
# Today where the seeded household lives — the clock the refusal reads, and
# so the only honest anchor for the unfrozen tests below. See the module
# docstring: the process's own date is a different day from the household's
# for part of every UTC day, in BOTH directions. The frozen tests keep the
# SERVER anchor on purpose, because the gap between the two clocks is the
# thing they are about.
HOUSEHOLD_TODAY = household_today()

# 01:30 UTC is 21:30 the evening BEFORE in Toronto, so the household is a
# whole day BEHIND the server. That is the production direction, and every
# evening between 8pm and midnight Eastern.
UTC_EVENING = datetime.combine(SERVER_TODAY, time(1, 30), tzinfo=timezone.utc)
# 23:30 UTC is 08:30 the morning AFTER in Tokyo — the household a day
# AHEAD, the same split read from the other side.
UTC_LATE = datetime.combine(SERVER_TODAY, time(23, 30), tzinfo=timezone.utc)

TORONTO = "America/Toronto"
TOKYO = "Asia/Tokyo"

# Wider than any one test needs, and starting four days back from whichever
# anchor is earlier, so a past night is inside the plan's period whichever
# clock named it and `plan_meal`'s own period guard is never what refuses
# anything here.
PLAN_START = min(SERVER_TODAY, HOUSEHOLD_TODAY) - timedelta(days=4)
PLAN_DAYS = 11

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
    """A day of the plan, counted off the HOUSEHOLD's today — the clock the
    refusal reads. Everything unfrozen uses this."""
    return (HOUSEHOLD_TODAY + timedelta(days=offset)).isoformat()


def _server_day(offset: int) -> str:
    """A day counted off the SERVER's today. Only the frozen tests use it,
    and only because naming a day in the process's own terms is exactly what
    they are for."""
    return (SERVER_TODAY + timedelta(days=offset)).isoformat()


def _plan() -> int:
    return tools.create_weekly_plan(
        PLAN_START.isoformat(),
        content_start_date=PLAN_START.isoformat(),
        day_count=PLAN_DAYS,
    )["weekly_plan_id"]


def _recipe() -> None:
    tools.add_recipe(
        "Bean Chili",
        [
            {"item": "Black beans", "qty": "2 cans", "category": "pantry"},
            {"item": "Onion", "qty": "1", "category": "produce"},
        ],
        tags=["dinner"],
        instructions=["Simmer everything."],
    )


def _seed(*days: str, approve: bool = False) -> int:
    """Bean Chili on each of `days`, one plan. Approving is what puts its
    ingredients on the shopping list, which is half of what going through
    used to destroy."""
    plan = _plan()
    _recipe()
    for day in days:
        tools.plan_meal(day, "Bean Chili", slot="dinner", weekly_plan_id=plan)
    if approve:
        tools.approve_weekly_plan(plan)
    return plan


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
        "SELECT mpe.slot_state, mpe.open_reason, COALESCE(r.name, mpe.freeform_meal) AS meal "
        "FROM meal_plan_entries mpe LEFT JOIN recipes r ON r.id = mpe.recipe_id "
        "WHERE mpe.household_id = ? AND mpe.date = ? AND mpe.slot = ? ORDER BY mpe.id ASC",
        (tools.household_id(), day, slot),
    ).fetchall()
    conn.close()
    # An empty open_reason and no open_reason are the same thing to every
    # screen that reads one, and plan_meal writes '' where plan_slot_open
    # would leave NULL — so they are one value here.
    return [(r["slot_state"], r["meal"], r["open_reason"] or None) for r in rows]


def _list() -> list[tuple]:
    """The shopping list as rows, plus the per-meal ledger behind it — the
    reversal touches both, and only the ledger shows a night's share going."""
    conn = get_conn()
    items = conn.execute(
        "SELECT item, quantity, status FROM grocery_items WHERE household_id = ? "
        "ORDER BY item", (tools.household_id(),),
    ).fetchall()
    links = conn.execute(
        "SELECT COUNT(*) AS n FROM meal_plan_grocery_links WHERE household_id = ?",
        (tools.household_id(),),
    ).fetchone()["n"]
    conn.close()
    return [(r["item"], r["quantity"], r["status"]) for r in items] + [("ledger", links, "")]


def _mark_cooked(day: str) -> None:
    conn = get_conn()
    conn.execute(
        "UPDATE meal_plan_entries SET cooked_status = 'done' "
        "WHERE household_id = ? AND date = ? AND slot = 'dinner'",
        (tools.household_id(), day),
    )
    conn.commit()
    conn.close()


def _feeds(source_day: str, fed_day: str, slot: str = "dinner") -> None:
    """Make `source_day`'s dinner a leftover-chain SOURCE cooked double for
    `fed_day` — the source side of the pairing, which is what
    chain_fed_nights reads."""
    conn = get_conn()
    conn.execute(
        "UPDATE meal_plan_entries SET derived_from_json = ? "
        "WHERE household_id = ? AND date = ? AND slot = 'dinner'",
        (json.dumps({"make_double_for": [f"{fed_day}:{slot}"]}),
         tools.household_id(), source_day),
    )
    conn.commit()
    conn.close()


# -------------------------------------------- the harness's own two clocks

class TestTheFreezeReallySplitsTheTwoClocks:
    def test_a_toronto_evening_puts_the_household_a_day_behind_the_server(self, monkeypatch):
        """GUARD on the harness, not on the app. If the two clocks ever
        agree here, every frozen test below passes without testing
        anything."""
        day = _behind(monkeypatch)
        assert day != SERVER_TODAY
        assert _wp._household_today() == day

    def test_a_tokyo_morning_puts_the_household_a_day_ahead(self, monkeypatch):
        """GUARD, the other direction."""
        day = _ahead(monkeypatch)
        assert day != SERVER_TODAY
        assert _wp._household_today() == day

    def test_the_unfrozen_tests_count_their_days_off_the_households_clock(self):
        """
        GUARD on the anchor every unfrozen test below rests on, and the one
        that stops this file going red on a runner in another timezone.
        `_day(0)` must be the day the APP will call today, not the day the
        process does.

        Trivially true when the two clocks happen to agree and a real
        assertion whenever they do not, which is exactly when it matters.
        Pinned by mutation: repointing `_day` at `SERVER_TODAY` reddens this
        plus the tests in TestTodayItselfIsNeverRefused that name today.
        """
        assert _day(0) == _wp._household_today().isoformat()


# ------------------------------------------------ 1. the reported bug

class TestANightThatHasGoneBy:
    def test_yesterdays_dinner_is_refused(self):
        """CATCH. The reported bug: the stepper's "−" takes the last day
        the dish covers, and on a week reviewed after some of it has gone
        by that day is one that is over."""
        plan = _seed(_day(-1), _day(1))

        out = tools.drop_dish_from_day(plan, _ids(_day(-1))[0])

        assert out["status"] == "refused"
        assert out["message"] == REFUSAL

    def test_the_night_is_left_exactly_as_it_was(self):
        """CATCH. Harm two of the two: going through handed the night back
        as an `open` question — a decision returned on a day nobody can act
        on. The row is still the planned dinner it was."""
        plan = _seed(_day(-1), _day(1))
        before = _state(_day(-1))

        tools.drop_dish_from_day(plan, _ids(_day(-1))[0])

        assert _state(_day(-1)) == before == [("planned", "Bean Chili", None)]
        assert _state(_day(1)) == [("planned", "Bean Chili", None)]

    def test_the_shopping_list_is_untouched(self):
        """CATCH. Harm one of the two, and the one that costs money: the
        reversal took that night's share off an APPROVED week's list, for
        food already bought. Measured before the fix — Black beans went 4
        cans to 2, Onion 2 to 1, and the ledger lost the night's rows."""
        plan = _seed(_day(-1), _day(1), approve=True)
        before = _list()
        assert ("Black beans", "4 cans", "needed") in before

        tools.drop_dish_from_day(plan, _ids(_day(-1))[0])

        assert _list() == before

    def test_a_night_days_back_is_refused_too(self):
        """CATCH. Not just yesterday — anything behind today."""
        plan = _seed(_day(-3), _day(1))

        out = tools.drop_dish_from_day(plan, _ids(_day(-3))[0])

        # status first, so an app that DROPS fails on the claim this test
        # is named for rather than on a KeyError reading a key a dropped
        # result has no reason to carry.
        assert out["status"] == "refused"
        assert out["message"] == REFUSAL

    def test_the_refusal_is_the_same_shape_the_other_two_answer_in(self):
        """
        CATCH. `status: 'refused'` at 200 with a sentence, carrying the day
        and the dish the way the cooked and chain-source refusals above it
        do — not a raise, which the route turns into a 404 and the screen
        prints as its generic "that didn't work" line. One branch in
        runDropDishDay serves all three.
        """
        plan = _seed(_day(-1), _day(1))

        out = tools.drop_dish_from_day(plan, _ids(_day(-1))[0])

        assert out == {
            "status": "refused",
            "date": _day(-1),
            "slot": "dinner",
            "dish": "Bean Chili",
            "message": REFUSAL,
        }


# --------------------------------------- 2. today itself is never refused

class TestTodayItselfIsNeverRefused:
    def test_tonights_dinner_still_comes_off_the_week(self):
        """
        GUARD. Taking tonight's dinner off is an ordinary thing to ask for,
        and a check that refused it would be the same bug wearing the other
        hat. Green either way — the unmodified app has no check at all — so
        it is pinned by MUTATION: `<=` for `<` reddens it.
        """
        plan = _seed(_day(0), _day(1))

        out = tools.drop_dish_from_day(plan, _ids(_day(0))[0])

        assert out["status"] == "dropped"

    def test_and_really_hands_the_night_back(self):
        """GUARD, same mutation. The drop still does its whole job: the
        night comes back as a question in the household's own words."""
        plan = _seed(_day(0), _day(1))

        tools.drop_dish_from_day(plan, _ids(_day(0))[0])

        assert _state(_day(0)) == [
            ("open", None, "You cut Bean Chili back, so this one is yours to fill.")
        ]

    def test_a_night_still_ahead_is_untouched_by_this(self):
        """GUARD. Tomorrow was never in question; here so a check written
        the wrong way round shows up as more than one red test."""
        plan = _seed(_day(1), _day(2))

        out = tools.drop_dish_from_day(plan, _ids(_day(2))[0])

        assert out["status"] == "dropped"


# ------------------------------------------------ 3. which refusal wins

class TestWhichRefusalWins:
    def test_a_past_night_somebody_cooked_still_says_it_was_cooked(self):
        """
        GUARD, and the reason the new check sits BELOW the cooked one. Both
        sentences are true of a past night that was cooked; "already been
        cooked" is the more specific of the two and says why the record is
        being kept, so it keeps its place. Green either way — that refusal
        already existed — and pinned by MUTATION: moving the new check above
        the cooked one reddens it.
        """
        plan = _seed(_day(-1), _day(1))
        _mark_cooked(_day(-1))

        out = tools.drop_dish_from_day(plan, _ids(_day(-1))[0])

        assert out["status"] == "refused"
        assert "already been cooked" in out["message"]

    def test_a_past_chain_source_says_the_night_is_gone_not_change_that_first(self):
        """
        CATCH, and the reason the new check sits ABOVE the chain one. The
        chain refusal names a remedy — "change that first and I'll take this
        one off" — and on a night that is already over that remedy cannot
        work, so it must never be the answer a past night gets.
        """
        plan = _seed(_day(-2), _day(-1))
        _feeds(_day(-2), _day(-1))

        out = tools.drop_dish_from_day(plan, _ids(_day(-2))[0])

        assert out["message"] == REFUSAL
        assert "change that first" not in out["message"]

    def test_a_live_chain_source_still_names_the_night_that_depends_on_it(self):
        """GUARD. The chain refusal is unchanged for every night it was
        ever the right answer for. Green either way; pinned by the mutation
        above, which reddens it by answering REFUSAL here instead."""
        plan = _seed(_day(1), _day(2))
        _feeds(_day(1), _day(2))

        out = tools.drop_dish_from_day(plan, _ids(_day(1))[0])

        assert out["status"] == "refused"
        assert "change that first" in out["message"]


# ------------------------------- 4. the household's clock, not the server's

class TestTheClockIsTheHouseholds:
    """
    Both directions, frozen at one UTC instant. The server's date does not
    move; the household's does, because `households.timezone` and the zone
    lookup both run for real.
    """

    def test_a_household_a_day_behind_can_still_drop_its_own_tonight(self, monkeypatch):
        """
        GUARD, and the one the whole clock argument rests on. Toronto
        21:30: the server is already on tomorrow, so the household's own
        tonight is the server's YESTERDAY. On the server's clock this would
        be refused — every evening between 8pm and midnight Eastern, which
        is worse than the bug being fixed. Green on the unmodified app
        (no check at all), so pinned by MUTATION: `date.today()` for
        `_household_today()` reddens it.
        """
        day = _behind(monkeypatch)
        plan = _seed(day.isoformat(), (day + timedelta(days=1)).isoformat())

        out = tools.drop_dish_from_day(plan, _ids(day.isoformat())[0])

        assert day.isoformat() == _server_day(-1)
        assert out["status"] == "dropped"

    def test_a_household_a_day_behind_is_still_refused_its_own_yesterday(self, monkeypatch):
        """CATCH. The check is not simply switched off for them — the night
        before their own today is still gone."""
        day = _behind(monkeypatch)
        past = (day - timedelta(days=1)).isoformat()
        plan = _seed(past, (day + timedelta(days=1)).isoformat())

        out = tools.drop_dish_from_day(plan, _ids(past)[0])

        assert out["status"] == "refused"
        assert out["message"] == REFUSAL

    def test_a_household_a_day_ahead_is_refused_the_servers_today(self, monkeypatch):
        """
        CATCH, and the one test here that discriminates the clock in the
        other direction. Tokyo 08:30: the household is on tomorrow, so the
        server's today is their yesterday and must be refused. Red on the
        unmodified app (no check), and red again under the server-clock
        mutation (`SERVER_TODAY < SERVER_TODAY` is false, so it would go
        through).
        """
        day = _ahead(monkeypatch)
        plan = _seed(_server_day(0), (day + timedelta(days=1)).isoformat())

        out = tools.drop_dish_from_day(plan, _ids(_server_day(0))[0])

        assert _server_day(0) == (day - timedelta(days=1)).isoformat()
        assert out["status"] == "refused"
        assert out["message"] == REFUSAL

    def test_a_household_a_day_ahead_can_still_drop_its_own_tonight(self, monkeypatch):
        """GUARD. Their today is the server's tomorrow, and it drops.
        Pinned by MUTATION: `<=` reddens it."""
        day = _ahead(monkeypatch)
        plan = _seed(day.isoformat(), (day + timedelta(days=1)).isoformat())

        out = tools.drop_dish_from_day(plan, _ids(day.isoformat())[0])

        assert day.isoformat() == _server_day(1)
        assert out["status"] == "dropped"


# ------------------------------------------------- 5. the connection hazard

def test_the_clock_is_read_with_no_connection_of_this_functions_open(monkeypatch):
    """
    GUARD, and the one this repo has twice earned. `_household_today`
    reaches `cooker.household_now`, which opens a connection of its own;
    read while drop_dish_from_day is holding one — and in particular from
    inside its write transaction — it would be a nested get_conn, and this
    app has twice paid for that with an intermittent "database is locked"
    rather than a wrong answer. The kind of failure no test sees until
    production.

    Driven down the path that GOES THROUGH rather than the refusal, because
    the refusal returns before the transaction is ever opened and so cannot
    see the hazard at all.

    Green on the unmodified app, which reads no clock, so it is pinned by
    MUTATION: moving `today = _household_today().isoformat()` inside the
    try-block fails it on `assert 2 == 1`.

    Both modules import get_conn by name, so both have to be patched — a
    patch of app.db.get_conn alone would watch a door neither of them uses.
    A proxy rather than a patched method: sqlite3.Connection.close is
    read-only.
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

    plan = _seed(_day(0), _day(1))
    entry = _ids(_day(0))[0]

    monkeypatch.setattr(_wp, "get_conn", tracking)
    monkeypatch.setattr(_cooker, "get_conn", tracking)
    depth["max"] = 0
    assert tools.drop_dish_from_day(plan, entry)["status"] == "dropped"

    assert depth["max"] == 1


# ------------------------------------------------------------- 6. the route

class TestTheRouteAnswersItAsARefusal:
    def test_a_past_night_is_a_200_that_says_no(self, signed_in):
        """CATCH. 200 with the sentence, the shape this route already
        answers its other two refusals in — not the 404 and generic line a
        row id gets, which would report an app that did exactly the right
        thing as broken. Nothing written, over the real route."""
        plan = _seed(_day(-1), _day(1), approve=True)
        before = _list()

        res = signed_in.post(
            f"/api/week/{PLAN_START.isoformat()}/drop-dish-day",
            json={"entry_id": _ids(_day(-1))[0]},
        )

        assert res.status_code == 200, res.text
        assert res.json()["status"] == "refused"
        assert res.json()["message"] == REFUSAL
        assert _state(_day(-1)) == [("planned", "Bean Chili", None)]
        assert _list() == before
        assert plan  # the plan is still there to be dropped from

    def test_tonights_dinner_still_comes_off_over_the_route(self, signed_in):
        """GUARD, same mutation as the tool-level one: `<=` reddens it.
        The control still works for every night it was ever meant to."""
        _seed(_day(0), _day(1))

        res = signed_in.post(
            f"/api/week/{PLAN_START.isoformat()}/drop-dish-day",
            json={"entry_id": _ids(_day(0))[0]},
        )

        assert res.status_code == 200, res.text
        assert res.json()["status"] == "dropped"
