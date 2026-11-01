"""
What the grocery row's amount box (and the add sheet's) accepts (Emily,
2026-10-31, mixed): "x2" quietly reads as 2 — it's how people write it; a
zero, a negative or an amount over 40 characters is refused with "That
amount doesn't look right". Before: editing took 300 characters, "-1" and
"0" saved when adding and editing, and "x2" added twice read "x2 ×2".
"""
from __future__ import annotations

import pytest

from app import tools
from app.db import get_conn

REFUSED = "That amount doesn't look right."


def _qty(item_id: int) -> str:
    conn = get_conn()
    try:
        return conn.execute("SELECT quantity FROM grocery_items WHERE id = ?", (item_id,)).fetchone()["quantity"]
    finally:
        conn.close()


@pytest.mark.parametrize("typed", ["x2", "X2", "×2", "x 2", "2x"])
def test_x2_reads_as_two(signed_in, typed):
    res = signed_in.post("/api/grocery-list/add", json={"item": "Lemons", "quantity": typed})
    assert res.status_code == 200, res.text
    assert _qty(res.json()["item_id"]) == "2"


def test_x2_added_twice_is_four_not_x2_x2(signed_in):
    first = signed_in.post("/api/grocery-list/add", json={"item": "Lemons", "quantity": "x2"}).json()
    second = signed_in.post("/api/grocery-list/add", json={"item": "Lemons", "quantity": "x2"}).json()
    assert first["item_id"] == second["item_id"]
    assert _qty(first["item_id"]) == "4"


@pytest.mark.parametrize("typed", ["0", "-1", "0 cups", "-2 lb", "−1", "x0", "a" * 41])
def test_zero_negative_and_long_amounts_are_refused_when_adding(signed_in, typed):
    res = signed_in.post("/api/grocery-list/add", json={"item": "Lemons", "quantity": typed})
    assert res.status_code == 400
    assert res.json()["detail"] == REFUSED
    assert tools.list_grocery_list(status="needed") == []


@pytest.mark.parametrize("typed", ["0", "-1", "a" * 300])
def test_zero_negative_and_long_amounts_are_refused_when_editing_a_row(signed_in, typed):
    item_id = tools.add_grocery_item("Lemons", quantity="3")["item_id"]
    res = signed_in.post(f"/api/grocery-list/{item_id}/update", json={"quantity": typed})
    assert res.status_code == 400
    assert res.json()["detail"] == REFUSED
    assert _qty(item_id) == "3", "a refused edit changes nothing"


def test_editing_a_row_to_x3_saves_three(signed_in):
    item_id = tools.add_grocery_item("Lemons", quantity="1")["item_id"]
    res = signed_in.post(f"/api/grocery-list/{item_id}/update", json={"quantity": "x3"})
    assert res.status_code == 200, res.text
    assert _qty(item_id) == "3"


@pytest.mark.parametrize("typed", ["", "2", "1 1/2 lb", "1 (14 oz) can", "a bunch", "a" * 40, "2 x 400 g"])
def test_ordinary_amounts_still_save(signed_in, typed):
    res = signed_in.post("/api/grocery-list/add", json={"item": "Lemons", "quantity": typed})
    assert res.status_code == 200, res.text


def test_a_category_only_edit_is_not_held_to_the_amount_rule(signed_in):
    item_id = tools.add_grocery_item("Lemons", quantity="3")["item_id"]
    res = signed_in.post(f"/api/grocery-list/{item_id}/update", json={"category": "produce"})
    assert res.status_code == 200, res.text
