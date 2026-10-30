"""
A note written beside an allergy is not part of the food's name
(defect hunt, 2026-10-29).

Preferences › People takes each restriction as typed. "Shellfish
(anaphylactic)", "sesame - EpiPen" and "peanuts (airborne too)" were read as
one phrase, so the allergy check looked for the note's words in the dish as
well — no shrimp taco says "anaphylactic", and the person's most serious
allergy was checked against nothing. The same held for a must-avoid note
("Sam is allergic to shellfish (anaphylactic)").
"""
import pytest

from app import tools
from app.tools import allergen_gate


@pytest.fixture
def sam():
    tools.add_member("Sam")


@pytest.mark.parametrize("restriction, dish, item", [
    ("Shellfish (anaphylactic)", "Shrimp Tacos", "shrimp"),
    ("shellfish (mild)", "Shrimp Tacos", "shrimp"),
    ("shellfish — anaphylaxis", "Crab Cakes", "lump crab"),
    ("sesame - EpiPen", "Sesame Noodles", "toasted sesame oil"),
    ("Tree nut allergy - carries EpiPen", "Walnut Salad", "walnuts"),
    ("peanuts (airborne too)", "Satay Bowls", "peanut butter"),
    ("Peanuts: severe", "PB Toast", "peanut butter"),
    ("Fish (hives)", "Baked Salmon", "salmon fillets"),
    ("no shellfish please", "Shrimp Tacos", "shrimp"),
    # A note in words the check has never seen still ends at the dash.
    ("Shellfish - doctor's orders", "Shrimp Tacos", "shrimp"),
    ("sesame: since 2019", "Sesame Noodles", "toasted sesame oil"),
])
def test_a_restriction_with_a_note_still_holds_its_food_back(signed_in, sam, restriction, dish, item):
    # Saved the way the People sheet saves it.
    res = signed_in.post("/api/memory/member/restrictions",
                         json={"name": "Sam", "restrictions": [restriction], "replace": False})
    assert res.status_code == 200

    clashes = allergen_gate.hard_clashes(dish, [{"item": item, "qty": "1 cup"}])

    assert clashes, f"{dish} ({item}) passed the gate for Sam's {restriction!r}"
    assert clashes[0]["member"] == "Sam"


@pytest.mark.parametrize("restriction, dish, item", [
    # The note's own words are not foods: "mild" must not hold a mild curry.
    ("shellfish (mild)", "Mild Chicken Curry", "chicken thighs"),
    ("Shellfish (anaphylactic)", "Chicken Penne", "penne"),
    # A hyphen inside a word is not a note: gluten-free still reads as gluten.
    ("gluten-free", "Roast Chicken", "chicken"),
])
def test_the_note_itself_holds_nothing_back(sam, restriction, dish, item):
    tools.set_member_dietary_restrictions("Sam", [restriction])

    assert allergen_gate.hard_clashes(dish, [{"item": item, "qty": "1"}]) == []


@pytest.mark.parametrize("fact, dish, item", [
    ("Sam is allergic to shellfish (anaphylactic)", "Shrimp Tacos", "shrimp"),
    ("No sesame - Sam carries an EpiPen", "Sesame Noodles", "sesame oil"),
])
def test_a_must_avoid_note_with_a_note_inside_it_still_holds(sam, fact, dish, item):
    tools.add_fact("people", fact, hard=True)

    assert allergen_gate.hard_clashes(dish, [{"item": item, "qty": "1"}])


@pytest.mark.parametrize("fact, dish, item", [
    # Review, 2026-10-29: a note inside a "but/except" carve-out must not
    # carry the allergens after it into the exception. Main blocked all of
    # these; the first cut of this fix let every one through.
    ("Sam is allergic to peanuts except peanut oil (fine), and sesame", "Sesame Noodles", "sesame oil"),
    ("Sam is allergic to tree nuts but almonds (ok), and shellfish", "Shrimp Tacos", "shrimp"),
    ("Sam is allergic to tree nuts but almonds (ok) and shellfish", "Shrimp Tacos", "shrimp"),
    ("Sam is allergic to tree nuts except almonds (fine) and shellfish", "Shrimp Tacos", "shrimp"),
    ("Sam is allergic to milk except butter - fine - and eggs", "Omelette", "eggs"),
])
def test_a_note_inside_an_exception_keeps_the_later_allergens(sam, fact, dish, item):
    tools.add_fact("people", fact, hard=True)

    assert allergen_gate.hard_clashes(dish, [{"item": item, "qty": "1"}]), fact
