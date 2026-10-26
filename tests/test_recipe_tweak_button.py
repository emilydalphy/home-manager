"""
The recipe page's small "Tweak" button (Loop Board, Emily 2026-10-25).

Decided: Tweak sits BESIDE "Change recipe", small — Tweak is a small
adjustment to this dish, Change recipe a different recipe — and Settings →
Recipes gets none (that page is the book, not a meal). Its tap opens the
Tweak sheet straight on the box ("What would you change? Say it your way"
+ Rewrite), with no rows first, and the box is the Plan row's own
"Something else" (runTweakElse): POST /api/meal-recipe/rewrite with
this_meal_only, the allergy / won't-eat gate before anything is saved
(pinned server-side in test_tweak_something_else.py, which posts the same
body), Undo through crUndo. The renderers and the send run under node.
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

import nodeharness
from test_week_seven_tiles import _extract, _extract_var

REPO = Path(__file__).resolve().parent.parent
SHELL_JS = (REPO / "static" / "shell.js").read_text(encoding="utf-8")
_needs_node = pytest.mark.skipif(shutil.which("node") is None, reason="node is needed to run the renderers")

_PRELUDE = (
    _extract("escapeHtml", SHELL_JS) + "\n"
    + "var ICONS = { arrow: '<svg class=\"arrow\"></svg>' };\n"
    + "var WK_ICONS = { tweak: '<svg class=\"sliders\"></svg>' };\n"
    + "function cookTicked() { return false; }\n"
    + "function cookMealKey(m) { return String(m.entry_id); }\n"
    + "function cookDockHtml(p) { return '<div class=\"dock cook-dock\">' + p + '</div>'; }\n"
    + "function cookDockCookedHtml() { return '<button>Done — on the table</button>'; }\n"
    + _extract_var("CHANGE_RECIPE_LABEL", SHELL_JS) + "\n"
    + _extract_var("CHANGE_RECIPE_ASK_LABEL", SHELL_JS) + "\n"
    + _extract_var("RECIPE_TWEAK_LABEL", SHELL_JS) + "\n"
    + _extract_var("RECIPE_TWEAK_GO", SHELL_JS) + "\n"
    + _extract("recipeIsChangeable", SHELL_JS) + "\n"
    + _extract("recipeTweakBtnHtml", SHELL_JS) + "\n"
    + _extract("cookRecipeDockHtml", SHELL_JS) + "\n"
    + _extract("tweakElseHtml", SHELL_JS) + "\n"
    + _extract("recipeTweakSheetBodyHtml", SHELL_JS) + "\n"
)


def _run(expr: str):
    res = nodeharness.run_node(_PRELUDE + f"console.log(JSON.stringify({expr}));\n", timeout=30)
    assert res.returncode == 0, f"node failed: {res.stderr}"
    return json.loads(res.stdout.strip())


_MEAL = {"entry_id": 41, "meal": "Chicken Tacos", "date": "2026-10-26", "slot": "dinner",
         "has_full_recipe": True, "is_leftovers": False, "cooked_status": "pending",
         "instructions": ["Grill the chicken.", "Top with cilantro."]}


@_needs_node
def test_cooks_recipe_dock_has_a_small_tweak_beside_change_recipe_and_start_cooking_keeps_its_row():
    dock = _run(f"cookRecipeDockHtml({json.dumps(_MEAL)})")
    alts = dock[dock.index('recipe-dock-alts'):dock.index('cook-hero-action')]
    # Beside Change recipe, in the same row, ahead of it.
    assert alts.index('data-recipe-tweak="1"') < alts.index('data-cr="open"')
    assert 'data-entry-id="41"' in alts and 'data-date="2026-10-26"' in alts and 'data-slot="dinner"' in alts
    assert '<span>Tweak</span>' in alts
    # Outline, never a second apricot (Rule 5): the only apricot is Start cooking.
    assert dock.count("cook-hero-action") == 1
    assert 'class="cook-dock-back recipe-dock-tweak recipe-tweak-btn"' in alts


@_needs_node
def test_a_reheat_night_gets_no_tweak_as_it_gets_no_change_recipe():
    dock = _run(f"cookRecipeDockHtml({json.dumps(dict(_MEAL, is_leftovers=True))})")
    assert "data-recipe-tweak" not in dock and 'data-cr="open"' not in dock


def test_the_meal_step_puts_tweak_beside_change_recipe_as_one_unit():
    body = _extract("mealIngredientsHtml", SHELL_JS)
    assert "'<span class=\"recipe-change-pair\">' + recipeTweakBtnHtml(cookMeal, 'wk-ing-add') + change + '</span>'" in body


def test_settings_recipes_page_has_no_tweak():
    # Decision 2: the book's page has no actions at all.
    view = _extract("recipeViewHtml", SHELL_JS)
    assert "recipeTweakBtnHtml" not in view and "data-recipe-tweak" not in view
    assert "recipeChangeBtnHtml" not in view


@_needs_node
def test_the_sheet_opens_straight_on_the_box_with_rewrite():
    html = _run("recipeTweakSheetBodyHtml('Chicken Tacos')")
    assert '<div class="wk-tweak-else" id="wk-tweak-else">' in html, "open, not hidden"
    assert ">What would you change? Say it your way</label>" in html
    assert '<button type="button" class="cr-go" id="wk-tweak-go">Rewrite</button>' in html
    # No sheet of options first: none of the Plan row's part rows.
    assert 'class="plate-rows"' not in html and "wk-tweak-else-open" not in html
    assert html.count('class="cr-go"') == 1


def _drive(answer: dict, ok: bool = True) -> dict:
    """runTweakElse as the recipe page's sheet sets it up."""
    prelude = """
function el() { return { hidden: false, textContent: '', value: '', readOnly: false, disabled: false,
  focus: function () {}, setAttribute: function () {} }; }
var els = { 'wk-tweak-else': el(), 'wk-tweak-text': el(), 'wk-tweak-go': el(), 'wk-tweak-said': el(),
  'wk-tweak-done': el(), 'wk-tweak-done-row': el(), 'wk-tweak-undo': el(), 'wk-tweak-remember': el(),
  'wk-tweak-leave': el(), 'wk-tweak-hint': el() };
var label = el();
var tweakSheetEl = { hidden: false, classList: { contains: function () { return true; } },
  querySelector: function (sel) { return sel.charAt(0) === '#' ? els[sel.slice(1)] : label; } };
var log = { closed: 0, after: null, sent: null, busyWord: null };
var panels = {};
function closeTweakSheet() { log.closed += 1; }
function markRecentlyChanged() {}
async function crAfterChange(said, rid, quiet) { log.after = [said, rid, !!quiet]; }
function showToast() {}
function loadWeekMenu() {}
var Api = { fetch: async function (url, init) { log.sent = [url, JSON.parse(init.body)];
  log.busyWord = els['wk-tweak-go'].textContent;
  return { ok: OK, status: OK ? 200 : 400, json: async function () { return ANSWER; } }; } };
var tweakElseState = { panel: {}, date: '2026-10-26', slot: 'dinner', entryId: 41, busy: false, text: '',
  goLabel: 'Rewrite', busyLabel: 'Rewriting…' };
els['wk-tweak-text'].value = 'No cilantro';
""".replace("OK", "true" if ok else "false").replace("ANSWER", json.dumps(answer))
    harness = (prelude + _extract("tweakElseEl", SHELL_JS) + _extract("tweakElseSay", SHELL_JS)
               + "async " + _extract("runTweakElse", SHELL_JS)
               + "runTweakElse().then(function () { console.log(JSON.stringify({ log: log, els: els })); });\n")
    res = nodeharness.run_node(harness, timeout=30)
    assert res.returncode == 0, f"node failed: {res.stderr}"
    return json.loads(res.stdout.strip())


@_needs_node
def test_rewrite_sends_this_meal_only_and_lands_with_undo():
    out = _drive({"status": "rewritten", "said": "Chicken Tacos is rewritten.", "request_id": 7})
    assert out["log"]["sent"] == ["/api/meal-recipe/rewrite",
                                  {"entry_id": 41, "text": "No cilantro", "this_meal_only": True}]
    assert out["log"]["busyWord"] == "Rewriting…"
    # crAfterChange pops the toast with its Undo (crUndo) — not quiet.
    assert out["log"]["closed"] == 1 and out["log"]["after"] == ["Chicken Tacos is rewritten.", 7, False]


@_needs_node
def test_an_allergy_refusal_keeps_the_words_and_puts_rewrite_back():
    out = _drive({"status": "blocked", "said": "I couldn’t write that one without peanut in it."})
    els = out["els"]
    assert out["log"]["after"] is None and out["log"]["closed"] == 0
    assert els["wk-tweak-said"]["textContent"] == "I couldn’t write that one without peanut in it."
    assert els["wk-tweak-text"]["value"] == "No cilantro"
    assert els["wk-tweak-go"]["textContent"] == "Rewrite" and els["wk-tweak-go"]["disabled"] is False
