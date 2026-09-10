"""
Cooking is three stages now: before you start, one step at a time, and the
whole method one tap away.

Emily's approved design, 2026-09-09 ("Cooking: before you start, one step at
a time, and a proper finish"), first slice. Cook mode used to be one long
screen — hero, prep, the whole recipe, scroll — and it is now the three
things a person actually does in order, all stages of the SAME step of the
Kitchen tab (never routes, never a page with its own back button):

    'prep'   Before you start — everything out of the cupboard as a
             ticklist with quantities, plus the pans you'll want.
    'step'   One step at a time, big type. The default once you start.
    'method' The whole method on one screen, tick as you go.

Deliberately NOT in this slice, and so deliberately not covered here: the
running timer a step can offer, and the Done/arrival moment (the rating
writing to the taste record). Finishing already worked before this branch,
through "Mark it cooked", and the tests that guard it live in
tests/test_kitchen_and_preferences.py where they always did.

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
import subprocess
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
function cookAheadHtml(){ return ''; }
function cookPrepCutHtml(){ return ''; }
function cookReheatFocusHtml(m){ return '<div class="cook-reheat"></div>'; }
function renderCook(){ renderCount += 1; }
var renderCount = 0;
function showToast(){ }
function dayName(d, o){ return { '2026-09-11': 'Friday', '2026-09-12': 'Saturday' }[d] || 'Thursday'; }

// Just enough DOM for the stepper: it reads the tapped button's own
// attributes, finds the .cook-serves wrapper it sits in, and writes the
// count into a span. Everything else it does is state.
function fakeStepper(idx, delta, recipe, base) {
  var wrap = { getAttribute: function (a) {
    return a === 'data-recipe' ? recipe : (a === 'data-base' ? String(base) : null); } };
  return { getAttribute: function (a) {
             return a === 'data-idx' ? String(idx) : (a === 'data-delta' ? String(delta) : null); },
           closest: function () { return wrap; } };
}
var _counts = {};
var document = { getElementById: function (id) {
  if (!_counts[id]) _counts[id] = { textContent: '' };
  return _counts[id];
} };

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
    "cookWriteTicks",
    "cookTicked",
    "cookToggleTick",
    "cookSetTick",
    "cookKitMentions",
    "cookOvenLine",
    "cookUnscaledHtml",
    "cookKitFor",
    "cookIngredientLabel",
    "cookIngredientNouns",
    "cookStepNeeds",
    "cookStepLi",
    "cookInstructionsHtml",
    "cookDetailHtml",
    "cookFocusEndHtml",
    "cookFocusPrepTasks",
    "cookFocusPrepHtml",
    "cookAttendanceChip",
    "cookServesShown",
    "cookApplyServesOverride",
    "cookStepServings",
    "cookBatchNote",
    "cookFocusMeal",
    "cookFollowFocusedMeal",
    "cookFirstUndoneStep",
    "cookGoStage",
    "cookStartCooking",
    "cookStepForward",
    "cookStepBack",
    "cookFocusHeroHtml",
    "cookIngTickId",
    "cookGetOutRowHtml",
    "cookGetOutHtml",
    "cookKitHtml",
    "cookPrepStageHtml",
    "cookStepStageHtml",
    "cookMethodStageHtml",
    "cookDockHtml",
    "cookDockLink",
    "cookDockCookedHtml",
    "cookFocusDockHtml",
    "cookFocusHtml",
]


def _run(body: str, state: dict | None = None) -> object:
    """Run `body` (which must console.log one JSON value) with the cook
    screen's real functions in scope."""
    base = {
        "data": None,
        "meals": [],
        "focusIdx": 0,
        "focusStage": "prep",
        "serves": {},
        "servesSeq": 0,
        "focusMealKey": None,
        "stepIdx": 0,
        "methodFrom": "prep",
        "ticks": None,
        "ticksFor": None,
        "focusScrollTo": None,
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
        + _regex_const("COOK_OVEN_RE")
        + "\n"
        + _var_block("COOK_KIT_WORDS")
        + "\n"
        + "\n".join(_extract(n) for n in _FUNCTIONS)
        + "\n"
        + body
    )
    res = subprocess.run(
        ["node", "-e", harness], capture_output=True, text=True, timeout=30
    )
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


def _focus(stage: str = "prep", step: int = 0, meal: dict | None = None, **extra) -> str:
    m = meal or _MEAL
    view = dict(_VIEW, meals=[m])
    state = {"data": view, "focusStage": stage, "stepIdx": step}
    state.update(extra)
    return _run(
        "console.log(JSON.stringify(cookFocusHtml(cookState.data, cookState.data.meals, 0)));",
        state,
    )


# ---------- "Cook this opens Before you start" ----------


@_needs_node
def test_cook_this_opens_before_you_start():
    """Every entry point lands on the same first stage, and it says so."""
    html = _focus("prep")
    assert "Before you start" in html
    # The ticklist, with the amount that actually goes in the pan.
    assert "Everything out" in html
    assert "4 Chicken thighs" in html
    assert "2 tbsp Olive oil" in html
    assert 'data-cook="check-ing"' in html


@_needs_node
def test_every_way_into_cook_mode_resets_to_the_first_stage():
    """cookEnterFocus is the one door, so the reset belongs there rather
    than at each of the five call sites that use it."""
    enter = _extract("cookEnterFocus")
    assert "cookState.focusStage = 'prep';" in enter
    assert "cookState.stepIdx = 0;" in enter


@_needs_node
def test_before_you_start_names_the_pans_the_steps_ask_for():
    html = _focus("prep")
    assert "Baking sheet" in html
    assert "Parchment paper" in html
    # ...and the one before-you-start fact that costs twenty minutes when
    # it is missed, read out of the step that says it.
    assert "Oven at 425°F" in html


@_needs_node
def test_a_recipe_whose_steps_name_no_equipment_gets_no_kit_section():
    """An empty answer is a missing section, never an empty one."""
    plain = dict(_MEAL, instructions=["Stir it all together.", "Serve."])
    html = _focus("prep", meal=plain)
    assert "Pans and kit" not in html
    assert "Everything out" in html, "the ticklist is still there"


@_needs_node
def test_the_oven_line_needs_a_heating_verb_and_a_real_temperature():
    """"Take it out of the oven after 25 minutes" is not an oven at 25
    degrees, and without the guard it read as one."""
    got = _run(
        "console.log(JSON.stringify({\n"
        "  preheat: cookOvenLine(['Preheat the oven to 400.']),\n"
        "  celsius: cookOvenLine(['Heat the oven to 200C.']),\n"
        "  removing: cookOvenLine(['Take it out of the oven after 25 minutes.']),\n"
        "  none: cookOvenLine(['Warm a skillet over medium heat.'])\n"
        "}));"
    )
    assert got["preheat"] == "Oven at 400°"
    assert got["celsius"] == "Oven at 200°C", "a unit the step DID write is kept"
    assert got["removing"] == ""
    assert got["none"] == ""


@_needs_node
def test_grilled_is_not_a_reason_to_get_the_grill_out():
    """Whole words only — a bare substring match said it was."""
    got = _run(
        "console.log(JSON.stringify({\n"
        "  grilled: cookKitFor({ instructions: ['Serve with grilled halloumi.'] }),\n"
        "  grill: cookKitFor({ instructions: ['Put it on the grill for 6 minutes.'] })\n"
        "}));"
    )
    assert got["grilled"] == []
    assert got["grill"] == ["Grill"]


# ---------- "starting from there enters one-step-at-a-time" ----------


@_needs_node
def test_starting_enters_one_step_at_a_time():
    html = _focus("prep")
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
    assert "Toss the potatoes" in html
    assert "Preheat the oven" not in html
    assert "Roast for 35 minutes" not in html
    assert "Step 2 of 3" in html


@_needs_node
def test_a_step_names_what_that_step_needs_from_its_own_words():
    html = _focus("step", step=1)
    assert "For this step" in html
    assert "1 lb Baby potatoes, halved" in html
    assert "2 tbsp Olive oil" in html
    assert "1 tsp Smoked paprika" in html
    # Nothing the step doesn't mention.
    assert "4 Chicken thighs" not in html


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
def test_back_from_step_one_is_back_to_before_you_start():
    got = _run(
        "cookStepBack();\n"
        "console.log(JSON.stringify(cookState.focusStage));",
        {"data": _VIEW, "focusStage": "step", "stepIdx": 0},
    )
    assert got == "prep"


@_needs_node
def test_the_last_step_offers_the_finish_rather_than_a_next_into_nothing():
    """Finishing is the existing "Mark it cooked" write, untouched by this
    slice — the Done/arrival moment is the second night's work."""
    html = _focus("step", step=2)
    assert 'data-cook="step-next"' not in html
    assert 'data-cook="focus-check"' in html
    assert "Mark it cooked" in html


@_needs_node
def test_a_dish_with_no_steps_is_never_offered_a_step_through():
    no_steps = dict(_MEAL, instructions=[])
    html = _focus("prep", meal=no_steps)
    assert 'data-cook="start-cooking"' not in html
    assert "Mark it cooked" in html
    # ...and the whole method, which is where "Fill in this recipe" lives.
    assert 'data-stage="method"' in html


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


# ---------- "the whole method is one tap from either, and back keeps your place" ----------


@_needs_node
def test_the_whole_method_is_one_tap_from_both_of_the_others():
    assert 'data-cook="stage" data-stage="method"' in _focus("prep")
    assert 'data-cook="stage" data-stage="method"' in _focus("step", step=1)


@_needs_node
def test_the_whole_method_is_the_recipe_panel_this_screen_always_had():
    html = _focus("method")
    assert "The whole method" in html
    for step in ("Preheat the oven", "Toss the potatoes", "Roast for 35 minutes"):
        assert step in html
    assert 'data-cook="check-step"' in html, "every step is tickable there"


@_needs_node
def test_switching_back_from_the_whole_method_keeps_your_place():
    got = _run(
        "cookState.focusStage = 'step'; cookState.stepIdx = 2;\n"
        "cookGoStage('method');\n"
        "var dock = cookFocusDockHtml(cookState.data.meals[0]);\n"
        "console.log(JSON.stringify({\n"
        "  from: cookState.methodFrom, step: cookState.stepIdx, dock: dock\n"
        "}));",
        {"data": _VIEW},
    )
    assert got["from"] == "step"
    assert got["step"] == 2, "opening the method never moves the step cursor"
    assert "Back to step 3" in got["dock"]
    assert 'data-stage="step"' in got["dock"]


@_needs_node
def test_the_whole_method_opened_from_before_you_start_goes_back_there():
    got = _run(
        "cookGoStage('method');\n"
        "console.log(JSON.stringify(cookFocusDockHtml(cookState.data.meals[0])));",
        {"data": _VIEW, "focusStage": "prep"},
    )
    assert "Before you start" in got
    assert 'data-stage="prep"' in got


@_needs_node
def test_opening_the_whole_method_from_a_step_lands_on_that_step():
    """Landing back at step one would be losing your place in the other
    direction. The <li> is found by its position, not by its ordinal — the
    Do ahead / Day of split means the seventh <li> is not always step 7."""
    got = _run(
        "cookState.focusStage = 'step'; cookState.stepIdx = 2;\n"
        "cookGoStage('method');\n"
        "console.log(JSON.stringify(cookState.focusScrollTo));",
        {"data": _VIEW},
    )
    assert got == "step:2"
    wired = _extract("wireCookFocusScroll")
    assert "cook-step-check[data-step=" in wired


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
        "console.log(JSON.stringify(html.indexOf('Everything out') !== -1));",
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
def test_cook_keeps_no_primary_action_on_its_root():
    """DESIGN_SYSTEM Rule 5. The dock is a step of the tab, one level down,
    exactly where "Mark it cooked" already lived — so the apricot appears
    only inside the focused screen's own renderers."""
    for name in ("renderKitchen", "kitchenTilesHtml", "kitchenCookingTodayHtml"):
        body = _extract(name)
        assert "cook-hero-action" not in body, f"{name} put a primary on the root"
        assert "cook-dock" not in body, f"{name} put the dock on the root"
    assert "cookFocusDockHtml(meal)" in _extract("cookFocusHtml")
    assert "cook-dock" in _extract("cookDockHtml")


@_needs_node
def test_each_cooking_stage_has_exactly_one_apricot():
    """Rule 5 again, one level down: a screen gets one primary fill, and the
    whole method gets none because it already ends on cookFocusEndHtml's
    "Mark it cooked" under the last step."""
    counts = {
        stage: _focus(stage, step=1).count("cook-hero-action")
        for stage in ("prep", "step", "method")
    }
    assert counts["prep"] == 1
    assert counts["step"] == 1
    assert counts["method"] == 0


@_needs_node
def test_the_cooking_stages_carry_no_italic_accent_line():
    """DESIGN_SYSTEM §3, amended 2026-09-09: at most one per screen and most
    screens should have none. The one real fact about the cook belongs on
    Before you start; repeating it over every step would be the quota this
    rule change removed."""
    assert "cook-hero-note" in _focus("prep")
    assert "cook-hero-note" not in _focus("step", step=1)
    assert "cook-hero-note" not in _focus("method")


@_needs_node
def test_the_step_screens_do_not_restate_the_dish():
    """§8: a subtitle that says in a sentence what the content below says
    anyway. The slim hero names the dish and the stage, and stops."""
    html = _focus("step", step=1)
    assert "Sheet-pan chicken thighs" in html
    assert html.count("Sheet-pan chicken thighs") == 1


@_needs_node
def test_a_reheat_night_still_has_no_cook_screen():
    """The app's existing rule — there is no cook here, so there is nothing
    for a cook journey to hold. Untouched by this slice."""
    reheat = dict(_MEAL, is_leftovers=True)
    html = _focus("prep", meal=reheat)
    assert "cook-reheat" in html
    assert "Everything out" not in html


@_needs_node
def test_a_stage_with_nothing_behind_it_falls_back_rather_than_rendering_a_hole():
    no_steps = dict(_MEAL, instructions=[])
    html = _focus("step", step=4, meal=no_steps)
    assert "Before you start" in html
    assert "cook-bigstep" not in html


@_needs_node
def test_the_read_only_recipe_on_meals_still_writes_nothing():
    """The parent branch put cook mode's own renderer in Meals' Meal step as
    a reading copy. Re-shaping the cook screen must not have given that
    frame a working control — one renderer in two frames only works while
    the plain one stays plain."""
    got = _run(
        "console.log(JSON.stringify({\n"
        "  plain: cookDetailHtml(cookState.data.meals[0], 'meal', false, true),\n"
        "  full: cookDetailHtml(cookState.data.meals[0], 0, false)\n"
        "}));",
        {"data": _VIEW},
    )
    assert "Roast for 35 minutes" in got["plain"], "it still shows the recipe"
    assert "data-cook=" not in got["plain"], "and still writes nothing"
    for marker in ("cook-step-check", "cook-serves", "cook-fill", "cook-focus-end"):
        assert marker not in got["plain"]
    # The checkable copy is the one that has them.
    assert 'data-cook="check-step"' in got["full"]


@_needs_node
def test_the_read_only_frame_never_reads_the_tick_store():
    """A reading copy that showed one cook's ticks would be showing state
    from a screen it cannot write to."""
    got = _run(
        "cookToggleTick('steps', 'e41:0');\n"
        "console.log(JSON.stringify(cookDetailHtml(cookState.data.meals[0], 'meal', false, true)));",
        {"data": _VIEW},
    )
    assert "is-done" not in got
    assert "cook-box" not in got


# ---------- the shape of it, in CSS ----------


def test_the_get_out_row_clears_the_44px_floor():
    """Rule 6. The whole row is the control on this screen, not just the box
    beside it — it is read at arm's length with wet hands."""
    block = SHELL_CSS[SHELL_CSS.index(".cook-getout-row {") :][:400]
    assert "min-height: 48px" in block
    assert "width: 100%" in block


def test_the_dock_is_sticky_and_uses_tokens_only():
    block = SHELL_CSS[SHELL_CSS.index(".cook-dock {") :][:600]
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
    start = SHELL_CSS.index("/* ---------- Cook mode's three stages ----------")
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
        "cookGoStage('method');\n"
        "var method = cookFocusHtml(cookState.data, cookState.data.meals, 0);\n"
        "cookGoStage('prep');\n"
        "var back = cookFocusHtml(cookState.data, cookState.data.meals, 0);\n"
        "console.log(JSON.stringify({ step2: step2, method: method, back: back }));",
        {"data": _VIEW},
    )
    # What the step needs is the rescaled amount, not the recipe's own.
    assert "4 tbsp Olive oil" in got["step2"]
    assert "2 lb Baby potatoes, halved" in got["step2"]
    assert "2 tbsp Olive oil" not in got["step2"]
    # The whole method's stepper and list agree with it.
    assert ">8<" in got["method"], "the serving count follows the rescale"
    assert "8 Chicken thighs" in got["method"]
    # ...and stepping back to Before you start does not undo it.
    assert "8 Chicken thighs" in got["back"]
    assert "4 Chicken thighs" not in got["back"]


@_needs_node
def test_the_out_count_is_counted_by_the_renderer_not_patched_in_beside_it():
    """Same caveat as the test above: this hand-mutates rather than tapping,
    so it is a statement about the renderer, not a guard on the blocker."""
    got = _run(
        "var meal = cookState.data.meals[0];\n"
        "cookState.focusMealKey = cookMealKey(meal);\n"
        "cookToggleTick('ings', cookMealKey(meal) + ':olive oil');\n"
        "meal.ingredients = [{ qty: '4 tbsp', item: 'Olive oil' }];\n"
        "meal.default_servings = 8;\n"
        "console.log(JSON.stringify(cookGetOutHtml(meal, 0)));",
        {"data": _VIEW},
    )
    assert "1 of 1 out" in got or "All out." in got
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
def test_the_oven_line_is_told_a_temperature_rather_than_finding_a_number():
    """CONCERN. The first rule was "oven" + a heating word + any 3-digit
    number, and "set aside" is a heating word — so a meat-probe target, a
    braise time and a resting time all came back as oven temperatures, said
    first in the list with no hedge. The number now has to follow "oven to"
    directly, which every one of those fails, because in each of them the
    number belongs to something else."""
    got = _run(
        "console.log(JSON.stringify({\n"
        "  probe: cookOvenLine(['Return to the oven and roast until a probe reads 145°F.']),\n"
        "  braise: cookOvenLine(['Heat the oven, cover, and braise for 180 minutes.']),\n"
        "  resting: cookOvenLine(['Take it out of the oven and set aside for 100 minutes.']),\n"
        "  slow: cookOvenLine(['Preheat the oven to 90C for a slow roast.']),\n"
        "  gasmark: cookOvenLine(['Heat the oven to gas mark 6.']),\n"
        "  plain: cookOvenLine(['Preheat oven to 180.']),\n"
        "  spelled: cookOvenLine(['Set the oven to about 350 degrees F.'])\n"
        "}));"
    )
    assert got["probe"] == ""
    assert got["braise"] == ""
    assert got["resting"] == ""
    # ...and the real one under 100 that the three-digit rule made impossible.
    assert got["slow"] == "Oven at 90°C"
    assert got["plain"] == "Oven at 180°", "no unit is printed that the step didn't write"
    assert got["spelled"] == "Oven at 350°F"
    # A quiet miss is this section's stated failure mode; a wrong number is not.
    assert got["gasmark"] == ""


@_needs_node
def test_a_step_that_says_not_to_use_a_pan_does_not_ask_for_one():
    got = _run(
        "console.log(JSON.stringify({\n"
        "  refused: cookKitFor({ instructions: ['No skillet needed - use the baking sheet you already have.'] }),\n"
        "  wanted: cookKitFor({ instructions: ['Sear in a skillet, then finish on a baking sheet.'] })\n"
        "}));"
    )
    assert got["refused"] == ["Baking sheet"]
    assert got["wanted"] == ["Baking sheet", "Skillet"]


@_needs_node
def test_a_freeform_meal_is_not_pointed_at_a_fill_button_that_does_not_exist():
    """CONCERN. cookDetailHtml returns early on !has_full_recipe, so the
    whole method offers a freeform meal no fill control at all — and the
    fallback was telling the cook to go there and use one."""
    freeform = dict(_MEAL, has_full_recipe=False, ingredients=[], instructions=[])
    prep = _focus("prep", meal=freeform)
    method = _focus("method", meal=freeform)
    assert "the whole method has a way" not in prep.lower()
    assert "Ask me for the recipe" in prep
    assert "cook-fill" not in method, "the method really has no fill control here"

    # A SAVED recipe with nothing in it does have one, and is told so.
    empty = dict(_MEAL, ingredients=[], instructions=[])
    prep2 = _focus("prep", meal=empty)
    assert "The whole method has a way to fill the recipe in." in prep2
    assert "cook-fill" in _focus("method", meal=empty)


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
    assert "Step 2 of 2" in html
    assert "Serve." in html
    # The last step offers the finish, and the dock agrees with the body.
    assert 'data-cook="step-next"' not in html
    assert "Mark it cooked" in html


def test_the_whole_methods_finish_is_that_screens_one_apricot():
    """NIT from review: making the method's dock carry no primary left the
    skim-ahead cook's finish as the quietest control on the screen. The
    button at the end of the last step is that stage's one apricot now —
    there is still exactly one finish control on it, and still no second
    accent."""
    block = SHELL_CSS[SHELL_CSS.index(".cook-focus-end-done {") :]
    body = block[: block.index("}")]
    assert "background: var(--apricot);" in body
    assert "color: var(--on-accent-ink);" in body, "Rule 1"
    assert "width: 100%" in body


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
        "  var prep = cookFocusHtml(cookState.data, cookState.data.meals, 0);\n"
        "  cookStartCooking();\n"
        "  cookState.stepIdx = 1;\n"
        "  var step = cookFocusHtml(cookState.data, cookState.data.meals, 0);\n"
        "  cookGoStage('method');\n"
        "  var method = cookFocusHtml(cookState.data, cookState.data.meals, 0);\n"
        "  cookGoStage('prep');\n"
        "  var back = cookFocusHtml(cookState.data, cookState.data.meals, 0);\n"
        "  console.log(JSON.stringify({ asked: fetchCalls, prep: prep, step: step,\n"
        "                               method: method, back: back }));\n"
        "})();",
        {"data": _VIEW},
    )
    assert got["asked"] == [5], "one tap, one scale call, for base 4 + 1"
    # The stub answers with amounts proportional to the count asked for.
    for where in ("prep", "method", "back"):
        assert "10 Chicken thighs" in got[where], f"{where} lost the rescale"
        assert "2.5 tbsp Olive oil" in got[where]
        assert "4 Chicken thighs" not in got[where]
    # ...and the step screen's "for this step" chips are the new amounts too.
    assert "2.5 tbsp Olive oil" in got["step"]
    # The count in the stepper agrees with the amounts under it.
    assert ">5<" in got["prep"] and ">5<" in got["method"]


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
def test_a_rescaled_batch_stops_claiming_a_count_it_no_longer_cooks():
    """+1 on a cook-ahead source gave "Serves 7" over a hero chip still
    reading "for 6" and a note still reading "Cooking for 6 — enough for
    Thursday and Friday". The chip is the number being cooked, so it follows
    the cook; the note names a count the server worked out, so it goes, and
    the NIGHTS are said again from meal.covers instead."""
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
    assert "for 7" in got["up"], "the chip is the number actually being cooked"
    # The nights survive, rebuilt from the dates rather than the sentence.
    assert "This batch is also meant for Friday and Saturday." in got["up"]
    # Below what it was sized for, the caution is added — and only there.
    assert "check it still stretches" not in got["up"]
    assert "check it still stretches" in got["down"]
    assert "for 4" in got["down"]


def test_the_dock_foot_survives_the_desktop_breakpoint():
    """`padding: 16px 28px 0` in the 1100px block zeroed the bottom, so the
    dock's foot was switched off at exactly the width nobody had measured —
    --cook-dock-h still 123px, dock still sticky, padding 0."""
    # Every .cook-body rule in the file, base and breakpoint alike. The
    # invariant is the one that was broken: none of them may set padding
    # with the shorthand, because that silently zeroes the bottom, and each
    # must name the dock's own height.
    rules, at = [], 0
    while True:
        try:
            at = SHELL_CSS.index(".cook-body {", at)
        except ValueError:
            break
        rules.append(SHELL_CSS[at : SHELL_CSS.index("}", at)])
        at += 1
    assert len(rules) >= 2, "the desktop breakpoint has a rule of its own"
    for rule in rules:
        assert "var(--cook-dock-h" in rule, f"a .cook-body rule drops the dock's foot:\n{rule}"
    # The desktop one is the one that was written with the shorthand.
    assert "padding:" not in rules[-1], "the shorthand is what zeroed it"
    assert "padding-inline" in rules[-1]
