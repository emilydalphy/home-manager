"""
Emily, 2026-11-09: "as a rule, if a dish has eggs in it, it typically
doesn't hold well for reheating so it can't be prepped. but it's not always.
for example, a spanish tortilla can be pre-made, and egg bites can be as
well. but an egg scramble or oyakodon can't. Make sure that if you suggest
it as something that can be made and then have leftover that the recipe
says it can be made ahead."
"""
import pytest

from app.tools import leftovers as L


@pytest.mark.parametrize("name", [
    "Oyakodon (Japanese Chicken and Egg Rice Bowl)",
    "Veggie Scrambled Eggs with Whole Wheat Toast",
    "Egg Scramble",
    "Spinach Omelette",
    "Spaghetti Carbonara",
    "Shakshuka",
    "Katsudon",
])
def test_fresh_egg_dishes_do_not_keep(name):
    assert L.is_egg_dish(name)
    assert not L.egg_dish_keeps(name)
    assert not L.keeps_as_leftovers({"meal_name": name})


@pytest.mark.parametrize("name", [
    "Spanish Tortilla",
    "Tortilla Española",
    "Egg White Bites",
    "Spinach and Feta Frittata",
    "Quiche Lorraine",
    "Hard-Boiled Eggs with Everything Seasoning",
    "Egg Salad Sandwiches",
    "Egg Fried Rice",
    "Ajitsuke Tamago",
])
def test_make_ahead_egg_dishes_keep(name):
    assert L.egg_dish_keeps(name)


@pytest.mark.parametrize("name", [
    "Chicken Tortilla Soup", "Eggplant Parmesan", "Swedish Meatballs", "Beef Chili",
])
def test_not_egg_dishes(name):
    assert not L.is_egg_dish(name)
    assert L.egg_dish_keeps(name)


def test_recipe_that_says_make_ahead_keeps():
    recipe = {"notes": "Make it ahead: keeps up to 3 days in the fridge."}
    assert L.egg_dish_keeps("Egg and Potato Hash", recipe)
    assert not L.egg_dish_keeps("Egg and Potato Hash", {})


@pytest.mark.parametrize("name", ["Banana Bread", "Tofu Scramble on Toast", "Paneer Bhurji"])
def test_baking_and_tofu_are_not_egg_dishes(name):
    # The library files banana bread under main_protein "eggs"; it keeps.
    assert not L.is_egg_dish(name, "eggs")


def test_soft_boiled_never_keeps():
    assert not L.egg_dish_keeps("Soft-Boiled Eggs with Toast Soldiers")


def test_keeper_gets_make_ahead_note():
    notes = L.with_make_ahead_note("Spanish Tortilla", "")
    assert notes == L.EGG_MAKE_AHEAD_NOTE
    # the note carries a reheat sentence, so the leftovers day card shows it
    assert L.reheat_note({"notes": notes})


def test_note_not_doubled_or_added_where_not_needed():
    said = "Keeps up to 4 days in the fridge."
    assert L.with_make_ahead_note("Frittata", said) == said
    assert L.with_make_ahead_note("Egg Scramble", "") == ""
    assert L.with_make_ahead_note("Beef Chili", "") == ""


def test_add_recipe_writes_make_ahead_note(tmp_db=None):
    from app.tools import recipes
    from app.db import get_conn
    name = "Test Spanish Tortilla 2026-11-09"
    try:
        recipes.add_recipe(name, [{"item": "Eggs", "qty": "6"}], instructions=["Cook."])
        conn = get_conn()
        row = conn.execute("SELECT notes FROM recipes WHERE name = ?", (name,)).fetchone()
        conn.close()
        assert L.EGG_MAKE_AHEAD_NOTE in (row["notes"] or "")
    finally:
        conn = get_conn()
        conn.execute("DELETE FROM recipes WHERE name = ?", (name,))
        conn.commit()
        conn.close()
