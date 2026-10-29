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

import pytest

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
    # Nor a package: a recipe's bare "8" tortillas is not eight packs.
    assert tools.inventory._try_subtract_quantity("1 pack", "8") == ("1 pack", False)
    # A plain noun is: a bare "2" off "3 lemons" is two lemons.
    assert tools.inventory._try_subtract_quantity("3 lemons", "2") == ("1 lemon", True)




@pytest.mark.parametrize("have, used", [
    ("1 pack tortillas", "8"), ("1 head garlic", "2"), ("1 stick butter", "2"),
    ("1 bunch cilantro", "1"), ("1 bag spinach", "1"), ("1 loaf bread", "2"),
    ("1 carton eggs", "6"), ("1 pkg", "8"), ("1 sleeve", "8"),
    ("2 cans tomatoes", "1"), ("3 heads garlic", "1"), ("6 cloves garlic", "1"),
])
def test_a_bare_number_off_a_package_or_piece_is_left_alone(have, used):
    # Review, 2026-10-29: a package word in front of the food ("1 pack
    # tortillas") was read as a plain noun, and cooking "8" deleted the row.
    assert tools.inventory._try_subtract_quantity(have, used) == (have, False)


def test_a_cooked_bare_count_never_deletes_a_package_row():
    tools.update_inventory("Tortillas", "add", quantity="1 pack tortillas", location="pantry")
    tools.update_inventory("Tortillas", "use", quantity="8")
    assert _qty("Tortillas") == "1 pack tortillas"


def test_a_singular_noun_is_pluralised_when_the_count_grows():
    assert tools.inventory._try_subtract_quantity("4", "1 onion") == ("3 onions", True)
    assert tools.inventory._merged_quantity("1 tomato", "2") == "3 tomatoes"


# ---------------------------------------------------------- 2. chat batch

def test_a_numeric_quantity_in_the_batch_is_read_as_its_number():
    tools.update_inventory_items([{"item": "Eggs", "quantity": 6, "location": "fridge"}])
    tools.update_inventory_items([{"item": "Eggs", "quantity": 6, "location": "fridge"}])
    assert _qty("Eggs") == "12"
    out = tools.update_inventory_items([{"item": "Eggs", "action": "use", "quantity": 2}])
    assert out["updated"][0]["quantity"] == "10"


def test_one_bad_entry_is_that_entrys_answer_and_the_rest_are_written_once():
    out = tools.update_inventory_items([
        {"item": "Rice", "quantity": "2 cups"},
        {"item": "Milk", "action": "used"},
        {"item": "Yogurt", "quantity": "1 tub", "expiration_date": "Friday"},
        {"item": "Butter", "quantity": True},
        7,
    ])
    errors = [r for r in out["updated"] if "error" in r]
    assert [r["item"] for r in errors] == ["Milk", "Yogurt", "Butter"]
    assert _qty("Rice") == "2 cups"
    assert _qty("Yogurt") is None and _qty("Butter") is None


def test_an_iso_date_is_kept_and_a_bad_one_is_a_400_on_the_form(signed_in):
    tools.update_inventory("Yogurt", "add", quantity="1 tub", expiration_date="2026-11-03T00:00:00", location="fridge")
    assert [r for r in tools.get_inventory() if r["item"] == "Yogurt"][0]["expiration_date"] == "2026-11-03"
    res = signed_in.post("/api/inventory/update", json={"item": "Cream", "action": "add", "expiration_date": "next week"})
    assert res.status_code == 400
    assert _qty("Cream") is None
    # A use never needs a date, so a stray one doesn't block it.
    assert tools.update_inventory("Yogurt", "use", expiration_date="whenever").get("removed") is True
