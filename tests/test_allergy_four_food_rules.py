"""
Emily's four food rules, decided 2026-11-09: a household with a celiac,
dairy-free, fish, shellfish, soy, sesame, pork or egg avoidance has library
dishes whose lists buy that food held back, and safe lookalikes are not.

  1. Thai curry pastes (red, green, yellow, panang, massaman) and kimchi are
     held for fish AND shellfish, unless the line says vegan, vegetarian or
     fish-free.
  2. Oats are held for gluten (celiac) unless the line says gluten-free.
     Oat milk is made from oats, so it is held too. Not for a wheat allergy.
  3. Andouille counts as pork unless the line names another meat (chicken
     or turkey andouille).
  4. Worcestershire, bouillon (cubes, base, stock powder), miso and
     tonkatsu sauce are held for gluten unless the line says gluten-free.
     Their fish and soy holds stay.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from app import agent, tools
from app.tools import allergen_gate, coordination
from app.tools import recipe_library as lib

BATCHES = Path(__file__).resolve().parents[1] / "data" / "recipe_library_batches"


def _held(label: str, line: str, name: str = "Dinner") -> bool:
    avoid = [{"member": "Sam", "label": label, "source": "dietary_restriction", "severity": "hard",
              "terms": coordination._match_terms(coordination._conflict_phrases(label))}]
    hits = coordination.check_meal_conflicts(name, ingredients=[{"item": line}], avoidances=avoid)
    return any(h.get("severity") == "hard" for h in hits)


# ---------- 1. Thai curry pastes and kimchi for fish and shellfish ----------

@pytest.mark.parametrize("label", ["fish allergy", "shellfish"])
@pytest.mark.parametrize("line", [
    "2 tbsp Thai red curry paste",
    "Green curry paste",
    "Yellow curry paste",
    "Panang curry paste",
    "3 tbsp massaman curry paste",
    "1 cup kimchi, chopped",
    "Red curry paste, not vegan",
])
def test_thai_curry_pastes_and_kimchi_are_held_for_fish_and_shellfish(label, line):
    """CATCH: shrimp paste and fish sauce were never written on the line."""
    assert _held(label, line), f"{line!r} went through {label}"


@pytest.mark.parametrize("label", ["fish allergy", "shellfish"])
@pytest.mark.parametrize("line", [
    "Vegan kimchi",
    "Red curry paste (vegetarian)",
    "Fish-free green curry paste",
    "Curry powder",
    "Curry leaves",
    "Japanese curry roux",
    "Korma curry paste",
])
def test_the_vegan_kinds_and_other_curries_pass(label, line):
    """GUARD: a line that says vegan, vegetarian or fish-free is believed,
    and curry powder and curry leaves are not a Thai paste."""
    assert not _held(label, line)


# ---------- 2. Oats for gluten ----------

@pytest.mark.parametrize("label", ["celiac", "gluten free"])
@pytest.mark.parametrize("line", ["Rolled oats", "1 cup old-fashioned oats", "Oatmeal", "Oat milk", "Oat flour"])
def test_oats_are_held_for_gluten(label, line):
    """CATCH: oats were no gluten word at all. Oat milk is held too, by
    choice: it is made from the same oats."""
    assert _held(label, line)


@pytest.mark.parametrize("label,line", [
    ("celiac", "Gluten-free rolled oats"),
    ("celiac", "GF oat milk"),
    ("celiac", "Goat cheese"),
    ("wheat allergy", "Rolled oats"),
])
def test_gluten_free_oats_and_a_wheat_allergy_pass(label, line):
    """GUARD: a gluten-free line is believed, "goat" is not oat, and oats
    are not wheat."""
    assert not _held(label, line)


# ---------- 3. Andouille is pork ----------

@pytest.mark.parametrize("label", ["pork", "no pork", "red meat"])
@pytest.mark.parametrize("line", ["Andouille sausage", "12 oz smoked andouille, sliced"])
def test_andouille_is_held_for_pork(label, line):
    assert _held(label, line)


@pytest.mark.parametrize("line", [
    "Chicken andouille sausage",
    "Smoked turkey andouille",
    "Andouille sausage (chicken)",
])
def test_chicken_and_turkey_andouille_pass_for_pork(line):
    """GUARD: a line that names another meat is that meat."""
    assert not _held("pork", line)


# ---------- 4. Worcestershire, bouillon, miso, tonkatsu sauce for gluten ----------

@pytest.mark.parametrize("line", [
    "Worcestershire sauce",
    "Chicken bouillon",
    "2 bouillon cubes",
    "Better Than Bouillon beef base",
    "1 tbsp chicken stock powder",
    "Vegetable stock cube",
    "White miso",
    "Tonkatsu sauce",
])
def test_the_four_jars_are_held_for_gluten(line):
    assert _held("celiac", line)


@pytest.mark.parametrize("line", [
    "Gluten-free Worcestershire sauce",
    "Gluten-free chicken bouillon",
    "Gluten-free white miso",
    "Gluten-free tonkatsu sauce",
    "Court-bouillon",
    "Chicken stock",
])
def test_gluten_free_jars_pass_for_gluten(line):
    """GUARD: the gluten-free bottle is believed; a court-bouillon is a
    poaching liquid, and plain stock was never on the card."""
    assert not _held("celiac", line)


@pytest.mark.parametrize("label,line", [
    ("fish allergy", "Gluten-free Worcestershire sauce"),
    ("soy", "Gluten-free white miso"),
])
def test_their_fish_and_soy_holds_stay(label, line):
    assert _held(label, line)


def test_tonkatsu_sauce_is_still_not_pork():
    assert not _held("pork", "Tonkatsu sauce")


# ---------- the lookalikes still pass ----------

@pytest.mark.parametrize("label,line", [
    ("dairy-free", "Butter lettuce"),
    ("shellfish", "Oyster mushrooms"),
    ("celiac", "Gram flour"),
    ("celiac", "Shirataki noodles"),
    ("soy", "Soybean oil"),
    ("fish allergy", "Curry powder"),
])
def test_the_lookalikes_still_pass(label, line):
    assert not _held(label, line)


def test_the_new_words_are_not_title_warnings():
    """GUARD: "red", "green" and "curry" in a title are not an allergen."""
    from app.tools import plan_quality
    words = plan_quality._allergen_title_words()
    assert not {"red", "green", "yellow", "curri", "curry", "kimchi"} & words


# ---------- the library ----------

def _copied(key: str):
    e = json.loads((BATCHES / f"{key}.json").read_text())
    lib.file_entry(e["dish_name"], **{**{k: v for k, v in e.items() if k != "dish_name"}, "origin": "seed"})
    name = e["dish_name"]
    tools.add_recipe(name=name, ingredients=[], details_pending=True, cuisine="")
    recipe = {**tools.get_recipe(name), "slot": "dinner"}
    return agent._copy_from_library(recipe, name, name, allergen_gate.hard_avoidances(), {})


@pytest.mark.parametrize("restriction,key", [
    ("shellfish", "kimchi-fried-rice"),
    ("shellfish", "massaman-curry"),
    ("celiac", "overnight-oats"),
    ("pork", "jambalaya"),
])
def test_the_library_s_dishes_are_not_copied_for_that_household(restriction, key):
    tools.add_member("Maya")
    tools.set_member_dietary_restrictions("Maya", [restriction])
    copied, clash = _copied(key)
    assert not copied and clash, key


# ---------- review, 2026-11-09: the escapes sit right against the food ----------

@pytest.mark.parametrize("label", ["fish allergy", "shellfish"])
@pytest.mark.parametrize("line", [
    "Non-vegetarian red curry paste",
    "Non-vegan kimchi",
    "Kimchi (not strictly vegan)",
    "Red curry paste (vegetarian brands are fish-free; most are not)",
    "Kimchi, or vegan kimchi for vegetarians",
])
def test_a_claim_that_is_not_right_against_the_paste_is_not_believed(label, line):
    """CATCH: the vegan/vegetarian/fish-free escape covered the whole line
    and only knew "not " as a negation."""
    assert _held(label, line), f"{line!r} went through {label}"


@pytest.mark.parametrize("line", [
    "Vegan kimchi",
    "Vegetarian Thai red curry paste",
    "Vegetarian massaman curry paste",
    "Red curry paste (vegetarian)",
    "Kimchi (vegan)",
])
def test_the_claim_right_before_or_in_a_bracket_after_is_believed(line):
    """GUARD: the forms the card means, including the three library lines."""
    assert not _held("shellfish", line)
    assert not _held("fish allergy", line)


def test_non_vegan_kimchi_is_held_for_a_vegetarian():
    assert _held("vegetarian", "Non-vegan kimchi")
    assert not _held("vegetarian", "Vegan kimchi")


@pytest.mark.parametrize("line", [
    "Worcester sauce",
    "Lea & Perrins",
    "Chicken base",
    "Beef base",
    "1 tsp vegetable base",
    "Hot pot soup base",
    "1 Oxo cube",
])
def test_the_jars_other_names_are_held_for_gluten(line):
    """CATCH: Worcester sauce, Lea & Perrins, a bouillon base without the
    word bouillon, and an Oxo cube all went through a celiac."""
    assert _held("celiac", line)


@pytest.mark.parametrize("line", [
    "Gluten-free Worcester sauce",
    "Gluten-free chicken base",
    "Base layer: rice",
])
def test_gluten_free_bases_and_a_bare_base_pass(line):
    assert not _held("celiac", line)


def test_lea_and_perrins_keeps_the_worcestershire_fish_hold():
    assert _held("fish allergy", "Lea & Perrins")


@pytest.mark.parametrize("line", [
    "Andouille sausage, chicken thighs",
    "Andouille sausage (chicken thighs on the side)",
])
def test_a_separate_meat_after_andouille_is_not_its_meat(line):
    """CATCH: the meat across a comma read as chicken andouille."""
    assert _held("pork", line)


def test_a_dish_named_chicken_andouille_is_judged_on_its_list():
    """CATCH: the name "Chicken Andouille Gumbo" passed over a pork
    andouille line; the exception is for an ingredient line only."""
    avoid = [{"member": "Sam", "label": "pork", "source": "dietary_restriction", "severity": "hard",
              "terms": coordination._match_terms(coordination._conflict_phrases("pork"))}]
    hits = coordination.check_meal_conflicts(
        "Chicken Andouille Gumbo", ingredients=[{"item": "Okra"}], avoidances=avoid)
    assert any(h["severity"] == "hard" for h in hits)


def test_a_claim_with_plain_descriptors_between_is_believed():
    """GUARD: the library's "Vegan gluten-free Thai red curry paste"."""
    assert not _held("shellfish", "Vegan gluten-free Thai red curry paste")
    assert _held("shellfish", "Vegan-friendly? No: Thai red curry paste")
