"""
A kitchen row the shop joined ("1 cup + 2 L") loses only what was used
(defect hunt, 2026-10-31).

Repro over the real routes: a dinner needs 1 cup of milk, the household
adds its own 2 L, both lines are ticked on the trip, and the Kitchen reads
"milk · 1 cup + 2 L" (metric onto imperial stays joined — _merged_quantity).
Then "used a cup of milk" deleted the whole row — two litres bought that
morning gone — because a joined amount read as freeform ("a bunch"), and
freeform means "all of it". Cooking the dinner took nothing off at all.
"""
from __future__ import annotations

from datetime import timedelta

from conftest import household_today

from app import tools
from app.db import get_conn


def _milk() -> list[str]:
    conn = get_conn()
    rows = conn.execute("SELECT quantity FROM inventory_items WHERE LOWER(item) = 'milk'").fetchall()
    conn.close()
    return [r["quantity"] for r in rows]


def _shop(client) -> int:
    today = household_today()
    tools.add_recipe("Pancakes", ingredients=[{"item": "milk", "qty": "1 cup"}], default_servings=4)
    plan_id = tools.create_weekly_plan((today - timedelta(days=today.weekday())).isoformat())["weekly_plan_id"]
    entry = tools.plan_meal(today.isoformat(), "Pancakes", slot="dinner", weekly_plan_id=plan_id)["entry_id"]
    tools.approve_weekly_plan(plan_id, "Alex")
    assert client.post("/api/grocery-list/add", json={"item": "milk", "quantity": "2 L"}).status_code == 200
    conn = get_conn()
    lines = [r["id"] for r in conn.execute("SELECT id FROM grocery_items WHERE item = 'milk'").fetchall()]
    conn.close()
    assert len(lines) == 2
    for line in lines:
        res = client.post(f"/api/grocery-list/{line}/status", json={"status": "purchased"})
        assert res.status_code == 200
    assert _milk() == ["1 cup + 2 L"]
    return entry


def test_a_cup_used_off_a_joined_row_leaves_the_two_litres(signed_in):
    _shop(signed_in)

    result = tools.update_inventory("milk", "use", quantity="1 cup")

    assert result.get("removed") is not True
    assert _milk() == ["2 L"]


def test_cooking_takes_the_recipes_cup_and_undo_puts_it_back(signed_in):
    entry = _shop(signed_in)

    assert signed_in.post("/api/cooker/check-meal", json={"entry_id": entry, "status": "done"}).status_code == 200
    assert _milk() == ["2 L"]
    assert signed_in.post("/api/cooker/check-meal", json={"entry_id": entry, "status": "done"}).status_code == 200
    assert _milk() == ["2 L"], "a second tick takes nothing more"

    assert signed_in.post("/api/cooker/undo-eaten", json={"entry_id": entry}).status_code == 200
    assert _milk() == ["1 cup + 2 L"]


def test_an_amount_in_neither_unit_leaves_the_row_alone(signed_in):
    tools.update_inventory("Flour", "add", quantity="1 kg + 2 cups", location="pantry")

    result = tools.update_inventory("Flour", "use", quantity="1 bag")

    assert result["units_reconciled"] is False
    assert result["quantity"] == "1 kg + 2 cups"


def test_a_freeform_amount_used_is_still_all_of_it(signed_in):
    tools.update_inventory("Rice", "add", quantity="a big bag", location="pantry")

    assert tools.update_inventory("Rice", "use", quantity="1 cup") == {"item": "Rice", "removed": True}
