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
    assert len(DISHES) == 500
    assert Counter(d["cuisine"] for d in DISHES) == PROPOSED


def test_cuisines_are_exactly_the_ones_a_household_can_pick():
    onboarding = _select_cuisines("static/onboarding.html", r"const CUISINES")
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
