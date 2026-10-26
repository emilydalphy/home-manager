"""
Shared Recipe Library: the 500-dish starter list (Loop Board, Emily
2026-10-18 — "500 to start", "cover all cuisines that are the options in
the select", "give me a few samples to review first").

The list is data (data/recipe_library_seed.json). These pin what makes it
usable as a seed: 500 dishes, every cuisine the household can pick and no
other, keys that are the library's own key for the dish (so a seeded dish
is found when the planner names it), and no two dishes the library would
file as one.
"""
from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path

from app.tools import allergen_gate
from app.tools.recipe_research import dish_key

ROOT = Path(__file__).resolve().parent.parent
SEED = json.loads((ROOT / "data" / "recipe_library_seed.json").read_text())
DISHES = SEED["dishes"]

# The split proposed on the card. A change to it is a change to this line,
# on purpose — the summary Emily approves names these numbers.
PROPOSED = {
    "American": 70, "Italian": 60, "Mexican": 45, "Indian": 45, "Chinese": 40,
    "Thai": 30, "Japanese": 30, "Greek": 25, "Korean": 25, "Vietnamese": 25,
    "French": 25, "Lebanese": 20, "Spanish": 20, "Turkish": 20, "Ethiopian": 20,
}


def _select_cuisines(path: str, var: str) -> list[str]:
    text = (ROOT / path).read_text()
    block = re.search(var + r"\s*=\s*\[(.*?)\]", text, re.S).group(1)
    return re.findall(r"'([^']+)'", block)


def test_five_hundred_dishes_split_as_proposed():
    # 500 to start, split as proposed; 200 more on 2026-10-20 (Emily: "add
    # another 200 recipes"), 45 gap-fillers the same day, and 100 more on
    # 2026-10-25 ("make another 200 recipes": the paused 100 plus 100 new),
    # still only the cuisines a household can pick.
    assert len(DISHES) == 845
    assert Counter(d["cuisine"] for d in DISHES[:500]) == PROPOSED
    assert set(d["cuisine"] for d in DISHES[500:]) <= set(PROPOSED)


def test_cuisines_are_exactly_the_ones_a_household_can_pick():
    # Onboarding's screens live in setup-screens.js since re-plan Option B
    # (merge 2026-10-19).
    onboarding = _select_cuisines("static/setup-screens.js", r"const CUISINES")
    what_we_know = _select_cuisines("static/shell.js", r"var WWK_CUISINES")
    assert onboarding == what_we_know == SEED["cuisines"]
    assert set(PROPOSED) == set(onboarding)


def test_every_key_is_the_library_key_and_no_dish_is_there_twice():
    for d in DISHES:
        assert d["dish_key"] == dish_key(d["dish_name"]), d
    keys = Counter(d["dish_key"] for d in DISHES)
    assert [k for k, n in keys.items() if n > 1] == []


def test_names_are_what_the_writer_would_file_them_under():
    # The recipe pass writes a "Vegan …" name up under its plain name, and
    # dish_key does not fold accents ("Marinières" and "Marinieres" are two
    # keys) — so names are plain and ASCII, as a planner would type them.
    for d in DISHES:
        name = d["dish_name"]
        assert allergen_gate.plain_dish_name(name) == name, name
        assert name.isascii() and name == name.strip(), name


def test_every_dish_says_meal_protein_and_why():
    assert set(Counter(d["meal"] for d in DISHES)) == {"breakfast", "lunch", "dinner", "snack"}
    for d in DISHES:
        assert d["main_protein"] and d["main_protein"] == d["main_protein"].lower(), d
        why = d["why"]
        assert why and len(why) <= 100 and "!" not in why, d
        assert not re.search(r"\b(delicious|simply|amazing)\b", why, re.I), d


# ---------- the six samples Emily reviews first ----------

SAMPLES = json.loads((ROOT / "data" / "recipe_library_samples.json").read_text())["samples"]


def test_six_samples_one_per_named_cuisine_each_on_the_list():
    assert sorted(s["cuisine"] for s in SAMPLES) == sorted(
        ["American", "Italian", "Indian", "Mexican", "Thai", "Ethiopian"])
    keys = {d["dish_key"]: d for d in DISHES}
    for s in SAMPLES:
        listed = keys[dish_key(s["dish_name"])]
        assert (listed["cuisine"], listed["main_protein"]) == (s["cuisine"], s["main_protein"])


def test_each_sample_is_a_whole_recipe_with_its_lead_first():
    from app.tools import recipe_library, recipe_research
    allowed = set(recipe_library.file_entry.__kwdefaults__) | {"dish_name"}
    for s in SAMPLES:
        assert set(s) <= allowed, set(s) - allowed
        assert s["origin"] == "seed" and s["ingredients"] and s["instructions"]
        assert s["default_servings"] and s["prep_time_minutes"] and s["cook_time_minutes"]
        # The lead is the app's own rule, not a hand pick: re-running it on
        # the sources as they stand changes nothing.
        assert len(s["sources"]) >= 2 and s["sources"][0]["lead"]
        again = recipe_research.pick_lead([{k: v for k, v in x.items() if k != "lead"} for x in s["sources"]])
        assert [x["url"] for x in again] == [x["url"] for x in s["sources"]]
        assert all(x["url"].startswith("https://") and x["rating_count"] for x in s["sources"])


def test_no_name_is_refused_as_a_households_own():
    # The library refuses a name that reads as someone's ("Nana's dal",
    # "Kevin's Chili"); the seed is filed in no household's name, so only
    # the name is checked. Every possessive on the list is a known dish.
    from app.tools.recipe_library import private_dish_name
    names = [d["dish_name"] for d in DISHES] + [s["dish_name"] for s in SAMPLES]
    assert [n for n in names if private_dish_name(n, household=False)] == []
