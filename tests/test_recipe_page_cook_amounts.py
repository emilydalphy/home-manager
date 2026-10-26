"""
A household's own recipe after it exists (defect hunt, 2026-10-22).

1. The servings stepper scales a count written with a glyph — "½" lemon,
   "1 ½" onions, "½ medium" — the way it scales "1/2" lime. Before, those
   stayed as written at every size and were listed under "Eyeball these —
   they don't scale automatically", while the step above said "Squeeze 1
   lemon" (scale_steps did scale it there).
2. Settings → Recipes → a recipe shows the amounts a cook reads (the ones
   cook mode and the stepper show), not the stored shopping amount: "2 tbsp
   olive oil", not "1 bottle"; a page's "1/4 cup + 2 tbsp" butter, not the
   "6 tbsp" the grocery list adds up.
"""
from __future__ import annotations

from app import tools


def _qtys(result):
    return {i["item"]: i["qty"] for i in result["scaled_ingredients"]}


def test_a_glyph_count_scales_like_its_ascii_twin():
    tools.add_recipe(
        "Citrus Salad",
        [
            {"item": "lemon", "qty": "½"},
            {"item": "lime", "qty": "1/2"},
            {"item": "onion", "qty": "1 ½"},
            {"item": "avocado", "qty": "1 1/2"},
            {"item": "red onion", "qty": "½ medium"},
            {"item": "shallot", "qty": "1/2 medium"},
        ],
        instructions=["Squeeze ½ lemon over everything."],
        default_servings=4,
    )
    doubled = tools.scale_recipe("Citrus Salad", 8)
    q = _qtys(doubled)
    assert q["lemon"] == q["lime"] == "1"
    assert q["onion"] == q["avocado"] == "3"
    assert q["red onion"] == q["shallot"] == "1 medium"
    assert doubled["unscaled_items"] == []
    # The step and the list say one number.
    assert doubled["scaled_instructions"] == ["Squeeze 1 lemon over everything."]

    halved = _qtys(tools.scale_recipe("Citrus Salad", 2))
    assert halved["lemon"] == halved["lime"] == "0.25"
    assert halved["onion"] == halved["avocado"] == "0.75"


def test_at_its_own_size_a_glyph_count_keeps_its_words():
    tools.add_recipe("Lemon Rice", [{"item": "lemon", "qty": "½"}, {"item": "rice", "qty": "1 ½ cups"}],
                     instructions=["Cook the rice."], default_servings=4)
    same = tools.scale_recipe("Lemon Rice", 4)
    assert _qtys(same) == {"lemon": "½", "rice": "1 ½ cups"}
    assert same["unscaled_items"] == []
    # A glyph measure was already scaled as text; unchanged by this fix.
    assert _qtys(tools.scale_recipe("Lemon Rice", 8))["rice"] == "3 cups"


def test_the_recipe_page_shows_the_cooking_amounts(signed_in):
    res = signed_in.post("/api/recipes/add", json={
        "name": "Oil Pasta",
        "ingredients": [
            {"item": "olive oil", "qty": "1 bottle"},
            {"item": "butter", "qty": "1/4 cup + 2 tbsp"},
            {"item": "spaghetti", "qty": "1 lb"},
        ],
        "instructions": ["Boil the spaghetti.", "Warm 2 tbsp olive oil and the butter."],
        "default_servings": 4,
    })
    assert res.status_code == 200, res.text
    rid = res.json()["recipe_id"]

    page = signed_in.get(f"/api/recipes/{rid}").json()
    rows = {i["item"]: i for i in page["ingredients"]}
    assert rows["olive oil"]["qty"] == "2 tbsp"
    assert rows["olive oil"]["shopping_qty"] == "1 bottle"
    assert rows["butter"]["qty"] == "1/4 cup + 2 tbsp"
    assert rows["spaghetti"]["qty"] == "1 lb"
    assert not any("cook_qty" in i for i in page["ingredients"])

    # The same amounts the stepper hands back at the recipe's own size.
    scaled = signed_in.get("/api/recipes/scale", params={"name": "Oil Pasta", "servings": 4}).json()
    assert [i["qty"] for i in page["ingredients"]] == [i["qty"] for i in scaled["scaled_ingredients"]]

    # Read, not stored: the grocery list still buys the bottle.
    stored = {i["item"]: i["qty"] for i in tools.get_recipe("Oil Pasta")["ingredients"]}
    assert stored["olive oil"] == "1 bottle"
    assert stored["butter"] == "6 tbsp"
