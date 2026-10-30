"""
A pre-shop Undo only undoes a drop (defect hunt, 2026-10-30).

/pre-shop-undo put ANY line back to 'needed', whatever it was. After the
line was already back — and then bought — a second Undo (a double tap, or
the other adult's phone still showing "Already had on hand") turned the
bought line back into one to buy, with the food already in the kitchen.
"""
from __future__ import annotations

from app import tools


def _status(item_id: int) -> str:
    (row,) = [r for r in tools.list_grocery_list(status="all") if r["id"] == item_id]
    return row["status"]


def test_undo_after_the_line_was_put_back_and_bought_leaves_it_bought(signed_in):
    c = signed_in
    item_id = c.post("/api/grocery-list/add", json={"item": "Greek yogurt", "quantity": "1 tub",
                                                    "category": "dairy"}).json()["item_id"]
    assert c.post(f"/api/grocery-list/{item_id}/pre-shop", json={"decision": "drop"}).status_code == 200
    assert c.post(f"/api/grocery-list/{item_id}/pre-shop-undo").json()["status"] == "needed"
    c.post(f"/api/grocery-list/{item_id}/status", json={"status": "purchased"})

    r = c.post(f"/api/grocery-list/{item_id}/pre-shop-undo")
    assert r.status_code == 200
    assert r.json()["status"] == "purchased"
    assert _status(item_id) == "purchased"


def test_undo_still_puts_a_dropped_line_back(signed_in):
    c = signed_in
    item_id = c.post("/api/grocery-list/add", json={"item": "Feta", "quantity": "200 g",
                                                    "category": "dairy"}).json()["item_id"]
    c.post(f"/api/grocery-list/{item_id}/pre-shop", json={"decision": "drop"})
    assert _status(item_id) == "removed"
    assert c.post(f"/api/grocery-list/{item_id}/pre-shop-undo").json()["status"] == "needed"
    assert _status(item_id) == "needed"
