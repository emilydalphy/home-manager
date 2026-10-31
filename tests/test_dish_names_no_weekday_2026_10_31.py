"""
Dish names never carry a weekday, and a breakfast or snack is never
"Leftovers" (Loop Board, 2026-10-31 — Emily's Today screenshot: breakfast
"Greek Yogurt Parfait with Berries and Granola (Wednesday)" marked
Leftovers, and the snacks marked Leftovers too).

Where the weekday came from: Tweak's "this meal only" change copies a
shared recipe for the one night (recipe_change.apply_rewrite) and named
the copy after its day. The name is the dish's name on every screen, so
the day read as part of the dish. Now the copy is "<dish> (your way)",
and db._repair_weekday_fork_names renames the copies made before.

Where "Leftovers" came from: Today's meal row said "Leftovers" for every
reheat, a batch made ahead on purpose included. day_meals.made_ahead says
which word: "Made ahead" for a cook-ahead batch and for any breakfast or
snack eaten from an earlier batch; the Plan rows say the same.
"""
from __future__ import annotations

import json
import shutil

import pytest

from app import agent, db, tools
from app.db import get_conn
from app.tools import day_meals, recipe_change
from test_tweak_something_else import (
    _plan, _recipe, _meal, _post, _writer, _entry_recipe_id, _NO_CILANTRO, TUE, WED,
)
from test_today_days_meals import _render, _node, _MEAL_ROWS
from test_declutter_2026_10_30 import _plan as _plan_js

_needs_node = pytest.mark.skipif(shutil.which("node") is None, reason="node is needed to run the renderers")

_WEEKDAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")


def _name(recipe_id):
    conn = get_conn()
    try:
        return conn.execute("SELECT name FROM recipes WHERE id = ?", (recipe_id,)).fetchone()[0]
    finally:
        conn.close()


# ---------- the copy's name ----------

def test_a_this_meal_only_copy_is_named_your_way_never_by_its_day(signed_in, monkeypatch):
    plan = _plan("approved")
    _recipe("Chicken Tacos", [("Chicken thighs", "1 lb"), ("Cilantro", "1 bunch")])
    tue = _meal(plan, TUE, "Chicken Tacos", buy=True)
    _meal(plan, WED, "Chicken Tacos", buy=True)
    monkeypatch.setattr(agent, "generate_recipe_details_llm", _writer(_NO_CILANTRO))

    body = _post(signed_in, tue, "No cilantro").json()

    assert body["forked"] is True, body
    name = _name(_entry_recipe_id(tue))
    assert name == "Chicken Tacos (your way)"
    assert not any(day in name for day in _WEEKDAYS)


def test_a_copy_of_a_copy_is_not_marked_twice():
    tools.add_recipe("Oats", [{"item": "Oats", "qty": "1 cup"}], instructions=["Soak."])
    tools.add_recipe("Oats (your way)", [{"item": "Oats", "qty": "1 cup"}], instructions=["Soak."])
    conn = get_conn()
    try:
        assert recipe_change._fork_name(conn, "Oats (your way)") == "Oats (your way) 2"
        assert recipe_change._fork_name(conn, "Oats") == "Oats (your way) 2"
    finally:
        conn.close()


# ---------- the startup repair ----------

def _add_raw(hid, name):
    conn = get_conn()
    try:
        cur = conn.execute("INSERT INTO recipes (household_id, name) VALUES (?, ?)", (hid, name))
        conn.commit()
        return int(cur.lastrowid)
    finally:
        conn.close()


def test_the_repair_renames_old_weekday_copies_per_household_and_is_idempotent():
    hid = tools.household_id()
    conn = get_conn()
    conn.execute("INSERT INTO households (id, name) VALUES (99, 'Next door')")
    conn.commit()
    conn.close()
    _add_raw(hid, "Greek Yogurt Parfait with Berries and Granola")
    parfait = _add_raw(hid, "Greek Yogurt Parfait with Berries and Granola (Wednesday)")
    tacos_2 = _add_raw(hid, "Chicken Tacos (Tuesday) 2")
    _add_raw(hid, "Chicken Tacos")
    own = _add_raw(hid, "Pizza (Friday)")            # no "Pizza": their own name, kept
    _add_raw(99, "Oats")
    theirs = _add_raw(99, "Oats (Monday)")
    lonely = _add_raw(99, "Chicken Tacos (Thursday)")  # "Chicken Tacos" is household 1's, not 99's

    conn = get_conn()
    try:
        db._repair_weekday_fork_names(conn)
        conn.commit()
        db._repair_weekday_fork_names(conn)
        conn.commit()
    finally:
        conn.close()

    assert _name(parfait) == "Greek Yogurt Parfait with Berries and Granola (your way)"
    assert _name(tacos_2) == "Chicken Tacos (your way)"
    assert _name(own) == "Pizza (Friday)"
    assert _name(theirs) == "Oats (your way)"
    assert _name(lonely) == "Chicken Tacos (Thursday)", "scoped per household"


def test_the_repair_numbers_a_copy_when_the_household_already_has_one():
    _add_raw(tools.household_id(), "Frittata")
    _add_raw(tools.household_id(), "Frittata (your way)")
    old = _add_raw(tools.household_id(), "Frittata (Saturday)")
    conn = get_conn()
    try:
        db._repair_weekday_fork_names(conn)
        conn.commit()
    finally:
        conn.close()
    assert _name(old) == "Frittata (your way) 2"


def test_the_repair_runs_at_startup():
    src = open(db.__file__, encoding="utf-8").read()
    assert "    _repair_weekday_fork_names(conn)\n" in src


# ---------- "Made ahead", not "Leftovers" ----------

def _reheat(**kw):
    meal = {"is_leftovers": True, "leftovers_from": {"date": "2026-10-28", "slot": "breakfast", "meal": "Parfait"},
            "leftovers_headline": "Leftovers — Tuesday’s Parfait"}
    meal.update(kw)
    return meal


def test_a_breakfast_or_snack_from_an_earlier_batch_is_made_ahead():
    assert day_meals.made_ahead(_reheat(), "breakfast") is True
    assert day_meals.made_ahead(_reheat(), "snack") is True


def test_a_dinner_batch_made_on_purpose_is_made_ahead_and_plain_leftovers_stay_leftovers():
    assert day_meals.made_ahead(_reheat(leftovers_headline="Made ahead — Sunday’s Stew"), "dinner") is True
    assert day_meals.made_ahead(_reheat(), "lunch") is False
    assert day_meals.made_ahead(_reheat(), "dinner") is False
    # A night named "Leftover chili" and a meal cooked fresh are neither.
    assert day_meals.made_ahead(_reheat(leftovers_named=True), "snack") is False
    assert day_meals.made_ahead({"is_leftovers": False}, "breakfast") is False


@_needs_node
def test_todays_row_and_sheet_say_made_ahead_for_a_made_ahead_row():
    made = [dict(_MEAL_ROWS[0], is_leftovers=True, made_ahead=True)]
    html = _render([], None, None, made)
    assert '<span class="day-meal-note">Made ahead</span>' in html
    assert "Leftovers" not in html
    sheet = _node("console.log(JSON.stringify(todayMealSheetHtml(%s)));" % json.dumps(made[0]))
    assert "Made ahead" in sheet and "Leftovers" not in sheet
    left = [dict(_MEAL_ROWS[0], is_leftovers=True, made_ahead=False)]
    assert '<span class="day-meal-note">Leftovers</span>' in _render([], None, None, left)


@_needs_node
def test_a_plan_row_for_a_breakfast_or_snack_batch_says_made_ahead():
    out = _plan_js(
        "var src = function (slot) { return { state: 'planned', leftover_from: { date: '2026-09-21', slot: slot } }; };\n"
        "console.log(JSON.stringify([planRowMeta(src('breakfast')), planRowMeta(src('snack')),"
        " planRowMeta(src('dinner'))]));"
    )
    assert out == ["Made ahead", "Made ahead", "Leftovers"]
