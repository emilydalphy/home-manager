"""
A batch night's steps say the batch's amounts (QA walk, 2026-10-02).

A household of three, Friday's Chicken Fajita Bowls doubled for Saturday's
lunch: the header said "Cooking for 6", the list said "2 cups Rice", and
step one said "add 1 cup rice" — with the "2 cups Rice" chip directly under
it in the step view. Root cause: every scaling pass (cooker.
_scale_card_to_batch for a batch, get_cooker_view's attendance pass for an
ordinary night, the Cook screen's stepper) rescaled the ingredient list via
recipes.scale_recipe and handed the steps over exactly as the recipe wrote
them, at its own default_servings.

recipes.scale_steps rewrites only amounts of food (a number + a measuring
word, or a bare count + something on the ingredient list) with
scale_recipe's own arithmetic, and leaves every other number alone.
"""
import datetime
import json
import shutil

import pytest

from app import tools
from app.tools import recipes as _recipes

import test_cook_journey as cj
import nodeharness


# ---------- scale_steps itself ----------

INGS = ["Rice", "Olive oil", "Ground cumin", "Limes", "Garlic", "Eggs", "Butter", "Tomatoes"]


def test_the_reported_steps_come_out_at_the_batch_size():
    steps = [
        "Rinse and add 1 cup rice to 2 cups water; simmer 18 minutes.",
        "Toss the chicken with 1 tbsp olive oil, 1 tsp cumin and the juice of 1 lime.",
        "Serve with lime wedges from the second lime.",
    ]
    assert _recipes.scale_steps(steps, 2, INGS) == [
        "Rinse and add 2 cups rice to 4 cups water; simmer 18 minutes.",
        "Toss the chicken with 2 tbsp olive oil, 2 tsp cumin and the juice of 2 limes.",
        "Serve with lime wedges from the second lime.",
    ]


def test_numbers_that_are_not_amounts_of_food_are_left_alone():
    steps = [
        "Preheat the oven to 400°F. Grease a 9x13 dish.",
        "Cut into 1-inch pieces and bake 20-25 minutes.",
        "Add a 14 oz can of tomatoes. Use a 2 quart saucepan.",
        "Divide among 4 bowls.",
    ]
    assert _recipes.scale_steps(steps, 2, INGS) == steps


def test_a_ratio_of_one_changes_nothing():
    steps = ["Add 1 cup rice."]
    assert _recipes.scale_steps(steps, 1, INGS) == steps


def test_fractions_ranges_and_whole_things_follow_scale_recipes_rules():
    got = _recipes.scale_steps(
        [
            "Add ½ cup rice, 1 ½ cups stock and 2-3 tbsp oil.",
            "Mince 2 garlic cloves; melt 1/2 stick butter.",
            "Beat 2 eggs.",
        ],
        0.75,
        INGS,
    )
    assert got[0] == "Add ⅜ cup rice, 1 ⅛ cups stock and 1 ½-2 ¼ tbsp oil."
    # A clove only comes whole (scale_recipe's _DISCRETE_UNITS); a stick
    # cut to a fraction is tablespoons.
    assert got[1] == "Mince 2 garlic cloves; melt 3 tbsp butter."
    assert got[2] == "Beat 1 ½ eggs."


def test_the_count_word_agrees_with_the_new_number():
    assert _recipes.scale_steps(["Beat 2 eggs."], 0.5, INGS) == ["Beat 1 egg."]
    assert _recipes.scale_steps(["Squeeze 1 lime."], 3, INGS) == ["Squeeze 3 limes."]
    assert _recipes.scale_steps(["Add 1 cup rice."], 0.5, INGS) == ["Add ½ cup rice."]


def test_scale_recipe_hands_back_the_steps_at_the_new_size():
    tools.add_recipe(
        "Fajita Bowls",
        ingredients=[{"item": "Rice", "qty": "1 cup"}, {"item": "Limes", "qty": "2"}],
        instructions=["Add 1 cup rice.", "Juice 1 lime."],
        default_servings=3,
    )
    got = tools.scale_recipe("Fajita Bowls", 6)
    assert got["scaled_instructions"] == ["Add 2 cups rice.", "Juice 2 limes."]


# ---------- the Cook view ----------

def _monday() -> datetime.date:
    today = datetime.date.today()
    return today - datetime.timedelta(days=today.weekday())


def _day(n: int) -> str:
    return (_monday() + datetime.timedelta(days=n)).isoformat()


FRI, SAT = _day(4), _day(5)


def _bowls(default_servings=3):
    tools.add_recipe(
        "Chicken Fajita Bowls",
        ingredients=[
            {"item": "Rice", "qty": "1 cup"},
            {"item": "Chicken breast", "qty": "1 lb"},
            {"item": "Limes", "qty": "2"},
        ],
        instructions=[
            "Rinse and add 1 cup rice to a pot with 2 cups water.",
            "Season the chicken and sear 6 minutes a side at medium-high.",
            "Squeeze over the juice of 1 lime; lime wedges from the second lime.",
        ],
        default_servings=default_servings,
    )


def test_a_doubled_night_says_the_batch_amounts_in_its_steps():
    for n in ("Alex", "Sam", "Rae"):
        tools.add_member(n)
    _bowls()
    plan_id = tools.create_weekly_plan(_monday().isoformat())["weekly_plan_id"]
    tools.plan_meal(FRI, "Chicken Fajita Bowls", slot="dinner", weekly_plan_id=plan_id)
    tools.plan_meal(SAT, "Chicken Fajita Bowls", slot="dinner", weekly_plan_id=plan_id,
                    derived_from={"links_to": f"{FRI}:dinner"})
    tools.repair_leftover_chains(plan_id)

    by_date = {m["date"]: m for m in tools.get_cooker_view(plan_id)["meals"]}
    cook, reheat = by_date[FRI], by_date[SAT]

    assert cook["servings"] == 6
    assert {i["item"]: i["qty"] for i in cook["ingredients"]}["Rice"] == "2 cups"
    assert cook["instructions"] == [
        "Rinse and add 2 cups rice to a pot with 4 cups water.",
        "Season the chicken and sear 6 minutes a side at medium-high.",
        "Squeeze over the juice of 2 limes; lime wedges from the second lime.",
    ]
    # The reheat night is still not a cook.
    assert reheat["is_leftovers"] is True and reheat["instructions"] == []


def test_an_ordinary_night_sized_to_the_table_says_the_same_in_its_steps():
    for n in ("Alex", "Sam"):
        tools.add_member(n)
    _bowls(default_servings=4)
    plan_id = tools.create_weekly_plan(_monday().isoformat())["weekly_plan_id"]
    tools.plan_meal(FRI, "Chicken Fajita Bowls", slot="dinner", weekly_plan_id=plan_id)

    card = tools.get_cooker_view(plan_id)["meals"][0]

    assert card["default_servings"] == 2
    assert {i["item"]: i["qty"] for i in card["ingredients"]}["Rice"] == "0.5 cup"
    assert card["instructions"][0] == "Rinse and add ½ cup rice to a pot with 1 cup water."


def test_a_sides_steps_stay_after_the_dishes_and_as_written():
    from app.tools import cooker as _cooker

    tools.add_recipe(
        "Rice Bowl",
        ingredients=[{"item": "Rice", "qty": "1 cup"}],
        instructions=["Add 1 cup rice."],
        default_servings=2,
    )
    card = {
        "meal": "Rice Bowl", "has_full_recipe": True, "default_servings": 2,
        "sides": [{"name": "Green salad", "instructions": ["Toss 1 cup greens."]}],
        "instructions": [],
    }
    assert _cooker._scale_card_to_batch(card, 4) is True
    assert card["instructions"] == ["Add 2 cups rice.", "Alongside — Green salad: Toss 1 cup greens."]


# ---------- the Cook screen's stepper ----------

_needs_node = pytest.mark.skipif(shutil.which("node") is None, reason="node runs the screen's own functions")

_STEPPED_MEAL = dict(
    cj._MEAL,
    instructions=["Add 2 cups rice.", "Alongside — Salad: Toss it."],
)


_SCALE_FETCH = (
    "fetch = function (url) {\n"
    "  var n = parseInt(/servings=(\\d+)/.exec(url)[1], 10);\n"
    "  var body = { scaled_ingredients: [{ qty: (n / 2) + ' cups', item: 'Rice' }],\n"
    "               scaled_instructions: ['Add ' + (n / 2) + ' cups rice.'], unscaled_items: [] };\n"
    "  return Promise.resolve({ ok: true, json: function () { return Promise.resolve(body); } });\n"
    "};\n"
)

# A page reload: the in-memory overrides are gone and come back only from
# the tick record in localStorage (cookReadTicks -> cookReadServes).
_RELOAD = "cookState.ticksFor = null; cookState.ticks = null; cookState.serves = {}; cookReadTicks();\n"


def _run_js(body: str) -> object:
    base = {
        "data": dict(cj._VIEW, meals=[_STEPPED_MEAL]), "meals": [], "focusIdx": 0,
        "focusStage": "recipe", "serves": {}, "servesSeq": 0, "focusMealKey": "e41",
        "stepIdx": 0, "ticks": None, "ticksFor": None, "pendingScrollTop": False,
    }
    harness = (
        cj._STUBS
        + f"var cookState = {json.dumps(base)};\n"
        + f"var STEPPED = {json.dumps(_STEPPED_MEAL)};\n"
        + cj._string_const("COOK_TICKS_PREFIX") + "\n"
        + cj._var_block("HUMAN_QTY_FRACTIONS") + "\n"
        + "\n".join(cj._extract(n) for n in cj._FUNCTIONS) + "\n"
        + _SCALE_FETCH
        + body
    )
    res = nodeharness.run_node(harness, timeout=30)
    assert res.returncode == 0, f"node failed: {res.stderr}"
    return json.loads(res.stdout.strip())


@_needs_node
def test_a_stepper_tap_moves_the_steps_and_keeps_the_sides_step():
    got = _run_js(
        "(async function () {\n"
        "  await cookStepServings(fakeStepper(0, 2, 'Sheet-pan chicken thighs', 4));\n"
        "  var after = cookState.data.meals[0].instructions.slice();\n"
        "  var fresh = JSON.parse(JSON.stringify(STEPPED));\n"
        "  cookApplyServesOverride([fresh]);\n"
        "  console.log(JSON.stringify({ after: after, fresh: fresh.instructions }));\n"
        "})();"
    )
    assert got["after"] == ["Add 3 cups rice.", "Alongside — Salad: Toss it."]
    assert got["fresh"] == ["Add 3 cups rice.", "Alongside — Salad: Toss it."]


@_needs_node
def test_the_scaled_steps_survive_a_reload_with_the_amounts():
    """Found on review: cookReadServes rebuilt each stored override from a
    whitelist without the steps, so a reload put the server's steps back
    under the cook's amounts — the original bug, one reload later."""
    got = _run_js(
        "(async function () {\n"
        "  await cookStepServings(fakeStepper(0, 2, 'Sheet-pan chicken thighs', 4));\n"
        + _RELOAD
        + "  var fresh = JSON.parse(JSON.stringify(STEPPED));\n"
        "  cookApplyServesOverride([fresh]);\n"
        "  console.log(JSON.stringify({ ings: fresh.ingredients, steps: fresh.instructions }));\n"
        "})();"
    )
    assert got["ings"] == [{"qty": "3 cups", "item": "Rice"}]
    assert got["steps"] == ["Add 3 cups rice.", "Alongside — Salad: Toss it."]


@_needs_node
def test_a_dish_swapped_onto_the_entry_keeps_its_own_steps():
    got = _run_js(
        "(async function () {\n"
        "  await cookStepServings(fakeStepper(0, 2, 'Sheet-pan chicken thighs', 4));\n"
        "  var swapped = JSON.parse(JSON.stringify(STEPPED));\n"
        "  swapped.meal = 'Lentil soup'; swapped.instructions = ['Simmer the lentils.'];\n"
        "  cookApplyServesOverride([swapped]);\n"
        "  console.log(JSON.stringify(swapped.instructions));\n"
        "})();"
    )
    assert got == ["Simmer the lentils."]


@_needs_node
def test_an_override_saved_before_steps_were_part_of_it_leaves_them_as_sent():
    """A tick record written by the previous build has no steps in its
    override; read back after a reload, the server's steps stand."""
    got = _run_js(
        "window.localStorage.setItem(COOK_TICKS_PREFIX + '12', JSON.stringify({ steps: {}, ings: {},\n"
        "  serves: { e41: { servings: 6, ingredients: [{ qty: '3 cups', item: 'Rice' }], unscaled_items: [] } } }));\n"
        + _RELOAD
        + "var fresh = JSON.parse(JSON.stringify(STEPPED));\n"
        "cookApplyServesOverride([fresh]);\n"
        "console.log(JSON.stringify({ ings: fresh.ingredients, steps: fresh.instructions }));\n"
    )
    assert got["ings"] == [{"qty": "3 cups", "item": "Rice"}], "the stored override was really read"
    assert got["steps"] == _STEPPED_MEAL["instructions"]


# ---------- found on review: what scale_steps must not touch ----------

REVIEW_INGS = ["Milk", "Ground beef", "Butter", "Lime wedges", "Bouillon cubes", "Potatoes",
               "Eggs", "Bay leaves", "Cherries", "Celery", "Cinnamon", "Tomatoes", "Rice"]


@pytest.mark.parametrize("step, doubled", [
    # The metric twin in brackets moves with the amount it restates...
    ("Add 1 cup (240 ml) milk.", "Add 2 cups (480 ml) milk."),
    ("Brown 1 lb (450 g) ground beef.", "Brown 2 lbs (900 g) ground beef."),
    ("Add 2 cups/500 ml milk.", "Add 4 cups/1000 ml milk."),
    # ...but a can's size is the can's.
    ("Add 1 can (14 oz) tomatoes.", "Add 2 cans (14 oz) tomatoes."),
    ("Add 2 (15 oz) cans tomatoes.", "Add 2 (15 oz) cans tomatoes."),
    # Counts of pieces and portions are not amounts of food.
    ("Cut each loaf into 8 slices.", "Cut each loaf into 8 slices."),
    ("Cut the lime into 8 wedges.", "Cut the lime into 8 wedges."),
    ("Cut the chicken into 16 cubes.", "Cut the chicken into 16 cubes."),
    ("Bake 1 potato per person.", "Bake 1 potato per person."),
    ("Use 1 cup milk per 2 people.", "Use 1 cup milk per 2 people."),
    ("Crack 1 egg into each of 4 cups.", "Crack 1 egg into each of 4 cups."),
    # Only a stick of BUTTER is ever tablespoons.
    ("Add 1 stick cinnamon.", "Add 2 sticks cinnamon."),
    ("Add 1 cup rice.", "Add 2 cups rice."),
])
def test_review_cases_at_double(step, doubled):
    assert _recipes.scale_steps([step], 2, REVIEW_INGS) == [doubled]


@pytest.mark.parametrize("step, halved", [
    ("Add 2 bay leaves.", "Add 1 bay leaf."),
    ("Pit 2 cherries.", "Pit 1 cherry."),
    ("Melt 1/2 stick butter.", "Melt 2 tbsp butter."),
    ("Cut 1 stick of celery.", "Cut 1 stick of celery."),
])
def test_review_cases_at_half(step, halved):
    assert _recipes.scale_steps([step], 0.5, REVIEW_INGS) == [halved]


def test_a_glued_metric_amount_gets_its_space_back():
    assert _recipes.scale_steps(["Add 250ml milk."], 0.75, REVIEW_INGS) == ["Add 187 ½ ml milk."]


# ---------- second review round: guards that over-reached ----------

@pytest.mark.parametrize("step, doubled", [
    ("Add between 1 and 2 cups stock.", "Add between 2 and 4 cups stock."),
    ("Stir 2 cups rice into 4 cups boiling water.", "Stir 4 cups rice into 8 cups boiling water."),
    ("Whisk the eggs into 1 cup milk.", "Whisk the eggs into 2 cups milk."),
    ("Add 1 cup each of rice and beans.", "Add 2 cups each of rice and beans."),
    ("Season with 1 tsp each salt and pepper.", "Season with 2 tsp each salt and pepper."),
    ("Add 2 to 3 cups water each time.", "Add 4 to 6 cups water each time."),
    ("Spoon 1 cup into each bowl.", "Spoon 1 cup into each bowl."),
    ("Serve 1 egg each.", "Serve 1 egg each."),
])
def test_second_review_cases_at_double(step, doubled):
    assert _recipes.scale_steps([step], 2, REVIEW_INGS) == [doubled]


def test_only_the_noun_a_stick_is_of_decides_whether_it_is_butter():
    got = _recipes.scale_steps(
        ["Add 1 stick celery and 2 tbsp butter.", "Melt 1 stick, then add the flour."],
        0.75,
        REVIEW_INGS,
    )
    assert got == ["Add 1 stick celery and 1 ½ tbsp butter.", "Melt 6 tbsp, then add the flour."]
