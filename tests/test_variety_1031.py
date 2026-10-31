"""
Three variety rules from Emily's phone test, 2026-10-31.

  1. The same dish under another name is a repeat (the bean stew):
     meal_variety.same_dish, repick_recent_repeats, repick_near_twins.
  2. A week with no cuisine asked for spreads across cuisines:
     cuisine_spread.enforce_cuisine_spread.
  3. Breakfasts and snacks: no snack that isn't one thing or a pair that
     goes together (everyday_snacks.pairing_problem), no breakfast on more
     than three mornings (breakfast_variety), library breakfasts first.

Every test stubs the model: the week at agent.generate_weekly_plan_llm,
the re-pick at swap_in_place._pick_replacement.
"""
from __future__ import annotations

import pytest

from app import agent, tools
from app.tools import allergen_gate, breakfast_variety, cuisine_spread, everyday_snacks, meal_variety
from app.tools import recipe_library
from app.tools import swap_in_place as sip
from test_no_repeat_enforced import (  # noqa: F401 — fixtures
    FRESH_DINNERS, _approve_in_place, _monday, _names, _slot, _week, stub_model,
)


@pytest.fixture
def picker(monkeypatch):
    """The re-pick, stubbed: a different dish each call, each with its own
    cuisine, so a spread can be reached."""
    calls = []
    dishes = iter([("Pad kra pao", "Thai"), ("Enchiladas verdes", "Mexican"), ("Chana masala", "Indian"),
                   ("Oyakodon", "Japanese"), ("Bibimbap", "Korean"), ("Ropa vieja", "Cuban"),
                   ("Bobotie", "South African"), ("Pierogi", "Polish")])

    def pick(context):
        calls.append(context)
        name, cuisine = next(dishes)
        return {
            "meal_name": name, "reason": "", "cuisine": cuisine,
            "ingredients": [{"item": f"{name} stuff", "qty": "1", "category": "pantry"}],
            "instructions": [f"Cook the {name.lower()} over medium heat.", "Serve."],
            "food_groups": ["protein", "vegetable", "carb"],
            "prep_time_minutes": 5, "cook_time_minutes": 10,
        }

    monkeypatch.setattr(sip, "_pick_replacement", pick)
    return calls


# ---------- 1. the same dish under another name ----------

@pytest.mark.parametrize("a,b", [
    ("Turkish White Bean and Tomato Stew (Kuru Fasulye)", "Mediterranean White Bean, Tomato and Feta Bowl"),
    ("Tuscan White Bean Stew", "Turkish White Bean and Tomato Stew (Kuru Fasulye)"),
    ("Chicken Tacos", "Chicken Taco Bowls"),
    ("Vegetable Lasagna", "Roasted Vegetable Lasagna"),
    ("Bean chili", "bean chili!"),
])
def test_these_are_the_same_dish(a, b):
    assert meal_variety.same_dish(a, b) and meal_variety.same_dish(b, a)


@pytest.mark.parametrize("a,b", [
    ("Chicken Tacos", "Chicken Curry"),
    ("Beef Chili", "Turkey Chili"),
    ("Black Bean Tacos", "White Bean Stew"),
    ("Mushroom Risotto", "Mushroom Soup"),
    ("Turkish Lamb Kofte", "Turkey Meatballs"),
    ("Chili", "Bean chili"),
    ("", "Bean chili"),
])
def test_these_are_two_dishes(a, b):
    assert not meal_variety.same_dish(a, b) and not meal_variety.same_dish(b, a)


def _last_week_with(dinner: str, stub_model) -> None:
    week = _monday(-1)
    others = ["Beef tacos", "Salmon", "Kofte", "Shrimp scampi", "Moussaka", "Gnocchi"]
    stub_model(_week(week, [dinner] + others, ["Greek salad"] * 7, snacks=["Fig bars"] * 7))
    plan = agent.generate_weekly_plan(week)
    _approve_in_place(plan["weekly_plan_id"])


def test_last_weeks_bean_stew_under_a_new_name_is_replaced(stub_model, picker):
    """The reported case: last week's white bean stew, this week's Sunday
    lunch a Turkish white bean and tomato stew. On main it stood."""
    _last_week_with("Tuscan White Bean Stew", stub_model)
    week = _monday(0)
    renamed = "Turkish White Bean and Tomato Stew (Kuru Fasulye)"
    lunches = ["Chickpea salad"] * 6 + [renamed]
    stub_model(_week(week, ["Pad thai", "Burgers", "Miso cod", "Ratatouille", "Jerk chicken", "Pierogi", "Dal"],
                     lunches))
    plan_id = agent.generate_weekly_plan(week)["weekly_plan_id"]
    assert renamed not in _names(plan_id, "lunch")
    assert any(c["slot"] == "lunch" and c["replacing"] == renamed for c in picker)


def test_two_near_twins_on_one_week_lose_the_later(stub_model, picker):
    """Bean stew Sunday dinner, bean bowl Monday lunch (no history): the
    later one is re-picked, once, and the earlier one stays."""
    week = _monday(0)
    stew = "Turkish White Bean and Tomato Stew"
    bowl = "Mediterranean White Bean, Tomato and Feta Bowl"
    dinners = [stew, "Burgers", "Miso cod", "Ratatouille", "Jerk chicken", "Pierogi", "Lamb stew"]
    lunches = ["Chickpea salad", bowl] + ["Chickpea salad"] * 5
    stub_model(_week(week, dinners, lunches))
    plan_id = agent.generate_weekly_plan(week)["weekly_plan_id"]
    assert stew in _names(plan_id, "dinner")
    assert bowl not in _names(plan_id, "lunch")
    twin_calls = [c for c in picker if c.get("replacing") == bowl]
    assert len(twin_calls) == 1


def test_the_twin_pass_never_raises_and_keeps_to_the_budget(stub_model, monkeypatch):
    """A picker that only ever answers with another twin: the dish stays
    as generated, and the pass spends no more than the budget it was given."""
    week = _monday(0)
    monkeypatch.setattr(meal_variety, "repick_near_twins", lambda *a, **k: {"twins": 0, "repicked": 0, "left": []})
    stew = "Turkish White Bean and Tomato Stew"
    bowl = "Mediterranean White Bean, Tomato and Feta Bowl"
    stub_model(_week(week, [stew] + FRESH_DINNERS[1:], ["Chickpea salad", bowl] + ["Chickpea salad"] * 5))
    plan_id = agent.generate_weekly_plan(week)["weekly_plan_id"]
    monkeypatch.undo()
    calls = []

    def twin(context):
        calls.append(context)
        return {"meal_name": "White Bean and Tomato Soup", "reason": "", "ingredients": [],
                "food_groups": ["protein", "vegetable", "carb"]}

    budget = allergen_gate.CallBudget(1)
    out = meal_variety.repick_near_twins(plan_id, week, budget, picker=twin)
    assert out["twins"] == 1 and out["repicked"] == 0
    assert len(calls) == 1 and budget.left == 0
    assert bowl in _names(plan_id, "lunch")


# ---------- 2. cuisines ----------

def _with_cuisines(days: list[dict], cuisines: dict[str, str]) -> list[dict]:
    return [dict(d, cuisine=cuisines[d["meal_name"]]) if d["meal_name"] in cuisines else d for d in days]


ITALIAN_WEEK = {"Lasagna": "Italian", "Risotto alla milanese": "Italian", "Osso buco": "Italian",
                "Chicken piccata": "Italian", "Tacos al pastor": "Mexican", "Pozole": "Mexican",
                "Chicken tikka": "Indian", "Chickpea salad": "Mediterranean"}


def test_no_cuisine_asked_for_spreads_the_week(stub_model, picker):
    """Four Italian dinners, two Mexican, one Indian, and one lunch dish:
    no cuisine on more than two dinners, and at least four cuisines."""
    week = _monday(0)
    dinners = ["Lasagna", "Risotto alla milanese", "Tacos al pastor", "Osso buco", "Pozole",
               "Chicken piccata", "Chicken tikka"]
    stub_model(_with_cuisines(_week(week, dinners), ITALIAN_WEEK))
    plan_id = agent.generate_weekly_plan(week)["weekly_plan_id"]
    from app.tools.leftovers import plan_leftover_chains
    dishes = cuisine_spread._dishes(plan_id, plan_leftover_chains(plan_id))
    crowded, why = cuisine_spread.shortfall(dishes)
    assert why is None, [(d["name"], d["cuisine"]) for d in dishes]
    dinner_italian = [d for d in dishes if d["slot"] == "dinner" and d["cuisine"] == "italian"]
    assert len(dinner_italian) <= 2
    assert len({d["cuisine"] for d in dishes if d["cuisine"]}) >= 4
    assert all("avoid_cuisines" in c for c in picker if c.get("replacing") in ITALIAN_WEEK)


def test_a_household_with_cuisine_preferences_keeps_them(stub_model, picker):
    week = _monday(0)
    tools.edit_preference("cuisine_preferences", ["Italian"])
    dinners = ["Lasagna", "Risotto alla milanese", "Tacos al pastor", "Osso buco", "Pozole",
               "Chicken piccata", "Chicken tikka"]
    stub_model(_with_cuisines(_week(week, dinners), ITALIAN_WEEK))
    plan_id = agent.generate_weekly_plan(week)["weekly_plan_id"]
    assert {"Lasagna", "Risotto alla milanese", "Osso buco", "Chicken piccata"} <= set(_names(plan_id, "dinner"))
    assert not any("avoid_cuisines" in c for c in picker)


def test_a_pick_with_no_cuisine_or_a_crowded_one_is_turned_down(stub_model, monkeypatch):
    week = _monday(0)
    dinners = ["Lasagna", "Risotto alla milanese", "Osso buco", "Chicken piccata", "Tacos al pastor",
               "Pozole", "Chicken tikka"]
    stub_model(_with_cuisines(_week(week, dinners), ITALIAN_WEEK))
    monkeypatch.setattr(cuisine_spread, "enforce_cuisine_spread", lambda *a, **k: {})
    plan_id = agent.generate_weekly_plan(week)["weekly_plan_id"]
    monkeypatch.undo()
    answers = iter([{"meal_name": "Spaghetti carbonara", "cuisine": "Italian"},
                    {"meal_name": "Plain toast"}] * 10)
    calls = []

    def pick(context):
        calls.append(context)
        return dict(next(answers), reason="", ingredients=[], food_groups=["protein", "vegetable", "carb"])

    budget = allergen_gate.CallBudget(4)
    out = cuisine_spread.enforce_cuisine_spread(plan_id, {}, {}, budget, picker=pick)
    assert out["repicked"] == [] and budget.left == 0
    assert "Spaghetti carbonara" not in _names(plan_id, "dinner")


# ---------- 3. snacks and breakfasts ----------

def test_clementines_with_edamame_is_not_a_pair():
    assert everyday_snacks.pairing_problem("Edamame with Sea Salt and Clementines")
    assert everyday_snacks.pairing_problem("Apple and Tuna Salad Cups")
    for fine in ("Apple Slices with Cheddar and Pretzels", "Hard-Boiled Eggs with Cherry Tomatoes",
                 "Black Bean and Corn Salsa with Tortilla Chips and Limes", "Edamame with Sea Salt",
                 "Greek Yogurt with Berries and Granola"):
        assert everyday_snacks.pairing_problem(fine) is None, fine


def test_every_snack_on_the_list_is_one_thing_or_a_pair():
    for snack in everyday_snacks.load_list():
        for variant in (snack, snack["swap"]):
            if variant:
                assert everyday_snacks.pairing_problem(variant["name"], variant["lines"]) is None, variant["name"]


def test_a_snack_that_is_not_a_pair_is_repicked_from_the_list(stub_model):
    tools.add_member("Emily")
    tools.set_household_meal_preferences(snacks_per_day=1, mark_complete=False)
    week = _monday(0)
    bad = "Edamame with Sea Salt and Clementines"
    days = [dict(d, meal_name=bad) if d["slot"] == "snack" else d for d in _week(week, FRESH_DINNERS)]
    stub_model(days)
    # The list pass would also swap it (it is off the list now); stand it
    # down so this measures the pairing check.
    import app.tools.everyday_snacks as es
    real = es.enforce_snacks_from_list
    es.enforce_snacks_from_list = lambda *a, **k: {"replaced": [], "left": [], "skipped": "test"}
    try:
        plan_id = agent.generate_weekly_plan(week)["weekly_plan_id"]
    finally:
        es.enforce_snacks_from_list = real
    assert bad not in _names(plan_id, "snack")


def _breakfast_week(week, breakfasts):
    return _week(week, FRESH_DINNERS, breakfasts=breakfasts)


def _mornings(plan_id) -> dict[str, int]:
    out: dict[str, int] = {}
    for name in _names(plan_id, "breakfast"):
        out[name] = out.get(name, 0) + 1
    return out


def test_no_breakfast_on_more_than_three_mornings(stub_model, picker):
    """Emily's draft: Veggie Scrambled Eggs Tuesday to Saturday. Three
    breakfasts set as the household's count: the eggs keep three mornings,
    the others take the rest — no model call."""
    tools.set_household_meal_preferences(breakfasts_per_week=3, mark_complete=False)
    week = _monday(0)
    eggs = "Veggie Scrambled Eggs"
    stub_model(_breakfast_week(week, ["Overnight oats", eggs, eggs, eggs, eggs, eggs, "Yogurt parfait"]))
    plan_id = agent.generate_weekly_plan(week)["weekly_plan_id"]
    mornings = _mornings(plan_id)
    assert max(mornings.values()) <= 3, mornings
    assert sum(mornings.values()) == 7
    assert not [c for c in picker if c["slot"] == "breakfast"]


def test_a_go_to_or_two_is_them_asking_for_the_same_breakfasts():
    memory = {"breakfasts_per_week": 2, "meal_counts_set": True}
    assert breakfast_variety.cap_for(memory, 7) == 4
    assert breakfast_variety.cap_for({"breakfasts_per_week": 7}, 7) == 3
    assert breakfast_variety.cap_for({"same_breakfasts": True}, 7) is None
    assert breakfast_variety.asks_for_same_breakfasts("same breakfast every day is fine")
    assert not breakfast_variety.asks_for_same_breakfasts("not the same breakfast every day please")


def test_a_one_breakfast_week_takes_library_breakfasts(stub_model, picker):
    """Oats on all seven mornings and nobody chose that: the library's
    breakfasts take the other mornings, with no model call."""
    for name in ("Baked Oatmeal", "Egg Muffin Cups", "Blueberry Muffins"):
        recipe_library.file_entry(name, ingredients=[{"item": "rolled oats", "qty": "2 cups"}],
                                  instructions=["Mix.", "Bake."], default_servings=4, origin="seed")
    week = _monday(0)
    stub_model(_breakfast_week(week, ["Overnight oats"] * 7))
    plan_id = agent.generate_weekly_plan(week)["weekly_plan_id"]
    mornings = _mornings(plan_id)
    assert max(mornings.values()) <= 3, mornings
    assert set(mornings) - {"Overnight oats"} <= {"Baked Oatmeal", "Egg Muffin Cups", "Blueberry Muffins"}
    assert not [c for c in picker if c["slot"] == "breakfast"]


def test_their_words_keep_the_same_breakfast(stub_model, picker):
    week = _monday(0)
    tools.set_household_meal_preferences(notes="Same breakfast every day is fine for us", mark_complete=False)
    stub_model(_breakfast_week(week, ["Overnight oats"] * 7))
    plan_id = agent.generate_weekly_plan(week)["weekly_plan_id"]
    assert _mornings(plan_id) == {"Overnight oats": 7}
