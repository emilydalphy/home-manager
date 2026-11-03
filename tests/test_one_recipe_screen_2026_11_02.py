"""One recipe screen — Emily, 2026-11-02 (option B, with two changes).

Plan's "See the recipe" opened Plan's own Meal step, and Cook opened its
tabbed recipe page: two layouts for one dish. Now a meal on the week Cook
holds opens Cook's recipe page from Plan too (planOpenMeal), with the crumb
naming where it came from. The page opens on the tab the visit is for —
Steps once a cook is under way, Ingredients from Shop, Overview otherwise
(cookOpeningTab) — and its Overview leads with "Already done" (the meal's
ticked prep, recipeAlreadyDoneHtml), shows what's on the plate
(recipePlateHtml), and keeps only what is still to do in "For this meal".
A cook under way shows its ticked steps and "You're here" on Steps.

Next week's draft, a reheat night, or a view still loading keep the Meal
step: cook mode can only open a meal in Cook's own view, and an unresolved
target lands on Cook's root (the 2026-09-13 "other page" bug).
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

import nodeharness

REPO = Path(__file__).resolve().parents[1]
SHELL_JS = (REPO / "static" / "shell.js").read_text(encoding="utf-8")
SHELL_CSS = (REPO / "static" / "shell.css").read_text(encoding="utf-8")

_needs_node = pytest.mark.skipif(shutil.which("node") is None, reason="node is needed to run the renderers")


def _extract(name: str) -> str:
    start = SHELL_JS.index(f"function {name}(")
    if SHELL_JS[start - 6 : start] == "async ":
        start -= 6
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


def _run(script: str):
    res = nodeharness.run_node(script, timeout=30)
    assert res.returncode == 0, f"node failed: {res.stderr}"
    return json.loads(res.stdout.strip().splitlines()[-1])


PRELUDE = (
    "var TICKS = {};\n"
    "function cookTicked(kind, key) { return !!TICKS[kind + '|' + key]; }\n"
    "function cookMealKey(m) { return String(m.entry_id); }\n"
    "var COOK_ICONS = { check: '<svg>check</svg>', mic: '' };\n"
    "var COOK_VOICE_ENABLED = false;\n"
    "function cookDateLabel(d) { return d; }\n"
    + _extract("escapeHtml") + "\n"
    + "\n".join(_extract(n) for n in (
        "cookUnderway", "cookOpeningTab", "recipeAlreadyDoneHtml", "cookFocusPrepHtml",
        "recipeStepsHtml", "recipePlateHtml", "daySlotEntry", "daySlotKeys"))
    + "\nvar WEEK_SLOTS = ['breakfast', 'lunch', 'dinner'];\nvar PLATE_NO_NAME = 'In the dish';\n"
)

MEAL = ("{ entry_id: 7, meal: 'Lemon chicken', has_full_recipe: true, cooked_status: 'not_cooked', "
        "ingredients: [{ item: 'chicken' }], instructions: ['Toss.', 'Brown.', 'Simmer.'] }")


# ---------------------------------------------------------------------------
# The tab the page opens on
# ---------------------------------------------------------------------------

@_needs_node
def test_opens_on_overview_from_plan_and_cook():
    out = _run(PRELUDE + f"var m = {MEAL};\n"
               "console.log(JSON.stringify([cookOpeningTab(m, null), cookOpeningTab(m, { tab: 'week' }), "
               "cookOpeningTab(m, { tab: 'today' })]));")
    assert out == ["overview", "overview", "overview"]


@_needs_node
def test_opens_on_ingredients_from_shop():
    out = _run(PRELUDE + f"var m = {MEAL};\nconsole.log(JSON.stringify(cookOpeningTab(m, {{ tab: 'grocery' }})));")
    assert out == "ingredients"


@_needs_node
def test_opens_on_steps_once_a_cook_is_under_way_wherever_it_was_tapped():
    out = _run(PRELUDE + f"var m = {MEAL};\nTICKS['steps|7:0'] = true;\n"
               "var a = cookOpeningTab(m, { tab: 'grocery' });\n"
               "TICKS = {}; m.cook_started_at = '2026-11-02T18:00:00';\n"
               "var b = cookOpeningTab(m, null);\n"
               "m.cooked_status = 'done';\n"
               "var c = cookOpeningTab(m, null);\n"
               "console.log(JSON.stringify([a, b, c]));")
    # Ticks or a recorded start mean under way; a cooked meal is not.
    assert out == ["steps", "steps", "overview"]


@_needs_node
def test_a_dish_with_no_saved_recipe_always_opens_on_overview():
    out = _run(PRELUDE + "var m = { entry_id: 1, has_full_recipe: false, ingredients: [], instructions: [] };\n"
               "console.log(JSON.stringify(cookOpeningTab(m, { tab: 'grocery' })));")
    assert out == "overview"


def test_cook_enter_focus_uses_the_opening_tab_and_the_dock_the_same_test():
    enter = _extract("cookEnterFocus")
    assert "cookState.recipeTab = cookOpeningTab(cookState.data.meals[idx], cookState.focusOrigin);" in enter
    assert "var underway = cookUnderway(meal);" in _extract("cookRecipeDockHtml")


# ---------------------------------------------------------------------------
# Already done leads the Overview; For this meal keeps only what's left
# ---------------------------------------------------------------------------

TASKS = ("[{ id: 1, status: 'done', description: 'Marinate the chicken', task_date: '2026-11-01' },"
         " { id: 2, status: 'pending', description: 'Thaw the stock', task_date: '2026-11-02' },"
         " { id: 3, status: 'skipped', description: 'Zest the lemons', task_date: '2026-11-02' }]")


@_needs_node
def test_already_done_lists_done_and_skipped_prep_with_a_tick_that_comes_off():
    out = _run(PRELUDE + f"console.log(JSON.stringify(recipeAlreadyDoneHtml({TASKS})));")
    assert "Already done" in out
    assert "Marinate the chicken" in out and "Zest the lemons" in out
    assert "Thaw the stock" not in out
    assert out.count('data-cook="check-prep"') == 2 and out.count('data-next="pending"') == 2


@_needs_node
def test_nothing_done_draws_no_already_done_card():
    out = _run(PRELUDE + "console.log(JSON.stringify(recipeAlreadyDoneHtml([{ id: 2, status: 'pending', description: 'x' }])));")
    assert out == ""


@_needs_node
def test_for_this_meal_holds_only_what_is_left_and_goes_when_nothing_is():
    out = _run(PRELUDE + f"var a = cookFocusPrepHtml({TASKS});\n"
               "var b = cookFocusPrepHtml([{ id: 1, status: 'done', description: 'Marinate', task_date: 'x' }]);\n"
               "console.log(JSON.stringify([a, b]));")
    left, none = out
    assert "Thaw the stock" in left
    assert "Marinate the chicken" not in left and "Zest the lemons" not in left
    # The count still counts the whole meal's prep.
    assert "2 of 3 done" in left
    assert none == ""


def test_the_overview_leads_with_already_done_then_the_plate():
    body = _extract("recipeOverviewHtml")
    assert body.index("recipeAlreadyDoneHtml(prep)") < body.index("recipeCreditHtml(extra)")
    assert "recipePlateHtml(meal)" in body
    assert "cookFocusPrepHtml(prep)" in body


# ---------------------------------------------------------------------------
# The plate, read-only
# ---------------------------------------------------------------------------

@_needs_node
def test_the_plate_shows_the_plan_entrys_named_parts():
    out = _run(PRELUDE + "var weekState = { days: [{ date: '2026-11-05', dinner: { entry_id: 7, plate_parts: ["
               "{ role: 'carb', word: 'Carb', name: 'Orzo' }, { role: 'veg', word: 'Veg', name: 'Spinach' },"
               "{ role: 'protein', word: 'Protein', missing: true }] } }] };\n"
               "var a = recipePlateHtml({ entry_id: 7 });\n"
               "var b = recipePlateHtml({ entry_id: 99 });\n"
               "console.log(JSON.stringify([a, b]));")
    shown, absent = out
    assert "Orzo" in shown and "Spinach" in shown
    assert "carb" in shown and "veg" in shown
    # A missing part is an offer to add one, which is Plan's job.
    assert "Protein" not in shown and "protein" not in shown
    # Read-only: nothing to tap.
    assert "<button" not in shown
    assert absent == ""


# ---------------------------------------------------------------------------
# Steps: where a cook under way got to
# ---------------------------------------------------------------------------

@_needs_node
def test_steps_mark_ticked_steps_and_youre_here_on_the_next():
    out = _run(PRELUDE + f"var m = {MEAL};\nTICKS['steps|7:0'] = true;\n"
               "var live = recipeStepsHtml(m, true);\n"
               "var plan = recipeStepsHtml(m, false);\n"
               "TICKS = {};\nvar fresh = recipeStepsHtml(m, true);\n"
               "console.log(JSON.stringify([live, plan, fresh]));")
    live, plan, fresh = out
    assert live.count("is-done") == 1 and live.count("is-here") == 1
    assert live.index("is-done") < live.index("is-here")
    assert "You’re here" in live
    # The plan's copy just reads, and a cook not started marks nothing.
    for html in (plan, fresh):
        assert "is-done" not in html and "is-here" not in html and "You’re here" not in html


def test_the_new_styles_use_tokens():
    for sel in (".recipe-done {", ".recipe-plate-part {", ".recipe-step-here {"):
        assert sel in SHELL_CSS, sel
    block = SHELL_CSS[SHELL_CSS.index("One recipe screen (Emily, 2026-11-02"):]
    for line in block.splitlines():
        if ":" in line and "{" not in line and "/*" not in line:
            assert "#" not in line, line


# ---------------------------------------------------------------------------
# Plan opens Cook's page only when Cook can open that meal
# ---------------------------------------------------------------------------

def _plan_prelude() -> str:
    return (
        "var CALLS = [];\n"
        "function openRecipeFor(t, o) { CALLS.push(['cook', t, o]); }\n"
        "function goMealsStep(s, o) { CALLS.push(['meal', s, o]); }\n"
        "function dayName(d) { return 'Thursday'; }\n"
        "function isSnackSlot(s) { return /^snack/.test(s); }\n"
        "var VIEW = null; function planCookView() { return VIEW; }\n"
        "var FAILED = false; function planCookViewFailed() { return FAILED; }\n"
        "var LATER = null, READS = 0;\n"
        "async function ensureCookDataForMeals(p) { READS++; await Promise.resolve(); if (LATER) VIEW = LATER; }\n"
        "var panels = { week: {} };\n"
        "var planOpenMealSeq = 0; var cookState = { data: null };\n"
        "function cookMealForEntry(id) { return (VIEW && VIEW.meals || []).filter(function (m) { return m.entry_id === id; })[0] || null; }\n"
        "var weekState = { step: 'week', mealBack: 'week', selectedIndex: 0, days: [{ date: '2026-11-05',"
        " dinner: { state: 'planned', entry_id: 7, title: 'Lemon chicken', source: 'recipe' },"
        " lunch: { state: 'planned', entry_id: 8, title: 'Leftovers', source: 'leftovers' } }] };\n"
        + _extract("daySlotEntry") + "\n"
        + _extract("recipeTargetForEntry") + "\n"
        + _extract("planOpenMeal") + "\n"
    )


@_needs_node
def test_a_meal_on_cooks_week_opens_cooks_recipe_page_and_comes_back():
    out = _run(_plan_prelude()
               + "VIEW = { is_current_plan: true, meals: [{ entry_id: 7 }] };\n"
               + "(async function () { await planOpenMeal({ slot: 'dinner', back: 'day', dayIndex: 0 });\n"
               + "console.log(JSON.stringify(CALLS)); })();")
    assert len(out) == 1 and out[0][0] == "cook"
    target, origin = out[0][1], out[0][2]
    assert target["entryId"] == 7 and target["slot"] == "dinner"
    assert origin["tab"] == "week" and origin["label"] == "Thursday"
    assert origin["mealsDay"] == 0 and origin["mealsSlot"] == "dinner"
    # Back goes to the Plan screen the tap was on, not to a Meal step.
    assert origin["mealsStep"] == "week"


@_needs_node
def test_the_crumb_names_the_day_open_on_plans_root_and_this_week_on_all():
    out = _run(_plan_prelude()
               + "VIEW = { is_current_plan: true, meals: [{ entry_id: 7 }] };\n"
               + "(async function () {\n"
               + "weekState.planDay = 0; await planOpenMeal({ slot: 'dinner', back: 'week', dayIndex: 0 });\n"
               + "weekState.planDay = null; await planOpenMeal({ slot: 'dinner', back: 'week', dayIndex: 0 });\n"
               + "weekState.step = 'review'; await planOpenMeal({ slot: 'dinner', back: 'review', dayIndex: 0 });\n"
               + "console.log(JSON.stringify(CALLS.map(function (c) { return c[2].label; }))); })();")
    assert out == ["Thursday", "This week", "Check the week"]


@_needs_node
def test_a_tap_on_plans_root_reads_the_plans_view_first_then_opens_cook():
    # The root never reads the plan's cooker view itself (only a day or a
    # meal does), so the first tap there reads it, then decides.
    out = _run(_plan_prelude()
               + "LATER = { is_current_plan: true, meals: [{ entry_id: 7 }] };\n"
               + "(async function () { await planOpenMeal({ slot: 'dinner', back: 'week', dayIndex: 0 });\n"
               + "console.log(JSON.stringify([READS, CALLS])); })();")
    reads, calls = out
    assert reads == 1 and calls[0][0] == "cook"


@_needs_node
def test_a_failed_read_keeps_the_meal_step_without_asking_again():
    out = _run(_plan_prelude()
               + "FAILED = true; LATER = { is_current_plan: true, meals: [{ entry_id: 7 }] };\n"
               + "(async function () { await planOpenMeal({ slot: 'dinner', back: 'week', dayIndex: 0 });\n"
               + "console.log(JSON.stringify([READS, CALLS])); })();")
    reads, calls = out
    assert reads == 0 and calls[0][0] == "meal"


@_needs_node
@pytest.mark.parametrize("view", [
    "null",                                                   # read, still nothing
    "{ is_current_plan: false, meals: [{ entry_id: 7 }] }",   # next week's draft
    "{ is_current_plan: true, meals: [{ entry_id: 99 }] }",   # not in the view
])
def test_anything_cook_cant_open_keeps_the_meal_step(view):
    out = _run(_plan_prelude()
               + f"VIEW = {view};\n"
               + "(async function () { await planOpenMeal({ slot: 'dinner', back: 'week', dayIndex: 0 });\n"
               + "console.log(JSON.stringify(CALLS)); })();")
    assert len(out) == 1 and out[0][0] == "meal" and out[0][1] == "meal"


@_needs_node
def test_a_cook_view_that_is_behind_keeps_the_meal_step():
    # Plan's view has the new dish (a swap just made); Cook's own view,
    # loaded earlier, still has the old one in that slot. Cook mode would
    # open the old dish (date + slot) or its root — so the Meal step.
    out = _run(_plan_prelude()
               + "VIEW = { is_current_plan: true, meals: [{ entry_id: 7 }] };\n"
               + "cookState.data = { meals: [{ entry_id: 3, date: '2026-11-05', slot: 'dinner', meal: 'Old dish' }] };\n"
               + "(async function () { await planOpenMeal({ slot: 'dinner', back: 'week', dayIndex: 0 });\n"
               + "console.log(JSON.stringify(CALLS)); })();")
    assert out[0][0] == "meal"


@_needs_node
def test_a_second_tap_while_the_view_is_read_wins():
    out = _run(_plan_prelude()
               + "LATER = { is_current_plan: true, meals: [{ entry_id: 7 }] };\n"
               + "(async function () {\n"
               + "var first = planOpenMeal({ slot: 'dinner', back: 'week', dayIndex: 0 });\n"
               + "var second = planOpenMeal({ slot: 'dinner', back: 'week', dayIndex: 0 });\n"
               + "await first; await second;\n"
               + "console.log(JSON.stringify(CALLS.length)); })();")
    assert out == 1


@_needs_node
def test_moving_to_another_plan_screen_during_the_read_does_nothing():
    out = _run(_plan_prelude()
               + "LATER = { is_current_plan: true, meals: [{ entry_id: 7 }] };\n"
               + "ensureCookDataForMeals = async function () { weekState.step = 'day'; VIEW = LATER; };\n"
               + "(async function () { await planOpenMeal({ slot: 'dinner', back: 'week', dayIndex: 0 });\n"
               + "console.log(JSON.stringify(CALLS.length)); })();")
    assert out == 0


@_needs_node
def test_a_reheat_night_keeps_the_meal_step():
    out = _run(_plan_prelude()
               + "VIEW = { is_current_plan: true, meals: [{ entry_id: 8 }] };\n"
               + "(async function () { await planOpenMeal({ slot: 'lunch', back: 'week', dayIndex: 0 });\n"
               + "console.log(JSON.stringify(CALLS)); })();")
    assert out[0][0] == "meal"


def test_every_plan_way_into_a_meal_goes_through_plan_open_meal():
    # The meal sheet's See the recipe, a day card's meal, and the cook a
    # reheat night came from.
    assert SHELL_JS.count("planOpenMeal({") == 3
    assert "goMealsStep('meal', { slot: slot, back: 'week'" not in SHELL_JS
    assert "goMealsStep('meal', { slot: btn.getAttribute('data-wk-meal')" not in SHELL_JS
    assert "goMealsStep('meal', { dayIndex: index, slot: slot, back: back });" not in SHELL_JS
