"""
"Change recipe" review follow-ups (2026-10-05): a rewrite or an undo edits
the recipes row IN PLACE, so every approved night on that row moves, not
only the chain; a pick with nothing to buy is refused; an undo only runs
when it is still the truth.
"""
from __future__ import annotations

import json

from app import households, tools
from app.db import get_conn
from app.tools import recipe_change

from conftest import household_date
from test_recipe_change import (  # helpers only
    MON, TUE, WED, _entry, _entry_recipe, _needed, _plan, _recipe,
)

FRI = household_date(4)

NEW = {
    "ingredients": [{"item": "Dried chickpeas", "qty": "250 g"}],
    "instructions": ["Soak them.", "Simmer."],
}


def _rewrite(entry, text="Use dried chickpeas", detail=NEW):
    return tools.apply_rewrite(tools.rewrite_spec(entry), detail, text)


def _recipe_json(recipe_id):
    conn = get_conn()
    try:
        return conn.execute(
            "SELECT ingredients_json FROM recipes WHERE id = ?", (recipe_id,)
        ).fetchone()["ingredients_json"]
    finally:
        conn.close()


def _chain(cook, reheat, cook_date, reheat_date):
    conn = get_conn()
    try:
        conn.execute("UPDATE meal_plan_entries SET derived_from_json = ? WHERE id = ?",
                     (json.dumps({"links_to": f"{cook_date}:dinner"}), reheat))
        conn.execute("UPDATE meal_plan_entries SET derived_from_json = ? WHERE id = ?",
                     (json.dumps({"make_double_for": [f"{reheat_date}:lunch"]}), cook))
        conn.commit()
    finally:
        conn.close()


# ---------- 1. every approved night on the row ----------

def test_a_rewrite_moves_an_unchained_night_on_the_same_recipe(signed_in):
    plan = _plan("approved", day_count=5)
    _recipe("Chana Masala", [("Chickpeas", "2 cans"), ("Coconut milk", "1 can")])
    tue = _entry(plan, TUE, "Chana Masala", buy=True)
    _entry(plan, FRI, "Chana Masala", buy=True)

    res = _rewrite(tue)

    after = _needed()
    assert "Coconut milk" not in after, after
    assert "Chickpeas" not in after, after
    assert "Dried chickpeas" in after, after
    assert res["lines_changed"] >= 2


def test_a_rewrite_moves_another_approved_weeks_night_too(signed_in):
    this_week = _plan("approved")
    nxt = tools.create_weekly_plan(household_date(7), content_start_date=household_date(7),
                                   day_count=3)["weekly_plan_id"]
    conn = get_conn()
    try:
        conn.execute("UPDATE weekly_plans SET status = 'approved' WHERE id = ?", (nxt,))
        conn.commit()
    finally:
        conn.close()
    _recipe("Chana Masala", [("Chickpeas", "2 cans"), ("Coconut milk", "1 can")])
    tue = _entry(this_week, TUE, "Chana Masala", buy=True)
    _entry(nxt, household_date(8), "Chana Masala", buy=True)

    _rewrite(tue)

    after = _needed()
    assert "Coconut milk" not in after and "Chickpeas" not in after, after


def test_an_undo_moves_a_later_unchained_night_back_too(signed_in):
    plan = _plan("approved", day_count=5)
    _recipe("Chana Masala", [("Chickpeas", "2 cans"), ("Coconut milk", "1 can")])
    tue = _entry(plan, TUE, "Chana Masala", buy=True)
    res = _rewrite(tue)
    # Friday is added after the rewrite, so it is bought on the NEW recipe.
    _entry(plan, FRI, "Chana Masala", buy=True)
    assert "Dried chickpeas" in _needed()

    undone = tools.undo_recipe_change(res["request_id"])

    assert undone["status"] == "undone", undone
    back = _needed()
    assert "Dried chickpeas" not in back, back
    assert "Coconut milk" in back and "Chickpeas" in back, back


# ---------- 2. a pick with nothing to buy ----------

def test_picking_a_recipe_with_no_ingredients_on_an_approved_week_is_refused_and_writes_nothing(signed_in):
    plan = _plan("approved")
    _recipe("Chana Masala", [("Chickpeas", "2 cans")])
    empty = _recipe("Half Written", [("Rice", "1 cup")])
    conn = get_conn()
    try:
        conn.execute("UPDATE recipes SET ingredients_json = '[]', details_pending = 1 WHERE id = ?", (empty,))
        conn.commit()
    finally:
        conn.close()
    entry = _entry(plan, TUE, "Chana Masala", buy=True)
    before, was = _needed(), _entry_recipe(entry)["recipe_id"]

    res = tools.change_meal_recipe(entry, empty)

    assert res["status"] == "refused" and res["said"] == recipe_change.NO_INGREDIENTS_YET, res
    assert _needed() == before
    assert _entry_recipe(entry)["recipe_id"] == was


# ---------- 3. undo safety ----------

def test_an_undo_is_refused_once_the_meal_points_somewhere_else(signed_in):
    plan = _plan("approved")
    old = _recipe("Chana Masala", [("Chickpeas", "2 cans")])
    other = _recipe("Rajma", [("Kidney beans", "2 cans")])
    entry = _entry(plan, TUE, "Chana Masala", buy=True)
    res = _rewrite(entry)
    tools.change_meal_recipe(entry, other)
    list_now, row_now = _needed(), _recipe_json(old)

    out = tools.undo_recipe_change(res["request_id"])

    assert out["status"] == "refused" and out["said"] == recipe_change.UNDO_MOVED_ON, out
    assert _needed() == list_now and _recipe_json(old) == row_now


def test_an_undo_is_refused_while_a_newer_rewrite_is_on_top(signed_in):
    plan = _plan("approved")
    rid = _recipe("Chana Masala", [("Chickpeas", "2 cans")])
    entry = _entry(plan, TUE, "Chana Masala", buy=True)
    first = _rewrite(entry, "first")
    _rewrite(entry, "second", {"ingredients": [{"item": "Spinach", "qty": "1 bag"}],
                               "instructions": ["Wilt it."]})
    row_now = _recipe_json(rid)

    out = tools.undo_recipe_change(first["request_id"])

    assert out["status"] == "refused" and out["said"] == recipe_change.UNDO_NEWER, out
    assert _recipe_json(rid) == row_now


def test_an_undo_runs_the_allergy_gate_on_the_restored_recipe(signed_in):
    plan = _plan("approved")
    rid = _recipe("Satay Noodles", [("Peanut butter", "3 tbsp"), ("Noodles", "400 g")])
    entry = _entry(plan, TUE, "Satay Noodles", buy=True)
    res = _rewrite(entry, "no peanuts", {
        "ingredients": [{"item": "Sunflower butter", "qty": "3 tbsp"}, {"item": "Noodles", "qty": "400 g"}],
        "instructions": ["Toss."]})
    tools.add_member("Reid")
    tools.set_member_age_group("Reid", "child")
    tools.set_member_dietary_restrictions("Reid", ["allergy: peanut"])
    row_now = _recipe_json(rid)

    out = tools.undo_recipe_change(res["request_id"])

    assert out["status"] == "refused", out
    assert "Reid" in out["said"] and "peanut" in out["said"].lower(), out
    assert _recipe_json(rid) == row_now


# ---------- 4. missing coverage ----------

def test_an_undo_on_an_approved_week_follows_the_leftover_chain(signed_in):
    plan = _plan("approved", day_count=5)
    _recipe("Chana Masala", [("Chickpeas", "2 cans"), ("Coconut milk", "1 can")])
    cook = _entry(plan, MON, "Chana Masala", buy=True)
    reheat = _entry(plan, WED, "Chana Masala", slot="lunch")
    _chain(cook, reheat, MON, WED)
    res = _rewrite(cook)
    assert "Coconut milk" not in _needed()

    out = tools.undo_recipe_change(res["request_id"])

    assert out["status"] == "undone", out
    back = _needed()
    assert "Coconut milk" in back and "Dried chickpeas" not in back, back
    assert _entry_recipe(reheat)["recipe_id"] == _entry_recipe(cook)["recipe_id"]


def test_an_undo_is_scoped_to_its_own_household(signed_in):
    plan = _plan("approved")
    rid = _recipe("Chana Masala", [("Chickpeas", "2 cans")])
    entry = _entry(plan, TUE, "Chana Masala", buy=True)
    res = _rewrite(entry)
    row_now = _recipe_json(rid)

    other = households.create_household("Recipe Undo Isolation", "recipe-undo-isolation-pass")
    with tools.use_household(other):
        out = tools.undo_recipe_change(res["request_id"])
    assert out["status"] == "refused" and out["said"] == recipe_change.UNDO_NOTHING, out

    assert _recipe_json(rid) == row_now
    assert [r["outcome"] for r in tools.recent_recipe_change_requests()] == ["rewritten"]


# ---------- only what is still ahead ----------

def _lines_of_plan(plan_id):
    conn = get_conn()
    try:
        return sorted(
            (r["item"], r["quantity"], r["status"]) for r in conn.execute(
                "SELECT item, quantity, status FROM grocery_items WHERE source_weekly_plan_id = ?",
                (plan_id,))
        )
    finally:
        conn.close()


def test_a_rewrite_leaves_a_finished_approved_week_and_a_cooked_night_alone(signed_in):
    old_start = household_date(-28)
    old = tools.create_weekly_plan(old_start, content_start_date=old_start,
                                   day_count=3)["weekly_plan_id"]
    conn = get_conn()
    try:
        conn.execute("UPDATE weekly_plans SET status = 'approved' WHERE id = ?", (old,))
        conn.commit()
    finally:
        conn.close()
    _recipe("Chana Masala", [("Chickpeas", "2 cans"), ("Coconut milk", "1 can")])
    tools.plan_meal(household_date(-27), "Chana Masala", slot="dinner", weekly_plan_id=old,
                    add_ingredients_to_grocery_list=True)
    plan = _plan("approved", day_count=5)
    tue = _entry(plan, TUE, "Chana Masala", buy=True)
    # A night of this week that is already behind the household's today.
    cooked = _entry(plan, MON, "Chana Masala", buy=True)
    conn = get_conn()
    try:
        conn.execute("UPDATE meal_plan_entries SET date = ? WHERE id = ?", (household_date(-1), cooked))
        conn.commit()
    finally:
        conn.close()
    old_lines = _lines_of_plan(old)

    res = _rewrite(tue)

    assert _lines_of_plan(old) == old_lines
    assert res["entry_ids"] == [tue]
    assert "Dried chickpeas" in _needed()


def _make_past(entry_id):
    conn = get_conn()
    try:
        conn.execute("UPDATE meal_plan_entries SET date = ? WHERE id = ?", (household_date(-3), entry_id))
        conn.commit()
    finally:
        conn.close()


def test_changing_a_night_already_cooked_changes_the_recipe_and_leaves_the_list_alone(signed_in):
    plan = _plan("approved", day_count=5)
    _recipe("Chana Masala", [("Chickpeas", "2 cans"), ("Coconut milk", "1 can")])
    new = _recipe("Rajma", [("Kidney beans", "2 cans")])
    past = _entry(plan, MON, "Chana Masala", buy=True)
    _entry(plan, TUE, "Chana Masala", buy=True)
    _make_past(past)
    before = _needed()

    res = tools.change_meal_recipe(past, new)

    assert res["status"] == "changed", res
    assert _entry_recipe(past)["recipe_id"] == new
    assert _needed() == before
    assert res["lines_changed"] == 0


def test_rewriting_a_night_already_cooked_leaves_the_list_alone(signed_in):
    plan = _plan("approved", day_count=5)
    _recipe("Chana Masala", [("Chickpeas", "2 cans"), ("Coconut milk", "1 can")])
    past = _entry(plan, MON, "Chana Masala", buy=True)
    _make_past(past)
    before = _needed()

    res = _rewrite(past)

    assert res["status"] == "rewritten", res
    assert _needed() == before
    assert res["lines_changed"] == 0
