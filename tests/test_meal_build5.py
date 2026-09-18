"""The Meal step, Build 5 of the screen-by-screen redesign (Emily, 2026-09-11:
"Plan - meal screen. needs work. looks weak for content.")."""
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SHELL_JS = (REPO / "static" / "shell.js").read_text(encoding="utf-8")
SHELL_CSS = (REPO / "static" / "shell.css").read_text(encoding="utf-8")


def _fn(name):
    start = SHELL_JS.index("  function %s(" % name)
    return SHELL_JS[start:SHELL_JS.index("\n  }\n", start)]


def test_the_meal_opens_on_its_title_with_no_hero_and_no_reasoning():
    """UPDATED 2026-09-18 ("The recipe is the recipe"): the spruce hero
    ("Meal · B · The clock", 2026-09-12) is gone — the meal opens on the
    dish as its title, "Cooking for", the Ingredients and Steps cards. The
    planner's reasoning stays out (copy cleanse, 2026-09-11)."""
    meal = _fn("mealStepHtml")
    assert '<h1 class="recipe-title">' in meal
    assert "mealHeroHtml" not in meal and "dinner-hero" not in meal
    assert "On the table by" not in meal and "spokenTime" not in meal
    assert "entry.reason" not in meal
    assert 'wk-card-title">Why this night' not in meal


def test_the_recipe_follows_and_the_action_is_docked():
    meal = _fn("mealStepHtml")
    # The plate card and the recipe card are gone (2026-09-12), and so is
    # the clock and the cook-ahead picker (2026-09-18); the recipe's own
    # cards render here, the same ones cook mode draws.
    assert "plateCardHtml" not in meal and "mealRecipeCardHtml" not in meal
    assert "mealClockHtml" not in meal and "cookAheadHtml" not in meal
    assert "recipeServesHtml(cookMeal, 'wk')" in meal
    assert "mealIngredientsHtml(day, slot, entry, info)" in meal
    assert "recipeStepsHtml(cookMeal, false)" in meal
    assert "mealDockHtml(day, slot, info)" in meal
    dock = _fn("mealDockHtml")
    assert '<div class="wk-decide dock wk-meal-dock">' in dock
    assert "day.isPast) return '';" in dock  # no dock on a past day
    assert "SWAP_LABEL" in dock
    assert "#week-steps .wk-decide.dock { margin-top: auto; }" in SHELL_CSS
