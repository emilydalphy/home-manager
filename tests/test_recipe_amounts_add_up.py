"""
Three cups of cashews, and the steps only ever use one.

Gowthami's household (beta tester), 2026-10-04: "Some of the ingredients
in the actual recipe are not adding up. it says use 3 cups of cashews for
example, but then the actually steps doesn't use the 3 cups." Filed under
"Blockers to use it effectively: Recipe quality: can't trust the recipe."

`recipes.check_steps_ingredients_consistency` could already see an
ingredient no step touches, and a step reaching for something nobody
bought. It compared NAMES only — so a step using a third of what the list
bought read as perfectly fine, and the one thing it logged was logged
nowhere a household could act on it. This file pins the amount half: the
check, the one targeted repair before save, and — the larger half of the
work — the dozen shapes where a number in a step is NOT an amount of food
and this must stay quiet.

RED AGAINST MAIN IS 54 OF 54 AND IS WORTH NOTHING, and it is given that
way rather than decomposed, because every test here calls
check_steps_ingredients_consistency with the servings argument the fix
adds — so against main every one of them is a TypeError before it reaches
an assertion. (An earlier version of this docstring claimed "14 of 31"
with a decomposition; neither number reproduces, and the file is 54 cases
rather than 31 once the two parametrize blocks are counted.)

THE HONEST BEHAVIOUR NUMBER IS MEASURED AGAINST A STUB — everything
present and wired, with only `_amount_mismatches` returning nothing and
`_settle_recipe_amounts` returning the recipe untouched, so the one
difference from this branch is that nothing checks the amounts and nothing
repairs them: **16 failed / 38 passed**. Of those 16, five are red for a
reason other than the claim they are named for (the three stray-amount and
message guards and the two repair-keeps-the-recipe guards all assert
against a mismatch the stub cannot produce) and each says so in its own
docstring.

THE 38 ARE GUARDS, and most of this file is one: a false "your amounts
don't add up" on a good recipe is worse than a missed one, because a
household that learns to click past this learns to click past the real
one. Each names the mutation that pins it, and every one of those
mutations was run — the red counts in the docstrings are measured, and
where a first draft's claim did not reproduce the docstring says so rather
than being quietly corrected.
"""
from __future__ import annotations

import types

import pytest

from app import agent, tools
from app.tools import plan_quality, recipes


# --------------------------------------------------------------------------
# 1. The reported bug
# --------------------------------------------------------------------------

def test_three_cups_of_cashews_and_steps_that_use_one_fails_the_check():
    """CATCH. The card's own reproduction, verbatim."""
    result = tools.check_steps_ingredients_consistency(
        [{"item": "Cashews", "qty": "3 cups"}, {"item": "Rice", "qty": "2 cups"}],
        [
            "Soak 1 cup of cashews in hot water for 20 minutes.",
            "Cook 2 cups rice until tender, about 15 minutes.",
            "Blend and serve.",
        ],
        4,
    )

    assert result["ok"] is False
    assert result["amount_mismatches"] == [
        {"item": "Cashews", "listed": "3 cups", "in_steps": "1 cup"},
    ]
    # The name half is quiet: the cashews DO appear in a step. That is
    # exactly why this went unseen.
    assert result["unused_ingredients"] == []
    assert result["missing_from_list"] == []


def test_a_clean_recipe_passes_untouched():
    """
    GUARD. Pinned by the mutation that credits every listed name in the
    window rather than the nearest one, which hands the cashews the rice's
    two cups (2 red: this and the nearest-ingredient guard below).

    SEEDED THE WAY IT IS FOR A MEASURED REASON. The first version of this
    test had no step where two listed names shared one window, so the
    mutation it names reddened nothing; and because a single step amount
    equal to the list amount is enough on its own, a mis-attribution is
    invisible unless the victim's own amount is SPLIT across steps. Hence
    the cashews in two goes, and one step naming the rice and the cashews
    together.
    """
    result = tools.check_steps_ingredients_consistency(
        [
            {"item": "Cashews", "qty": "3 cups"},
            {"item": "Olive oil", "qty": "2 tbsp"},
            {"item": "Rice", "qty": "2 cups"},
        ],
        [
            "Soak 2 cups of the cashews in hot water for 20 minutes.",
            "Warm 2 tbsp olive oil over medium heat.",
            "Add 2 cups rice and the cashews, and toast for 2 minutes.",
            "Blend the last 1 cup of cashews and stir through.",
        ],
        4,
    )

    assert result == {
        "ok": True, "unused_ingredients": [], "missing_from_list": [], "amount_mismatches": [],
    }


# --------------------------------------------------------------------------
# 2. What "add up" means
# --------------------------------------------------------------------------

def test_an_amount_split_across_steps_adds_up():
    """CATCH. The card: "split across steps is fine: 2 cups + 1 cup = 3
    cups"."""
    result = tools.check_steps_ingredients_consistency(
        [{"item": "Cashews", "qty": "3 cups"}],
        ["Toast 2 cups of cashews.", "Blend with the remaining 1 cup cashews."],
        4,
    )
    assert result["ok"] is True


def test_a_step_restating_the_full_amount_is_not_two_portions():
    """
    CATCH. "Soak the 3 cups of cashews", then "blend the 3 cups of
    cashews" — the same cashews named twice, not six cups of them. Summing
    alone would report a recipe that is right. Also the GUARD on the
    single-amount rule itself, and the second step says the cashews again
    for a measured reason: written "blend the 3 cups until smooth" the
    clause break at "until" left that amount naming nothing, so it became
    an unpinned amount and quieted the line — the test passed without ever
    reaching the rule it is named for, and the sum-only mutation reddened
    nothing. It reddens this now (1 red).
    """
    result = tools.check_steps_ingredients_consistency(
        [{"item": "Cashews", "qty": "3 cups"}],
        ["Soak the 3 cups of cashews.", "Drain, then blend the 3 cups of cashews."],
        4,
    )
    assert result["ok"] is True


def test_no_amount_in_the_steps_at_all_is_not_a_finding():
    """
    GUARD. The card holds this only to "when a step names an amount for
    it" — a step that refers to the ingredient by name is its own stated
    fallback. Pinned by the mutation that treats a missing step amount as
    zero (4 red: this, and the three window guards whose numbers then
    read as zero-amount findings).
    """
    result = tools.check_steps_ingredients_consistency(
        [{"item": "Cashews", "qty": "3 cups"}],
        ["Soak the cashews, drain, and blend until smooth."],
        4,
    )
    assert result["ok"] is True


def test_units_are_converted_before_they_are_compared():
    """CATCH. A pound in the list and sixteen ounces in the step are the
    same pound."""
    assert tools.check_steps_ingredients_consistency(
        [{"item": "Ground beef", "qty": "1 lb"}],
        ["Brown 16 oz of ground beef over medium-high heat, about 8 minutes."],
        4,
    )["ok"] is True

    assert tools.check_steps_ingredients_consistency(
        [{"item": "Ground beef", "qty": "1 lb"}],
        ["Brown 4 oz of ground beef over medium-high heat, about 8 minutes."],
        4,
    )["ok"] is False


def test_rounding_is_forgiven_and_a_doubling_is_not():
    """
    GUARD on the tolerance in both directions. Recipes write a third of a
    cup as "0.33 cup"; nobody got that wrong. Pinned by the mutation that
    demands an exact match (1 red) and by the one that widens it to 1.0,
    which forgives a doubling and so takes the whole check with it (15
    red). Both counts are measured; the first version of this docstring
    had them the other way round.
    """
    assert tools.check_steps_ingredients_consistency(
        [{"item": "Cashews", "qty": "0.33 cup"}], ["Blend 1/3 cup of cashews until smooth."], 4,
    )["ok"] is True
    assert tools.check_steps_ingredients_consistency(
        [{"item": "Cashews", "qty": "1 cup"}], ["Blend 2 cups of cashews until smooth."], 4,
    )["ok"] is False


def test_a_unit_that_does_not_convert_is_passed_over():
    """
    GUARD, and it is the shopping-versus-cooking split this app is built
    on: the list buys "1 head" of cabbage and the step shreds three cups
    of it. Those are not two readings of one number.

    MEASURED: this half is pinned by the UNIT CONVERSION and not by the
    bought-unit guard — cups don't convert to heads, so dropping the
    guard changes nothing here. The guard is load-bearing for the
    same-unit case below, which is where it is a judgement rather than
    arithmetic.
    """
    for qty, step in [
        ("1 head", "Shred 3 cups of the cabbage, then salt it."),
        ("1 bunch", "Chop 2 tbsp of the cabbage for the garnish."),
    ]:
        assert tools.check_steps_ingredients_consistency(
            [{"item": "Cabbage", "qty": qty}], [step], 4,
        )["ok"] is True


def test_a_bought_unit_is_the_lists_number_and_not_the_pans():
    """
    GUARD on the judgement the bought-unit guard actually makes. "1 head"
    is how cabbage is bought, so a step reaching for two heads is read as
    a step talking about shopping, and this stays out of it — the quiet
    direction, and the one consistent with cooking_ingredients never
    showing a cook a package word.

    MEASURED, and it took two lines rather than one: NEITHER side's guard
    pins this on its own. Dropping the list side's leaves "1 head" to be
    compared against a step this reads as a bare count of two cabbages,
    which converts to nothing, so it is still passed over (0 red).
    Dropping the step side's alone leaves the list side refusing first (0
    red). Removing BOTH — the list side's measured-unit guard and the step
    side's — is what reddens it (2 red: this, and the unit-family guard
    below, where a stray then converts where it could not before).
    """
    assert tools.check_steps_ingredients_consistency(
        [{"item": "Cabbage", "qty": "1 head"}],
        ["Shred 2 heads of cabbage, then salt and drain."],
        4,
    )["ok"] is True


# --------------------------------------------------------------------------
# 3. The exemptions the card names
# --------------------------------------------------------------------------

def test_the_spice_rack_is_exempt_from_the_amount_check():
    """
    CATCH. The card: "salt/pepper/oil-for-the-pan don't need an amount in
    the steps". The list's amount for a rack item is the app's own figure
    rather than the recipe's — the Shop tab never sells them per recipe —
    so holding a step to it holds the recipe to a number it never wrote.
    """
    result = tools.check_steps_ingredients_consistency(
        [
            {"item": "Chicken thighs", "qty": "4"},
            {"item": "Salt", "qty": "1 tsp"},
            {"item": "Olive oil", "qty": "2 tbsp"},
            {"item": "Black pepper", "qty": "1 tsp"},
        ],
        [
            "Season the 4 chicken thighs with 1/2 tsp salt and a grind of black pepper.",
            "Sear in 1 tbsp olive oil, skin down, 6 minutes.",
        ],
        4,
    )
    assert result["amount_mismatches"] == []
    assert result["ok"] is True


@pytest.mark.parametrize("qty", ["to taste", "a pinch", "for garnish", "as needed", ""])
def test_a_freeform_amount_is_never_held_to_a_step(qty):
    """
    CATCH for the card's "'To taste', 'a pinch', 'for garnish' ... don't
    need an amount in the steps". They need no rule of their own: none of
    them parses as a number, so there is nothing to compare.
    """
    result = tools.check_steps_ingredients_consistency(
        [{"item": "Parsley", "qty": qty}, {"item": "Rice", "qty": "2 cups"}],
        ["Cook 2 cups rice.", "Scatter 2 tbsp parsley over the top and serve."],
        4,
    )
    assert result["amount_mismatches"] == []


def test_an_amount_the_app_filled_in_is_not_a_claim_the_recipe_made():
    """
    CATCH, and the one false positive this branch's own tests caught
    before anything shipped. A blank qty comes back from
    cooking_ingredients filled in off COOKING_QUANTITIES_PER_4 — the
    app's figure for a quarter cup of parsley, not something the recipe
    wrote — so a step naming 2 tbsp of it was being reported as the
    recipe disagreeing with itself when it is this module disagreeing
    with the recipe.
    """
    result = tools.check_steps_ingredients_consistency(
        [{"item": "Parsley", "qty": ""}, {"item": "Rice", "qty": "2 cups"}],
        ["Cook 2 cups rice.", "Scatter 2 tbsp parsley over the top and serve."],
        4,
    )
    assert result["amount_mismatches"] == []


def test_a_stated_amount_the_app_corrected_is_held_to_what_the_cook_sees():
    """
    CATCH on the other side of the same line. The recipe DID write an
    amount here, so there is something to hold a step to — and the number
    to hold it to is the one the Cook screen prints beside the steps. A
    cup of butter in a dish for two is shown as 1 tbsp
    (plausible_cooking_quantity), so a step saying 1 tbsp agrees with the
    screen and a step saying a cup disagrees with it, which is exactly
    what the cook would see.
    """
    listed = [{"item": "Butter", "qty": "1 cup"}]
    assert tools.check_steps_ingredients_consistency(
        listed, ["Melt 1 tbsp butter over medium heat."], 2,
    )["ok"] is True
    assert tools.check_steps_ingredients_consistency(
        listed, ["Melt 1 cup butter over medium heat."], 2,
    )["ok"] is False


# --------------------------------------------------------------------------
# 4. Every shape where a number in a step is NOT an amount of food.
#
# This is the bulk of the work and the bulk of the risk. A false "your
# amounts don't add up" on a good recipe is worse than a missed one,
# because a household that learns to click past this learns to click past
# the real one. Every case here is green on main (which compares no
# amounts at all) and green here, and the mutation that pins the lot is
# removing _STEP_NOT_AN_AMOUNT / _STEP_VESSEL_WORDS / _STEP_MAX_COUNT.
# --------------------------------------------------------------------------

@pytest.mark.parametrize("step", [
    # A clock.
    "Bake 25 minutes, then flip the 4 chicken thighs.",
    "Rest 10 min before slicing the 4 chicken thighs.",
    "Marinate 2 hours, then sear the 4 chicken thighs.",
    # A thermometer.
    "Preheat the oven to 400 and sear the 4 chicken thighs.",
    "Roast at 425 degrees until the 4 chicken thighs read 165.",
    "Heat the oven to 200C before the 4 chicken thighs go in.",
    # A ruler.
    "Cut into 1 inch pieces and brown the 4 chicken thighs.",
    # A pan.
    "In a 2 quart saucepan, warm the stock for the 4 chicken thighs.",
    "Spread the 4 chicken thighs in a 9 inch baking dish.",
    # Servings and steps, which are numbers about the recipe not the food.
    "Divide between 2 plates after searing the 4 chicken thighs.",
    "As in step 3, turn the 4 chicken thighs once.",
])
def test_a_number_that_is_not_an_amount_of_food_is_never_read_as_one(step):
    """GUARD. Each of these puts a number within reach of an ingredient
    name; none of them is an amount of it."""
    result = tools.check_steps_ingredients_consistency(
        [{"item": "Chicken thighs", "qty": "4"}, {"item": "Chicken stock", "qty": "2 cups"}],
        [step, "Add 2 cups chicken stock and simmer."],
        4,
    )
    assert result["amount_mismatches"] == [], step


def test_a_vessels_own_size_is_not_an_amount_of_what_goes_in_it():
    """
    GUARD. "In a 2 quart saucepan warm the chicken stock" — a quart is a
    real measure, so only the word right after it says the number is the
    pan's. Pinned by the mutation that drops the vessel words (1 red).
    """
    result = tools.check_steps_ingredients_consistency(
        [{"item": "Chicken stock", "qty": "1 quart"}],
        ["In a 2 quart saucepan warm the chicken stock over low heat."],
        4,
    )
    assert result["amount_mismatches"] == []


def test_an_oven_dial_is_not_a_count_of_the_food():
    """
    GUARD. "Preheat the oven to 400 and sear the chicken thighs" puts a
    bare 400 within reach of a listed ingredient's name with no second
    number to stop the window — the size cap is the only thing between
    that and a reported four-hundred-thigh mismatch. Pinned by the
    mutation that lifts _STEP_MAX_COUNT (1 red).
    """
    result = tools.check_steps_ingredients_consistency(
        [{"item": "Chicken thighs", "qty": "4"}],
        ["Preheat the oven to 400 and sear the chicken thighs until golden."],
        4,
    )
    assert result["amount_mismatches"] == []


def test_an_amount_belongs_to_the_nearest_listed_ingredient_and_no_other():
    """
    GUARD, and the single rule that keeps this honest. "Toss with 2 tbsp
    olive oil and the cashews" names one amount and it is the oil's;
    crediting every name in reach hands the cashews two tablespoons and
    reports a correct recipe as wrong. Pinned by the mutation that credits
    every name in the window (2 red: this and the clean-recipe guard).
    """
    result = tools.check_steps_ingredients_consistency(
        [{"item": "Olive oil", "qty": "2 tbsp"}, {"item": "Cashews", "qty": "3 cups"}],
        ["Soak the cashews.", "Toss with 2 tbsp olive oil and the cashews."],
        4,
    )
    assert result["amount_mismatches"] == []


def test_two_amounts_in_one_step_each_find_their_own_ingredient():
    """
    GUARD. "1 cup rice and 1 cup chicken stock" — the window stops at the
    next number, so the stock two words further on never takes the rice's
    amount. Pinned by the mutation that lets the window run to the end of
    the clause (1 red).

    The rice is split across two steps for the same measured reason as the
    clean-recipe guard above: with the window widened the stock outscores
    the rice on its own two name words and takes both amounts, and the
    only line that can then be a finding is the rice, which is short.
    """
    result = tools.check_steps_ingredients_consistency(
        [{"item": "Rice", "qty": "2 cups"}, {"item": "Chicken stock", "qty": "1 cup"}],
        [
            "Toast 1 cup rice and 1 cup chicken stock together.",
            "Stir in the other 1 cup rice.",
        ],
        4,
    )
    assert result["ok"] is True


def test_a_name_that_shares_a_word_with_another_does_not_steal_its_amount():
    """
    GUARD. Two listed names sharing a word — "tomato paste" and "tomato
    sauce" — are told apart by how many of each name's words are in the
    window: the sauce matches on both of its, the paste on one. Scored on
    position alone the two tie at the word they share and the first-listed
    takes the amount, which here is the paste — so the sauce's cup lands
    on the paste and the recipe, which is right, is reported. Pinned by
    the mutation that scores on position alone (1 red).

    The paste is split across two steps for the measured reason the
    clean-recipe guard above records: a single step amount equal to the
    list amount is enough on its own, so with the paste's whole 2 tbsp in
    one step the stolen cup changed nothing and the mutation reddened
    nothing.
    """
    result = tools.check_steps_ingredients_consistency(
        [{"item": "Tomato paste", "qty": "2 tbsp"}, {"item": "Tomato sauce", "qty": "1 cup"}],
        [
            "Fry 1 tbsp tomato paste for 1 minute, until it darkens.",
            "Stir in the remaining 1 tbsp tomato paste.",
            "Stir in 1 cup tomato sauce and simmer 20 minutes.",
        ],
        4,
    )
    assert result["amount_mismatches"] == []


def test_the_chicken_in_the_stock_is_not_the_chicken_in_the_thighs():
    """GUARD, the same rule on the shape it was written for."""
    result = tools.check_steps_ingredients_consistency(
        [{"item": "Chicken thighs", "qty": "4"}, {"item": "Chicken stock", "qty": "2 cups"}],
        ["Sear the 4 chicken thighs.", "Pour in 2 cups chicken stock and simmer 20 minutes."],
        4,
    )
    assert result["ok"] is True


def test_a_batch_browned_in_two_goes_is_not_a_shortfall():
    """
    CATCH, and the one false positive a sweep of twelve realistic
    self-consistent recipes turned up before anything shipped. "Brown 1 lb
    of ground beef... push aside and brown the remaining 1 lb" is two
    pounds of a two-pound list, and the second amount names nothing at all
    — the beef is in the previous step, so no positional rule can reach
    it. An amount nobody can pin down means the sum is incomplete, and a
    sum that might be short is not evidence of anything.
    """
    result = tools.check_steps_ingredients_consistency(
        [{"item": "Ground beef", "qty": "2 lbs"}],
        [
            "Brown 1 lb of ground beef in a large pot over medium-high, 8 minutes.",
            "Push aside and brown the remaining 1 lb, another 8 minutes.",
        ],
        4,
    )
    assert result["amount_mismatches"] == []


def test_an_unpinned_amount_only_quiets_its_own_unit_family():
    """
    GUARD on how far that goes. A stray weight says nothing about a
    count — "the remaining 1 lb" casts no doubt on four chicken thighs.
    Pinned by the mutation that lets any stray quiet any ingredient
    (1 red).
    """
    result = tools.check_steps_ingredients_consistency(
        [{"item": "Ground beef", "qty": "2 lbs"}, {"item": "Chicken thighs", "qty": "4"}],
        [
            "Brown 1 lb of ground beef, 8 minutes.",
            "Push aside and brown the remaining 1 lb, another 8 minutes.",
            "Sear 2 chicken thighs alongside.",
        ],
        4,
    )
    assert [m["item"] for m in result["amount_mismatches"]] == ["Chicken thighs"]


def test_an_amount_of_water_is_accounted_for_and_quiets_nothing():
    """
    GUARD, and it is what keeps the stray rule from gutting the check. A
    step adding three cups of water names a thing the app has a word for
    (_STEP_ONLY_WORDS), so it is accounted for rather than a stray — and
    without that, every recipe that adds water in cups would stop having
    its cup-measured ingredients checked at all. Pinned by the mutation
    that drops the known-food test (1 red).
    """
    result = tools.check_steps_ingredients_consistency(
        [{"item": "Rice", "qty": "2 cups"}],
        ["Rinse 1 cup rice.", "Add 3 cups water, cover, and cook 18 minutes."],
        4,
    )
    assert result["amount_mismatches"] == [
        {"item": "Rice", "listed": "2 cups", "in_steps": "1 cup"},
    ]


def test_a_unicode_fraction_is_read_as_a_fraction():
    """CATCH. A model writes "1½ cups" as readily as "1 1/2 cups", and a
    fraction this cannot read is a mismatch it cannot see."""
    assert tools.check_steps_ingredients_consistency(
        [{"item": "Cashews", "qty": "3 cups"}], ["Blend ½ cup of cashews."], 4,
    )["ok"] is False
    assert tools.check_steps_ingredients_consistency(
        [{"item": "Cashews", "qty": "1.5 cups"}], ["Blend 1½ cups of cashews."], 4,
    )["ok"] is True


def test_the_check_never_raises_on_anything_it_is_handed():
    """GUARD. Its own docstring's promise — an observation, not a gate."""
    for ingredients, instructions in [
        ([], []),
        ([{"item": "Rice", "qty": "2 cups"}], []),
        ([], ["Cook 2 cups rice."]),
        (["Rice"], ["Cook 2 cups rice."]),
        ([{"item": "", "qty": ""}], [""]),
        ([{"item": "Rice", "qty": None}], [None]),
        ([{"item": "Rice", "qty": "0 cups"}], ["Cook 2 cups rice."]),
    ]:
        assert "ok" in tools.check_steps_ingredients_consistency(ingredients, instructions, 4)


def test_the_cook_facing_amount_is_what_a_step_is_held_to():
    """
    CATCH on the choice of comparison target. The number a step has to
    agree with is the one the Cook screen prints beside it — a stored
    cook_qty, not the shopping qty the grocery list reads. "1 bottle" of
    oil is not a number any step could repeat.
    """
    listed = [{"item": "Cashews", "qty": "1 bag", "cook_qty": "3 cups"}]
    assert tools.check_steps_ingredients_consistency(
        listed, ["Blend 3 cups of cashews until smooth."], 4,
    )["ok"] is True
    assert tools.check_steps_ingredients_consistency(
        listed, ["Blend 1 cup of cashews until smooth."], 4,
    )["ok"] is False


# --------------------------------------------------------------------------
# 5. The wording, and the morning report
# --------------------------------------------------------------------------

def test_the_message_says_which_amounts_disagree():
    """CATCH. One sentence, shared by plan_quality's rule and both writers."""
    message = plan_quality.steps_ingredients_message({
        "amount_mismatches": [{"item": "Cashews", "listed": "3 cups", "in_steps": "1 cup"}],
    })
    assert message == "the steps use 1 cup of Cashews where the list says 3 cups"


def test_the_message_still_leads_with_nothing_dated():
    """
    GUARD on the 2026-10-01 collapse rule: steps_match_ingredients is a
    RECIPE rule, so its line names the recipe and never the night, or one
    recipe's fault prints once per night it is on.
    """
    entries = [{
        "date": "2026-10-06", "slot": "dinner", "slot_state": "planned",
        "meal_name": "Cashew Curry", "reasoning": "quick", "is_new_recipe": False,
        "default_servings": 4,
        "ingredients": [{"item": "Cashews", "qty": "3 cups"}],
        "instructions": ["Blend 1 cup of cashews until smooth."],
    }]
    violations = plan_quality._steps_match_ingredients(entries, {})

    assert len(violations) == 1
    assert violations[0].message.startswith("Cashew Curry:")
    assert "2026-10-06" not in violations[0].message


def test_the_rule_judges_the_amounts_at_the_weeks_own_table_size():
    """
    CATCH. The amount a step has to agree with depends on how many people
    the recipe is written for — a cup of butter shows as 1 tbsp for two
    and 2 tbsp for four — so the rule has to hand the check the entry's
    own default_servings rather than letting it fall back to the
    documented four. Pinned by the mutation that stops passing it
    (1 red).
    """
    def _entry(servings):
        return {
            "date": "2026-10-06", "slot": "dinner", "slot_state": "planned",
            "meal_name": "Butter Sauce", "reasoning": "quick", "is_new_recipe": False,
            "default_servings": servings,
            "ingredients": [{"item": "Butter", "qty": "1 cup"}],
            "instructions": ["Melt 1 tbsp butter over low heat, 2 minutes, until foaming."],
        }

    assert plan_quality._steps_match_ingredients([_entry(2)], {}) == []
    assert len(plan_quality._steps_match_ingredients([_entry(4)], {})) == 1


def test_the_plan_quality_rule_reports_an_amount_mismatch(caplog):
    """CATCH. The card's "log it to the morning report as a recipe
    defect" — steps_match_ingredients is where that line comes out."""
    entries = [{
        "date": "2026-10-06", "slot": "dinner", "slot_state": "planned",
        "meal_name": "Cashew Curry", "reasoning": "quick", "is_new_recipe": False,
        "default_servings": 4,
        "ingredients": [{"item": "Cashews", "qty": "3 cups"}],
        "instructions": ["Blend 1 cup of cashews until smooth."],
    }]
    violations = plan_quality._steps_match_ingredients(entries, {})

    assert [v.rule for v in violations] == ["steps_match_ingredients"]
    assert "3 cups" in violations[0].message and "1 cup" in violations[0].message
    # Still info: a soft signal about a recipe, never a broken rule about
    # the week, and never a flipped exit code on the morning report.
    assert violations[0].severity == "info"


# --------------------------------------------------------------------------
# 6. The repair, with the model stubbed
# --------------------------------------------------------------------------

class _Usage:
    input_tokens = 10
    cache_read_input_tokens = 0
    cache_creation_input_tokens = 0
    output_tokens = 10


def _tool_block(name, tool_input):
    return types.SimpleNamespace(type="tool_use", name=name, input=tool_input, id="tu_1")


def _response(*blocks):
    return types.SimpleNamespace(content=list(blocks), stop_reason="tool_use", usage=_Usage())


class _FakeMessages:
    def __init__(self, responses):
        self._responses = list(responses)
        self.prompts: list[str] = []
        self.tools_seen: list[str] = []

    def create(self, **kwargs):
        self.prompts.append(kwargs["messages"][0]["content"])
        self.tools_seen.append(kwargs["tools"][0]["name"])
        return self._responses.pop(0)


def _stub(monkeypatch, *responses):
    fake = types.SimpleNamespace(messages=_FakeMessages(responses))
    monkeypatch.setattr(agent, "_client", lambda: fake)
    return fake.messages


_BAD = (
    [{"item": "Cashews", "qty": "3 cups"}, {"item": "Rice", "qty": "2 cups"}],
    ["Soak 1 cup of cashews in hot water.", "Cook 2 cups rice.", "Blend the cashews and serve."],
)


def test_the_repair_is_asked_once_with_the_mismatch_named(monkeypatch):
    """CATCH. The card: "one targeted rewrite of the steps (or the list)
    with the mismatch named"."""
    messages = _stub(monkeypatch, _response(_tool_block("submit_recipe_amounts", {
        "instructions": [
            "Soak the 3 cups of cashews in hot water.",
            "Cook 2 cups rice.",
            "Blend the cashews and serve.",
        ],
        "cooking_quantities": [],
    })))

    instructions, ingredients, check = agent._settle_recipe_amounts(
        "Cashew Curry", _BAD[0], _BAD[1], 4,
    )

    assert len(messages.prompts) == 1, "exactly one repair call"
    assert messages.tools_seen == ["submit_recipe_amounts"]
    # The mismatch is named in the ask, in the app's own numbers.
    assert "Cashews" in messages.prompts[0]
    assert "the list says 3 cups" in messages.prompts[0]
    assert "the steps use 1 cup" in messages.prompts[0]
    # And the repaired steps are what gets saved.
    assert check["ok"] is True
    assert instructions[0] == "Soak the 3 cups of cashews in hot water."
    assert ingredients == _BAD[0]


def test_the_repair_may_correct_the_list_instead_of_the_steps(monkeypatch):
    """CATCH. The card's "(or the list)": a step that was right all along
    over a list amount that was the wrong number."""
    _stub(monkeypatch, _response(_tool_block("submit_recipe_amounts", {
        "instructions": _BAD[1],
        "cooking_quantities": [{"item": "Cashews", "cook_qty": "1 cup"}],
    })))

    _instructions, ingredients, check = agent._settle_recipe_amounts(
        "Cashew Curry", _BAD[0], _BAD[1], 4,
    )

    assert check["ok"] is True
    assert ingredients[0]["cook_qty"] == "1 cup"
    # The shopping amount is never rewritten — that is the grocery list's.
    assert ingredients[0]["qty"] == "3 cups"


def test_a_repair_may_never_add_an_ingredient_to_the_list(monkeypatch):
    """
    GUARD, and it is the allergen gate's safety that rests on it. The
    repair runs after the gate has passed THIS ingredient list, so a
    corrected amount for a name the list hasn't got is dropped rather
    than appended. Pinned by the mutation that appends an unknown item
    (1 red).
    """
    _stub(monkeypatch, _response(_tool_block("submit_recipe_amounts", {
        "instructions": [
            "Soak the 3 cups of cashews in hot water.",
            "Cook 2 cups rice.",
            "Blend the cashews and serve.",
        ],
        "cooking_quantities": [{"item": "Peanut butter", "cook_qty": "2 tbsp"}],
    })))

    _instructions, ingredients, _check = agent._settle_recipe_amounts(
        "Cashew Curry", _BAD[0], _BAD[1], 4,
    )

    assert [i["item"] for i in ingredients] == ["Cashews", "Rice"]


def test_a_repair_that_is_no_better_is_discarded(monkeypatch):
    """
    CATCH. A rewrite that fixes one amount and loses an ingredient is not
    a repair; the recipe that already passed the allergen gate is the one
    worth keeping.
    """
    _stub(monkeypatch, _response(_tool_block("submit_recipe_amounts", {
        # Still 1 cup of a 3-cup list, and now the rice is gone from the
        # steps as well.
        "instructions": ["Soak 1 cup of cashews in hot water.", "Blend the cashews and serve."],
        "cooking_quantities": [],
    })))

    instructions, ingredients, check = agent._settle_recipe_amounts(
        "Cashew Curry", _BAD[0], _BAD[1], 4,
    )

    assert instructions == _BAD[1]
    assert ingredients == _BAD[0]
    assert check["ok"] is False


def test_a_clean_recipe_costs_no_repair_call(monkeypatch):
    """CATCH on the cost. A recipe whose amounts add up never reaches the
    model at all."""
    messages = _stub(monkeypatch)

    instructions, ingredients, check = agent._settle_recipe_amounts(
        "Cashew Curry",
        [{"item": "Cashews", "qty": "3 cups"}],
        ["Soak the 3 cups of cashews, then blend until smooth."],
        4,
    )

    assert messages.prompts == []
    assert check["ok"] is True
    assert instructions == ["Soak the 3 cups of cashews, then blend until smooth."]
    assert ingredients == [{"item": "Cashews", "qty": "3 cups"}]


def test_a_failed_repair_keeps_the_recipe_rather_than_losing_it(monkeypatch):
    """
    GUARD. The card: "If it still fails, keep the recipe but log it to the
    morning report as a recipe defect." A household with a slightly-off
    recipe is better off than one with no dinner. Pinned by the mutation
    that raises instead of returning the recipe as written (1 red, not
    the 4 the first version of this docstring claimed).

    It is ALSO red under the stub that turns the whole feature off, and
    for a reason other than its own claim: with nothing checking the
    amounts, `check["ok"]` is True and the last assertion fails before the
    keep-the-recipe one is reached.
    """
    def _boom():
        raise RuntimeError("no API key")
    monkeypatch.setattr(agent, "_client", _boom)

    instructions, ingredients, check = agent._settle_recipe_amounts(
        "Cashew Curry", _BAD[0], _BAD[1], 4,
    )

    assert instructions == _BAD[1]
    assert ingredients == _BAD[0]
    assert check["ok"] is False


def test_a_repair_that_answers_with_no_steps_keeps_the_recipe(monkeypatch):
    """GUARD. An empty answer is not a repair."""
    _stub(monkeypatch, _response(_tool_block("submit_recipe_amounts", {
        "instructions": [], "cooking_quantities": [],
    })))

    instructions, _ingredients, check = agent._settle_recipe_amounts(
        "Cashew Curry", _BAD[0], _BAD[1], 4,
    )

    assert instructions == _BAD[1]
    assert check["ok"] is False


# --------------------------------------------------------------------------
# 7. The repair is wired into the writers, and it runs after the allergen
#    gate — which is what makes "the repair can't add an ingredient" the
#    safety property it is.
# --------------------------------------------------------------------------

def test_the_recipe_pass_settles_the_amounts_before_it_saves():
    """
    GUARD, read off the source. _write_one_pending_recipe's own ordering:
    the allergen check, then the amount settle, then the save. Pinned by
    the mutation that moves the settle above the clash check (1 red, and
    it is the one ordering here that is a safety question rather than a
    quality one).
    """
    import inspect

    body = inspect.getsource(agent._write_one_pending_recipe)
    clash_at = body.index("hard_clashes(plain, ingredients=ingredients")
    settle_at = body.index("_settle_recipe_amounts(")
    save_at = body.index("tools.fill_recipe_details(")
    assert clash_at < settle_at < save_at


def test_the_cook_screens_fill_settles_the_amounts_too():
    """GUARD. The other writer a household reaches: "Fill in this recipe"
    on a recipe that has a list and no steps."""
    import inspect

    body = inspect.getsource(agent.fill_in_recipe)
    assert "_settle_recipe_amounts(" in body
    assert body.index("_settle_recipe_amounts(") < body.index("tools.update_recipe_details(")


def test_both_prompts_ask_for_amounts_that_add_up():
    """
    GUARD on the root fix. The check and the repair are the backstop; the
    writer being ASKED is what makes most recipes right first time.
    """
    assert "THE AMOUNTS HAVE TO ADD UP" in agent.RECIPE_DETAILS_INSTRUCTIONS
    assert "add up" in inspect_source(agent.generate_recipe_detail_llm)


def inspect_source(fn) -> str:
    import inspect

    return inspect.getsource(fn)
