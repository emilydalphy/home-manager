"""
A title must never promise an ingredient the recipe hasn't got.

Emily, 2026-09-14, on her phone: "Seared Turkey and Zucchini Skillet with
White Beans" — seven ingredients, seven steps, and not a bean in either.
The title and the ingredient list come out of ONE model call
(agent.generate_weekly_plan_llm), so nothing downstream had ever compared
the two halves of the same answer to each other.

The fix corrects the NAME and never the ingredients — a recipe whose
groceries have already shipped must not grow a tin of beans nobody bought —
and it does it deterministically, with no second model call.

Most of this file is the NEGATIVE half. The rule's whole risk is the one
the allergy work wrote down on 2026-09-04: a check that fires on good
dinners is one people learn to click past. So there are far more titles
here that must be left exactly as they are than titles that must be
corrected, and they are drawn from dish names this repo's own tests and
fixtures already use.

Each test says whether it is a CATCH (red on main) or a GUARD (green on
main, there so the rule cannot get louder or quieter later). Measured
rather than reasoned: 12 of the 51 are red against origin/main and 39 are
green. The file cannot be collected against main as it stands — the three
names it imports do not exist there — so the measurement was taken with
those three stubbed to main's behaviour, which is no check at all. Every
guard whose redness a stub could not show is pinned by mutation instead,
and says which mutation in its own docstring.
"""
import datetime
import inspect

import pytest

from app import agent, tools
from app.tools import plan_quality
from app.tools.plan_quality import (
    honest_recipe_title,
    repair_recipe_titles,
    unkept_title_promises,
)


# Emily's recipe, exactly as it was on the Cook screen.
TICKET_TITLE = "Seared Turkey and Zucchini Skillet with White Beans"
TICKET_INGREDIENTS = [
    {"item": "Ground turkey", "qty": "2 lbs", "category": "meat"},
    {"item": "Zucchini", "qty": "4", "category": "produce"},
    {"item": "Cherry tomatoes", "qty": "2 pints", "category": "produce"},
    {"item": "Baby spinach", "qty": "10 oz", "category": "produce"},
    {"item": "Garlic", "qty": "8 cloves", "category": "produce"},
    {"item": "Lemon", "qty": "2", "category": "produce"},
    {"item": "Parmesan", "qty": "0.5 cup", "category": "dairy"},
]
TICKET_STEPS = [
    "Season the turkey and sear it in a hot skillet over medium-high heat until browned.",
    "Add the garlic and cook until fragrant, about 30 seconds.",
    "Add the zucchini and cherry tomatoes and cook until softened, 6 to 8 minutes.",
    "Stir in the baby spinach and cook until just wilted.",
    "Finish with a squeeze of lemon.",
    "Grate the parmesan over the top.",
    "Plate and serve.",
]
TICKET_HONEST = "Seared Turkey and Zucchini Skillet"


def _ing(*items) -> list[dict]:
    return [{"item": i, "qty": "1", "category": "produce"} for i in items]


def _week_start() -> str:
    today = datetime.date.today()
    monday = today - datetime.timedelta(days=today.weekday())
    return (monday + datetime.timedelta(days=7)).isoformat()


# ---------- the rule itself, with no database in sight ----------

def test_the_ticket_title_is_caught_and_corrected():
    """CATCH. The exact title and ingredient list Emily was handed."""
    assert unkept_title_promises(TICKET_TITLE, TICKET_INGREDIENTS, TICKET_STEPS) == ["White Beans"]
    assert honest_recipe_title(TICKET_TITLE, TICKET_INGREDIENTS, TICKET_STEPS) == TICKET_HONEST


def test_the_correction_never_invents_an_ingredient():
    """GUARD — green on main, where nothing rewrites anything. It is here so
    the safe direction stays the direction: correcting a title must never
    grow the list the grocery run was already built from. Mutation-checked
    rather than by redness."""
    before = [dict(i) for i in TICKET_INGREDIENTS]
    honest_recipe_title(TICKET_TITLE, TICKET_INGREDIENTS, TICKET_STEPS)
    assert TICKET_INGREDIENTS == before


def test_a_clause_that_names_several_things_keeps_the_real_ones():
    """CATCH. Dropping the whole clause would take a true half off with the
    false one."""
    ingredients = _ing("Chicken thighs", "Basmati rice", "Onion", "Stock")
    assert honest_recipe_title("Chicken with Rice and White Beans", ingredients, []) == "Chicken with Rice"


def test_the_beans_being_in_the_METHOD_leaves_the_title_alone():
    """GUARD — green on main, which flags nothing at all. The method is read
    as evidence on purpose: a dish whose steps really do cook white beans
    has an ingredient list with a hole in it, and renaming it would take a
    true title off to cover for a false list. That hole is
    _steps_match_ingredients' business, not this rule's. Mutation-checked:
    dropping `instructions` from _recipe_word_pool reddens it."""
    ingredients = _ing("Ground turkey", "Zucchini", "Garlic", "Lemon")
    steps = ["Sear the turkey.", "Stir in the drained white beans.", "Finish with lemon."]
    assert unkept_title_promises("Turkey Skillet with White Beans", ingredients, steps) == []


def test_a_title_whose_head_says_nothing_is_left_alone():
    """CATCH. "Bowl with White Beans" would become "Bowl". A bad name beats
    no name, so it is reported instead of rewritten."""
    ingredients = _ing("Rice", "Carrots", "Cucumber")
    assert unkept_title_promises("Bowl with White Beans", ingredients, []) == ["White Beans"]
    assert honest_recipe_title("Bowl with White Beans", ingredients, []) == "Bowl with White Beans"


def test_a_clause_naming_an_allergen_is_reported_and_never_stripped():
    """CATCH. Taking "with Peanuts" off quietly would take a fail-closed
    signal off every allergen check downstream, which reads the NAME as
    well as the ingredients. Worth a person's eyes, not a rename."""
    ingredients = _ing("Chicken thighs", "Rice", "Scallions", "Soy sauce")
    assert unkept_title_promises("Chicken Stir-Fry with Peanuts", ingredients, []) == ["Peanuts"]
    assert honest_recipe_title("Chicken Stir-Fry with Peanuts", ingredients, []) == "Chicken Stir-Fry with Peanuts"


def test_a_short_ingredient_list_is_no_answer_rather_than_a_short_one():
    """GUARD — green on main, which never rewrites. A model cut off mid-list,
    or a badly read web page, must not get its title rewritten against the
    two lines that survived. Mutation-checked: dropping
    _TITLE_MIN_INGREDIENTS to 1 reddens it."""
    assert unkept_title_promises("Toast with Jam", _ing("Bread", "Butter"), []) == []
    assert unkept_title_promises("Toast with Jam", [], []) == []


# ---------- the negative half: titles that must be left exactly as they are ----------

@pytest.mark.parametrize("name,ingredients", [
    # The four the ticket named by hand.
    ("Chicken Thighs with Herbed Rice", _ing("Chicken thighs", "Basmati rice", "Parsley", "Thyme", "Butter")),
    ("Steak with Garlic Butter Sauce", _ing("Sirloin steak", "Garlic", "Butter", "Parsley", "Salt")),
    ("Seared Turkey Skillet with Lemon", _ing("Ground turkey", "Zucchini", "Garlic", "Lemon", "Parmesan")),
    ("Chicken Traybake with a Crispy Top", _ing("Chicken", "Potatoes", "Breadcrumbs", "Olive oil")),
    ("Sheet Pan Salmon with Everything Seasoning", _ing("Salmon", "Broccoli", "Oil", "Everything bagel seasoning")),
    # Plural, canned and varietal differences.
    ("White Bean Stew with White Beans", _ing("Cannellini beans", "Carrots", "Celery", "Stock")),
    ("Grain Bowl with Chickpeas", _ing("Quinoa", "1 can (15 oz) chickpeas", "Cucumber", "Feta")),
    ("Yogurt with Berries", _ing("Greek yogurt", "Blueberries", "Honey")),
    ("Pasta with Peas", _ing("Spaghetti", "Frozen peas", "Parmesan", "Butter")),
    ("Risotto with Squash", _ing("Arborio rice", "Butternut squash", "Stock", "Parmesan")),
    # A category word where the ingredients are the specific things.
    ("Herb-Roasted Chicken with Roasted Root Vegetables", _ing("Chicken", "Carrots", "Parsnips", "Potatoes", "Thyme")),
    ("Sheet-Pan Chicken with Vegetables", _ing("Chicken thighs", "Courgette", "Red onion", "Peppers")),
    ("Roast Pork with Apple Sauce", _ing("Pork loin", "Apples", "Butter", "Cider")),
    ("Grilled Halloumi with Greens", _ing("Halloumi", "Rocket", "Lemon", "Olive oil")),
    # A preparation standing in for what it is made of.
    ("One-Pot Tomato Soup with Grilled Cheese Croutons",
     _ing("Tomatoes", "Onion", "Sourdough bread", "Sharp cheddar", "Stock")),
    ("Roast Chicken with Sage and Onion Stuffing",
     _ing("Whole chicken", "Sage", "Onion", "Stuffing mix", "Butter")),
    # Names this repo's own tests and fixtures already use.
    ("Baked Lemon Herb Salmon with Roasted Asparagus", _ing("Salmon", "Lemon", "Asparagus", "Dill", "Oil")),
    ("Garlic-Herb Shrimp with Roasted Broccolini", _ing("Shrimp", "Garlic", "Broccolini", "Parsley", "Oil")),
    ("Boneless Pork Chops with Apples", _ing("Pork chops", "Apples", "Onion", "Cider")),
    ("Broiled Salmon with Asparagus", _ing("Salmon", "Asparagus", "Lemon", "Oil")),
    ("Green Beans with Lemon", _ing("Green beans", "Lemon", "Olive oil")),
    ("Roast Chicken Thighs with Potatoes", _ing("Chicken thighs", "Yukon potatoes", "Rosemary")),
    ("Sheet-pan Sausages with Peppers", _ing("Italian sausages", "Bell peppers", "Onion", "Oil")),
    ("Cottage Cheese with Pineapple-Free Fruit Cup", _ing("Cottage cheese", "Melon", "Grapes", "Mint")),
    # An "and" that joins two halves of one name, not a side. Never checked.
    ("Mac and Cheese", _ing("Macaroni", "Cheddar", "Milk", "Butter")),
    ("Surf and Turf", _ing("Sirloin", "Shrimp", "Butter")),
    ("Beef and Broccoli Stir-Fry", _ing("Beef", "Broccoli", "Soy sauce", "Rice")),
    ("Hard-Boiled Eggs and Avocado Toast", _ing("Eggs", "Avocado", "Bread", "Chilli flakes")),
    ("Sage and Onion Stuffing", _ing("Bread", "Butter", "Onion", "Sage")),
    # No clause at all.
    ("Chili", _ing("Ground beef", "Kidney beans", "Tomatoes", "Onion")),
    ("Bean Chili", _ing("Black beans", "Tomatoes", "Onion", "Cumin")),
    # Twenty plausible generated dinners, swept as a batch while tuning the
    # word lists. Three of them were the only good titles the first cut
    # touched — the two named preparations (tzatziki, marinara) and the
    # baked-alongside (cornbread) — and they are the reason those entries
    # exist. The rest went through untouched from the start and are here so
    # they go on doing.
    ("Crispy Gnocchi with Sausage and Kale", _ing("Gnocchi", "Italian sausage", "Kale", "Garlic", "Chilli flakes")),
    ("Miso Salmon with Sesame Greens", _ing("Salmon", "White miso", "Bok choy", "Sesame oil", "Ginger")),
    ("Shakshuka with Feta and Warm Pita", _ing("Eggs", "Tomatoes", "Onion", "Feta", "Pita bread", "Cumin")),
    ("Chicken Souvlaki with Tzatziki", _ing("Chicken thighs", "Yogurt", "Cucumber", "Lemon", "Oregano")),
    ("Beef Tacos with Pickled Onions", _ing("Ground beef", "Tortillas", "Red onion", "Vinegar", "Lime")),
    ("Sheet Pan Gnocchi with Cherry Tomatoes", _ing("Gnocchi", "Cherry tomatoes", "Basil", "Olive oil", "Mozzarella")),
    ("Lentil Soup with Crusty Bread", _ing("Lentils", "Carrots", "Celery", "Stock", "Sourdough")),
    ("Thai Green Curry with Jasmine Rice", _ing("Chicken", "Green curry paste", "Coconut milk", "Jasmine rice", "Basil")),
    ("Pork Chops with Apple Slaw", _ing("Pork chops", "Apples", "Cabbage", "Mayonnaise", "Cider vinegar")),
    ("Baked Ziti with Italian Sausage", _ing("Ziti", "Italian sausage", "Passata", "Mozzarella", "Basil")),
    ("Cod with Brown Butter and Capers", _ing("Cod fillets", "Butter", "Capers", "Lemon", "Parsley")),
    ("Roast Cauliflower with Tahini", _ing("Cauliflower", "Tahini", "Lemon", "Cumin", "Parsley")),
    ("Egg Fried Rice with Peas and Spring Onion", _ing("Cooked rice", "Eggs", "Frozen peas", "Spring onions", "Soy sauce")),
    ("Chicken Caesar with Garlic Croutons", _ing("Chicken breast", "Romaine", "Parmesan", "Anchovies", "Baguette")),
    ("Turkey Meatballs with Marinara", _ing("Ground turkey", "Breadcrumbs", "Egg", "Passata", "Basil")),
    ("Sausage Traybake with Butter Beans", _ing("Sausages", "Fennel", "Red onion", "Butter beans", "Olive oil")),
    ("Salmon Rice Bowl with Avocado", _ing("Salmon", "Sushi rice", "Cucumber", "Avocado", "Soy sauce")),
    ("Harissa Chicken with Couscous", _ing("Chicken thighs", "Harissa", "Couscous", "Lemon", "Coriander")),
    ("Prawn Linguine with Chilli and Garlic", _ing("Prawns", "Linguine", "Red chilli", "Garlic", "Parsley")),
    ("Beef Chilli with Cornbread", _ing("Ground beef", "Kidney beans", "Tomatoes", "Cornmeal", "Buttermilk")),
])
def test_a_good_title_is_never_touched(name, ingredients):
    """GUARD. Every one of these passes on main too — the point is that it
    goes on passing. A rule that fires on these is worse than no rule."""
    assert unkept_title_promises(name, ingredients, []) == [], name
    assert honest_recipe_title(name, ingredients, []) == name, name


def test_a_real_missing_side_in_a_plausible_dinner_is_still_caught():
    """CATCH. The sweep above only proves the rule is quiet. This is the
    same shape of title with the thing genuinely absent — the avocado is on
    the name and on no line — so quiet has not become silent."""
    ingredients = _ing("Salmon", "Sushi rice", "Cucumber", "Soy sauce", "Sesame seeds")
    assert honest_recipe_title("Salmon Rice Bowl with Avocado", ingredients, []) == "Salmon Rice Bowl"


def test_a_colour_word_in_the_recipe_does_not_keep_a_promise_of_its_own():
    """CATCH — and the reason _TITLE_MODIFIERS exists. "White" says nothing
    about what a thing IS, so a bottle of white wine in the cupboard must
    not stand in for the beans. Without the modifier list this clause reads
    as kept and the bug goes straight back."""
    ingredients = _ing("Ground turkey", "Zucchini", "Garlic", "Dry white wine", "Parmesan")
    assert unkept_title_promises("Turkey Skillet with White Beans", ingredients, []) == ["White Beans"]


def test_the_alias_table_only_ever_makes_the_rule_quieter():
    """CATCH on its second half. Every entry in _TITLE_INGREDIENT_ALIASES can
    turn a would-be flag into a pass and never the other way round — the
    first assertion says an alias quietens, the second that it does not
    silence the rule outright."""
    ingredients = _ing("Cannellini beans", "Carrots", "Celery")
    assert unkept_title_promises("Stew with Beans", ingredients, []) == []
    assert unkept_title_promises("Stew with Quinoa", ingredients, []) == ["Quinoa"]


# ---------- the generated week, end to end ----------

@pytest.fixture
def stub_week(monkeypatch):
    """The week generator, canned — the same idea as test_full_plate's."""
    def _stub(days):
        monkeypatch.setattr(agent, "generate_weekly_plan_llm", lambda context: days)
    return _stub


def _full_week(week: str, overrides: dict | None = None) -> list[dict]:
    overrides = overrides or {}
    return [
        overrides.get((day, slot), {
            "date": day, "slot": slot, "meal_name": "Chili",
            "is_new_recipe": False, "reasoning": "it fit the week",
        })
        for day in tools._week_dates(week)
        for slot in tools.WEEK_SLOTS
    ]


def test_a_generated_week_never_saves_a_title_that_promises_beans(stub_week):
    """CATCH, and the one that matters: the whole path Emily's recipe took,
    from the model's answer to the recipe row and the week's own card."""
    week = _week_start()
    monday = tools._week_dates(week)[0]
    stub_week(_full_week(week, {(monday, "lunch"): {
        "date": monday, "slot": "lunch", "meal_name": TICKET_TITLE,
        "is_new_recipe": True, "reasoning": "a big skillet that covers Friday too",
        "ingredients": TICKET_INGREDIENTS, "instructions": TICKET_STEPS,
        "default_servings": 4,
    }}))

    plan = agent.generate_weekly_plan(week)

    saved = [r["name"] for r in tools.list_recipes()]
    assert TICKET_HONEST in saved
    assert TICKET_TITLE not in saved
    meals = {(m["date"], m["slot"]): m["meal"] for m in tools.get_weekly_plan(plan["weekly_plan_id"])["meals"]}
    # The card and the recipe have to agree, or the same dinner is called
    # two different things on two screens.
    assert meals[(monday, "lunch")] == TICKET_HONEST


def test_an_item_with_no_ingredient_list_keeps_its_name(stub_week):
    """GUARD. A reused saved recipe comes back by name with no ingredients
    on it; there is nothing to check it against, so nothing is changed."""
    week = _week_start()
    monday = tools._week_dates(week)[0]
    stub_week(_full_week(week, {(monday, "dinner"): {
        "date": monday, "slot": "dinner", "meal_name": "Takeout with Dumplings",
        "is_new_recipe": False, "reasoning": "nobody is cooking on a Monday",
    }}))

    plan = agent.generate_weekly_plan(week)

    meals = {(m["date"], m["slot"]): m["meal"] for m in tools.get_weekly_plan(plan["weekly_plan_id"])["meals"]}
    assert meals[(monday, "dinner")] == "Takeout with Dumplings"


def test_the_repair_costs_no_model_call(stub_week, monkeypatch):
    """GUARD on the ticket's own rule: the check is deterministic, so a week
    full of over-promising titles must not buy a single round trip."""
    calls = []
    monkeypatch.setattr(agent, "_create_with_retry", lambda *a, **k: calls.append(a))
    week = _week_start()
    monday = tools._week_dates(week)[0]
    stub_week(_full_week(week, {(monday, "lunch"): {
        "date": monday, "slot": "lunch", "meal_name": TICKET_TITLE,
        "is_new_recipe": True, "reasoning": "a big skillet",
        "ingredients": TICKET_INGREDIENTS, "instructions": TICKET_STEPS,
    }}))

    agent.generate_weekly_plan(week)

    assert calls == []


# ---------- the week's own quality report ----------

def test_the_week_checker_reports_a_title_it_could_not_correct():
    """CATCH. The morning report is where anything the repair passed over —
    an older recipe, one brought in from a link, a head too thin to trim —
    becomes legible."""
    entry = {
        "date": "2026-09-14", "slot": "lunch", "slot_state": "planned",
        "meal_name": TICKET_TITLE, "reasoning": "a big skillet that covers Friday",
        "food_groups": ["protein", "vegetable"], "main_protein": "turkey",
        "prep_time_minutes": 10, "cook_time_minutes": 20, "is_new_recipe": False,
        "links_to": None, "ingredients": TICKET_INGREDIENTS,
        "instructions": TICKET_STEPS, "default_servings": 4,
    }
    hits = [v for v in plan_quality.check_week([entry], {}) if v.rule == "title_promises_an_ingredient"]
    assert len(hits) == 1
    assert "White Beans" in hits[0].message
    assert hits[0].severity == "warn"


def test_the_week_checker_says_nothing_about_a_good_title():
    """GUARD."""
    entry = {
        "date": "2026-09-14", "slot": "dinner", "slot_state": "planned",
        "meal_name": "Baked Lemon Herb Salmon with Roasted Asparagus",
        "reasoning": "you said you love salmon", "food_groups": ["protein", "vegetable"],
        "main_protein": "salmon", "prep_time_minutes": 10, "cook_time_minutes": 20,
        "is_new_recipe": False, "links_to": None,
        "ingredients": _ing("Salmon fillets", "Lemon", "Asparagus", "Dill", "Olive oil"),
        "instructions": ["Roast the salmon at 400F until it flakes.", "Roast the asparagus alongside."],
        "default_servings": 4,
    }
    assert [v for v in plan_quality.check_week([entry], {}) if v.rule == "title_promises_an_ingredient"] == []


# ---------- what is already on record ----------

def test_the_backfill_corrects_a_recipe_already_saved():
    """CATCH. Emily's Friday leftover of this dish, read honestly."""
    tools.add_recipe(name=TICKET_TITLE, ingredients=TICKET_INGREDIENTS, instructions=TICKET_STEPS)

    changes = repair_recipe_titles(apply=True)

    assert changes == [{"recipe_id": changes[0]["recipe_id"], "before": TICKET_TITLE, "after": TICKET_HONEST}]
    assert [r["name"] for r in tools.list_recipes()] == [TICKET_HONEST]
    # And the ingredients are exactly as they were bought.
    assert [i["item"] for i in tools.list_recipes()[0]["ingredients"]] == [i["item"] for i in TICKET_INGREDIENTS]


def test_the_backfill_changes_nothing_until_it_is_asked_to():
    """CATCH. A rename has no undo, so the default is a list, not a write."""
    tools.add_recipe(name=TICKET_TITLE, ingredients=TICKET_INGREDIENTS, instructions=TICKET_STEPS)

    changes = repair_recipe_titles()

    assert [c["after"] for c in changes] == [TICKET_HONEST]
    assert [r["name"] for r in tools.list_recipes()] == [TICKET_TITLE]


def test_the_backfill_runs_twice_without_doing_anything_twice():
    """CATCH. A corrected title has nothing left to take off."""
    tools.add_recipe(name=TICKET_TITLE, ingredients=TICKET_INGREDIENTS, instructions=TICKET_STEPS)

    repair_recipe_titles(apply=True)
    assert repair_recipe_titles(apply=True) == []
    assert [r["name"] for r in tools.list_recipes()] == [TICKET_HONEST]


def test_the_backfill_refuses_to_make_two_recipes_of_one_name():
    """GUARD — green on main, which renames nothing and so can collide with
    nothing. Several lookups are `WHERE name = ?` with one row expected, so
    two recipes answering to one name is the worse problem than an
    inaccurate title. Mutation-checked: removing the `taken` guard reddens
    it."""
    tools.add_recipe(name=TICKET_HONEST, ingredients=_ing("Ground turkey", "Zucchini", "Garlic"))
    tools.add_recipe(name=TICKET_TITLE, ingredients=TICKET_INGREDIENTS, instructions=TICKET_STEPS)

    assert repair_recipe_titles(apply=True) == []
    assert sorted(r["name"] for r in tools.list_recipes()) == sorted([TICKET_HONEST, TICKET_TITLE])


def test_the_backfill_stays_inside_one_household():
    """GUARD, in this repo's standing shape for anything that writes."""
    tools.add_recipe(name=TICKET_TITLE, ingredients=TICKET_INGREDIENTS, instructions=TICKET_STEPS)
    from app.db import get_conn

    conn = get_conn()
    conn.execute("INSERT INTO households (id, name) VALUES (99, 'Next door')")
    conn.commit()
    conn.close()

    with tools.use_household(99):
        assert repair_recipe_titles(apply=True) == []
    assert [r["name"] for r in tools.list_recipes()] == [TICKET_TITLE]


# ---------- the wiring, pinned ----------

def test_every_generated_recipe_path_goes_through_the_same_correction():
    """Red on main for the trivial reason that none of this exists there, so
    it is a marker rather than a catch. The week generator and the in-place
    swap both write a recipe whose name and ingredients came out of one
    model call; a third one added later should have to notice this test."""
    from app.tools import swap_in_place

    assert "honest_recipe_title" in inspect.getsource(agent._honest_meal_names)
    assert "_honest_meal_names(items)" in inspect.getsource(agent._generate_weekly_plan)
    assert "honest_recipe_title" in inspect.getsource(swap_in_place.apply_pick)
