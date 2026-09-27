"""
A cuisine chip the household picks is honoured (Loop Board "Draft: a
cuisine chip I pick is honoured", Emily 2026-09-27).

She tapped Burgers. The draft had no burger, and a Greek chicken carried
"Burgers, as asked". Two faults:

  * nothing checked the chips against the draft — the prompt was told
    (intake.cuisines) and that was all. typed_requests.use_picked_cuisines
    now makes it true after the model: a chip no lunch or dinner answers
    gets ONE fitting slot re-picked for it (allergies, taste, who's home
    and that slot's time cap all still hold), and when none can be found
    the opener says so in one plain line ("No burgers fit this week.");
  * draft_opener.asked_fact trusted the model's derived_from.inputs, so a
    dish that wasn't a burger said it was — and junk like
    "None-specific-but-requested" could be shown. It now needs the chip
    to be this week's AND the dish to be that cuisine.

Every test stubs the model: the week at agent.generate_weekly_plan_llm,
the re-pick at swap_in_place._pick_replacement.
"""
from __future__ import annotations

import datetime

import pytest

from app import agent, tools
from app.tools import draft_opener, typed_requests
from app.tools import swap_in_place as sip


def _monday(offset_weeks: int = 1) -> str:
    from conftest import household_today
    today = household_today()
    monday = today - datetime.timedelta(days=today.weekday())
    return (monday + datetime.timedelta(days=7 * offset_weeks)).isoformat()


def _slot(date, slot, name, **extra):
    d = {
        "date": date, "slot": slot, "meal_name": name, "is_new_recipe": True,
        "ingredients": [{"item": f"{name} stuff", "qty": "1", "category": "pantry"}],
        "instructions": [f"Cook the {name.lower()}.", "Serve."],
        "reasoning": f"{name} because", "food_groups": ["protein", "vegetable", "carb"],
        "prep_time_minutes": 10, "cook_time_minutes": 15,
    }
    d.update(extra)
    return d


DINNERS = ["Greek chicken", "Miso cod", "Bibimbap", "Ratatouille", "Jerk chicken", "Pierogi", "Dal"]
LUNCHES = ["Chickpea salad", "Lentil soup", "Egg fried rice", "Tuna melt", "Minestrone", "Falafel wrap", "Cobb salad"]


def _week(week: str, dinner_extra=None) -> list[dict]:
    out = []
    for i, date in enumerate(tools._week_dates(week)):
        out.append(_slot(date, "breakfast", "Overnight oats"))
        out.append(_slot(date, "snack", "Apple"))
        out.append(_slot(date, "lunch", LUNCHES[i]))
        out.append(_slot(date, "dinner", DINNERS[i], **((dinner_extra or {}).get(i) or {})))
    return out


@pytest.fixture
def stub_model(monkeypatch):
    def _stub(days):
        monkeypatch.setattr(agent, "generate_weekly_plan_llm", lambda ctx: days)
    return _stub


def _picker(monkeypatch, name="Smash burgers", cuisine="American", minutes=25):
    calls = []

    def pick(context):
        calls.append(context)
        return {
            "meal_name": name, "reason": f"{name} because", "cuisine": cuisine,
            "ingredients": [{"item": f"{name} stuff", "qty": "1", "category": "pantry"}],
            "instructions": [f"Cook the {name.lower()}.", "Serve."],
            "food_groups": ["protein", "vegetable", "carb"],
            "prep_time_minutes": 10, "cook_time_minutes": minutes - 10,
        }

    monkeypatch.setattr(sip, "_pick_replacement", pick)
    return calls


def _menu_rows(plan_id: int) -> list[dict]:
    menu = tools.get_week_menu(plan_id)
    rows = []
    for day in menu["days"]:
        for slot in ("lunch", "dinner"):
            if day.get(slot):
                rows.append(dict(day[slot], date=day["date"], slot=slot))
    return rows


# ---------- the reported bug ----------

def test_a_burger_chip_with_no_burger_gets_one_and_the_greek_chicken_stops_claiming_it(stub_model, monkeypatch):
    """CATCH. Emily's week: Burgers picked, Greek chicken labelled "Burgers,
    as asked" by the model's own inputs, no burger anywhere."""
    week = _monday()
    tools.save_week_intake(week, cuisines=["Burgers"])
    # Wednesday's Bibimbap carries the model's "cuisines:Burgers" — the
    # shape of Emily's Greek chicken — and Monday takes the burger.
    stub_model(_week(week, dinner_extra={2: {
        "cuisine": "Korean",
        "derived_from": {"inputs": ["cuisines:Burgers", "cuisines:None-specific-but-requested"]},
    }}))
    calls = _picker(monkeypatch)

    plan = agent.generate_weekly_plan(week)
    rows = _menu_rows(plan["weekly_plan_id"])

    burgers = [r for r in rows if r["title"] == "Smash burgers"]
    assert len(burgers) == 1, [r["title"] for r in rows]
    assert burgers[0]["asked"] == "Burgers, as asked"
    assert calls and calls[0]["must_be_cuisine"] == "Burgers"
    # Nothing that isn't a burger says it is one, and the junk input is never shown.
    for r in rows:
        if r["title"] != "Smash burgers":
            assert r.get("asked") in (None, "as asked", "travels well"), (r["title"], r.get("asked"))
    mislabelled = next(r for r in rows if r["title"] == "Bibimbap")
    assert mislabelled["asked"] is None


def test_a_chip_the_week_already_answers_costs_no_call(stub_model, monkeypatch):
    week = _monday()
    tools.save_week_intake(week, cuisines=["Greek"])
    stub_model(_week(week, dinner_extra={0: {"cuisine": "Greek", "derived_from": {"inputs": ["cuisines:Greek"]}}}))
    calls = _picker(monkeypatch)

    plan = agent.generate_weekly_plan(week)
    assert calls == []
    greek = next(r for r in _menu_rows(plan["weekly_plan_id"]) if r["title"] == "Greek chicken")
    assert greek["asked"] == "Greek, as asked"


def test_a_chip_nothing_can_answer_is_said_in_one_plain_line(stub_model, monkeypatch):
    """CATCH. The picker never comes back with a burger: the week stands,
    and the opener says so rather than staying quiet."""
    week = _monday()
    tools.save_week_intake(week, cuisines=["Burgers"])
    stub_model(_week(week))
    _picker(monkeypatch, name="Lemon orzo", cuisine="Mediterranean")

    plan = agent.generate_weekly_plan(week)
    menu = tools.get_week_menu(plan["weekly_plan_id"])
    assert "No burgers fit this week." in menu["draft_opener"]
    assert "Lemon orzo" not in [r["title"] for r in _menu_rows(plan["weekly_plan_id"])]


def test_the_burger_lands_on_a_night_it_fits(stub_model, monkeypatch):
    """Mon–Fri are rush nights (30 minutes); a 45-minute burger goes on the
    weekend, never on a rush night."""
    week = _monday()
    dates = tools._week_dates(week)
    tools.save_week_intake(week, cuisines=["Burgers"], night_tags={d: ["rush"] for d in dates[:5]})
    stub_model(_week(week))
    _picker(monkeypatch, minutes=45)

    plan = agent.generate_weekly_plan(week)
    burger = next(r for r in _menu_rows(plan["weekly_plan_id"]) if r["title"] == "Smash burgers")
    assert burger["date"] in dates[5:]


def test_a_pick_too_long_for_every_open_night_is_refused(stub_model, monkeypatch):
    week = _monday()
    dates = tools._week_dates(week)
    tools.save_week_intake(week, cuisines=["Burgers"], night_tags={d: ["rush"] for d in dates})
    stub_model(_week(week))
    _picker(monkeypatch, minutes=60)

    plan = agent.generate_weekly_plan(week)
    assert "Smash burgers" not in [r["title"] for r in _menu_rows(plan["weekly_plan_id"])]
    assert "No burgers fit this week." in tools.get_week_menu(plan["weekly_plan_id"])["draft_opener"]


# ---------- the pieces ----------

def test_asked_fact_needs_this_weeks_chip_and_a_dish_of_it():
    fact = draft_opener.asked_fact
    greek = {"meal": "Greek chicken", "cuisine": "Greek", "derived_from": {"inputs": ["cuisines:Burgers"]}}
    assert fact(greek, cuisines=["Burgers"]) is None
    burger = {"meal": "Smash burgers", "cuisine": "American", "derived_from": {"inputs": ["cuisines:burgers"]}}
    assert fact(burger, cuisines=["Burgers"]) == "Burgers, as asked"
    assert fact(burger, cuisines=[]) is None
    junk = {"meal": "Dal", "derived_from": {"inputs": ["cuisines:None-specific-but-requested"]}}
    assert fact(junk, cuisines=["Indian"]) is None


@pytest.mark.parametrize("chip, meal, cuisine, hit", [
    ("Burgers", "Smash burgers", "American", True),
    ("Burgers", "Turkey burger with slaw", "", True),
    ("Burgers", "Greek chicken", "Greek", False),
    ("Mexican", "Chicken tinga tacos", "Mexican", True),
    ("Thai", "Thaini noodles", "", False),
    ("Middle Eastern", "Chicken shawarma", "Middle Eastern", True),
    ("", "Anything", "Anything", False),
])
def test_dish_is_cuisine(chip, meal, cuisine, hit):
    assert typed_requests.dish_is_cuisine(chip, meal, cuisine) is hit


def test_the_unmet_line_is_plain():
    assert typed_requests.cuisine_unmet_line("Burgers") == "No burgers fit this week."
    assert typed_requests.cuisine_unmet_line("Mexican") == "No Mexican dish fit this week."
    assert typed_requests.cuisine_unmet_line("Middle Eastern") == "No Middle Eastern dish fit this week."


# ---------- review round, 2026-09-27 ----------

def test_a_second_chip_never_takes_the_only_dish_of_the_first(stub_model, monkeypatch):
    """CATCH: chips Mexican + Burgers, Monday's tacos the only Mexican dish
    and the roomiest night — the burger went there and Mexican vanished."""
    week = _monday()
    tools.save_week_intake(week, cuisines=["Mexican", "Burgers"])
    stub_model(_week(week, dinner_extra={0: {"meal_name": "Chicken tinga tacos", "cuisine": "Mexican"}}))
    _picker(monkeypatch)
    plan = agent.generate_weekly_plan(week)
    titles = [r["title"] for r in _menu_rows(plan["weekly_plan_id"])]
    assert "Smash burgers" in titles and "Chicken tinga tacos" in titles, titles


def test_a_dish_their_words_name_is_never_the_one_repicked(stub_model, monkeypatch):
    """Monday's Greek chicken is the roomiest night, and their words name it."""
    week = _monday()
    tools.save_week_intake(week, cuisines=["Burgers"], freeform="greek chicken please")
    stub_model(_week(week))
    _picker(monkeypatch)
    plan = agent.generate_weekly_plan(week)
    titles = [r["title"] for r in _menu_rows(plan["weekly_plan_id"])]
    assert "Greek chicken" in titles and "Smash burgers" in titles


def test_a_curries_chip_is_answered_by_a_curry_without_a_call(stub_model, monkeypatch):
    week = _monday()
    tools.save_week_intake(week, cuisines=["Curries"])
    stub_model(_week(week, dinner_extra={0: {"meal_name": "Chicken curry", "cuisine": "Indian"}}))
    calls = _picker(monkeypatch, name="Beef curry", cuisine="Thai")
    plan = agent.generate_weekly_plan(week)
    assert calls == []
    assert not any("curr" in line.lower() for line in tools.get_week_menu(plan["weekly_plan_id"])["draft_opener"])


@pytest.mark.parametrize("chip, meal, cuisine, hit", [
    ("Curries", "Chicken curry", "", True),
    ("Sandwiches", "Steak sandwich", "", True),
    ("Stir-fries", "Beef stir-fry", "", True),
    ("Fries", "Beef stir-fry", "", False),
    ("Fries", "Loaded fries", "", True),
    ("Asian", "Pad kra pao", "Thai", True),
    ("Asian", "Moussaka", "Greek", False),
    ("Mediterranean", "Moussaka", "Greek", True),
    ("Tex-Mex", "Enchiladas", "Mexican", True),
    ("BBQ", "Barbecue ribs", "", True),
    ("Barbecue", "BBQ chicken", "American", True),
    ("Middle Eastern", "Kofte", "Turkish", True),
])
def test_plurals_and_family_chips(chip, meal, cuisine, hit):
    assert typed_requests.dish_is_cuisine(chip, meal, cuisine) is hit


@pytest.mark.parametrize("chip, line", [
    ("Mexican", "No Mexican dish fit this week."),
    ("Asian", "No Asian dish fit this week."),
    ("Burgers", "No burgers fit this week."),
    ("Burger", "No burgers fit this week."),
    ("Curries", "No curries fit this week."),
    ("Curry", "No curries fit this week."),
    ("Sandwich", "No sandwiches fit this week."),
    ("Comfort food", "No comfort food fit this week."),
])
def test_the_unmet_line_never_breaks_a_word(chip, line):
    assert typed_requests.cuisine_unmet_line(chip) == line


def test_a_chip_whose_only_dish_goes_later_is_still_said(stub_model, monkeypatch):
    """CATCH: the burger lands, then the allergen sweep (stubbed to open it)
    takes it away — the opener must still say so, with no model call."""
    from app.db import get_conn
    from app.tools import allergen_gate
    week = _monday()
    tools.save_week_intake(week, cuisines=["Burgers"])
    stub_model(_week(week))
    calls = _picker(monkeypatch)

    def sweep(plan_id, **kw):
        conn = get_conn()
        row = conn.execute(
            "SELECT mpe.date FROM meal_plan_entries mpe JOIN recipes r ON r.id = mpe.recipe_id "
            "WHERE mpe.weekly_plan_id = ? AND r.name = 'Smash burgers'", (plan_id,)).fetchone()
        conn.close()
        tools.clear_plan_slot(plan_id, row["date"], "dinner")
        tools.plan_slot_open(weekly_plan_id=plan_id, meal_date=row["date"], slot="dinner",
                             open_reason="Couldn't keep it.")
        return {}

    monkeypatch.setattr(allergen_gate, "sweep_plan", sweep)
    plan = agent.generate_weekly_plan(week)
    assert len(calls) == 1
    assert "Smash burgers" not in [r["title"] for r in _menu_rows(plan["weekly_plan_id"])]
    assert tools.plan_requests(plan["weekly_plan_id"])["unmet"][0]["cuisine"] == "Burgers"
