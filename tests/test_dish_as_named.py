"""
"A dish is what its name says — 'Korean chicken pancake' came as a
cucumber salad" (Loop Board bug, 2026-09-21). Emily typed "I have some
corn so incorporate that into a meal" and wanted a Korean chicken
pancake; the draft's dish was "Korean Chicken Pancake with Cucumber
Salad" — no corn anywhere, and the side folded into the name. Two rules:

  * a typed INGREDIENT is in at least one dish, or one slot is re-picked
    with it on must_contain (the swap picker, the shared budget), or the
    draft says "I couldn't fit the corn in this week" — never silent;
  * a dish asked for BY NAME keeps that name; a side the model folded into
    it is stored as a side (a plate part on the card), and a dish they
    did NOT ask for keeps whatever name the model gave it.

See app/tools/typed_requests.py and week_intake.freeform_ingredient_requests.
"""
from __future__ import annotations

import datetime
import json

import pytest

from app import agent, tools
from app.db import get_conn
from app.tools import swap_in_place as sip
from app.tools import typed_requests
from conftest import prompt_literals


# ---------------------------------------------------------------------------
# the parser
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("text, expected", [
    ("I have some corn so incorporate that into a meal, and I do want to have chicken breast this week.", ["corn"]),
    ("Friday is pizza night. I want to use the lamb in the freezer.", ["lamb"]),
    ("There's salmon in the freezer", ["salmon"]),
    ("I have a bag of spinach that needs using", ["spinach"]),
    ("We've got lots of zucchini from the garden", ["zucchini"]),
    ("use up the rest of the tortillas", ["tortillas"]),
    ("I have chicken breast in the fridge", ["chicken breast"]),
])
def test_the_unmistakable_shapes_of_an_ingredient_request_are_read(text, expected):
    assert [r["ingredient"] for r in tools.freeform_ingredient_requests(text)] == expected


@pytest.mark.parametrize("text", [
    "I don't have any corn",              # a negation: left to the model
    "use the lamb, not the chicken",
    "I have some time on Sunday",         # not a food
    "I have guests Friday",
    "no fish this week",
    "Mexican for lunch",                  # a meal request, not an ingredient
    "Something with mushrooms please",    # a shape the parser can't be sure of
    "",
    None,
])
def test_anything_the_parser_cannot_be_sure_of_yields_nothing(text):
    assert tools.freeform_ingredient_requests(text) == []


def test_the_prompt_is_told_an_ingredient_is_a_request_and_a_named_dish_keeps_its_name():
    text = prompt_literals(agent.generate_weekly_plan_llm)
    assert "An INGREDIENT is a request too" in text
    assert "`intake.must_use` lists the ones that are beyond doubt" in text
    assert "a dish the household asked for BY NAME" in text
    assert 'never "Korean Chicken Pancake with Cucumber Salad"' in text
    ctx = agent._intake_generation_context(
        {"freeform": "I have some corn so incorporate that into a meal", "night_tags": {}, "guest_counts": {},
         "household_snapshot": {}},
        tools.period_dates("2026-09-21", 7),
    )
    assert ctx["must_use"] == ["corn"]
    # The swap picker is told what must_contain means, and the recipe
    # writer what must_use means.
    assert "`must_contain`, when present" in sip.INSTRUCTIONS
    assert "`must_use`, when present" in agent.RECIPE_DETAILS_INSTRUCTIONS


# ---------------------------------------------------------------------------
# a week through generation, model stubbed
# ---------------------------------------------------------------------------

def _monday(offset_weeks: int = 1) -> str:
    today = datetime.date.today()
    monday = today - datetime.timedelta(days=today.weekday())
    return (monday + datetime.timedelta(days=7 * offset_weeks)).isoformat()


def _slot(date, slot, name, **extra):
    """A menu-pass item: a name and no ingredients (the recipe is written
    at approval), food groups whole so the plates pass stays out of it."""
    d = {"date": date, "slot": slot, "meal_name": name, "is_new_recipe": True,
         "reasoning": f"{name} because", "food_groups": ["protein", "vegetable", "carb"]}
    d.update(extra)
    return d


DINNERS = ["Lemon chicken", "Beef tacos", "Salmon traybake", "Mushroom risotto", "Pork chops", "Lentil dal", "Shrimp pad thai"]


def _week(week: str, dinners=None) -> list[dict]:
    out = []
    for date, dinner in zip(tools._week_dates(week), dinners or DINNERS):
        out.append(_slot(date, "breakfast", "Overnight oats"))
        out.append(_slot(date, "snack", "Apple"))
        out.append(_slot(date, "lunch", "Chickpea salad"))
        out.append(dinner(date) if callable(dinner) else _slot(date, "dinner", dinner))
    return out


@pytest.fixture
def stub_model(monkeypatch):
    def _stub(days, report=None):
        def fake(ctx):
            if report is None:
                return days
            out = agent.GeneratedDays(days)
            out.report = report
            return out
        monkeypatch.setattr(agent, "generate_weekly_plan_llm", fake)
    return _stub


def _pick(name: str, ingredients: list[str]) -> dict:
    return {
        "meal_name": name, "reason": "uses what you have",
        "ingredients": [{"item": i, "qty": "1", "category": "produce"} for i in ingredients],
        "instructions": ["Cook.", "Serve."], "food_groups": ["protein", "vegetable", "carb"],
        "prep_time_minutes": 10, "cook_time_minutes": 20,
    }


def _plan_id(week: str) -> int:
    conn = get_conn()
    row = conn.execute("SELECT id FROM weekly_plans WHERE week_start_date = ? ORDER BY id DESC", (week,)).fetchone()
    conn.close()
    return row["id"]


def _entries(plan_id: int, slot: str) -> list[dict]:
    return [m for m in tools.get_weekly_plan(plan_id)["meals"] if m["slot"] == slot]


def _derived(entry_id: int) -> dict:
    conn = get_conn()
    raw = conn.execute("SELECT derived_from_json FROM meal_plan_entries WHERE id = ?", (entry_id,)).fetchone()[0]
    conn.close()
    return json.loads(raw or "{}")


CORN = "I have some corn so incorporate that into a meal, and I do want to have chicken breast this week."


def test_a_typed_ingredient_no_dish_used_gets_one_slot_repicked_with_it_on_must_contain(stub_model, monkeypatch):
    """(a) the model sent a week with no corn; one dinner is re-picked
    through the swap picker with corn on must_contain, and the draft says
    where it went."""
    week = _monday()
    tools.save_week_intake(week, freeform=CORN)
    stub_model(_week(week), report={"honoured_requests": [], "unmet_requests": []})
    picks = []
    monkeypatch.setattr(sip, "_pick_replacement", lambda ctx: (picks.append(ctx), _pick("Corn and chicken chowder", ["Corn", "Chicken breast"]))[1])

    agent.generate_weekly_plan(week)
    plan_id = _plan_id(week)

    assert len(picks) == 1
    assert picks[0]["must_contain"] == ["corn"]
    assert "corn" in picks[0]["replacing_because"]
    dinners = _entries(plan_id, "dinner")
    monday = dinners[0]
    assert monday["meal"] == "Corn and chicken chowder", "the earliest dinner nobody asked for by name"
    assert [d["meal"] for d in dinners[1:]] == DINNERS[1:], "nothing else touched"
    derived = _derived(monday["entry_id"])
    assert derived["must_use_repick"]["dropped"] == "Lemon chicken"
    assert derived["must_use"] == ["corn"] and derived["freeform"].startswith("I have some corn")
    # The report and the opener: honoured, with the day it reached.
    report = tools.plan_requests(plan_id)
    assert report["honoured"] == [{"words": derived["freeform"], "label": "the corn", "ingredient": "corn"}]
    assert report["unmet"] == []
    menu = tools.get_week_menu(plan_id)
    assert menu["draft_opener"][0] == "The corn Monday, as you asked."
    assert not any("couldn’t fit" in line for line in menu["draft_opener"])
    # The recipe the picker wrote carries the corn, so approval has it.
    recipe = tools.get_recipe("Corn and chicken chowder")
    assert any("corn" in (i.get("item") or "").lower() for i in recipe["ingredients"])
    assert tools.audit_plan_slots(plan_id)["complete"] is True


def test_a_pick_without_the_ingredient_is_thrown_away_and_the_next_one_taken(stub_model, monkeypatch):
    week = _monday()
    tools.save_week_intake(week, freeform=CORN)
    stub_model(_week(week))
    answers = iter([_pick("Beef stew", ["Beef", "Carrots"]), _pick("Corn fritters", ["Corn", "Eggs"])])
    monkeypatch.setattr(sip, "_pick_replacement", lambda ctx: next(answers))

    agent.generate_weekly_plan(week)
    plan_id = _plan_id(week)
    assert _entries(plan_id, "dinner")[0]["meal"] == "Corn fritters"
    assert tools.plan_requests(plan_id)["honoured"][0]["label"] == "the corn"


def test_when_no_repick_lands_the_ingredient_the_draft_says_it_could_not_fit_it(stub_model, monkeypatch):
    """(b) the picker has nothing with corn in it: the week stands, the
    report names the corn as unmet, and the opener says so."""
    week = _monday()
    tools.save_week_intake(week, freeform=CORN)
    # The model even claimed to have honoured it — a claim nothing backs.
    stub_model(_week(week), report={
        "honoured_requests": [{"words": "I have some corn so incorporate that into a meal", "label": "the corn"}],
        "unmet_requests": [],
    })
    monkeypatch.setattr(sip, "_pick_replacement", lambda ctx: {})

    agent.generate_weekly_plan(week)
    plan_id = _plan_id(week)
    assert [d["meal"] for d in _entries(plan_id, "dinner")] == DINNERS
    report = tools.plan_requests(plan_id)
    assert report["honoured"] == [], "the model's unbacked claim is dropped"
    assert report["unmet"][0]["ingredient"] == "corn"
    assert report["unmet"][0]["words"].startswith("I have some corn")
    menu = tools.get_week_menu(plan_id)
    assert "I couldn’t fit the corn in this week." in menu["draft_opener"]


def test_a_dish_the_model_chose_for_the_ingredient_counts_and_no_repick_is_spent(stub_model, monkeypatch):
    week = _monday()
    tools.save_week_intake(week, freeform=CORN)
    dinners = list(DINNERS)
    dinners[2] = lambda date: _slot(date, "dinner", "Salmon traybake", dish_note="roast the salmon over charred corn and peppers")
    stub_model(_week(week, dinners))
    monkeypatch.setattr(sip, "_pick_replacement", lambda ctx: pytest.fail("no re-pick needed"))

    agent.generate_weekly_plan(week)
    plan_id = _plan_id(week)
    wednesday = _entries(plan_id, "dinner")[2]
    assert wednesday["meal"] == "Salmon traybake"
    derived = _derived(wednesday["entry_id"])
    assert derived["must_use"] == ["corn"]
    report = tools.plan_requests(plan_id)
    assert report["honoured"][0]["label"] == "the corn" and report["unmet"] == []
    assert tools.get_week_menu(plan_id)["draft_opener"][0] == "The corn Wednesday, as you asked."
    # The recipe pass is told to write the corn in.
    pending = tools.pending_recipes_for_plan(plan_id)
    salmon = next(r for r in pending if r["name"] == "Salmon traybake")
    assert typed_requests.plan_must_use(plan_id, salmon["id"]) == ["corn"]
    spec = agent._recipe_details_spec({**salmon, "must_use": ["corn"]}, "dinner", {"serves": 4})
    assert spec["must_use"] == ["corn"]


# ---------------------------------------------------------------------------
# a dish asked for by name keeps its name
# ---------------------------------------------------------------------------

PANCAKE = "I want a Korean chicken pancake this week. " + CORN


def test_a_requested_dish_keeps_its_name_and_the_folded_side_becomes_a_plate_part(stub_model, monkeypatch):
    """(c) the model named the pancake with the salad it planned into it:
    the dish is the pancake, and the cucumber salad is a veg side on the
    card — written by the sides call, the way "Add something" writes one."""
    week = _monday()
    tools.save_week_intake(week, freeform=PANCAKE)
    dinners = list(DINNERS)
    dinners[1] = lambda date: _slot(
        date, "dinner", "Korean Chicken Pancake with Cucumber Salad",
        derived_from={"freeform": "I want a Korean chicken pancake this week"},
        main_protein="chicken", dish_note="a crisp scallion pancake with shredded chicken, corn folded through the batter",
    )
    stub_model(_week(week, dinners))
    monkeypatch.setattr(sip, "_pick_replacement", lambda ctx: pytest.fail("no re-pick needed"))
    side_calls = []

    def sides(context):
        side_calls.append(context)
        return [{"name": "Cucumber salad", "covers": ["vegetable"], "minutes": 5,
                 "ingredients": [{"item": "Persian cucumbers", "qty": "4", "category": "produce"}],
                 "instructions": ["Slice and dress."]}]
    monkeypatch.setattr(agent, "generate_sides_llm", sides)

    agent.generate_weekly_plan(week)
    plan_id = _plan_id(week)
    tuesday = _entries(plan_id, "dinner")[1]
    assert tuesday["meal"] == "Korean Chicken Pancake"
    assert tools.get_recipe("Korean Chicken Pancake") is not None
    assert not tools.existing_recipe_named("Korean Chicken Pancake with Cucumber Salad")
    assert len(side_calls) == 1 and side_calls[0]["requested"] == "Cucumber Salad"
    # On the card: the dish's name, and the salad as the Veg part, a side.
    menu = tools.get_week_menu(plan_id)
    card = menu["days"][1]["dinner"]
    assert card["title"] == "Korean Chicken Pancake"
    assert [s["name"] for s in card["sides"]] == ["Cucumber salad"]
    assert card["plate_note"] == "with Cucumber salad"
    veg = next(p for p in card["plate_parts"] if p["role"] == "vegetable")
    assert veg == {"role": "vegetable", "word": "Veg", "name": "Cucumber salad", "source": "side", "missing": False}
    protein = next(p for p in card["plate_parts"] if p["role"] == "protein")
    assert protein["name"] == "Chicken" and protein["source"] == "dish"
    # The corn is in the pancake (its dish_note), so nothing was re-picked
    # and the opener credits both requests.
    assert tools.plan_requests(plan_id)["unmet"] == []
    assert menu["draft_opener"][0] == "The corn Tuesday, as you asked."


def test_when_the_sides_call_fails_the_side_still_goes_on_as_named(stub_model, monkeypatch):
    week = _monday()
    tools.save_week_intake(week, freeform=PANCAKE)
    dinners = list(DINNERS)
    dinners[1] = lambda date: _slot(date, "dinner", "Korean Chicken Pancake with Cucumber Salad",
                                    derived_from={"freeform": "Korean chicken pancake"}, dish_note="corn in the batter")
    stub_model(_week(week, dinners))
    monkeypatch.setattr(sip, "_pick_replacement", lambda ctx: pytest.fail("no re-pick needed"))
    monkeypatch.setattr(agent, "generate_sides_llm", lambda ctx: (_ for _ in ()).throw(RuntimeError("down")))

    agent.generate_weekly_plan(week)
    plan_id = _plan_id(week)
    card = tools.get_week_menu(plan_id)["days"][1]["dinner"]
    assert card["title"] == "Korean Chicken Pancake"
    assert [s["name"] for s in card["sides"]] == ["Cucumber Salad"]
    assert any(p["role"] == "side" and p["name"] == "Cucumber Salad" for p in card["plate_parts"])


def test_a_dish_the_household_did_not_ask_for_keeps_whatever_name_the_model_gave(stub_model, monkeypatch):
    """(d) no over-trimming: the same "with Cucumber Salad" on a dish they
    never named, and a requested name whose clause is part of the dish,
    both stand."""
    week = _monday()
    tools.save_week_intake(week, freeform=PANCAKE)
    dinners = list(DINNERS)
    dinners[0] = "Lemon Chicken with Cucumber Salad"
    dinners[1] = lambda date: _slot(date, "dinner", "Korean Chicken Pancake with Gochujang Mayo",
                                    derived_from={"freeform": "Korean chicken pancake"}, dish_note="corn in the batter")
    dinners[3] = "Chicken Breast with Lemon and Herbs"
    stub_model(_week(week, dinners))
    monkeypatch.setattr(sip, "_pick_replacement", lambda ctx: pytest.fail("no re-pick needed"))
    monkeypatch.setattr(agent, "generate_sides_llm", lambda ctx: pytest.fail("no side to write"))

    agent.generate_weekly_plan(week)
    plan_id = _plan_id(week)
    names = [d["meal"] for d in _entries(plan_id, "dinner")]
    assert names[0] == "Lemon Chicken with Cucumber Salad"
    assert names[1] == "Korean Chicken Pancake with Gochujang Mayo"
    assert names[3] == "Chicken Breast with Lemon and Herbs"
    assert all(not d["sides"] for d in tools.get_weekly_plan(plan_id)["meals"] if d["slot"] == "dinner")


def test_the_pure_rule_on_its_own():
    f = typed_requests.requested_dish_name
    asks = "I want a Korean chicken pancake this week"
    assert f("Korean Chicken Pancake with Cucumber Salad", asks) == ("Korean Chicken Pancake", ["Cucumber Salad"])
    assert f("Korean Chicken Pancakes with Rice and Cucumber Salad", asks) == ("Korean Chicken Pancakes", ["Rice", "Cucumber Salad"])
    assert f("Korean Chicken Pancake with Gochujang Mayo", asks) == ("Korean Chicken Pancake with Gochujang Mayo", [])
    assert f("Lemon Chicken with Cucumber Salad", asks) == ("Lemon Chicken with Cucumber Salad", [])
    # They asked for the whole thing: nothing comes off.
    assert f("Fish Tacos with Slaw", "fish tacos with slaw please") == ("Fish Tacos with Slaw", [])
    assert f("Fish Tacos with Slaw", "fish tacos please") == ("Fish Tacos", ["Slaw"])
    # One word is a category, not a dish name — "pasta" doesn't claim "Pasta with Sausage".
    assert f("Pasta with Sausage and Broccoli", "pasta please") == ("Pasta with Sausage and Broccoli", [])
    # "chicken" inside "chicken breast" is an ingredient, not this rule's business.
    assert f("Chicken with Roasted Vegetables", "I want chicken breast this week") == ("Chicken with Roasted Vegetables", [])
    # A saved recipe's name identifies a row and is never rewritten.
    assert f("Korean Chicken Pancake with Cucumber Salad", asks, taken={"korean chicken pancake with cucumber salad"}) == \
        ("Korean Chicken Pancake with Cucumber Salad", [])
    assert f("", asks) == ("", []) and f("Korean Chicken Pancake", "") == ("Korean Chicken Pancake", [])


def test_the_record_keeps_the_ingredient_marker_and_the_opener_reads_it():
    from app.tools import draft_opener
    assert draft_opener._line_two([], {"unmet": [{"words": "I have some corn", "reason": "x", "ingredient": "corn"}]}, None) == \
        "I couldn’t fit the corn in this week."
    assert draft_opener._line_two([], {"unmet": [{"words": "Use the lamb", "reason": "x"}]}, None) == \
        "I couldn’t fit “Use the lamb” in this week."
