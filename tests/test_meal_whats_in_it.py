"""
The meal screen's ingredients and its "Add something" (Emily, 2026-09-13
— Loop Board "Meal screen: ingredients at a glance, for review before I
commit to the swap" and "Meal screen: add what's missing ('add potatoes')
from the dish itself, not via chat").

Since 2026-09-18 ("The recipe is the recipe") the Meal step draws the same
Ingredients card cook mode does (recipeIngredientsHtml, one row per
ingredient with its amount, the plate pass's tags beside it) and, under
it, a plain "Add something" — an outline control, never the screen's
apricot (the dock has that, Rule 5). The Add sheet (#wk-add-sheet) asks
"What should go with it?" and offers the server's short list for this
dish, each row the tap that adds, plus one line to type. The clock that
used to time a side's steps went with the clock (tests/test_recipe_screen.py
covers the screen). These run shell.js's own functions under node
(tests/nodeharness.py), the house standard.
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

import nodeharness

REPO = Path(__file__).resolve().parent.parent
SHELL_JS = (REPO / "static" / "shell.js").read_text(encoding="utf-8")
SHELL_CSS = (REPO / "static" / "shell.css").read_text(encoding="utf-8")
SHELL_HTML = (REPO / "static" / "shell.html").read_text(encoding="utf-8")

_needs_node = pytest.mark.skipif(
    shutil.which("node") is None, reason="node is needed to execute shell.js's own functions"
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
                break
        j += 1
    return SHELL_JS[start : j + 1]


def _var(name: str) -> str:
    start = SHELL_JS.index(f"var {name} = ")
    end = SHELL_JS.index(";\n", start) + 1
    return SHELL_JS[start:end]


_PURE = (
    "function escapeHtml(s) { return String(s == null ? '' : s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/\"/g,'&quot;'); }\n"
    + _var("WK_ADD_ICON") + "\n"
    + _var("HUMAN_QTY_FRACTIONS") + "\n"
    + "function cookMealKey(m) { return 'e' + m.entry_id; }\n"
    + "function cookTicked() { return false; }\n"
    + _extract("isSnackSlot") + "\n"
    + "".join(_extract(n) + "\n" for n in (
        "humanQtyAmount", "humanQtyText", "cookIngredientLabel", "cookUnscaledHtml",
        "recipeIngredientRowHtml", "recipeIngredientsHtml", "mealIngredientsHtml"))
)


def _run(expr: str):
    res = nodeharness.run_node(_PURE + f"console.log(JSON.stringify({expr}));\n", timeout=30)
    assert res.returncode == 0, f"node failed: {res.stderr}"
    return json.loads(res.stdout.strip())


POTATOES = {
    "name": "Roasted potatoes", "minutes": 25, "added_by": "household",
    "instructions": [
        "Alongside — Roasted potatoes: Halve the potatoes, toss with oil and salt on a sheet pan.",
        "Alongside: Roast at 425° until golden, about 25 minutes.",
    ],
}

SHRIMP = {
    "meal": "Garlic-Herb Shrimp with Roasted Broccolini",
    "has_full_recipe": True,
    "prep_time_minutes": 10,
    "cook_time_minutes": 20,
    "default_servings": 2,
    "attendance": {"headcount": 2},
    "ingredients": [
        {"item": "Shrimp", "qty": "0.5 lb"},
        {"item": "Broccolini", "qty": "1 bunch"},
        {"item": "Lemon", "qty": "0.5"},
    ],
    "instructions": [
        "Toss the broccolini with oil and roast.",
        "Sear the shrimp with the garlic.",
        "Finish with lemon.",
    ],
    "sides": [],
}


def _with_side(meal: dict, side: dict, extra_ingredients: list | None = None) -> dict:
    out = json.loads(json.dumps(meal))
    out["sides"] = out.get("sides", []) + [side]
    out["instructions"] = out["instructions"] + side["instructions"]
    out["ingredients"] = out["ingredients"] + (extra_ingredients or [])
    return out


# ---------- the overview ----------

def _overview(meal: dict | None, is_past: bool = False, state: str = "planned", is_cook: bool = True) -> str:
    info = {"isCook": is_cook, "cookMeal": meal}
    return _run(
        f"mealIngredientsHtml({{ isPast: {json.dumps(is_past)} }}, 'dinner', "
        f"{{ state: {json.dumps(state)}, entry_id: 7 }}, {json.dumps(info)})"
    )


@_needs_node
def test_the_ingredients_are_one_per_row_with_the_amount_for_tonight():
    html = _overview(SHRIMP)
    assert '<span class="cook-eyebrow recipe-eyebrow">Ingredients</span>' in html
    assert html.count('<li class="cook-getout-item') == 3
    assert '<span class="cook-getout-text">½ lb Shrimp</span>' in html
    assert '<span class="cook-getout-text">½ Lemon</span>' in html
    # Not a dot-separated sentence.
    assert " · Broccolini · " not in html
    # The one way to change the plate from here, plain, under the rows.
    assert 'class="wk-ing-add" data-wk-add="dinner"' in html
    assert "<span>Add something</span>" in html
    assert "dock-primary" not in html and "btn-primary" not in html
    # No "for two" / "two nights, 4 plates" eyebrow: "Cooking for" above
    # the card is the one place the number lives (2026-09-18).
    assert "What’s in it" not in html and "for two" not in html


@_needs_node
def test_an_added_thing_and_a_thing_already_at_home_are_marked():
    meal = _with_side(SHRIMP, POTATOES, [{"item": "Yukon Gold potatoes", "qty": "1 lb", "added": True}])
    meal["ingredients"][2]["at_home"] = True
    html = _overview(meal)
    assert 'Yukon Gold potatoes <span class="wk-ing-tag">added</span>' in html
    assert 'Lemon <span class="wk-ing-tag">at home</span>' in html
    assert html.count("wk-ing-tag") == 2


@_needs_node
def test_a_past_day_shows_what_was_in_it_but_offers_nothing_to_add():
    html = _overview(SHRIMP, is_past=True)
    assert "½ lb Shrimp" in html
    assert "data-wk-add" not in html


@_needs_node
def test_a_reheat_night_or_a_missing_card_gets_no_ingredients():
    assert _overview(None, is_cook=False) == ""
    assert _overview(None, is_cook=True) == ""


# ---------- the screen, the sheet, the styles (source markers) ----------

def test_the_ingredients_sit_between_the_count_and_the_steps():
    step = _extract("mealStepHtml")
    assert step.index("recipeServesHtml(") < step.index("mealIngredientsHtml(") < step.index("recipeStepsHtml(")


def test_the_sheet_asks_the_plain_question_and_has_the_line_to_type():
    assert 'id="wk-add-sheet"' in SHELL_HTML
    assert "What should go with it?" in SHELL_HTML
    rows = _extract("mealAddRowsHtml")
    assert 'placeholder="Something else…"' in rows
    # Since 2026-09-13 (S10) a row is chosen, then saved: the "Add" on
    # every row became one Save under them — see test_plate_parts.py.
    assert 'id="wk-add-save"' in rows and ">Save</button>" in rows
    assert "coming soon" not in SHELL_JS.lower()


def test_the_add_flow_reaches_the_servers_three_calls_and_offers_undo():
    assert "/additions?entry_id=" in _extract("openMealAddSheet")
    add = _extract("runMealAdd")
    assert "/add-component" in add
    assert "label: 'Undo'" in add
    assert "is already on it." in add
    assert "/remove-component" in _extract("runMealAddUndo")
    # Wired from the overview's button.
    assert "[data-wk-add]" in _extract("wireMealsStep")


def test_the_copy_uses_contractions_and_no_dashboard_labels():
    for line in ("What should go with it?", "Add something", "Taken back off.",
                 "That didn’t go on — try again."):
        assert line in SHELL_JS or line in SHELL_HTML, line
    for banned in ("Action required", "Add component", "Add ingredient", "Ingredients:"):
        assert banned not in _extract("mealIngredientsHtml")


def test_add_something_is_an_outline_control_not_a_second_apricot():
    i = SHELL_CSS.index(".wk-ing-add {")
    block = SHELL_CSS[i:SHELL_CSS.index("}", i)]
    assert "var(--apricot)" not in block
    assert "var(--hairline-strong)" in block
    assert "min-height: 44px" in block, "tappable at 44px (Rule 6)"
    for cls in (".wk-whatsin", ".wk-ing-tag", ".recipe-card .cook-getout-row",
                "#wk-add-sheet", ".wk-add-option", ".wk-add-input"):
        assert cls in SHELL_CSS, cls
    assert "#wk-add-scrim[hidden], #wk-add-sheet[hidden] { display: none; }" in SHELL_CSS
