"""
Week generation in two passes (2026-09-21): the menu first, the recipes
at approval.

Production measured the single generation call at 46 seconds typical —
6,500 output tokens of full recipes for every meal in a draft that, five
times out of nine that month, was never approved. The menu pass now
chooses the week and saves each new dish as a pending recipe row; the
recipe pass writes ingredients and steps in parallel, one call per
recipe, when the household approves. Every model call is stubbed; what is
under test is the contract around them:

- a new dish the menu pass names lands as a real recipe row (Cook can find
  it, the plan references it by id), pending — never as a freeform meal;
- approval writes the pending recipes BEFORE the grocery list is built
  from them, and clears the flag;
- a week made of saved recipes costs no recipe call at all;
- a draft that is never approved never writes a recipe;
- a written recipe that carries a must-avoid is retried once, then the
  slot is re-picked or opened — the household never sees it;
- the Cook screen's "Fill in this recipe" writes a pending recipe on the
  spot, and approval then finds nothing left to write for it.
"""
import datetime
import threading

import pytest

from app import agent, tools
from app.tools import allergen_gate


def _week_start(offset_weeks: int = 1) -> str:
    today = datetime.date.today()
    monday = today - datetime.timedelta(days=today.weekday())
    return (monday + datetime.timedelta(days=7 * offset_weeks)).isoformat()


def _menu_week(week: str, new_dinner: str = "Chettinad Pepper Chicken") -> list[dict]:
    """What the menu pass sends now: every slot, and for the one new dish
    everything BUT ingredients and steps."""
    days = []
    for day in tools._week_dates(week):
        for slot in tools.WEEK_SLOTS:
            if slot == "dinner":
                days.append({
                    "date": day, "slot": slot, "meal_name": new_dinner, "is_new_recipe": True,
                    "reasoning": "you asked for something with pepper",
                    "cuisine": "Indian", "main_protein": "chicken", "tags": ["weeknight"],
                    "food_groups": ["protein", "vegetable", "carb"],
                    "prep_time_minutes": 15, "cook_time_minutes": 25,
                    "dish_note": "sear the thighs, bloom the pepper-fennel blend, braise briefly",
                })
            else:
                days.append({
                    "date": day, "slot": slot, "meal_name": "Toast", "is_new_recipe": False,
                    "reasoning": "quick",
                })
    return days


DETAILS = {
    "ingredients": [
        {"item": "Chicken thighs", "qty": "2 lb", "category": "meat/seafood"},
        {"item": "Black pepper", "qty": "1 jar", "category": "pantry"},
        {"item": "Cauliflower", "qty": "1 head", "category": "produce"},
    ],
    "instructions": [
        "Sear the thighs skin-down in a hot pan, medium-high, until deep brown, 5 minutes.",
        "Bloom the pepper and fennel in the fat for 30 seconds until fragrant.",
        "Add the cauliflower and a splash of water; cover and cook 12 minutes until tender.",
    ],
    "default_servings": 2,
    "prep_time_minutes": 15,
    "cook_time_minutes": 25,
    "advance_prep_notes": "",
    "advance_prep_step_indices": [],
}


@pytest.fixture
def household():
    tools.add_member("Emily")
    tools.add_member("Ana")
    tools.add_recipe("Toast", ingredients=[{"item": "bread", "qty": "1 loaf"}])
    # No sides model in these tests: the plate pass is not what's under test.
    tools.edit_preference("complete_plates", False)


@pytest.fixture
def menu_model(monkeypatch):
    def _stub(days):
        monkeypatch.setattr(agent, "generate_weekly_plan_llm", lambda context: days)
    return _stub


@pytest.fixture
def recipe_model(monkeypatch):
    """The recipe pass's model call, canned. Records every dish it was
    asked for, and the most threads it was ever running on at once."""
    seen = {"specs": [], "peak": 0, "live": 0}
    lock = threading.Lock()

    def _stub(answer=DETAILS, delay: float = 0.0):
        import time

        def _fake(spec):
            with lock:
                seen["specs"].append(spec)
                seen["live"] += 1
                seen["peak"] = max(seen["peak"], seen["live"])
            try:
                if delay:
                    time.sleep(delay)
                return dict(answer(spec) if callable(answer) else answer)
            finally:
                with lock:
                    seen["live"] -= 1
        monkeypatch.setattr(agent, "generate_recipe_details_llm", _fake)
        return seen

    return _stub


def _recipe(name):
    return tools.get_recipe(name)


# ---------- the menu pass ----------

def test_a_new_dish_from_the_menu_pass_is_a_pending_recipe_not_a_freeform_meal(household, menu_model):
    week = _week_start()
    menu_model(_menu_week(week))

    plan = agent.generate_weekly_plan(week)

    saved = _recipe("Chettinad Pepper Chicken")
    assert saved["details_pending"] is True
    assert saved["ingredients"] == [] and saved["instructions"] == []
    assert saved["cuisine"] == "Indian" and saved["main_protein"] == "chicken"
    assert saved["food_groups"] == ["protein", "vegetable", "carb"]
    assert saved["prep_time_minutes"] == 15 and saved["cook_time_minutes"] == 25
    assert saved["dish_note"].startswith("sear the thighs")
    dinners = [m for m in plan["meals"] if m["slot"] == "dinner"]
    assert len(dinners) == 7
    assert all(m["meal"] == "Chettinad Pepper Chicken" for m in dinners)
    # The plan points at the recipe row, not at a name nothing can look up.
    assert tools.pending_recipes_for_plan(plan["weekly_plan_id"]) == [{
        "id": saved["id"], "name": "Chettinad Pepper Chicken", "slot": "dinner",
        "cuisine": "Indian", "main_protein": "chicken", "tags": ["weeknight"],
        "food_groups": ["protein", "vegetable", "carb"],
        "prep_time_minutes": 15, "cook_time_minutes": 25,
        "dish_note": "sear the thighs, bloom the pepper-fennel blend, braise briefly",
    }]


def test_the_draft_says_how_many_recipes_approval_will_write(household, menu_model):
    week = _week_start()
    menu_model(_menu_week(week))
    plan = agent.generate_weekly_plan(week)

    menu = tools.get_week_menu(plan["weekly_plan_id"])

    assert menu["recipes_pending"] == 1


def test_a_full_recipe_handed_to_the_saver_is_still_saved_whole(household, menu_model):
    """The component planner and older callers still send ingredients; a
    dish that arrives written is saved written, not marked pending."""
    week = _week_start()
    days = _menu_week(week)
    for d in days:
        if d["slot"] == "dinner":
            d["ingredients"] = DETAILS["ingredients"]
            d["instructions"] = DETAILS["instructions"]
    menu_model(days)

    agent.generate_weekly_plan(week)

    saved = _recipe("Chettinad Pepper Chicken")
    assert saved["details_pending"] is False
    assert [i["item"] for i in saved["ingredients"]] == ["Chicken thighs", "Black pepper", "Cauliflower"]


# ---------- the recipe pass, at approval ----------

def test_approval_writes_the_pending_recipes_and_then_builds_the_list(household, menu_model, recipe_model):
    week = _week_start()
    menu_model(_menu_week(week))
    plan = agent.generate_weekly_plan(week)
    seen = recipe_model()

    result = tools.approve_weekly_plan(plan["weekly_plan_id"])

    assert result["status"] == "approved"
    saved = _recipe("Chettinad Pepper Chicken")
    assert saved["details_pending"] is False
    assert [i["item"] for i in saved["ingredients"]] == ["Chicken thighs", "Black pepper", "Cauliflower"]
    assert len(saved["instructions"]) == 3
    assert saved["default_servings"] == 2
    # Written once, for the one dish — seven dinners of it are one recipe.
    assert [s["name"] for s in seen["specs"]] == ["Chettinad Pepper Chicken"]
    # And the list was built from what was written.
    items = {g["item"].lower() for g in tools.list_grocery_list("needed")}
    assert "chicken thighs" in items and "cauliflower" in items
    assert tools.pending_recipes_for_plan(plan["weekly_plan_id"]) == []


def test_the_recipe_pass_is_told_the_dish_the_table_and_the_must_avoids(household, menu_model, recipe_model):
    tools.set_member_dietary_restrictions("Ana", ["peanut allergy"])
    week = _week_start()
    menu_model(_menu_week(week))
    plan = agent.generate_weekly_plan(week)
    seen = recipe_model()

    tools.approve_weekly_plan(plan["weekly_plan_id"])

    spec = seen["specs"][0]
    assert spec["name"] == "Chettinad Pepper Chicken" and spec["slot"] == "dinner"
    assert spec["dish_note"].startswith("sear the thighs")
    assert spec["serves"] == 2
    assert any("peanut" in line.lower() for line in spec["must_not_contain"])


def test_a_week_of_saved_recipes_makes_no_recipe_call(household, menu_model, recipe_model):
    week = _week_start()
    days = [{**d, "meal_name": "Toast", "is_new_recipe": False} for d in _menu_week(week)]
    for d in days:
        d.pop("dish_note", None)
    menu_model(days)
    plan = agent.generate_weekly_plan(week)
    seen = recipe_model()

    tools.approve_weekly_plan(plan["weekly_plan_id"])

    assert seen["specs"] == []


def test_a_draft_that_is_never_approved_never_writes_a_recipe(household, menu_model, recipe_model):
    """The whole point: five of nine drafts in production were thrown
    away. A re-roll must not pay for recipes on the draft it replaces."""
    week = _week_start()
    menu_model(_menu_week(week, new_dinner="First Draft Dinner"))
    agent.generate_weekly_plan(week)
    seen = recipe_model()
    menu_model(_menu_week(week, new_dinner="Second Draft Dinner"))
    plan = agent.generate_weekly_plan(week)

    tools.approve_weekly_plan(plan["weekly_plan_id"])

    assert [s["name"] for s in seen["specs"]] == ["Second Draft Dinner"]
    assert _recipe("First Draft Dinner")["details_pending"] is True


def test_several_new_recipes_are_written_at_once_not_one_after_another(household, menu_model, recipe_model):
    week = _week_start()
    days = []
    for i, d in enumerate(_menu_week(week)):
        if d["slot"] == "dinner":
            d = {**d, "meal_name": f"New Dinner {i}"}
        days.append(d)
    menu_model(days)
    plan = agent.generate_weekly_plan(week)
    seen = recipe_model(delay=0.15)

    tools.approve_weekly_plan(plan["weekly_plan_id"])

    assert len(seen["specs"]) == 7
    assert seen["peak"] >= 2, "the recipe pass ran one recipe at a time"
    for i, d in enumerate(_menu_week(week)):
        if d["slot"] == "dinner":
            assert _recipe(f"New Dinner {i}")["details_pending"] is False


def test_a_recipe_that_fails_to_write_stays_pending_and_the_approval_still_lands(household, menu_model, recipe_model):
    week = _week_start()
    menu_model(_menu_week(week))
    plan = agent.generate_weekly_plan(week)
    recipe_model(answer=lambda spec: {})

    result = tools.approve_weekly_plan(plan["weekly_plan_id"])

    assert result["status"] == "approved"
    assert _recipe("Chettinad Pepper Chicken")["details_pending"] is True


def test_a_written_recipe_that_carries_a_must_avoid_is_never_saved(household, menu_model, recipe_model, monkeypatch):
    """The writer is told the must-avoids; being told is not being
    prevented. A list that comes back with the allergen in it is retried
    once with the clash named, and if it comes back again the dish is
    handed to the sweep — re-picked or opened — rather than saved."""
    tools.set_member_dietary_restrictions("Ana", ["pineapple allergy"])
    week = _week_start()
    menu_model(_menu_week(week))
    plan = agent.generate_weekly_plan(week)
    bad = {**DETAILS, "ingredients": DETAILS["ingredients"] + [{"item": "Pineapple", "qty": "1", "category": "produce"}]}
    seen = recipe_model(answer=bad)
    swept = {}

    def _fake_sweep(plan_id, budget=None, picker=None, known_clashes=None):
        swept["known"] = known_clashes
        return {"sides_removed": 0, "dishes_repicked": 0, "slots_opened": 0}
    monkeypatch.setattr(allergen_gate, "sweep_plan", _fake_sweep)

    tools.approve_weekly_plan(plan["weekly_plan_id"])

    assert len(seen["specs"]) == 2, "one retry, naming the clash"
    assert "pineapple" in seen["specs"][1]["previous_attempt_included"].lower()
    saved = _recipe("Chettinad Pepper Chicken")
    assert saved["details_pending"] is True and saved["ingredients"] == []
    assert list(swept["known"]) == ["chettinad pepper chicken"]
    assert swept["known"]["chettinad pepper chicken"][0]["matched"] == "pineapple"


def test_the_retry_that_comes_back_clean_is_saved(household, menu_model, recipe_model):
    tools.set_member_dietary_restrictions("Ana", ["pineapple allergy"])
    week = _week_start()
    menu_model(_menu_week(week))
    plan = agent.generate_weekly_plan(week)
    calls = {"n": 0}

    def _answer(spec):
        calls["n"] += 1
        if calls["n"] == 1:
            return {**DETAILS, "ingredients": DETAILS["ingredients"] + [{"item": "Pineapple", "qty": "1", "category": "produce"}]}
        return DETAILS
    recipe_model(answer=_answer)

    tools.approve_weekly_plan(plan["weekly_plan_id"])

    saved = _recipe("Chettinad Pepper Chicken")
    assert saved["details_pending"] is False
    assert "Pineapple" not in [i["item"] for i in saved["ingredients"]]


def test_a_second_approval_does_not_write_again(household, menu_model, recipe_model):
    week = _week_start()
    menu_model(_menu_week(week))
    plan = agent.generate_weekly_plan(week)
    seen = recipe_model()
    tools.approve_weekly_plan(plan["weekly_plan_id"])

    tools.approve_weekly_plan(plan["weekly_plan_id"])

    assert len(seen["specs"]) == 1


# ---------- the Cook screen reaches a pending recipe first ----------

def test_fill_in_writes_a_pending_recipe_on_the_spot(household, menu_model, recipe_model):
    week = _week_start()
    menu_model(_menu_week(week))
    plan = agent.generate_weekly_plan(week)
    seen = recipe_model()

    filled = agent.fill_in_recipe("Chettinad Pepper Chicken")

    assert filled["details_pending"] is False
    assert len(filled["ingredients"]) == 3 and len(filled["instructions"]) == 3
    assert seen["specs"][0]["slot"] == "dinner"
    # Approval then has nothing left to write for it.
    tools.approve_weekly_plan(plan["weekly_plan_id"])
    assert len(seen["specs"]) == 1


def test_the_cook_card_says_a_pending_recipe_is_pending(household, menu_model):
    week = _week_start()
    menu_model(_menu_week(week))
    agent.generate_weekly_plan(week)
    cook = tools.get_cooker_view()
    pending = [m for m in cook["meals"] if m.get("meal") == "Chettinad Pepper Chicken"]
    assert pending and all(m["details_pending"] is True for m in pending)
    assert all(m["has_full_recipe"] is True for m in pending), "a pending recipe is a recipe row, not a missing one"


# ---------- fill_recipe_details on its own ----------

def test_fill_recipe_details_only_writes_a_pending_row(household):
    tools.add_recipe("Chili", ingredients=[{"item": "beans", "qty": "1 tin"}], instructions=["Warm it."])

    back = tools.fill_recipe_details("Chili", ingredients=[{"item": "Cheese", "qty": "1 bag"}],
                                     instructions=["Grate."], default_servings=6)

    assert [i["item"] for i in back["ingredients"]] == ["beans"]
    assert back["instructions"] == ["Warm it."]
    assert back["default_servings"] == 4


def test_fill_recipe_details_keeps_the_planners_minutes_when_the_writer_sends_none(household):
    tools.add_recipe("Stub", ingredients=[], prep_time_minutes=10, cook_time_minutes=30,
                     details_pending=True, dish_note="roast it")

    back = tools.fill_recipe_details("Stub", ingredients=DETAILS["ingredients"],
                                     instructions=DETAILS["instructions"], default_servings=2)

    assert back["prep_time_minutes"] == 10 and back["cook_time_minutes"] == 30
    assert back["details_pending"] is False


# ---------- quality rules and the two passes ----------

def _violations_for(plan_id):
    from app.db import get_conn
    conn = get_conn()
    rows = conn.execute(
        "SELECT rule FROM plan_quality_events WHERE weekly_plan_id = ? ORDER BY id", (plan_id,)
    ).fetchall()
    conn.close()
    return [r["rule"] for r in rows]


def test_a_pending_recipe_trips_no_recipe_rule_at_draft_time(household, menu_model):
    """An empty ingredient list is not a recipe with no salt in it. The
    recipe-level rules have nothing to read until the recipe pass, and
    must not fill the morning report with what they can't see."""
    week = _week_start()
    menu_model(_menu_week(week))
    plan = agent.generate_weekly_plan(week)

    rules = set(_violations_for(plan["weekly_plan_id"]))

    assert not rules & {
        "steps_match_ingredients", "quantities_plausible", "seasoning_never_mentioned",
        "method_is_assembly", "steps_have_no_cue", "no_heat_named", "minutes_vs_steps",
        "title_promises_an_ingredient", "longest_thing_not_first",
    }, rules


def test_the_recipe_rules_run_once_the_recipe_is_written(household, menu_model, recipe_model):
    """The same rules, read against what the recipe pass wrote: a step
    that reaches for salt nobody bought is caught at approval, not never."""
    week = _week_start()
    menu_model(_menu_week(week))
    plan = agent.generate_weekly_plan(week)
    recipe_model(answer={**DETAILS, "instructions": [
        "Season the thighs with salt and sear skin-down, medium-high, until deep brown, 5 minutes.",
        "Add the cauliflower and cook 12 minutes until tender.",
    ]})

    tools.approve_weekly_plan(plan["weekly_plan_id"])

    assert "steps_match_ingredients" in _violations_for(plan["weekly_plan_id"])


# ---------- two households ----------

def test_the_recipe_pass_writes_into_the_household_that_approved(monkeypatch, recipe_model):
    """The workers run on threads, and the household is a contextvar. A
    bare thread sees the default household — 1 — so without a copied
    context, a second household's approval would write its recipes into
    the first household's kitchen (or find no recipe to write at all).
    Caught in review before it shipped; this is the pin."""
    from app import households

    beta = households.create_household("The Beta Testers", "beta-passphrase-for-the-test")
    week = _week_start()
    with tools.use_household(beta):
        tools.add_member("Julia")
        tools.add_recipe("Toast", ingredients=[{"item": "bread", "qty": "1 loaf"}])
        tools.edit_preference("complete_plates", False)
        # Two new dishes, so the second is written on a worker thread — the
        # first is written on the calling thread to warm the cache.
        days = [
            {**d, "meal_name": "Paneer Tikka Wrap", "is_new_recipe": True, "dish_note": "sear the paneer"}
            if d["slot"] == "lunch" else d
            for d in _menu_week(week)
        ]
        monkeypatch.setattr(agent, "generate_weekly_plan_llm", lambda context: days)
        plan = agent.generate_weekly_plan(week)
        seen = recipe_model()

        tools.approve_weekly_plan(plan["weekly_plan_id"])

        assert len(seen["specs"]) == 2
        assert all(s["serves"] == 1 for s in seen["specs"]), "written for Julia's table, not household 1's"
        for name in ("Chettinad Pepper Chicken", "Paneer Tikka Wrap"):
            saved = _recipe(name)
            assert saved["details_pending"] is False, name
            assert [i["item"] for i in saved["ingredients"]] == ["Chicken thighs", "Black pepper", "Cauliflower"]
    # And nothing landed in household 1.
    with tools.use_household(1):
        names = {r["name"] for r in tools.list_recipes()}
        assert not names & {"Chettinad Pepper Chicken", "Paneer Tikka Wrap"}


# ---------- the plate pass runs its side calls abreast ----------

def test_the_plate_pass_asks_for_its_sides_at_once_not_in_turn(household, menu_model, monkeypatch):
    """Up to six side calls used to run one after another AFTER the menu
    came back — 12-18s of the draft's wall-clock on the live run of
    2026-09-21. They are independent; they run together now."""
    import time
    tools.edit_preference("complete_plates", True)
    week = _week_start()
    # Dinners short of a vegetable, so the plate pass has six calls to make.
    days = [
        {**d, "food_groups": ["protein", "carb"]} if d["slot"] == "dinner" else d
        for d in _menu_week(week)
    ]
    menu_model(days)
    seen = {"peak": 0, "live": 0, "calls": 0}
    lock = threading.Lock()

    def sides_llm(context):
        with lock:
            seen["calls"] += 1
            seen["live"] += 1
            seen["peak"] = max(seen["peak"], seen["live"])
        try:
            time.sleep(0.15)
            return [{"name": "Green salad", "food_groups": ["vegetable"],
                     "ingredients": [{"item": "Romaine", "qty": "1 head", "category": "produce"}],
                     "minutes": 5}]
        finally:
            with lock:
                seen["live"] -= 1
    monkeypatch.setattr(agent, "generate_sides_llm", sides_llm)

    plan = agent.generate_weekly_plan(week)

    assert seen["calls"] == 6, "six short dinners, the six-side cap"
    assert seen["peak"] >= 2, "the side calls ran one at a time"
    with_sides = [m for m in tools.get_weekly_plan(plan["weekly_plan_id"])["meals"] if m.get("sides")]
    assert len(with_sides) == 6


# ---------- review findings, pinned ----------

def test_two_approvals_at_once_run_the_recipe_pass_once(household, menu_model, recipe_model):
    """Review, 2026-09-21: two adults tapping Approve together each ran
    the recipe pass. The data stayed right (fill_recipe_details guards
    the row) but the model was called twice for one recipe. The loser
    now waits for the winner and finds nothing pending."""
    import time
    week = _week_start()
    menu_model(_menu_week(week))
    plan = agent.generate_weekly_plan(week)
    seen = recipe_model(delay=0.3)
    errors = []

    def approve():
        try:
            tools.approve_weekly_plan(plan["weekly_plan_id"])
        except Exception as e:  # pragma: no cover - reported below
            errors.append(e)

    a, b = threading.Thread(target=approve), threading.Thread(target=approve)
    a.start(); time.sleep(0.02); b.start()
    a.join(); b.join()

    assert not errors
    assert len(seen["specs"]) == 1


def test_a_new_dish_whose_note_names_the_allergen_is_held_back_at_the_draft(household, menu_model, monkeypatch):
    """The menu pass sends no ingredients, so the draft's check was the
    name alone (review, 2026-09-21). The planner's dish_note names the
    dish's defining ingredients; it is matched too, so "Pad Thai" over a
    note that says peanuts never reaches the draft."""
    tools.set_member_dietary_restrictions("Ana", ["peanut allergy"])
    week = _week_start()
    days = []
    for d in _menu_week(week):
        if d["slot"] == "dinner":
            d = {**d, "meal_name": "Pad Thai", "dish_note": "rice noodles in tamarind sauce, finish with crushed peanuts and lime"}
        days.append(d)
    menu_model(days)
    # The re-pick's own model call, canned: a safe dish, with ingredients.
    from app.tools import swap_in_place
    monkeypatch.setattr(swap_in_place, "_pick_replacement", lambda context: {
        "meal_name": "Chicken Fried Rice", "reason": "no peanuts",
        "ingredients": [{"item": "Rice", "qty": "2 cups", "category": "pantry"}],
        "food_groups": ["protein", "carb"],
    })

    plan = agent.generate_weekly_plan(week)

    names = {m.get("meal") for m in plan["meals"] if m["slot"] == "dinner"}
    assert "Pad Thai" not in names
    assert not any(r["name"] == "Pad Thai" for r in tools.list_recipes())
