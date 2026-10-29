"""
Kitchen inventory defect hunt, 2026-10-29 (branch
overnight/hunt-inventory-1029b), found driving the Kitchen add, the list
tick and the chat inventory tools on a throwaway database.

1. A bare count and a counted unit never met. Eggs typed in the Kitchen as
   "12 eggs" are kept as "12"; a dozen ticked off the list ("1 dozen")
   merged into "12 + 1 dozen", which nothing parses, so the next "we used
   2 eggs" in chat read the amount as "all of it" and deleted the row.
   "3 apples" less "1 apple" took nothing off.
2. The chat batch tool raised partway through: an entry with a numeric
   quantity ({"quantity": 6}) or an action it doesn't know ("used") left
   the entries before it written, so the assistant's retry added them
   twice. A best-before of "Friday" was stored as given and never read as
   a date again.
"""
from __future__ import annotations

from app import tools


def _qty(item: str) -> str | None:
    rows = [r for r in tools.get_inventory() if r["item"].lower() == item.lower()]
    return rows[0]["quantity"] if rows else None


# ---------------------------------------------------------------- 1. counts

def test_a_dozen_bought_onto_eggs_typed_as_a_count_adds_up(signed_in):
    res = signed_in.post("/api/whats-home/add", json={"kind": "type", "items": [{"item": "12 eggs"}]})
    assert res.status_code == 200, res.text
    line = tools.add_grocery_item("Eggs", "1 dozen", "dairy")
    res = signed_in.post(f"/api/grocery-list/{line['item_id']}/status", json={"status": "purchased"})
    assert res.status_code == 200, res.text
    assert _qty("Eggs") == "2 dozen"

    # And the chat's "we used 2 eggs" takes two off, not the whole row.
    out = tools.update_inventory("Eggs", "use", quantity="2")
    assert out.get("removed") is not True
    assert _qty("Eggs") == "22 eggs"


def test_same_noun_singular_and_plural_subtract_and_merge():
    assert tools.inventory._try_subtract_quantity("3 apples", "1 apple") == ("2 apples", True)
    assert tools.inventory._try_subtract_quantity("2 apples", "1 apple") == ("1 apple", True)
    assert tools.inventory._merged_quantity("1 tomato", "2 tomatoes") == "3 tomatoes"
    # Half a dozen is six eggs, never "0.5 dozen".
    assert tools.inventory._try_subtract_quantity("1 dozen", "6") == ("6 eggs", True)


def test_a_bare_number_against_a_measure_is_still_not_guessed():
    # "2" says nothing about how much of 2 lbs; a garlic "2" could be heads
    # or cloves. Both stay as they were, unreconciled.
    assert tools.inventory._try_subtract_quantity("2 lbs", "1") == ("2 lbs", False)
    assert tools.inventory._try_subtract_quantity("1 head", "2") == ("1 head", False)
    assert tools.inventory._merged_quantity("2 lbs", "2") == "2 lbs + 2"
