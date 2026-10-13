"""
An allergen a recipe's STEPS use but its ingredient list forgot
(Loop Board, decided 2026-10-12 — Option B plus A's soft variant).

The live case: Thai Basil Chicken Stir-Fry, "Beat two eggs and scramble
them in the wok", no egg on the list. With "allergy: eggs" on Sam the
approval check said nothing at all, because it reads the list and never
the method. Now:

  B. a recipe saved with steps that use something the list hasn't got
     gets it ADDED to the list — so the gate sees it as an ordinary hard
     clash, and the shopping list buys it;
  A. (backstop, for recipes saved before) the plan check reads the steps
     too, and a must-avoid found ONLY there is a SOFT line, never a block.
"""
import datetime
import json

import pytest
from conftest import household_today

from app import tools
from app.db import get_conn
from app.tools import recipes as _recipes

STIR_FRY_STEPS = [
    "Beat two eggs and scramble them in the wok, then set aside.",
    "Stir-fry the chicken until cooked through, then add the basil.",
    "Serve over the jasmine rice.",
]
STIR_FRY_LIST = [
    {"item": "Chicken thighs", "qty": "1.5 lb"},
    {"item": "Thai basil", "qty": "1 cup"},
    {"item": "Jasmine rice", "qty": "1.5 cups"},
]


@pytest.fixture
def sam_avoids_eggs():
    tools.add_member("Sam")
    tools.set_member_dietary_restrictions("Sam", ["allergy: eggs"])


def _plan_with(meal: str) -> int:
    today = household_today()
    monday = today - datetime.timedelta(days=today.weekday())
    plan = tools.create_weekly_plan(monday.isoformat())
    tools.plan_meal(today.isoformat(), meal, slot="dinner", weekly_plan_id=plan["weekly_plan_id"])
    return plan["weekly_plan_id"]


def test_saving_a_recipe_puts_the_steps_egg_on_its_list():
    tools.add_recipe("Thai Basil Chicken Stir-Fry", ingredients=STIR_FRY_LIST,
                     instructions=STIR_FRY_STEPS, default_servings=4)
    saved = tools.get_recipe("Thai Basil Chicken Stir-Fry")
    eggs = [i for i in saved["ingredients"] if i["item"].lower() == "eggs"]
    assert eggs, saved["ingredients"]
    # The step's own amount, not the table's four.
    assert eggs[0]["qty"] == "2"
    # Acceptance 4: the morning report's finding is gone for a recipe saved
    # through this.
    check = tools.check_steps_ingredients_consistency(
        saved["ingredients"], saved["instructions"], saved["default_servings"])
    assert check["missing_from_list"] == []


def test_a_saved_recipe_whose_steps_use_eggs_is_a_hard_clash_at_approval(sam_avoids_eggs):
    tools.add_recipe("Thai Basil Chicken Stir-Fry", ingredients=STIR_FRY_LIST,
                     instructions=STIR_FRY_STEPS, default_servings=4)
    found = tools.check_plan_conflicts(_plan_with("Thai Basil Chicken Stir-Fry"))
    hard = [c for c in found["conflicts"] if c["severity"] == "hard"]
    assert hard and hard[0]["member"] == "Sam"
    assert found["settle"] is not None


def test_a_recipe_saved_before_the_fix_gets_a_soft_steps_line_not_a_block(sam_avoids_eggs):
    tools.add_recipe("Thai Basil Chicken Stir-Fry", ingredients=STIR_FRY_LIST,
                     instructions=STIR_FRY_STEPS, default_servings=4)
    # As it sits on the live app: the list without the egg.
    conn = get_conn()
    conn.execute("UPDATE recipes SET ingredients_json = ? WHERE name = ?",
                 (json.dumps(STIR_FRY_LIST), "Thai Basil Chicken Stir-Fry"))
    conn.commit()
    conn.close()
    found = tools.check_plan_conflicts(_plan_with("Thai Basil Chicken Stir-Fry"))
    assert [c["severity"] for c in found["conflicts"]] == ["soft"]
    assert found["conflicts"][0]["steps_only"] is True
    assert found["settle"] is None and found["note"] is None
    assert "steps" in found["soft_note"] and "Sam is allergic to" in found["soft_note"]
    assert "eggs" in found["soft_note"].lower()


@pytest.mark.parametrize("step, word", [
    ("Swap the butter for oil if you prefer.", "butter"),
    ("This works without the eggs too.", "eggs"),
    ("No need for cream; the coconut milk does the work.", "cream"),
])
def test_what_a_step_leaves_out_is_neither_added_nor_a_clash(step, word):
    tools.add_member("Sam")
    tools.set_member_dietary_restrictions("Sam", [f"allergy: {word}"])
    steps = [step, "Simmer the coconut milk with the chicken."]
    listed = [{"item": "Chicken thighs", "qty": "1.5 lb"}, {"item": "Coconut milk", "qty": "1 can"}]
    assert _recipes.complete_ingredient_list(listed, steps, 4) == listed
    assert tools.check_meal_conflicts("Coconut Chicken", ingredients=listed, instructions=steps) == []


def test_a_generic_step_word_does_not_buy_a_second_thing():
    # "the cheese" over a list with Parmesan is the Parmesan.
    listed = [{"item": "Parmesan", "qty": "1/2 cup"}, {"item": "Spaghetti", "qty": "12 oz"}]
    steps = ["Boil the pasta.", "Toss with the cheese."]
    assert _recipes.complete_ingredient_list(listed, steps, 4) == listed


# --- the review's round (2026-10-13) ---------------------------------------

DAIRY_SHELLFISH = ["allergy: dairy", "allergy: shellfish", "allergy: eggs"]


@pytest.mark.parametrize("step", [
    "Use almond milk instead of milk.",          # a plant milk is no milk
    "Pour in the oat milk and stir.",
    "If you don't have butter, use oil.",        # the household's call
    "Butter is optional.",
    "Fry the chicken in butter or oil until golden.",
    "Serve with sour cream or yogurt, if you like.",
    "Leave the shrimp out for the kids.",        # split "leave … out"
    "This batter is egg-free and dairy-free.",
])
def test_a_choice_or_an_absence_in_a_step_adds_nothing_and_clashes_nothing(step):
    tools.add_member("Sam")
    tools.set_member_dietary_restrictions("Sam", DAIRY_SHELLFISH)
    listed = [{"item": "Chicken thighs", "qty": "1 lb"}]
    assert _recipes.complete_ingredient_list(listed, [step], 4) == listed
    assert tools.check_meal_conflicts("Dish", ingredients=listed, instructions=[step]) == []


def test_swapping_IN_peanut_butter_is_a_peanut_the_note_sees():
    tools.add_member("Sam")
    tools.set_member_dietary_restrictions("Sam", ["allergy: peanuts"])
    listed = [{"item": "Tahini", "qty": "1/4 cup"}, {"item": "Noodles", "qty": "12 oz"}]
    steps = ["Swap in peanut butter for the tahini if you like.", "Toss the noodles with the sauce."]
    found = tools.check_meal_conflicts("Sesame Noodles", ingredients=listed, instructions=steps)
    assert [(c["restriction"], c["steps_only"]) for c in found] == [("allergy: peanuts", True)]
    # A swap is a suggestion: nothing is bought for it.
    assert _recipes.complete_ingredient_list(listed, steps, 4) == listed


@pytest.mark.parametrize("step", ["Crack an egg into each bowl.", "Beat one egg with the milk."])
def test_a_single_egg_in_a_step_puts_eggs_on_the_list(step):
    listed = [{"item": "Rice", "qty": "1 cup"}, {"item": "Milk", "qty": "1 cup"}]
    added = _recipes.complete_ingredient_list(listed, [step], 4)[len(listed):]
    assert [(a["item"], a["qty"]) for a in added] == [("Eggs", "1")]


def test_pantry_odds_and_ends_do_not_go_on_the_shopping_list():
    # The review's recipe: flour to dust, a pinch of sugar, a splash of soy
    # sauce, a knob of butter "or" oil, a squeeze of lemon, parsley to
    # garnish, rice "or" bread "if you like" — none of it is a purchase.
    tools.add_recipe("Lemon Chicken Cutlets", ingredients=[
        {"item": "Chicken breast", "qty": "1.5 lb"}, {"item": "Green beans", "qty": "1 lb"}],
        instructions=[
            "Season the chicken with salt and pepper, then dust with flour.",
            "Fry in a knob of butter or a splash of olive oil until golden.",
            "Deglaze with a splash of soy sauce and a pinch of sugar; finish with a squeeze of lemon.",
            "Boil the green beans in salted water. Garnish with parsley; serve with rice or bread if you like.",
        ], default_servings=4)
    items = [i["item"] for i in tools.get_recipe("Lemon Chicken Cutlets")["ingredients"]]
    assert items == ["Chicken breast", "Green beans"]
    # The allergy note still reads the flour the steps dust with.
    tools.add_member("Sam")
    tools.set_member_dietary_restrictions("Sam", ["allergy: wheat"])
    recipe = tools.get_recipe("Lemon Chicken Cutlets")
    found = tools.check_meal_conflicts("Lemon Chicken Cutlets", ingredients=recipe["ingredients"],
                                       instructions=recipe["instructions"])
    assert [c.get("steps_only") for c in found] == [True]


@pytest.mark.skipif(__import__("shutil").which("node") is None, reason="node renders the line")
def test_the_draft_root_draws_the_soft_line_and_an_approved_week_does_not():
    # The review's finding: the line reached the API and no screen, because
    # the draft root renders through reviewStepHtml, which never read it.
    from pathlib import Path
    from test_week_seven_tiles import _extract, _run_node
    shell = (Path(__file__).resolve().parent.parent / "static" / "shell.js").read_text(encoding="utf-8")
    assert "draftSoftNoteHtml(data)" in _extract("reviewStepHtml", shell)
    line = "The steps for Tuesday’s Thai Basil Chicken Stir-Fry use eggs, which Sam is allergic to."
    out = _run_node(
        _extract("escapeHtml", shell) + "\n" + _extract("weekPlanState", shell) + "\n"
        + _extract("draftSoftNoteHtml", shell) + "\n"
        + "var line = " + json.dumps(line) + ";\n"
        + "console.log(JSON.stringify([draftSoftNoteHtml({weekly_plan_id: 1, days: [{}], status: 'draft', soft_note: line}),"
        + " draftSoftNoteHtml({weekly_plan_id: 1, days: [{}], status: 'approved', soft_note: line}),"
        + " draftSoftNoteHtml({weekly_plan_id: 1, days: [{}], status: 'draft', soft_note: null})]));"
    )
    assert 'class="wk-note"' in out[0] and "Sam is allergic to" in out[0]
    assert out[1] == "" and out[2] == ""
