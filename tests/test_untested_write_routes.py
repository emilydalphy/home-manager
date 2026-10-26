"""
Write routes no test drove over HTTP (overnight route sweep, 2026-10-18).

`POST /api/inventory/update` and the four `/api/inventory/{item_id}/...`
routes are the Inventory screen's add form and its item sheet's steppers,
location picker and delete. Their tools are tested directly and
test_isolation_attack throws household A's ids at them from B, but nothing
put a request through the routes themselves, so a broken body model, a
renamed field or a wrong except order would have shipped green.
(The other write route the sweep found, before-shop/regulars-undo, is
pinned on branch overnight/regulars-undo-route-pinned.)
"""
from __future__ import annotations

from conftest import household_date

from app import tools


def _inventory_row(name):
    for section in tools.get_inventory_by_section()["sections"]:
        for it in section["items"]:
            if it["item"] == name:
                return it
    return None


def test_inventory_item_sheet_routes_change_the_row_they_name(signed_in):
    res = signed_in.post(
        "/api/inventory/update",
        json={"item": "Yogurt", "action": "add", "quantity": "2", "category": "dairy",
              "location": "fridge", "expiration_date": household_date(3)},
    )
    assert res.status_code == 200
    item_id = _inventory_row("Yogurt")["id"]

    q = signed_in.post(f"/api/inventory/{item_id}/quantity", json={"delta": 1}).json()
    assert q == {"item_id": item_id, "found": True, "quantity": "3"}

    e = signed_in.post(f"/api/inventory/{item_id}/expiration", json={"delta_days": -1}).json()
    assert e["expiration_date"] == household_date(2)

    loc = signed_in.post(f"/api/inventory/{item_id}/location", json={"location": "freezer"})
    # Into the freezer restarts the date on the frozen shelf life (2026-10-25,
    # tests/test_inventory_hunt_2026_10_25.py).
    assert loc.json() == {"item_id": item_id, "location": "freezer", "expiration_date": household_date(90)}
    by_loc = signed_in.get("/api/inventory/by-location").json()["locations"]
    assert [b["location"] for b in by_loc if any(i["id"] == item_id for i in b["items"])] == ["freezer"]

    gone = signed_in.post(f"/api/inventory/{item_id}/remove")
    assert gone.status_code == 200
    assert _inventory_row("Yogurt") is None


def test_inventory_item_sheet_routes_on_a_missing_id(signed_in):
    """A stale sheet (the item was removed elsewhere) gets a 404 or a
    found=False, never a 500 and never a new row."""
    missing = 987654
    assert signed_in.post(f"/api/inventory/{missing}/remove").status_code == 404
    assert signed_in.post(
        f"/api/inventory/{missing}/location", json={"location": "pantry"}
    ).status_code == 404
    assert signed_in.post(
        f"/api/inventory/{missing}/quantity", json={"delta": 1}
    ).json() == {"item_id": missing, "found": False}
    assert signed_in.post(
        f"/api/inventory/{missing}/expiration", json={"delta_days": 1}
    ).json() == {"item_id": missing, "found": False}
    assert tools.get_inventory_by_section()["sections"] == []
