"""
The Cook screen's "Cooking for" stepper follows the recipe the night cooks
NOW, and keeps the side's ingredients (defect hunt, 2026-10-31).

Driven at 390px on a real server: tap + on Friday's burrito bowls (3), then
Change recipe to Beef Tacos — the tacos' card read "1 ½ lbs chicken thighs,
1 cup + 2 tbsp jasmine rice…" under "Beef Tacos", because the stepper's
stored list is keyed by the plan entry and was put back over whatever the
entry cooked next. And the same tap dropped the Green salad's romaine and
cucumber from the list while "Alongside — Green salad" stayed in the steps,
because /api/recipes/scale only knows the recipe.
"""
from __future__ import annotations

import json
import shutil

import pytest

import nodeharness
import test_cook_journey as cj
from app import tools
from conftest import household_today

_needs_node = pytest.mark.skipif(shutil.which("node") is None, reason="node runs the screen's own functions")

_MEAL = dict(
    cj._MEAL,
    recipe_sig="sig-a",
    ingredients=[
        {"qty": "4", "item": "Chicken thighs"},
        {"qty": "2 tbsp", "item": "Olive oil", "substitute": "avocado oil"},
        {"qty": "1 head", "item": "Romaine", "from_side": "Green salad"},
    ],
    instructions=["Roast the chicken.", "Alongside — Green salad: Toss it."],
)

# The scale reply knows only the recipe: no side rows, no substitute note.
_SCALE_FETCH = (
    "fetch = function (url) {\n"
    "  var n = parseInt(/servings=(\\d+)/.exec(url)[1], 10);\n"
    "  var body = { scaled_ingredients: [{ qty: String(n), item: 'Chicken thighs' },\n"
    "                                    { qty: (n / 2) + ' tbsp', item: 'Olive oil' }],\n"
    "               scaled_instructions: ['Roast the chicken.'], unscaled_items: [] };\n"
    "  return Promise.resolve({ ok: true, json: function () { return Promise.resolve(body); } });\n"
    "};\n"
)


def _run_js(body: str) -> object:
    base = {
        "data": dict(cj._VIEW, meals=[_MEAL]), "meals": [], "focusIdx": 0,
        "focusStage": "recipe", "serves": {}, "servesSeq": 0, "focusMealKey": "e41",
        "stepIdx": 0, "ticks": None, "ticksFor": None, "pendingScrollTop": False,
    }
    harness = (
        cj._STUBS
        + f"var cookState = {json.dumps(base)};\n"
        + f"var FRESH = {json.dumps(_MEAL)};\n"
        + cj._string_const("COOK_TICKS_PREFIX") + "\n"
        + cj._var_block("HUMAN_QTY_FRACTIONS") + "\n"
        + "\n".join(cj._extract(n) for n in cj._FUNCTIONS) + "\n"
        + _SCALE_FETCH
        + "function fresh(over) { var m = JSON.parse(JSON.stringify(FRESH));"
        + " Object.keys(over || {}).forEach(function (k) { m[k] = over[k]; }); return m; }\n"
        + "var RELOAD = function () { cookState.ticksFor = null; cookState.ticks = null;"
        + " cookState.serves = {}; cookReadTicks(); };\n"
        + body
    )
    res = nodeharness.run_node(harness, timeout=30)
    assert res.returncode == 0, f"node failed: {res.stderr}"
    return json.loads(res.stdout.strip())


@_needs_node
def test_a_stepper_tap_keeps_the_sides_ingredients_and_the_swap_note():
    got = _run_js(
        "(async function () {\n"
        "  await cookStepServings(fakeStepper(0, 2, 'Sheet-pan chicken thighs', 4));\n"
        "  var tapped = cookState.data.meals[0].ingredients;\n"
        "  RELOAD();\n"
        "  var m = fresh(); cookApplyServesOverride([m]);\n"
        "  console.log(JSON.stringify({ tapped: tapped, reloaded: m.ingredients }));\n"
        "})();"
    )
    want = [
        {"qty": "6", "item": "Chicken thighs"},
        {"qty": "3 tbsp", "item": "Olive oil", "substitute": "avocado oil"},
        {"qty": "1 head", "item": "Romaine", "from_side": "Green salad"},
    ]
    assert got["tapped"] == want
    assert got["reloaded"] == want, "a reload reads the same list back"


@_needs_node
def test_change_recipe_drops_the_last_dishes_count():
    got = _run_js(
        "(async function () {\n"
        "  await cookStepServings(fakeStepper(0, 2, 'Sheet-pan chicken thighs', 4));\n"
        "  RELOAD();\n"
        "  var tacos = fresh({ meal: 'Beef Tacos', recipe_sig: 'sig-b', default_servings: 2,\n"
        "    ingredients: [{ qty: '1 lb', item: 'Beef mince' }], instructions: ['Brown the beef.'] });\n"
        "  cookApplyServesOverride([tacos]);\n"
        "  console.log(JSON.stringify({ m: tacos, kept: Object.keys(cookState.serves) }));\n"
        "})();"
    )
    assert got["m"]["ingredients"] == [{"qty": "1 lb", "item": "Beef mince"}]
    assert got["m"]["default_servings"] == 2
    assert not got["m"].get("serves_overridden")
    assert got["kept"] == [], "the stale count is forgotten, not kept for later"


@_needs_node
def test_a_recipe_rewritten_in_place_drops_the_old_count():
    """A Tweak keeps the recipe's id and name; only its signature moves."""
    got = _run_js(
        "(async function () {\n"
        "  await cookStepServings(fakeStepper(0, 2, 'Sheet-pan chicken thighs', 4));\n"
        "  var same = fresh(); cookApplyServesOverride([same]);\n"
        "  var rewritten = fresh({ recipe_sig: 'sig-a2',\n"
        "    ingredients: [{ qty: '1 block', item: 'Tofu' }] });\n"
        "  cookApplyServesOverride([rewritten]);\n"
        "  console.log(JSON.stringify({ same: same.default_servings, m: rewritten }));\n"
        "})();"
    )
    assert got["same"] == 6, "the same recipe keeps the cook's count"
    assert got["m"]["ingredients"] == [{"qty": "1 block", "item": "Tofu"}]
    assert got["m"]["default_servings"] == 4


def test_the_cook_card_carries_a_signature_that_moves_with_the_recipe():
    tools.add_recipe("Fried Rice", [{"item": "Rice", "qty": "2 cups", "category": "pantry"}],
                     instructions=["Cook the rice."], default_servings=4)
    day = household_today()
    plan_id = tools.create_weekly_plan(day.isoformat(), day_count=7)["weekly_plan_id"]
    tools.plan_meal(day.isoformat(), "Fried Rice", slot="dinner", weekly_plan_id=plan_id)

    def sig():
        return tools.get_cooker_view(plan_id)["meals"][0]["recipe_sig"]

    first = sig()
    assert first and first == sig(), "stable while the recipe is unchanged"
    tools.update_recipe_details("Fried Rice", instructions=["Cook the rice.", "Scramble 3 eggs and fold in."])
    assert sig() != first
