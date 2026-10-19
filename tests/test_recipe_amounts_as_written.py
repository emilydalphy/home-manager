"""
A recipe the household brought in shows its amounts as written (Emily,
2026-10-18: "Yes." — changing the 2026-09-13 rule).

The cook card's per-serving check (recipes.plausible_cooking_quantity) was
built to catch made-up amounts in AI-written recipes, and fired on real
ones too: a cake serving 4 with "1 ¼ cups sugar" showed ½ cup, "10 tbsp
butter" showed 2 tbsp. A line saved through the review sheet (a link, a
cookbook photo, or typed there) is now marked `verbatim` and the check
leaves its own qty alone. Anything the app wrote keeps the check.

Built with it: "1/4 cup + 2 tbsp flour" used to import as an item called
"+ 2 tbsp flour"; it is flour, 6 tbsp — one amount the grocery list merges.
"""
from __future__ import annotations

import datetime
import json

import pytest
from conftest import household_date

from app import db, recipe_import as ri, tools
from app.tools import recipes

from test_recipe_import import _html_response, _page, _wire


CAKE = [
    {"item": "Granulated sugar", "qty": "1 1/4 cups"},
    {"item": "Unsalted butter", "qty": "10 tbsp"},
    {"item": "All-purpose flour", "qty": "2 cups"},
    {"item": "Eggs", "qty": "2"},
]


def _save_through_the_sheet(client, name="Yellow Cake", **extra):
    body = {"name": name, "ingredients": CAKE, "instructions": ["Cream the butter and sugar.", "Bake."],
            "default_servings": 4, **extra}
    res = client.post("/api/recipes/add", json=body)
    assert res.status_code == 200, res.text
    return name


def _shown(name, servings=4):
    """What the cook card lists, read as (amount, unit): the card writes
    "1.25 cups" and shell.js's humanQtyText draws it as 1 ¼."""
    return {i["item"]: tools._parse_quantity(i["qty"]) for i in tools.scale_recipe(name, servings)["scaled_ingredients"]}


# ---------- criterion 1: the household's amounts, as written ----------

def test_a_typed_cake_shows_its_sugar_and_butter_as_written(signed_in):
    shown = _shown(_save_through_the_sheet(signed_in))
    assert shown["Granulated sugar"] == (1.25, "cup")
    assert shown["Unsalted butter"] == (10.0, "tbsp")


def test_a_cake_from_a_link_shows_its_amounts_as_written(signed_in, monkeypatch):
    page = json.dumps({
        "@type": "Recipe", "name": "Brown Butter Cake", "recipeYield": "4",
        "recipeIngredient": ["1 ¼ cups sugar", "10 tbsp butter", "2 cups flour"],
        "recipeInstructions": "Mix.\nBake.",
    })
    _wire(monkeypatch, {"/cake": _html_response(_page(page))})
    draft = signed_in.post("/api/recipes/import-url", json={"url": "https://bake.example.com/cake"}).json()["draft"]
    assert signed_in.post("/api/recipes/add", json=draft).status_code == 200
    saved = tools.get_recipe("Brown Butter Cake")["ingredients"]
    assert all(i.get("verbatim") for i in saved if i["item"] in ("sugar", "butter", "flour"))
    assert not any(i.get("cook_qty") for i in saved), "nothing of the app's own was written over the page"
    shown = _shown("Brown Butter Cake")
    assert (shown["sugar"], shown["butter"]) == ((1.25, "cup"), (10.0, "tbsp"))


def test_scaled_down_it_halves_the_recipes_own_amount(signed_in):
    shown = _shown(_save_through_the_sheet(signed_in), servings=2)
    assert shown["Unsalted butter"] == (5.0, "tbsp")


def test_the_morning_report_does_not_flag_a_household_recipe(signed_in):
    _save_through_the_sheet(signed_in)
    ings = tools.get_recipe("Yellow Cake")["ingredients"]
    assert recipes._implausible_lines(ings, 4) == []
    assert recipes._implausible_lines(ings, 4, as_written=True) == []


# ---------- criterion 2: AI-written recipes keep the check ----------

def test_a_recipe_the_app_wrote_still_gets_the_check():
    tools.add_recipe("Model Cake", [dict(i) for i in CAKE], instructions=["Bake."], default_servings=4)
    shown = _shown("Model Cake")
    assert shown["Granulated sugar"] != (1.25, "cup")
    assert shown["Unsalted butter"] != (10.0, "tbsp")


def test_a_line_the_app_added_from_the_steps_is_not_the_households(signed_in):
    body = {"name": "Buttered Rice", "ingredients": [{"item": "Rice", "qty": "1 cup"}],
            "instructions": ["Cook the rice.", "Stir in the butter."], "default_servings": 4}
    assert signed_in.post("/api/recipes/add", json=body).status_code == 200
    by_item = {i["item"].lower(): i for i in tools.get_recipe("Buttered Rice")["ingredients"]}
    assert by_item["rice"].get("verbatim") is True
    added = [i for k, i in by_item.items() if "butter" in k]
    assert added and not added[0].get("verbatim")


def test_a_cook_qty_the_app_wrote_is_still_checked_on_a_household_line():
    # "1 bunch" measures nothing, so the cook reads the fill path's amount
    # — which the app wrote, so it is held to the range as before.
    line = {"item": "Butter", "qty": "1 bunch", "cook_qty": "1 cup", "verbatim": True}
    assert recipes.cooking_ingredients([line], servings=2)[0]["qty"] != "1 cup"


def test_an_apple_pies_eight_apples_reach_the_grocery_list(signed_in):
    # The per-person COUNT ceiling would have bought 2 for a pie serving 2;
    # the household's recipe says 8 and the grocery ingest takes it.
    body = {"name": "Apple Pie", "ingredients": [{"item": "Apples", "qty": "8"}],
            "instructions": ["Bake."], "default_servings": 2}
    assert signed_in.post("/api/recipes/add", json=body).status_code == 200
    today = household_date(0)
    plan = tools.create_weekly_plan(today, day_count=1)["weekly_plan_id"]
    tools.plan_meal(today, "Apple Pie", slot="dinner", weekly_plan_id=plan)
    tools.approve_weekly_plan(plan, approved_by="Emily")
    conn = db.get_conn()
    rows = conn.execute("SELECT quantity FROM grocery_items WHERE LOWER(item) = 'apples'").fetchall()
    conn.close()
    assert [tools._parse_quantity(r["quantity"])[0] for r in rows] == [8.0]


# ---------- recipes already saved from a link ----------

def test_the_migration_marks_link_recipes_and_not_the_apps():
    lines = json.dumps([{"item": "sugar", "qty": "1 1/4 cups", "cook_qty": "1/2 cup"},
                        {"item": "butter", "qty": "2 tbsp", "added_from_steps": True}])
    conn = db.get_conn()
    try:
        conn.execute("INSERT INTO recipes (household_id, name, ingredients_json, source_url) "
                     "VALUES (1, 'Linked', ?, 'https://x.example/c')", (lines,))
        conn.execute("INSERT INTO recipes (household_id, name, ingredients_json) VALUES (1, 'Generated', ?)", (lines,))
        # A link recipe Change recipe has since rewritten (and nobody undid)
        # is the model's now; one whose rewrite was undone is the page's again.
        for name, outcome in (("Rewritten", "rewritten"), ("Undone", "undone")):
            rid = conn.execute("INSERT INTO recipes (household_id, name, ingredients_json, source_url) "
                               "VALUES (1, ?, ?, 'https://x.example/r')", (name, lines)).lastrowid
            conn.execute("INSERT INTO recipe_change_requests (household_id, recipe_id, outcome, previous_json) "
                         "VALUES (1, ?, ?, '{}')", (rid, outcome))
        conn.execute("PRAGMA user_version = %d" % (db._DATA_VERSION_VERBATIM_AMOUNTS - 1))
        db._run_once_data_migrations(conn)
        conn.commit()
        rows = {r["name"]: json.loads(r["ingredients_json"]) for r in conn.execute("SELECT name, ingredients_json FROM recipes")}
    finally:
        # Never leave the write lock held: every later test would wait on it.
        conn.rollback()
        conn.close()
    assert rows["Linked"][0]["verbatim"] is True
    assert "verbatim" not in rows["Linked"][1], "the app's line from the steps stays the app's"
    assert "verbatim" not in rows["Generated"][0]
    assert "verbatim" not in rows["Rewritten"][0]
    assert rows["Undone"][0]["verbatim"] is True
    # And the cook card reads the page's amount over the app's old guess.
    assert recipes.cooking_ingredients(rows["Linked"], servings=4)[0]["qty"] == "1 1/4 cups"


# ---------- criterion 3: "1/4 cup + 2 tbsp flour" ----------
# The draft and the cook card keep the page's words; the save gives the
# grocery list one amount it can add up (recipe_import.one_amount).

@pytest.mark.parametrize("line, item, written, summed", [
    ("1/4 cup + 2 tbsp flour", "flour", "1/4 cup + 2 tbsp", "6 tbsp"),
    ("¼ cup plus 2 tablespoons all-purpose flour", "all-purpose flour", "1/4 cup + 2 tablespoons", "6 tbsp"),
    ("1 cup + 2 tbsp. sugar", "sugar", "1 cup + 2 tbsp", "18 tbsp"),
    ("1 lb + 2 oz ground beef", "ground beef", "1 lb + 2 oz", "18 oz"),
    ("1 1/2 cups plus 2 tablespoons flour", "flour", "1 1/2 cups + 2 tablespoons", "26 tbsp"),
    # Same unit stays in it, as a cook writes it — not "1.333 cups".
    ("1 cup + 1/3 cup sugar", "sugar", "1 cup + 1/3 cup", "1 1/3 cups"),
    ("1/2 cup + 1/4 cup milk", "milk", "1/2 cup + 1/4 cup", "3/4 cup"),
    # The bracketed grams after the second amount stay off the item too.
    ("3/4 cup + 2 tbsp (105g) flour", "flour", "3/4 cup + 2 tbsp", "14 tbsp"),
])
def test_two_amounts_joined_by_a_plus_are_one_amount(line, item, written, summed):
    assert ri.split_ingredient_line(line) == {"item": item, "qty": written}
    assert ri.one_amount(written) == summed


def test_two_families_are_left_for_the_household_to_fix():
    assert ri.split_ingredient_line("1 cup + 2 eggs batter") == {"item": "+ 2 eggs batter", "qty": "1 cup"}
    assert ri.one_amount("1 cup + 2 eggs") is None
    assert ri.one_amount("2 cups") is None


def test_the_model_reads_plus_the_same_way():
    assert ri.normalise_amount("1/4 cup plus 2 tbsp.") == "1/4 cup + 2 tbsp"


def _cook_card_text(name, servings=4):
    return {i["item"]: i["qty"] for i in tools.scale_recipe(name, servings)["scaled_ingredients"]}


def test_the_flour_merges_on_the_grocery_list_and_shows_as_written_on_the_card(signed_in, monkeypatch):
    page = json.dumps({
        "@type": "Recipe", "name": "Shortbread", "recipeYield": "4",
        "recipeIngredient": ["1 1/2 cups plus 2 tablespoons almond flour", "10 tbsp butter"],
        "recipeInstructions": "Mix.\nBake.",
    })
    _wire(monkeypatch, {"/sb": _html_response(_page(page))})
    draft = signed_in.post("/api/recipes/import-url", json={"url": "https://bake.example.com/sb"}).json()["draft"]
    assert ("almond flour", "1 1/2 cups + 2 tablespoons") in [(i["item"], i["qty"]) for i in draft["ingredients"]]
    assert signed_in.post("/api/recipes/add", json=draft).status_code == 200
    flour = next(i for i in tools.get_recipe("Shortbread")["ingredients"] if i["item"] == "almond flour")
    assert (flour["qty"], flour["qty_as_written"]) == ("26 tbsp", "1 1/2 cups + 2 tablespoons")
    # The cook card: the page's words, and scaled as words.
    assert _cook_card_text("Shortbread")["almond flour"] == "1 1/2 cups + 2 tablespoons"
    assert _cook_card_text("Shortbread", 8)["almond flour"] == "3 cups + 4 tablespoons"
    # Almond flour, not plain flour: pantry-staples-assumed keeps plain flour
    # off the list as already at home (merge 2026-10-19).
    # The grocery list: one almond flour line with another meal's almond flour, no "+".
    tools.add_recipe("Pancakes", ingredients=[{"item": "almond flour", "qty": "1 cup", "category": "pantry"}])
    today = datetime.date.fromisoformat(household_date(0))
    plan = tools.create_weekly_plan(today.isoformat(), day_count=3)["weekly_plan_id"]
    tools.plan_meal(today.isoformat(), "Pancakes", slot="dinner", weekly_plan_id=plan)
    tools.plan_meal((today + datetime.timedelta(days=1)).isoformat(), "Shortbread", slot="dinner", weekly_plan_id=plan)
    tools.approve_weekly_plan(plan, approved_by="Emily")
    conn = db.get_conn()
    rows = conn.execute("SELECT item, quantity FROM grocery_items WHERE status = 'needed' AND item LIKE '%flour%'").fetchall()
    conn.close()
    assert len(rows) == 1 and rows[0]["item"].lower() == "almond flour"
    assert tools._parse_quantity(rows[0]["quantity"])[1] in ("cup", "tbsp")
    assert "+" not in rows[0]["quantity"]


# ---------- the mark never travels onto the model's lines (review, 2026-10-19) ----------

def test_add_recipe_takes_the_mark_off_a_line_from_any_other_door():
    tools.add_recipe("Copied Cake", [{"item": "Sugar", "qty": "1 1/4 cups", "verbatim": True,
                                      "qty_as_written": "1 cup + 1/4 cup"}], instructions=["Bake."])
    line = tools.get_recipe("Copied Cake")["ingredients"][0]
    assert "verbatim" not in line and "qty_as_written" not in line


def test_a_rewrite_handing_back_a_marked_line_saves_it_unmarked(signed_in):
    from test_recipe_change import TUE, _entry, _plan

    _save_through_the_sheet(signed_in)
    plan = _plan("draft")
    entry = _entry(plan, TUE, "Yellow Cake")
    spec = tools.rewrite_spec(entry)
    res = tools.apply_rewrite(spec, {
        "ingredients": [{"item": "Granulated sugar", "qty": "1 1/4 cups", "verbatim": True}],
        "instructions": ["Bake."], "default_servings": 4,
    }, "Less butter")
    assert res["status"] == "rewritten", res
    assert not any(i.get("verbatim") for i in tools.get_recipe("Yellow Cake")["ingredients"])


def test_the_rewrite_prompt_does_not_carry_the_marks(monkeypatch, signed_in):
    from app import agent
    from test_recipe_change import TUE, _entry, _plan

    _save_through_the_sheet(signed_in)
    entry = _entry(_plan("draft"), TUE, "Yellow Cake")
    seen = {}

    def fake(spec):
        seen["spec"] = spec
        return {"ingredients": [{"item": "Sugar", "qty": "1 cup"}], "instructions": ["Bake."], "default_servings": 4}

    monkeypatch.setattr(agent, "generate_recipe_details_llm", fake)
    agent.rewrite_meal_recipe(entry, "Less sugar")
    lines = seen["spec"]["current_recipe"]["ingredients"]
    assert lines and not any(k in i for i in lines for k in ("verbatim", "cook_qty", "qty_as_written"))
