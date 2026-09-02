"""
The same item, written singular in one place and plural in another, is
one line on the shopping list.

"Bell pepper" and "Bell peppers" used to sit on the list as two separate
entries with quantities that never combined — which reads to whoever is
shopping as two different things to buy.
"""
import pytest

from app import tools
from app.db import get_conn
from app.tools import grocery


@pytest.mark.parametrize("a,b", [
    ("Bell pepper", "Bell peppers"),
    ("Tomato", "Tomatoes"),
    ("Potato", "Potatoes"),
    ("Berry", "Berries"),
    ("Peach", "Peaches"),
    ("carrot", "Carrots"),
    ("Chicken thigh", "chicken thighs"),
])
def test_singular_and_plural_are_the_same_item(a, b):
    tools.add_grocery_item(a, quantity="2")
    tools.add_grocery_item(b, quantity="3")

    items = tools.list_grocery_list()
    assert len(items) == 1, f"{a!r} and {b!r} should be one line, got {[i['item'] for i in items]}"


@pytest.mark.parametrize("a,b", [
    ("Chicken breast", "Chicken broth"),
    ("Green beans", "Green onions"),
    ("Cream", "Creamer"),
    ("Oat milk", "Oats"),
])
def test_genuinely_different_items_stay_separate(a, b):
    """
    A wrong merge is worse than a duplicate line: a duplicate is visible
    and annoying, a silent merge means something never gets bought.
    """
    tools.add_grocery_item(a)
    tools.add_grocery_item(b)

    assert len(tools.list_grocery_list()) == 2, f"{a!r} and {b!r} are different things"


def test_the_line_keeps_the_name_already_on_the_list():
    tools.add_grocery_item("Bell peppers", quantity="2")
    result = tools.add_grocery_item("bell pepper", quantity="1")

    items = tools.list_grocery_list()
    assert items[0]["item"] == "Bell peppers", "the existing line keeps its own wording"
    assert result["item"] == "Bell peppers", "and that's the name reported back"
    assert result["merged"] is True


def test_quantities_combine_across_the_plural_split():
    tools.add_grocery_item("Carrot", quantity="2 lbs")
    tools.add_grocery_item("Carrots", quantity="3 lbs")

    items = tools.list_grocery_list()
    assert len(items) == 1
    assert "5" in items[0]["quantity"], f"quantities should add up, got {items[0]['quantity']!r}"


def test_cleaning_up_the_list_merges_splits_that_are_already_there():
    """
    Preventing new duplicates doesn't help a list that already has them —
    and every list in use right now does.
    """
    # Inserted straight into the table, bypassing the merge, to build the
    # "before" state a household living with this already has.
    conn = get_conn()
    for name, qty in [("Bell pepper", "2"), ("Bell peppers", "3"),
                      ("Tomato", "1"), ("Tomatoes", "4"), ("Rice", "1 bag")]:
        conn.execute(
            "INSERT INTO grocery_items (household_id, item, quantity, category, status) "
            "VALUES (1, ?, ?, 'produce', 'needed')",
            (name, qty),
        )
    conn.commit()
    conn.close()
    assert len(tools.list_grocery_list()) == 5

    result = tools.consolidate_grocery_list()

    assert result["lines_merged_away"] == 2
    names = sorted(i["item"] for i in tools.list_grocery_list())
    assert names == ["Bell pepper", "Rice", "Tomato"], "the first line of each pair survives"


def test_the_key_itself_behaves():
    assert grocery._merge_key("Bell Peppers") == grocery._merge_key("bell pepper")
    assert grocery._merge_key("  Tomatoes  ") == "tomato"
    assert grocery._merge_key("Berries") == "berry"
    # Short words are left alone — nobody writes "pea" or "oat" on a list.
    assert grocery._merge_key("Peas") == "peas"
    assert grocery._merge_key("Oats") == "oats"
    # Consistent with itself is what matters for an odd word like this.
    assert grocery._merge_key("Asparagus") == grocery._merge_key("asparagus")
