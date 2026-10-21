"""
A gluten / wheat / celiac restriction holds back pretzels and crackers
(defect hunt, 2026-10-21, found by the snacks builder).

The wheat word list covered pasta shapes, breads and noodles but not the
bought snacks whose wheat is never written as an ingredient: "Apple slices
with cheddar and pretzels" passed the hard gate for a gluten-free child.
"""
import pytest

from app import tools
from app.tools import allergen_gate


@pytest.fixture
def maya():
    tools.add_member("Maya")


@pytest.mark.parametrize("restriction", ["gluten", "wheat", "Celiac"])
@pytest.mark.parametrize("dish, item", [
    ("Apple slices with cheddar and pretzels", "pretzels"),
    ("Soup and crackers", "saltine crackers"),
    ("Yogurt with graham crackers", "graham crackers"),
    ("Bruschetta", "sliced baguette"),
    ("Grilled Cheese", "ciabatta"),
    ("Matzo Ball Soup", "matzo meal"),
])
def test_a_gluten_restriction_holds_back_everyday_wheat_snacks(maya, restriction, dish, item):
    tools.set_member_dietary_restrictions("Maya", [restriction])

    clashes = allergen_gate.hard_clashes(dish, [{"item": item, "qty": "1 cup"}])

    assert clashes, f"{dish} ({item}) passed the gate for a {restriction!r} restriction"
    assert clashes[0]["member"] == "Maya"


def test_the_dish_name_alone_holds_back_pretzels(maya):
    """A snack planned on its name with nothing listed still trips."""
    tools.set_member_dietary_restrictions("Maya", ["gluten"])

    assert allergen_gate.hard_clashes("Apple slices with cheddar and pretzels", [])


@pytest.mark.parametrize("item", ["gluten-free pretzels", "GF crackers", "gluten free graham crackers"])
def test_a_gluten_free_line_is_believed_the_same_way_as_gf_pasta(maya, item):
    tools.set_member_dietary_restrictions("Maya", ["gluten"])

    assert allergen_gate.hard_clashes("Snack plate", [{"item": item, "qty": "1 cup"}]) == []


def test_a_nut_allergy_is_not_tripped_by_pretzels(maya):
    tools.set_member_dietary_restrictions("Maya", ["nuts"])

    assert allergen_gate.hard_clashes("Snack plate", [{"item": "pretzels", "qty": "1 cup"}]) == []


def test_a_guest_named_graham_is_still_a_name_not_a_food():
    """Review: "graham" in the wheat list made big_meal keep it in "no nuts
    for Graham", and the nut rule became the phrase "nuts graham"."""
    from app.tools import big_meal

    assert big_meal.dish_conflicts("Pesto Pasta", [{"item": "basil pesto", "qty": "1 cup"}], "no nuts for Graham")
