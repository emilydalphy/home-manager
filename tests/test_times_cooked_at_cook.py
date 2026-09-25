"""
A recipe counts as cooked when a night is ticked cooked, not when it is
planned.

Loop Board "A recipe counts as 'cooked' the moment it's planned" (Bug,
Phase 1). recipes.times_cooked and last_cooked_date are what "you've made
this 4 times" and "last cooked in August" read, and they feed the variety
and rotation rules in the generation prompt. Until this change plan_meal
bumped both on INSERT, so drafts, swapped-out nights and abandoned
generations all counted as meals the household ate — on the local
database, "Roast Chicken" read made-twice with both of its nights still
pending. Now:

- planning a recipe leaves both columns alone;
- ticking a night cooked (cooker.check_off_meal) bumps once, per batch;
- a leftovers night (derived_from.links_to) is a reheat, not a cook;
- un-ticking reverses the bump;
- a one-off backfill (db._backfill_recipe_cook_counters_from_ticks)
  rebuilds existing databases' counts from their ticked nights, once.

The generation rollback's snapshot/restore of the counters is gone with
the planning-time bump: nothing is bumped, so nothing needs restoring
(test_plan_integrity.py still covers that failure path).
"""
from __future__ import annotations

import datetime
import sqlite3

from app import db as _db, tools
from app.db import get_conn


def _monday() -> str:
    today = datetime.date.today()
    return (today - datetime.timedelta(days=today.weekday())).isoformat()


def _day(offset: int) -> str:
    return (datetime.date.fromisoformat(_monday()) + datetime.timedelta(days=offset)).isoformat()


def _counters(name: str) -> tuple[int, str | None]:
    conn = get_conn()
    row = conn.execute(
        "SELECT times_cooked, last_cooked_date FROM recipes WHERE name = ?", (name,)
    ).fetchone()
    conn.close()
    return (row["times_cooked"], row["last_cooked_date"])


def _chili():
    tools.add_recipe("Chili", ingredients=[{"item": "beans", "qty": "1 tin"}], default_servings=4)


# ---------- planning doesn't bump ----------

def test_planning_a_recipe_does_not_count_as_cooking_it():
    """The bug itself. Fails on main: plan_meal bumped on insert."""
    _chili()
    plan_id = tools.create_weekly_plan(_monday())["weekly_plan_id"]
    tools.plan_meal(_day(0), "Chili", slot="dinner", weekly_plan_id=plan_id)
    tools.plan_meal(_day(3), "Chili", slot="dinner", weekly_plan_id=plan_id)

    assert _counters("Chili") == (0, None)


def test_a_swapped_in_recipe_is_not_counted_either():
    """A swap writes a new entry through plan_meal; same rule."""
    _chili()
    tools.add_recipe("Soup", ingredients=[{"item": "stock", "qty": "1 l"}])
    plan_id = tools.create_weekly_plan(_monday())["weekly_plan_id"]
    tools.plan_meal(_day(0), "Chili", slot="dinner", weekly_plan_id=plan_id)

    tools.swap_meal_in_plan(plan_id, _day(0), "Soup", slot="dinner")

    assert _counters("Chili") == (0, None)
    assert _counters("Soup") == (0, None)


def test_list_recipes_still_orders_by_times_cooked():
    """The ordering the model relies on ("favor familiar favorites") is unchanged."""
    _chili()
    tools.add_recipe("Soup", ingredients=[{"item": "stock", "qty": "1 l"}])
    entry = tools.plan_meal(_day(0), "Soup", slot="dinner")["entry_id"]
    tools.check_off_meal(entry, "done")

    names = [r["name"] for r in tools.list_recipes()]
    assert names.index("Soup") < names.index("Chili")
    assert {r["name"]: r["times_cooked"] for r in tools.list_recipes()} == {"Soup": 1, "Chili": 0}


# ---------- ticking cooked bumps once ----------

def test_ticking_a_night_cooked_bumps_once_with_the_nights_date():
    _chili()
    entry = tools.plan_meal(_day(1), "Chili", slot="dinner")["entry_id"]

    tools.check_off_meal(entry, "done")
    assert _counters("Chili") == (1, _day(1))

    # A repeat tick is a no-op on the status and must be one on the count.
    tools.check_off_meal(entry, "done")
    assert _counters("Chili") == (1, _day(1))


def test_two_nights_ticked_are_two_cooks_and_last_cooked_is_the_latest():
    _chili()
    mon = tools.plan_meal(_day(0), "Chili", slot="dinner")["entry_id"]
    thu = tools.plan_meal(_day(3), "Chili", slot="dinner")["entry_id"]

    # Ticked out of order: the later night first, then the earlier one.
    # "Last cooked" must not walk backwards when Monday is ticked late.
    tools.check_off_meal(thu, "done")
    tools.check_off_meal(mon, "done")

    assert _counters("Chili") == (2, _day(3))


def test_a_freeform_meal_has_nothing_to_count():
    entry = tools.plan_meal(_day(0), "takeout", slot="dinner")["entry_id"]
    tools.check_off_meal(entry, "done")  # must not raise, nothing to bump
    assert tools.list_recipes() == []


def test_a_component_batch_counts_as_one_cook_however_many_rows_it_has():
    """
    A component-based plan writes one row per meal the component covers,
    and check_off_meal marks every sibling together — one cook, one bump,
    whichever sibling was tapped, and a sibling planned after the batch
    was ticked joins a batch already counted.
    """
    tools.add_recipe("Jello Bowl", ingredients=[{"item": "Jello", "qty": "1 box"}], default_servings=2)
    tools.set_planning_mode("component_based")
    week = _monday()
    plan_id = tools.create_weekly_plan(week)["weekly_plan_id"]
    ids = [
        tools.plan_meal(week, "Jello Bowl", weekly_plan_id=plan_id, component_category="treat")["entry_id"]
        for _ in range(3)
    ]

    tools.check_off_meal(ids[1], "done")
    assert _counters("Jello Bowl") == (1, week)

    late = tools.plan_meal(week, "Jello Bowl", weekly_plan_id=plan_id, component_category="treat")["entry_id"]
    tools.check_off_meal(late, "done")
    assert _counters("Jello Bowl") == (1, week), "the late sibling joins a batch already counted"

    tools.check_off_meal(ids[0], "pending")
    assert _counters("Jello Bowl") == (0, None), "unticking any sibling puts the whole batch back"


# ---------- a leftovers night is not a cook ----------

def test_a_leftovers_night_does_not_count_as_a_cook():
    """
    Monday cooks, Wednesday reheats (derived_from.links_to names Monday).
    Ticking Wednesday eaten is not a second cook of the recipe.
    """
    _chili()
    plan_id = tools.create_weekly_plan(_monday())["weekly_plan_id"]
    mon = tools.plan_meal(_day(0), "Chili", slot="dinner", weekly_plan_id=plan_id)["entry_id"]
    wed = tools.plan_meal(
        _day(2), "Chili", slot="dinner", weekly_plan_id=plan_id,
        derived_from={"links_to": f"{_day(0)}:dinner"},
    )["entry_id"]

    tools.check_off_meal(wed, "done")
    assert _counters("Chili") == (0, None)

    tools.check_off_meal(mon, "done")
    assert _counters("Chili") == (1, _day(0))

    # And unticking the reheat takes nothing off the cook.
    tools.check_off_meal(wed, "pending")
    assert _counters("Chili") == (1, _day(0))


# ---------- un-ticking reverses ----------

def test_unticking_reverses_the_bump():
    _chili()
    entry = tools.plan_meal(_day(1), "Chili", slot="dinner")["entry_id"]
    tools.check_off_meal(entry, "done")
    tools.check_off_meal(entry, "pending")

    assert _counters("Chili") == (0, None)

    # tick -> untick -> tick is the ordinary toggle; it lands on one cook.
    tools.check_off_meal(entry, "done")
    assert _counters("Chili") == (1, _day(1))


def test_unticking_the_latest_night_falls_back_to_the_previous_cook():
    _chili()
    mon = tools.plan_meal(_day(0), "Chili", slot="dinner")["entry_id"]
    thu = tools.plan_meal(_day(3), "Chili", slot="dinner")["entry_id"]
    tools.check_off_meal(mon, "done")
    tools.check_off_meal(thu, "done")
    assert _counters("Chili") == (2, _day(3))

    tools.check_off_meal(thu, "pending")
    assert _counters("Chili") == (1, _day(0))

    # Unticking an EARLIER night leaves "last cooked" where it was.
    tools.check_off_meal(thu, "done")
    tools.check_off_meal(mon, "pending")
    assert _counters("Chili") == (1, _day(3))


def test_unticking_never_goes_below_zero():
    """
    A night ticked done before the backfill counted it cannot exist after
    the backfill, but the guard costs nothing and a negative "made -1
    times" would be the one number worse than a phantom one.
    """
    _chili()
    entry = tools.plan_meal(_day(0), "Chili", slot="dinner")["entry_id"]
    tools.check_off_meal(entry, "done")
    conn = get_conn()
    conn.execute("UPDATE recipes SET times_cooked = 0 WHERE name = 'Chili'")
    conn.commit()
    conn.close()

    tools.check_off_meal(entry, "pending")
    assert _counters("Chili")[0] == 0


def test_discarding_a_draft_after_a_tick_keeps_the_cook():
    """
    times_cooked is nudged, not recomputed from rows, so a cooked night
    whose plan is later removed stays in the count — the household did
    eat it.
    """
    _chili()
    plan_id = tools.create_weekly_plan(_monday())["weekly_plan_id"]
    entry = tools.plan_meal(_day(0), "Chili", slot="dinner", weekly_plan_id=plan_id)["entry_id"]
    tools.check_off_meal(entry, "done")

    tools.discard_failed_plan(plan_id)

    conn = get_conn()
    assert conn.execute("SELECT COUNT(*) c FROM meal_plan_entries").fetchone()["c"] == 0
    conn.close()
    assert _counters("Chili") == (1, _day(0))


# ---------- the one-off backfill ----------

def _seed_old_style_counts(conn):
    """
    What an existing database looks like: planning-time bumps that count
    every planned night, whether or not it was cooked.
    """
    conn.execute("INSERT INTO recipes (household_id, name, times_cooked, last_cooked_date) VALUES (1, 'Roast Chicken', 2, '2027-10-14')")
    conn.execute("INSERT INTO recipes (household_id, name, times_cooked, last_cooked_date) VALUES (1, 'Chili', 5, '2026-08-20')")
    conn.execute("INSERT INTO recipes (household_id, name, times_cooked, last_cooked_date) VALUES (1, 'Never Planned', 1, '2026-01-01')")
    roast = conn.execute("SELECT id FROM recipes WHERE name = 'Roast Chicken'").fetchone()["id"]
    chili = conn.execute("SELECT id FROM recipes WHERE name = 'Chili'").fetchone()["id"]
    rows = [
        # Roast Chicken: planned twice, cooked never.
        (roast, "2027-10-07", "pending", "{}"),
        (roast, "2027-10-14", "pending", "{}"),
        # Chili: five nights on record, two actually cooked, one a reheat
        # that was ticked eaten.
        (chili, "2026-08-03", "done", "{}"),
        (chili, "2026-08-05", "done", '{"links_to": "2026-08-03:dinner"}'),
        (chili, "2026-08-12", "pending", "{}"),
        (chili, "2026-08-17", "done", "{}"),
        (chili, "2026-08-20", "pending", "{}"),
    ]
    conn.executemany(
        "INSERT INTO meal_plan_entries (household_id, recipe_id, date, slot, cooked_status, derived_from_json) "
        "VALUES (1, ?, ?, 'dinner', ?, ?)",
        rows,
    )


def test_backfill_recomputes_both_columns_from_ticked_nights():
    # try/finally so a failure here cannot leave the connection's open
    # transaction holding the test database's lock for the tests after it.
    conn = get_conn()
    try:
        _seed_old_style_counts(conn)
        _db._backfill_recipe_cook_counters_from_ticks(conn)
        conn.commit()
        got = {
            r["name"]: (r["times_cooked"], r["last_cooked_date"])
            for r in conn.execute("SELECT name, times_cooked, last_cooked_date FROM recipes")
        }
    finally:
        conn.close()
    assert got == {
        "Roast Chicken": (0, None),
        "Chili": (2, "2026-08-17"),
        "Never Planned": (0, None),
    }


def test_backfill_runs_once_per_database(tmp_path):
    """
    The real startup path on a database that predates the change: the
    first init recomputes, stamps PRAGMA user_version, and a second init
    leaves a count that has since moved on (a cooked night whose plan was
    deleted) alone.
    """
    path = tmp_path / "old.db"
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    with open(_db.SCHEMA_PATH, encoding="utf-8") as f:
        conn.executescript(f.read())
    assert conn.execute("PRAGMA user_version").fetchone()[0] == 0
    _seed_old_style_counts(conn)

    _db._run_migrations(conn)
    conn.commit()
    assert conn.execute("PRAGMA user_version").fetchone()[0] >= _db._DATA_VERSION_COOK_COUNTERS
    assert conn.execute("SELECT times_cooked FROM recipes WHERE name = 'Chili'").fetchone()[0] == 2

    # History the rows no longer carry: the household ate Chili on a night
    # whose plan was since removed. The tick counted it; a second startup
    # must not take it away.
    conn.execute("DELETE FROM meal_plan_entries WHERE recipe_id = (SELECT id FROM recipes WHERE name = 'Chili')")
    _db._run_migrations(conn)
    conn.commit()
    assert conn.execute("SELECT times_cooked FROM recipes WHERE name = 'Chili'").fetchone()[0] == 2
    conn.close()


def test_backfill_says_so_when_it_resets_every_count_to_zero(caplog):
    """
    A database with old-style counts and no ticked nights — Emily's own —
    drops every favourite to 0 on the first startup. That must not happen
    silently: the log has to say what moved and why.
    """
    conn = get_conn()
    try:
        conn.execute("INSERT INTO recipes (household_id, name, times_cooked, last_cooked_date) VALUES (1, 'Roast Chicken', 2, '2027-10-14')")
        conn.execute("INSERT INTO recipes (household_id, name, times_cooked, last_cooked_date) VALUES (1, 'Chili', 5, '2026-08-20')")
        with caplog.at_level("INFO", logger="home_manager"):
            _db._backfill_recipe_cook_counters_from_ticks(conn)
        conn.commit()
        counts = [r[0] for r in conn.execute("SELECT times_cooked FROM recipes")]
    finally:
        conn.close()
    assert counts == [0, 0]
    assert "Reset cook counters on 2 recipes to 0 — no cooked nights on record yet" in caplog.text
