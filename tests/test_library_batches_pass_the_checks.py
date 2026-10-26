"""
Every dish in data/recipe_library_batches/ through the app's own food
checks (library audit, 2026-10-25, on the 594 batch files).

`library_batch.py check` is what an agent runs on a dish before it is filed,
and nothing ran it on the whole library afterwards: a batch that slipped
through, or a later change to the checks, would show only in the morning
report of whichever household got the dish. This runs it on all of them.

What the sweep found was in the CHECKS, not the dishes — four amounts the
morning report would have put against a correct recipe:
  * Gigantes Plaki: "a 9x13 inch baking dish and stir in the onion" was 9
    onions to the list's 1 (the "x" read as the word after the number);
  * Roast Chicken: the "2 whole garlic cloves" in the cavity were 2 of
    "Whole chicken";
  * General Tso's: "2 tbsp of fresh oil" was Fresh ginger's;
  * Buffalo Wings: "the hot sauce" over a list with Frank's RedHot sauce
    "isn't on the list".
And one in the copy: a household with a "vegetarian" member was copied
Broccoli Cheddar Soup on chicken broth — the allergen gate reads foods, not
diets, so it held nothing back.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

import library_batch
from app import agent, tools
from app.tools import allergen_gate
from app.tools import recipe_library as lib
from app.tools import recipes

BATCHES = sorted(Path(library_batch.BATCH_DIR).glob("*.json"))

# The amount "looks" the batch check prints on today's library, each read by
# hand on 2026-10-25 and each a misread, not a wrong recipe: a per-piece
# amount ("2 tbsp of the cheese" on each of 4 tortillas), "2 of the eggs ...
# the last egg", "the rest of the X", "the 2 zucchini and the yellow squash",
# or the per-serving guard's own correction (Grilled Cheese's 9 tbsp butter,
# Granola Bars' 1 cup peanut butter, shown as 2 tbsp). A NEW look goes red
# here: read the dish, fix it if the sum is really off, else add it.
KNOWN_AMOUNT_LOOKS = {
    ("bean-and-cheese-burritos", "Shredded cheddar cheese"),
    ("biscotti", "Eggs"),
    ("calabacitas-con-pollo", "Yellow squash"),
    ("calzones", "Marinara sauce"),
    ("chicken-enchiladas", "Sour cream"),
    ("chicken-souvlaki", "Garlic"),
    ("chiles-rellenos", "All-purpose flour"),
    ("cochinita-pibil", "Limes"),
    ("grilled-cheese-and-tomato-soup", "Unsalted butter"),
    ("hash-browns-and-eggs", "Unsalted butter"),
    ("homemade-granola-bars", "Creamy peanut butter"),
    ("idli-with-coconut-chutney", "Curry leaves"),
    ("lamb-rogan-josh", "Plain yogurt"),
    ("leek-and-potato-soup", "Heavy cream"),
    ("paneer-tikka-wraps", "Cilantro chutney"),
    ("tonkatsu", "Tonkatsu sauce"),
    ("turkey-and-cheese-roll-ups", "Sliced deli turkey"),
    ("warak-enab", "Roma tomatoes"),
    ("warak-enab", "Lemons"),
}
# Tagged vegetarian with fish sauce on the list — left for Emily (the tag is
# a variety word no household's copy carries; see the decision log).
KNOWN_MEATLESS_LOOKS = {("green-papaya-salad", "Fish sauce")}

_AMOUNT_LOOK = re.compile(r"look (\S+)\.json: steps use .+? of (.+?), list says")
_MEAT_LOOK = re.compile(r"look (\S+)\.json: main_protein is \S+ but the list has (.+)$")


@pytest.fixture(scope="module")
def swept():
    import contextlib
    import io
    problems, out = {}, io.StringIO()
    with contextlib.redirect_stdout(out):
        for path in BATCHES:
            found = library_batch.check_one(path)
            if found:
                problems[path.name] = found
    return problems, out.getvalue().splitlines()


def test_every_library_batch_passes_the_batch_check(swept):
    """GUARD over the whole library: no dish has a step reaching for
    something off its list, a line no step uses, a bad category, or an
    advance-prep step number past the end of the method."""
    problems, _ = swept
    assert len(BATCHES) >= 594
    assert problems == {}


def test_no_new_amount_or_meatless_looks(swept):
    _, lines = swept
    amounts = {m.groups() for m in map(_AMOUNT_LOOK.search, lines) if m}
    meat = {m.groups() for m in map(_MEAT_LOOK.search, lines) if m}
    assert amounts == KNOWN_AMOUNT_LOOKS
    assert meat == KNOWN_MEATLESS_LOOKS


# ---------- the checker's own misreads ----------

def _mismatched(ingredients, steps):
    return [m["item"] for m in recipes.check_steps_ingredients_consistency(ingredients, steps, 4)["amount_mismatches"]]


@pytest.mark.parametrize("dish", ["9x13 inch", "9 x 13 inch", "13x9 inch", "8 x 8"])
def test_a_pans_size_is_not_an_amount_of_what_goes_in_it(dish):
    """CATCH: Gigantes Plaki's 9 onions."""
    steps = [f"Pour the oil into a {dish} baking dish and stir in the onion.", "Bake the onion until soft."]
    assert _mismatched([{"item": "Yellow onion", "qty": "1"}, {"item": "Olive oil", "qty": "5 tbsp"}], steps) == []


def test_a_can_count_by_weight_is_still_an_amount():
    """GUARD (review): "2 x 400g cans" is two cans, not a pan's size."""
    found = [m.group(1) for m in recipes._CHECK_AMOUNT_RE.finditer("add 2 x 400g cans of tomatoes")]
    assert found[0] == "2"
    # The pan's first number is not read; its second sizes the pan.
    assert [m.group(1) for m in recipes._CHECK_AMOUNT_RE.finditer("an 8 x 8 pan")] == ["8"]


def test_a_sort_word_in_a_name_is_not_the_food():
    """CATCH: the cavity's "2 whole garlic cloves" were 2 Whole chickens,
    and "fresh oil" was Fresh ginger. GUARD: a real mismatch still shows."""
    ings = [{"item": "Whole chicken", "qty": "1"}, {"item": "Garlic", "qty": "2 cloves"},
            {"item": "Fresh ginger", "qty": "1 tbsp"}]
    steps = ["Stuff the chicken with 2 whole garlic cloves.", "Grate 1 tbsp of the ginger.",
             "Heat 2 tbsp of fresh oil and roast the chicken."]
    assert _mismatched(ings, steps) == []
    assert _mismatched(ings, ["Stuff the chicken with 2 whole garlic cloves.", "Grate 2 tbsp of the ginger.",
                              "Roast the chicken."]) == ["Fresh ginger"]


def test_a_hot_sauce_named_for_its_maker_is_the_hot_sauce():
    r = recipes.check_steps_ingredients_consistency(
        [{"item": "Chicken wings", "qty": "3 lb"}, {"item": "Frank's RedHot sauce", "qty": "1/3 cup"}],
        ["Bake the wings.", "Toss the wings in the hot sauce."], 4)
    assert r["missing_from_list"] == []


# ---------- a diet the gate can't read is written, not copied ----------

def _copied(key, restriction=None, fact=None):
    with open(library_batch.BATCH_DIR / f"{key}.json") as f:
        e = json.load(f)
    lib.file_entry(e["dish_name"], **{**{k: v for k, v in e.items() if k != "dish_name"}, "origin": "seed"})
    tools.add_member("Pat")
    if restriction is not None:
        tools.set_member_dietary_restrictions("Pat", [restriction])
    if fact:
        tools.add_fact("diet", fact, hard=True)
    name = e["dish_name"]
    tools.add_recipe(name=name, ingredients=[], details_pending=True, cuisine="")
    recipe = {**tools.get_recipe(name), "slot": "dinner"}
    out, _clash = agent._copy_from_library(recipe, name, name, allergen_gate.hard_avoidances(), {})
    return bool(out)


@pytest.mark.parametrize("restriction", [
    "vegetarian", "Vegan", "pescatarian", "no meat", "meat-free", "veggie", "plant-based",
])
def test_a_household_with_a_vegetarian_is_never_copied_a_library_dish(restriction):
    """CATCH: Broccoli Cheddar Soup, chicken broth and all, copied for a
    household with a vegetarian member — however the restriction is put."""
    assert not _copied("broccoli-cheddar-soup", restriction)


@pytest.mark.parametrize("fact", [
    "Pat is vegetarian", "We're vegetarian", "We keep kosher", "No meat in this house",
])
def test_a_hard_fact_naming_a_diet_is_never_copied_a_library_dish(fact):
    """CATCH (review): a fact whose words name no food never reaches the
    gate's avoidances, so reading those let the soup be copied."""
    assert not _copied("broccoli-cheddar-soup", fact=fact)


@pytest.mark.parametrize("restriction", ["sesame", "omnivore", "none", ""])
def test_an_allergy_the_gate_reads_or_no_diet_still_copies_a_clean_dish(restriction):
    """GUARD: a sesame allergy, or a restriction that says no diet, doesn't
    stop a sesame-free copy."""
    assert _copied("broccoli-cheddar-soup", restriction)
