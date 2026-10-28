"""
Defect hunt, 2026-10-28 — adding food to the Kitchen tab and taking it out.

1. A receipt's "Eggs 12 pack" keyed as "eggs pack" (the number went, the
   word stayed), so it made a second Eggs row beside the first.
2. The Kitchen tab read a decimal or mixed-number total as a fraction:
   3.25 lbs of chicken said "almost gone", 1.5 L of milk "about half".
3. Undo on "marked eaten", for a row changed since, joined the amount back
   on as "2 lbs + 12 oz" — unparseable, so the next dinner took nothing.
4. The add toast said "Eggs was added to the fridge."
"""
from __future__ import annotations

from pathlib import Path

import pytest

from app import tools
from app.db import get_conn
from app.tools import eaten_receipt, whats_home



def _rows():
    conn = get_conn()
    try:
        return [dict(r) for r in conn.execute(
            "SELECT id, item, quantity, location FROM inventory_items ORDER BY id")]
    finally:
        conn.close()


# ---------- 1. a pack is the food it holds ----------

def test_a_receipts_pack_lands_on_the_tracked_row_as_a_count(signed_in):
    signed_in.post("/api/whats-home/add", json={"kind": "type", "items": [{"item": "12 eggs", "location": "fridge"}]})
    res = signed_in.post("/api/whats-home/add", json={"kind": "receipt", "items": [
        {"item": "Eggs 12 pack", "quantity": "1", "location": "fridge"}]})
    assert res.status_code == 200
    assert [(r["item"], r["quantity"]) for r in _rows()] == [("Eggs", "24")]


def test_a_pack_with_nothing_tracked_is_saved_as_the_food_and_how_many(signed_in):
    signed_in.post("/api/whats-home/add", json={"kind": "receipt", "items": [
        {"item": "Yogurt 6-pack", "quantity": "2", "location": "fridge"}]})
    assert [(r["item"], r["quantity"]) for r in _rows()] == [("Yogurt", "12")]


def test_a_pack_with_an_amount_in_words_keeps_its_amount_but_still_merges(signed_in):
    """GUARD: only a plain count is multiplied; "2 cartons" stays as said."""
    signed_in.post("/api/whats-home/add", json={"kind": "type", "items": [{"item": "Eggs", "location": "fridge"}]})
    signed_in.post("/api/whats-home/add", json={"kind": "receipt", "items": [
        {"item": "Eggs 12 pack", "quantity": "2 cartons", "location": "fridge"}]})
    assert [r["item"] for r in _rows()] == ["Eggs"]


def test_different_cuts_still_stay_apart(signed_in):
    """GUARD: the pack rule never collapses two foods."""
    signed_in.post("/api/whats-home/add", json={"kind": "type", "items": [{"item": "1 lb chicken thighs", "location": "fridge"}]})
    signed_in.post("/api/whats-home/add", json={"kind": "receipt", "items": [
        {"item": "Chicken breast 4 pack", "quantity": "1", "location": "fridge"}]})
    assert sorted(r["item"] for r in _rows()) == ["Chicken breast", "Chicken thighs"]


# ---------- 2. a total is not a fraction ----------

@pytest.mark.parametrize("qty", ["3.25 lbs", "1.5 L", "1 1/2 cups", "1 1/4 cups", "1½ cups", "10.5 oz"])
def test_a_whole_number_and_a_fraction_reads_plenty(qty):
    assert whats_home.amount_level({"quantity": qty}) == "plenty"


@pytest.mark.parametrize("qty,level", [("½ bag", "half"), ("0.5 kg", "half"), ("1/2 cup", "half"),
                                       ("¼ cup", "almost_gone"), ("0.25 kg", "almost_gone"),
                                       ("a little", "almost_gone")])
def test_a_fraction_on_its_own_still_reads_as_one(qty, level):
    """GUARD."""
    assert whats_home.amount_level({"quantity": qty}) == level


# ---------- 3. Undo puts the amount back in the row's own unit ----------

def test_undo_onto_a_changed_row_adds_up_instead_of_joining():
    tools.update_inventory("Chicken thighs", "add", quantity="2 lbs", category="meat/seafood", location="fridge")
    row = _rows()[0]
    conn = get_conn()
    before = dict(conn.execute("SELECT * FROM inventory_items WHERE id = ?", (row["id"],)).fetchone())
    conn.close()
    # The row moved on since the tick (its rev is not the receipt's).
    eaten_receipt._put_back({"before": before, "taken": "12 oz", "after_rev": -1})
    assert _rows()[0]["quantity"] == "2.75 lbs"
