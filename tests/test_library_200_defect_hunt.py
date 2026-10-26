"""
Defect hunt over the 200 dishes the shared recipe library just grew by
(2026-10-26). What a household hits when these land in a week:

  1. The allergy gate (coordination.check_meal_conflicts) missed things the
     dishes' own lists buy: hamburger buns, bolillo and telera rolls,
     ladyfingers, frozen parathas, Shaoxing wine, oyster sauce and
     gochujang for gluten; Mexican crema for dairy; hake for fish; a frozen
     seafood mix for shellfish; doenjang and fermented black beans for soy;
     furikake for sesame; baby back ribs for pork. And it held back dishes
     that were safe: butter lettuce as dairy (Bulgogi, Lettuce Wraps),
     oyster mushrooms as shellfish, tonkatsu sauce as pork, gram flour and
     rice vermicelli as gluten.
  2. Their Indian, Korean and Turkish spice jars were not spice-rack
     things, so the list asked for "Kashmiri chili powder · 0.25 tsp" in
     an aisle instead of one jar in Spices.
  3. Package words the shopping lines use rendered unpluralised ("2
     package (8 oz)", "2 wedge", "2 piece") and their plurals never parsed
     back, so "1 stalk" and "2 stalks" could not add up.

Every test is a CATCH (red on main) unless it says GUARD.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.tools import coordination, quantities, spices
from app.tools import grocery as _grocery

BATCHES = Path(__file__).resolve().parent.parent / "data" / "recipe_library_batches"


def _avoid(label: str) -> list[dict]:
    return [{"member": "Sam", "label": label, "source": "dietary_restriction", "severity": "hard",
             "terms": coordination._match_terms(coordination._conflict_phrases(label))}]


def _line_clashes(label: str, line: str, name: str = "Dinner") -> bool:
    hits = coordination.check_meal_conflicts(name, ingredients=[{"item": line}], avoidances=_avoid(label))
    return any(h.get("severity") == "hard" for h in hits)


def _dish(key: str) -> dict:
    return json.loads((BATCHES / f"{key}.json").read_text())


def _dish_clashes(label: str, key: str) -> bool:
    d = _dish(key)
    hits = coordination.check_meal_conflicts(d["dish_name"], ingredients=d["ingredients"], avoidances=_avoid(label))
    return any(h.get("severity") == "hard" for h in hits)


# ---------- 1. the allergy gate: what the library's lists buy ----------

@pytest.mark.parametrize("label,line", [
    ("gluten", "Hamburger buns"),
    ("gluten", "Telera rolls"),
    ("gluten", "Bolillo rolls"),
    ("gluten", "Baguette"),
    ("gluten", "Ladyfingers"),
    ("gluten", "Frozen parathas"),
    ("gluten", "Shaoxing wine"),
    ("gluten", "Oyster sauce"),
    ("gluten", "Gochujang"),
    ("gluten", "Egg roll wrappers"),
    ("gluten", "Corn muffin mix"),
    ("gluten", "Frozen deep-dish pie crust"),
    ("gluten", "Dried soba noodles"),
    ("gluten", "Vermicelli"),
    ("celiac", "English muffins"),
    ("dairy", "Mexican crema"),
    ("fish", "Hake fillets"),
    ("shellfish", "Frozen seafood mix"),
    ("soy", "Doenjang"),
    ("soy", "Fermented black beans"),
    ("soy", "Gochujang"),
    ("sesame", "Furikake"),
    ("pork", "Baby back ribs"),
    ("egg", "Ladyfingers"),
])
def test_a_library_line_that_is_the_allergen_is_held(label, line):
    assert _line_clashes(label, line), f"{line!r} went through a {label} avoidance"


@pytest.mark.parametrize("label,line", [
    ("dairy", "Butter lettuce"),
    ("shellfish", "Oyster mushrooms"),
    ("shellfish", "Vegetarian oyster sauce"),
    ("pork", "Tonkatsu sauce"),
    ("gluten", "Gram flour"),
    ("gluten", "Rice vermicelli noodles"),
    ("gluten", "Bean thread noodles"),
    ("gluten", "Gluten-free oyster sauce"),
    ("soy", "Shrimp paste in soybean oil"),
])
def test_a_library_line_named_for_an_allergen_it_lacks_is_not_held(label, line):
    assert not _line_clashes(label, line), f"{line!r} was held for {label}"


@pytest.mark.parametrize("label,line,name", [
    # GUARDS: the things the new words must not reach.
    ("gluten", "Rice noodles", "Bun Cha"),        # Vietnamese bún is rice noodles
    ("gluten", "Eggs", "Egg Muffin Cups"),        # no bare "muffin"
    ("gluten", "Rice paper wrappers", "Fresh Spring Rolls"),
    ("soy", "Black beans", "Black Bean Burgers"),
    ("dairy", "Balsamic crema", "Caprese Salad"),
])
def test_the_new_words_stop_where_the_food_stops(label, line, name):
    assert not _line_clashes(label, line, name)


def test_whole_library_dishes_the_gate_now_reads_right():
    # On main a celiac household could be planned these on a bun, a roll
    # and ladyfingers; and a dairy-free one lost Bulgogi to butter lettuce.
    assert _dish_clashes("gluten", "smash-burgers")
    assert _dish_clashes("gluten", "chicken-torta")
    assert _dish_clashes("gluten", "tiramisu")
    assert _dish_clashes("gluten", "kimchi-fried-rice")
    assert not _dish_clashes("dairy", "bulgogi")
    assert not _dish_clashes("gluten", "glass-noodle-salad")
    assert not _dish_clashes("shellfish", "chinese-eggplant-with-garlic-sauce")


# ---------- 2. the library's spice jars are spice-rack things ----------

@pytest.mark.parametrize("name", [
    "Kashmiri chili powder", "Kasuri methi", "Asafoetida", "Chaat masala", "Amchur",
    "Ajwain (carom seeds)", "Gochugaru", "Aleppo pepper", "Shichimi togarashi",
    "Black mustard seeds", "Ground Sichuan pepper", "Pav bhaji masala", "Sambar powder",
])
def test_a_library_spice_waits_in_spices_not_an_aisle(name):
    assert spices.assumed_section(name) == spices.SECTION_SPICES


@pytest.mark.parametrize("name", ["Fennel", "Cloves (garlic)", "Pepper (bell)", "Masala dosa batter"])
def test_guard_produce_and_batters_stay_in_the_aisles(name):
    assert spices.assumed_section(name) is None


# ---------- 3. package words add up and read as English ----------

def test_package_words_pluralise_on_the_list():
    assert quantities._format_quantity(2, "package (8 oz)") == "2 packages (8 oz)"
    assert quantities._format_quantity(2, "piece") == "2 pieces"
    assert quantities._format_quantity(1, "wedge") == "1 wedge"


def test_package_plurals_add_up_with_their_singular():
    assert _grocery._try_consolidate_quantity("1 stalk", "2 stalks") == ("3 stalks", True)
    # Three pastas' Parmesan: "2 wedge + 1 wedge (4 oz)" on main.
    assert _grocery._try_consolidate_quantity("2 wedges", "1 wedge (4 oz)") == ("3 wedges (4 oz)", True)


def test_every_library_shopping_unit_is_one_the_list_knows():
    """The two lines that weren't (Ham bone "1 meaty", Yakisoba noodles "4
    servings") were the recipe data, fixed in the data. A new batch file
    with a unit the list can't read fails here, not on someone's phone."""
    known = (set(quantities._measure_units()) | set(quantities._CONTAINER_UNIT_PLURALS)
             | {"inch piece", "thick slice", "berries", "leaves", "pots"})
    odd = []
    for path in sorted(BATCHES.glob("*.json")):
        for line in json.loads(path.read_text())["ingredients"]:
            parsed = quantities._parse_quantity(line.get("qty") or "")
            if not parsed or not parsed[1]:
                continue
            head = quantities._split_package_size(parsed[1])[0]
            if head not in known and quantities._normalize_container_word(head) not in known:
                odd.append(f"{path.stem}: {line['item']} {line['qty']!r}")
    assert odd == []
