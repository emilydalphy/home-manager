"""
The last three server-clock pockets outside Chores — inventory, big_meal
and notifications (Loop Board bug, filed and built 2026-09-18).

The container runs UTC and `households.timezone` defaults to
America/Toronto, so from about 8pm local the server's date is already
tomorrow. Every other module was swept between 2026-09-14 and 2026-09-17;
these three were named as follow-ups each time and never done.

The one with teeth is `inventory.get_expiring_soon`. An `expiration_date`
is a CALENDAR DATE, never a UTC instant, so there is no sense in which the
server's day is the right one to compare it against — and before this,
milk that was good all day today was reported `expired` for four hours
every evening. That function is one of the two checks
`agent._build_proactive_check_block` injects into the model's context at
the start of a session, so the assistant said so out loud, unprompted, in
exactly the hours somebody opens the app to sort dinner.

The clock is frozen the way `tests/test_weekly_plan_household_clock.py`
freezes it: `cooker.datetime` is replaced with a subclass whose `now()`
answers one fixed UTC instant, so the zone lookup, the
`households.timezone` read and the ZoneInfo fallback all run for real and
only the wall clock is faked. `date.today()` is left alone, so the two
genuinely differ inside one test exactly as they do in production. Both
directions are pinned: Toronto 21:30 (the household a day BEHIND — the
reported bug) and Tokyo 08:30 (a day AHEAD, the same split from the other
side).

Each test says in its own docstring whether it is a CATCH (red without the
fix) or a NO-REGRESSION GUARD.
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone

import pytest

from app import tools
from app.db import get_conn
from app.tools import big_meal as _big_meal
from app.tools import cooker as _cooker
from app.tools import inventory as _inventory
from app.tools import notifications as _notifications


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


def _stock(item: str, expires: date, category: str = "dairy") -> None:
    conn = get_conn()
    conn.execute(
        "INSERT INTO inventory_items (household_id, item, quantity, category, expiration_date, location) "
        "VALUES (1, ?, '1', ?, ?, 'fridge')",
        (item, category, expires.isoformat()),
    )
    conn.commit()
    conn.close()


def _status_of(item: str, days: int = 4) -> str | None:
    return next((r["status"] for r in _inventory.get_expiring_soon(days=days) if r["item"] == item), None)


# ---------- inventory: the one with teeth ----------

def test_food_good_all_day_today_is_not_reported_expired(monkeypatch):
    """
    CATCH. Milk dated the household's today has not gone off; on the
    server's clock, from 8pm local, it read `expired`.
    """
    household_today = _behind(monkeypatch)
    _stock("Milk", household_today)
    assert _status_of("Milk") == "expiring_soon"


def test_food_dated_yesterday_is_still_reported_expired(monkeypatch):
    """GUARD — the fix must not stop the app saying a true thing."""
    household_today = _behind(monkeypatch)
    _stock("Old yoghurt", household_today - timedelta(days=1))
    assert _status_of("Old yoghurt") == "expired"


def test_a_household_a_day_ahead_has_yesterdays_food_expired(monkeypatch):
    """
    CATCH from the other side: a Tokyo morning where the server still reads
    the previous day. Food dated the server's today HAS gone off there.
    """
    household_today = _ahead(monkeypatch)
    _stock("Milk", household_today - timedelta(days=1))
    assert _status_of("Milk") == "expired"


def test_the_window_is_measured_from_the_households_day(monkeypatch):
    """
    CATCH. The `days` window is a run of calendar days; counted from the
    server's it reaches one day too far for a household behind it.
    """
    household_today = _behind(monkeypatch)
    _stock("Just outside", household_today + timedelta(days=5))
    assert _status_of("Just outside", days=4) is None
    _stock("Just inside", household_today + timedelta(days=4))
    assert _status_of("Just inside", days=4) == "expiring_soon"


def test_the_two_perishable_lists_cannot_disagree_about_one_item(monkeypatch):
    """
    GUARD, pinned by mutation: get_fresh_perishable_inventory is the
    complement of get_expiring_soon, so two clocks would let one item be in
    both lists or in neither. Point either at date.today() and this fails.
    """
    household_today = _behind(monkeypatch)
    for n in range(0, 8):
        _stock(f"Item {n}", household_today + timedelta(days=n), category="produce")
    expiring = {r["item"] for r in _inventory.get_expiring_soon(days=4)}
    fresh = {r["item"] for r in _inventory.get_fresh_perishable_inventory(near_expiring_days=4)}
    assert not (expiring & fresh), "an item in both lists"
    assert expiring | fresh == {f"Item {n}" for n in range(0, 8)}, "an item in neither"


def test_nudging_an_undated_item_starts_from_the_households_day(monkeypatch):
    """
    CATCH. One tap is one day, and the date it starts from is the one the
    household then sees on the item.
    """
    household_today = _behind(monkeypatch)
    conn = get_conn()
    conn.execute(
        "INSERT INTO inventory_items (household_id, item, quantity, category, location) "
        "VALUES (1, 'Leeks', '2', 'produce', 'fridge')"
    )
    item_id = conn.execute("SELECT id FROM inventory_items WHERE item = 'Leeks'").fetchone()["id"]
    conn.commit()
    conn.close()

    out = _inventory.step_inventory_expiration(item_id, 3)
    assert out["expiration_date"] == (household_today + timedelta(days=3)).isoformat()


# ---------- big_meal ----------

def test_the_early_trip_is_not_dropped_a_day_early(monkeypatch):
    """
    CATCH. shop_dates drops the early trip once it is too late for it; on
    the server's clock a household behind it loses that trip a day early.
    """
    household_today = _behind(monkeypatch)
    holiday = household_today + timedelta(days=_big_meal.EARLY_SHOP_DAYS_AHEAD)
    trips = _big_meal.shop_dates(holiday.isoformat())
    assert trips["early"] == household_today.isoformat()


def _hosting_on(day: date) -> None:
    conn = get_conn()
    conn.execute(
        "INSERT INTO holiday_answers (household_id, date, holiday_name, answer, headcount) "
        "VALUES (1, ?, 'Thanksgiving', 'hosting', 6)",
        (day.isoformat(),),
    )
    conn.commit()
    conn.close()


def test_a_holiday_today_is_still_an_upcoming_menu(monkeypatch):
    """
    CATCH. `_upcoming_menus` is "from today on"; on the server's clock a
    household behind it loses the split for the holiday it is cooking
    TODAY, which is the one day the split matters most.

    `menu_entry` is stubbed to a sentinel on purpose: this test is about the
    date bound and nothing else, and building a real menu entry would make
    it a test of the menu builder that happened to mention a clock.
    """
    household_today = _behind(monkeypatch)
    _hosting_on(household_today)
    monkeypatch.setattr(_big_meal, "menu_entry", lambda d: {"id": 1})
    assert [a["date"] for a, _ in _big_meal._upcoming_menus()] == [household_today.isoformat()]


def test_a_holiday_the_household_has_already_passed_is_not_upcoming(monkeypatch):
    """
    CATCH, and it was labelled a guard until redness was measured. A
    household a day AHEAD has already had the holiday the server still reads
    as today, so on the server's clock the split keeps being offered for a
    dinner that has been eaten.
    """
    household_today = _ahead(monkeypatch)
    _hosting_on(household_today - timedelta(days=1))
    monkeypatch.setattr(_big_meal, "menu_entry", lambda d: {"id": 1})
    assert _big_meal._upcoming_menus() == []


def test_an_explicit_today_still_wins_over_the_clock(monkeypatch):
    """
    GUARD, pinned by mutation: every one of these takes `today` and the
    change is to its DEFAULT only. A caller holding one clock must never be
    answered about another day.

    The first version of this test was TOOTHLESS and only measurement found
    it: it named a day a month out, where both clocks give the same answer,
    so making shop_dates ignore its argument outright left it green. The two
    days below are chosen so the two clocks genuinely disagree — one keeps
    the early trip, the other has already dropped it.
    """
    household_today = _behind(monkeypatch)
    holiday = household_today + timedelta(days=_big_meal.EARLY_SHOP_DAYS_AHEAD)
    # On the household's own clock the early trip is today, and stands.
    assert _big_meal.shop_dates(holiday.isoformat())["early"] == household_today.isoformat()
    # A caller naming tomorrow is past it, and must be told so.
    named = household_today + timedelta(days=1)
    assert _big_meal.shop_dates(holiday.isoformat(), today=named)["early"] is None


# ---------- notifications ----------

def test_the_expiring_dismissal_key_names_the_households_day(monkeypatch):
    """
    CATCH. The key rolls over at about 8pm local on the server's clock, so a
    household that tapped the notification away at 7:55 was shown it again
    at 8:01 under a key naming tomorrow.
    """
    household_today = _behind(monkeypatch)
    _stock("Milk", household_today + timedelta(days=1))
    keys = [n["key"] for n in _notifications.get_active_notifications() if n["type"] == "expiring_soon"]
    assert keys == [f"expiring:{household_today.isoformat()}"]


def test_a_dismissal_actually_holds_for_the_rest_of_the_households_day(monkeypatch):
    """CATCH, the same defect said as behaviour rather than as a string."""
    household_today = _behind(monkeypatch)
    _stock("Milk", household_today + timedelta(days=1))
    key = f"expiring:{household_today.isoformat()}"
    _notifications.dismiss_notification(key)
    assert [n for n in _notifications.get_active_notifications() if n["type"] == "expiring_soon"] == []


def test_the_week_the_household_is_living_in_is_not_announced_as_next_weeks(monkeypatch):
    """
    CATCH. "a plan for a week that hasn't started yet" is a calendar-day
    comparison. Read on the server's clock, a household a day AHEAD is told
    the week it woke up in is next week's plan, newly ready.

    The AHEAD direction is the one that separates the two answers here: a
    household BEHIND the server is suppressed either way, so a test in that
    direction would have passed without the fix. The first version of this
    test was in that direction AND filtered on a `type` string the app never
    emits, so it compared an empty list to an empty list and could not fail —
    found by measuring redness rather than by reading it.
    """
    household_today = _ahead(monkeypatch)
    conn = get_conn()
    conn.execute(
        "INSERT INTO weekly_plans (household_id, week_start_date, status, content_start_date, day_count) "
        "VALUES (1, ?, 'approved', ?, 7)",
        (household_today.isoformat(), household_today.isoformat()),
    )
    plan_id = conn.execute("SELECT id FROM weekly_plans ORDER BY id DESC LIMIT 1").fetchone()["id"]
    for n in range(3):
        conn.execute(
            "INSERT INTO meal_plan_entries (household_id, weekly_plan_id, date, slot, freeform_meal) "
            "VALUES (1, ?, ?, 'dinner', 'Chili')",
            (plan_id, (household_today + timedelta(days=n)).isoformat()),
        )
    conn.commit()
    conn.close()
    kinds = [n["type"] for n in _notifications.get_active_notifications()]
    assert "weekly_plan_ready" not in kinds, kinds


def test_a_plan_for_a_week_genuinely_ahead_is_still_announced(monkeypatch):
    """GUARD — the fix must not silence the notification it is narrowing."""
    household_today = _ahead(monkeypatch)
    start = household_today + timedelta(days=3)
    conn = get_conn()
    conn.execute(
        "INSERT INTO weekly_plans (household_id, week_start_date, status, content_start_date, day_count) "
        "VALUES (1, ?, 'approved', ?, 7)",
        (start.isoformat(), start.isoformat()),
    )
    plan_id = conn.execute("SELECT id FROM weekly_plans ORDER BY id DESC LIMIT 1").fetchone()["id"]
    for n in range(3):
        conn.execute(
            "INSERT INTO meal_plan_entries (household_id, weekly_plan_id, date, slot, freeform_meal) "
            "VALUES (1, ?, ?, 'dinner', 'Chili')",
            (plan_id, (start + timedelta(days=n)).isoformat()),
        )
    conn.commit()
    conn.close()
    kinds = [n["type"] for n in _notifications.get_active_notifications()]
    assert "weekly_plan_ready" in kinds, kinds


# ---------- the rule the sweep follows ----------

def test_the_duration_read_is_deliberately_left_on_utc():
    """
    GUARD, and red against the unmodified app only because `_today` does not
    exist there — NOT evidence of a defect. What it is really for is the
    rule: a read that compares a UTC instant
    against another UTC instant as a DURATION stays where it is. `created_at`
    is SQLite's own UTC `datetime('now')`, so converting one side would make
    the two disagree — and _today()'s docstring says so, which is what a
    future sweep will act on.
    """
    code = _code_of(_notifications._today)
    assert "utcnow" in _source(_notifications.get_active_notifications)
    assert "household_today" in code


def test_every_module_moved_in_the_same_commit():
    """
    A SOURCE guard on this repo's own rule (2026-09-17,
    freezer-ask-household-clock): a half-converted module is a new bug, not a
    smaller one. Red against the unmodified app for exactly the reason it is
    named after — all eleven live `date.today()` reads are still there.

    It walks the AST rather than the text, so a `date.today()` inside a
    comment or a docstring cannot satisfy it; notifications.py has one of
    those, left over from a deleted line, and an earlier grep-shaped version
    of this guard would have been fooled by it.
    """
    import ast
    import inspect

    for module in (_inventory, _big_meal, _notifications):
        tree = ast.parse(inspect.getsource(module))
        calls = [
            n for n in ast.walk(tree)
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
            and n.func.attr == "today" and isinstance(n.func.value, ast.Name)
            and n.func.value.id == "date"
        ]
        assert not calls, f"{module.__name__} still reads the server's clock at line {calls[0].lineno}"


# ---------- helpers ----------

def _source(fn) -> str:
    import inspect
    return inspect.getsource(fn)


def _code_of(fn) -> str:
    """
    A function's CODE with its docstring and comments taken off — an
    assertion prose can satisfy is not an assertion.
    """
    import ast
    import inspect
    import textwrap

    tree = ast.parse(textwrap.dedent(inspect.getsource(fn)))
    body = tree.body[0].body
    if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) \
            and isinstance(body[0].value.value, str):
        body = body[1:]
    return "\n".join(ast.unparse(node) for node in body)
