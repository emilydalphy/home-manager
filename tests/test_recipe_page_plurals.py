"""
The recipe page says "2 onions", not "2 onion" (2026-10-10).

Repro: a recipe written for 4 with "onion · 1" opened at 8 servings —
GET /api/recipes/scale answers qty "2", item "onion", and the page's
ingredient row (cookIngredientLabel) joined them as "2 onion". The server
now adds `item_label` ("onions") for a bare count over one, through the
plural rule the scaled steps already use (recipes._pluralize_noun), and the
row reads it. `item` is never rewritten: ticks and the list key on it.
"""
from __future__ import annotations

import json
from pathlib import Path

import nodeharness
from shop_harness import needs_node

from app import tools
from app.tools import recipes

SHELL_JS = (Path(__file__).resolve().parent.parent / "static" / "shell.js").read_text(encoding="utf-8")


def _rows(name: str, servings: int, signed_in) -> dict:
    res = signed_in.get("/api/recipes/scale", params={"name": name, "servings": servings})
    assert res.status_code == 200
    return {r["item"]: r for r in res.json()["scaled_ingredients"]}


def test_eight_servings_of_a_one_onion_recipe_says_onions(signed_in):
    tools.add_recipe(
        "Onion Soup",
        ingredients=[
            {"item": "onion", "qty": "1"},
            {"item": "potato, diced", "qty": "2"},
            {"item": "garlic", "qty": "2 cloves"},
            {"item": "bay leaf", "qty": "1"},
        ],
        default_servings=4,
    )
    rows = _rows("Onion Soup", 8, signed_in)
    assert (rows["onion"]["qty"], rows["onion"]["item_label"]) == ("2", "onions")
    assert rows["potato, diced"]["item_label"] == "potatoes, diced"
    # Not on the countable list: left as written rather than guessed at.
    assert "item_label" not in rows["bay leaf"]
    # A unit carries its own plural; the item is left alone.
    assert "item_label" not in rows["garlic"]
    # At four, one onion: no label, and the label never sticks around from
    # a bigger count.
    rows4 = _rows("Onion Soup", 4, signed_in)
    assert "item_label" not in rows4["onion"]


def test_the_stored_item_is_never_rewritten():
    out = recipes.cooking_ingredients([{"item": "egg", "qty": "3"}], servings=4)
    assert out[0]["item"] == "egg" and out[0]["item_label"] == "eggs"


def _label(ing: dict) -> str:
    start = SHELL_JS.index("  var HUMAN_QTY_FRACTIONS")
    end = SHELL_JS.index("  // \"18:30\"", start)
    fn_start = SHELL_JS.index("  function cookIngredientLabel(")
    fn_end = SHELL_JS.index("\n  }\n", fn_start) + 4
    script = SHELL_JS[start:end] + SHELL_JS[fn_start:fn_end] + (
        "console.log(JSON.stringify(cookIngredientLabel(" + json.dumps(ing) + ")));"
    )
    res = nodeharness.run_node(script)
    assert res.returncode == 0, res.stderr
    return json.loads(res.stdout.strip())


@needs_node
def test_the_ingredient_row_reads_the_label():
    assert _label({"item": "onion", "qty": "2", "item_label": "onions"}) == "2 onions"
    assert _label({"item": "onion", "qty": "1"}) == "1 onion"


def test_what_is_not_plainly_countable_stays_as_written():
    """The review round's regressions: a general plural rule got all of
    these wrong at the recipe's own serving size."""
    for item in (
        "garlic", "celery", "fish", "shrimp", "rice", "spinach", "ginger", "basil",
        "lemon juice", "avocado", "jalapeño", "cilantro", "chicken breast (boneless)",
        "black pepper",
    ):
        out = recipes.cooking_ingredients([{"item": item, "qty": "3"}], servings=4)[0]
        assert "item_label" not in out, item
    roma = recipes.cooking_ingredients([{"item": "tomato (Roma)", "qty": "2"}], servings=4)[0]
    assert roma["item_label"] == "tomatoes (Roma)"
