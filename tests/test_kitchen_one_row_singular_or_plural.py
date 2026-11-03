"""
A Shop line bought over a Kitchen row of the same food under the other
number lands on that row (defect hunt, 2026-11-03).

inventory._find_row_by_name matched a name exactly (case aside), while
the Kitchen's own add sheet and Shop merge on the grocery key, which
forgives a trailing plural. So "Chicken thigh · 1 lb" ticked on Shop over
the fridge's "Chicken thighs · 2 lbs" became a second row, and Kitchen
listed "Chicken thigh" and "Chicken thighs" side by side; chat's "used an
onion" found no "Onions" and changed nothing.
"""
from __future__ import annotations

from app import tools


def _rows() -> list[tuple[str, str, str]]:
    return sorted((i["item"], i["quantity"], i["location"]) for i in tools.get_inventory())


def test_a_bought_singular_lands_on_the_plural_row_and_its_untick_takes_it_back():
    tools.update_inventory("Chicken thighs", "add", quantity="2 lbs", location="fridge")
    line = tools.add_grocery_item("Chicken thigh", quantity="1 lb", category="meat/seafood")
    tools.mark_grocery_item(line["item_id"], "purchased")
    assert _rows() == [("Chicken thighs", "3 lbs", "fridge")]  # was two rows
    tools.mark_grocery_item(line["item_id"], "needed")
    assert _rows() == [("Chicken thighs", "2 lbs", "fridge")]


def test_used_an_onion_comes_off_onions():
    tools.update_inventory("Onions", "add", quantity="3", location="pantry")
    tools.update_inventory("onion", "use", quantity="1")
    assert _rows() == [("Onions", "2", "pantry")]  # was "3", nothing found


def test_an_exact_name_still_wins_over_a_near_one():
    """GUARD: the near match is a fallback, never ahead of the name as typed."""
    tools.update_inventory("Egg", "add", quantity="6", location="fridge")
    tools.update_inventory("Eggs", "add", quantity="4", location="pantry")  # a second row, by shelf
    tools.update_inventory("Eggs", "use", quantity="1")
    assert _rows() == [("Egg", "6", "fridge"), ("Eggs", "3", "pantry")]


def test_already_have_onto_a_plural_row_is_not_deleted_by_its_undo():
    """The already-have's own "was it fresh" look-up was exact too: once
    the merge matched "Onion" onto "Onions", it recorded the household's
    row as fresh, and the Undo deleted all their onions."""
    tools.update_inventory("Onions", "add", quantity="3", location="pantry")
    line = tools.add_grocery_item("Onion", quantity="2", category="produce")
    tools.move_grocery_item_to_inventory(line["item_id"])
    assert _rows() == [("Onions", "5", "pantry")]
    tools.undo_pre_shop_drop(line["item_id"])
    assert [r[0] for r in _rows()] == ["Onions"]
