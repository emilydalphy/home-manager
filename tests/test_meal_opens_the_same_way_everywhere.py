"""Tapping a meal in the draft always opens that meal.

Emily, 2026-09-13, on her phone: "Sometimes when I push the meal option
for the week, it brings me to the recipe, and sometimes it brings me to
this other page with the full cook list." And, after a swap in chat:
"When I click the new recipe it gave me, it shows it in the new draft, but
I can't go to the screen where I can see the instructions for it."

Two things were true at once, and both are pinned here:

  * A dish name on the root's list ("What we're eating") went through
    openRecipeFor into COOK MODE, while a tile → Day → card went to Plan's
    own Meal step — two destinations for one tap. Cook mode can only open
    a meal that is in Cook's own view, and that view is the plan whose
    period contains today. On a Sunday, with Plan pinned to the week just
    drafted, nothing on the draft is in it, so every name landed on Cook's
    root: "Cook · 4 cooks today", this week's list.

  * The Meal step read its steps off that same view (cookState.data),
    fetched once and never again — so a meal on next week's draft, or a
    dish just swapped in (a new entry id), drew a hero with nothing under
    it.

Now every tap opens the Meal step, and the Meal step reads the cooker view
of THE PLAN ON SCREEN (`/api/cooker-view?weekly_plan_id=`), re-read after
anything that changes the week. The server says on that view whether it
is also the plan Cook holds (`is_current_plan`), which is what decides
whether "Cook this" / "Start at …" is offered at all.
"""
from __future__ import annotations

import datetime
import json
import shutil
from pathlib import Path

import nodeharness
import pytest

from app import tools

REPO = Path(__file__).resolve().parents[1]
SHELL_JS = (REPO / "static" / "shell.js").read_text(encoding="utf-8")

_needs_node = pytest.mark.skipif(
    shutil.which("node") is None,
    reason="node is needed to execute the screen's own functions",
)


def _monday(offset_weeks: int = 0) -> str:
    today = datetime.date.today()
    monday = today - datetime.timedelta(days=today.weekday())
    return (monday + datetime.timedelta(days=7 * offset_weeks)).isoformat()


def _extract(name: str, source: str = SHELL_JS) -> str:
    start = source.index(f"function {name}(")
    i = source.index("{", start)
    depth, j = 0, i
    while True:
        if source[j] == "{":
            depth += 1
        elif source[j] == "}":
            depth -= 1
            if depth == 0:
                break
        j += 1
    return source[start : j + 1]


def _run_node(harness: str):
    res = nodeharness.run_node(harness, timeout=30)
    assert res.returncode == 0, f"node failed: {res.stderr}"
    return json.loads(res.stdout.strip())


_ESCAPE = (
    "function escapeHtml(s){return String(s == null ? '' : s)"
    ".replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/\"/g,'&quot;');}\n"
)


# ------------------------------------------------ the server: two views


@pytest.fixture
def two_weeks():
    """This week approved, next week a draft — the Sunday case. Returns
    (this_week_id, next_week_id, the draft's dinner entry id)."""
    tools.add_recipe("Chicken curry", ingredients=[
        {"item": "Chicken thighs", "qty": "2 lb", "category": "meat/seafood"},
    ], instructions=["Brown the chicken.", "Simmer in the sauce."],
        prep_time_minutes=10, cook_time_minutes=30)
    tools.add_recipe("Lamb chops", ingredients=[
        {"item": "Lamb chops", "qty": "4", "category": "meat/seafood"},
    ], instructions=["Season the chops.", "Grill four minutes a side."],
        prep_time_minutes=5, cook_time_minutes=10)
    this_week = tools.create_weekly_plan(_monday(0))["weekly_plan_id"]
    tools.plan_meal(tools._week_dates(_monday(0))[0], "Chicken curry", slot="dinner", weekly_plan_id=this_week)
    tools.approve_weekly_plan(this_week, approved_by="Emily")
    next_week = tools.create_weekly_plan(_monday(1))["weekly_plan_id"]
    entry = tools.plan_meal(tools._week_dates(_monday(1))[1], "Lamb chops", slot="dinner", weekly_plan_id=next_week)
    return this_week, next_week, entry["entry_id"]


def test_the_root_cause_cooks_own_view_never_holds_next_weeks_draft(two_weeks):
    """Characterises the bug, not the fix: the no-id view is this week's,
    and the draft's dinner is nowhere on it — so cook mode, which only ever
    opens that view, had nothing to focus and fell back to its root."""
    this_week, next_week, lamb = two_weeks
    view = tools.get_cooker_view()
    assert view["weekly_plan_id"] == this_week
    assert view["is_current_plan"] is True
    assert lamb not in [m["entry_id"] for m in view["meals"]]


def test_the_plans_own_view_holds_the_drafts_meal_with_its_recipe(two_weeks):
    this_week, next_week, lamb = two_weeks
    view = tools.get_cooker_view(next_week)
    assert view["weekly_plan_id"] == next_week
    card = next(m for m in view["meals"] if m["entry_id"] == lamb)
    assert card["has_full_recipe"] is True
    assert card["instructions"] == ["Season the chops.", "Grill four minutes a side."]
    # ...and it says it is NOT the plan Cook holds, which is what takes
    # "Cook this" off a meal cook mode couldn't open anyway.
    assert view["is_current_plan"] is False
    # Asking for this week by id agrees with asking for nothing.
    assert tools.get_cooker_view(this_week)["is_current_plan"] is True


def test_is_current_plan_is_the_same_query_not_a_date_rule(two_weeks):
    """On a day no plan covers, the current plan is the newest unexpired
    one — next week's draft — so a "has its period started" test would say
    no when cook mode would in fact open it. The flag comes from the same
    resolver cook mode uses."""
    this_week, next_week, lamb = two_weeks
    from app.db import get_conn
    conn = get_conn()
    conn.execute("UPDATE weekly_plans SET status = 'retired' WHERE id = ?", (this_week,))
    conn.commit()
    conn.close()
    assert tools.get_cooker_view()["weekly_plan_id"] == next_week
    assert tools.get_cooker_view(next_week)["is_current_plan"] is True


def test_a_dish_just_swapped_in_is_on_the_plans_view_by_its_new_id(two_weeks):
    """The new recipe is reachable from the draft immediately: the swap
    saves the recipe and the plan's view carries it under the new entry
    id — which is why the screen re-reads that view after a swap rather
    than keeping the one it fetched before the entry existed."""
    this_week, next_week, lamb = two_weeks

    def picker(context):
        return {
            "meal_name": "Lemon Chicken Traybake", "reason": "Lighter than the chops.",
            "ingredients": [{"item": "Chicken thighs", "qty": "2 lb", "category": "meat/seafood"}],
            "instructions": ["Heat the oven.", "Roast everything together."],
            "food_groups": ["protein"], "prep_time_minutes": 10, "cook_time_minutes": 35,
            "default_servings": 2,
        }

    out = tools.swap_meal_in_place(next_week, lamb, picker=picker)
    assert out["status"] == "swapped"
    new_id = out["entry_id"]
    view = tools.get_cooker_view(next_week)
    card = next(m for m in view["meals"] if m["entry_id"] == new_id)
    assert card["meal"] == "Lemon Chicken Traybake"
    assert card["instructions"] == ["Heat the oven.", "Roast everything together."]


def test_the_endpoint_serves_a_named_plan(signed_in, two_weeks):
    this_week, next_week, lamb = two_weeks
    res = signed_in.get(f"/api/cooker-view?weekly_plan_id={next_week}")
    assert res.status_code == 200
    body = res.json()
    assert body["weekly_plan_id"] == next_week and body["is_current_plan"] is False
    assert lamb in [m["entry_id"] for m in body["meals"]]


# ------------------------------------------------- the screen: one door


def test_every_way_of_tapping_a_meal_lands_on_the_meal_step():
    """The name on the root's list, "Change" beside it, and the Day step's
    card all go through goMealsStep('meal'); none goes into cook mode."""
    wiring = SHELL_JS[SHELL_JS.index("[data-rv-recipe]"):][:1400]
    assert "goMealsStep('meal', { dayIndex: idx, slot: slot, back: 'week' });" in wiring
    assert "openRecipeFor" not in wiring
    change = SHELL_JS[SHELL_JS.index("[data-rv-change]"):][:1200]
    assert "goMealsStep('meal', {" in change and "back: 'week'" in change
    card = SHELL_JS[SHELL_JS.index("[data-wk-meal]"):][:700]
    assert "goMealsStep('meal', { slot: btn.getAttribute('data-wk-meal'), back: weekState.step === 'week' ? 'week' : 'day' });" in card
    # The dead second handler (no markup ever emitted it) is gone.
    assert "data-rv-recipe-date" not in SHELL_JS


def test_the_meal_step_asks_for_the_plan_on_screens_own_view():
    fn = _extract("ensureCookDataForMeals")
    assert "'/api/cooker-view?weekly_plan_id=' + encodeURIComponent(planId)" in fn
    # Never written over Cook's own reading of its own week.
    assert "cookState.data =" not in fn
    assert "weekState.cookView = {" in fn
    # ...and re-read after anything that reloads the week.
    load = _extract("loadWeekMenu")
    assert "if (weekState.cookView) weekState.cookView.stale = true;" in load
    assert load.index("cookView.stale = true") < load.index("renderWeekMenu(panel, data)")
    # The Day step asks too: its cards decide "Cook this" off the same view.
    render = _extract("renderMealsStep")
    day_branch = render.split("weekState.step === 'day') {", 1)[1].split("} else if", 1)[0]
    assert "ensureCookDataForMeals(panel);" in day_branch


@_needs_node
def test_the_lookup_reads_the_plans_view_first_and_only_for_that_plan():
    harness = (
        "var cookState = { data: { weekly_plan_id: 1, meals: [{ entry_id: 7, meal: 'Chicken curry' }] } };\n"
        "var weekState = { data: { weekly_plan_id: 2 }, cookView: null };\n"
        + _extract("planCookView") + "\n"
        + _extract("planCookableNow") + "\n"
        + _extract("cookMealForEntry") + "\n"
        + "var out = {};\n"
        # Nothing landed for plan 2 yet: Cook's view still answers for its
        # own entries, the draft's is unknown, and "cookable" is unknown-as-yes.
        + "out.beforeCook = (cookMealForEntry(7) || {}).meal || null;\n"
        + "out.beforeDraft = cookMealForEntry(9);\n"
        + "out.beforeCookable = planCookableNow();\n"
        # A view for a DIFFERENT plan than the one on screen is not read.
        + "weekState.cookView = { planId: 3, data: { weekly_plan_id: 3, is_current_plan: false, meals: [{ entry_id: 9, meal: 'Wrong plan' }] } };\n"
        + "out.otherPlan = cookMealForEntry(9);\n"
        + "out.otherCookable = planCookableNow();\n"
        # The plan on screen's view lands: its meal is found, and Cook can't hold it.
        + "weekState.cookView = { planId: 2, data: { weekly_plan_id: 2, is_current_plan: false, meals: [{ entry_id: 9, meal: 'Lamb chops' }] } };\n"
        + "out.draft = cookMealForEntry(9).meal;\n"
        + "out.cookable = planCookableNow();\n"
        + "out.stillCooks = cookMealForEntry(7).meal;\n"
        # The same plan, once Cook holds it (Monday morning).
        + "weekState.cookView.data.is_current_plan = true;\n"
        + "out.mondayCookable = planCookableNow();\n"
        + "console.log(JSON.stringify(out));\n"
    )
    got = _run_node(harness)
    assert got == {
        "beforeCook": "Chicken curry", "beforeDraft": None, "beforeCookable": True,
        "otherPlan": None, "otherCookable": True,
        "draft": "Lamb chops", "cookable": False, "stillCooks": "Chicken curry",
        "mondayCookable": True,
    }


@_needs_node
def test_the_fetch_lands_on_the_plans_view_and_refetches_when_stale():
    harness = (
        "var calls = [];\n"
        "var answer = { weekly_plan_id: 2, is_current_plan: false, meals: [{ entry_id: 9 }] };\n"
        "global.fetch = function (url) { calls.push(url); return Promise.resolve({ ok: true, json: function () { return Promise.resolve(answer); } }); };\n"
        "var renders = 0; function renderMealsStep() { renders += 1; }\n"
        "var cookState = { data: null };\n"
        "var weekState = { step: 'meal', data: { weekly_plan_id: 2 }, cookView: null };\n"
        + _extract("planCookView") + "\n"
        # _extract lifts from "function", so the async keyword goes back on.
        + "async " + _extract("ensureCookDataForMeals") + "\n"
        + "(async function () {\n"
        + "  await ensureCookDataForMeals({});\n"
        + "  var out = { calls: calls.slice(), planId: weekState.cookView.planId, found: planCookView().meals[0].entry_id, cookData: cookState.data, renders: renders };\n"
        # Cached: a second ask costs nothing.
        + "  await ensureCookDataForMeals({});\n"
        + "  out.cachedCalls = calls.length;\n"
        # Marked stale (loadWeekMenu after a swap): read again, old answer kept meanwhile.
        + "  weekState.cookView.stale = true;\n"
        + "  var p = ensureCookDataForMeals({});\n"
        + "  out.keptWhileLoading = !!planCookView();\n"
        + "  await p;\n"
        + "  out.staleCalls = calls.length;\n"
        # The screen moved to another plan: the view is for that plan now.
        + "  weekState.data = { weekly_plan_id: 5 };\n"
        + "  out.gone = planCookView();\n"
        + "  answer = { weekly_plan_id: 5, is_current_plan: true, meals: [] };\n"
        + "  await ensureCookDataForMeals({});\n"
        + "  out.newPlan = [calls[calls.length - 1], weekState.cookView.planId];\n"
        + "  console.log(JSON.stringify(out));\n"
        + "})();\n"
    )
    got = _run_node(harness)
    assert got["calls"] == ["/api/cooker-view?weekly_plan_id=2"]
    assert got["planId"] == 2 and got["found"] == 9
    assert got["cookData"] is None, "Cook's own view is not written by Plan"
    assert got["renders"] == 1
    assert got["cachedCalls"] == 1
    assert got["keptWhileLoading"] is True
    assert got["staleCalls"] == 2
    assert got["gone"] is None
    assert got["newPlan"] == ["/api/cooker-view?weekly_plan_id=5", 5]


@_needs_node
def test_only_the_latest_read_for_a_plan_may_land():
    """A week marked stale while its first read is still out (a swap
    answering before the first read has) starts a second read — and the
    older one, resolving last, must not stand as the settled answer.
    Found by the verifying pass on 2026-09-13; each read carries a number."""
    harness = (
        "var pending = [];\n"
        "global.fetch = function (url) { return new Promise(function (resolve) { pending.push(function (body) {"
        " resolve({ ok: true, json: function () { return Promise.resolve(body); } }); }); }); };\n"
        "function renderMealsStep() {}\n"
        "var cookState = { data: null };\n"
        "var weekState = { step: 'meal', data: { weekly_plan_id: 2 }, cookView: null };\n"
        + _extract("planCookView") + "\n"
        + "async " + _extract("ensureCookDataForMeals") + "\n"
        + "(async function () {\n"
        + "  var first = ensureCookDataForMeals({});\n"
        + "  weekState.cookView.stale = true;\n"
        + "  var second = ensureCookDataForMeals({});\n"
        + "  var out = { reads: pending.length };\n"
        # The newer read answers first, then the older one straggles in.
        + "  pending[1]({ weekly_plan_id: 2, meals: [{ entry_id: 9, meal: 'after the swap' }] });\n"
        + "  await second;\n"
        + "  out.afterNewer = planCookView().meals[0].meal;\n"
        + "  pending[0]({ weekly_plan_id: 2, meals: [{ entry_id: 7, meal: 'before the swap' }] });\n"
        + "  await first;\n"
        + "  out.afterOlder = planCookView().meals[0].meal;\n"
        + "  out.settled = !weekState.cookView.loading && !weekState.cookView.stale;\n"
        + "  console.log(JSON.stringify(out));\n"
        + "})();\n"
    )
    got = _run_node(harness)
    assert got["reads"] == 2
    assert got["afterNewer"] == "after the swap"
    assert got["afterOlder"] == "after the swap", "the older read landed over the newer one"
    assert got["settled"] is True


@_needs_node
def test_a_failed_read_is_held_not_retried_on_every_render():
    """The render at the end of ensureCookDataForMeals would otherwise ask
    again, fail again, render again. A failure is held; opening the meal
    afresh (goMealsStep) or reloading the week (stale) is the retry — and
    the clock says the read failed rather than "Getting the recipe…" for
    good."""
    harness = (
        "var calls = 0;\n"
        "global.fetch = function () { calls += 1; return Promise.resolve({ ok: false }); };\n"
        "var renders = 0;\n"
        "var cookState = { data: null };\n"
        "var weekState = { step: 'meal', data: { weekly_plan_id: 2 }, cookView: null };\n"
        "function renderMealsStep() { renders += 1; ensureCookDataForMeals({}); }\n"
        + _extract("planCookView") + "\n"
        + _extract("planCookViewFailed") + "\n"
        + "async " + _extract("ensureCookDataForMeals") + "\n"
        + "(async function () {\n"
        + "  await ensureCookDataForMeals({});\n"
        + "  await new Promise(function (r) { setTimeout(r, 20); });\n"
        + "  var out = { calls: calls, renders: renders, failed: planCookViewFailed(), view: planCookView() };\n"
        + "  weekState.cookView.stale = true;\n"
        + "  await ensureCookDataForMeals({});\n"
        + "  await new Promise(function (r) { setTimeout(r, 20); });\n"
        + "  out.afterStale = calls;\n"
        + "  console.log(JSON.stringify(out));\n"
        + "})();\n"
    )
    got = _run_node(harness)
    assert got["calls"] == 1 and got["renders"] == 1
    assert got["failed"] is True and got["view"] is None
    assert got["afterStale"] == 2
    # Opening a meal afresh drops the held failure.
    go = _extract("goMealsStep")
    assert "weekState.cookView.failed" in go and "weekState.cookView = null;" in go
    # And the words: the read failed, paired with its way out.
    line = _extract("mealNoRecipeHtml")
    assert "Couldn’t get the recipe just now — go back and open it again." in line


# ------------------------------------ the crumb goes back the way you came


def _meal_screen(back: str, cookable: bool) -> str:
    entry = {"state": "planned", "title": "Lamb chops", "source": "plan", "meta": "15 min",
             "entry_id": 9, "sides": [], "food_groups": ["protein"], "defrost": None, "plate_note": ""}
    day = {"date": "2026-09-15", "isToday": False, "isPast": False,
           "breakfast": None, "lunch": None, "dinner": entry, "snacks": [], "snack": None}
    harness = (
        _ESCAPE
        + "function dayName(d, opts){ return 'Tuesday'; }\n"
        + f"var weekState = {{ step: 'meal', mealBack: {json.dumps(back)}, data: {{ weekly_plan_id: 2, slot_times: {{ dinner: '6:30' }} }}, rhythm: null }};\n"
        + "var swapState = null;\n"
        + "var REHEAT_ACTION_LABEL = 'Mark eaten';\n"
        + "var SWAP_LABEL = 'Swap · I’ll pick';\n"
        + "var cookState = { data: null, cookAheadPicks: {} };\n"
        + "var GRO_ICONS = { chevRight: '<svg></svg>' };\n"
        + "var SLOT_LABELS = { breakfast: 'Breakfast', lunch: 'Lunch', dinner: 'Dinner' };\n"
        + "function capitalizeFirst(s) { return String(s).charAt(0).toUpperCase() + String(s).slice(1); }\n"
        + "function cookMealKey(m) { return 'e' + m.entry_id; }\n"
        + "function cookTicked() { return false; }\n"
        + "var WK_ADD_ICON = '<svg/>'; function humanQtyText(t) { return String(t == null ? '' : t); }\n"
        + "function cookIngredientLabel(i) { return i.item; }\n"
        + "function cookServesShown(m) { return m.default_servings; }\n"
        + "function recipeCitationHtml() { return ''; }\n"
        + "var RECIPE_ICONS = { minus: '<svg/>', plus: '<svg/>', chevLeft: '<svg/>' };\n"
        + f"function planCookView() {{ return {{ is_current_plan: {json.dumps(cookable)}, meals: [] }}; }}\n"
        + "".join(_extract(n) + "\n" for n in (
            "isSnackSlot", "daySlotEntry", "slotWord", "isRealCook", "mealDisplayName",
            "chipsRowHtml", "cookTimeChip", "planCookableNow", "cookMealForEntry",
            "mealCookUnderway", "mealRecipeFor", "mealHeroLine", "mealNoRecipeHtml",
            "cookUnscaledHtml", "cookIngTickId", "cookGetOutRowHtml",
            "recipeTitleHtml", "recipeServesHtml", "recipeIngredientsHtml", "recipeIngredientRowHtml",
            "recipeStepsHtml", "mealIngredientsHtml", "swapStateFor", "swapLineHtml",
            "mealDockHtml", "mealStepHtml"))
        + f"console.log(JSON.stringify(mealStepHtml({json.dumps(day)}, 'dinner')));\n"
    )
    return _run_node(harness)


@_needs_node
def test_the_crumb_names_where_the_meal_was_opened_from():
    from_list = _meal_screen("week", True)
    assert '<button type="button" class="crumb" data-wk-back="week">‹ This week</button>' in from_list
    from_day = _meal_screen("day", True)
    assert '<button type="button" class="crumb" data-wk-back="day">‹ Tuesday</button>' in from_day


@_needs_node
def test_a_meal_cook_cannot_hold_yet_gets_no_way_into_cook_mode():
    """Next week's draft on a Sunday: the recipe is on this screen, and the
    dock offers the two ways to change the meal instead of a "Start
    cooking" that landed on Cook's root with this week's list."""
    html = _meal_screen("week", False)
    assert "data-wk-cook" not in html
    assert 'class="dock-primary wk-act-swap" data-wk-swap="dinner">' in html
    assert 'data-wk-tell="dinner">Tell me what instead<' in html
    # The same meal once Cook holds it (Monday): the start is back.
    html = _meal_screen("week", True)
    assert 'class="dock-primary" data-wk-cook="dinner">Start cooking<' in html
    assert 'class="dock-link wk-act-swap" data-wk-swap="dinner">' in html
    assert "data-wk-tell" not in html


@_needs_node
def test_the_day_steps_card_follows_the_same_rule():
    entry = {"state": "planned", "title": "Lamb chops", "source": "plan", "meta": "15 min",
             "entry_id": 9, "sides": [], "food_groups": [], "defrost": None, "plate_note": ""}
    day = {"date": "2026-09-15", "isPast": False, "dinner": entry, "snacks": []}
    harness = (
        _ESCAPE
        + "var swapState = null;\n"
        + "var REHEAT_ACTION_LABEL = 'Mark eaten';\n"
        + "var SWAP_LABEL = 'Swap · I’ll pick';\n"
        + "var cookable = true;\n"
        + "function planCookableNow() { return cookable; }\n"
        + "".join(_extract(n) + "\n" for n in (
            "isSnackSlot", "daySlotEntry", "isRealCook", "cookTimeChip",
            "swapStateFor", "swapLineHtml", "slotActionsHtml"))
        + f"var day = {json.dumps(day)};\n"
        + "var out = { now: slotActionsHtml(day, 'dinner', false) };\n"
        + "cookable = false;\n"
        + "out.later = slotActionsHtml(day, 'dinner', false);\n"
        + "console.log(JSON.stringify(out));\n"
    )
    got = _run_node(harness)
    assert 'data-wk-cook="dinner">Cook this · 15 min<' in got["now"]
    assert 'data-wk-swap="dinner">' in got["now"]
    assert "data-wk-cook" not in got["later"]
    # The swap takes the row on its own, full width, and the chat line stays.
    assert 'class="wk-act wk-act-primary" data-wk-swap="dinner">' in got["later"]
    assert "Tell me what instead" in got["later"]


@_needs_node
def test_the_back_gesture_and_cook_modes_return_keep_the_crumb_right():
    """goMealsStep records which way a Meal step was opened; history and
    cook mode's origin carry it, so a back gesture or "‹ Tuesday" out of
    cook mode redraws the crumb the way it was."""
    harness = (
        "var pushed = [], replaced = [];\n"
        "global.window = { history: { pushState: function (s) { pushed.push(s); }, replaceState: function (s) { replaced.push(s); } } };\n"
        "var panels = { week: { dataset: { built: '1' } } };\n"
        "var scrollEl = null;\n"
        "function renderMealsStep() {}\n"
        "function loadPlanChores() {}\n"
        "var weekState = { step: 'week', selectedIndex: null, mealSlot: 'dinner', mealBack: 'day' };\n"
        + "".join(_extract(n) + "\n" for n in (
            "mealsStepHistoryState", "pushMealsStepHistory", "replaceMealsStepHistory",
            "goMealsStep", "applyMealsStepFromHistory", "mealsOriginFor"))
        + "function dayName() { return 'Tuesday'; }\n"
        + "var out = {};\n"
        + "goMealsStep('meal', { dayIndex: 1, slot: 'dinner', back: 'week' });\n"
        + "out.fromList = weekState.mealBack;\n"
        + "out.history = pushed[pushed.length - 1].mealsBack;\n"
        + "out.origin = mealsOriginFor({ date: '2026-09-15' }, 'dinner').mealsBack;\n"
        + "goMealsStep('day', { dayIndex: 1 });\n"
        + "goMealsStep('meal', { slot: 'dinner' });\n"
        + "out.fromDay = weekState.mealBack;\n"
        + "applyMealsStepFromHistory({ mealsStep: 'meal', mealsDay: 1, mealsSlot: 'dinner', mealsBack: 'week' });\n"
        + "out.restored = weekState.mealBack;\n"
        + "console.log(JSON.stringify(out));\n"
    )
    got = _run_node(harness)
    assert got == {"fromList": "week", "history": "week", "origin": "week", "fromDay": "day", "restored": "week"}
    # ...and cook mode hands it back on the way out.
    assert "back: origin.mealsBack" in _extract("cookExitFocus")
