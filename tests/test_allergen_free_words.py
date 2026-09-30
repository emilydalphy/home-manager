"""
Speed: the allergy check stops reading "dairy-free" as dairy (2026-09-30).

A household with a dairy-free member lost its first week to the gate: the
draft held back "Veggie & Egg White Scramble" five times, "Greek Yogurt
(Dairy-Free) Parfait" three times, a teriyaki bowl, a peanut noodle salad
and turkey meatballs — each one a re-pick call or an empty slot, rewritten
in front of her — and chat refused "Dairy-Free Pancakes" outright. The
matcher read the word "dairy" in "dairy-free" (and "cheese" in "no cheese")
as the allergen itself, in the dish's name and in the planner's dish_note.

Emily's 2026-09-20 rule is the north star: a "-free" label can never sneak
an allergen in. So what changed is narrow, and the safety verifier's
probes (the second half of this file) pin every edge of it:

  - A DRAFT dish — one that gets a checked ingredient list before anyone
    shops or cooks — may say what it leaves out in its name and note:
    "<word>-free", "non-<word>", "no/without/instead of <word>". Only that
    one occurrence of that one word is cancelled.
  - A name over a real ingredient list may too; the list is then what
    decides, matched strictly, and the alias table was widened so the
    list knows the named cheeses, sauces and dishes (cheddar, feta,
    carbonara, satay, teriyaki…).
  - A dish that is FINAL without a list (a freeform name planned from
    chat, a recipe the pass failed to write) is matched on its name
    strictly — no negation, no plant qualifier.
  - On an ingredient line only a LEADING plant qualifier counts ("vegan
    butter", "oat milk"); "butter (dairy-free if needed)" is butter.
  - Chat's add_recipe checks before it saves, and its override is only
    honoured as an answer to a refusal.
"""
import logging

import pytest

from app import agent, tools
from app.tools import allergen_gate, coordination
from app.tools import weekly_plan as _wp


@pytest.fixture(autouse=True)
def _no_old_refusals():
    allergen_gate._RECIPE_REFUSALS.clear()
    yield
    allergen_gate._RECIPE_REFUSALS.clear()


def _restrict(restriction):
    tools.add_member("Emily")
    tools.set_member_dietary_restrictions("Emily", [restriction])


@pytest.fixture
def dairy_free():
    _restrict("dairy free")


def _item(name, ingredients=None, note=None):
    item = {"meal_name": name, "is_new_recipe": True}
    if ingredients is not None:
        item["ingredients"] = [{"item": i, "qty": "1"} for i in ingredients]
    if note is not None:
        item["dish_note"] = note
    return item


def _draft(name, ingredients=None, note=None):
    """A dish on the week draft: split_safe's reading."""
    return allergen_gate.hard_clashes(
        name, ingredients=allergen_gate.ingredients_for(_item(name, ingredients, note)), draft=True,
    )


def _final(name, ingredients=None):
    """A dish matched anywhere else — chat, swap, the approved week."""
    own = [{"item": i, "qty": "1"} for i in ingredients or []]
    return allergen_gate.hard_clashes(name, ingredients=own)


# =====================================================================
# The speed half: a draft's label is not the allergen
# =====================================================================

@pytest.mark.parametrize("name", [
    "Dairy-Free Pancakes",
    "Dairy Free Pancakes",
    "Non-Dairy Pancakes",
    "Pancakes with No Dairy",
    "Vegan Pancakes",
    "Greek Yogurt (Dairy-Free) Parfait",
    "Mac and Cheese (Vegan)",
    "Dairy‑Free Pancakes",            # a non-breaking hyphen
])
def test_a_draft_dairy_free_name_is_not_held_for_dairy(dairy_free, name):
    assert _draft(name) == [], name


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
def test_every_allergen_free_label_on_a_draft_is_read_the_same_way(restriction, name):
    _restrict(restriction)
    assert _draft(name) == [], (restriction, name)


@pytest.mark.parametrize("name, note", [
    # The production shapes: a clean name over a note that says what the
    # dish leaves out.
    ("Veggie & Egg White Scramble", "soft-scramble the whites in olive oil; keep it dairy-free, no cheese"),
    ("Japanese Teriyaki Salmon Rice Bowl", "glossy homemade teriyaki glaze, naturally dairy-free"),
    ("Italian Turkey Meatballs", "bind with breadcrumbs and egg, no parmesan"),
    ("Thai Peanut Chicken Noodle Salad", "peanut-lime dressing with coconut milk, dairy free"),
    ("Roast Chicken", "crisp the skin with olive oil instead of butter"),
    ("Mushroom Risotto", "finish without the cream, stir in olive oil"),
])
def test_a_draft_note_that_says_what_the_dish_leaves_out_is_not_held(dairy_free, name, note):
    assert _draft(name, note=note) == [], (name, note)


def test_a_name_over_a_real_list_is_decided_by_the_list(dairy_free):
    """Chat's own dairy-free pancakes: the list is plant milk, so the name's
    label is believed."""
    assert _final("Dairy-Free Pancakes", ["flour", "eggs", "oat milk"]) == []


# =====================================================================
# Real dairy still holds, every time
# =====================================================================

@pytest.mark.parametrize("word", [
    "butter", "milk", "cheese", "cream", "buttermilk", "yogurt", "whey", "ghee", "paneer",
    "parmesan", "greek yogurt", "heavy cream", "lactose-free milk",
])
def test_a_real_dairy_ingredient_is_held_under_a_dairy_free_label(dairy_free, word):
    assert _draft("Dairy-Free Weeknight Bake", ingredients=[word]), word
    assert _draft("Weeknight Bake", note=f"keep it dairy-free, finish with {word}"), word
    assert _final("Dairy-Free Weeknight Bake", [word]), word


@pytest.mark.parametrize("name", [
    "Dairy-Free Buttermilk Pancakes",
    "Dairy-Free Buttermilk-Style Pancakes",
    "Greek Yogurt Parfait",
    "Dairy-Free Chicken with Cream Sauce",   # the label covers the word it touches, not the dish
    "No-Bake Cheese Tart",                   # "no-bake" is not "no"
    "No Fuss Cheese Toastie",                # the word has to follow the negator
    "Butter Free-Range Chicken",             # "free-range" is not "-free"
    # Verifier C: "X free range" is not X-free either.
    "Butter Free Range Chicken",
    "Cheese Free Range Omelette",
    "Dairy-Free-Cheese Pizza",
    "Cheese-less Pizza",
])
def test_a_draft_name_that_still_names_dairy_is_held(dairy_free, name):
    clashes = _draft(name)
    assert clashes and clashes[0]["member"] == "Emily", name


def test_eggs_free_range_benedict_is_eggs():
    _restrict("egg allergy")
    assert _draft("Eggs Free Range Benedict")
    assert _draft("Free-Range Egg Frittata")


def test_a_negation_cancels_only_its_own_word(dairy_free):
    assert [c["matched_word"] for c in _draft("Weeknight Pasta", note="no cheese, finish with butter")] == ["butter"]
    assert _draft("Pasta", note="dairy-free cheese, grated parmesan")
    assert _draft("Pasta", note="keep it dairy-free; finish with a little cream")


@pytest.mark.parametrize("restriction, name, note", [
    # Verifier C: no "or" chaining — the negation reaches one word.
    ("nut allergy", "Chicken with no nuts or peanut sauce", None),
    # Negating one alias never stops another word matching.
    ("nut allergy", "Nut Free Satay Chicken", None),
    ("nut allergy", "Chicken", "no nuts, almond crust"),
    ("nut allergy", "Chicken", "no nuts or almonds, top with pistachios"),
    ("nut allergy", "Chicken", "nut free; sprinkle hazelnuts"),
    ("nut allergy", "Nut-Free Peanut Butter Cookies", None),
    ("nut allergy", "Nut-Free Pesto Pasta", None),
    ("nut allergy", "Nut-free Praline Tart", None),
    ("nut allergy", "Tree-Nut-Free Pesto", None),
    ("peanut allergy", "Peanut-Free Satay", None),
    ("peanut allergy", "Satay", "no peanuts, satay sauce"),
    ("egg allergy", "Sandwich", "no egg; add mayo"),
    ("egg allergy", "Sandwich", "no egg or mayo; meringue on top"),
    ("egg allergy", "Egg Free Carbonara", None),
    ("shellfish allergy", "Pasta", "no shellfish, just shrimp"),
    ("shellfish allergy", "Pasta", "no shellfish or shrimp; clam juice"),
    ("shellfish allergy", "Crab-Free Crab Cakes", None),
    ("gluten free", "No-Flour Brownies", None),
    ("gluten free", "Pasta-Free Lasagna", None),
    ("gluten free", "Noodle Free Ramen", None),
    ("gluten free", "Stir-fry", "no bread or pasta, serve with couscous"),
    ("gluten free", "Beef", "instead of flour use breadcrumb"),
    ("soy allergy", "Soy-Free Teriyaki", None),
    ("sesame allergy", "Sesame-Free Hummus Bowl", None),
])
def test_a_draft_that_still_names_the_allergen_elsewhere_is_held(restriction, name, note):
    _restrict(restriction)
    assert _draft(name, note=note), (restriction, name, note)


def test_an_ingredient_line_is_still_matched_strictly(dairy_free):
    """A list is what the dish is made of: a line reading "no butter" is
    butter. Only the named compounds (plant versions, peanut butter…) are
    discounted in a list."""
    assert _draft("Toast", ingredients=["no butter"])
    assert _draft("Toast", ingredients=["dairy free spread", "butter"])


# =====================================================================
# Verifier A: the list has to know the allergen by every common name
# =====================================================================

@pytest.mark.parametrize("name, ingredients", [
    ("Dairy-Free Cheese Sauce", ["2 cups sharp cheddar", "flour"]),
    ("Vegan Cheese Pizza", ["pizza dough", "2 cups mozzarella", "tomato sauce"]),
    ("Dairy-Free Lasagna", ["lasagna sheets", "ricotta", "mozzarella", "tomato sauce"]),
    ("Non-Dairy Greek Salad", ["cucumber", "feta", "olives"]),
    ("Dairy-Free Queso", ["2 cups cheddar", "tomato"]),
    ("Chicken", ["cheddar"]),
])
def test_a_dairy_free_label_over_a_named_cheese_is_held(dairy_free, name, ingredients):
    assert _draft(name, ingredients=ingredients), name
    assert _final(name, ingredients), name


@pytest.mark.parametrize("restriction, item", [
    *[("dairy free", w) for w in [
        "cheddar", "mozzarella", "ricotta", "feta", "brie", "camembert", "gouda", "gruyère",
        "gruyere", "parmigiano reggiano", "pecorino", "provolone", "mascarpone", "halloumi",
        "burrata", "cottage cheese", "cream cheese", "sour cream", "crème fraîche",
        "creme fraiche", "queso fresco", "kefir", "ice cream", "custard", "monterey jack",
        "labneh", "tzatziki", "alfredo sauce", "caesar dressing",
    ]],
    *[("egg allergy", w) for w in [
        "meringue", "aioli", "mayo", "custard", "hollandaise", "carbonara sauce", "quiche",
        "egg yolks", "egg whites",
    ]],
    *[("shellfish allergy", w) for w in [
        "shrimp", "prawns", "crab", "lobster", "scallops", "clams", "mussels", "oysters",
        "crawfish", "langoustines", "oyster sauce", "dried shrimp", "imitation crab",
    ]],
    *[("fish allergy", w) for w in [
        "anchovies", "fish sauce", "worcestershire sauce", "sea bass", "dashi", "caesar dressing",
    ]],
    *[("nut allergy", w) for w in [
        "satay sauce", "pesto", "praline", "marzipan", "nutella", "pine nuts", "peanut sauce",
        "frangipane", "romesco",
    ]],
    *[("soy allergy", w) for w in [
        "tofu", "tempeh", "edamame", "miso", "soy sauce", "tamari", "teriyaki sauce", "hoisin",
    ]],
    *[("gluten free", w) for w in [
        "panko", "semolina", "spaghetti", "udon", "ramen noodles", "orzo", "spelt", "wheat berries",
        "malt vinegar", "teriyaki sauce",
    ]],
    *[("sesame allergy", w) for w in ["tahini", "hummus", "halva", "za'atar"]],
])
def test_the_audited_alias_table_reaches_the_names_lists_use(restriction, item):
    _restrict(restriction)
    assert _final("Dinner", [item]), (restriction, item)


def test_peanuts_are_fine_still_lifts_satay_out_of_a_tree_nut_allergy():
    tools.add_member("Emily")
    tools.add_fact("people", "allergic to tree nuts but peanuts are fine", hard=True)
    assert _final("Satay", ["satay sauce"]) == []
    assert _final("Pesto Pasta", ["pesto"])


# =====================================================================
# Plant versions — and verifier B: only the LEADING qualifier, in a list
# =====================================================================

@pytest.mark.parametrize("line", [
    "coconut cream", "coconut yogurt", "coconut milk", "oat milk", "soy milk", "almond milk",
    "oat cream", "dairy-free butter", "vegan butter", "dairy-free yogurt", "dairy-free greek yogurt",
    "vegan cheese", "vegan cream cheese", "non-dairy milk", "plant-based butter",
    "dairy-free coconut milk", "dairy-free oat yogurt",
    "peanut butter", "coconut butter", "cashew cheese", "cream of tartar",
])
def test_a_plant_version_on_the_list_passes(dairy_free, line):
    assert _final("Pancakes", [line]) == [], line


@pytest.mark.parametrize("line", [
    "1 cup shredded cheese (vegan if needed)",
    "2 tbsp butter (dairy-free if needed)",
    "2 tbsp butter, vegan optional",
    "heavy cream, dairy-free works too",
    "Greek yogurt (vegan optional)",
    "1/2 cup sour cream (dairy-free if needed)",
    "1 cup cheese vegan or regular",
    "dairy-free or regular butter",
])
def test_a_trailing_or_optional_plant_qualifier_on_a_line_is_still_dairy(dairy_free, line):
    assert _final("Pasta", [line]), line
    assert _draft("Pasta", ingredients=[line]), line


@pytest.mark.parametrize("line, held", [
    ("vegan mayo", False),
    ("egg-free mayonnaise", False),
    ("eggless mayo", False),
    ("mayonnaise", True),
    ("mayo (vegan if needed)", True),
    ("aioli, vegan optional", True),
    ("dairy-free mayo", True),        # "dairy-free" says nothing about egg
])
def test_plant_mayo_for_an_egg_allergy(line, held):
    _restrict("egg allergy")
    assert bool(_final("Sandwich", [line])) is held, line


@pytest.mark.parametrize("restriction, line", [
    ("nut allergy", "almond milk"),
    ("nut allergy", "cashew cheese"),
    ("nut allergy", "dairy-free almond butter"),
    ("nut allergy", "vegan cashew cheese"),
    ("soy allergy", "soy yogurt"),
    ("soy allergy", "vegan soy cream"),
])
def test_a_plant_version_is_only_plant_for_dairy_not_for_its_own_allergen(restriction, line):
    _restrict(restriction)
    assert _final("Parfait", [line]), (restriction, line)


# =====================================================================
# Verifier D: anywhere a dish is final without a list, the name is strict
# =====================================================================

@pytest.mark.parametrize("name", [
    "Dairy-Free Pancakes",
    "Non-Dairy Pancakes",
    "Vegan Cheese Pizza",
    "DAIRY-FREE CHEESE PIZZA",
    "Mac and Cheese (Vegan)",
])
def test_a_final_name_with_no_list_is_matched_strictly(dairy_free, name):
    assert _final(name), name


def test_chat_plan_meal_checks_a_freeform_name_strictly(dairy_free):
    with pytest.raises(_wp.SlotRefused, match="dairy"):
        tools.plan_meal_for_chat("2026-10-05", "Dairy-Free Pancakes", slot="breakfast")
    with pytest.raises(_wp.SlotRefused):
        tools.plan_meal_for_chat("2026-10-05", "Vegan Cheese Pizza", slot="dinner")


def test_chat_plan_meal_reads_a_saved_list_over_the_label(dairy_free):
    tools.add_recipe("Dairy-Free Pancakes", ingredients=[{"item": "oat milk", "qty": "1 cup"},
                                                         {"item": "flour", "qty": "2 cups"}])
    out = tools.plan_meal_for_chat("2026-10-05", "Dairy-Free Pancakes", slot="breakfast")
    assert out


def test_the_approved_week_reads_a_pending_dish_strictly():
    """A pending dish is only a draft while the week is a draft."""
    tools.add_member("Emily")
    tools.set_member_dietary_restrictions("Emily", ["dairy free"])
    tools.add_recipe("Dairy-Free Pancakes", ingredients=[], details_pending=True, dish_note="oat milk")
    recipe = {r["name"]: r for r in tools.list_recipes()}["Dairy-Free Pancakes"]
    assert recipe["details_pending"] is True
    assert coordination.check_meal_conflicts("Dairy-Free Pancakes", avoidances=allergen_gate.hard_avoidances()), \
        "no list and not a draft: strict"
    assert coordination.check_meal_conflicts(
        "Dairy-Free Pancakes", avoidances=allergen_gate.hard_avoidances(), negate_labels=True,
    ) == []


# ---------- the recipe pass that fails for another reason ----------

def _week_start():
    import datetime
    today = datetime.date.today()
    monday = today - datetime.timedelta(days=today.weekday())
    return (monday + datetime.timedelta(days=7)).isoformat()


def _menu_week(week, new_dinner, note):
    days = []
    for day in tools._week_dates(week):
        for slot in tools.WEEK_SLOTS:
            if slot == "dinner":
                days.append({
                    "date": day, "slot": slot, "meal_name": new_dinner, "is_new_recipe": True,
                    "reasoning": "fits", "cuisine": "American", "main_protein": "vegetarian",
                    "food_groups": ["carb"], "prep_time_minutes": 10, "cook_time_minutes": 15,
                    "dish_note": note,
                })
            else:
                days.append({"date": day, "slot": slot, "meal_name": "Toast", "is_new_recipe": False,
                             "reasoning": "quick"})
    return days


@pytest.fixture
def pancake_week(monkeypatch):
    tools.add_member("Emily")
    tools.set_member_dietary_restrictions("Emily", ["dairy free"])
    tools.add_recipe("Toast", ingredients=[{"item": "sourdough", "qty": "1 loaf"}])
    tools.edit_preference("complete_plates", False)
    week = _week_start()
    days = _menu_week(week, "Dairy-Free Pancakes", "fluffy, made with oat milk")
    monkeypatch.setattr(agent, "generate_weekly_plan_llm", lambda context: days)
    plan = agent.generate_weekly_plan(week)
    swept = []

    def _fake_sweep(plan_id, budget=None, picker=None, known_clashes=None):
        swept.append(known_clashes)
        return {"sides_removed": 0, "dishes_repicked": 0, "slots_opened": 0}
    return {"plan": plan, "swept": swept, "fake_sweep": _fake_sweep}


def test_the_draft_keeps_the_dairy_free_pancakes(pancake_week):
    meals = tools.get_weekly_plan(pancake_week["plan"]["weekly_plan_id"])["meals"]
    assert any(m.get("meal") == "Dairy-Free Pancakes" for m in meals)
    assert tools.check_plan_conflicts(pancake_week["plan"]["weekly_plan_id"])["settle"] is None


def test_a_recipe_pass_that_fails_rechecks_the_name_strictly_and_repicks(pancake_week, monkeypatch):
    monkeypatch.setattr(agent, "generate_recipe_details_llm", lambda spec: {})
    monkeypatch.setattr(allergen_gate, "sweep_plan", pancake_week["fake_sweep"])

    result = agent.fill_pending_recipes_for_plan(pancake_week["plan"]["weekly_plan_id"])

    assert result["failed"] == ["Dairy-Free Pancakes"]
    assert pancake_week["swept"] and "dairy-free pancakes" in pancake_week["swept"][-1]


def test_a_recipe_pass_that_fails_on_a_clean_name_leaves_it_pending(monkeypatch):
    tools.add_member("Emily")
    tools.set_member_dietary_restrictions("Emily", ["dairy free"])
    tools.add_recipe("Toast", ingredients=[{"item": "sourdough", "qty": "1 loaf"}])
    tools.edit_preference("complete_plates", False)
    week = _week_start()
    monkeypatch.setattr(agent, "generate_weekly_plan_llm",
                        lambda context: _menu_week(week, "Lentil Soup", "cumin, lemon"))
    plan = agent.generate_weekly_plan(week)
    monkeypatch.setattr(agent, "generate_recipe_details_llm", lambda spec: {})
    swept = []
    monkeypatch.setattr(allergen_gate, "sweep_plan", lambda *a, **k: swept.append(k) or {})

    result = agent.fill_pending_recipes_for_plan(plan["weekly_plan_id"])

    assert result["failed"] == ["Lentil Soup"]
    assert swept == []


def test_the_cook_screen_repicks_a_dish_it_cannot_write_safely(pancake_week, monkeypatch):
    monkeypatch.setattr(agent, "generate_recipe_details_llm", lambda spec: {})
    monkeypatch.setattr(allergen_gate, "sweep_plan", pancake_week["fake_sweep"])

    out = agent.fill_in_recipe("Dairy-Free Pancakes")

    assert out == {"status": "replaced", "name": "Dairy-Free Pancakes"}
    assert pancake_week["swept"] and "Dairy-Free Pancakes" in pancake_week["swept"][-1]


def test_the_cook_screen_repicks_when_the_writer_keeps_adding_the_allergen(pancake_week, monkeypatch):
    monkeypatch.setattr(agent, "generate_recipe_details_llm", lambda spec: {
        "ingredients": [{"item": "buttermilk", "qty": "1 cup"}, {"item": "flour", "qty": "2 cups"}],
        "instructions": ["Whisk.", "Fry."],
    })
    monkeypatch.setattr(allergen_gate, "sweep_plan", pancake_week["fake_sweep"])

    out = agent.fill_in_recipe("Dairy-Free Pancakes")

    assert out["status"] == "replaced"
    assert tools.get_recipe("Dairy-Free Pancakes")["ingredients"] == []


# =====================================================================
# The draft keeps them, and the log says why when it doesn't
# =====================================================================

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


# =====================================================================
# Chat's add_recipe checks before it saves (and verifier E: the override)
# =====================================================================

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


def test_chat_add_recipe_refuses_dairy_free_mac_and_cheese_made_with_cheddar(dairy_free):
    with pytest.raises(_wp.SlotRefused, match="cheese|cheddar"):
        agent.TOOL_FUNCTIONS["add_recipe"](
            name="Dairy-Free Mac and Cheese",
            ingredients=[{"item": "macaroni", "qty": "1 lb"}, {"item": "sharp cheddar", "qty": "2 cups"}],
        )
    assert "Dairy-Free Mac and Cheese" not in _recipe_names()


def test_chat_add_recipe_reads_a_list_of_plain_strings_too(dairy_free):
    with pytest.raises(_wp.SlotRefused, match="cheese|cheddar"):
        tools.add_recipe_for_chat("Toastie", ["sourdough", "cheddar"])


def test_chat_add_recipe_saves_the_dairy_free_pancakes(dairy_free):
    agent.TOOL_FUNCTIONS["add_recipe"](
        name="Dairy-Free Pancakes",
        ingredients=[{"item": "flour", "qty": "2 cups"}, {"item": "oat milk", "qty": "1 cup"},
                     {"item": "vegan butter", "qty": "2 tbsp"}],
    )
    assert "Dairy-Free Pancakes" in _recipe_names()


def test_an_override_on_the_first_call_is_not_honoured(dairy_free):
    with pytest.raises(_wp.SlotRefused):
        tools.add_recipe_for_chat(
            "Grandma's Cheesecake Bars", [{"item": "cream cheese", "qty": "1 block"}], override=True,
        )
    assert "Grandma's Cheesecake Bars" not in _recipe_names()


def test_an_override_after_a_refusal_of_the_same_recipe_saves_it(dairy_free):
    lines = [{"item": "cream cheese", "qty": "1 block"}]
    with pytest.raises(_wp.SlotRefused):
        tools.add_recipe_for_chat("Grandma's Cheesecake Bars", lines)
    # A different recipe was never refused: its override is not an answer.
    with pytest.raises(_wp.SlotRefused):
        tools.add_recipe_for_chat("Cheese Board", [{"item": "brie", "qty": "1"}], override=True)
    tools.add_recipe_for_chat("grandma's cheesecake bars ", lines, override=True)
    assert "grandma's cheesecake bars" in {n.lower() for n in _recipe_names()}


def test_an_old_refusal_does_not_arm_an_override(dairy_free, monkeypatch):
    lines = [{"item": "cream cheese", "qty": "1 block"}]
    with pytest.raises(_wp.SlotRefused):
        tools.add_recipe_for_chat("Cheesecake Bars", lines)
    monkeypatch.setattr(allergen_gate, "RECIPE_OVERRIDE_WINDOW_SECONDS", -1)
    with pytest.raises(_wp.SlotRefused):
        tools.add_recipe_for_chat("Cheesecake Bars", lines, override=True)


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


def test_the_chat_prompt_names_add_recipe_in_the_allergy_paragraph():
    import inspect
    source = inspect.getsource(agent)
    start = source.index("`must_not_contain` — every member's dietary_restrictions")
    paragraph = source[start:start + 1400]
    assert "add_recipe declines the same way" in paragraph
