"""The recipe page's crumb names the day Plan's root goes back to.

Defect hunt, 2026-11-03: on Plan's root with Thursday open, Thursday's
leftover chili -> "See Wednesday's recipe" opened Cook's recipe page with
"‹ Wednesday", and tapping it landed on Thursday (the root keeps its day,
weekState.planDay). The crumb named the cook's day, not the day it goes to.
"""
from test_one_recipe_screen_2026_11_02 import _extract, _needs_node, _run


def _prelude() -> str:
    return (
        "var CALLS = [];\n"
        "function openRecipeFor(t, o) { CALLS.push(o); }\n"
        "function goMealsStep(s, o) { CALLS.push({ meal: s }); }\n"
        "var NAMES = { '2026-11-04': 'Wednesday', '2026-11-05': 'Thursday' };\n"
        "function dayName(d) { return NAMES[d]; }\n"
        "function isSnackSlot(s) { return /^snack/.test(s); }\n"
        "var VIEW = { is_current_plan: true, meals: [{ entry_id: 7 }] };\n"
        "function planCookView() { return VIEW; }\n"
        "function planCookViewFailed() { return false; }\n"
        "async function ensureCookDataForMeals(p) {}\n"
        "var panels = { week: {} };\n"
        "var planOpenMealSeq = 0; var cookState = { data: null };\n"
        "var weekState = { step: 'week', mealBack: 'week', selectedIndex: 1, planDay: 1, days: [\n"
        "  { date: '2026-11-04', dinner: { state: 'planned', entry_id: 7, title: 'Weeknight Chili', source: 'recipe' } },\n"
        "  { date: '2026-11-05', dinner: { state: 'planned', entry_id: 8, title: 'Weeknight Chili', source: 'leftovers' } }] };\n"
        + _extract("daySlotEntry") + "\n"
        + _extract("recipeTargetForEntry") + "\n"
        + _extract("planOpenMeal") + "\n"
    )


@_needs_node
def test_see_wednesdays_recipe_from_thursday_on_the_root_says_thursday():
    # openSourceMeal from the reheat night's sheet: Wednesday's cook, back 'week'.
    out = _run(_prelude()
               + "(async function () { await planOpenMeal({ dayIndex: 0, slot: 'dinner', back: 'week' });\n"
               + "console.log(JSON.stringify(CALLS)); })();")
    assert len(out) == 1
    assert out[0]["label"] == "Thursday"
    assert out[0]["mealsStep"] == "week"


@_needs_node
def test_a_day_step_crumb_still_names_the_meals_own_day():
    # From a Day step (back 'day') the way back IS that meal's day.
    out = _run(_prelude()
               + "weekState.step = 'day';\n"
               + "(async function () { await planOpenMeal({ dayIndex: 0, slot: 'dinner', back: 'day' });\n"
               + "console.log(JSON.stringify(CALLS[0].label)); })();")
    assert out == "Wednesday"


@_needs_node
def test_the_all_view_still_says_this_week():
    out = _run(_prelude()
               + "weekState.planDay = null;\n"
               + "(async function () { await planOpenMeal({ dayIndex: 0, slot: 'dinner', back: 'week' });\n"
               + "console.log(JSON.stringify(CALLS[0].label)); })();")
    assert out == "This week"
