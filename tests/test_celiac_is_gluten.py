"""
"Celiac" on someone's dietary restrictions is a gluten avoidance
(defect hunt, 2026-10-20).

A household writes the condition, not the protein: "Celiac", "coeliac
disease". The allergy check matched those as the literal word "celiac",
which no ingredient line ever says — so a child entered as Celiac in
Settings had spaghetti, sourdough and couscous pass the hard gate that
holds a dish back for an allergy. A "gluten" restriction caught all three.
"""
import pytest

from app import tools
from app.tools import allergen_gate


@pytest.fixture
def maya():
    tools.add_member("Maya")


@pytest.mark.parametrize("restriction", ["Celiac", "coeliac", "Celiac disease", "coeliac's disease"])
@pytest.mark.parametrize("dish, item", [
    ("Spaghetti Bolognese", "spaghetti"),
    ("Cheese Toast", "sourdough bread"),
    ("Chicken Teriyaki", "teriyaki sauce"),
])
def test_a_celiac_restriction_holds_back_what_gluten_is_in(maya, restriction, dish, item):
    tools.set_member_dietary_restrictions("Maya", [restriction])

    clashes = allergen_gate.hard_clashes(dish, [{"item": item, "qty": "1 cup"}])

    assert clashes, f"{dish} ({item}) passed the gate for a {restriction!r} restriction"
    assert clashes[0]["member"] == "Maya"


def test_a_gluten_free_line_is_not_held_back_for_celiac(maya):
    """The same "gluten-free pasta is fine" a gluten restriction gets —
    otherwise every GF swap the planner makes for her is re-picked."""
    tools.set_member_dietary_restrictions("Maya", ["Celiac"])

    assert allergen_gate.hard_clashes("Veggie Primavera", [{"item": "gluten-free penne", "qty": "1 lb"}]) == []


@pytest.mark.parametrize("fact", ["Maya is celiac", "Maya has coeliac disease"])
def test_a_celiac_fact_on_what_we_know_holds_back_bread_too(maya, fact):
    """Written as a must-avoid note rather than a restriction chip: no
    "allergic to", no food named, and still a gluten avoidance."""
    tools.add_fact("people", fact, hard=True)

    assert allergen_gate.hard_clashes("Garlic Bread", [{"item": "baguette bread", "qty": "1"}])


def test_the_warning_names_her_condition_the_way_people_say_it(maya):
    """"…a clash with Maya's celiac", never "the celiac Maya can't have"."""
    from app.tools import coordination
    tools.set_member_dietary_restrictions("Maya", ["Celiac"])
    clashes = allergen_gate.hard_clashes("Spaghetti Bolognese", [{"item": "spaghetti", "qty": "1 lb"}])

    assert "Maya’s celiac" in coordination._conflicts_note(clashes)
