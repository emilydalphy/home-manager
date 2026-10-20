"""
An add that merges onto a line already on the list keeps that line's
aisle (2026-10-20, branch overnight/merge-keeps-the-aisle).

`add_grocery_item` used to overwrite the merged row's category with the
incoming one, and the incoming one is usually a guess: the shop sheet's
groGuessCategory ('other' for any word it doesn't know) or chat's
add_grocery_items, which files a bare name under 'other'. So a recipe's
"Orzo · pantry" jumped to Other the moment someone typed "orzo", and
Put back (which restores amount and store only) left it there. Found
driving /api/grocery-list/add on a running app; it was characterised in
tests/test_put_back_merged_line.py as a known open defect.
"""
from __future__ import annotations

from app import tools


def _lines():
    return [(r["item"], r["quantity"], r["category"]) for r in tools.list_grocery_list()]


def test_a_typed_add_guessed_other_leaves_the_line_in_its_aisle(signed_in):
    signed_in.post("/api/grocery-list/add", json={"item": "Orzo", "quantity": "1 box", "category": "pantry"})
    r = signed_in.post("/api/grocery-list/add", json={"item": "orzo", "quantity": "1 box", "category": "other"})
    assert r.status_code == 200 and r.json()["merged"] is True
    sections = signed_in.get("/api/grocery-list").json()["sections"]
    assert [(s["section"], [i["item"] for i in s["items"]]) for s in sections] == [("pantry", ["Orzo"])]
    assert _lines() == [("Orzo", "2 boxes", "pantry")]


def test_chat_adding_a_bare_name_does_not_move_the_plan_line():
    """Chat's add_grocery_items with a plain string files it as 'other'."""
    tools.add_grocery_item("Milk", "1 l", "dairy", source_weekly_plan_id=None)
    tools.add_grocery_items(["milk"])
    assert _lines() == [("Milk", "1 L", "dairy")]


def test_a_line_with_no_aisle_takes_the_one_the_add_knows():
    """The other direction still works: a line filed under Other picks up
    a real aisle from the add that joins it."""
    tools.add_grocery_item("Cheddar", "1 block", "other")
    tools.add_grocery_item("cheddar", "1 block", "dairy")
    assert _lines() == [("Cheddar", "2 blocks", "dairy")]


def test_a_line_shown_under_other_by_an_unknown_word_takes_a_real_aisle():
    """Review 2026-10-20: a recipe can file a line under a word no section
    knows ("bakery"); the list shows it under Other, so it is a line with
    no aisle and an add bringing one fills it. A known alias ("meat") is
    a real aisle and stays."""
    tools.add_grocery_item("Bagels", "1 bag", "bakery")
    tools.add_grocery_item("bagels", "1 bag", "pantry")
    tools.add_grocery_item("Ground beef", "1 lb", "meat")
    tools.add_grocery_item("ground beef", "1 lb", "other")
    assert dict((item, cat) for item, _qty, cat in _lines()) == {"Bagels": "pantry", "Ground beef": "meat"}
