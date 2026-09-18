"""
The recipe is the recipe (Emily, 2026-09-18 — boards 13-recipe and
14-cooking; Loop Board "The recipe is the recipe" and "The step-by-step
view has no timestamps and no batch prompt").

The screen with a recipe on it — Plan's Meal step, and cook mode's
opening screen on Cook — is the dish, who it's for, what's in it and what
to do: the crumb, the dish as the title, "Cooking for · − · 4 · +", an
Ingredients card, a Steps card, and "Start cooking" in the dock. Gone from
it: the spruce hero, "On the table by …", "Start at 6:00" (on a chip and
on the dock), the timed stops, the prep/cook minute chips, the "for 6"
chip, the "enough for Wednesday and Saturday" lines and the batch-cooking
question ("Do you want to batch cook this? …"). The plan still holds the
times — Now and Cook's Tonight card read them — the recipe simply doesn't.

The cooker (one step at a time): "‹ Recipe", "Lemon chicken & orzo · step
2 of 4", a segmented progress bar, the step in a card with what it needs
as one quiet line, "Next: …" under it, and Back / Next step in the dock —
"Done — on the table" on the last, which marks the meal cooked exactly as
"Mark it cooked" did.

Until this the Meal step was "Meal · B · The clock" (2026-09-12) and cook
mode had three stages (Before you start / one step at a time / the whole
method); tests/test_meal_clock.py covered the clock and went with it. The
number-and-time helpers the Tonight card still reads (numberWord,
minutesInWords, clockLabel, slotTableMinutes, mealClockTotal) are pinned
here so nothing about them drifts while the screens that used them are
gone.

These run shell.js's own functions under node (tests/nodeharness.py), the
house standard for behaviour a source-marker test cannot see.
"""
from __future__ import annotations

import json
import re
import shutil
from pathlib import Path

import pytest

import nodeharness

REPO = Path(__file__).resolve().parent.parent
SHELL_JS = (REPO / "static" / "shell.js").read_text(encoding="utf-8")
SHELL_CSS = (REPO / "static" / "shell.css").read_text(encoding="utf-8")

_needs_node = pytest.mark.skipif(
    shutil.which("node") is None, reason="node is needed to execute shell.js's own functions"
)


def _extract(name: str) -> str:
    """Lift one brace-balanced `function name(...) {...}` out of shell.js."""
    start = SHELL_JS.index(f"function {name}(")
    i = SHELL_JS.index("{", start)
    depth, j = 0, i
    while True:
        if SHELL_JS[j] == "{":
            depth += 1
        elif SHELL_JS[j] == "}":
            depth -= 1
            if depth == 0:
                break
        j += 1
    return SHELL_JS[start : j + 1]


def _var(name: str) -> str:
    start = SHELL_JS.index(f"var {name} = ")
    end = SHELL_JS.index(";\n", start) + 1
    return SHELL_JS[start:end]


def _var_block(name: str) -> str:
    """A bracket-balanced `var NAME = {...};` (the icon sets)."""
    start = SHELL_JS.index(f"var {name} = {{")
    i = SHELL_JS.index("{", start)
    depth, j = 0, i
    while True:
        if SHELL_JS[j] == "{":
            depth += 1
        elif SHELL_JS[j] == "}":
            depth -= 1
            if depth == 0:
                break
        j += 1
    return SHELL_JS[start : j + 1] + ";"


# The number-and-time helpers that survive the clock (the Tonight card and
# the real-start readers still use them).
_PURE = (
    "function capitalizeFirst(s) { return String(s).charAt(0).toUpperCase() + String(s).slice(1); }\n"
    + _var("NUMBER_WORDS") + "\n"
    + _var("TENS_WORDS") + "\n"
    + _extract("isSnackSlot") + "\n"
    + "".join(_extract(n) + "\n" for n in (
        "numberWord", "countInWords", "minutesInWords", "clockLabel",
        "slotTableMinutes", "mealTotalMinutes", "mealClockSides", "mealClockTotal"))
)

_ESCAPE = (
    "function escapeHtml(s){return String(s == null ? '' : s)"
    ".replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/\"/g,'&quot;');}\n"
)

# The recipe renderers themselves, shared by both doors.
_RECIPE = (
    _var_block("RECIPE_ICONS") + "\n"
    + "var ICONS = { arrow: '<svg data-icon=\"arrow\"></svg>' };\n"
    + "var COOK_ICONS = { check: '<svg data-icon=\"check\"></svg>', mic: '<svg data-icon=\"mic\"></svg>' };\n"
    + "var COOK_VOICE_ENABLED = false;\n"
    + "function humanQtyText(t) { return String(t == null ? '' : t); }\n"
    + "function cookIngredientLabel(i) { return ((i.qty ? i.qty + ' ' : '') + i.item).trim(); }\n"
    + "function cookServesShown(m) { return m.serves_target != null ? m.serves_target : m.default_servings; }\n"
    + "function cookMealKey(m) { return 'e' + m.entry_id; }\n"
    + "function recipeCitationHtml() { return ''; }\n"
    + "".join(_extract(n) + "\n" for n in (
        "cookUnscaledHtml", "cookIngTickId", "cookGetOutRowHtml",
        "recipeTitleHtml", "recipeServesHtml", "recipeIngredientsHtml", "recipeIngredientRowHtml",
        "recipeStepsHtml"))
)


def _run(expr: str, prelude: str = "") -> object:
    res = nodeharness.run_node(_PURE + prelude + f"console.log(JSON.stringify({expr}));\n", timeout=30)
    assert res.returncode == 0, f"node failed: {res.stderr}"
    return json.loads(res.stdout.strip())


# ---------------------------------------------- the words that survive


@_needs_node
def test_numbers_are_words_up_to_twelve_and_minutes_are_said_aloud():
    got = _run(
        "[countInWords(6, 'stop'), countInWords(1, 'stop'), countInWords(12, 'stop'), countInWords(13, 'stop'),"
        " minutesInWords(30), minutesInWords(45), minutesInWords(60), minutesInWords(75),"
        " minutesInWords(90), minutesInWords(120), minutesInWords(130), minutesInWords(1),"
        " clockLabel(18 * 60 + 5), clockLabel(0), clockLabel(null)]"
    )
    assert got == [
        "six stops", "one stop", "twelve stops", "13 stops",
        "thirty minutes", "forty-five minutes", "an hour", "an hour and a quarter",
        "an hour and a half", "two hours", "two hours and ten minutes", "one minute",
        "6:05", "12:00", "",
    ]


@_needs_node
def test_slot_times_read_back_into_a_clock_the_slot_supplies_the_half_of_day():
    # get_week_menu's slot_times carry no am/pm (moves.py's _clock says the
    # time the way a person does), so the slot decides.
    got = _run(
        "[slotTableMinutes({ dinner: '6:30' }, 'dinner'), slotTableMinutes({ dinner: '5:30' }, 'dinner'),"
        " slotTableMinutes({ breakfast: '8:00' }, 'breakfast'), slotTableMinutes({ lunch: 'noon' }, 'lunch'),"
        " slotTableMinutes({ lunch: '12:30' }, 'lunch'), slotTableMinutes({ snack: '3:30' }, 'snack2'),"
        " slotTableMinutes({}, 'dinner'), slotTableMinutes(null, 'dinner'), slotTableMinutes({ dinner: '??' }, 'dinner')]"
    )
    assert got == [18 * 60 + 30, 17 * 60 + 30, 8 * 60, 12 * 60, 12 * 60 + 30, 15 * 60 + 30, None, None, None]


@_needs_node
def test_the_whole_plate_takes_the_dish_or_the_longest_side():
    got = _run(
        "[mealClockTotal({ prep_time_minutes: 10, cook_time_minutes: 20 }),"
        " mealClockTotal({ prep_time_minutes: 5, cook_time_minutes: 10,"
        "   sides: [{ name: 'Roasted potatoes', minutes: 25, instructions: ['Roast.'] }] }),"
        " mealClockTotal({ prep_time_minutes: 5, cook_time_minutes: 10, sides: [{ name: 'Rice', minutes: 25 }] }),"
        " mealClockTotal({ meta: '35 min' }), mealClockTotal({})]"
    )
    assert got == [30, 25, 15, 35, None]


# ---------------------------------------------- the recipe renderers


STIR_FRY = {
    "entry_id": 7,
    "meal": "Ginger Beef Stir-Fry",
    "has_full_recipe": True,
    "cooked_status": "pending",
    "default_servings": 4,
    "prep_time_minutes": 10,
    "cook_time_minutes": 20,
    "ingredients": [
        {"item": "Steak", "qty": "1 lb"},
        {"item": "Broccoli, chopped", "qty": "2 cups"},
        {"item": "Carrots", "qty": "2"},
        {"item": "Garlic", "qty": "3 cloves"},
        {"item": "Ginger", "qty": "1 tbsp"},
        {"item": "Rice", "qty": "1.5 cups"},
    ],
    "instructions": [
        "Heat the oil in a large pan over medium-high heat.",
        "Add the steak, sear 2 minutes a side.",
        "Toss in broccoli and carrots, cook until tender.",
        "Stir in garlic and ginger, then the sauce.",
        "Serve over rice.",
    ],
}


def _recipe(expr: str, ticked: list | None = None) -> str:
    prelude = (
        _ESCAPE
        + f"var TICKED = {json.dumps(ticked or [])};\n"
        + "function cookTicked(kind, key) { return TICKED.indexOf(kind + ':' + key) !== -1; }\n"
        + _RECIPE
    )
    return _run(expr, prelude)


@_needs_node
def test_the_servings_control_is_cooking_for_minus_count_plus_on_one_line():
    html = _recipe(f"recipeServesHtml({json.dumps(STIR_FRY)}, 0)")
    assert html.startswith('<div class="cook-serves recipe-serves"')
    assert 'data-recipe="Ginger Beef Stir-Fry" data-base="4"' in html
    assert '<span class="cook-serves-label">Cooking for</span>' in html
    assert html.count('data-cook="serves"') == 2
    assert 'data-delta="-1" aria-label="One fewer"' in html
    assert 'data-delta="1" aria-label="One more"' in html
    assert '<span class="cook-serves-count">4</span>' in html
    # Stroke icons, never "−"/"+" as text (Rule 7).
    assert 'stroke-width="2.2"' in html and ">+<" not in html
    # The cook's own count wins over the household's.
    assert '<span class="cook-serves-count">6</span>' in _recipe(f"recipeServesHtml({json.dumps(dict(STIR_FRY, serves_target=6))}, 0)")
    # Nothing to scale, nothing to show.
    assert _recipe(f"recipeServesHtml({json.dumps(dict(STIR_FRY, default_servings=None))}, 0)") == ""


@_needs_node
def test_the_ingredients_card_is_one_row_per_ingredient_with_a_box():
    live = _recipe(f"recipeIngredientsHtml({json.dumps(STIR_FRY)}, 0, true)", ticked=["ings:e7:steak"])
    assert live.startswith('<section class="card recipe-card recipe-ings" aria-label="Ingredients">')
    assert '<span class="cook-eyebrow recipe-eyebrow">Ingredients</span>' in live
    assert live.count('<li class="cook-getout-item') == 6
    # Live (cook mode): the row is the tick, and a ticked one reads so.
    assert live.count('data-cook="check-ing"') == 6
    assert 'class="cook-getout-item is-done"' in live and 'aria-pressed="true"' in live
    assert "1 lb Steak" in live and "1.5 cups Rice" in live
    # The plan's copy: the same rows, the box drawn, nothing to press.
    plain = _recipe(f"recipeIngredientsHtml({json.dumps(STIR_FRY)}, 'wk', false)")
    assert plain.count('<li class="cook-getout-item') == 6
    assert "data-cook" not in plain and "<button" not in plain
    assert plain.count('<span class="cook-box recipe-box" aria-hidden="true"></span>') == 6
    assert "1 lb Steak" in plain
    # Nothing without ingredients.
    assert _recipe(f"recipeIngredientsHtml({json.dumps(dict(STIR_FRY, ingredients=[]))}, 0, true)") == ""


@_needs_node
def test_the_plan_copy_keeps_the_plate_passs_tags():
    meal = dict(STIR_FRY, ingredients=[
        {"item": "Yukon Gold potatoes", "qty": "1 lb", "added": True},
        {"item": "Lemon", "qty": "1", "at_home": True},
        {"item": "Butter", "qty": "2 tbsp", "substitute": "olive oil"},
    ])
    html = _recipe(f"recipeIngredientsHtml({json.dumps(meal)}, 'wk', false)")
    assert '1 lb Yukon Gold potatoes <span class="wk-ing-tag">added</span>' in html
    assert '1 Lemon <span class="wk-ing-tag">at home</span>' in html
    assert '<span class="wk-ing-tag">using olive oil instead</span>' in html
    assert 'class="cook-getout-item is-added"' in html


@_needs_node
def test_the_steps_card_numbers_every_step_in_a_sand_tile():
    html = _recipe(f"recipeStepsHtml({json.dumps(STIR_FRY)}, true)")
    assert html.startswith('<section class="card recipe-card recipe-method" aria-label="Steps">')
    assert '<span class="cook-eyebrow recipe-eyebrow">Steps</span>' in html
    nums = re.findall(r'recipe-step-num" aria-hidden="true">(\d+)<', html)
    assert nums == ["1", "2", "3", "4", "5"]
    assert 'recipe-step-text">Heat the oil in a large pan over medium-high heat.</span>' in html
    # No times anywhere on the recipe.
    assert not re.search(r"\b\d{1,2}:\d{2}\b", html)
    assert "Start at" not in html and "min" not in html.replace("minutes a side", "")


@_needs_node
def test_a_make_ahead_step_keeps_its_do_ahead_word():
    meal = dict(STIR_FRY, advance_prep_step_indices=[2])
    html = _recipe(f"recipeStepsHtml({json.dumps(meal)}, true)")
    assert html.count('<li class="recipe-step is-ahead">') == 1
    assert '<span class="recipe-step-tag">Do ahead</span> Add the steak' in html


@_needs_node
def test_an_empty_recipe_says_so_and_only_cook_mode_offers_to_fill_it():
    empty = dict(STIR_FRY, instructions=[])
    live = _recipe(f"recipeStepsHtml({json.dumps(empty)}, true)")
    assert "No steps saved yet." in live
    assert 'class="cook-fill" data-cook="fill" data-recipe="Ginger Beef Stir-Fry">Fill in this recipe</button>' in live
    plain = _recipe(f"recipeStepsHtml({json.dumps(empty)}, false)")
    assert "No steps saved yet — ask me for the recipe in the chat." in plain
    assert "cook-fill" not in plain


# ---------------------------------------------- the Meal step on Plan


def _screen(day: dict, slot: str, cook_meals: list, ticked: list | None = None,
            plan_view: str = "undefined") -> str:
    """The Meal step, rendered by its own functions. `plan_view` is what
    planCookView answers ('undefined' leaves it out, as the tests that
    are not about the wait do)."""
    harness = (
        _ESCAPE
        + "function dayName(d, opts){ return 'Monday'; }\n"
        + "var weekState = { data: { slot_times: { breakfast: '8:00', lunch: '12:30', dinner: '6:30' } }, mealBack: 'day' };\n"
        + "var swapState = null;\n"
        + "var REHEAT_ACTION_LABEL = 'Mark eaten';\n"
        + "var SWAP_LABEL = 'Swap · I’ll pick';\n"
        + f"var cookState = {{ data: {{ meals: {json.dumps(cook_meals)} }} }};\n"
        + "var GRO_ICONS = { chevRight: '<svg class=\"chev\"></svg>' };\n"
        + "var SLOT_LABELS = { breakfast: 'Breakfast', lunch: 'Lunch', dinner: 'Dinner' };\n"
        + "var WK_ADD_ICON = '<svg/>';\n"
        + f"var TICKED = {json.dumps(ticked or [])};\n"
        + "function cookTicked(kind, key) { return TICKED.indexOf(kind + ':' + key) !== -1; }\n"
        + "function cookStartedMinutes(m) { return m && m.cook_started_at ? 1 : null; }\n"
        + (f"function planCookView() {{ return {plan_view}; }}\nfunction planCookViewFailed() {{ return false; }}\n"
           if plan_view != "undefined" else "")
        + _PURE + _RECIPE
        + "".join(_extract(n) + "\n" for n in (
            "daySlotEntry", "slotWord", "isRealCook", "mealDisplayName", "cookMealForEntry",
            "mealCookUnderway", "mealRecipeFor", "mealHeroLine", "mealNoRecipeHtml", "mealIngredientsHtml",
            "swapStateFor", "swapLineHtml", "mealDockHtml", "mealStepHtml"))
        + f"console.log(JSON.stringify(mealStepHtml({json.dumps(day)}, {json.dumps(slot)})));\n"
    )
    res = nodeharness.run_node(harness, timeout=30)
    assert res.returncode == 0, f"node failed: {res.stderr}"
    return json.loads(res.stdout.strip())


def _monday(dinner: dict | None) -> dict:
    return {
        "date": "2026-09-14", "isToday": False, "isPast": False,
        "breakfast": None, "lunch": None, "dinner": dinner, "snacks": [], "snack": None,
    }


_DINNER = {
    "state": "planned", "title": "Ginger Beef Stir-Fry", "source": "plan", "meta": "30 min",
    "entry_id": 7, "sides": [], "food_groups": ["protein", "vegetable", "carb"],
    "defrost": None, "plate_note": "",
}
_COOK_CARD = dict(STIR_FRY, is_leftovers=False, date="2026-09-14", slot="dinner",
                  attendance={"headcount": 4})


@_needs_node
def test_the_meal_step_is_the_crumb_the_title_the_count_the_cards_and_the_dock():
    html = _screen(_monday(_DINNER), "dinner", [_COOK_CARD])
    # The crumb names its parent.
    assert html.startswith('<button type="button" class="crumb" data-wk-back="day">‹ Monday</button>')
    # In order: title, Cooking for, Ingredients, Steps, the dock.
    order = [html.index(s) for s in (
        '<h1 class="recipe-title">Ginger Beef Stir-Fry</h1>',
        '<span class="cook-serves-label">Cooking for</span>',
        'aria-label="Ingredients"',
        'aria-label="Steps"',
        '<div class="wk-decide dock wk-meal-dock">',
    )]
    assert order == sorted(order)
    assert '<span class="cook-serves-count">4</span>' in html
    assert html.count('<li class="cook-getout-item') == 6
    assert re.findall(r'recipe-step-num" aria-hidden="true">(\d+)<', html) == ["1", "2", "3", "4", "5"]
    # The dock: Start cooking as the one action, the swap as the quiet link.
    assert 'class="dock-primary" data-wk-cook="dinner">Start cooking<' in html
    assert 'class="dock-link wk-act-swap" data-wk-swap="dinner">Swap · I’ll pick<' in html
    assert "Tell me what instead" not in html


@_needs_node
def test_nothing_about_time_or_the_batch_is_on_the_meal_step():
    card = dict(_COOK_CARD, cook_started_at="2026-09-14T18:02:00", servings=8,
                covers=[{"date": "2026-09-16", "slot": "dinner", "eaters": 4}],
                covers_note="Cooking for 8 — enough for Wednesday.",
                cook_ahead={"days": [{"entry_id": 11, "date": "2026-09-16", "eaters": 4, "selected": True}]},
                batch_note="Bulk", meal_count=2)
    html = _screen(_monday(_DINNER), "dinner", [card])
    for gone in ("Start at", "On the table", "Started ", "wk-stop", "wk-clock", "hero-chip", "dinner-hero",
                 "cook-ahead", "batch cook", "should it cover", "enough for", "covers ", "Cooking for 8",
                 "for 8", "m prep", "m cook", "Bulk", "wk-meal-hero", "Emily’s cooking", "thirty minutes"):
        assert gone not in html, gone
    assert not re.search(r"\b\d{1,2}:\d{2}\b", html), "no clock on the recipe"
    # The count is the one place the number lives — and it is the batch's.
    assert html.count("cook-serves-count") == 1


@_needs_node
def test_the_thaw_note_is_the_one_line_under_the_title():
    entry = dict(_DINNER, defrost={"note": "Steak out of the freezer Sunday night"})
    html = _screen(_monday(entry), "dinner", [_COOK_CARD])
    assert '</h1><p class="recipe-line">Steak out of the freezer Sunday night.</p>' in html
    # No line at all when there is nothing to thaw — not "Nothing to thaw."
    html = _screen(_monday(_DINNER), "dinner", [_COOK_CARD])
    assert "recipe-line" not in html and "Nothing to thaw" not in html


@_needs_node
def test_a_cook_already_under_way_offers_to_keep_cooking_without_a_time():
    html = _screen(_monday(_DINNER), "dinner", [_COOK_CARD], ticked=["steps:e7:0", "steps:e7:1"])
    assert 'data-wk-cook="dinner">Keep cooking<' in html
    # The real start on record says the same.
    started = dict(_COOK_CARD, cook_started_at="2026-09-14T18:02:00")
    html = _screen(_monday(_DINNER), "dinner", [started])
    assert 'data-wk-cook="dinner">Keep cooking<' in html and "6:02" not in html
    # Marked cooked: not under way any more, and the start is offered again.
    done = dict(_COOK_CARD, cooked_status="done")
    html = _screen(_monday(_DINNER), "dinner", [done], ticked=["steps:e7:0"])
    assert 'data-wk-cook="dinner">Start cooking<' in html


@_needs_node
def test_before_the_cook_view_loads_the_screen_waits_and_the_dock_still_starts():
    html = _screen(_monday(_DINNER), "dinner", [], plan_view="null")
    assert 'class="cook-norecipe recipe-norecipe">Getting the recipe…</p>' in html
    assert "Cooking for" not in html and 'aria-label="Steps"' not in html
    assert 'data-wk-cook="dinner">Start cooking<' in html
    # The view has answered and this entry is not on it: the no-recipe line.
    html = _screen(_monday(_DINNER), "dinner", [])
    assert "No saved recipe for this one — ask me for it in the chat." in html


@_needs_node
def test_a_dinner_with_no_recipe_says_so():
    card = {"entry_id": 7, "meal": "Ginger Beef Stir-Fry", "is_leftovers": False, "has_full_recipe": False,
            "ingredients": [], "instructions": [], "cooked_status": "pending"}
    html = _screen(_monday(_DINNER), "dinner", [card])
    assert "No saved recipe for this one" in html
    assert 'aria-label="Steps"' not in html and "Cooking for" not in html
    assert 'data-wk-cook="dinner">Start cooking<' in html


@_needs_node
def test_a_reheat_night_is_the_title_its_provenance_and_mark_eaten():
    entry = {
        "state": "planned", "title": "Leftovers — Sunday’s Bulgogi", "source": "leftovers", "meta": "reheat",
        "entry_id": 9, "sides": [], "food_groups": [], "defrost": None, "plate_note": "",
        "leftover_from": {"date": "2026-09-13", "meal": "Bulgogi", "cook_ahead": False},
    }
    card = {"entry_id": 9, "meal": "Bulgogi", "is_leftovers": True, "ingredients": [], "instructions": [],
            "has_full_recipe": False, "cooked_status": "pending"}
    html = _screen(_monday(entry), "dinner", [card])
    assert '<p class="recipe-line">Leftovers from Monday.</p>' in html  # dayName is stubbed to Monday
    assert "Cooking for" not in html and 'aria-label="Ingredients"' not in html and "No saved recipe" not in html
    assert 'data-wk-cook="dinner">Mark eaten<' in html


@_needs_node
def test_a_past_day_has_no_dock_but_the_recipe_is_still_worth_reading():
    day = dict(_monday(_DINNER), isPast=True)
    html = _screen(day, "dinner", [_COOK_CARD])
    assert "wk-meal-dock" not in html
    assert 'aria-label="Steps"' in html and "Cooking for" in html
    assert "data-wk-add" not in html


@_needs_node
def test_a_thing_can_still_be_added_from_the_ingredients():
    html = _screen(_monday(_DINNER), "dinner", [_COOK_CARD])
    assert 'class="wk-ing-add" data-wk-add="dinner"' in html
    assert "<span>Add something</span>" in html
    # Under the Ingredients card, before the steps.
    assert html.index('aria-label="Ingredients"') < html.index('data-wk-add="dinner"') < html.index('aria-label="Steps"')


# ---------------------------------------------- the wiring


def test_start_cooking_on_the_meal_step_goes_straight_into_the_cooker():
    wire = SHELL_JS[SHELL_JS.index("function wireMealsStep("):]
    wire = wire[:wire.index("[data-wk-swap]")]
    assert "openRecipeFor({" in wire
    assert "{ start: !!(entry && entry.source !== 'leftovers') }" in wire
    assert "activateTab('kitchen', true, { cookFocus: target, cookStart: !!(opts && opts.start) })" in _extract("openRecipeFor")
    assert "kitchenEnterCook(opts.cookFocus, !!opts.cookStart)" in SHELL_JS
    enter = _extract("cookEnterFocus")
    assert "if (start) return cookStartCooking();" in enter
    assert "cookState.focusStage = 'recipe';" in enter
    # ...and a link that arrives before Cook has loaded keeps the "and start".
    assert "cookState.pendingFocusStart = !!start;" in _extract("kitchenEnterCook")
    assert "cookEnterFocus(idx, start);" in _extract("loadKitchen")


def test_the_meal_steps_stepper_is_cook_modes_own():
    wire = _extract("wireMealsStep")
    assert "[data-cook=\"serves\"]" in wire
    assert "cookStepServings(btn, cookMeal, function () { renderMealsStep(panel); });" in wire
    step = _extract("cookStepServings")
    assert "wrap.querySelector('.cook-serves-count')" in step
    assert "redraw();" in step
    assert "cookApplyServesOverride([cookMeal]);" in _extract("mealStepHtml")
    # The same per-plan record on both tabs, before Cook has ever loaded.
    assert "planCookView()" in _extract("cookTickPlanId")


def test_the_clock_and_the_batch_question_are_gone_from_the_shell():
    for gone in ("function mealClockStops(", "function mealClockHtml(", "function mealStopHtml(",
                 "function mealHeroHtml(", "function mealClockFor(", "function spokenTime(",
                 "function stopTitleSplit(", "function cookAheadHtml(", "function cookAheadPicks(",
                 "function cookSetCookAhead(", "function ensureRhythmForMeals(", "function mealCookName(",
                 "function cookDefrostLinkHtml(", "function cookAheadAskLinkHtml(", "function cookBatchNote(",
                 "function cookAttendanceChip(", "function cookKitFor(", "function cookFocusHeroHtml(",
                 "function cookMethodStageHtml(", "function cookDetailHtml(", "function mealAddStopTime(",
                 "'Start at '", "data-cook=\"ahead-day\"",
                 "data-cook=\"defrost-ask\"", "data-cook=\"cook-ahead-ask\"", ">Something in the freezer?<",
                 "The whole method", "Before you start</", "'Mark it cooked'"):
        assert gone not in SHELL_JS, gone
    # forceShow stays for the freezer question's new home on Plan.
    assert "forceShow" in SHELL_JS


# ---------------------------------------------- the CSS


def _rule(selector: str) -> str:
    start = SHELL_CSS.index(selector + " {")
    return SHELL_CSS[start : SHELL_CSS.index("}", start)]


def test_the_recipe_is_drawn_to_the_board_and_every_colour_is_a_token():
    title = _rule(".recipe-title")
    assert "font-size: 31px" in title and "var(--font-display)" in title and "-0.038em" in title
    btn = _rule(".recipe-serves .cook-serves-btn")
    assert "width: 36px" in btn and "height: 36px" in btn and "var(--radius-control)" in btn
    assert "inset: -5px" in _rule(".cook-serves-btn::after"), "36px of box on a 44px target (Rule 6)"
    assert "padding: 10px 0 4px" in _rule(".recipe-eyebrow")
    box = _rule(".recipe-card .cook-box")
    assert "width: 22px" in box and "height: 22px" in box
    assert "min-height: 44px" in _rule(".recipe-card .cook-getout-row")
    num = _rule(".recipe-step-num")
    assert "width: 30px" in num and "height: 30px" in num and "var(--sand)" in num and "var(--font-display)" in num
    block = SHELL_CSS[SHELL_CSS.index("/* ---------- The recipe, as the recipe ----------"):SHELL_CSS.index("/* ---------- The dock: where a screen's one action lives")]
    assert not re.search(r"#[0-9a-fA-F]{3,8}\b", block), "Rule 9 — every colour goes through a token"
    assert not re.search(r"^\s*(transition|animation)\b", block, re.M), "§4 — nothing new animates"
    # The one italic on the cooker is the accent line, Newsreader at 17px.
    nxt = _rule(".cook-next")
    assert "var(--font-accent)" in nxt and "font-style: italic" in nxt and "font-size: 17px" in nxt
    assert block.count("italic") == 1
    # The old rules went with the old screens.
    for gone in (".wk-stop", ".wk-clock", ".wk-meal-hero", ".wk-meal-by", ".cook-ahead-day", ".cook-hero-slim",
                 ".cook-detail", ".cook-focus-end", ".cook-kit-chip", ".cook-step-stage", ".cook-meta-chip.is-live",
                 ".cook-dock-link", ".wk-ing-qty", ".wk-ing-list"):
        assert gone not in SHELL_CSS, gone
    # The last card clears the chat icon when there is no dock to do it.
    assert ".wk-meal-body:not(:has(+ .dock)) { padding-bottom: 84px; }" in SHELL_CSS
