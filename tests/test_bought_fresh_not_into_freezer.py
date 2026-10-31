"""
Fresh food ticked off on a shop goes where fresh food goes, never onto a
frozen pack (defect hunt, 2026-10-31).

Repro over the real routes: 1 lb chicken thighs in the freezer, a dinner
that needs 2 lbs, the list buys "chicken thighs · 2 lbs", tick it. The tick
names no shelf, so the name-only match took the only chicken row there was:
Kitchen read "Chicken thighs · 3 lbs · Freezer", dated January, the fresh
2 lbs were never in "use these first", and fresh chicken that keeps two
days read as keeping three months.
"""
from __future__ import annotations

from datetime import date, timedelta

from conftest import household_today

from app import tools
from app.db import get_conn


def _rows(item: str) -> list[dict]:
    conn = get_conn()
    rows = conn.execute(
        "SELECT id, quantity, location, expiration_date FROM inventory_items "
        "WHERE LOWER(item) = LOWER(?) ORDER BY id",
        (item,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def _plan_and_list(item: str, qty: str) -> tuple[int, int]:
    today = household_today()
    tools.add_recipe("Stew", ingredients=[{"item": item, "qty": qty}], default_servings=4)
    plan_id = tools.create_weekly_plan((today - timedelta(days=today.weekday())).isoformat())["weekly_plan_id"]
    entry = tools.plan_meal(today.isoformat(), "Stew", slot="dinner", weekly_plan_id=plan_id)["entry_id"]
    tools.approve_weekly_plan(plan_id, "Alex")
    conn = get_conn()
    line = conn.execute("SELECT id FROM grocery_items WHERE LOWER(item) = LOWER(?)", (item,)).fetchone()["id"]
    conn.close()
    return entry, line


def _tick(client, line: int, status: str) -> dict:
    res = client.post(f"/api/grocery-list/{line}/status", json={"status": status})
    assert res.status_code == 200, res.text
    return res.json()


def test_fresh_chicken_bought_beside_a_frozen_pack_lands_in_the_fridge(signed_in):
    tools.update_inventory("Chicken thighs", "add", quantity="1 lb", location="freezer")
    frozen = _rows("chicken thighs")[0]
    _, line = _plan_and_list("chicken thighs", "2 lbs")

    _tick(signed_in, line, "purchased")

    rows = _rows("chicken thighs")
    assert rows[0] == frozen, "the frozen pack is not touched by a fresh purchase"
    assert len(rows) == 2
    fresh = rows[1]
    assert (fresh["quantity"], fresh["location"]) == ("2 lbs", "fridge")
    assert date.fromisoformat(fresh["expiration_date"]) <= household_today() + timedelta(days=4)
    expiring = {i["item"].lower() for i in signed_in.get("/api/inventory/expiring").json()["items"]}
    assert "chicken thighs" in expiring


def test_the_untick_takes_back_the_fresh_row_and_leaves_the_pack(signed_in):
    tools.update_inventory("Chicken thighs", "add", quantity="1 lb", location="freezer")
    frozen = _rows("chicken thighs")[0]
    _, line = _plan_and_list("chicken thighs", "2 lbs")
    _tick(signed_in, line, "purchased")

    assert _tick(signed_in, line, "needed")["inventory_restored"] is True

    assert _rows("chicken thighs") == [frozen]


def test_a_fridge_row_beside_a_newer_frozen_pack_is_the_one_bought_onto(signed_in):
    tools.update_inventory("Chicken thighs", "add", quantity="1 lb", location="fridge")
    tools.update_inventory("Chicken thighs", "add", quantity="1 lb", location="freezer")
    _, line = _plan_and_list("chicken thighs", "2 lbs")

    _tick(signed_in, line, "purchased")

    by_place = {r["location"]: r["quantity"] for r in _rows("chicken thighs")}
    assert by_place == {"fridge": "3 lbs", "freezer": "1 lb"}


def test_cooking_after_the_shop_takes_the_fresh_chicken_once(signed_in):
    tools.update_inventory("Chicken thighs", "add", quantity="1 lb", location="freezer")
    entry, line = _plan_and_list("chicken thighs", "2 lbs")
    _tick(signed_in, line, "purchased")

    for _ in range(2):
        assert signed_in.post("/api/cooker/check-meal", json={"entry_id": entry, "status": "done"}).status_code == 200

    assert [(r["location"], r["quantity"]) for r in _rows("chicken thighs")] == [("freezer", "1 lb")]


def test_a_frozen_bag_still_joins_the_frozen_bag(signed_in):
    tools.update_inventory("Frozen peas", "add", quantity="1 bag", location="freezer")
    _, line = _plan_and_list("frozen peas", "1 bag")

    _tick(signed_in, line, "purchased")

    assert [(r["location"], r["quantity"]) for r in _rows("frozen peas")] == [("freezer", "2 bags")]
