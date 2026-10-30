"""
Defect hunt, 2026-10-28 (branch overnight/hunt-goingoff-1028): three ways
Today's going-off card and line (tools/going_off.py) said the wrong thing.

1. "Leave it" on the card brought the identical swap straight back for the
   same food on a second row ("Spinach" + "Baby spinach"), and the line
   could name that second row under the card about the first.
2. A dinner AFTER the day the food turns (next week's draft) counted as "a
   plan for it" and silenced both the card and the line.
3. An emptied "0" row ("set milk to 0") was "best used by tomorrow".
"""
from __future__ import annotations

from app import tools
from app.db import get_conn
from app.tools import going_off

from test_going_off_on_today import _d, _double_cook_week, _meal, _plan, _stock


# ---------------------------------------------------------------- 1. one food, two rows

def test_leave_it_covers_the_same_food_on_another_row():
    plan, pasta, chili, fed, spinach = _double_cook_week()
    _stock("Baby spinach", 1)
    card = tools.today_going_off()["card"]
    assert card["item_id"] == spinach and card["entry_id"] == pasta, "premise"
    out = tools.leave_going_off(spinach)
    assert out["card"] is None, "the same swap must not come straight back for the baby spinach"
    assert out["line"] is None, "nor the baby spinach on the line: they said they know"


def test_the_line_never_names_the_food_the_card_is_about():
    plan, pasta, chili, fed, spinach = _double_cook_week()
    _stock("Baby spinach", 1)
    out = tools.today_going_off()
    assert out["card"]["item_id"] == spinach
    assert out["line"] is None


def test_leaving_the_card_stands_its_swap_down_but_other_food_falls_to_the_line():
    plan = _plan()
    pasta = _meal(plan, -2, "Spinach Feta Pasta", recipe_items=["Spinach", "Feta"])
    _meal(plan, 0, "Beef Tacos", recipe_items=["Ground beef"])
    spinach = _stock("Spinach", 1)
    feta = _stock("Feta", 1, category="dairy")
    card = tools.today_going_off()["card"]
    assert card and card["entry_id"] == pasta and card["item_id"] == spinach, "premise"
    out = tools.leave_going_off(spinach)
    assert out["card"] is None, "the household just said no to that swap"
    assert out["line"]["item_id"] == feta
    assert out["line"]["text"] == "Feta is best used by tomorrow."


def test_leaving_the_line_does_not_stand_a_swap_down():
    plan, pasta, chili, fed, spinach = _double_cook_week()
    yogurt = _stock("Greek yogurt", 0, category="dairy")
    out = tools.today_going_off()
    assert out["line"]["item_id"] == yogurt and out["card"]["item_id"] == spinach
    out = tools.leave_going_off(yogurt)
    assert out["card"]["item_id"] == spinach and out["line"] is None


# ---------------------------------------------------------------- 2. too late is no plan

def test_a_dinner_after_the_food_turns_is_no_plan_for_it():
    plan, pasta, chili, fed, spinach = _double_cook_week()
    # Next week's draft, a spinach salad five days out.
    later = tools.create_weekly_plan(_d(4), day_count=7)["weekly_plan_id"]
    _meal(later, 5, "Spinach Salad", recipe_items=["Spinach"])
    card = tools.today_going_off()["card"]
    assert card is not None and card["item_id"] == spinach


def test_a_dinner_on_the_day_it_turns_is_a_plan():
    plan = _plan()
    _meal(plan, 1, "Spinach Omelette", recipe_items=["Spinach", "Eggs"])
    _stock("Spinach", 1)
    assert tools.today_going_off()["line"] is None


def test_the_line_comes_back_for_food_only_a_too_late_dinner_uses():
    plan = _plan()
    _meal(plan, 3, "Spinach Omelette", recipe_items=["Spinach", "Eggs"])
    _stock("Spinach", 1)
    assert tools.today_going_off()["line"]["text"] == "Spinach is best used by tomorrow."


# ---------------------------------------------------------------- 3. an emptied row

def test_an_emptied_row_is_not_going_off():
    _plan()
    _stock("Milk", 1, category="dairy")
    tools.update_inventory("Milk", "set", quantity="0")
    conn = get_conn()
    qty = conn.execute("SELECT quantity FROM inventory_items WHERE item = 'Milk'").fetchone()["quantity"]
    conn.close()
    assert qty == "0", "premise: the row stays, emptied"
    assert going_off.going_off_items(going_off._today()) == []
    assert tools.today_going_off()["line"] is None
