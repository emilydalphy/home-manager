"""
Speed: the allergy check stops reading "dairy-free" as dairy (2026-09-30).

A household with a dairy-free member lost its first week to the gate: the
draft held back "Veggie & Egg White Scramble" five times, "Greek Yogurt
(Dairy-Free) Parfait" three times, a teriyaki bowl, a peanut noodle salad
and turkey meatballs — each one a re-pick call or an empty slot, rewritten
in front of her — and chat refused "Dairy-Free Pancakes" outright. The
matcher read the word "dairy" in "dairy-free" (and "cheese" in "no cheese")
as the allergen itself, in the dish's name and in the planner's dish_note.

What changed, and what did not:

  - In a NAME or a planner's NOTE, a negated allergen word doesn't count:
    "<word>-free", "non-<word>", "no/without/instead of <word>". Only that
    occurrence — "no cheese, finish with butter" is still butter.
  - Plant versions pass anywhere, ingredient lines included: coconut
    cream/yogurt/milk, oat/soy/almond milk, dairy-free or vegan
    butter/yogurt/cheese, vegan mayo.
  - Real dairy still holds a dish back every time, and Emily's 2026-09-20
    rule stands: a "-free" label can't sneak an allergen in. "Dairy-Free
    Buttermilk Pancakes" is held (buttermilk has no plant exception).
  - An INGREDIENT LINE is still matched strictly — "no butter" as a line of
    a recipe's list is butter.
  - Chat's add_recipe checks before it saves, so a recipe the house can't
    have is never left orphaned in the recipe box.
  - The held-back log line names the word that matched.
"""
import logging

import pytest

from app import agent, tools
from app.tools import allergen_gate, coordination
from app.tools import weekly_plan as _wp


@pytest.fixture
def dairy_free():
    tools.add_member("Emily")
    tools.set_member_dietary_restrictions("Emily", ["dairy free"])


def _held(name, ingredients=None, note=None):
    item = {"meal_name": name, "is_new_recipe": True}
    if ingredients is not None:
        item["ingredients"] = [{"item": i, "qty": "1"} for i in ingredients]
    if note is not None:
        item["dish_note"] = note
    return allergen_gate.hard_clashes(name, ingredients=allergen_gate.ingredients_for(item))


# ---------- a label alone is not the allergen ----------

@pytest.mark.parametrize("name", [
    "Dairy-Free Pancakes",
    "Dairy Free Pancakes",
    "Non-Dairy Pancakes",
    "Pancakes with No Dairy",
    "Vegan Pancakes",
    "Greek Yogurt (Dairy-Free) Parfait",
    "Mac and Cheese (Vegan)",
])
def test_a_dairy_free_name_is_not_held_for_dairy(dairy_free, name):
    assert _held(name) == [], name


@pytest.mark.parametrize("restriction, name", [
    ("nut allergy", "Nut-Free Granola Bars"),
    ("peanut allergy", "Peanut-Free Energy Bites"),
    ("egg allergy", "Egg-Free Banana Muffins"),
    ("soy allergy", "Soy-Free Stir Fry"),
    ("sesame allergy", "Sesame-Free Noodle Bowl"),
    ("shellfish allergy", "Shellfish-Free Paella"),
    ("fish allergy", "Fish-Free Fried Rice"),
    ("gluten free", "Gluten-Free Pancakes"),
])
def test_every_allergen_free_label_is_read_the_same_way(restriction, name):
    tools.add_member("Emily")
    tools.set_member_dietary_restrictions("Emily", [restriction])
    assert _held(name) == [], (restriction, name)


@pytest.mark.parametrize("name, note", [
    # The production shapes: a clean name over a note that says what the
    # dish leaves out.
    ("Veggie & Egg White Scramble", "soft-scramble the whites in olive oil; keep it dairy-free, no cheese"),
    ("Japanese Teriyaki Salmon Rice Bowl", "glossy homemade teriyaki glaze, naturally dairy-free"),
    ("Italian Turkey Meatballs", "bind with breadcrumbs and egg, no parmesan or milk"),
    ("Thai Peanut Chicken Noodle Salad", "peanut-lime dressing with coconut milk, dairy free"),
    ("Roast Chicken", "crisp the skin with olive oil instead of butter"),
    ("Mushroom Risotto", "finish without the cream, stir in olive oil"),
])
def test_a_note_that_says_what_the_dish_leaves_out_is_not_held(dairy_free, name, note):
    assert _held(name, note=note) == [], (name, note)


# ---------- real dairy still holds, every time ----------

@pytest.mark.parametrize("word", [
    "butter", "milk", "cheese", "cream", "buttermilk", "yogurt", "whey", "ghee", "paneer",
    "parmesan", "greek yogurt", "heavy cream", "lactose-free milk",
])
def test_a_real_dairy_ingredient_is_held_under_a_dairy_free_name(dairy_free, word):
    """Emily, 2026-09-20: the list decides, and a "-free" label can't sneak
    the allergen in."""
    assert _held("Dairy-Free Weeknight Bake", ingredients=[word]), word
    assert _held("Weeknight Bake", note=f"keep it dairy-free, finish with {word}"), word


@pytest.mark.parametrize("name", [
    "Dairy-Free Buttermilk Pancakes",
    "Dairy-Free Buttermilk-Style Pancakes",
    "Greek Yogurt Parfait",
    "Dairy-Free Chicken with Cream Sauce",   # the label covers the word it touches, not the dish
    "No-Bake Cheese Tart",                   # "no-bake" is not "no"
    "No Fuss Cheese Toastie",                # the word has to follow the negator
    "Butter Free-Range Chicken",             # "free-range" is not "-free"
])
def test_a_name_that_still_names_dairy_is_held(dairy_free, name):
    clashes = _held(name)
    assert clashes and clashes[0]["member"] == "Emily", name


def test_a_negation_cancels_only_its_own_word(dairy_free):
    clashes = _held("Weeknight Pasta", note="no cheese, finish with butter")
    assert [c["matched_word"] for c in clashes] == ["butter"]


def test_an_ingredient_line_is_still_matched_strictly(dairy_free):
    """A list is what the dish is made of: a line reading "no butter" is
    butter as far as the gate is concerned. Only the named compounds (plant
    versions, peanut butter…) are discounted in a list."""
    assert _held("Toast", ingredients=["no butter"])
    assert _held("Toast", ingredients=["dairy free spread", "butter"])


# ---------- plant versions pass ----------

@pytest.mark.parametrize("line", [
    "coconut cream", "coconut yogurt", "coconut milk", "oat milk", "soy milk", "almond milk",
    "dairy-free butter", "vegan butter", "dairy-free yogurt", "dairy-free greek yogurt",
    "vegan cheese", "vegan cream cheese", "non-dairy milk", "plant-based butter",
    "peanut butter", "coconut butter", "cashew cheese", "cream of tartar",
])
def test_a_plant_version_on_the_list_passes(dairy_free, line):
    assert _held("Pancakes", ingredients=[line]) == [], line


def test_vegan_mayo_passes_an_egg_allergy_and_mayo_does_not():
    tools.add_member("Emily")
    tools.set_member_dietary_restrictions("Emily", ["egg allergy"])
    assert _held("Sandwich", ingredients=["vegan mayo"]) == []
    assert _held("Sandwich", ingredients=["egg-free mayonnaise"]) == []
    assert _held("Sandwich", ingredients=["mayonnaise"])
    # "dairy-free" says nothing about egg.
    assert _held("Sandwich", ingredients=["dairy-free mayo"])


# ---------- the draft keeps them ----------

def test_the_draft_keeps_the_dairy_free_week_it_used_to_throw_away(dairy_free):
    items = [
        {"date": "2026-10-01", "slot": "breakfast", "meal_name": "Veggie & Egg White Scramble",
         "is_new_recipe": True, "dish_note": "keep it dairy-free, no cheese"},
        {"date": "2026-10-01", "slot": "snack", "meal_name": "Greek Yogurt (Dairy-Free) Parfait",
         "is_new_recipe": True, "dish_note": "coconut yogurt, berries, granola"},
        {"date": "2026-10-01", "slot": "dinner", "meal_name": "Italian Turkey Meatballs",
         "is_new_recipe": True, "dish_note": "no parmesan; bind with breadcrumbs and egg"},
        {"date": "2026-10-02", "slot": "breakfast", "meal_name": "Dairy-Free Buttermilk Pancakes",
         "is_new_recipe": True, "dish_note": "fluffy and tangy"},
    ]
    safe, held = allergen_gate.split_safe(items)
    assert [i["meal_name"] for i in safe] == [
        "Veggie & Egg White Scramble", "Greek Yogurt (Dairy-Free) Parfait", "Italian Turkey Meatballs",
    ]
    assert [h["item"]["meal_name"] for h in held] == ["Dairy-Free Buttermilk Pancakes"]


def test_the_held_back_log_line_names_the_word_that_matched(dairy_free, caplog):
    with caplog.at_level(logging.WARNING, logger="home_manager"):
        allergen_gate.split_safe([
            {"date": "2026-10-02", "slot": "breakfast", "meal_name": "Dairy-Free Buttermilk Pancakes",
             "is_new_recipe": True},
        ])
    assert "held back" in caplog.text
    assert "dairy (matched 'buttermilk')" in caplog.text


def test_the_matcher_reports_the_word_and_keeps_the_label(dairy_free):
    hits = tools.check_meal_conflicts("Soda Bread", ingredients=[{"item": "buttermilk"}])
    assert hits[0]["matched"] == "dairy"          # the sentences still say "dairy"
    assert hits[0]["matched_word"] == "buttermilk"
    # A plain dish_note-less line has no label reading: coordination's own
    # tiers are what decide, not the caller.
    assert coordination.check_meal_conflicts(
        "Toast", ingredients=[{"item": "keep it dairy-free"}],
    )


# ---------- chat's add_recipe checks before it saves ----------

def _recipe_names():
    return {r["name"] for r in tools.list_recipes()}


def test_chat_add_recipe_refuses_a_recipe_the_house_cannot_have_and_saves_nothing(dairy_free):
    with pytest.raises(_wp.SlotRefused) as caught:
        agent.TOOL_FUNCTIONS["add_recipe"](
            name="Dairy-Free Buttermilk-Style Pancakes",
            ingredients=[{"item": "flour", "qty": "2 cups"}, {"item": "buttermilk", "qty": "1 cup"}],
        )
    message = str(caught.value)
    assert message.startswith("Not saved: Dairy-Free Buttermilk-Style Pancakes has buttermilk (dairy)")
    assert "Emily can’t have" in message and "Write it again without buttermilk" in message
    assert "Dairy-Free Buttermilk-Style Pancakes" not in _recipe_names()


def test_chat_add_recipe_saves_the_dairy_free_pancakes(dairy_free):
    agent.TOOL_FUNCTIONS["add_recipe"](
        name="Dairy-Free Pancakes",
        ingredients=[{"item": "flour", "qty": "2 cups"}, {"item": "oat milk", "qty": "1 cup"},
                     {"item": "vegan butter", "qty": "2 tbsp"}],
    )
    assert "Dairy-Free Pancakes" in _recipe_names()


def test_chat_add_recipe_saves_anyway_only_on_the_persons_word(dairy_free):
    tools.add_recipe_for_chat(
        "Grandma's Cheesecake Bars", [{"item": "cream cheese", "qty": "1 block"}], override=True,
    )
    assert "Grandma's Cheesecake Bars" in _recipe_names()


def test_the_other_add_recipe_callers_are_not_gated(dairy_free):
    """The week's own saves, imports and the recipe pass call add_recipe
    itself — they have run the gate their own way already."""
    tools.add_recipe("Butter Chicken", ingredients=[{"item": "butter", "qty": "2 tbsp"}])
    assert "Butter Chicken" in _recipe_names()


def test_the_agent_tool_points_at_the_gated_twin_and_describes_override():
    assert agent.TOOL_FUNCTIONS["add_recipe"] is tools.add_recipe_for_chat
    schema = next(t for t in agent.TOOL_DEFINITIONS if t["name"] == "add_recipe")
    assert "override" in schema["input_schema"]["properties"]
    assert "never on a first call" in schema["input_schema"]["properties"]["override"]["description"]
