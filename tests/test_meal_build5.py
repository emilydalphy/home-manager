"""The Meal step, Build 5 of the screen-by-screen redesign (Emily, 2026-09-11:
"Plan - meal screen. needs work. looks weak for content.")."""
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SHELL_JS = (REPO / "static" / "shell.js").read_text(encoding="utf-8")
SHELL_CSS = (REPO / "static" / "shell.css").read_text(encoding="utf-8")


def _fn(name):
    start = SHELL_JS.index("  function %s(" % name)
    return SHELL_JS[start:SHELL_JS.index("\n  }\n", start)]


def test_the_meal_opens_on_a_spruce_hero_with_its_facts_and_no_reasoning():
    meal = _fn("mealStepHtml")
    assert '<div class="dinner-hero wk-meal-hero">' in meal
    assert "slotEyebrow(day, slot)" in meal
    # The planner's reasoning used to be the hero's italic line; cut
    # 2026-09-11 (copy cleanse) — the hero is the name and the facts.
    assert "hero-accent" not in meal
    assert "entry.reason" not in meal
    assert "hero-chips" in meal
    assert 'wk-card-title">Why this night' not in meal


def test_plate_recipe_and_cook_ahead_follow_as_cards_and_the_action_is_docked():
    meal = _fn("mealStepHtml")
    assert "plateCardHtml(entry)" in meal
    assert "mealRecipeCardHtml(cookMeal, slot)" in meal
    assert "mealDockHtml(day, slot)" in meal
    dock = _fn("mealDockHtml")
    assert '<div class="wk-decide dock wk-meal-dock">' in dock
    assert "if (!acts) return '';" in dock  # no dock on a past or away day
    assert "#week-steps .wk-decide.dock { margin-top: auto; }" in SHELL_CSS
