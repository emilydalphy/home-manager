"""
Vegetarian and vegan get a hard check, like allergies (Emily's pick #61,
2026-10-26).

Before: a member's "Vegetarian" was matched as the literal word, so it
held back nothing a vegetarian can't eat (Chicken Tacos went through every
door) and held back a dish that called itself "Vegetarian Chili". Now a
diet reads as the foods it rules out (coordination._DIET_FAMILIES), through
the same matcher every door already shares — the draft, Swap, the three
picks, chat's own writes, the recipe Tweak and the sides sweep — so none of
them needed a change of its own.
"""
import datetime

import pytest

import library_batch
from app import tools
from app.tools import allergen_gate, recipe_change, swap_in_place as sip, weekly_plan as _wp
from conftest import household_today


TODAY = household_today()
WEEK_START = TODAY.isoformat()
DAYS = [(TODAY + datetime.timedelta(days=i)).isoformat() for i in range(7)]


def _member(restriction, name="Maya"):
    tools.add_member(name)
    tools.set_member_dietary_restrictions(name, [restriction])


def _clashes(name, *items):
    return allergen_gate.hard_clashes(name, ingredients=[{"item": i, "qty": "1"} for i in items])


def _dish(name, *items):
    return {"meal_name": name, "is_new_recipe": True, "reason": "fits",
            "ingredients": [{"item": i, "qty": "1", "category": "pantry"} for i in items],
            "instructions": ["Cook."], "food_groups": ["protein"], "cuisine": "Mexican",
            "main_protein": "", "prep_time_minutes": 10, "cook_time_minutes": 20, "default_servings": 2}


# ---------- the matcher ----------

@pytest.mark.parametrize("restriction, name, item", [
    ("Vegetarian", "Chicken Tacos", "chicken thighs"),
    ("Vegetarian", "Broccoli Cheddar Soup", "low-sodium chicken broth"),
    ("Vegetarian", "Green Papaya Salad", "fish sauce"),
    ("Vegetarian", "Caesar Salad", "anchovy fillets"),
    ("Vegetarian", "Carbonara", "pancetta"),
    ("Vegetarian", "Shrimp Fried Rice", "shrimp"),
    ("Vegetarian", "Fruit Gummies", "gelatin"),
    ("Vegetarian", "Pie", "lard"),
    ("Vegetarian", "Bolognese", "ground beef"),
    ("vegetarians", "Turkey Chili", "ground turkey"),
    ("Vegan", "Mac and Cheese", "sharp cheddar"),
    ("Vegan", "Pancakes", "2 eggs"),
    ("Vegan", "Granola", "honey"),
    ("Vegan", "Toast", "butter"),
    ("Vegan", "Chicken Tacos", "chicken thighs"),
    ("plant-based", "Omelette", "eggs"),
    ("Pescatarian", "Chicken Tacos", "chicken thighs"),
])
def test_a_diet_holds_back_what_it_rules_out(restriction, name, item):
    _member(restriction)
    assert _clashes(name, item), (restriction, item)


@pytest.mark.parametrize("restriction, name, items", [
    ("Vegetarian", "Vegetarian Chili", ["black beans", "vegetable broth", "tomatoes"]),
    ("Vegetarian", "Eggplant Parmesan", ["eggplant", "parmesan", "eggs"]),
    ("Vegetarian", "Goat Cheese Salad", ["goat's cheese", "lamb's lettuce"]),
    ("Vegetarian", "Mushroom Stir-Fry", ["vegetarian oyster sauce", "oyster mushrooms"]),
    ("Vegetarian", "Veggie Sausage Hash", ["veggie sausages", "potatoes"]),
    ("Vegetarian", "Bolognese", ["meat-free mince", "tomatoes"]),
    ("Vegetarian", "Spring Rolls", ["duck sauce", "cabbage"]),
    ("Vegan", "Vegan Curry", ["eggplant", "coconut milk", "vegetable broth"]),
    ("Vegan", "Peanut Noodles", ["peanut butter", "rice noodles"]),
    ("Vegan", "Snickerdoodles", ["cream of tartar", "flour", "vegan butter"]),
    ("Vegan", "Butter Bean Stew", ["butter beans", "honeydew melon"]),
    ("Vegan", "Oat Porridge", ["oat milk", "flax egg"]),
    ("Pescatarian", "Salmon Bowl", ["salmon", "rice"]),
    ("Vegetarian", "Veggie Dogs", ["veggie dogs", "hot dog buns"]),
])
def test_a_diet_lets_through_what_only_sounds_like_it(restriction, name, items):
    """GUARD: the false positives — a word that names an animal food and
    isn't one, and a dish whose name says its own diet."""
    _member(restriction)
    assert _clashes(name, *items) == []


def test_a_plant_qualifier_speaks_for_a_diet_never_for_an_allergy():
    """GUARD: "vegan fish sauce" passes a vegetarian and is still fish to a
    fish allergy, which reads it exactly as it did before."""
    _member("Vegetarian")
    _member("fish allergy", name="Sam")
    assert {c["member"] for c in _clashes("Noodles", "vegan fish sauce")} == {"Sam"}


def test_veggies_are_vegetables_not_a_diet():
    _member("veggies")
    assert _clashes("Chicken Tacos", "chicken thighs") == []


def test_a_name_with_no_list_is_held_on_its_meat():
    _member("Vegetarian")
    assert allergen_gate.hard_clashes("Chicken Tacos")
    assert allergen_gate.hard_clashes("Vegetarian Chili") == []


def test_a_clash_says_the_food_not_the_diet():
    _member("Vegetarian")
    clashes = _clashes("Broccoli Cheddar Soup", "chicken broth")
    assert clashes[0]["matched"] == "chicken"
    assert allergen_gate.refusal_sentence("Broccoli Cheddar Soup", clashes) == (
        "Broccoli Cheddar Soup has chicken, which Maya can’t have — want me to pick something else?")


def test_another_members_allergy_still_reads_as_it_did():
    _member("Vegetarian")
    _member("peanut allergy", name="Sam")
    clashes = _clashes("Satay", "peanut sauce", "chicken")
    assert {(c["member"], c["matched"]) for c in clashes} == {("Maya", "chicken"), ("Sam", "peanut")}


# ---------- every door ----------

@pytest.fixture
def week():
    _member("Vegetarian")
    tools.add_recipe("Chili", ingredients=[{"item": "black beans", "qty": "1 tin"}],
                     prep_time_minutes=10, cook_time_minutes=20)
    tools.add_recipe("Chicken Soup", ingredients=[{"item": "chicken thighs", "qty": "1 lb"}])
    plan_id = tools.create_weekly_plan(WEEK_START)["weekly_plan_id"]
    for day in DAYS:
        tools.plan_meal(day, "Chili", slot="dinner", weekly_plan_id=plan_id)
    return plan_id


def _slot(plan_id, date, slot):
    return next((m for m in tools.get_weekly_plan(plan_id)["meals"]
                 if m["date"] == date and m["slot"] == slot), None)


def test_the_draft_holds_back_a_meat_dish(week):
    safe, held = allergen_gate.split_safe([_dish("Chicken Tacos", "chicken thighs", "tortillas"),
                                           _dish("Bean Tacos", "black beans", "tortillas")])
    assert [s["meal_name"] for s in safe] == ["Bean Tacos"]
    assert held[0]["item"]["meal_name"] == "Chicken Tacos"


def test_swap_refuses_a_meat_pick_and_nothing_changes(week):
    entry = _slot(week, DAYS[3], "dinner")
    out = tools.swap_meal_in_place(week, entry["entry_id"],
                                   picker=lambda ctx: _dish("Chicken Tacos", "chicken thighs"))
    assert out["status"] == "refused"
    assert _slot(week, DAYS[3], "dinner")["meal"] == "Chili"


def test_the_three_picks_never_offer_the_meat_one(week):
    entry = _slot(week, DAYS[3], "dinner")
    out = tools.swap_options(week, entry["entry_id"], asker=lambda ctx: [
        _dish("Chicken Tacos", "chicken thighs"), _dish("Bean Tacos", "black beans"),
        _dish("Lentil Soup", "red lentils", "vegetable broth"),
    ])
    assert [o["meal"] for o in out["options"]] == ["Bean Tacos", "Lentil Soup"]


def test_chat_declines_a_meat_dish_and_only_the_persons_answer_gets_past(week):
    """Mirrors the allergy exactly: refused, and an override counts only as
    an answer in a later turn."""
    allergen_gate._RECIPE_REFUSALS.clear()
    allergen_gate.begin_chat_turn()
    with pytest.raises(_wp.SlotRefused, match="Chicken Soup has chicken, which Maya can’t have"):
        tools.swap_meal_in_plan_for_chat(week, DAYS[3], "Chicken Soup", slot="dinner", override=True)
    with pytest.raises(_wp.SlotRefused):
        tools.plan_meal_for_chat(DAYS[5], "Chicken Soup", slot="lunch", weekly_plan_id=week)
    assert _slot(week, DAYS[3], "dinner")["meal"] == "Chili"
    allergen_gate.begin_chat_turn()
    tools.swap_meal_in_plan_for_chat(week, DAYS[3], "Chicken Soup", slot="dinner", override=True)
    assert _slot(week, DAYS[3], "dinner")["meal"] == "Chicken Soup"


def test_chat_cannot_save_a_meat_recipe_for_a_vegetarian_house(week):
    with pytest.raises(_wp.SlotRefused, match="chicken"):
        tools.add_recipe_for_chat("Chicken Pot Pie", ingredients=[{"item": "chicken breast", "qty": "1 lb"}])
    assert not any(r["name"] == "Chicken Pot Pie" for r in tools.list_recipes())


def test_a_tweak_that_adds_meat_is_blocked(week):
    blocked = recipe_change.recipe_change_blocked("Chili", [{"item": "black beans"}, {"item": "ground beef"}])
    assert blocked and blocked["member"] == "Maya" and blocked["ingredient"] == "beef"


def test_a_side_with_bacon_comes_back_off_the_plate(week):
    from app.tools import plates
    dinner = _slot(week, DAYS[2], "dinner")
    plates.attach_sides(dinner["entry_id"], [
        {"name": "Green beans with bacon", "covers": ["vegetable"],
         "ingredients": [{"item": "green beans", "qty": "1 lb"}, {"item": "bacon", "qty": "4 slices"}]},
    ], ["vegetable"])
    out = allergen_gate.sweep_plan(week)
    assert out["sides_removed"] == 1
    assert tools.get_plate_sides(dinner["entry_id"]) == []
    assert _slot(week, DAYS[2], "dinner")["meal"] == "Chili"


def test_the_slot_route_refuses_a_meat_choice_over_http(week, signed_in):
    _wp.plan_slot_open(week, DAYS[5], "lunch", "I would rather ask than guess.")
    res = signed_in.post(f"/api/week/{WEEK_START}/slot", json={
        "date": DAYS[5], "slot": "lunch", "choice": "Chicken Soup", "weekly_plan_id": week})
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["status"] == "refused" and "chicken" in body["message"]
    assert _slot(week, DAYS[5], "lunch")["slot_state"] == "open"


# ---------- library tags, verified against the list ----------

def test_a_vegan_tag_over_butter_is_a_look_and_a_clean_one_is_not():
    vegan = {"main_protein": "vegan", "ingredients": [{"item": "Unsalted butter"}, {"item": "Peanut butter"},
                                                      {"item": "Coconut milk"}]}
    assert library_batch.meat_in_a_meatless_dish(vegan) == ["Unsalted butter"]
    veg = {"main_protein": "vegetarian", "ingredients": [{"item": "Butter"}, {"item": "Vegetable broth"},
                                                         {"item": "Chicken stock"}]}
    assert library_batch.meat_in_a_meatless_dish(veg) == ["Chicken stock"]
