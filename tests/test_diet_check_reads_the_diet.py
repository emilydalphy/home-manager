"""
The diet hard check reads the diet as people write it, and holds back
only what the diet rules out (defect hunt, 2026-10-28).

Two ways the 2026-10-27 check (coordination._DIET_FAMILIES) went wrong
for a real household:

  * A member saved as "Vegetarian diet", "Lacto-ovo vegetarian" or "Vegan
    diet" got NO check: a multi-word restriction was matched as the
    literal words, so Chicken Tacos went through every door.
  * A vegetarian's eggs were meat: "quail eggs", "duck eggs", "chicken
    eggs" matched the bird; and the meatless broth bases sold by name
    ("no-chicken broth") and the soy kinds ("soy chorizo", "tempeh bacon")
    were held as the real thing.
"""
import pytest

from app import tools
from app.tools import allergen_gate


def _member(restriction):
    tools.add_member("Maya")
    tools.set_member_dietary_restrictions("Maya", [restriction])


def _clashes(name, *items):
    return allergen_gate.hard_clashes(name, ingredients=[{"item": i, "qty": "1"} for i in items])


@pytest.mark.parametrize("restriction", [
    "Vegetarian diet", "Lacto-ovo vegetarian", "ovo-vegetarian", "Vegan diet",
    "Pescatarian diet", "plant based diet",
])
def test_a_diet_written_with_its_kind_still_holds_back_the_meat(restriction):
    _member(restriction)

    clashes = _clashes("Chicken Tacos", "chicken thighs", "tortillas")

    assert clashes and clashes[0]["matched"] == "chicken"
    assert not _clashes("Black Bean Tacos", "black beans", "tortillas")


@pytest.mark.parametrize("restriction", ["mostly vegetarian", "pollo vegetarian", "vegetarian meat"])
def test_a_phrase_that_is_not_the_whole_diet_is_not_read_as_it(restriction):
    """A pollotarian eats chicken; "vegetarian meat" is a won't-eat."""
    _member(restriction)

    assert not _clashes("Chicken Tacos", "chicken thighs")


@pytest.mark.parametrize("item", [
    "12 quail eggs", "4 duck eggs", "2 large chicken eggs", "duck egg yolks",
    "no-chicken broth", "no chicken base", "soy chorizo", "tempeh bacon",
    "chicken of the woods mushrooms",
])
def test_a_vegetarian_is_not_held_on_what_isnt_meat(item):
    _member("Vegetarian")

    assert not _clashes("Supper", item), item


@pytest.mark.parametrize("name, item", [
    ("Chicken Egg Fried Rice", "chicken egg fried rice"),
    ("Turkey Egg Muffins", "turkey egg muffins"),
    ("Soup", "chicken broth"),
    ("Pasta", "no salt chicken stock"),
    ("Toast", "chicken liver pate"),
    ("Soy Chicken", "soy chicken"),
])
def test_the_bird_itself_is_still_held(name, item):
    _member("Vegetarian")

    assert _clashes(name, item), item


def test_a_vegan_is_still_held_on_the_eggs():
    _member("Vegan")

    assert _clashes("Ramen", "6 quail eggs")


def test_an_allergy_reads_exactly_as_before():
    """The diet's own exceptions never speak for an allergy."""
    _member("Egg allergy")

    assert _clashes("Ramen", "6 quail eggs")
