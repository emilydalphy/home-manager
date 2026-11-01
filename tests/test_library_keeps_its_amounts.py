"""
Library recipes keep their own amounts on the cook card (Emily,
2026-10-31: "yes, library recipes keep their own amounts (exempt like
household-imported ones); the check stays on AI-written recipes").

The per-serving check was built to catch amounts the model made up. On the
shared library's researched set (origin "seed") it rewrote real ones:
Tonkatsu's deep-frying oil "4 cups" showed "2 tbsp", cookies' "1 cup
butter" showed "2 tbsp", while the step said the real amount.
"""
from __future__ import annotations

import json
from pathlib import Path

from app import agent, db, tools
from app.tools import allergen_gate, recipes
from app.tools import recipe_library as lib

BATCHES = Path(__file__).resolve().parents[1] / "data" / "recipe_library_batches"


def _file(key: str, origin: str = "seed") -> dict:
    e = json.loads((BATCHES / f"{key}.json").read_text())
    lib.file_entry(e["dish_name"], **{**{k: v for k, v in e.items() if k != "dish_name"}, "origin": origin})
    return e


def _copy(name: str) -> None:
    tools.add_recipe(name=name, ingredients=[], details_pending=True, cuisine="")
    recipe = {**tools.get_recipe(name), "slot": "dinner"}
    outcome, clashed = agent._copy_from_library(recipe, name, name, allergen_gate.hard_avoidances(), {})
    assert outcome and outcome["ok"] and not clashed


def _card(name: str, servings: int = 4) -> dict:
    """What the cook card lists, read as (amount, unit)."""
    return {i["item"]: tools._parse_quantity(i["qty"]) for i in tools.scale_recipe(name, servings)["scaled_ingredients"]}


def test_tonkatsu_from_the_library_fries_in_four_cups_of_oil():
    e = _file("tonkatsu")
    _copy(e["dish_name"])
    assert _card(e["dish_name"])["Vegetable oil"] == (4.0, "cup")


def test_cookies_from_the_library_keep_their_cup_of_butter():
    e = _file("chocolate-chip-cookies")
    butter = next(i for i in e["ingredients"] if "butter" in i["item"].lower())
    _copy(e["dish_name"])
    assert _card(e["dish_name"])[butter["item"]] == tools._parse_quantity(butter["qty"])


def test_the_librarys_own_cook_amount_beside_a_package_still_shows():
    """A seed line bought as a package carries the library's own cook_qty
    ("1 bottle" of fish sauce, cook 2 tsp): that is the library's amount
    too, and the card shows it rather than the bottle."""
    recipe_lines = [
        {"item": "Fish sauce", "qty": "1 bottle", "category": "pantry", "cook_qty": "2 tsp"},
        {"item": "Vegetable oil", "qty": "4 cups", "category": "pantry"},
    ]
    lib.file_entry("Test Fried Fish", ingredients=recipe_lines,
                   instructions=["Heat the 4 cups oil.", "Season with 2 tsp fish sauce."],
                   default_servings=4, origin="seed")
    _copy("Test Fried Fish")
    card = _card("Test Fried Fish")
    assert card["Fish sauce"] == (2.0, "tsp")
    assert card["Vegetable oil"] == (4.0, "cup")


def test_an_ai_written_library_entry_keeps_the_check():
    """The check stays on AI-written recipes: an entry a household's own
    recipe pass filed ("written") is the model's, and its frying oil is
    still held to the table."""
    e = _file("tonkatsu", origin="written")
    _copy(e["dish_name"])
    assert _card(e["dish_name"])["Vegetable oil"] != (4.0, "cup")


def test_a_model_handing_the_lines_back_does_not_keep_the_mark():
    marked = [{"item": "Vegetable oil", "qty": "4 cups", "library_amount": True}]
    assert "library_amount" not in recipes.without_household_marks(marked)[0]
    assert recipes.cooking_ingredients(recipes.without_household_marks(marked), servings=4)[0]["qty"] != "4 cups"


def test_a_line_the_app_added_from_the_steps_keeps_the_check():
    """Marked before the list is completed, as add_recipe does: a line the
    app adds from the steps is its own guess."""
    lib.file_entry("Test Fry Bread", ingredients=[{"item": "Flour", "qty": "2 cups", "category": "pantry"}],
                   instructions=["Mix the flour with water.", "Fry in 4 cups vegetable oil."],
                   default_servings=4, origin="seed")
    _copy("Test Fry Bread")
    lines = tools.get_recipe("Test Fry Bread")["ingredients"]
    assert next(i for i in lines if i["item"] == "Flour").get("library_amount") is True
    for added in (i for i in lines if i.get("added_from_steps")):
        assert not added.get("library_amount")


def test_the_migration_puts_the_librarys_amounts_back_on_copies_already_saved():
    e = _file("tonkatsu")
    entry_id = lib.base_recipe(e["dish_name"])["id"]
    lines = [
        {"item": "Vegetable oil", "qty": "4 cups", "category": "pantry", "cook_qty": "2 tbsp"},
        {"item": "Panko", "qty": "9 cups", "category": "pantry"},  # not the library's line
        {"item": "Salt", "qty": "1 tsp", "added_from_steps": True},
    ]
    conn = db.get_conn()
    try:
        conn.execute("INSERT INTO recipes (household_id, name, ingredients_json, library_id) "
                     "VALUES (1, 'Old Copy', ?, ?)", (json.dumps(lines), entry_id))
        rid = conn.execute("INSERT INTO recipes (household_id, name, ingredients_json, library_id) "
                           "VALUES (1, 'Rewritten Copy', ?, ?)", (json.dumps(lines), entry_id)).lastrowid
        conn.execute("INSERT INTO recipe_change_requests (household_id, recipe_id, outcome, previous_json) "
                     "VALUES (1, ?, 'rewritten', '{}')", (rid,))
        conn.execute("PRAGMA user_version = %d" % (db._DATA_VERSION_LIBRARY_AMOUNTS - 1))
        db._run_once_data_migrations(conn)
        conn.commit()
        rows = {r["name"]: json.loads(r["ingredients_json"])
                for r in conn.execute("SELECT name, ingredients_json FROM recipes WHERE name LIKE '%Copy'")}
    finally:
        conn.rollback()
        conn.close()
    oil = rows["Old Copy"][0]
    assert oil["library_amount"] is True and "cook_qty" not in oil
    assert "library_amount" not in rows["Old Copy"][1]
    assert "library_amount" not in rows["Old Copy"][2]
    assert "library_amount" not in rows["Rewritten Copy"][0]
    assert recipes.cooking_ingredients(rows["Old Copy"], servings=4)[0]["qty"] == "4 cups"
