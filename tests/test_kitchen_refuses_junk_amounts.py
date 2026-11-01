"""
Kitchen's add refuses an amount nobody has on a shelf (overnight hunt,
2026-11-01, branch overnight/kitchen-refuses-junk-amounts). POST
/api/inventory/update with action "add" stored "-2 lb", "inf" and "nan" as
written. Same rule and sentence as the grocery amount box (Emily,
2026-10-31): a 400, "That amount doesn't look right."; the chat tool raises
it as a tool error rather than storing it.
"""
from __future__ import annotations

import pytest

from app import tools

REFUSED = "That amount doesn't look right."


def _qty(item: str) -> str | None:
    rows = [r for r in tools.get_inventory() if r["item"].lower() == item.lower()]
    return rows[0]["quantity"] if rows else None


@pytest.mark.parametrize("amount", ["-2 lb", "inf", "nan", "-inf", "Infinity", "0", "x" * 41, "99999999999999999999"])
def test_kitchen_add_refuses_junk_with_the_grocery_sentence(signed_in, amount):
    res = signed_in.post("/api/inventory/update", json={"item": "Ground beef", "action": "add", "quantity": amount})
    assert res.status_code == 400, res.text
    assert res.json()["detail"] == REFUSED
    assert _qty("Ground beef") is None


@pytest.mark.parametrize("amount", ["2 lb", "1/2 cup", "some", "", "1 dozen"])
def test_kitchen_add_keeps_ordinary_amounts_as_today(signed_in, amount):
    res = signed_in.post("/api/inventory/update", json={"item": "Rice", "action": "add", "quantity": amount})
    assert res.status_code == 200, res.text
    assert _qty("Rice") == amount


def test_kitchen_set_refuses_junk_onto_an_existing_row(signed_in):
    tools.update_inventory("Milk", "add", quantity="1 gallon")
    res = signed_in.post("/api/inventory/update", json={"item": "Milk", "action": "set", "quantity": "nan"})
    assert res.status_code == 400, res.text
    assert _qty("Milk") == "1 gallon"


def test_a_count_of_zero_still_empties_the_row(signed_in):
    tools.update_inventory("Eggs", "add", quantity="6")
    res = signed_in.post("/api/inventory/update", json={"item": "Eggs", "action": "set", "quantity": "0"})
    assert res.status_code == 200, res.text
    assert _qty("Eggs") == "0"


def test_chat_tool_raises_and_the_batch_answers_per_entry():
    with pytest.raises(ValueError, match=REFUSED):
        tools.update_inventory("Butter", "add", quantity=float("inf"))
    with pytest.raises(ValueError, match=REFUSED):
        tools.update_inventory("Butter", "set", quantity="-1")
    with pytest.raises(ValueError, match=REFUSED):
        tools.update_inventory("Butter", "add", quantity=1e30)
    out = tools.update_inventory_items([
        {"item": "Flour", "quantity": "2 cups"},
        {"item": "Sugar", "quantity": "nan"},
    ], action="add")
    assert _qty("Flour") == "2 cups"
    assert _qty("Sugar") is None
    assert {"item": "Sugar", "error": REFUSED} in out["updated"]
