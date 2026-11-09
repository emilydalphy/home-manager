"""
A dish that needs a special pan, with no everyday pan that works, is cut
(Emily, 2026-11-09, on the 1,000-recipe spot-check page: "Call out that it
needs a real pan. If there is an alternative pan that works you can leave
it. if not just cut them").
"""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CUT = ("Takoyaki", "Bungeoppang", "Khanom Krok", "Egg Waffles", "Murukku", "Paniyaram",
       "Thai Coconut Pancakes")


def test_the_special_pan_dishes_stay_cut():
    seed = {d["dish_name"] for d in json.loads((ROOT / "data" / "recipe_library_seed.json").read_text())["dishes"]}
    for name in CUT:
        slug = name.lower().replace(" ", "-")
        assert name not in seed, name
        assert not (ROOT / "data" / "recipe_library_batches" / f"{slug}.json").exists(), name
        assert not (ROOT / "static" / "plates" / f"{slug}.svg").exists(), name


def test_paletas_name_an_everyday_stand_in_for_popsicle_molds():
    recipe = json.loads((ROOT / "data" / "recipe_library_batches" / "paletas-de-fresa.json").read_text())
    assert any("paper cups" in step for step in recipe["instructions"])
