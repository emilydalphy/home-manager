"""
Part cups read as cups on the grocery list (Loop Board, Medium bug; decided
2026-10-18, Emily: "Yes").

Any amount under one cup rolled down to tablespoons: ¾ cup parmesan read
"12 tbsp", ⅔ cup sour cream "10.75 tbsp", ⅓ cup maple syrup "5.25 tbsp".
quantities._roll_up_unit only rolled into a cup at one whole cup. Now a cup
fits from ¼ (_ROLL_UP_FROM) and lands on a measuring cup's steps (quarters
and thirds, nearest — _round_in_unit); under ¼ cup stays tbsp/tsp.

The change is to what is STORED, not only to what is shown, because the
row's Save sends the shown text back even when nobody changed it and a
merge's Undo sends the phone's copy — shown ≠ stored would file a household
edit nobody made. So a cup is STORED the way the list shows it — "2/3 cup",
"1 1/3 cups" (quantities._cups_text) — and an old two-place "0.67 cup" sent
back lands on the step (quantities.typed_back_quantity).
"""
from __future__ import annotations

from datetime import timedelta

import pytest
from conftest import household_today

from app import tools
from app.db import get_conn
from app.tools import quantities


@pytest.mark.parametrize("raw,stored", [
    ("3/4 cup", "3/4 cup"),          # the card's parmesan (main: 12 tbsp)
    ("2/3 cup", "2/3 cup"),          # sour cream (main: 10.75 tbsp)
    ("10.75 tbsp", "2/3 cup"),       # what main stored for it
    ("1/3 cup", "1/3 cup"),          # maple syrup (main: 5.25 tbsp)
    ("0.6 cup", "2/3 cup"),          # off the steps: the nearest one
    ("1/4 cup", "1/4 cup"),
    ("3.9 tbsp", "1/4 cup"),         # rounds up across the line, re-rolled
    ("3 tbsp", "3 tbsp"),            # under 1/4 cup stays tbsp
    ("1/8 cup", "2 tbsp"),
    ("52 tbsp", "3 1/4 cups"),
    ("1.3 cups", "1 1/3 cups"),
    ("2 cups", "2 cups"),
])
def test_a_part_cup_is_stored_and_shown_in_cups(raw, stored):
    assert quantities._normalize_grocery_quantity(raw) == stored
    # Shown exactly as stored, and read back to the same amount: the row's
    # Save and a merge's Undo send this text straight back.
    assert quantities.tidy_display_quantity("x", stored) == stored
    assert quantities._normalize_grocery_quantity(stored) == stored


def test_a_line_stored_before_this_change_reads_in_cups():
    """Rows written by an older build show in cups too (display only)."""
    assert quantities.tidy_display_quantity("Parmesan", "12 tbsp") == "12 tbsp"
    assert quantities.tidy_display_quantity("Milk", "0.75 cup") == "3/4 cup"
    assert quantities.tidy_display_quantity("Milk", "3.25 cups") == "3 1/4 cups"


def test_scale_recipe_keeps_the_cooks_tablespoons():
    """GUARD. Cooking is not shopping: a recipe's 12 tbsp stays 12 tbsp."""
    tools.add_recipe("Dip", ingredients=[{"item": "parmesan", "qty": "12 tbsp"}], default_servings=4)
    scaled = tools.scale_recipe("Dip", 4)
    assert [i["qty"] for i in scaled["scaled_ingredients"]] == ["12 tbsp"]


def _sour_cream_week() -> int:
    """One dinner wanting ⅔ cup of sour cream, approved onto the list."""
    conn = get_conn()
    conn.execute("DELETE FROM members WHERE household_id = ? AND name != 'Emily'", (tools.household_id(),))
    conn.commit()
    conn.close()
    tools.add_recipe(
        "Tacos", ingredients=[{"item": "Sour cream", "qty": "2/3 cup", "category": "dairy"}],
        default_servings=1,
    )
    today = household_today()
    start = today - timedelta(days=today.weekday()) + timedelta(days=7)
    plan_id = tools.create_weekly_plan(start.isoformat())["weekly_plan_id"]
    tools.plan_meal((start + timedelta(days=1)).isoformat(), "Tacos", slot="dinner", weekly_plan_id=plan_id)
    tools.approve_weekly_plan(plan_id, "Emily")
    return _line()["id"]


def _line() -> dict:
    conn = get_conn()
    row = conn.execute(
        "SELECT id, quantity, kept_amount, short_amount, edited_to FROM grocery_items "
        "WHERE LOWER(item) = 'sour cream' AND status = 'needed'"
    ).fetchone()
    conn.close()
    return dict(row)


def _shown() -> str:
    return next(i["quantity"] for i in tools.list_grocery_list() if i["item"].lower() == "sour cream")


def _no_edit(line: dict) -> bool:
    return not (line["kept_amount"] or line["short_amount"] or line["edited_to"])


def test_save_with_the_shown_amount_unchanged_records_no_edit(signed_in):
    line_id = _sour_cream_week()
    assert _line()["quantity"] == "2/3 cup"
    assert _shown() == "2/3 cup"
    # What the row's Save sends: the box exactly as it was filled — and a
    # box still holding a two-place decimal from a screen loaded before
    # this change.
    for sent in (_shown(), "0.67 cup"):
        res = signed_in.post(f"/api/grocery-list/{line_id}/update", json={"quantity": sent})
        assert res.status_code == 200
        after = _line()
        assert after["quantity"] == "2/3 cup"
        assert _no_edit(after), (sent, after)


def test_a_real_edit_is_still_recorded(signed_in):
    """GUARD on the no-op above: a changed box is an edit."""
    line_id = _sour_cream_week()
    signed_in.post(f"/api/grocery-list/{line_id}/update", json={"quantity": "1 cup"})
    after = _line()
    assert after["quantity"] == "1 cup"
    assert after["kept_amount"] == "0.333333 cup"


def test_an_old_decimal_sent_back_by_undo_lands_on_the_step(signed_in):
    """Undo with a phone copy from before this change ("0.67 cup") puts
    back ⅔ cup, not a hair over it filed as the household's extra."""
    line_id = _sour_cream_week()
    tools.add_grocery_item("Sour cream", quantity="1/4 cup", category="dairy")
    signed_in.post(f"/api/grocery-list/{line_id}/update", json={"quantity": "0.67 cup"})
    after = _line()
    assert after["quantity"] == "2/3 cup"
    assert _no_edit(after), after


def test_undoing_a_merge_round_trips(signed_in):
    line_id = _sour_cream_week()
    was = _shown()  # the phone's copy, which Undo sends back
    added = tools.add_grocery_item("Sour cream", quantity="1/4 cup", category="dairy")
    assert added.get("item_id") == line_id
    assert _shown() != was
    res = signed_in.post(f"/api/grocery-list/{line_id}/update", json={"quantity": was})
    assert res.status_code == 200
    assert _shown() == was
    after = _line()
    assert after["quantity"] == "2/3 cup"
    assert _no_edit(after), after
