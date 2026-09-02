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
    ("Egg", "Eggs"),            # on almost every list
    ("Cookie", "Cookies"),      # cookie+s, not "cooky"
    ("Quiche", "Quiches"),      # quiche+s, not "quich"
    ("Onion", "Onions"),
    # Qualified names are unambiguous, so they merge even when the bare
    # noun would not.
    ("Bell pepper", "Bell peppers"),
    ("Chocolate chip", "Chocolate chips"),
])
def test_singular_and_plural_are_the_same_item(a, b):
    tools.add_grocery_item(a, quantity="2")
    tools.add_grocery_item(b, quantity="3")

    items = tools.list_grocery_list()
    assert len(items) == 1, f"{a!r} and {b!r} should be one line, got {[i['item'] for i in items]}"


@pytest.mark.parametrize("a,b", [
    # The dangerous ones: a bare noun whose plural is a different product.
    # "Pepper" is what's in the cupboard; "Peppers" are in the fridge.
    # Merging those files a pantry staple under Produce and it never gets
    # bought — silently, which is the failure this must never cause.
    ("Pepper", "Peppers"),
    ("Green", "Greens"),
    ("Chili", "Chilis"),
    ("Chip", "Chips"),
    ("Ground", "Grounds"),
    # And names that simply aren't the same thing.
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


def test_a_plan_cannot_take_over_something_you_asked_for_yourself():
    """
    An item added by hand has no plan attached, and clear_stale_grocery_items
    is required to leave those alone forever. If a merge stamped this
    week's plan onto it, generating next week would delete a standing want
    the household had explicitly added — and the plural matching makes that
    collision far more likely than an exact-name one did.
    """
    tools.add_grocery_item("Bell pepper", quantity="2")          # by hand
    tools.add_grocery_item("Bell peppers", quantity="3", source_weekly_plan_id=99)

    conn = get_conn()
    row = conn.execute(
        "SELECT item, source_weekly_plan_id FROM grocery_items WHERE household_id = 1"
    ).fetchone()
    conn.close()

    assert row["source_weekly_plan_id"] is None, (
        "a plan may add quantity to a hand-added item, but must not take ownership of it"
    )


def test_a_store_preference_still_applies_after_a_merge():
    """
    The preference is saved under the name the household said; the line
    keeps whichever name got there first. Looking them up differently
    meant the app confirmed a preference that then never took effect.
    """
    tools.add_grocery_item("Bell pepper", quantity="1")
    tools.set_item_store("bell peppers", "Costco")

    tools.add_grocery_item("Bell peppers", quantity="2")

    conn = get_conn()
    stores = [r["store"] for r in conn.execute(
        "SELECT store FROM grocery_items WHERE household_id = 1"
    ).fetchall()]
    conn.close()
    assert stores == ["Costco"], f"the preference should reach the merged line, got {stores}"


def test_consolidation_never_hides_a_visible_line_inside_an_excluded_one():
    """
    An excluded row is deliberately hidden ("we get those at the market").
    Folding a visible line into a hidden one made the visible line vanish
    and parked its quantity where nobody can see it.
    """
    conn = get_conn()
    conn.execute(
        "INSERT INTO grocery_items (household_id, item, quantity, category, status, excluded_from_list) "
        "VALUES (1, 'Bell pepper', '2', 'produce', 'needed', 1)"
    )
    conn.execute(
        "INSERT INTO grocery_items (household_id, item, quantity, category, status, excluded_from_list) "
        "VALUES (1, 'Bell peppers', '3', 'produce', 'needed', 0)"
    )
    conn.commit()
    conn.close()

    visible_before = [i["item"] for i in tools.list_grocery_list()]
    tools.consolidate_grocery_list()
    visible_after = [i["item"] for i in tools.list_grocery_list()]

    assert visible_after == visible_before, (
        "consolidation must not make a visible line disappear into a hidden one"
    )
