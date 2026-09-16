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
rather than reasoned, and the count is re-measured whenever this file
grows — see the run recorded in the decision log. The file cannot be
collected against main as it stands (the three names it imports do not
exist there), so the measurement is taken with those three stubbed to
main's behaviour, which is no check at all. Every guard whose redness a
stub could not show is pinned by mutation instead, and says which mutation
in its own docstring.

The last section is the 2026-09-15 review: three reproduced blockers about
what a CORRECTED NAME does to the rest of the app, and the corpus of good
titles the first cut renamed.
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
from conftest import agent_function_source


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
    silence the rule outright.

    The first ingredient is a BARE "Cannellini", not "Cannellini beans",
    and that is the whole point of this test. Its earlier version wrote
    the full name, so the clause "with Beans" was answered by the word
    "beans" sitting in the ingredient string and the assertion passed
    with `_TITLE_FOOD_GROUPS` emptied — i.e. the one test in this file
    claiming to pin the alias table proved nothing about it, which is
    itself an instance of the forgiven-vs-invisible confusion this file
    documents. Found on review, 2026-09-15. Mutation-checked: emptying
    the table now reddens the first assertion."""
    ingredients = _ing("Cannellini", "Carrots", "Celery")
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

    assert changes == [{"recipe_id": changes[0]["recipe_id"], "before": TICKET_TITLE,
                        "after": TICKET_HONEST, "why": None}]
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
    it. The refusal is REPORTED rather than swallowed — the person running
    the script is deciding whether to write."""
    tools.add_recipe(name=TICKET_HONEST, ingredients=_ing("Ground turkey", "Zucchini", "Garlic"))
    tools.add_recipe(name=TICKET_TITLE, ingredients=TICKET_INGREDIENTS, instructions=TICKET_STEPS)

    found = repair_recipe_titles(apply=True)

    assert [f["after"] for f in found] == [None]
    assert found[0]["before"] == TICKET_TITLE and found[0]["why"]
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

    # agent_function_source, not inspect.getsource, for the two that live in
    # app/agent.py: getsource pairs line numbers baked in at import time
    # against the file as it reads NOW, so a merge landing mid-run can return
    # a different function's body and redden this for a reason that is not
    # real (tests/conftest.py, and the 2026-09-15 failure it records). It is
    # byte-identical to what getsource returned for both of these, so the two
    # assertions below see exactly the bytes they always saw. The other three
    # modules are not swept here: this file is not that ticket's, and the same
    # helper for tools/ does not exist yet.
    assert "honest_recipe_title" in agent_function_source("_honest_meal_names")
    assert "_honest_meal_names(items)" in agent_function_source("_generate_weekly_plan")
    assert "honest_meal_name(pick)" in inspect.getsource(swap_in_place.apply_pick)
    assert "honest_recipe_title" in inspect.getsource(swap_in_place.honest_meal_name)
    from app.tools import big_meal
    assert "honest_recipe_title" in inspect.getsource(big_meal._clean_main)
    # ...and the one caller that must NOT be corrected says so at the call.
    # COMMENTS STRIPPED FIRST: the comment above that call explains itself
    # using the same literal, so searching the raw source made this test
    # green with the argument deleted. Review found that, and it is the
    # standing hazard with every source-marker test in this repo.
    from app.tools import plate_parts
    code = "\n".join(
        line.split("#")[0] for line in inspect.getsource(plate_parts.change_part).splitlines()
    )
    assert "correct_title=False" in code


# ---------------------------------------------------------------------------
# Review, 2026-09-15. The rule itself held; what did not was everything a
# corrected name touches on its way out.
# ---------------------------------------------------------------------------

REVIEW_GOOD_TITLES = [
    # Named preparations — a sauce or a paste made from things the list does
    # name. This app's own prompt asks for exactly this shape ("Every dinner
    # plate carries a sauce, dressing, broth or spoonable something"), and no
    # blocklist bounds the tail, which is why the rule now judges only words
    # it has a vocabulary for.
    ("Chicken Shawarma with Toum", _ing("Chicken thighs", "Garlic", "Lemon", "Yogurt", "Pita")),
    ("Slow-Roast Pork with Mojo", _ing("Pork shoulder", "Orange", "Garlic", "Oregano", "Olive oil")),
    ("Grilled Lamb with Chermoula", _ing("Lamb", "Cilantro", "Parsley", "Cumin", "Lemon")),
    ("Roast Chicken with Sofrito", _ing("Chicken", "Onion", "Peppers", "Garlic", "Tomato")),
    ("Beef Bulgogi with Ssamjang", _ing("Beef", "Soy sauce", "Pear", "Garlic", "Rice")),
    ("Grilled Fish with Zhoug", _ing("Sea bass", "Cilantro", "Green chilli", "Cardamom", "Olive oil")),
    ("Chicken Tinga with Crema", _ing("Chicken", "Chipotle", "Tomato", "Sour cream", "Tortillas")),
    ("Tacos with Pico de Gallo", _ing("Ground beef", "Tortillas", "Tomato", "Onion", "Lime")),
    ("Salmon with Beurre Blanc", _ing("Salmon", "Butter", "Shallot", "White wine", "Cream")),
    ("Chicken with Aji Verde", _ing("Chicken", "Cilantro", "Jalapeno", "Mayonnaise", "Lime")),
    ("Shrimp with Nuoc Cham", _ing("Shrimp", "Fish sauce", "Lime", "Sugar", "Garlic")),
    ("Chicken with Ranch", _ing("Chicken breast", "Buttermilk", "Dill", "Garlic", "Mayonnaise")),
    ("Noodles with Chilli Crisp", _ing("Egg noodles", "Chilli oil", "Soy sauce", "Spring onion", "Sesame")),
    # The alias table read only one way: every word listed as a SATISFIER was
    # an unsatisfiable PROMISE.
    ("Chicken with Orzo", _ing("Chicken thighs", "Pasta", "Stock", "Lemon", "Parsley")),
    ("Curry with Basmati", _ing("Chicken", "Curry paste", "Coconut milk", "Rice", "Coriander")),
    ("Soup with Ciabatta", _ing("Tomatoes", "Onion", "Stock", "Sourdough", "Basil")),
    ("Soup with Sourdough", _ing("Lentils", "Carrots", "Stock", "Bread", "Thyme")),
    ("Curry with Naan", _ing("Chickpeas", "Coconut milk", "Spinach", "Flatbread", "Cumin")),
    ("Curry with Flatbread", _ing("Chickpeas", "Coconut milk", "Spinach", "Naan", "Cumin")),
    ("Stir-Fry with Mangetout", _ing("Beef", "Snow peas", "Soy sauce", "Rice", "Ginger")),
    # The same food in two Englishes. This repo's own fixtures write both.
    ("Traybake with Courgette", _ing("Chicken", "Zucchini", "Red onion", "Olive oil", "Oregano")),
    ("Moussaka with Aubergine", _ing("Lamb", "Eggplant", "Tomato", "Cinnamon", "Potato")),
    ("Curry with Coriander", _ing("Chicken", "Cilantro", "Cumin", "Coconut milk", "Rice")),
    ("Salad with Rocket", _ing("Arugula", "Parmesan", "Lemon", "Olive oil", "Pear")),
    ("Stew with Swede", _ing("Beef", "Rutabaga", "Carrots", "Stock", "Thyme")),
    ("Stir-Fry with Prawns", _ing("Shrimp", "Broccoli", "Soy sauce", "Rice", "Ginger")),
    # A clause that isn't a food at all.
    ("Chili with a Kick", _ing("Ground beef", "Kidney beans", "Chipotle", "Tomatoes", "Onion")),
    ("Burger with the Works", _ing("Ground beef", "Buns", "Lettuce", "Tomato", "Cheddar")),
]


@pytest.mark.parametrize("name,ingredients", REVIEW_GOOD_TITLES)
def test_a_title_the_first_cut_renamed_wrongly_is_left_alone(name, ingredients):
    """CATCH against this branch's own first commit, GUARD against main.

    All 28 were renamed by the version that stripped unless the word was on
    a list of category words. None is flagged now, so none reaches the
    morning report either — a warning nobody can clear is the same failure
    one level over."""
    assert unkept_title_promises(name, ingredients, []) == [], name
    assert honest_recipe_title(name, ingredients, []) == name, name


@pytest.mark.parametrize("name,ingredients,expected", [
    (TICKET_TITLE, TICKET_INGREDIENTS, TICKET_HONEST),
    ("Risotto with Mushrooms", _ing("Arborio rice", "Stock", "Parmesan", "Onion", "Butter"), "Risotto"),
    ("Chicken Traybake with Broccoli",
     _ing("Chicken thighs", "Potatoes", "Carrots", "Thyme", "Oil"), "Chicken Traybake"),
    ("Steak with Garlic Butter", _ing("Sirloin", "Rosemary", "Potatoes", "Salt", "Oil"), "Steak"),
    ("Salmon Rice Bowl with Avocado",
     _ing("Salmon", "Sushi rice", "Cucumber", "Soy sauce", "Sesame seeds"), "Salmon Rice Bowl"),
])
def test_quiet_has_not_become_silent(name, ingredients, expected):
    """CATCH. The controls the review kept beside its 28 — every one a food
    the app knows, genuinely absent. "Steak with Garlic Butter" is here
    because the first cut of the allergen carve-out swallowed it: butter is
    in the dairy family, and carving out every allergen-family MEMBER took
    butter, cheese, bread and pasta off the rule entirely."""
    assert honest_recipe_title(name, ingredients, []) == expected


def test_a_reused_saved_recipe_is_never_renamed_off_its_own_recipe():
    """CATCH against this branch's own first commit. The correction is gated
    on exactly what _ensure_recipe_saved is gated on. Without that, a model
    reusing a saved recipe and echoing a list missing the clause word
    renames the dish off the row it names, plan_meal finds nothing under the
    new name, and the slot lands FREEFORM — no recipe, no steps, and nothing
    on the shopping list at approval."""
    tools.add_recipe(name="Chicken Traybake with Mushrooms",
                     ingredients=_ing("Chicken thighs", "Mushrooms", "Potatoes", "Paprika", "Onion"))
    items = [{"meal_name": "Chicken Traybake with Mushrooms", "is_new_recipe": False,
              "ingredients": _ing("Chicken thighs", "Potatoes", "Paprika", "Onion")}]

    agent._honest_meal_names(items)

    assert items[0]["meal_name"] == "Chicken Traybake with Mushrooms"


def test_a_generated_week_reusing_a_recipe_still_lands_on_its_recipe(stub_week):
    """CATCH against this branch's own first commit, end to end: the failure
    above is only visible as a freeform entry three steps later."""
    tools.add_recipe(name="Chicken Traybake with Mushrooms",
                     ingredients=_ing("Chicken thighs", "Mushrooms", "Potatoes", "Paprika", "Onion"),
                     food_groups=["protein", "carb"], instructions=["Roast it all."])
    week = _week_start()
    monday = tools._week_dates(week)[0]
    stub_week(_full_week(week, {(monday, "dinner"): {
        "date": monday, "slot": "dinner", "meal_name": "Chicken Traybake with Mushrooms",
        "is_new_recipe": False, "reasoning": "you liked it last time",
        "ingredients": _ing("Chicken thighs", "Potatoes", "Paprika", "Onion"),
    }}))

    plan = agent.generate_weekly_plan(week)

    entry = next(m for m in tools.get_weekly_plan(plan["weekly_plan_id"])["meals"]
                 if m["date"] == monday and m["slot"] == "dinner")
    assert entry["meal"] == "Chicken Traybake with Mushrooms"
    # Read the row itself: a freeform entry is exactly "no recipe_id", and
    # that is the whole failure — the name alone cannot show it.
    from app.db import get_conn
    conn = get_conn()
    row = conn.execute("SELECT recipe_id, freeform_meal FROM meal_plan_entries WHERE id = ?",
                       (entry["entry_id"],)).fetchone()
    conn.close()
    assert row["recipe_id"], "a reused recipe must not land as a freeform entry"
    assert row["freeform_meal"] is None


def test_a_correction_onto_another_recipes_name_is_refused():
    """CATCH against this branch's own first commit. _ensure_recipe_saved is
    skip-if-the-name-exists and plan_meal resolves `WHERE name = ?`, so a
    corrected name landing on a different saved recipe silently points the
    slot at a different dinner — and the week shops for it. The correction
    makes that likelier, because taking the distinguishing words off is what
    causes the collision."""
    tools.add_recipe(name="Chicken Thighs with Rice",
                     ingredients=_ing("Chicken thighs", "Rice", "Peanut butter", "Soy sauce"))
    items = [{"meal_name": "Chicken Thighs with Rice and Black Beans", "is_new_recipe": True,
              "ingredients": _ing("Chicken thighs", "Rice", "Onion", "Stock", "Cumin")}]

    agent._honest_meal_names(items)

    assert items[0]["meal_name"] == "Chicken Thighs with Rice and Black Beans"


def test_two_new_dishes_cannot_be_corrected_onto_each_other():
    """CATCH against this branch's own first commit — the same collision
    between two dishes in one generation, where neither is on disk yet."""
    items = [
        {"meal_name": "Chicken and Rice with Black Beans", "is_new_recipe": True,
         "ingredients": _ing("Chicken", "Rice", "Onion", "Stock", "Cumin")},
        {"meal_name": "Chicken and Rice with Mushrooms", "is_new_recipe": True,
         "ingredients": _ing("Chicken", "Rice", "Carrot", "Stock", "Thyme")},
    ]

    agent._honest_meal_names(items)

    assert items[0]["meal_name"] == "Chicken and Rice"
    assert items[1]["meal_name"] == "Chicken and Rice with Mushrooms"


def test_changing_a_protein_still_saves_the_new_recipe():
    """CATCH against this branch's own first commit, and the worst of the
    three: plate_parts._variant_name builds "<base> with <choice>" BECAUSE
    the base name is taken, so correcting the clause off handed back a name
    _save_recipe_if_new refuses and the OLD dish was planned again and
    reported as a change. Reproduced with the user typing "mince" and the
    model writing the list in its own words."""
    from app.tools import plate_parts

    tools.add_recipe(name="Chili", ingredients=_ing("Ground beef", "Kidney beans", "Tomatoes", "Onion"),
                     food_groups=["protein"], instructions=["Brown the beef.", "Simmer."])
    week = _week_start()
    plan = tools.create_weekly_plan(week)
    entry = tools.plan_meal(meal_date=week, meal="Chili", slot="dinner",
                            weekly_plan_id=plan["weekly_plan_id"])

    def ask(context):
        return {"meal_name": "Chili", "food_groups": ["protein"], "default_servings": 4,
                "ingredients": _ing("Ground beef", "Kidney beans", "Tomatoes", "Onion"),
                "instructions": ["Brown it.", "Simmer."]}

    out = plate_parts.change_part(plan["weekly_plan_id"], entry["entry_id"], "protein", "mince", asker=ask)

    assert out["status"] == "changed"
    assert out["meal"] == "Chili with mince"
    assert "Chili with mince" in [r["name"] for r in tools.list_recipes()]


def test_a_caller_can_say_its_name_is_not_a_description():
    """GUARD on `correct_title`, and it says what the test above cannot.

    plate_parts is safe today for TWO reasons — its opt-out, and the fact
    that _variant_name's base is always a name the household already uses,
    which honest_recipe_title refuses to correct onto. So removing the
    opt-out reddens nothing, and the parameter has to be pinned directly or
    it reads as dead. It is not: it is what keeps that caller right if
    _variant_name ever builds a name from an untaken base."""
    tools.add_recipe(name="Bean Chili", ingredients=_ing("Black beans", "Tomatoes", "Onion"))
    week = _week_start()
    plan = tools.create_weekly_plan(week)
    e = tools.plan_meal(meal_date=week, meal="Bean Chili", slot="dinner",
                        weekly_plan_id=plan["weekly_plan_id"])
    from app.tools import swap_in_place

    entry = swap_in_place._entry(plan["weekly_plan_id"], e["entry_id"])
    pick = {"meal_name": "Weeknight Skillet with Mushrooms", "food_groups": ["protein"],
            "ingredients": _ing("Ground turkey", "Zucchini", "Garlic", "Parmesan"),
            "instructions": ["Cook it."], "default_servings": 4}

    # The default corrects... (asked FIRST: applying the pick saves a recipe
    # under that name, and a name that is already a recipe is one this rule
    # refuses to touch at all.)
    assert swap_in_place.honest_meal_name(pick) == "Weeknight Skillet"

    # ...and the opt-out does not.
    out = swap_in_place.apply_pick(plan["weekly_plan_id"], entry, dict(pick), correct_title=False)
    assert out["meal"] == "Weeknight Skillet with Mushrooms"


def test_the_holiday_big_meal_saves_an_honest_title_too():
    """CATCH against this branch's own first commit, where big_meal was left
    out and a dishonest title really was SAVED there — which the ticket's
    own acceptance criteria forbid, not merely a reporting gap. Corrected in
    _clean_main so the recipe, the menu record, the prep rows and the
    timeline all say one thing."""
    from app.tools import big_meal

    main = big_meal._clean_main({
        "name": "Roast Turkey with White Beans",
        "ingredients": [{"item": i, "qty": "1", "category": "meat"} for i in
                        ("Whole turkey", "Butter", "Sage", "Onion", "Stock")],
        "instructions": ["Roast the turkey.", "Rest it.", "Make the gravy."],
    }, eaters=8)

    assert main["name"] == "Roast Turkey"


def test_the_rule_only_judges_words_this_app_has_a_vocabulary_for():
    """CATCH against this branch's own first commit, which had no such gate.
    The vocabulary is assembled from tables this app already maintains for
    other jobs, never hand-written for this rule, so it grows with the app
    rather than rotting. Mutation-checked: dropping the gate reddens 14 of
    the 28 above."""
    known = plan_quality._known_food_words()
    assert {"bean", "mushroom", "broccoli", "avocado", "lemon"} <= known
    assert not ({"toum", "mojo", "crema", "ssamjang", "zhoug"} & known)


def test_the_alias_table_is_read_in_both_directions():
    """CATCH against this branch's own first commit. Orzo answers for pasta
    and pasta for orzo; reading it one way made every satisfier an
    unsatisfiable promise."""
    assert "pasta" in plan_quality._same_food("orzo")
    assert "orzo" in plan_quality._same_food("pasta")
    assert "sourdough" in plan_quality._same_food("ciabatta")


# ---------------------------------------------------------------------------
# Re-review, 2026-09-15. One root: the correction was being applied to names
# that IDENTIFY AN EXISTING ROW, judged against a model's echo of that row.
# ---------------------------------------------------------------------------

def test_a_name_that_is_already_a_recipe_is_never_corrected():
    """CATCH against b4f0304. The one guard, stated on its own: a saved
    recipe's name does not describe a dish, it names a row — and what it is
    judged against here is somebody's restatement of that row, which can be
    missing the very word the clause names."""
    saved = _ing("Beef chuck", "Carrots", "Onion", "Stock", "Thyme")
    tools.add_recipe(name="Beef Stew with Mushrooms", ingredients=saved,
                     instructions=["Brown the beef.", "Add the mushrooms.", "Simmer."])
    taken = {"beef stew with mushrooms"}

    assert honest_recipe_title("Beef Stew with Mushrooms", saved, [], taken=taken) == "Beef Stew with Mushrooms"
    # ...and with no `taken` it would still be corrected, which is why every
    # live write path has to pass one.
    assert honest_recipe_title("Beef Stew with Mushrooms", saved, []) == "Beef Stew"


def test_a_swap_reusing_a_saved_recipe_does_not_fork_a_stepless_duplicate():
    """CATCH against b4f0304. apply_pick never got the is_new_recipe gate
    agent._honest_meal_names did, so a picker reusing a household recipe and
    restating its ingredients (but not its steps) renamed the dish off its
    own row, planted a stepless duplicate under the short name, and orphaned
    the real recipe — its steps, its times and its mushrooms."""
    from app.tools import swap_in_place

    tools.add_recipe(name="Beef Stew with Mushrooms",
                     ingredients=_ing("Beef chuck", "Carrots", "Onion", "Stock", "Thyme"),
                     instructions=["Brown the beef.", "Add the mushrooms.", "Simmer 40 minutes."],
                     prep_time_minutes=10, cook_time_minutes=40, food_groups=["protein"])
    tools.add_recipe(name="Chili", ingredients=_ing("Ground beef", "Beans", "Tomatoes"),
                     instructions=["Simmer."])
    week = _week_start()
    plan = tools.create_weekly_plan(week)
    e = tools.plan_meal(meal_date=week, meal="Chili", slot="dinner",
                        weekly_plan_id=plan["weekly_plan_id"])
    entry = swap_in_place._entry(plan["weekly_plan_id"], e["entry_id"])

    out = swap_in_place.apply_pick(plan["weekly_plan_id"], entry, {
        "meal_name": "Beef Stew with Mushrooms", "is_new_recipe": False,
        "food_groups": ["protein"], "default_servings": 4,
        "ingredients": _ing("Beef chuck", "Carrots", "Onion", "Stock", "Thyme"),
    })

    assert out["meal"] == "Beef Stew with Mushrooms"
    assert len(tools.list_recipes()) == 2, "no third, stepless recipe"
    from app.db import get_conn
    conn = get_conn()
    row = conn.execute("SELECT recipe_id FROM meal_plan_entries WHERE id = ?", (out["entry_id"],)).fetchone()
    steps = conn.execute("SELECT instructions_json FROM recipes WHERE id = ?", (row["recipe_id"],)).fetchone()
    conn.close()
    assert "Simmer 40 minutes." in steps["instructions_json"], "the slot must point at the real recipe"


def test_a_model_that_mislabels_a_reuse_as_new_is_covered_too():
    """CATCH against b4f0304. The is_new_recipe gates cannot see this case —
    it is exactly what _ensure_recipe_saved's own `if not existing` guard
    exists for — and the name guard can."""
    tools.add_recipe(name="Chicken Traybake with Mushrooms",
                     ingredients=_ing("Chicken thighs", "Mushrooms", "Potatoes", "Paprika", "Onion"))
    items = [{"meal_name": "Chicken Traybake with Mushrooms", "is_new_recipe": True,
              "ingredients": _ing("Chicken thighs", "Potatoes", "Paprika", "Onion")}]

    agent._honest_meal_names(items)

    assert items[0]["meal_name"] == "Chicken Traybake with Mushrooms"


def test_the_big_meal_keeps_a_reused_recipes_method_and_clocks():
    """CATCH against b4f0304. set_big_meal_dish(role='main') looked the dish
    up from the household's own recipe for its INGREDIENTS and then passed
    `instructions or []`, so it judged a saved title with the method thrown
    away — and the timeline lost the times to work back from."""
    from app.tools import big_meal
    import inspect

    code = inspect.getsource(big_meal.set_big_meal_dish)
    assert 'match.get("instructions")' in code
    assert 'match.get("prep_time_minutes")' in code
    assert 'match.get("cook_time_minutes")' in code


@pytest.mark.parametrize("title,ingredients", [
    ("Chicken with Sweet Potatoes", _ing("Chicken thighs", "Sweet potato", "Onion", "Oil", "Paprika")),
    ("Chicken with Potatoes", _ing("Chicken thighs", "Potato", "Onion", "Oil", "Rosemary")),
    ("Traybake with Sweet Potato", _ing("Chicken", "Sweet potatoes", "Onion", "Oil", "Cumin")),
    ("Chicken with Tomatoes", _ing("Chicken", "Cherry tomato", "Garlic", "Basil", "Oil")),
    ("Skillet with Tomato", _ing("Ground turkey", "Cherry tomatoes", "Garlic", "Spinach", "Parmesan")),
])
def test_a_plural_in_oes_still_finds_its_singular(title, ingredients):
    """CATCH against b4f0304. _stem's `es` branch handled shes/ches/xes/ses
    only, so "potatoes" became "potatoe" and matched nothing — and whether a
    title agreed with its own ingredient line was a coin flip on which
    plural each happened to use. Both corpora missed it because every potato
    and tomato in them was spelled the agreeing way. Two of the commonest
    words in a dinner title."""
    assert unkept_title_promises(title, ingredients, []) == []
    assert honest_recipe_title(title, ingredients, []) == title


@pytest.mark.parametrize("title,ingredients,expected", [
    ("Lentil Salad with Feta", _ing("Lentils", "Cucumber", "Tomato", "Olive oil", "Lemon"), "Lentil Salad"),
    ("Chicken Salad with Avocado", _ing("Chicken", "Romaine", "Tomato", "Olive oil", "Lemon"), "Chicken Salad"),
    ("Pork Ragu with Peas", _ing("Pork shoulder", "Passata", "Onion", "Pasta", "Garlic"), "Pork Ragu"),
    ("Beef Hash with Mushrooms", _ing("Beef", "Potato", "Onion", "Eggs", "Paprika"), "Beef Hash"),
    ("Winter Slaw with Apples", _ing("Cabbage", "Carrot", "Mayonnaise", "Vinegar", "Parsley"), "Winter Slaw"),
])
def test_a_head_containing_a_vague_word_can_still_be_corrected(title, ingredients, expected):
    """CATCH against b4f0304, which asked _title_content_stems — a function
    that answers "can I read this as a promise" — the different question
    "does this name say anything". Any head with salad, slaw, hash, ragu or
    mash in it was detected and never correctable: a permanent line in the
    morning report, on an extremely common shape of generated title."""
    assert honest_recipe_title(title, ingredients, []) == expected


def test_a_head_that_really_says_nothing_is_still_refused():
    """GUARD on the fix above not going too far — mutation-checked against a
    version that only checks for a non-empty word list."""
    assert honest_recipe_title("Salad with Feta",
                               _ing("Romaine", "Cucumber", "Tomato", "Olive oil", "Lemon"), []) == "Salad with Feta"


@pytest.mark.parametrize("title,ingredients", [
    ("Battered Cod with Chips", _ing("Cod", "Flour", "French fries", "Malt vinegar", "Peas")),
    ("Steak with Fries", _ing("Sirloin", "Potato chips", "Butter", "Salt", "Rocket")),
    ("Sandwich with Chips", _ing("Bread", "Ham", "Crisps", "Butter", "Lettuce")),
])
def test_a_word_that_means_two_foods_in_two_englishes_is_not_judged(title, ingredients):
    """CATCH against b4f0304. British chips are American fries; American
    chips are British crisps. No table reconciles that, so the rule cannot
    say what the clause promises and must not guess."""
    assert unkept_title_promises(title, ingredients, []) == []


def test_an_alias_can_only_ever_make_the_rule_quieter():
    """CATCH against b4f0304, and the claim the whole known-food gate rests
    on. The alias table used to feed the vocabulary — 91 of 418 words came
    from it — so ADDING AN ALIAS MADE THE RULE LOUDER by making that word
    judgeable, which is the opposite of what its own comment promises. Out
    of the vocabulary now, so the comment is true."""
    known = plan_quality._known_food_words()
    alias_only = {w for w in plan_quality._TITLE_FOOD_GROUPS
                  if w in ("orzo", "courgette", "sourdough", "chorizo", "mangetout", "sirloin")}
    assert alias_only, "the table still holds these words"
    assert not (alias_only & known), "an alias word must not become judgeable"
    # ...and it still does its real job: quietening a word the app does know.
    assert unkept_title_promises("Stew with Beans",
                                 _ing("Cannellini beans", "Carrots", "Celery"), []) == []


def test_no_alias_entry_is_silently_dead():
    """GUARD. Groups are keyed on stems and lookups are stemmed, so an entry
    written in a form that stems to something else would sit there matching
    nothing — which is what "chip: {fries}" did, while still making its
    words judgeable."""
    for key, family in plan_quality._TITLE_INGREDIENT_ALIASES.items():
        for word in {key} | set(family):
            assert plan_quality._stem(word) in plan_quality._TITLE_FOOD_GROUPS, word


def test_the_repair_script_says_which_refusal_it_made():
    """CATCH against b4f0304, which printed one sentence for three different
    refusals and sent the true reason to stderr — against this function's
    own docstring."""
    tools.add_recipe(name=TICKET_HONEST, ingredients=_ing("Ground turkey", "Zucchini", "Garlic"))
    tools.add_recipe(name=TICKET_TITLE, ingredients=TICKET_INGREDIENTS, instructions=TICKET_STEPS)
    tools.add_recipe(name="Salad with Feta", ingredients=_ing("Romaine", "Cucumber", "Tomato"))

    why = {f["before"]: f["why"] for f in repair_recipe_titles() if not f["after"]}

    assert "already another of your recipes" in why[TICKET_TITLE]
    assert "doesn't say what the dish is" in why["Salad with Feta"]


# ---------------------------------------------------------------------------
# Round 4: a corrected name has to survive the rest of its own week.
# ---------------------------------------------------------------------------

_BITES = _ing("Eggs", "Cottage cheese", "Cheddar", "Salt", "Chives")
_BITES_STEPS = ["Blend the eggs and cottage cheese.", "Bake in a muffin tin 20 minutes."]


def _three_mornings(week: str, flags: list[bool]) -> list[dict]:
    """The shape the prompt asks for: one dish on three mornings, the first
    marked new and the repeats not (generate_weekly_plan_llm)."""
    days = tools._week_dates(week)
    out = []
    for i, day in enumerate(days):
        for slot in tools.WEEK_SLOTS:
            if slot == "breakfast" and i < 3:
                out.append({"date": day, "slot": slot, "meal_name": "Egg White Bites with Spinach",
                            "is_new_recipe": flags[i], "reasoning": "quick mornings",
                            "ingredients": _BITES, "instructions": _BITES_STEPS,
                            "default_servings": 4})
            else:
                out.append({"date": day, "slot": slot, "meal_name": "Chili",
                            "is_new_recipe": False, "reasoning": "it fit the week"})
    return out


def _breakfast_rows(plan_id: int) -> list[tuple]:
    from app.db import get_conn
    conn = get_conn()
    rows = conn.execute(
        "SELECT date, recipe_id, freeform_meal FROM meal_plan_entries "
        "WHERE weekly_plan_id = ? AND slot = 'breakfast' ORDER BY date LIMIT 3", (plan_id,)
    ).fetchall()
    conn.close()
    return [(r["recipe_id"], r["freeform_meal"]) for r in rows]


def test_a_repeated_dish_keeps_its_recipe_on_every_morning(stub_week):
    """CATCH against all three earlier commits, and the one strict
    REGRESSION AGAINST MAIN this card produced: a dish corrected on Monday
    was unrecognisable to Tuesday's copy of it, so `plan_meal` found nothing
    under the old name and Tuesday and Wednesday landed FREEFORM — no
    recipe on Cook, no steps, nothing bought at approval. Doing nothing at
    all was better."""
    week = _week_start()
    stub_week(_three_mornings(week, [True, False, False]))

    plan = agent.generate_weekly_plan(week)

    assert [r["name"] for r in tools.list_recipes()] == ["Egg White Bites"]
    rows = _breakfast_rows(plan["weekly_plan_id"])
    assert len({rid for rid, _ in rows}) == 1, "all three mornings are one recipe"
    assert all(rid and free is None for rid, free in rows), "none of them is freeform"


def test_a_repeated_dish_marked_new_every_time_still_makes_ONE_recipe(stub_week):
    """CATCH against b4f0304 and ba2c737 — and introduced by 005b81e's own
    successor, which is the point: round 2 added the collision guard and
    round 3 rewrote it, and neither noticed it fires on its own sibling. The
    second copy was corrected onto the first's new name, refused for
    colliding with it, and the week ended with two recipe rows for one dish
    that repair_recipe_titles can then never reconcile — which its own
    docstring calls the worse problem."""
    week = _week_start()
    stub_week(_three_mornings(week, [True, True, True]))

    plan = agent.generate_weekly_plan(week)

    assert [r["name"] for r in tools.list_recipes()] == ["Egg White Bites"]
    assert len({rid for rid, _ in _breakfast_rows(plan["weekly_plan_id"])}) == 1
    assert repair_recipe_titles() == [], "nothing left for the repair to argue with"


def test_a_repeat_carrying_no_ingredient_list_follows_the_rename_too():
    """CATCH. A reuse often comes back as a bare name, so the rename has to
    be followed BEFORE the gates that need an ingredient list."""
    items = [
        {"meal_name": "Egg White Bites with Spinach", "is_new_recipe": True,
         "ingredients": _BITES, "instructions": _BITES_STEPS},
        {"meal_name": "Egg White Bites with Spinach", "is_new_recipe": False},
    ]

    agent._honest_meal_names(items)

    assert [i["meal_name"] for i in items] == ["Egg White Bites", "Egg White Bites"]


def test_the_big_meals_guard_is_what_keeps_a_reused_main_whole():
    """CATCH against ba2c737's TEST, not its code: the guard was real and
    only the clocks half was pinned, so deleting `taken=` from _clean_main
    left the whole suite green while the blocker came straight back.

    Worth saying precisely, because "one fix" was the framing and not the
    count: this blocker needed BOTH halves. Without the guard a reused main
    is renamed and forks; without the clocks the timeline loses its times."""
    from app.tools import big_meal

    tools.add_recipe(name="Duck Traybake with Mushrooms",
                     ingredients=_ing("Duck legs", "Potatoes", "Thyme", "Stock", "Garlic"),
                     instructions=["Roast the duck.", "Add the potatoes."],
                     prep_time_minutes=15, cook_time_minutes=90)
    saved = tools.list_recipes()[0]

    main = big_meal._clean_main({
        "name": "Duck Traybake with Mushrooms",
        "ingredients": saved["ingredients"],
        "instructions": saved["instructions"],
    }, eaters=8)

    assert main["name"] == "Duck Traybake with Mushrooms"
