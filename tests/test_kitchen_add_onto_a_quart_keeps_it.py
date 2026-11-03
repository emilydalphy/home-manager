"""
What is put onto a Kitchen row in quarts or gallons stays on it (defect
hunt, 2026-11-02).

inventory._big_volume_text reads a big volume DOWN to whole cups — right
for what a dinner takes off ("a tbsp off a gallon is 3 3/4 quarts"), and
it was used for adds too. So "1/2 cup" of cream onto "1 quart" read "1
quart", three 3/4 cups onto it still "1 quart", and using a quart then
deleted a row that held 2 1/4 cups. An add now keeps a measuring step past
the whole cups ("4 1/2 cups"); spoons still floor away.
"""
from __future__ import annotations

from app import tools


def _kitchen(name: str) -> list[str]:
    return [i["quantity"] for i in tools.get_inventory() if i["item"].lower() == name.lower()]


def test_part_cups_added_onto_a_quart_are_counted_and_a_quart_used_leaves_them():
    tools.update_inventory("Heavy cream", "add", quantity="1 quart")
    for _ in range(3):
        tools.update_inventory("Heavy cream", "add", quantity="3/4 cup")
    assert _kitchen("Heavy cream") == ["6 1/4 cups"]  # was "1 quart" all three times
    tools.update_inventory("Heavy cream", "use", quantity="1 quart")
    assert _kitchen("Heavy cream") == ["2 1/4 cups"]  # was: the row deleted


def test_a_quart_bought_onto_a_half_cup_left_counts_the_half_cup():
    tools.update_inventory("Chicken broth", "add", quantity="1/2 cup")
    line = tools.add_grocery_item("Chicken broth", quantity="1 quart", category="pantry")
    tools.mark_grocery_item(line["item_id"], "purchased")
    assert _kitchen("Chicken broth") == ["4 1/2 cups"]  # was "1 quart"


def test_whole_cups_and_spoons_onto_a_quart_read_as_before():
    """GUARD: whole cups still read in quarts; a spoonful still floors."""
    tools.update_inventory("Milk", "add", quantity="1 quart")
    tools.update_inventory("Milk", "add", quantity="2 cups")
    assert _kitchen("Milk") == ["1 1/2 quarts"]
    tools.update_inventory("Milk", "add", quantity="1 tbsp")
    assert _kitchen("Milk") == ["1 1/2 quarts"]


def test_a_cup_used_off_a_quart_and_a_half_cup_reads_on_a_cup_step():
    """Review of the fix: the cups row it writes then lost a cup as "3.5 cups"."""
    tools.update_inventory("Milk", "add", quantity="1 quart")
    tools.update_inventory("Milk", "add", quantity="1/2 cup")
    tools.update_inventory("Milk", "use", quantity="1 cup")
    assert _kitchen("Milk") == ["3 1/2 cups"]


def test_a_cup_used_off_a_gallon_and_a_third_reads_on_a_cup_step():
    tools.update_inventory("Milk", "add", quantity="1 gallon")
    tools.update_inventory("Milk", "add", quantity="1/3 cup")
    assert _kitchen("Milk") == ["16 1/3 cups"]
    tools.update_inventory("Milk", "use", quantity="1 cup")
    assert _kitchen("Milk") == ["15 1/3 cups"]  # review: "15.3333 cups"
    tools.update_inventory("Milk", "use", quantity="1 tbsp")
    assert _kitchen("Milk") == ["15 1/4 cups"]  # down to the step, never "245 tbsp"
