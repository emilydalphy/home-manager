"""
Cook mode says it right (overnight defect hunt, 2026-10-21; branch
overnight/cook-mode-says-it-right). Three small slips, each driven through
shell.js's own functions under node:

1. The batch line ("Double batch: 2 tonight, 2 for dinner tomorrow.") kept
   reading after the cook stepped "Cooking for" off the plan's count. Its
   numbers are the chain's and still never follow the stepper (2026-10-05);
   it is now simply not said while the stepper is off the plan's count, and
   comes back when the stepper does. Exercised through
   cookApplyServesOverride, the same path a stepper reply takes.
2. "Ingredients · 1 servings" -> "Ingredients · 1 serving", in both
   ingredient renderers (recipeIngredientsHtml, recipePlainIngredientsHtml).
3. "Tacos was marked skipped." -> "Marked Tacos as skipped" (markedLine).
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

import nodeharness

REPO = Path(__file__).resolve().parent.parent
SHELL_JS = (REPO / "static" / "shell.js").read_text(encoding="utf-8")

pytestmark = pytest.mark.skipif(
    shutil.which("node") is None, reason="node is needed to run the screen's own functions"
)


def _extract(name: str) -> str:
    start = SHELL_JS.index(f"function {name}(")
    i = SHELL_JS.index("{", start)
    depth, j = 0, i
    while True:
        if SHELL_JS[j] == "{":
            depth += 1
        elif SHELL_JS[j] == "}":
            depth -= 1
            if depth == 0:
                return SHELL_JS[start:j + 1]
        j += 1


_PRELUDE = """
function escapeHtml(s){return String(s == null ? '' : s)
  .replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');}
function cookMealKey(m){ return 'k' + (m.entry_id || m.meal); }
function cookGetOutRowHtml(){ return '<li></li>'; }
function recipeIngredientRowHtml(){ return '<li></li>'; }
function cookUnscaledHtml(){ return ''; }
function cookIngredientLabel(i){ return String(i.item || ''); }
function cookReadTicks(){}
var cookState = { serves: {} };
var SAVED_PLAIN = 'Saved';
var SAVED_NAME_MAX = 44;
"""

_FUNCS = ("cookServesShown", "cookApplyServesOverride", "recipeBatchLineHtml",
          "recipeIngredientsHtml", "recipePlainIngredientsHtml", "savedName", "markedLine")


def _run(body: str):
    script = _PRELUDE + "\n".join(_extract(f) for f in _FUNCS) + "\n" + body
    res = nodeharness.run_node(script, timeout=30)
    assert res.returncode == 0, f"node failed: {res.stderr}"
    return json.loads(res.stdout.strip())


LINE = "Double batch: 2 tonight, 2 for dinner tomorrow."
_BATCH_MEAL = json.dumps({
    "entry_id": 7, "meal": "Chicken Skewers", "batch_line": LINE,
    "covers_note": "x", "servings": 4, "default_servings": 4,
    "ingredients": [{"item": "Chicken thighs", "qty": "2 lb"}],
})


def _after_stepping_to(n: int) -> str:
    # What cookStepServings stores once the scale reply lands, then the
    # override re-applied as every render does.
    return _run(f"""
var meal = {_BATCH_MEAL};
cookState.serves[cookMealKey(meal)] = {{servings: {n}, ingredients: meal.ingredients,
  dish: meal.meal, unscaled_items: [], was_batch: true, planned_for: 4}};
cookApplyServesOverride([meal]);
console.log(JSON.stringify(recipeBatchLineHtml(meal)));
""")


def test_the_batch_line_is_not_said_once_the_cook_steps_off_the_plans_count():
    assert _after_stepping_to(6) == ""
    assert _after_stepping_to(2) == ""


def test_the_batch_line_holds_on_the_plans_count_and_comes_back_when_stepped_back():
    # untouched: said
    assert LINE in _run(f"var meal = {_BATCH_MEAL};"
                        "console.log(JSON.stringify(recipeBatchLineHtml(meal)));")
    # stepped up and back down to the plan's 4: said again, words unchanged
    assert LINE in _after_stepping_to(4)


def test_an_override_stored_before_planned_for_existed_keeps_the_line():
    out = _run(f"""
var meal = {_BATCH_MEAL};
cookState.serves[cookMealKey(meal)] = {{servings: 6, ingredients: [], dish: meal.meal,
  unscaled_items: [], was_batch: true}};
cookApplyServesOverride([meal]);
console.log(JSON.stringify(recipeBatchLineHtml(meal)));
""")
    assert LINE in out


@pytest.mark.parametrize("fn", ["recipeIngredientsHtml", "recipePlainIngredientsHtml"])
def test_one_serving_is_singular_and_more_stay_plural(fn):
    out = _run(f"""
var one = {{batch_line: 'x', default_servings: 1, ingredients: [{{item: 'Eggs', qty: '2'}}]}};
var six = {{batch_line: 'x', default_servings: 6, ingredients: [{{item: 'Eggs', qty: '12'}}]}};
console.log(JSON.stringify([{fn}(one, 'x', false), {fn}(six, 'x', false)]));
""")
    assert "Ingredients &middot; 1 serving<" in out[0]
    assert "1 servings" not in out[0]
    assert "Ingredients &middot; 6 servings<" in out[1]


def test_the_yesterday_toast_needs_no_was_or_were():
    out = _run("console.log(JSON.stringify([markedLine('Tacos', 'skipped'),"
               " markedLine('Beef chili', 'cooked'), markedLine('', 'skipped')]));")
    assert out == ["Marked Tacos as skipped", "Marked Beef chili as cooked", "Saved"]
    # and the Yesterday check is the caller that says it
    assert "showToast(markedLine(meal.meal, answer === 'had' ? 'cooked' : 'skipped'));" in SHELL_JS
    assert "'marked skipped'" not in SHELL_JS
