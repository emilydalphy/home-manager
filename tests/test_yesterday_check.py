"""
Today, the morning after: "Did you have it?" about any meal left unticked
(Loop Board card, Emily 2026-09-25 — the bottom of the "Bring it over"
mockup).

Server: tools.yesterday_check lists yesterday's unticked meals and snacks
on the household's clock (never reheats, never one already answered);
tools.answer_yesterday takes "had" (the cooked tick itself) or "skipped"
(meal_plan_entries.skipped_at — cooked_status stays 'pending', so the
bring-over list still offers it). Client: the card's copy and wiring in
shell.js. Every test here fails on main, where none of it exists.
"""
from __future__ import annotations

import datetime
from pathlib import Path

import pytest

from app import tools
from app.db import get_conn

REPO = Path(__file__).resolve().parents[1]
SHELL_JS = (REPO / "static" / "shell.js").read_text(encoding="utf-8")
SHELL_CSS = (REPO / "static" / "shell.css").read_text(encoding="utf-8")


def _today() -> datetime.date:
    from conftest import household_today
    return household_today()


def _setup(approved: bool = True):
    """An approved plan covering the day before yesterday, yesterday AND
    today — the only three days this file ever plans on — whatever weekday
    today is. Returns (plan_id, today, yesterday ISO).

    NOT a Monday-anchored week, and that is the whole point. On a Monday,
    yesterday and the day before sit in LAST week while today sits in this
    one, so no seven-day week starting on a Monday can hold all three, and
    plan_meal's period guard correctly refuses the meal on today — which is
    what made `clock (monday)` red. The app was right and the seed was
    wrong; naming the days the tests actually need is the fix CLAUDE.md's
    own weekday-cliff entries prescribe (see the 2026-09-17
    drop-dish-refuses-the-past entry, and the 2026-09-22 shop_freezing_it
    one, which is the same class on the sunday pin).

    A 3-day period, not 7, for the same reason: every extra day is a day
    some future weekday could fall the wrong side of. content_start_date is
    left unset with day_count set, which plan_period resolves to
    (week_start_date, 3) — see its docstring on the sentinel pair."""
    today = _today()
    yday = today - datetime.timedelta(days=1)
    start = yday - datetime.timedelta(days=1)
    plan = tools.create_weekly_plan(start.isoformat(), day_count=3)["weekly_plan_id"]
    if approved:
        conn = get_conn()
        conn.execute("UPDATE weekly_plans SET status = 'approved' WHERE id = ?", (plan,))
        conn.commit()
        conn.close()
    return plan, today, yday.isoformat()


def _recipe(name, item="Onion", qty="1"):
    tools.add_recipe(name, ingredients=[{"item": item, "qty": qty, "category": "produce"}],
                     prep_time_minutes=10, cook_time_minutes=20, default_servings=4)


def _plan(day, meal, slot="dinner", plan=None, derived=None, recipe=True):
    if recipe:
        _recipe(meal)
    return tools.plan_meal(day, meal, slot=slot, weekly_plan_id=plan, derived_from=derived)["entry_id"]


def _row(entry_id):
    conn = get_conn()
    row = conn.execute(
        "SELECT cooked_status, skipped_at, inventory_depleted_at, recipe_id FROM meal_plan_entries WHERE id = ?",
        (entry_id,),
    ).fetchone()
    conn.close()
    return dict(row)


def _times_cooked(entry_id):
    conn = get_conn()
    row = conn.execute(
        "SELECT r.times_cooked, r.last_cooked_date FROM recipes r "
        "JOIN meal_plan_entries mpe ON mpe.recipe_id = r.id WHERE mpe.id = ?",
        (entry_id,),
    ).fetchone()
    conn.close()
    return row["times_cooked"], row["last_cooked_date"]


def _names(card):
    return [m["meal"] for m in card["meals"]]


# ---------------------------------------------------------------- what it asks

def test_asks_about_yesterdays_unticked_meals_and_snacks_in_eating_order():
    plan, today, yday = _setup()
    _plan(yday, "Lemon salmon traybake", "dinner", plan)
    _plan(yday, "Apple slices", "snack", plan)
    _plan(yday, "Grain bowl", "lunch", plan)
    card = tools.yesterday_check(today)
    assert card["date"] == yday
    assert _names(card) == ["Grain bowl", "Lemon salmon traybake", "Apple slices"]
    assert all(set(m) >= {"entry_id", "meal", "slot"} for m in card["meals"])


def test_never_asks_about_today_or_further_back_than_yesterday():
    plan, today, yday = _setup()
    before = (datetime.date.fromisoformat(yday) - datetime.timedelta(days=1)).isoformat()
    _plan(before, "Bean chili", "dinner", plan)
    _plan(today.isoformat(), "Pad thai", "dinner", plan)
    assert tools.yesterday_check(today)["meals"] == []


def test_a_meal_already_ticked_cooked_is_not_asked_about():
    plan, today, yday = _setup()
    entry = _plan(yday, "Lemon salmon traybake", "dinner", plan)
    tools.check_off_meal(entry, "done")
    assert tools.yesterday_check(today)["meals"] == []


def test_leftovers_and_reheat_nights_are_not_asked_about():
    plan, today, yday = _setup()
    _plan(yday, "Chili reheat", "dinner", plan, derived={"links_to": "some earlier cook"})
    _plan(yday, "Frozen lasagna", "lunch", plan, derived={"from_freezer": True})
    _plan(yday, "Leftovers", "snack", plan, recipe=False)
    _plan(yday, "Takeout", "breakfast", plan, recipe=False)
    assert tools.yesterday_check(today)["meals"] == []


def test_an_unapproved_draft_is_not_asked_about():
    plan, today, yday = _setup(approved=False)
    _plan(yday, "Lemon salmon traybake", "dinner", plan)
    assert tools.yesterday_check(today)["meals"] == []


def test_an_empty_slot_is_not_asked_about():
    plan, today, yday = _setup()
    entry = _plan(yday, "Lemon salmon traybake", "dinner", plan)
    conn = get_conn()
    conn.execute("UPDATE meal_plan_entries SET slot_state = 'planned_empty' WHERE id = ?", (entry,))
    conn.commit()
    conn.close()
    assert tools.yesterday_check(today)["meals"] == []


def test_no_argument_reads_the_households_clock_not_the_servers(monkeypatch):
    from app.tools import cooker
    pinned = datetime.date(2031, 3, 4)
    monkeypatch.setattr(cooker, "household_today", lambda *a, **kw: pinned)
    assert tools.yesterday_check()["date"] == "2031-03-03"


# ---------------------------------------------------------------- "We had it"

def test_we_had_it_is_the_cooked_tick():
    plan, today, yday = _setup()
    tools.add_recipe("Tacos", ingredients=[{"item": "Tortillas", "qty": "8"}], default_servings=1)
    tools.update_inventory("Tortillas", "add", quantity="20", location="pantry")
    entry = tools.plan_meal(yday, "Tacos", slot="dinner", weekly_plan_id=plan)["entry_id"]
    before = _times_cooked(entry)[0] or 0

    out = tools.answer_yesterday(entry, "had", today)

    row = _row(entry)
    assert row["cooked_status"] == "done"
    assert row["inventory_depleted_at"] is not None
    assert {i["item"]: i["quantity"] for i in tools.get_inventory()}["Tortillas"] == "12"
    times, last = _times_cooked(entry)
    assert times == before + 1 and last
    assert out["meals"] == [] and out["meal"] == "Tacos" and out["answer"] == "had"


def test_a_row_is_asked_once_a_second_answer_is_refused():
    plan, today, yday = _setup()
    entry = _plan(yday, "Lemon salmon traybake", "dinner", plan)
    other = _plan(yday, "Apple slices", "snack", plan)
    out = tools.answer_yesterday(entry, "had", today)
    assert _names(out) == ["Apple slices"]
    with pytest.raises(tools.NotAskedAbout):
        tools.answer_yesterday(entry, "had", today)
    with pytest.raises(tools.NotAskedAbout):
        tools.answer_yesterday(entry, "skipped", today)
    assert _row(entry)["skipped_at"] is None
    assert tools.answer_yesterday(other, "skipped", today)["meals"] == []


# ---------------------------------------------------------------- "We skipped it"

def test_we_skipped_it_records_the_skip_and_leaves_the_meal_pending():
    plan, today, yday = _setup()
    entry = _plan(yday, "Lemon salmon traybake", "dinner", plan)
    before = _times_cooked(entry)
    out = tools.answer_yesterday(entry, "skipped", today)
    row = _row(entry)
    assert row["cooked_status"] == "pending"
    assert row["skipped_at"] is not None
    assert row["inventory_depleted_at"] is None
    assert _times_cooked(entry) == before
    assert out["meals"] == []


def test_a_skipped_dinner_is_still_offered_by_the_bring_over_list():
    from app.tools import bring_over
    plan, today, yday = _setup()
    entry = _plan(yday, "Lemon salmon traybake", "dinner", plan)
    tools.answer_yesterday(entry, "skipped", today)
    conn = get_conn()
    row = conn.execute(
        "SELECT week_start_date, content_start_date, day_count FROM weekly_plans WHERE id = ?", (plan,)
    ).fetchone()
    days = row["day_count"] or 7
    next_week = (datetime.date.fromisoformat(row["week_start_date"]) + datetime.timedelta(days=days)).isoformat()
    offered = bring_over.last_week_uncooked(conn, next_week)
    conn.close()
    assert "Lemon salmon traybake" in [d["meal"] for d in offered]


def test_marking_a_skipped_meal_cooked_later_clears_the_skip():
    plan, today, yday = _setup()
    entry = _plan(yday, "Lemon salmon traybake", "dinner", plan)
    tools.answer_yesterday(entry, "skipped", today)
    tools.check_off_meal(entry, "done")
    assert _row(entry)["skipped_at"] is None
    assert _row(entry)["cooked_status"] == "done"


def test_only_rows_the_card_is_asking_about_can_be_answered():
    plan, today, yday = _setup()
    tonight = _plan(today.isoformat(), "Pad thai", "dinner", plan)
    with pytest.raises(tools.NotAskedAbout):
        tools.answer_yesterday(tonight, "skipped", today)
    assert _row(tonight)["skipped_at"] is None
    entry = _plan(yday, "Lemon salmon traybake", "dinner", plan)
    with pytest.raises(ValueError):
        tools.answer_yesterday(entry, "moved", today)


def test_skipped_at_is_a_real_column_on_a_migrated_database():
    conn = get_conn()
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(meal_plan_entries)").fetchall()}
    conn.close()
    assert "skipped_at" in cols


# ---------------------------------------------------------------- the routes

def test_routes_read_and_answer_on_the_households_day(signed_in):
    plan, today, yday = _setup()
    entry = _plan(yday, "Lemon salmon traybake", "dinner", plan)
    res = signed_in.get("/api/today/yesterday")
    assert res.status_code == 200
    body = res.json()
    assert body["date"] == yday and _names(body) == ["Lemon salmon traybake"]

    res = signed_in.post("/api/today/yesterday/answer", json={"entry_id": entry, "answer": "had"})
    assert res.status_code == 200
    assert res.json()["meals"] == [] and res.json()["meal"] == "Lemon salmon traybake"

    again = signed_in.post("/api/today/yesterday/answer", json={"entry_id": entry, "answer": "skipped"})
    assert again.status_code == 409
    bad = signed_in.post("/api/today/yesterday/answer", json={"entry_id": entry, "answer": "later"})
    assert bad.status_code == 422


# ---------------------------------------------------------------- the card

def test_the_card_says_what_emily_decided():
    assert "'<div class=\"ny-kicker\">Yesterday</div>'" in SHELL_JS
    assert "' Did you have it?'" in SHELL_JS
    assert '>We had it</button>' in SHELL_JS
    assert '>We skipped it</button>' in SHELL_JS
    assert "fetch('/api/today/yesterday')" in SHELL_JS
    assert "fetch('/api/today/yesterday/answer'" in SHELL_JS
    assert 'id="yesterday-check"' in SHELL_JS
    assert "loadYesterdayCheck(panel)," in SHELL_JS


def test_the_card_spends_no_apricot_and_hides_when_empty():
    # Both answers sit inside .ny-actions, which re-inks .btn-gold to spruce
    # (Rule 5 — Today's apricot is the dock).
    start = SHELL_JS.index("function renderYesterdayCheck")
    body = SHELL_JS[start:SHELL_JS.index("async function answerYesterday", start)]
    assert "'<div class=\"ny-actions\">'" in body
    assert ".today-area-yesterday:empty { display: none; }" in SHELL_CSS
