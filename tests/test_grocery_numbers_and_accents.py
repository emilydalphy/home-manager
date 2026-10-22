"""
Two hand-add defects on the grocery list (defect hunt, 2026-10-22).

1. A comma between digits is part of the number. "1,000 g" of flour was
   read as "1" (the comma taken for a "3, diced" prep note) and the next
   "500 g" made the line "1 + 500 g"; a French-Canadian "1,5 kg" read "1".
2. An accent does not make a second thing to buy. "Jalapeño" and
   "jalapeno" were two lines; the merge key now folds accents, the line
   keeps the name as written.
"""
from __future__ import annotations

import pytest
from conftest import household_date

from app import tools
from app.db import get_conn


def _lines(*names: str) -> list[tuple[str, str]]:
    return [(i["item"], i["quantity"]) for i in tools.list_grocery_list() if i["item"] in names]


@pytest.mark.parametrize("typed,shown", [
    ("1,000 g", "1 kg"),
    ("1,5 kg", "1.5 kg"),
    ("2,500 ml", "2.5 L"),
    ("1 can (14,5 oz)", "1 can (14.5 oz)"),
    ("3, diced", "3"),                       # a prep comma is still a prep comma
])
def test_a_comma_inside_a_number_is_part_of_it(typed, shown):
    tools.add_grocery_item("Flour", quantity=typed, category="pantry")
    assert _lines("Flour") == [("Flour", shown)]


def test_a_thousand_grams_then_five_hundred_add_up_over_the_route(signed_in):
    signed_in.post("/api/grocery-list/add", json={"item": "Flour", "quantity": "1,000 g"})
    res = signed_in.post("/api/grocery-list/add", json={"item": "flour", "quantity": "500 g"})
    assert res.status_code == 200
    assert res.json()["quantity"] == "1.5 kg"
    assert _lines("Flour") == [("Flour", "1.5 kg")]


def test_an_accent_is_the_same_thing_to_buy(signed_in):
    signed_in.post("/api/grocery-list/add", json={"item": "Jalapeño", "quantity": "2"})
    res = signed_in.post("/api/grocery-list/add", json={"item": "jalapenos", "quantity": "1"})
    assert res.json()["merged"] is True
    assert _lines("Jalapeño", "jalapenos") == [("Jalapeño", "3")]


def test_two_recipes_spelling_it_both_ways_are_one_plan_line():
    conn = get_conn()
    plan_id = conn.execute(
        "INSERT INTO weekly_plans (household_id, week_start_date, status) VALUES (1, ?, 'approved')",
        (household_date(0),),
    ).lastrowid
    conn.commit()
    conn.close()
    tools.add_grocery_item("Crème fraîche", quantity="1 tub", category="dairy", source_weekly_plan_id=plan_id)
    tools.add_grocery_item("creme fraiche", quantity="1 tub", category="dairy", source_weekly_plan_id=plan_id)
    lines = [i for i in tools.list_grocery_list() if "fra" in i["item"].lower()]
    assert len(lines) == 1, lines
    assert lines[0]["quantity"] == "2 tubs"


def test_different_letters_still_stay_apart():
    tools.add_grocery_item("Pâté", quantity="1", category="meat/seafood")
    tools.add_grocery_item("Paté", quantity="1", category="meat/seafood")
    tools.add_grocery_item("Pita", quantity="1", category="pantry")
    assert len(_lines("Pâté", "Paté", "Pita")) == 2
