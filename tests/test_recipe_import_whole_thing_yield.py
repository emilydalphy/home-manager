"""
An imported recipe whose yield is "1 pie", "1 loaf", "2 dozen" (Loop Board
card 4001f4c0523181e88f5ef6e52dcb350b; Emily 2026-10-26 "go with your
picks"): the yield says what the recipe makes, not how many it serves.
Read as servings it was 1, and the planner multiplied every amount by four
for a family of four. Now the servings are unknown, the draft carries the
yield's words, and the review sheet says so and asks for Serves before
saving.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from app import recipe_import as ri

SHELL_JS = (Path(__file__).resolve().parent.parent / "static" / "shell.js").read_text()


def _node(recipe_yield):
    return {
        "@type": "Recipe", "name": "Apple Pie", "recipeYield": recipe_yield,
        "recipeIngredient": ["6 apples", "1 cup sugar"],
        "recipeInstructions": ["Bake it."],
    }


@pytest.mark.parametrize("value, makes", [
    ("1 pie", "1 pie"), ("1 loaf", "1 loaf"), ("2 dozen", "2 dozen"),
    (["1 pie"], "1 pie"), ("Makes 1 cake", "1 cake"), ("Yield: 2 dozen cookies", "2 dozen cookies"),
])
def test_a_whole_thing_yield_leaves_servings_unknown(value, makes):
    draft = ri.draft_from_json_ld(_node(value), "https://example.com/pie")
    assert draft["servings_unknown_makes"] == makes
    # Not 1 (which the planner scaled by four): the planner's 4 until the
    # household sets it on the review sheet.
    assert draft["default_servings"] == 4


@pytest.mark.parametrize("value, servings", [
    ("4 servings", 4), ("Serves 6-8", 6), (["8", "1 pie"], 8), ("1 pie, serves 8", 8),
    ("makes 12 cookies", 12), (6, 6),
])
def test_a_serving_count_is_still_read(value, servings):
    draft = ri.draft_from_json_ld(_node(value), "https://example.com/pie")
    assert draft["servings_unknown_makes"] == ""
    assert draft["default_servings"] == servings


def test_the_model_reader_can_say_what_it_makes():
    draft = ri.draft_from_model(
        {"found": True, "name": "Banana Bread", "makes": "1 loaf",
         "ingredients": ["3 bananas"], "instructions": ["Bake."]},
        "https://example.com/bread",
    )
    assert draft["servings_unknown_makes"] == "1 loaf"
    stated = ri.draft_from_model(
        {"found": True, "name": "Banana Bread", "makes": "1 loaf", "default_servings": 10,
         "ingredients": ["3 bananas"], "instructions": ["Bake."]},
        "https://example.com/bread",
    )
    assert stated["servings_unknown_makes"] == ""
    assert stated["default_servings"] == 10


def test_the_review_sheet_says_so_and_asks_for_serves():
    review = SHELL_JS[SHELL_JS.index("function renderRecipeLinkReview"):SHELL_JS.index("function collectRecipeLinkDraft")]
    assert "draft.servings_unknown_makes" in review
    assert "not how many it serves. Set Serves before saving." in review
    # Serves starts empty rather than a number nobody chose.
    assert "(makes ? '' : escapeHtml(draft.default_servings || 4))" in review
    save = SHELL_JS[SHELL_JS.index("function saveRecipeLink"):]
    save = save[:save.index("Api.fetch")]
    assert "Set how many it serves, then save." in save
