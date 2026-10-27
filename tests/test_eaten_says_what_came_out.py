"""
Loop Board "Inventory A2 · marking dinner eaten says what came out of the
kitchen, with Undo" (Emily, 2026-10-27).

- The tick answers what it took out (`took_out`) and the toast's second
  sentence (`kitchen_line`): "Took the chicken, spinach, rice and lemons
  out of the kitchen." Up to four names, then "and N more".
- Undo (cooker.undo_meal_eaten, POST /api/cooker/undo-eaten) un-marks the
  meal and puts every row back exactly as it was — a row the tick used up
  and DELETED comes back with its id, location and expiry.
- A leftovers night takes nothing out, so it says nothing about the
  kitchen; a meal with nothing tracked says nothing either.
"""
from __future__ import annotations

import datetime

from app import tools
from app.db import get_conn
from conftest import household_date, household_today


def _row(item):
    conn = get_conn()
    row = conn.execute(
        "SELECT id, item, quantity, location, category, expiration_date, updated_at "
        "FROM inventory_items WHERE item = ?", (item,)
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def _cooked(entry_id):
    conn = get_conn()
    row = conn.execute(
        "SELECT cooked_status, inventory_depleted_at FROM meal_plan_entries WHERE id = ?", (entry_id,)
    ).fetchone()
    conn.close()
    return row["cooked_status"], row["inventory_depleted_at"]


def _lemon_chicken():
    tools.add_recipe("Lemon chicken", ingredients=[
        {"item": "Chicken", "qty": "2 lbs"},
        {"item": "Spinach", "qty": "1 bag"},
        {"item": "Rice", "qty": "2 cups"},
        {"item": "Lemons", "qty": "2"},
        {"item": "Salt"},  # no amount: left alone, never named
    ], default_servings=4)
    tools.update_inventory("Chicken", "add", quantity="2 lbs", location="fridge",
                           category="meat/seafood", expiration_date=household_date(3))
    tools.update_inventory("Spinach", "add", quantity="2 bags", location="fridge")
    tools.update_inventory("Rice", "add", quantity="6 cups", location="pantry")
    tools.update_inventory("Lemons", "add", quantity="5", location="fridge")
    tools.update_inventory("Salt", "add", quantity="1 box", location="pantry")
    return tools.plan_meal(household_date(), "Lemon chicken", slot="dinner")["entry_id"]


def test_the_tick_names_what_came_out_of_the_kitchen():
    entry = _lemon_chicken()
    result = tools.check_off_meal(entry, "done")
    assert result["meal"] == "Lemon chicken"
    assert result["took_out"] == ["chicken", "spinach", "rice", "lemons"]
    assert result["kitchen_line"] == "Took the chicken, spinach, rice and lemons out of the kitchen."
    assert _row("Chicken") is None, "2 lbs of 2 lbs is used up — the row goes"
    assert _row("Lemons")["quantity"] == "3"


def test_kitchen_line_counts_past_four():
    assert tools.kitchen_line([]) == ""
    assert tools.kitchen_line(["rice"]) == "Took the rice out of the kitchen."
    assert tools.kitchen_line(["rice", "lemons"]) == "Took the rice and lemons out of the kitchen."
    assert tools.kitchen_line(list("abcdef")) == "Took the a, b, c, d and 2 more out of the kitchen."


def test_undo_puts_every_row_back_exactly_and_unmarks_the_meal(signed_in):
    entry = _lemon_chicken()
    before = {i: _row(i) for i in ("Chicken", "Spinach", "Rice", "Lemons", "Salt")}

    res = signed_in.post("/api/cooker/check-meal", json={"entry_id": entry, "status": "done"})
    assert res.status_code == 200
    checked = res.json()["checked"]
    assert checked["meal"] == "Lemon chicken"
    assert checked["kitchen_line"] == "Took the chicken, spinach, rice and lemons out of the kitchen."

    res = signed_in.post("/api/cooker/undo-eaten", json={"entry_id": entry})
    assert res.status_code == 200
    assert res.json()["undone"]["left_alone"] == []

    for item, was in before.items():
        assert _row(item) == was, f"{item} should be back exactly as it was"
    assert _cooked(entry) == ("pending", None)

    # Back on the shelf, so eating it again takes it out again.
    again = tools.check_off_meal(entry, "done")
    assert again["took_out"] == ["chicken", "spinach", "rice", "lemons"]
    assert _row("Chicken") is None


def test_a_second_undo_puts_nothing_back_twice():
    entry = _lemon_chicken()
    tools.check_off_meal(entry, "done")
    tools.undo_meal_eaten(entry)
    second = tools.undo_meal_eaten(entry)
    assert second["put_back"] == []
    assert _row("Lemons")["quantity"] == "5"


def test_a_row_changed_since_the_tick_is_left_alone():
    entry = _lemon_chicken()
    tools.check_off_meal(entry, "done")
    tools.update_inventory("Lemons", "set", quantity="1")
    undone = tools.undo_meal_eaten(entry)
    assert "lemons" in undone["left_alone"]
    assert _row("Lemons")["quantity"] == "1", "the household's own count wins"
    assert _row("Rice")["quantity"] == "6 cups"
    assert _cooked(entry)[0] == "pending"


def test_two_ticks_from_two_phones_take_it_out_once():
    entry = _lemon_chicken()
    tools.check_off_meal(entry, "done")
    repeat = tools.check_off_meal(entry, "done")
    assert not repeat.get("kitchen_line")
    assert _row("Lemons")["quantity"] == "3"


def test_nothing_tracked_says_nothing_about_the_kitchen(signed_in):
    tools.add_recipe("Plain toast", ingredients=[{"item": "Bread", "qty": "2 slices"}], default_servings=1)
    entry = tools.plan_meal(household_date(), "Plain toast", slot="dinner")["entry_id"]
    res = signed_in.post("/api/cooker/check-meal", json={"entry_id": entry, "status": "done"})
    assert res.json()["checked"]["kitchen_line"] == ""
    assert res.json()["checked"]["took_out"] == []


def test_a_leftovers_night_takes_nothing_and_says_nothing():
    monday = household_today() - datetime.timedelta(days=household_today().weekday())
    tue = (monday + datetime.timedelta(days=1)).isoformat()
    thu = (monday + datetime.timedelta(days=3)).isoformat()
    tools.add_member("Alex")
    tools.add_recipe("Bulgogi Wraps", ingredients=[{"item": "beef", "qty": "1 lb"}], default_servings=3)
    tools.update_inventory("beef", "add", quantity="4 lbs", category="meat/seafood")
    plan_id = tools.create_weekly_plan(monday.isoformat())["weekly_plan_id"]
    tools.plan_meal(tue, "Bulgogi Wraps", slot="dinner", weekly_plan_id=plan_id)
    tools.plan_meal(thu, "Bulgogi Wraps", slot="dinner", weekly_plan_id=plan_id,
                    derived_from={"links_to": f"{tue}:dinner"})
    tools.repair_leftover_chains(plan_id)
    view = {m["date"]: m for m in tools.get_cooker_view(plan_id)["meals"]}
    reheat = view[thu]["entry_id"]

    result = tools.check_off_meal(reheat, "done")
    assert not result.get("kitchen_line")
    assert _row("beef")["quantity"] == "4 lbs"


def test_today_move_tick_carries_the_same_receipt(signed_in):
    entry = _lemon_chicken()
    res = signed_in.post(f"/api/today/moves/cook:{entry}/done", json={"done": True})
    assert res.status_code == 200
    assert res.json()["checked"]["kitchen_line"].startswith("Took the chicken")


def test_the_toast_says_it_with_undo_and_stays_quiet_otherwise():
    """shell.js's own toastEatenFromKitchen, under node."""
    import shutil

    import pytest
    if shutil.which("node") is None:
        pytest.skip("node is needed to run the function")
    from test_plan_cards_2026_09_18 import _run
    from test_week_seven_tiles import _extract
    from test_plan_next_on_draft import SHELL_JS
    out = _run("""
var TOASTS = [];
function savedName(s) { return s; }
function showToast(msg, action) { TOASTS.push([msg, action && action.label]); }
""" + _extract("toastEatenFromKitchen", SHELL_JS) + """
var said = toastEatenFromKitchen({ entry_id: 1, meal: 'Lemon chicken', cooked_status: 'done',
  kitchen_line: 'Took the chicken, spinach, rice and lemons out of the kitchen.' });
var quiet = toastEatenFromKitchen({ entry_id: 2, meal: 'Leftover chili', cooked_status: 'done', kitchen_line: '' });
console.log(JSON.stringify({ said: said, quiet: quiet, toasts: TOASTS }));
""")
    assert out == {"said": True, "quiet": False, "toasts": [[
        "Lemon chicken marked eaten. Took the chicken, spinach, rice and lemons out of the kitchen.", "Undo"]]}


def test_undo_puts_back_a_row_two_recipe_lines_drew_on():
    # Olive oil for the marinade and again for the dressing; lemons twice,
    # the second take emptying the row. Undo must bring both back whole.
    tools.add_recipe("Oily salad", ingredients=[
        {"item": "Olive oil", "qty": "2 tbsp"},
        {"item": "Olive oil", "qty": "1 tbsp"},
        {"item": "Lemons", "qty": "2"},
        {"item": "Lemons", "qty": "3"},
    ], default_servings=2)
    tools.update_inventory("Olive oil", "add", quantity="10 tbsp", location="pantry")
    tools.update_inventory("Lemons", "add", quantity="5", location="fridge", expiration_date=household_date(9))
    entry = tools.plan_meal(household_date(0), "Oily salad", slot="dinner")["entry_id"]
    oil_before, lemons_before = _row("Olive oil"), _row("Lemons")

    tools.check_off_meal(entry, "done")
    undone = tools.undo_meal_eaten(entry)

    assert not undone["left_alone"], undone
    assert _row("Olive oil")["quantity"] == oil_before["quantity"]
    assert _row("Lemons")["quantity"] == lemons_before["quantity"]
    conn = get_conn()
    claim = conn.execute("SELECT inventory_depleted_at FROM meal_plan_entries WHERE id = ?", (entry,)).fetchone()[0]
    conn.close()
    assert claim is None, "every row came back, so ticking it again takes the food out again"
