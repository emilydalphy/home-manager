"""
A step seasoning with salt is not a finding, because this app has already
decided the household owns a spice rack.

`recipes.check_steps_ingredients_consistency` reports a food word a step
reaches for that nobody bought — which is how a household finds out
mid-cook that there is no broth. Its vocabulary is the measurement table's
own keys, and 39 of those 176 words (22.2%, measured) are things
`spices.is_spice()` calls rack items: salt, black pepper, olive oil,
paprika, cumin, dried oregano, garlic powder and so on.

So the grocery half and the quality half of the app disagreed about
whether a recipe has to list its salt. The Shop tab's "Spices this week"
section exists precisely because the list assumes a rack (2026-09-23), and
a household that is never sold salt cannot be told off for not buying it.

MEASURED ON THE LIVE APP, 2026-09-27, over the report token's own 7-day
window: `steps_match_ingredients` **10 findings -> 2**, every one of the
eight suppressed being `salt` and both survivors being `broth`. The FOOD
section as a whole goes 29 -> 21, and the rule stops being its biggest
line (`dinner_repeat_in_history`, 6, takes over). The card was written
against 33-of-59, which was measured before the FOOD dedupe fix merged
(`overnight/food-count-live-plans-only`); 10-of-29 is the same window
counted honestly, and 10 was still the biggest line in it.

Red against main is **11 of 19, and that number is decomposed rather than
quoted**: NINE are behaviour catches failing on the assertion they are
named for (the reproduction, the six parametrized rack words, the
salt-and-broth recipe, and the coupling test, which on main reports every
rack word). The other TWO die on a name main has not got — `recipes._spices`
— which is the only kind of red a test of a new symbol can have, and both
say so. The 8 green are guards on the half that must NOT move; each names
the mutation that pins it instead.
"""

import pytest

from app import tools
from app.tools import recipes, spices


# --------------------------------------------------------------------------
# 1. The reported bug
# --------------------------------------------------------------------------

def test_seasoning_with_salt_is_not_a_finding():
    """CATCH. The card's own reproduction, verbatim."""
    result = tools.check_steps_ingredients_consistency(
        [{"item": "Chicken thighs"}, {"item": "Rice"}],
        [
            "Season the chicken with salt and pepper.",
            "Sear the chicken, then add the rice and simmer.",
        ],
    )

    assert result == {"ok": True, "unused_ingredients": [], "missing_from_list": []}


@pytest.mark.parametrize(
    "word, step",
    [
        ("salt", "Season generously with salt."),
        ("black pepper", "Finish with a grind of black pepper."),
        ("olive oil", "Warm the olive oil in a pan."),
        ("paprika", "Dust the paprika over the top."),
        ("cumin", "Toast the cumin until fragrant."),
        ("garlic powder", "Stir the garlic powder through."),
    ],
)
def test_no_rack_item_is_ever_reported_missing(word, step):
    """CATCH for every one of these but the parametrize's own bookkeeping."""
    result = tools.check_steps_ingredients_consistency(
        [{"item": "Chicken thighs"}], ["Sear the chicken.", step],
    )

    assert word not in result["missing_from_list"]


# --------------------------------------------------------------------------
# 2. The half that must survive
# --------------------------------------------------------------------------

def test_a_step_reaching_for_broth_nobody_bought_still_fires():
    """
    GUARD, and the one the card names by name. Broth is not a rack item and
    this is exactly the finding worth keeping — green on main, so what pins
    it is the mutation that suppresses every vocabulary word rather than
    only the rack ones.
    """
    result = tools.check_steps_ingredients_consistency(
        [{"item": "Beef"}, {"item": "Potatoes"}],
        ["Simmer the beef and potatoes in broth until tender."],
    )

    assert result["ok"] is False
    assert result["missing_from_list"] == ["broth"]


@pytest.mark.parametrize("word", ["broth", "butter", "stock", "heavy cream"])
def test_the_things_a_household_really_does_buy_still_fire(word):
    """
    GUARD. Butter in particular: it sits beside the oils in a kitchen and
    is NOT in the rack list, so this is the boundary the fix walks.
    """
    assert not spices.is_spice(word)
    result = tools.check_steps_ingredients_consistency(
        [{"item": "Beef"}], ["Sear the beef.", f"Stir in the {word}."],
    )

    assert word in result["missing_from_list"]


def test_a_real_finding_survives_a_step_that_also_seasons():
    """
    CATCH. The live shape: one recipe whose steps both season with salt and
    reach for broth. Main reports both; only the broth is worth saying.
    """
    result = tools.check_steps_ingredients_consistency(
        [{"item": "Beef"}],
        ["Season the beef with salt, then simmer it in broth."],
    )

    assert result["missing_from_list"] == ["broth"]


# --------------------------------------------------------------------------
# 3. The other half of the rule is untouched
# --------------------------------------------------------------------------

def test_a_spice_bought_and_never_used_is_still_reported():
    """
    GUARD, and the card asks for it by name: `unused_ingredients` is a
    different question — something was bought and no step wants it — and a
    rack item on the list is as worth saying as anything else.
    """
    result = tools.check_steps_ingredients_consistency(
        [{"item": "Paprika"}, {"item": "Beef"}], ["Sear the beef."],
    )

    assert result["ok"] is False
    assert result["unused_ingredients"] == ["Paprika"]


def test_water_in_a_step_is_not_a_finding_and_STEP_ONLY_WORDS_is_why_it_isnt():
    """
    CHARACTERISATION, and it is here because the first version of this test
    was a GUARD that could not fail — caught by running the mutation rather
    than by reading it.

    `_STEP_ONLY_WORDS = {"water", "ice"}` is the clause the card holds up as
    the precedent for this fix ("it already excuses exactly two words").
    **It excuses nothing.** The comprehension iterates the keys of
    `COOKING_QUANTITIES_PER_4`, and neither word is one — measured below —
    so `key not in _STEP_ONLY_WORDS` is True for every key there is.
    Deleting the clause outright reddens NOTHING, on main or here.

    That is worth knowing rather than fixing, and it cuts in the fix's
    favour: `is_spice` is the only excusing this rule actually does, so
    there is no second mechanism to drift from. The behaviour the clause
    was written for is real and holds anyway — water in a step is not a
    finding — it just holds because the rule cannot see the word at all.
    Whoever adds "water" to the measurement table is the person this
    sentence is for: the clause wakes up that day.
    """
    assert not any(w in recipes.COOKING_QUANTITIES_PER_4 for w in recipes._STEP_ONLY_WORDS)
    assert not any(spices.is_spice(w) for w in recipes._STEP_ONLY_WORDS)

    result = tools.check_steps_ingredients_consistency(
        [{"item": "Rice"}], ["Cook the rice in water until tender."],
    )

    assert result["missing_from_list"] == []


# --------------------------------------------------------------------------
# 4. One rule, not a copy of it
# --------------------------------------------------------------------------

def test_the_rule_reads_the_rack_rather_than_a_second_list():
    """
    GUARD, source-level, and the card's own instruction: "Read it off
    `spices.is_spice` rather than a second hand-written list ... two lists
    would drift." Comment-stripped, because this repo has been bitten three
    times by a marker a comment could satisfy.
    """
    import ast, inspect, textwrap

    tree = ast.parse(textwrap.dedent(
        inspect.getsource(recipes.check_steps_ingredients_consistency)
    ))
    ast.get_docstring(tree.body[0])  # present; the point is what follows
    tree.body[0].body[0] = ast.Expr(value=ast.Constant(value=""))
    code = ast.unparse(tree)

    assert "_spices.is_spice" in code, (
        "the rack has to be read from spices.is_spice, not copied here"
    )


def test_extending_the_rack_extends_this_rule_in_the_same_breath():
    """
    GUARD on the coupling, stated as behaviour rather than as prose: a word
    the rack claims is never reportable, whichever word it is. This is the
    thing to know before adding to `spices._SPICES` — it silences this rule
    for that word too.
    """
    rack = [k for k in recipes.COOKING_QUANTITIES_PER_4 if spices.is_spice(k)]

    assert len(rack) > 20, "the overlap is real; 39 of 176 when measured"
    for key in rack:
        result = tools.check_steps_ingredients_consistency(
            [{"item": "Chicken thighs"}], ["Sear the chicken.", f"Add the {key}."],
        )
        assert key not in result["missing_from_list"], key


def test_the_vocabulary_itself_did_not_shrink():
    """
    GUARD. The fix must not be "delete the rack words from the measurement
    table" — that table is what `plausible_cooking_quantity` reads to turn
    "1 bottle olive oil" into "2 tbsp", so losing a key there would be a
    quantities bug wearing a quality-rule hat.
    """
    for key in ("salt", "black pepper", "olive oil"):
        assert key in recipes.COOKING_QUANTITIES_PER_4


# --------------------------------------------------------------------------
# 5. The import, which is the one thing that could have exploded
# --------------------------------------------------------------------------

def test_the_module_alias_import_survives_the_cycle():
    """
    GUARD. `recipes` now imports `spices`, and `spices -> grocery ->
    weekly_plan -> recipes` closes a cycle. The package's own convention
    (import the MODULE, never the name) is what resolves it; this asserts
    the binding is a module rather than a function, which is the shape that
    survives. Checked empirically too, by importing each of recipes,
    spices, staples, grocery, weekly_plan, agent and main FIRST in a fresh
    subprocess — all seven fine.
    """
    import types

    assert isinstance(recipes._spices, types.ModuleType)
    assert recipes._spices.is_spice("salt") is True
