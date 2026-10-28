"""
The diet hard check reads the diet as people write it (defect hunt,
2026-10-28): a member saved as "Vegetarian diet", "Lacto-ovo vegetarian"
or "Vegan diet" got NO check from the 2026-10-27 diet check
(coordination._DIET_FAMILIES) — a multi-word restriction was matched as
the literal words, so Chicken Tacos went through every door.
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
