"""
Chat tools fed the arguments a model actually sends (overnight hunt,
2026-10-30, branch overnight/chat-args-1030). Every call goes through the
agent's own dispatch: agent.read_tool_input, then agent.TOOL_FUNCTIONS.

1. A blank dish. swap_meal_in_plan(new_meal="") deleted the planned dinner
   (and its groceries) and wrote a "planned" row with no meal on it;
   plan_meal(meal="   ") planted the same blank dinner on an empty night.
   Both answered success. The screens' own door (resolve_open_slot) has
   always refused a blank choice.
2. A use-by date in words. update_inventory(expiration_date="11/02") stored
   the string as typed; get_expiring_soon compares dates as text, so
   "11/02" sorted before every real date and the yogurt read EXPIRED, and
   "tomorrow" sorted after them and never expired at all.
3. A number for an amount. add_grocery_item(quantity=12) — the model's
   JSON number, not "12" — crashed on .strip(), in the batch tool too.
"""
from __future__ import annotations

import datetime

import pytest

from conftest import household_date, household_today

from app import agent, tools
from app.db import get_conn


def _call(tool_name, **kw):
    return agent.TOOL_FUNCTIONS[tool_name](**agent.read_tool_input(tool_name, kw))


def _rows(sql, *args):
    conn = get_conn()
    try:
        return [dict(r) for r in conn.execute(sql, args).fetchall()]
    finally:
        conn.close()


@pytest.fixture
def week_with_tacos():
    tools.add_member("Emily")
    tools.set_member_age_group("Emily", "adult")
    tools.add_recipe("Tacos", ingredients=[{"item": "tortillas", "qty": "8"}])
    today = household_today()
    plan_id = tools.create_weekly_plan(
        (today - datetime.timedelta(days=today.weekday())).isoformat()
    )["weekly_plan_id"]
    _call("plan_meal", meal_date=household_date(1), meal="Tacos", slot="dinner")
    return plan_id


# --- 1. A blank dish -------------------------------------------------------

@pytest.mark.parametrize("blank", ["", "   ", None])
def test_a_blank_swap_is_refused_and_the_dinner_stays(week_with_tacos, blank):
    before = _rows("SELECT id, recipe_id, freeform_meal, slot_state FROM meal_plan_entries ORDER BY id")
    with pytest.raises(ValueError):
        _call("swap_meal_in_plan", weekly_plan_id=week_with_tacos,
              meal_date=household_date(1), new_meal=blank)
    assert _rows("SELECT id, recipe_id, freeform_meal, slot_state FROM meal_plan_entries ORDER BY id") == before


def test_a_blank_plan_meal_writes_no_row(week_with_tacos):
    with pytest.raises(ValueError):
        _call("plan_meal", meal_date=household_date(2), meal="   ", slot="dinner")
    assert _rows("SELECT id FROM meal_plan_entries WHERE date = ?", household_date(2)) == []


def test_a_real_dish_with_stray_spaces_still_plans(week_with_tacos):
    result = _call("swap_meal_in_plan", weekly_plan_id=week_with_tacos,
                   meal_date=household_date(1), new_meal="  Fish pie ")
    assert result["meal"] == "Fish pie"


# --- 2. A use-by date in words ---------------------------------------------

@pytest.mark.parametrize("said", ["11/02", "tomorrow", "Friday", "2026-11-31", "20261102"])
def test_a_use_by_that_is_not_a_calendar_day_is_refused(said):
    with pytest.raises(ValueError):
        _call("update_inventory", item="Yogurt", action="add", quantity="1",
              expiration_date=said, location="fridge")
    assert _rows("SELECT id FROM inventory_items") == []
    assert tools.get_expiring_soon() == [] or all(
        r["item"] != "Yogurt" for r in tools.get_expiring_soon())


def test_a_real_use_by_and_a_blank_one_are_kept():
    _call("update_inventory", item="Yogurt", action="add", quantity="1",
          expiration_date=household_date(3), location="fridge")
    _call("update_inventory", item="Milk", action="add", quantity="1",
          expiration_date="", location="fridge")
    got = {r["item"]: r["expiration_date"] for r in _rows("SELECT item, expiration_date FROM inventory_items")}
    assert got["Yogurt"] == household_date(3)
    assert got["Milk"]  # the estimate, as before


def test_one_bad_use_by_in_a_batch_is_that_entry_s_answer():
    result = _call("update_inventory_items", action="add", items=[
        {"item": "Yogurt", "quantity": "1", "expiration_date": "11/02", "location": "fridge"},
        {"item": "Rice", "quantity": "1 bag", "location": "pantry"},
    ])
    assert "error" in result["updated"][0]
    assert [r["item"] for r in _rows("SELECT item FROM inventory_items")] == ["Rice"]


# --- 3. A number for an amount ---------------------------------------------

def test_a_number_quantity_is_read_as_that_amount():
    _call("add_grocery_item", item="Eggs", quantity=12)
    _call("add_grocery_items", items=[{"item": "Milk", "quantity": 2}])
    got = {r["item"]: r["quantity"] for r in _rows("SELECT item, quantity FROM grocery_items")}
    assert got == {"Eggs": "12", "Milk": "2"}
    eggs = _rows("SELECT id FROM grocery_items WHERE item = 'Eggs'")[0]["id"]
    _call("update_grocery_item", item_id=eggs, quantity=18)
    assert _rows("SELECT quantity FROM grocery_items WHERE id = ?", eggs)[0]["quantity"] == "18"


# --- 4. One bad entry in a grocery batch ------------------------------------
# add_grocery_items raised on the first bad entry with the ones before it
# already saved; the model retried the batch and those lines merged up
# (Milk 1 -> 2). Now each entry is its own answer, as update_inventory_items.

@pytest.mark.parametrize("bad", [{"item": 12}, {"item": "Bread", "quantity": {"amount": 2}}, 7])
def test_one_bad_entry_in_a_grocery_batch_is_that_entry_s_answer(bad):
    result = _call("add_grocery_items", items=[{"item": "Milk", "quantity": "1"}, bad, "Eggs"])
    assert result["added"] == ["Milk", "Eggs"]
    assert len(result["failed"]) == 1 and result["failed"][0]["error"]
    rows = {r["item"]: r["quantity"] for r in _rows("SELECT item, quantity FROM grocery_items")}
    assert rows == {"Milk": "1", "Eggs": ""}


def test_a_grocery_batch_with_nothing_good_saves_nothing_and_says_why():
    with pytest.raises(ValueError, match="isn't an item name"):
        _call("add_grocery_items", items=[{"item": 12}])
    assert _rows("SELECT item FROM grocery_items") == []


def test_a_partly_added_batch_still_draws_the_grocery_card():
    from app import main as app_main
    args = {"items": ["Milk", {"item": 12}]}
    result = _call("add_grocery_items", **args)
    after = [
        {"role": "assistant", "content": [{"type": "tool_use", "id": "t1", "name": "add_grocery_items", "input": args}]},
        {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t1", "content": __import__("json").dumps(result)}]},
    ]
    cards = app_main.summarize_chat_actions([], after)
    assert [a.tab for a in cards] == ["grocery"]
    # The card names what went on the list, not the entry that was refused.
    text = " ".join(str(getattr(a, f, "") or "") for a in cards for f in ("change",))
    assert "Milk" in text and "12" not in text, text
