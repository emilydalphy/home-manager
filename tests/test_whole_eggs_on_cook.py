"""
An egg only comes whole on the Cook screen (defect hunt, 2026-10-29).

A 6-serving pasta bake cooked for 8 (Saturday plus Sunday's leftovers)
read "2 ⅔ eggs" on its Cook card, and its step said "Beat 2 ⅔ eggs": a
bare count scaled as plain arithmetic. Eggs, yolks and whites now take
the clove rule — the nearest whole one, never none — in the list and the
steps alike, as a count range ("2-3 eggs") already did.
"""
from app import tools
from app.tools import recipes


def _bake():
    tools.add_recipe("Egg Bake", default_servings=6, ingredients=[
        {"item": "large eggs", "qty": "2"},
        {"item": "egg yolks", "qty": "3"},
        {"item": "red onion", "qty": "1"},
        {"item": "egg noodles", "qty": "1"},
    ], instructions=["Beat 2 large eggs with 3 egg yolks."])


def _qty(scaled: dict) -> dict:
    return {i["item"]: i["qty"] for i in scaled["scaled_ingredients"]}


def test_eggs_round_to_a_whole_egg_on_the_list_and_in_the_step():
    _bake()
    scaled = tools.scale_recipe("Egg Bake", 8)
    q = _qty(scaled)
    assert q["large eggs"] == "3", "2 eggs x 8/6 is 2.67: three eggs, not 2 2/3"
    assert q["egg yolks"] == "4"
    assert scaled["scaled_instructions"] == ["Beat 3 large eggs with 4 egg yolks."]


def test_one_serving_still_gets_an_egg_and_other_counts_keep_their_fractions():
    _bake()
    q = _qty(tools.scale_recipe("Egg Bake", 1))
    assert q["large eggs"] == "1", "never none"
    q8 = _qty(tools.scale_recipe("Egg Bake", 9))
    assert q8["red onion"] == "1.5", "an onion can be halved — only eggs changed"
    assert q8["egg noodles"] == "1.5"
    assert not recipes._is_whole_egg("eggplant")
