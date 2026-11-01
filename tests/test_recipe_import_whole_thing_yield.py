"""
An imported recipe whose yield is "1 pie", "1 loaf", "2 dozen" (Loop Board
card 4001f4c0523181e88f5ef6e52dcb350b; Emily 2026-10-26 "go with your
picks"): the yield says what the recipe makes, not how many it serves.
Read as servings it was 1, and the planner multiplied every amount by four
for a family of four. Now the servings are unknown, the draft carries the
yield's words, and the review sheet asks "How many people does this
feed?" — a skip saves the planner's 4 (Emily 2026-10-31, option A; a
batch count like "24 cookies" is flagged the same way since then).
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
    # Emily 2026-10-31, option A: a batch count is not people either.
    ("Makes 2 dozen", "2 dozen"), (["1 loaf", "12 slices"], "1 loaf"), ("1 1/2 quarts", "1 1/2 quarts"),
    ("makes 12 cookies", "12 cookies"), ("24 muffins", "24 muffins"),
    # WP Recipe Maker's shape: the bare number is the batch said again.
    (["24", "24 cookies"], "24 cookies"), ([12, "12 muffins"], "12 muffins"),
])
def test_a_whole_thing_yield_leaves_servings_unknown(value, makes):
    draft = ri.draft_from_json_ld(_node(value), "https://example.com/pie")
    assert draft["servings_unknown_makes"] == makes
    # Not 1 (which the planner scaled by four): the planner's 4 until the
    # household sets it on the review sheet.
    assert draft["default_servings"] == 4


@pytest.mark.parametrize("value, servings", [
    ("4 servings", 4), ("Serves 6-8", 6), (["8", "1 pie"], 8), ([8, "1 pie"], 8), (["8", "24 cookies"], 8), ("1 pie, serves 8", 8),
    ("Serves 4", 4), ("4 people", 4), (4, 4), ("4", 4), ("12 cookies, serves 6", 6), (6, 6),
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


def test_the_review_sheet_asks_how_many_people_it_feeds():
    review = SHELL_JS[SHELL_JS.index("function renderRecipeLinkReview"):SHELL_JS.index("function collectRecipeLinkDraft")]
    assert "draft.servings_unknown_makes" in review
    # Option A (Emily 2026-10-31): the plain question, empty, in Serves' place.
    assert "How many people does this feed?" in review
    assert 'id="rli-servings" class="rli-input" type="number" min="1" inputmode="numeric" value=""' in review
    # A skip is not a wall: Save no longer refuses a blank answer, and a
    # blank goes as the planner's 4.
    save = SHELL_JS[SHELL_JS.index("function saveRecipeLink"):]
    save = save[:save.index("Api.fetch")]
    assert "servings_unknown_makes" not in save
    collect = SHELL_JS[SHELL_JS.index("function collectRecipeLinkDraft"):SHELL_JS.index("function saveRecipeLink")]
    assert "default_servings: num('#rli-servings') || 4" in collect


@pytest.mark.parametrize("answer, saved", [(6, 6), (None, 4)])
def test_the_answer_is_what_it_serves_and_a_skip_is_four(signed_in, monkeypatch, answer, saved):
    from app import tools
    from test_recipe_import import _html_response, _page, _wire

    page = json.dumps({
        "@type": "Recipe", "name": "Sugar Cookies", "recipeYield": "Makes 2 dozen",
        "recipeIngredient": ["2 cups flour", "1 cup sugar"], "recipeInstructions": "Mix.\nBake.",
    })
    _wire(monkeypatch, {"/cookies": _html_response(_page(page))})
    draft = signed_in.post("/api/recipes/import-url", json={"url": "https://bake.example.com/cookies"}).json()["draft"]
    assert draft["servings_unknown_makes"] == "2 dozen"
    # What the sheet sends: the answer, or nothing for a skip.
    body = {**draft, "default_servings": answer}
    assert signed_in.post("/api/recipes/add", json=body).status_code == 200
    assert tools.get_recipe("Sugar Cookies")["default_servings"] == saved
