"""
Cooking is two stages: the recipe, and one step at a time.

Emily's approved design, 2026-09-18 ("The recipe is the recipe" and "The
step-by-step view has no timestamps and no batch prompt" — boards
13-recipe and 14-cooking), on top of the 2026-09-09 journey ("Cooking:
before you start, one step at a time, and a proper finish"). Cook mode is
two stages of the SAME step of the Kitchen tab (never routes, never a page
with its own back button):

    'recipe' The recipe — the dish, "Cooking for", the ingredients as a
             ticklist with quantities, the steps, "Start cooking".
    'step'   One step at a time, big type: the cooker. Back / Next step,
             "Done — on the table" on the last.

Until 2026-09-18 there were three ("Before you start", the step, and the
whole method); the recipe screen carries what the first and the last did
between them. The finish is the same write "Mark it cooked" always made
(tests/test_kitchen_and_preferences.py). The screens themselves are
covered in tests/test_recipe_screen.py; this file is about the journey —
which stage you land on, what a tap does, and what survives a reload.

These are BEHAVIOUR tests, not source-marker ones: shell.js has no JS test
harness in this repo, so the screen's own functions are lifted out and run
under node against a small stub, the same way tests/test_leftovers_batch.py
and tests/test_kitchen_and_preferences.py already run theirs. That matters
more than usual here, because the two things most likely to break — which
stage you land on, and whether a tick you made an hour ago is still there —
are exactly the kind a "does the source mention the right word" test cannot
see.
"""

from __future__ import annotations

import json
import shutil

import nodeharness
from pathlib import Path

import pytest

SHELL_JS_PATH = Path(__file__).resolve().parent.parent / "static" / "shell.js"
SHELL_JS = SHELL_JS_PATH.read_text(encoding="utf-8")
SHELL_CSS = (Path(__file__).resolve().parent.parent / "static" / "shell.css").read_text(
    encoding="utf-8"
)

_needs_node = pytest.mark.skipif(
    shutil.which("node") is None,
    reason="node is needed to execute the screen's own functions",
)


def _extract(name: str, source: str = SHELL_JS) -> str:
    """Lift one brace-balanced `function name(...) {...}` out of the file.

    Keeps a leading `async` — dropping it turns an await inside the body
    into a syntax error, which is how this first met cookStepServings.
    """
    start = source.index(f"function {name}(")
    if source[max(0, start - 6) : start] == "async ":
        start -= 6
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


def _var_block(name: str, source: str = SHELL_JS) -> str:
    """Lift one bracket-balanced `var NAME = [...]` — for the kit word list,
    which is data the screen reads rather than a function it calls."""
    start = source.index(f"var {name} = [")
    i = source.index("[", start)
    depth, j = 0, i
    while True:
        if source[j] == "[":
            depth += 1
        elif source[j] == "]":
            depth -= 1
            if depth == 0:
                break
        j += 1
    return source[start : j + 1] + ";"


def _regex_const(name: str, source: str = SHELL_JS) -> str:
    """Lift one `var NAME = /.../;` — the oven pattern, taken from the file
    so a test can never pass against a rule the app no longer applies."""
    start = source.index(f"var {name} = /")
    return source[start : source.index(";", start) + 1]


def _string_const(name: str, source: str = SHELL_JS) -> str:
    """Lift one `var NAME = '...';` — the storage key prefix, taken from the
    file rather than retyped, so a test can never pass against a key the app
    no longer writes."""
    start = source.index(f"var {name} = '")
    return source[start : source.index(";", start) + 1]


# The furniture the cook screen needs but that these tests are not about.
# Everything genuinely under test is _extract'd from shell.js itself.
_STUBS = """
function escapeHtml(s){return String(s == null ? '' : s)
  .replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');}
var ICONS = { arrow: '<svg data-icon="arrow"></svg>' };
var COOK_ICONS = { check: '<svg data-icon="check"></svg>', mic: '<svg data-icon="mic"></svg>' };
var COOK_VOICE_ENABLED = false;
function cookBackLabel(){ return 'Kitchen'; }
function cookDateLabel(d){ return 'Tuesday, Sep 9'; }
function cookPrepCutHtml(){ return ''; }
function cookReheatFocusHtml(m){ return '<div class="cook-reheat"></div>'; }
var RECIPE_ICONS = { minus: '<svg data-icon="minus"></svg>', plus: '<svg data-icon="plus"></svg>', chevLeft: '<svg data-icon="chev"></svg>' };
function renderCook(){ renderCount += 1; }
var renderCount = 0;
function showToast(){ }
function dayName(d, o){ return { '2026-09-11': 'Friday', '2026-09-12': 'Saturday' }[d] || 'Thursday'; }

// Just enough DOM for the stepper: it reads the tapped button's own
// attributes, finds the .cook-serves wrapper it sits in, and writes the
// count into a span. Everything else it does is state.
var _counts = {};
function fakeStepper(idx, delta, recipe, base) {
  var wrap = { getAttribute: function (a) {
    return a === 'data-recipe' ? recipe : (a === 'data-base' ? String(base) : null); },
    querySelector: function () {
      if (!_counts[idx]) _counts[idx] = { textContent: '' };
      return _counts[idx];
    } };
  return { getAttribute: function (a) {
             return a === 'data-idx' ? String(idx) : (a === 'data-delta' ? String(delta) : null); },
           closest: function () { return wrap; } };
}

// A /api/recipes/scale that answers with amounts proportional to the count
// asked for, after `latency[n]` ticks — so a test can make replies land out
// of order on purpose.
var fetchCalls = [];
var latency = {};
function fetch(url) {
  var n = parseInt(/servings=(\d+)/.exec(url)[1], 10);
  fetchCalls.push(n);
  var body = { scaled_ingredients: [{ qty: String(n * 2), item: 'Chicken thighs' },
                                    { qty: (n / 2) + ' tbsp', item: 'Olive oil' }],
               unscaled_items: [] };
  var wait = latency[n] || 0;
  return new Promise(function (res) {
    setTimeout(function () { res({ ok: true, json: function () { return Promise.resolve(body); } }); }, wait);
  });
}

// A localStorage that behaves like the browser's, so the persistence tests
// are about the code's own reads and writes rather than about a mock that
// agrees with it.
var _ls = {};
var window = { localStorage: {
  getItem: function (k) { return Object.prototype.hasOwnProperty.call(_ls, k) ? _ls[k] : null; },
  setItem: function (k, v) { _ls[k] = String(v); },
  removeItem: function (k) { delete _ls[k]; },
  key: function (i) { return Object.keys(_ls)[i]; },
  get length() { return Object.keys(_ls).length; }
} };
"""

_FUNCTIONS = [
    "cookMealKey",
    "cookTickPlanId",
    "cookReadTicks",
    # New with the serving count moving into the tick record (Emily,
    # 2026-09-10): cookReadTicks hydrates the overrides through it.
    "cookReadServes",
    "cookWriteTicks",
    "cookTicked",
    "cookToggleTick",
    "cookSetTick",
    "cookUnscaledHtml",
    "humanQtyAmount",
    "humanQtyText",
    "cookIngredientLabel",
    "cookIngredientNouns",
    "cookStepNeeds",
    "cookFocusPrepTasks",
    "cookFocusPrepHtml",
    # A dish whose eggs an earlier cook boiled (batch_components.py).
    "cookMadeAheadLinesHtml",
    "cookServesShown",
    "cookApplyServesOverride",
    "cookStepServings",
    "cookFocusMeal",
    "cookFollowFocusedMeal",
    "cookFirstUndoneStep",
    "cookGoStage",
    "cookStartCooking",
    "cookStepForward",
    "cookStepBack",
    "cookIngTickId",
    "cookGetOutRowHtml",
    "recipeCitationHtml",  # the credit line at the foot of the recipe (recipe photo import)
    "cookDockHtml",
    "cookDockCookedHtml",
    # The recipe and the cooker (2026-09-18).
    "recipeTitleHtml",
    "recipeServesHtml",
    "recipeIngredientsHtml",
    "recipeIngredientRowHtml",
    "recipeStepsHtml",
    "cookRecipeLinesHtml",
    "cookRecipeHtml",
    "cookRecipeDockHtml",
    "cookProgressHtml",
    "cookNextStepLine",
    "cookCookerHtml",
    "cookCookerDockHtml",
    "cookFocusHtml",
]


def _run(body: str, state: dict | None = None) -> object:
    """Run `body` (which must console.log one JSON value) with the cook
    screen's real functions in scope."""
    base = {
        "data": None,
        "meals": [],
        "focusIdx": 0,
        "focusStage": "recipe",
        "serves": {},
        "servesSeq": 0,
        "focusMealKey": None,
        "stepIdx": 0,
        "ticks": None,
        "ticksFor": None,
        "pendingScrollTop": False,
    }
    base.update(state or {})
    harness = (
        _STUBS
        + f"var cookState = {json.dumps(base)};\n"
        + f"var MEAL = {json.dumps(_MEAL)};\n"
        + f"var OTHER = {json.dumps(_OTHER_MEAL)};\n"
        + _string_const("COOK_TICKS_PREFIX")
        + "\n"
        # cookIngredientLabel reads amounts through humanQtyText (item 14,
        # design-tidy pass 2026-09-11) — its own array of nice fractions,
        # taken from the file rather than duplicated here.
        + _var_block("HUMAN_QTY_FRACTIONS")
        + "\n"
        + "\n".join(_extract(n) for n in _FUNCTIONS)
        + "\n"
        + body
    )
    res = nodeharness.run_node(harness, timeout=30)
    assert res.returncode == 0, f"node failed: {res.stderr}"
    return json.loads(res.stdout.strip())


# ---------- the meal these tests cook ----------

_MEAL = {
    "entry_id": 41,
    "date": "2026-09-09",
    "slot": "dinner",
    "meal": "Sheet-pan chicken thighs",
    "cooked_status": "pending",
    "is_leftovers": False,
    "has_full_recipe": True,
    "default_servings": 4,
    "prep_time_minutes": 15,
    "cook_time_minutes": 35,
    "advance_prep_notes": "",
    "advance_prep_step_indices": [],
    "reasoning": "Quick on a Tuesday",
    "batch_note": None,
    "covers_note": None,
    "servings": None,
    "sides_label": "",
    "attendance": None,
    "component_category": None,
    "ingredients": [
        {"qty": "4", "item": "Chicken thighs"},
        {"qty": "2 tbsp", "item": "Olive oil"},
        {"qty": "1 lb", "item": "Baby potatoes, halved"},
        {"qty": "1 tsp", "item": "Smoked paprika"},
    ],
    "instructions": [
        "Preheat the oven to 425°F and line a rimmed baking sheet with parchment.",
        "Toss the potatoes with olive oil and smoked paprika.",
        "Roast for 35 minutes, until the chicken thighs read 165°F.",
    ],
}

_OTHER_MEAL = dict(
    _MEAL, entry_id=99, meal="Overnight oats", instructions=["Stir."], ingredients=[]
)

_VIEW = {"weekly_plan_id": 12, "meals": [_MEAL], "prep_tasks": []}


def _focus(stage: str = "recipe", step: int = 0, meal: dict | None = None, **extra) -> str:
    m = meal or _MEAL
    view = dict(_VIEW, meals=[m])
    state = {"data": view, "focusStage": stage, "stepIdx": step}
    state.update(extra)
    return _run(
        "console.log(JSON.stringify(cookFocusHtml(cookState.data, cookState.data.meals, 0)));",
        state,
    )


# ---------- "Cook this opens the recipe" ----------


@_needs_node
def test_cook_this_opens_the_recipe():
    """Every entry point lands on the same first stage: the recipe."""
    html = _focus("recipe")
    assert '<h1 class="recipe-title">Sheet-pan chicken thighs</h1>' in html
    assert '<span class="cook-serves-label">Cooking for</span>' in html
    # The ticklist, with the amount that actually goes in the pan.
    assert 'aria-label="Ingredients"' in html
    assert "4 Chicken thighs" in html
    assert "2 tbsp Olive oil" in html
    assert 'data-cook="check-ing"' in html
    # ...and the steps, numbered.
    assert 'aria-label="Steps"' in html
    assert "Preheat the oven" in html and "Roast for 35 minutes" in html
    # Nothing that used to sit on "Before you start" (2026-09-18).
    for gone in ("Before you start", "Everything out", "Pans and kit", "Baking sheet", "Oven at",
                 "The whole method", "15m prep", "35m cook", 'class="cook-hero"', "cook-meta-chip", "Tuesday, Sep 9"):
        assert gone not in html, gone


@_needs_node
def test_every_way_into_cook_mode_resets_to_the_first_stage():
    """cookEnterFocus is the one door, so the reset belongs there rather
    than at each of the five call sites that use it."""
    enter = _extract("cookEnterFocus")
    assert "cookState.focusStage = 'recipe';" in enter
    assert "cookState.stepIdx = 0;" in enter


# ---------- "starting from there enters one-step-at-a-time" ----------


@_needs_node
def test_starting_enters_one_step_at_a_time():
    html = _focus("recipe")
    assert 'data-cook="start-cooking"' in html
    assert "Start cooking" in html

    started = _run(
        "cookStartCooking();\n"
        "console.log(JSON.stringify({ stage: cookState.focusStage, step: cookState.stepIdx }));",
        {"data": _VIEW},
    )
    assert started == {"stage": "step", "step": 0}


@_needs_node
def test_one_step_at_a_time_shows_exactly_one_instruction():
    html = _focus("step", step=1)
    assert '<p class="cook-bigstep">Toss the potatoes with olive oil and smoked paprika.</p>' in html
    assert "Preheat the oven" not in html
    # The crumb goes back to the recipe; the eyebrow says where you are.
    assert '<button type="button" class="crumb" data-cook="stage" data-stage="recipe">&lsaquo; Recipe</button>' in html
    assert 'class="cook-eyebrow cook-cooker-eyebrow">Sheet-pan chicken thighs · step 2 of 3</p>' in html
    # The bar: one segment per step, lit up to this one.
    assert html.count('class="cook-progress-seg is-done"') == 2
    assert html.count('class="cook-progress-seg"') == 1
    assert 'aria-valuenow="2"' in html and 'aria-valuemax="3"' in html
    # The next step, previewed in passing.
    assert '<p class="cook-next">Next: roast for 35 minutes, until the chicken thighs read 165°F.</p>' in html
    # Back and Next step in the dock, and nothing else.
    assert '<button type="button" class="cook-dock-back" data-cook="step-prev">' in html
    assert '<button type="button" class="cook-hero-action" data-cook="step-next"><span>Next step</span></button>' in html
    for gone in ("Start at", "Step 2 of 3", "The whole method", "cook-hero-slim", "For this step", "batch cook"):
        assert gone not in html, gone


@_needs_node
def test_a_step_names_what_that_step_needs_from_its_own_words():
    html = _focus("step", step=1)
    assert '<p class="cook-step-needs">2 tbsp Olive oil · 1 lb Baby potatoes, halved · 1 tsp Smoked paprika</p>' in html
    # Nothing the step doesn't mention.
    assert "4 Chicken thighs" not in html
    # A step that needs nothing gets no line.
    assert "cook-step-needs" not in _focus("step", step=0, meal=dict(_MEAL, instructions=["Stir.", "Serve."]))


@_needs_node
def test_the_next_line_is_the_next_steps_first_sentence_said_in_passing():
    got = _run(
        "console.log(JSON.stringify([\n"
        "  cookNextStepLine('Pour in the stock and the lemon juice. Simmer, lid on.'),\n"
        "  cookNextStepLine('Preheat the oven to 425°F and line a sheet.'),\n"
        "  cookNextStepLine('Add 1.5 cups of rice, then stir.'),\n"
        "  cookNextStepLine('   '),\n"
        "  cookNextStepLine('Serve')\n"
        "]));"
    )
    assert got == [
        "Next: pour in the stock and the lemon juice.",
        "Next: preheat the oven to 425°F and line a sheet.",
        "Next: add 1.5 cups of rice, then stir.",
        "",
        "Next: serve.",
    ]
    # The last step previews nothing.
    assert "cook-next" not in _focus("step", step=2)


@_needs_node
def test_next_step_marks_the_step_done_and_moves_on():
    got = _run(
        "cookStepForward();\n"
        "console.log(JSON.stringify({\n"
        "  step: cookState.stepIdx,\n"
        "  firstDone: cookTicked('steps', 'e41:0'),\n"
        "  secondDone: cookTicked('steps', 'e41:1')\n"
        "}));",
        {"data": _VIEW, "focusStage": "step", "stepIdx": 0},
    )
    assert got == {"step": 1, "firstDone": True, "secondDone": False}


@_needs_node
def test_stepping_back_re_reads_a_step_rather_than_undoing_it():
    got = _run(
        "cookStepForward();\n"        # step 0 done, now on 1
        "cookStepBack();\n"           # back to 0
        "console.log(JSON.stringify({\n"
        "  step: cookState.stepIdx, firstStillDone: cookTicked('steps', 'e41:0')\n"
        "}));",
        {"data": _VIEW, "focusStage": "step", "stepIdx": 0},
    )
    assert got == {"step": 0, "firstStillDone": True}


@_needs_node
def test_back_from_step_one_is_back_to_the_recipe():
    got = _run(
        "cookStepBack();\n"
        "console.log(JSON.stringify(cookState.focusStage));",
        {"data": _VIEW, "focusStage": "step", "stepIdx": 0},
    )
    assert got == "recipe"
    # ...and so is the crumb, whatever step you are on.
    got = _run(
        "cookGoStage('recipe');\n"
        "console.log(JSON.stringify([cookState.focusStage, cookState.stepIdx]));",
        {"data": _VIEW, "focusStage": "step", "stepIdx": 2},
    )
    assert got == ["recipe", 2], "the cursor is kept, so Keep cooking resumes"


@_needs_node
def test_the_last_step_offers_the_finish_rather_than_a_next_into_nothing():
    """Finishing is the existing "Mark it cooked" write under new words —
    "Done — on the table" (board 14-cooking) — the same focus-check handler
    that marks the meal cooked."""
    html = _focus("step", step=2)
    assert 'data-cook="step-next"' not in html
    assert 'data-cook="focus-check" data-entry-id="41" data-next="done"' in html
    assert "<span>Done — on the table</span>" in html
    assert "Mark it cooked" not in html
    # Back is still there beside it.
    assert 'data-cook="step-prev"' in html


@_needs_node
def test_a_dish_with_no_steps_is_never_offered_a_step_through():
    no_steps = dict(_MEAL, instructions=[])
    html = _focus("recipe", meal=no_steps)
    assert 'data-cook="start-cooking"' not in html
    assert "Done — on the table" in html
    # ...and "Fill in this recipe" is on the Steps card.
    assert 'data-cook="fill"' in html and "No steps saved yet." in html


@_needs_node
def test_a_cook_under_way_is_offered_keep_cooking_never_a_time():
    html = _run(
        "cookSetTick('steps', 'e41:0', true);\n"
        "console.log(JSON.stringify(cookFocusHtml(cookState.data, cookState.data.meals, 0)));",
        {"data": _VIEW},
    )
    assert "<span>Keep cooking</span>" in html and "Start cooking" not in html
    started = dict(_MEAL, cook_started_at="2026-09-09T18:02:00")
    html = _focus("recipe", meal=started)
    assert "<span>Keep cooking</span>" in html
    assert "6:02" not in html and "Started" not in html


@_needs_node
def test_start_cooking_resumes_a_half_cooked_dish():
    """The first step nobody has ticked, so putting the phone down and
    picking it back up does not restart the recipe."""
    got = _run(
        "cookSetTick('steps', 'e41:0', true);\n"
        "cookSetTick('steps', 'e41:1', true);\n"
        "cookStartCooking();\n"
        "console.log(JSON.stringify(cookState.stepIdx));",
        {"data": _VIEW},
    )
    assert got == 2


# ---------- "ticked prep and ticked steps survive leaving the screen and coming back" ----------


@_needs_node
def test_a_tick_survives_the_page_being_thrown_away():
    """The acceptance criterion, tested the way it actually fails: an
    installed PWA has its web view discarded the moment the phone goes down
    mid-cook, so "leaving the screen" includes a real reload."""
    got = _run(
        "cookToggleTick('steps', 'e41:1');\n"
        "cookToggleTick('ings', 'e41:olive oil');\n"
        # The reload: everything in memory goes, the browser's storage stays.
        "cookState.ticks = null; cookState.ticksFor = null;\n"
        "console.log(JSON.stringify({\n"
        "  step: cookTicked('steps', 'e41:1'),\n"
        "  ing: cookTicked('ings', 'e41:olive oil'),\n"
        "  other: cookTicked('steps', 'e41:2'),\n"
        "  stored: Object.keys(_ls)\n"
        "}));",
        {"data": _VIEW},
    )
    assert got["step"] is True
    assert got["ing"] is True
    assert got["other"] is False
    assert got["stored"] == ["pomona.cookTicks.p12"]


@_needs_node
def test_a_tick_is_filed_under_the_meal_not_its_place_in_the_list():
    """The old in-memory keys were the meal's INDEX into cookState.data.meals
    — an array rebuilt by every load and every write response, so an index
    that survived a reload would put last night's ticks on tonight's dish."""
    got = _run(
        "console.log(JSON.stringify({\n"
        "  byId: cookMealKey({ entry_id: 41, meal: 'Sheet-pan chicken thighs' }),\n"
        "  byName: cookMealKey({ entry_id: null, meal: 'Sheet-pan Chicken Thighs' })\n"
        "}));"
    )
    assert got["byId"] == "e41"
    assert got["byName"] == "nsheet-pan chicken thighs"


@_needs_node
def test_an_ingredient_tick_is_filed_under_its_name_so_rescaling_keeps_it():
    """The serving stepper rewrites every quantity in place, and a plates
    pass can add a side's ingredients to the list — a tick keyed by position
    would move onto the wrong row for both."""
    got = _run(
        "console.log(JSON.stringify([\n"
        "  cookIngTickId({ qty: '2 tbsp', item: 'Olive oil' }, 0),\n"
        "  cookIngTickId({ qty: '4 tbsp', item: 'Olive oil' }, 3)\n"
        "]));"
    )
    assert got[0] == got[1] == "olive oil"


@_needs_node
def test_last_weeks_ticks_expire_with_last_weeks_plan():
    """Keyed by weekly_plan_id: a new week is a new id, and writing prunes
    every other plan's entry so the store cannot only ever grow."""
    got = _run(
        "cookToggleTick('steps', 'e41:0');\n"
        "cookState.data = { weekly_plan_id: 13, meals: [] };\n"
        "cookState.ticks = null; cookState.ticksFor = null;\n"
        "console.log(JSON.stringify({\n"
        "  carriedOver: cookTicked('steps', 'e41:0'),\n"
        "  before: Object.keys(_ls)\n"
        "}));",
        {"data": _VIEW},
    )
    assert got["carriedOver"] is False
    assert got["before"] == ["pomona.cookTicks.p12"]

    pruned = _run(
        "cookToggleTick('steps', 'e41:0');\n"
        "cookState.data = { weekly_plan_id: 13, meals: [] };\n"
        "cookState.ticks = null; cookState.ticksFor = null;\n"
        "cookToggleTick('steps', 'e99:0');\n"
        "console.log(JSON.stringify(Object.keys(_ls)));",
        {"data": _VIEW},
    )
    assert pruned == ["pomona.cookTicks.p13"]


@_needs_node
def test_a_browser_that_refuses_storage_still_renders_the_screen():
    """Safari in private mode throws on localStorage rather than returning
    null, and a thrown memory aid must not take the cook screen with it."""
    got = _run(
        "window.localStorage.getItem = function () { throw new Error('nope'); };\n"
        "window.localStorage.setItem = function () { throw new Error('nope'); };\n"
        "var html = cookFocusHtml(cookState.data, cookState.data.meals, 0);\n"
        "cookToggleTick('steps', 'e41:0');\n"
        "console.log(JSON.stringify(html.indexOf('aria-label=\"Ingredients\"') !== -1));",
        {"data": _VIEW},
    )
    assert got is True


@_needs_node
def test_prep_ticks_are_still_the_servers_and_nothing_here_touches_them():
    """prep_tasks.status through check_off_prep_step, exactly as before —
    the two lists this slice adds are the ones with no column behind them."""
    view = dict(
        _VIEW,
        prep_tasks=[
            {
                "id": 5,
                "meal_plan_entry_id": 41,
                "task_type": "defrost",
                "task_date": "2026-09-08",
                "description": "Move the chicken to the fridge",
                "status": "done",
            }
        ],
    )
    html = _run(
        "console.log(JSON.stringify(cookFocusHtml(cookState.data, cookState.data.meals, 0)));",
        {"data": view},
    )
    assert 'data-cook="check-prep"' in html
    assert 'data-prep-id="5"' in html
    assert "1 of 1" in html or "Prep’s done" in html


# ---------- the rules this screen inherited ----------


@_needs_node
def test_cook_root_has_one_apricot_and_it_is_the_docks():
    """DESIGN_SYSTEM Rule 5, one screen at a time. Since the shelf design
    (2026-09-13) the root's one apricot is "Start cooking" in its own dock
    (cookRootDockHtml); nothing else on the root is a primary, and the
    cook step one level down keeps its own dock exactly where "Mark it
    cooked" already lived."""
    for name in ("renderKitchen", "cookMoreRowsHtml", "kitchenCookingTodayHtml", "cookTonightCardHtml",
                 "cookShelfTileHtml", "cookGetReadyRowsHtml"):
        body = _extract(name)
        assert "cook-hero-action" not in body, f"{name} put a primary on the root"
        assert "dock-primary" not in body, f"{name} put a second primary on the root"
    root_dock = _extract("cookRootDockHtml")
    assert root_dock.count("dock-primary") == 2, "one per branch: Start cooking, or Mark eaten"
    assert "cookRecipeDockHtml(meal)" in _extract("cookRecipeHtml")
    assert "cookCookerDockHtml(meal)" in _extract("cookCookerHtml")
    assert "cook-dock" in _extract("cookDockHtml")


@_needs_node
def test_each_cooking_stage_has_exactly_one_apricot():
    """Rule 5 again, one level down: a screen gets one primary fill."""
    assert _focus("recipe").count("cook-hero-action") == 1
    assert _focus("step", step=1).count("cook-hero-action") == 1
    assert _focus("step", step=2).count("cook-hero-action") == 1
    # Back is an outline, not a second fill.
    back = SHELL_CSS[SHELL_CSS.index(".cook-dock-back {"):]
    back = back[:back.index("}")]
    assert "var(--apricot)" not in back and "border: 1.5px solid var(--hairline-strong)" in back


@_needs_node
def test_the_recipe_carries_only_a_fact_under_the_title_never_the_batch():
    """DESIGN_SYSTEM §3 and §8: the recipe's quiet line is the recipe's own
    advance-prep note or what an earlier cook already made — a fact about
    the cooking. The batch ("Cooking for 6 — covers Thursday"), the
    planner's reasoning and the minutes are the plan's (2026-09-18)."""
    assert "recipe-line" not in _focus("recipe")
    assert "Quick on a Tuesday" not in _focus("recipe")
    with_batch = dict(_MEAL, covers_note="Cooking for 6 — covers tonight and leftovers on Thursday.",
                      servings=6, batch_note="Bulk", meal_count=2)
    html = _focus("recipe", meal=with_batch)
    for gone in ("Cooking for 6", "covers", "recipe-line", "Bulk", "for 6"):
        assert gone not in html, gone
    with_note = dict(_MEAL, advance_prep_notes="Marinate the chicken overnight.")
    assert '<p class="recipe-line">Marinate the chicken overnight.</p>' in _focus("recipe", meal=with_note)
    assert "recipe-line" not in _focus("step", step=1, meal=with_note)


@_needs_node
def test_the_cooker_names_the_dish_once_in_its_eyebrow():
    """§8: a subtitle that says in a sentence what the content below says
    anyway. The eyebrow names the dish and the step, and stops."""
    html = _focus("step", step=1)
    assert "Sheet-pan chicken thighs" in html
    assert html.count("Sheet-pan chicken thighs") == 1


@_needs_node
def test_a_reheat_night_still_has_no_cook_screen():
    """The app's existing rule — there is no cook here, so there is nothing
    for a cook journey to hold. Untouched by this slice."""
    reheat = dict(_MEAL, is_leftovers=True)
    html = _focus("recipe", meal=reheat)
    assert "cook-reheat" in html
    assert "recipe-title" not in html


@_needs_node
def test_a_stage_with_nothing_behind_it_falls_back_rather_than_rendering_a_hole():
    no_steps = dict(_MEAL, instructions=[])
    html = _focus("step", step=4, meal=no_steps)
    assert "recipe-title" in html
    assert "cook-bigstep" not in html
    # ...and a stage name from before 2026-09-18 lands on the recipe too.
    assert "recipe-title" in _focus("prep")
    assert "recipe-title" in _focus("method")


# ---------- the shape of it, in CSS ----------


def test_the_get_out_row_clears_the_44px_floor():
    """Rule 6. The whole row is the control on this screen, not just the box
    beside it — it is read at arm's length with wet hands."""
    block = SHELL_CSS[SHELL_CSS.index("\n.cook-getout-row {") :][:400]
    assert "min-height: 48px" in block
    assert "width: 100%" in block
    # ...and the recipe card's rows keep the floor (board 13-recipe: 44px).
    assert "min-height: 44px" in SHELL_CSS[SHELL_CSS.index(".recipe-card .cook-getout-row {") :][:80]


def test_the_dock_is_sticky_and_uses_tokens_only():
    # The box is `.dock` as of nav v2 part 2 (2026-09-10) — Emily's rule 2
    # puts the same strip on Plan and Shop, and one component cannot be two
    # implementations. Cook still renders `class="dock cook-dock"`; the
    # cook-scoped selectors below it are unchanged.
    block = SHELL_CSS[SHELL_CSS.index(".dock {") :][:600]
    assert "position: sticky" in block
    assert "bottom: 0" in block
    assert "#" not in block, "Rule 9 — every colour goes through a token"


def test_the_count_note_clears_aa_rather_than_shipping_just_under_it():
    """Measured in-browser: --ink-inactive put this 11px/700 note at 3.21:1
    on ground in light, and --ink-secondary at 4.44 — still under AA for
    text this size. A knowingly sub-AA value on a NEW screen is a decision,
    not a note, so the count takes body ink (12.78:1 / 14.4:1). The eyebrow
    beside it is a label and stays muted."""
    block = SHELL_CSS[SHELL_CSS.index(".cook-sectionnote {") :][:1200]
    body = block[: block.index("}")]
    assert "color: var(--ink);" in body
    assert "var(--ink-inactive)" not in body
    assert "var(--ink-secondary)" not in body


def test_the_new_cook_css_carries_no_literal_colours():
    """Rule 9: a literal hex outside theme.css is a review failure."""
    start = SHELL_CSS.index("/* ---------- The recipe, as the recipe ----------")
    end = SHELL_CSS.index("@media (min-width: 1024px) {", start)
    assert "#" not in SHELL_CSS[start:end]


# ---------- the review round, 2026-09-10 ----------
# Five things an independent reviewer reproduced in a real Chromium against
# the first commit of this branch. The two blockers are the first two.


@_needs_node
def test_every_stage_reads_its_amounts_off_the_meal():
    """NOT a guard against the servings blocker, and it was written as one —
    the reviewer caught that it hand-mutates the meal and the OLD renderers
    read from the meal too, so it passed on the broken commit. Kept for what
    it does cover, which is that all three stages read one source. The real
    guard is test_a_tap_on_the_stepper_is_carried_by_every_stage below,
    which drives cookStepServings itself.
    """
    got = _run(
        # Exactly what the handler does with the scale response.
        "var meal = cookState.data.meals[0];\n"
        "meal.ingredients = [{ qty: '8', item: 'Chicken thighs' },\n"
        "                    { qty: '4 tbsp', item: 'Olive oil' },\n"
        "                    { qty: '2 lb', item: 'Baby potatoes, halved' },\n"
        "                    { qty: '2 tsp', item: 'Smoked paprika' }];\n"
        "meal.default_servings = 8;\n"
        "cookStartCooking();\n"
        "var step = cookFocusHtml(cookState.data, cookState.data.meals, 0);\n"
        "cookState.stepIdx = 1;\n"
        "var step2 = cookFocusHtml(cookState.data, cookState.data.meals, 0);\n"
        "cookGoStage('recipe');\n"
        "var back = cookFocusHtml(cookState.data, cookState.data.meals, 0);\n"
        "console.log(JSON.stringify({ step2: step2, back: back }));",
        {"data": _VIEW},
    )
    # What the step needs is the rescaled amount, not the recipe's own.
    assert "4 tbsp Olive oil" in got["step2"]
    assert "2 lb Baby potatoes, halved" in got["step2"]
    assert "2 tbsp Olive oil" not in got["step2"]
    # ...and stepping back to the recipe does not undo it: the stepper and
    # the list agree with it.
    assert ">8<" in got["back"], "the serving count follows the rescale"
    assert "8 Chicken thighs" in got["back"]
    assert "4 Chicken thighs" not in got["back"]


@_needs_node
def test_a_tick_is_read_by_the_renderer_not_patched_in_beside_it():
    """Same caveat as the test above: this hand-mutates rather than tapping,
    so it is a statement about the renderer, not a guard on the blocker."""
    got = _run(
        "var meal = cookState.data.meals[0];\n"
        "cookState.focusMealKey = cookMealKey(meal);\n"
        "cookToggleTick('ings', cookMealKey(meal) + ':olive oil');\n"
        "meal.ingredients = [{ qty: '4 tbsp', item: 'Olive oil' }];\n"
        "meal.default_servings = 8;\n"
        "console.log(JSON.stringify(recipeIngredientsHtml(meal, 0, true)));",
        {"data": _VIEW},
    )
    assert "4 tbsp Olive oil" in got
    # The tick is filed under the name, so rescaling keeps it.
    assert "is-done" in got


def test_the_serving_stepper_no_longer_rewrites_the_lists_in_place():
    """Source-level, and deliberately narrow: the behavioural guards are the
    two tests above and below this one, which drive the function itself. All
    this pins is that neither ingredient list is patched in place any more —
    the one thing that, by itself, put the screen's state and its pixels out
    of step. The count in the stepper IS still written straight to its span,
    on purpose: that is the optimistic half, and it is asserted for real in
    test_three_fast_taps_count_three_times."""
    fn = _extract("cookStepServings")
    assert "innerHTML" not in fn, "no list is patched in place any more"
    assert "cook-getout-" not in fn
    assert "cook-ings-" not in fn


def test_the_body_makes_room_for_the_sticky_dock():
    """BLOCKER. .cook-dock is sticky with an opaque background and .cook-body
    had no foot, so at 390x780 two of three ingredients and the whole Pans
    and kit section sat behind it at first paint — and on a tab's first
    three visits, when the coaching row shrinks the scrollport, so did all
    of it. The dock is measured rather than guessed because its height
    changes with the stage and with wrapping."""
    block = SHELL_CSS[SHELL_CSS.index(".cook-body {") :][:400]
    assert "var(--cook-dock-h" in block
    wired = _extract("wireCookDock")
    assert "--cook-dock-h" in wired
    assert "dock.offsetHeight" in wired
    # ...and it has to run before the scroll is restored, since it changes
    # the height that scroll position is measured against.
    render = _extract("renderCook")
    assert render.index("wireCookDock(view)") < render.index("scrollEl.scrollTop =")


@_needs_node
def test_a_freeform_meal_is_not_pointed_at_a_fill_button_that_does_not_exist():
    """CONCERN (2026-09-10). A freeform meal (no saved recipe) has nothing
    to fill in, so it says so and offers no fill control; a SAVED recipe
    with nothing in it does have one, on the Steps card."""
    freeform = dict(_MEAL, has_full_recipe=False, ingredients=[], instructions=[])
    html = _focus("recipe", meal=freeform)
    assert "No saved recipe for this one — ask me for it in the chat." in html
    assert "cook-fill" not in html and 'aria-label="Steps"' not in html

    empty = dict(_MEAL, ingredients=[], instructions=[])
    html = _focus("recipe", meal=empty)
    assert "No steps saved yet." in html
    assert 'class="cook-fill" data-cook="fill" data-recipe="Sheet-pan chicken thighs">Fill in this recipe</button>' in html


@_needs_node
def test_a_reload_under_a_focused_cook_follows_the_dish_rather_than_the_index():
    """CONCERN. loadKitchen rebuilds cookState.data and re-pins tonightIdx
    but reset neither focusIdx nor stepIdx, so a chat turn arriving mid-cook
    could render "Step 3" of whatever dish now sat at that index — and hand
    the cook the finish of a dish they never started."""
    got = _run(
        "cookState.focusMealKey = 'e41';\n"
        # The reload: the same plan, a new array, our dish now second.
        "cookState.data = { weekly_plan_id: 12, meals: [OTHER, MEAL], prep_tasks: [] };\n"
        "cookFollowFocusedMeal(cookState.data.meals);\n"
        "console.log(JSON.stringify({ idx: cookState.focusIdx, screen: cookState.screen }));",
        {"data": _VIEW, "focusIdx": 0, "screen": "focus", "focusStage": "step", "stepIdx": 2},
    )
    assert got["idx"] == 1, "the focus followed the dish it was opened on"

    # ...and when the dish is genuinely gone, the screen goes to the root
    # rather than cooking a stranger.
    gone = _run(
        "cookState.focusMealKey = 'e41';\n"
        "cookState.data = { weekly_plan_id: 12, meals: [OTHER], prep_tasks: [] };\n"
        "cookFollowFocusedMeal(cookState.data.meals);\n"
        "console.log(JSON.stringify(cookState.screen));",
        {"data": _VIEW, "focusIdx": 0, "screen": "focus"},
    )
    assert gone == "overview"


@_needs_node
def test_the_step_cursor_is_clamped_where_the_stage_is_decided():
    """A shorter recipe arriving under a cursor pointing past its end used
    to leave the dock and the instruction answering about different steps."""
    short = dict(_MEAL, instructions=["Stir.", "Serve."])
    html = _focus("step", step=7, meal=short)
    assert "step 2 of 2" in html
    assert "Serve." in html
    # The last step offers the finish, and the dock agrees with the body.
    assert 'data-cook="step-next"' not in html
    assert "Done — on the table" in html


# ---------- the second review round, 2026-09-10 ----------
# The headline servings tests above turned out not to guard the blocker at
# all: they hand-mutate the meal and then render, and the OLD renderers read
# from the meal too, so they passed on the broken commit. What follows drives
# cookStepServings itself — tap, stored state, re-render — which is the path
# that was broken.


@_needs_node
def test_a_tap_on_the_stepper_is_carried_by_every_stage():
    """THE guard on the blocker. A tap, then a stage change, then another,
    then back — all reading whatever the tap actually left behind.

    On the broken commit the tap wrote to #cook-getout-N's innerHTML and
    nothing else, so the first renderCook() of the first stage change put
    the original amounts back.
    """
    got = _run(
        "(async function () {\n"
        "  cookState.focusMealKey = 'e41';\n"
        "  await cookStepServings(fakeStepper(0, 1, 'Sheet-pan chicken thighs', 2));\n"
        "  var recipe = cookFocusHtml(cookState.data, cookState.data.meals, 0);\n"
        "  cookStartCooking();\n"
        "  cookState.stepIdx = 1;\n"
        "  var step = cookFocusHtml(cookState.data, cookState.data.meals, 0);\n"
        "  cookGoStage('recipe');\n"
        "  var back = cookFocusHtml(cookState.data, cookState.data.meals, 0);\n"
        "  console.log(JSON.stringify({ asked: fetchCalls, recipe: recipe, step: step, back: back }));\n"
        "})();",
        {"data": _VIEW},
    )
    assert got["asked"] == [5], "one tap, one scale call, for base 4 + 1"
    # The stub answers with amounts proportional to the count asked for.
    # "2.5 tbsp" reads as "2½ tbsp" now (item 14, design-tidy pass
    # 2026-09-11) — cookIngredientLabel runs every qty through humanQtyText.
    for where in ("recipe", "back"):
        assert "10 Chicken thighs" in got[where], f"{where} lost the rescale"
        assert "2 ½ tbsp Olive oil" in got[where]
        assert "4 Chicken thighs" not in got[where]
    # ...and the cooker's "needs" line is the new amounts too.
    assert "2 ½ tbsp Olive oil" in got["step"]
    # The count in the stepper agrees with the amounts under it.
    assert ">5<" in got["recipe"] and ">5<" in got["back"]


@_needs_node
def test_three_fast_taps_count_three_times():
    """Three + in one frame gave ONE increment: `current` was read off
    meal.default_servings, which only moves when a reply lands, so all three
    taps counted from the same number and fired three identical requests."""
    got = _run(
        "(async function () {\n"
        "  var p = [];\n"
        "  for (var i = 0; i < 3; i++) p.push(cookStepServings(fakeStepper(0, 1, 'X', 4)));\n"
        "  await Promise.all(p);\n"
        "  console.log(JSON.stringify({ asked: fetchCalls,\n"
        "    serves: cookServesShown(cookState.data.meals[0]),\n"
        "    ings: cookState.data.meals[0].ingredients }));\n"
        "})();",
        {"data": _VIEW},
    )
    assert got["asked"] == [5, 6, 7], "each tap counts from the one before it"
    assert got["serves"] == 7
    assert got["ings"][0]["qty"] == "14", "the amounts are the last count's, not a stale one"


@_needs_node
def test_a_superseded_scale_reply_loses_however_late_it_arrives():
    """A fast + then - fired requests for 6 and 4 with no sequencing, so the
    survivor was whichever reply landed last. The first reply is made to
    arrive LAST here on purpose."""
    got = _run(
        "(async function () {\n"
        "  latency[6] = 40; latency[4] = 0;\n"   # the superseded one comes back late
        "  var a = cookStepServings(fakeStepper(0, 1, 'X', 5));\n"
        "  var b = cookStepServings(fakeStepper(0, -1, 'X', 5));\n"
        "  await Promise.all([a, b]);\n"
        "  await new Promise(function (r) { setTimeout(r, 80); });\n"
        "  console.log(JSON.stringify({ asked: fetchCalls,\n"
        "    serves: cookServesShown(cookState.data.meals[0]),\n"
        "    ings: cookState.data.meals[0].ingredients }));\n"
        "})();",
        {"data": _VIEW},
    )
    assert got["asked"] == [5, 4]
    assert got["serves"] == 4, "the last tap wins, not the last reply"
    assert got["ings"][0]["qty"] == "8"


@_needs_node
def test_a_tab_switch_does_not_quietly_undo_the_cooks_own_count():
    """One tap on Meals and one back on Kitchen ran loadKitchen, which
    refetches — and the screen came back at the household's 3 while the cook
    was holding the pan. The choice is kept for the page's life and put back
    over whatever a load hands in."""
    got = _run(
        "(async function () {\n"
        "  cookState.focusMealKey = 'e41';\n"
        "  await cookStepServings(fakeStepper(0, 1, 'X', 4));\n"
        # loadKitchen: a whole new payload, the server's own servings on it.
        "  cookState.data = { weekly_plan_id: 12, meals: [JSON.parse(JSON.stringify(MEAL))], prep_tasks: [] };\n"
        "  cookApplyServesOverride(cookState.data.meals);\n"
        "  var m = cookState.data.meals[0];\n"
        "  console.log(JSON.stringify({ serves: cookServesShown(m), ings: m.ingredients,\n"
        "                               flagged: !!m.serves_overridden }));\n"
        "})();",
        {"data": _VIEW},
    )
    assert got["serves"] == 5, "a refetch does not put the household's number back"
    assert got["ings"][0]["qty"] == "10"
    assert got["flagged"] is True


def test_the_override_is_re_applied_before_anything_is_drawn():
    """It has to run inside renderCook, not at the one call site that
    happened to need it — a load, a write response and a tab switch all
    arrive by different doors."""
    render = _extract("renderCook")
    assert "cookApplyServesOverride(meals)" in render
    assert render.index("cookApplyServesOverride(meals)") < render.index("cookFocusHtml(")
    # ...and the sibling ordering check the reviewer asked for.
    assert "cookFollowFocusedMeal(meals)" in render
    assert render.index("cookApplyServesOverride(meals)") < render.index("cookFollowFocusedMeal(meals)")


@_needs_node
def test_a_rescaled_batch_shows_one_number_and_it_is_the_cooks():
    """+1 on a cook-ahead source used to give "Serves 7" over a hero chip
    still reading "for 6" and a note still reading "Cooking for 6 — enough
    for Thursday and Friday". Since 2026-09-18 the count is the one place
    the number lives, and it is the cook's; the batch sentence is the
    plan's and never drawn here. The card's own `servings` still follows
    the cook (cookApplyServesOverride), for whatever reads it."""
    batch = dict(
        _MEAL,
        servings=6,
        default_servings=6,
        covers_note="Cooking for 6 — enough for Friday and Saturday.",
        covers=[{"date": "2026-09-11", "slot": "breakfast"},
                {"date": "2026-09-12", "slot": "breakfast"}],
    )
    view = dict(_VIEW, meals=[batch])
    got = _run(
        "(async function () {\n"
        "  await cookStepServings(fakeStepper(0, 1, 'X', 4));\n"
        "  var up = cookFocusHtml(cookState.data, cookState.data.meals, 0);\n"
        "  await cookStepServings(fakeStepper(0, -1, 'X', 4));\n"
        "  await cookStepServings(fakeStepper(0, -1, 'X', 4));\n"
        "  await cookStepServings(fakeStepper(0, -1, 'X', 4));\n"
        "  var down = cookFocusHtml(cookState.data, cookState.data.meals, 0);\n"
        "  console.log(JSON.stringify({ up: up, down: down }));\n"
        "})();",
        {"data": view},
    )
    # Nothing on the screen still says six.
    assert "for 6" not in got["up"]
    assert "Cooking for 6" not in got["up"]
    assert '<span class="cook-serves-count">7</span>' in got["up"], "the count is the number actually being cooked"
    assert got["up"].count("cook-serves-count") == 1
    # No batch sentence either way, and no caution: the plan holds the batch.
    for html in (got["up"], got["down"]):
        assert "This batch" not in html and "check it still stretches" not in html and "enough for" not in html
    assert '<span class="cook-serves-count">4</span>' in got["down"]


def test_the_dock_foot_survives_the_desktop_breakpoint():
    """`padding: 16px 28px 0` in a >=1100px override used to zero the
    bottom, so the dock's foot was switched off at exactly the width nobody
    had measured — --cook-dock-h still 123px, dock still sticky, padding 0.

    That override (part of a wider Kitchen desktop treatment — bigger
    gutters and type past 1100px) is gone entirely now: Emily's "phone in
    the room" decision, 2026-09-11, keeps Kitchen's phone gutter at every
    width, so there is no second .cook-body rule left to reintroduce the
    shorthand bug. This still pins the one rule that's left naming the
    dock's own height, so a future override can't drop it again."""
    rules, at = [], 0
    while True:
        try:
            at = SHELL_CSS.index(".cook-body {", at)
        except ValueError:
            break
        rules.append(SHELL_CSS[at : SHELL_CSS.index("}", at)])
        at += 1
    assert len(rules) == 1, (
        "expected exactly one .cook-body rule (no separate desktop "
        "treatment) — if a new breakpoint override was added, it must not "
        "zero the bottom padding with the shorthand (see this test's "
        "docstring)."
    )
    assert "var(--cook-dock-h" in rules[0], f"the .cook-body rule drops the dock's foot:\n{rules[0]}"


# ---------- The serving count is part of the cooking session ----------
# Emily, 2026-09-10: save it in the same place as the ticks, with the same
# lifetime. The reason is not tidiness — the two describe one cooking
# session, so a reload that kept the ticks and dropped the count left a
# HALF-TICKED ingredient list at amounts nobody chose: you ticked "4 chicken
# thighs", came back, and the row said 2 and was still ticked. That is not
# an inconsistency, it is a screen that is wrong.
#
# These run the real functions against the real-ish localStorage the harness
# already has, and every one of them drives a rescale and then throws the
# page away — which is the only way to see the bug at all.

_RELOAD = (
    # Everything the page holds in memory is gone; the store is not.
    "  cookState.ticks = null; cookState.ticksFor = null; cookState.serves = {};\n"
    "  cookState.data = { weekly_plan_id: 12, meals: [JSON.parse(JSON.stringify(MEAL))],"
    " prep_tasks: [] };\n"
)


def test_the_serving_count_survives_a_reload_like_the_ticks_do():
    got = _run(
        "(async function () {\n"
        "  await cookStepServings(fakeStepper(0, 2, 'X', 4));\n"
        + _RELOAD +
        "  cookApplyServesOverride(cookState.data.meals);\n"
        "  var m = cookState.data.meals[0];\n"
        "  console.log(JSON.stringify({ serves: cookServesShown(m), qty: m.ingredients[0].qty }));\n"
        "})();",
        {"data": _VIEW},
    )
    assert got["serves"] == 6
    assert got["qty"] == "12"


def test_the_amounts_that_come_back_are_the_ones_the_ticks_were_put_against():
    """The bug, said as one assertion. Tick an ingredient at the count you
    chose, reload, and the row must still say what it said when you ticked
    it — a tick and an amount that disagree is the screen being wrong, not
    merely untidy."""
    got = _run(
        "(async function () {\n"
        "  await cookStepServings(fakeStepper(0, 2, 'X', 4));\n"
        "  cookSetTick('ings', cookMealKey(cookState.data.meals[0]) + ':chicken thighs', true);\n"
        + _RELOAD +
        "  cookApplyServesOverride(cookState.data.meals);\n"
        "  var m = cookState.data.meals[0];\n"
        "  console.log(JSON.stringify({ qty: m.ingredients[0].qty,\n"
        "    ticked: cookTicked('ings', cookMealKey(m) + ':chicken thighs') }));\n"
        "})();",
        {"data": _VIEW},
    )
    assert got["ticked"] is True, "the tick still has to survive"
    assert got["qty"] == "12", "and the amount it was put against has to survive with it"


def test_it_lives_in_the_tick_record_rather_than_a_second_store_beside_it():
    """One record, one key, one expiry. A second key would be a second
    thing to prune and a second thing to get out of step with the ticks."""
    got = _run(
        "(async function () {\n"
        "  await cookStepServings(fakeStepper(0, 1, 'X', 4));\n"
        "  console.log(JSON.stringify({ keys: Object.keys(_ls), prefix: COOK_TICKS_PREFIX,\n"
        "    record: JSON.parse(_ls[COOK_TICKS_PREFIX + 12]) }));\n"
        "})();",
        {"data": _VIEW},
    )
    assert got["keys"] == [got["prefix"] + "12"], got["keys"]
    assert sorted(got["record"].keys()) == ["ings", "serves", "steps"]
    assert got["record"]["serves"]["e41"]["servings"] == 5


def test_last_weeks_serving_count_expires_with_last_weeks_ticks():
    """NO-REGRESSION GUARD — green before this change too, since nothing
    was stored at all then. What it pins is that storing it did not buy a
    leak across weeks. Same lifetime means the same expiry: a new week is a new plan id, so
    the count goes when the ticks go, by construction rather than by a
    sweep somebody has to remember."""
    got = _run(
        "(async function () {\n"
        "  await cookStepServings(fakeStepper(0, 2, 'X', 4));\n"
        # Next week: a different plan, the same dish, a fresh page.
        "  cookState.ticks = null; cookState.ticksFor = null; cookState.serves = {};\n"
        "  cookState.data = { weekly_plan_id: 77, meals: [JSON.parse(JSON.stringify(MEAL))],"
        " prep_tasks: [] };\n"
        "  cookApplyServesOverride(cookState.data.meals);\n"
        "  var m = cookState.data.meals[0];\n"
        "  console.log(JSON.stringify({ serves: cookServesShown(m), qty: m.ingredients[0].qty,\n"
        "    flagged: !!m.serves_overridden }));\n"
        "})();",
        {"data": _VIEW},
    )
    assert got["serves"] == 4, "the household's own number is what a new week starts on"
    assert got["qty"] == "4"
    assert got["flagged"] is False


def test_a_stored_count_with_no_amounts_behind_it_is_dropped():
    """Green on both sides for the same reason as the one above — before
    the change nothing read the record at all. It guards the validator.
    It is read back into the amounts a person cooks from, so a
    half-written or hand-edited record is dropped rather than rendered —
    a servings number over amounts that never moved is the same wrong
    screen from the other direction."""
    got = _run(
        "(function () {\n"
        "  _ls[COOK_TICKS_PREFIX + 12] = JSON.stringify({ steps: {}, ings: { 'e41:x': 1 },\n"
        "    serves: { e41: { servings: 9 } } });\n"
        "  cookApplyServesOverride(cookState.data.meals);\n"
        "  var m = cookState.data.meals[0];\n"
        "  console.log(JSON.stringify({ serves: cookServesShown(m), qty: m.ingredients[0].qty,\n"
        "    tick: cookTicked('ings', 'e41:x') }));\n"
        "})();",
        {"data": _VIEW},
    )
    assert got["serves"] == 4
    assert got["qty"] == "4"
    assert got["tick"] is True, "and the ticks in the same record are still read"


def test_restoring_it_leaves_the_steps_and_the_cursor_alone():
    """Also green on both sides — the acceptance criterion said restoring
    must not renumber steps or clear ticks, so it is pinned rather than
    assumed. The override touches amounts and nothing else. Steps are the
    recipe's own and ticks are filed under an ingredient's NAME, which a
    rescale never changes."""
    got = _run(
        "(async function () {\n"
        "  await cookStepServings(fakeStepper(0, 2, 'X', 4));\n"
        "  cookSetTick('steps', 'e41:1', true);\n"
        + _RELOAD +
        "  cookState.focusStage = 'step'; cookState.stepIdx = 1;\n"
        "  cookApplyServesOverride(cookState.data.meals);\n"
        "  var m = cookState.data.meals[0];\n"
        "  console.log(JSON.stringify({ steps: m.instructions.length,\n"
        "    cursor: cookState.stepIdx, ticked: cookTicked('steps', 'e41:1') }));\n"
        "})();",
        {"data": _VIEW},
    )
    assert got["steps"] == 3
    assert got["cursor"] == 1
    assert got["ticked"] is True


def test_the_amounts_are_stored_absolute_so_they_cannot_scale_twice():
    """
    What keeps this from composing twice with the batch and attendance
    scaling get_cooker_view already does: the stored list is the ABSOLUTE
    one /api/recipes/scale handed back, so re-applying it REPLACES the
    server's amounts rather than multiplying them. Rendering four times
    over is still four, not sixteen.
    """
    got = _run(
        "(async function () {\n"
        "  await cookStepServings(fakeStepper(0, 2, 'X', 4));\n"
        + _RELOAD +
        "  cookApplyServesOverride(cookState.data.meals);\n"
        "  cookApplyServesOverride(cookState.data.meals);\n"
        "  cookApplyServesOverride(cookState.data.meals);\n"
        "  var m = cookState.data.meals[0];\n"
        "  console.log(JSON.stringify({ qty: m.ingredients[0].qty,\n"
        "    serves: cookServesShown(m), calls: fetchCalls }));\n"
        "})();",
        {"data": _VIEW},
    )
    assert got["qty"] == "12"
    assert got["serves"] == 6
    assert got["calls"] == [6], "and putting it back asks the server for nothing"


def test_nothing_about_the_count_is_sent_to_the_household():
    """Still per device. A cook overriding tonight at the counter is not a
    change to who lives in the house, and surviving a reload does not make
    it one — the only request in the whole path is the one that scales the
    recipe."""
    fn = _extract("cookStepServings")
    assert fn.count("fetch(") == 1
    assert "/api/recipes/scale" in fn
    # cookWriteTicks is the only writer, and it writes to localStorage.
    assert "cookWriteTicks()" in fn
    assert "window.localStorage.setItem" in _extract("cookWriteTicks")
