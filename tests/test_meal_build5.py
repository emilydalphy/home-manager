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
    assert "mealHeroHtml(day, slot, entry, clock)" in meal
    hero = _fn("mealHeroHtml")
    assert '<div class="dinner-hero wk-meal-hero">' in hero
    # 2026-09-12 ("Meal · B · The clock"): the eyebrow is the slot and the
    # day; the right end says when it is on the table, in words.
    assert "slotEyebrowLabel(day, slot) + ' · ' + weekday" in hero
    assert "On the table by " in hero and "spokenTime(clock.table)" in hero
    # The planner's reasoning used to be the hero's italic line; cut
    # 2026-09-11 (copy cleanse) — the hero is the name and the facts.
    assert "entry.reason" not in hero and "entry.reason" not in meal
    assert "hero-chips" in hero
    assert 'wk-card-title">Why this night' not in meal


def test_the_clock_and_cook_ahead_follow_and_the_action_is_docked():
    meal = _fn("mealStepHtml")
    # The plate card and the recipe card are gone (2026-09-12); the cook
    # is the clock, and the cook-ahead picker keeps its card under it.
    assert "plateCardHtml" not in meal and "mealRecipeCardHtml" not in meal
    assert "mealClockHtml(slot, clock)" in meal
    assert "cookAheadHtml(cookMeal)" in meal
    assert "mealDockHtml(day, slot, clock)" in meal
    dock = _fn("mealDockHtml")
    assert '<div class="wk-decide dock wk-meal-dock">' in dock
    assert "day.isPast) return '';" in dock  # no dock on a past day
    assert "Swap this meal" in dock
    assert "#week-steps .wk-decide.dock { margin-top: auto; }" in SHELL_CSS
