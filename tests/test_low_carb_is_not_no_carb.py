"""
Low carb is not no carb.

Emily, 2026-09-21: "my preferences say 'low carbs' but it doesn't say 'no
carbs'. Make sure you can tell the difference between low, a lot, and
none." Her dinners had come with no carb at all — chicken + corn +
zucchini; salmon + broccoli — because "low carb" and "keto" were one
bucket, and that bucket meant no carb.

Four levels now (plates.carb_level): none / low / normal / lots. "Low
carb", wherever the household typed it — the eating_style line, a
What-we-know fact, the notes — maps to LOW: every lunch and dinner still
carries a carb, a small one. What this pins, with the model stubbed:

  * the prompts are handed the level and say what it means;
  * a low-carb household never gets a zero-carb dinner from the draft —
    the plate pass adds a small carb, marked so;
  * a no-carb household never gets one with a carb;
  * the plate view reads "Small" on a low-carb plate, "None" on a no-carb
    one, and never "In the dish" for a dish with no carb on a low-carb
    household;
  * an existing "low carb" household reads as low with no data edit.
"""
from __future__ import annotations

import datetime

import pytest

from app import agent, tools
from app.tools import plates, plate_parts, swap_in_place as sip
from conftest import prompt_literals


def _monday(offset_weeks: int = 1) -> str:
    today = datetime.date.today()
    monday = today - datetime.timedelta(days=today.weekday())
    return (monday + datetime.timedelta(days=7 * offset_weeks)).isoformat()


ZERO_CARB_DINNERS = {
    "Chicken with corn and zucchini": ["protein", "vegetable"],
    "Salmon with broccoli": ["protein", "vegetable"],
}

HALF_POTATO = {
    "name": "Half a roasted potato each", "covers": ["carb"],
    "ingredients": [{"item": "Potatoes", "qty": "2", "category": "produce"}],
    "instructions": ["Halve.", "Roast."], "minutes": 25,
}
SALAD = {
    "name": "Green salad", "covers": ["vegetable"],
    "ingredients": [{"item": "Lettuce", "qty": "1 head", "category": "produce"}],
    "instructions": ["Toss."], "minutes": 5,
}


@pytest.fixture
def recipes():
    for name, groups in ZERO_CARB_DINNERS.items():
        tools.add_recipe(name, ingredients=[{"item": f"{name} stuff", "qty": "1"}], food_groups=groups,
                         main_protein=name.split()[0].lower(), prep_time_minutes=10, cook_time_minutes=20)
    tools.add_recipe("Chicken and rice bowl", ingredients=[{"item": "rice", "qty": "1 cup"}],
                     food_groups=["protein", "vegetable", "carb"], main_protein="chicken",
                     prep_time_minutes=10, cook_time_minutes=20)
    tools.add_recipe("Greek yogurt with berries", ingredients=[{"item": "yogurt", "qty": "1 tub"}],
                     food_groups=["protein", "vegetable"])


@pytest.fixture
def stub_week(monkeypatch):
    seen = {}

    def _stub(days):
        def fake(ctx):
            seen["ctx"] = ctx
            return days
        monkeypatch.setattr(agent, "generate_weekly_plan_llm", fake)
        return seen
    return _stub


@pytest.fixture
def stub_sides(monkeypatch):
    calls = []

    def _fake(context):
        calls.append(context)
        wanted = set(context.get("missing") or [])
        return [s for s in (SALAD, HALF_POTATO) if set(s["covers"]) & wanted]
    monkeypatch.setattr(agent, "generate_sides_llm", _fake)
    return calls


def _week(week: str, dinner: str = "Chicken with corn and zucchini") -> list[dict]:
    """Six zero-carb dinners (the plate pass caps its side calls at
    agent.MAX_PLATE_SIDE_CALLS a week) and one that carries its own."""
    out = []
    for i, day in enumerate(tools._week_dates(week)):
        out.append({"date": day, "slot": "breakfast", "meal_name": "Greek yogurt with berries", "is_new_recipe": False})
        out.append({"date": day, "slot": "lunch", "meal_name": "Chicken and rice bowl", "is_new_recipe": False})
        out.append({"date": day, "slot": "dinner", "is_new_recipe": False,
                    "meal_name": dinner if i < agent.MAX_PLATE_SIDE_CALLS else "Chicken and rice bowl"})
        out.append({"date": day, "slot": "snack", "meal_name": "Greek yogurt with berries", "is_new_recipe": False})
    return out


def _entries(plan_id):
    return {(m["date"], m["slot"]): m for m in tools.get_weekly_plan(plan_id)["meals"]}


def _menu_dinner(plan_id, date):
    menu = tools.get_week_menu(plan_id)
    return next(d for d in menu["days"] if d["date"] == date)["dinner"]


# ---------- the levels, read from wherever they were said ----------

def test_low_carb_is_read_as_low_wherever_the_household_typed_it():
    # The eating_style line, as onboarding and What we know store it.
    tools.edit_preference("eating_style", "High-protein, Low-carb")
    assert plates.household_carb_level() == "low"
    # A What-we-know fact alone.
    tools.edit_preference("eating_style", "")
    assert plates.household_carb_level() == "normal"
    tools.add_fact("taste", "we keep the carbs low on weeknights")
    assert plates.household_carb_level() == "low"


def test_keto_beats_low_and_lots_is_its_own_level():
    assert plates.carb_level("keto, low carb") == "none"
    assert plates.carb_level("high-protein, low-carb") == "low"
    assert plates.carb_level("lots of carbs, we run") == "lots"
    assert plates.carb_level("Mediterranean") == "normal"
    assert plates.carb_level("low carb but not no carb") == "low"
    assert plates.carb_portion("low") == "small" and plates.carb_portion("none") == "none"
    assert set(plates.CARB_GUIDANCE) == set(plates.CARB_LEVELS)


# ---------- the draft ----------

def test_a_low_carb_household_never_gets_a_zero_carb_dinner(recipes, stub_week, stub_sides):
    tools.edit_preference("eating_style", "High-protein, Low-carb")
    week = _monday()
    dates = tools._week_dates(week)
    seen = stub_week(_week(week))

    plan = agent.generate_weekly_plan(week)
    plan_id = plan["weekly_plan_id"]

    # The model was told the level, in so many words.
    memory = seen["ctx"]["household_memory"]
    assert memory["carb_level"] == "low"
    assert "never none" in memory["carb_guidance"] and "SMALL" in memory["carb_guidance"]
    # Every dinner that came back with no carb got a small one.
    for d in dates:
        entry = _entries(plan_id)[(d, "dinner")]
        assert "carb" in entry["food_groups"], d
        if entry["meal"] == "Chicken and rice bowl":
            continue
        carb_sides = [s for s in entry["sides"] if "carb" in s["covers"]]
        assert [s["name"] for s in carb_sides] == ["Half a roasted potato each"]
        assert carb_sides[0]["portion"] == "small"
    assert len(stub_sides) == agent.MAX_PLATE_SIDE_CALLS
    # The side call was asked for a carb only, and told it is a small one.
    assert all(c["missing"] == ["carb"] and c["carb_portion"] == "small" for c in stub_sides)
    # The plate reads the small carb by name, never "In the dish".
    parts = {p["role"]: p for p in _menu_dinner(plan_id, dates[0])["plate_parts"]}
    assert parts["carb"]["name"] == "Half a roasted potato each (small)"
    assert parts["carb"]["source"] == "side" and parts["carb"]["missing"] is False
    # And the lunch that carries its own carb reads "Small".
    menu = tools.get_week_menu(plan_id)
    lunch = next(d for d in menu["days"] if d["date"] == dates[0])["lunch"]
    assert {p["role"]: p["name"] for p in lunch["plate_parts"]}["carb"] == "Small"


def test_a_no_carb_household_never_gets_a_carb(recipes, stub_week, stub_sides):
    tools.edit_preference("eating_style", "keto")
    week = _monday()
    dates = tools._week_dates(week)
    seen = stub_week(_week(week))

    plan = agent.generate_weekly_plan(week)
    plan_id = plan["weekly_plan_id"]

    assert seen["ctx"]["household_memory"]["carb_level"] == "none"
    assert stub_sides == [], "protein + vegetable is a full keto plate; nothing to add"
    for d in dates:
        entry = _entries(plan_id)[(d, "dinner")]
        assert entry["sides"] == []
        if entry["meal"] != "Chicken and rice bowl":
            assert "carb" not in entry["food_groups"]
    # The plate says None for the carb — and still offers the tap.
    parts = {p["role"]: p for p in _menu_dinner(plan_id, dates[0])["plate_parts"]}
    assert parts["carb"] == {"role": "carb", "word": "Carb", "name": "None", "source": None,
                             "missing": False, "empty": True}


def test_a_normal_household_is_exactly_as_before(recipes, stub_week, stub_sides):
    week = _monday()
    dates = tools._week_dates(week)
    stub_week(_week(week))
    plan = agent.generate_weekly_plan(week)
    entry = _entries(plan["weekly_plan_id"])[(dates[0], "dinner")]
    carb = [s for s in entry["sides"] if "carb" in s["covers"]][0]
    assert "portion" not in carb, "a full portion carries no marker"
    assert all(c["carb_portion"] == "normal" for c in stub_sides)
    parts = {p["role"]: p for p in _menu_dinner(plan["weekly_plan_id"], dates[0])["plate_parts"]}
    assert parts["carb"]["name"] == "Half a roasted potato each"


# ---------- the plate view, on its own ----------

def test_the_plate_view_tells_low_from_none_from_normal():
    zero = ["protein", "vegetable"]
    with_carb = ["protein", "vegetable", "carb"]
    # Low: a dish with no carb is MISSING (the pass should have added one), never "In the dish".
    low = {p["role"]: p for p in plate_parts.parts_of_plate("dinner", zero, "chicken", [], "low carb")}
    assert low["carb"]["missing"] is True and low["carb"]["name"] is None
    # Low: a dish carrying its own carb reads Small.
    low = {p["role"]: p for p in plate_parts.parts_of_plate("dinner", with_carb, "chicken", [], "low carb")}
    assert low["carb"] == {"role": "carb", "word": "Carb", "name": "Small", "source": "dish", "missing": False}
    # None: reads None, offered, never a shortfall.
    none = {p["role"]: p for p in plate_parts.parts_of_plate("dinner", zero, "chicken", [], "keto")}
    assert none["carb"]["name"] == "None" and none["carb"]["empty"] is True and none["carb"]["missing"] is False
    # Normal: as it always was.
    normal = {p["role"]: p for p in plate_parts.parts_of_plate("dinner", with_carb, "chicken", [], "")}
    assert normal["carb"]["name"] is None and normal["carb"]["source"] == "dish"
    # The level can be handed in, facts included, instead of read off the style.
    low = {p["role"]: p for p in plate_parts.parts_of_plate("dinner", with_carb, "chicken", [], "", carb_level="low")}
    assert low["carb"]["name"] == "Small"


# ---------- the prompts ----------

def test_the_prompts_name_the_level_and_say_low_is_not_none():
    for fn in (agent.generate_weekly_plan_llm, agent.generate_component_plan_llm):
        src = prompt_literals(fn)
        assert "household_memory.carb_level" in src
        assert "none / low / normal / lots" in src
        assert "not no carb" in src
    sides = agent._SIDE_INSTRUCTIONS
    assert "carb_portion" in sides and "HALF portion" in sides
    assert "not no carb" in sip.INSTRUCTIONS and "carb_portion" in sip.INSTRUCTIONS


def test_the_swap_context_carries_the_portion(recipes, stub_week, stub_sides):
    tools.edit_preference("eating_style", "low carb")
    week = _monday()
    stub_week(_week(week))
    plan = agent.generate_weekly_plan(week)
    entry = _entries(plan["weekly_plan_id"])[(tools._week_dates(week)[0], "dinner")]
    ctx = sip.build_swap_context(plan["weekly_plan_id"], {
        "date": entry["date"], "slot": "dinner", "meal": entry["meal"], "entry_id": entry["entry_id"],
    })
    assert ctx["plate_rule"] == ["protein", "vegetable", "carb"]
    assert ctx["carb_portion"] == "small" and "not no carb" in ctx["carb_guidance"]


# ---------- the verifier's round (2026-09-21) ----------

@pytest.mark.parametrize("style,level", [
    ("no-carb", "none"), ("zero-carb", "none"), ("carb-free", "none"), ("Keto-ish", "none"),
    ("low-carb", "low"), ("Low-Carb, high-protein", "low"),
    ("high-carb", "lots"), ("carb-heavy", "lots"),
])
def test_a_hyphen_reads_the_same_as_a_space(style, level):
    assert plates.carb_level(style) == level
    assert plates.carb_level(style.replace("-", " ")) == level


@pytest.mark.parametrize("fact", [
    "Vic tried keto in 2023 and hated it",
    "I am not on keto anymore",
    "we used to do low carb",
    "stopped keto last year",
    "Emily gave up on Atkins",
    "no longer keto",
])
def test_a_fact_about_the_past_does_not_set_the_level(fact):
    assert plates.carb_level("", fact) == "normal"
    tools.add_fact("taste", fact)
    assert plates.household_carb_level() == "normal"


def test_a_present_fact_still_counts_and_the_eating_style_line_wins_over_facts():
    assert plates.carb_level("", "we keep the carbs low") == "low"
    assert plates.carb_level("", "we are on keto") == "none"
    # Where they disagree, the eating-style line is the answer.
    assert plates.carb_level("lots of carbs", "we are on keto") == "lots"
    assert plates.carb_level("low carb", "we're keto now") == "low"
    tools.edit_preference("eating_style", "Mediterranean, lots of carbs")
    tools.add_fact("taste", "we are on keto")
    assert plates.household_carb_level() == "lots"


def test_the_none_chip_says_add_in_its_label():
    from pathlib import Path
    js = Path("static/shell.js").read_text()
    assert "var verb = part.empty ? 'Add a ' : 'Change the ';" in js
    assert "escapeHtml(verb + part.word.toLowerCase())" in js


def test_the_swap_context_explains_the_carb_fields_in_the_context_itself(recipes, stub_week, stub_sides):
    """The three-picks sheet reads this context under other instructions
    (swap_options, being rewritten elsewhere): the meaning rides along."""
    tools.edit_preference("eating_style", "low carb")
    week = _monday()
    stub_week(_week(week))
    plan = agent.generate_weekly_plan(week)
    entry = _entries(plan["weekly_plan_id"])[(tools._week_dates(week)[0], "dinner")]
    ctx = sip.build_swap_context(plan["weekly_plan_id"], {
        "date": entry["date"], "slot": "dinner", "meal": entry["meal"], "entry_id": entry["entry_id"],
    })
    assert "carb_portion" in ctx["carb_note"] and "not no carb" in ctx["carb_note"]
