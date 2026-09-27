"""
A leftovers meal in the future shows where it comes from, not "Mark eaten"
(Emily, 2026-09-27).

A reheat night's Meal step offered "Mark eaten" on any day that wasn't
past — a draft, next Thursday — for food that can't have been eaten yet,
and showed nothing else but the title (no recipe: nothing is cooked that
night). Now "Mark eaten" is only for today on the approved week Cook
holds; anywhere else the dock is the swap (with the chat as its quiet
link), and the page links to the recipe the food comes from: "See
Thursday's recipe" — get_week_menu's leftover_from now carries the cook's
slot so the link can open it.

Fails on main 8e046a6: no leftoversEatenNow / mealSourceLinkHtml, and
leftover_from has no slot.
"""
from __future__ import annotations

import json
from pathlib import Path

import nodeharness
import pytest

from test_recipe_screen import _ESCAPE, _PURE, _RECIPE, _extract, _needs_node

REPO = Path(__file__).resolve().parent.parent
SHELL_JS = (REPO / "static" / "shell.js").read_text(encoding="utf-8")

_REHEAT = {
    "state": "planned", "title": "Leftovers — Thursday’s Chili", "source": "leftovers", "meta": "reheat",
    "entry_id": 9, "sides": [], "food_groups": [], "defrost": None, "plate_note": "",
    "leftover_from": {"date": "2026-10-01", "meal": "Chili", "cook_ahead": False, "slot": "dinner"},
}
_CARD = {"entry_id": 9, "meal": "Chili", "is_leftovers": True, "ingredients": [], "instructions": [],
         "has_full_recipe": False, "cooked_status": "pending"}


def _day(date, is_today=False):
    return {"date": date, "isToday": is_today, "isPast": False,
            "breakfast": None, "lunch": None, "dinner": _REHEAT, "snacks": [], "snack": None}


def _screen(day: dict, status: str, cookable: bool = True) -> str:
    days = [{"date": "2026-10-01"}, {"date": "2026-10-02"}, {"date": "2026-10-03"}]
    harness = (
        _ESCAPE
        + "function dayName(d, opts){ return ({'2026-10-01': 'Thursday', '2026-10-02': 'Friday', '2026-10-03': 'Saturday'})[d]; }\n"
        + f"var weekState = {{ data: {{ status: {json.dumps(status)}, slot_times: {{}} }}, days: {json.dumps(days)}, mealBack: 'week' }};\n"
        + f"function planCookableNow() {{ return {json.dumps(cookable)}; }}\n"
        + "var swapState = null;\n"
        + "var REHEAT_ACTION_LABEL = 'Mark eaten';\n"
        + "var SWAP_LABEL = 'Swap';\n"
        + f"var cookState = {{ data: {{ meals: {json.dumps([_CARD])} }} }};\n"
        + "var GRO_ICONS = { chevRight: '<svg></svg>' };\n"
        + "var SLOT_LABELS = { breakfast: 'Breakfast', lunch: 'Lunch', dinner: 'Dinner' };\n"
        + "var WK_ADD_ICON = '<svg/>';\n"
        + "function cookTicked() { return false; }\n"
        + "function cookStartedMinutes() { return null; }\n"
        + _PURE + _RECIPE
        + "".join(_extract(n) + "\n" for n in (
            "daySlotEntry", "slotWord", "isRealCook", "mealDisplayName", "cookMealForEntry",
            "mealCookUnderway", "mealRecipeFor", "mealHeroLine", "mealNoRecipeHtml", "mealIngredientsHtml",
            "swapStateFor", "swapLineHtml", "leftoversEatenNow", "mealSourceLinkHtml", "mealDockHtml",
            "mealStepHtml"))
        + f"console.log(JSON.stringify(mealStepHtml({json.dumps(day)}, 'dinner')));\n"
    )
    res = nodeharness.run_node(harness, timeout=30)
    assert res.returncode == 0, f"node failed: {res.stderr}"
    return json.loads(res.stdout.strip())


_LINK = ('<button type="button" class="recipe-source-link" data-wk-source-day="0" '
         'data-wk-source-slot="dinner">See Thursday’s recipe</button>')


@_needs_node
def test_a_reheat_night_ahead_links_to_its_recipe_and_offers_the_swap():
    html = _screen(_day("2026-10-02"), "approved")
    assert "Mark eaten" not in html
    assert _LINK in html
    assert '<p class="recipe-line">Leftovers from Thursday.</p>' in html
    assert 'class="dock-primary wk-act-swap" data-wk-swap="dinner">Swap<' in html
    assert 'data-wk-tell="dinner">Ask for something else<' in html


@_needs_node
def test_a_reheat_night_on_a_draft_is_not_marked_eaten_even_today():
    html = _screen(_day("2026-10-02", is_today=True), "draft")
    assert "Mark eaten" not in html and _LINK in html


@_needs_node
def test_today_on_the_approved_week_still_marks_it_eaten():
    html = _screen(_day("2026-10-02", is_today=True), "approved")
    assert 'data-wk-cook="dinner">Mark eaten<' in html
    assert _LINK in html


@_needs_node
def test_today_on_an_approved_week_cook_does_not_hold_is_not_marked_eaten():
    html = _screen(_day("2026-10-02", is_today=True), "approved", cookable=False)
    assert "Mark eaten" not in html


def test_the_link_opens_the_cooks_meal_step():
    wire = _extract("wireMealsStep")
    assert "steps.querySelectorAll('[data-wk-source-day]')" in wire
    assert "dayIndex: Number(btn.getAttribute('data-wk-source-day'))," in wire
    css = (REPO / "static" / "shell.css").read_text(encoding="utf-8")
    block = css[css.index(".recipe-source-link {"):]
    block = block[:block.index("}")]
    assert "min-height: 44px;" in block and "color: var(--apricot-label);" in block


def test_leftover_from_carries_the_cooks_slot():
    src = (REPO / "app" / "tools" / "weekly_plan.py").read_text(encoding="utf-8")
    assert '"slot": src.get("slot"),' in src
