"""
Cook days, option B (Emily, 2026-10-31 — "bring it here"): a day that eats
something made earlier says when it was made and what to do with it today,
right on that day, so nobody goes hunting for the day the recipe lives on
(DESIGN_SYSTEM S12).

Server: get_week_menu's leftover_from, the Cook cards and Today's meals rows
carry `serve_step` — the recipe's own reheat sentence, else "Heat until hot
all the way through." / "Eat it cold, straight from the fridge." (never a
made-up time). Plan's day view: the row says "Made Sunday" and "This
morning: heat until hot…"; its sheet heads with the step and its recipe
link lands on that exact cook. Today's meal row and sheet say the step too.
"""
from __future__ import annotations

import datetime
import json

import pytest

import nodeharness
from conftest import household_today
from app import tools
from app.tools import leftovers as _leftovers
from test_declutter_2026_10_30 import _needs_node, _plan, _today

HOT = _leftovers.SERVE_HOT_DEFAULT
COLD = _leftovers.SERVE_COLD_DEFAULT


# ---------------------------------------------------------------------------
# serve_step
# ---------------------------------------------------------------------------

def test_the_recipes_own_reheat_line_wins_and_says_how_long():
    recipe = {"notes": "Keeps 4 days. Reheat in the microwave for 30 seconds.", "instructions": ["Bake."]}
    assert _leftovers.serve_step(recipe, "breakfast", True) == "Reheat in the microwave for 30 seconds."


def test_a_made_ahead_dish_with_no_heat_is_eaten_cold_and_anything_else_is_heated():
    cold = {"notes": "", "instructions": ["Stir the oats into the milk.", "Chill overnight."]}
    hot = {"notes": "", "instructions": ["Bake 20 minutes."]}
    assert _leftovers.serve_step(cold, "breakfast", True) == COLD
    assert _leftovers.serve_step(hot, "breakfast", True) == HOT
    assert _leftovers.serve_step(None, "dinner", True) == HOT, "nothing known: heat, never a guess"
    # A leftovers night is never called cold (served_cold is made-ahead only).
    assert _leftovers.serve_step(cold, "lunch", False) == HOT


# ---------------------------------------------------------------------------
# get_week_menu, Cook cards, Today's rows
# ---------------------------------------------------------------------------

TODAY = household_today()
START = (TODAY - datetime.timedelta(days=1)).isoformat()
SUN = (TODAY + datetime.timedelta(days=1)).isoformat()
TUE = (TODAY + datetime.timedelta(days=3)).isoformat()


@pytest.fixture
def batch():
    tools.add_member("Alex")
    tools.add_recipe("Egg Bites", ingredients=[{"item": "Eggs", "qty": "10", "category": "dairy"}],
                     food_groups=["protein"], default_servings=2, cook_time_minutes=20,
                     instructions=["Whisk the eggs.", "Bake 20 minutes."],
                     notes="Keeps 4 days. Reheat in the microwave for 30 seconds.")
    tools.add_recipe("Overnight Oats", ingredients=[{"item": "Oats", "qty": "1 cup", "category": "pantry"}],
                     food_groups=["carb"], default_servings=2,
                     instructions=["Stir the oats into the milk.", "Chill overnight."])
    plan = tools.create_weekly_plan(START)["weekly_plan_id"]
    tools.plan_meal(SUN, "Egg Bites", slot="breakfast", weekly_plan_id=plan)
    tools.plan_meal(TUE, "Egg Bites", slot="breakfast", weekly_plan_id=plan,
                    derived_from={"links_to": f"{SUN}:breakfast", "cook_ahead": True})
    tools.plan_meal(SUN, "Overnight Oats", slot="lunch", weekly_plan_id=plan)
    tools.plan_meal(TUE, "Overnight Oats", slot="lunch", weekly_plan_id=plan,
                    derived_from={"links_to": f"{SUN}:lunch", "cook_ahead": True})
    tools.repair_leftover_chains(plan)
    return plan


def _menu_slot(plan, date, slot):
    return next(d for d in tools.get_week_menu(plan)["days"] if d["date"] == date)[slot]


def test_the_plan_carries_what_to_do_on_the_day_it_is_eaten(batch):
    bites = _menu_slot(batch, TUE, "breakfast")
    assert bites["leftover_from"]["date"] == SUN
    assert bites["leftover_from"]["serve_step"] == "Reheat in the microwave for 30 seconds."
    oats = _menu_slot(batch, TUE, "lunch")
    assert oats["leftover_from"]["serve_step"] == COLD
    # The cook itself carries none — it is not eaten from the fridge.
    assert "leftover_from" not in _menu_slot(batch, SUN, "breakfast")


def test_the_cook_cards_and_todays_rows_carry_the_same_step(batch):
    view = tools.get_cooker_view(batch)
    card = next(m for m in view["meals"] if m["date"] == TUE and m["slot"] == "breakfast")
    assert card["serve_step"] == "Reheat in the microwave for 30 seconds."
    rows = tools.day_meals_for_day(TUE, view)
    by_slot = {r["slot"]: r for r in rows}
    assert by_slot["breakfast"]["serve_step"] == "Reheat in the microwave for 30 seconds."
    assert by_slot["lunch"]["serve_step"] == COLD
    sun = {r["slot"]: r for r in tools.day_meals_for_day(SUN, view)}
    assert sun["breakfast"]["serve_step"] == "", "a cook says nothing about serving"


# ---------------------------------------------------------------------------
# Plan's day view and its sheet
# ---------------------------------------------------------------------------

def _with_made_ahead(script: str, step: str = "Heat until hot all the way through."):
    """The harness week, with Wednesday's breakfast made ahead on Tuesday
    and Wednesday's lunch (Tuesday's paella) given a step."""
    return _plan(
        "DAYS[2].breakfast = Object.assign({}, DAYS[2].breakfast, { source: 'leftovers', meta: 'reheat',"
        " leftover_from: { date: '2026-09-22', slot: 'breakfast', meal: 'Frittata', cook_ahead: true,"
        " serve_step: %s } });\n"
        "DAYS[2].lunch.leftover_from.serve_step = 'Heat until hot all the way through.';\n" % json.dumps(step)
        + script
    )


@_needs_node
def test_a_day_says_made_sunday_and_what_to_do_this_morning():
    out = _with_made_ahead(
        "weekState.planDay = 2;\n"
        "console.log(JSON.stringify(planWeekHtml({ status: 'approved', days: DAYS }, DAYS)));"
    )
    breakfast = out.split('data-plan-row="breakfast"', 1)[1].split("</button>", 1)[0]
    assert '<span class="wk-row-meta">Made Tuesday</span>' in breakfast
    assert '<span class="wk-row-meta wk-qrow-step">In the morning: heat until hot all the way through.</span>' in breakfast
    # A leftovers night still says just "Leftovers" (S11), with its step.
    lunch = out.split('data-plan-row="lunch"', 1)[1].split("</button>", 1)[0]
    assert '<span class="wk-row-meta">Leftovers</span>' in lunch
    assert "At lunch: heat until hot" in lunch
    assert "Tuesday" not in lunch
    # The cook's own day says nothing about serving.
    tue = _with_made_ahead(
        "weekState.planDay = 1;\n"
        "console.log(JSON.stringify(planWeekHtml({ status: 'approved', days: DAYS }, DAYS)));"
    )
    assert "wk-qrow-step" not in tue


@_needs_node
def test_today_says_this_morning_and_keeps_the_recipes_capitals():
    out = _with_made_ahead(
        "var d = Object.assign({}, DAYS[2], { isToday: true });\n"
        "console.log(JSON.stringify([planServeLine(d, 'breakfast', d.breakfast),"
        " planServeLine(Object.assign({}, d, { isToday: true }), 'dinner', d.lunch)]));",
        step="IKEA bowls: microwave 1 minute.",
    )
    assert out[0] == "This morning: IKEA bowls: microwave 1 minute."
    assert out[1] == "Tonight: heat until hot all the way through."


@_needs_node
def test_the_sheet_heads_with_the_step_and_its_recipe_goes_to_that_cook():
    out = _with_made_ahead(
        "console.log(JSON.stringify({ made: planRowSheetHtml(DAYS[2], 'breakfast'),"
        " cook: planRowSheetHtml(DAYS[2], 'dinner') }));"
    )
    made = out["made"]
    assert '<span class="quiet-section-head">In the morning</span>' in made
    assert "Heat until hot all the way through." in made
    assert 'data-plan-opt="source">See Tuesday’s recipe</button>' in made
    assert 'data-plan-opt="recipe"' not in made
    assert made.index("In the morning") < made.index("See Tuesday")
    # An ordinary cook keeps "See the recipe" and no serving section.
    assert 'data-plan-opt="recipe">See the recipe' in out["cook"]
    assert "quiet-section" not in out["cook"]


# ---------------------------------------------------------------------------
# Today's meals card
# ---------------------------------------------------------------------------

@_needs_node
def test_todays_row_and_sheet_say_what_to_do_with_a_made_ahead_meal():
    rows = [
        {"slot": "breakfast", "label": "Breakfast", "dishes": ["Egg Bites"], "per_person": False,
         "is_leftovers": True, "made_ahead": True, "serve_step": "Reheat in the microwave for 30 seconds.",
         "lines": [{"who": "everyone", "where": "", "dish": "Egg Bites"}], "note": "", "entry_id": 1},
        {"slot": "dinner", "label": "Dinner", "dishes": ["Pepper chicken"], "per_person": False,
         "serve_step": "", "lines": [{"who": "everyone", "where": "", "dish": "Pepper chicken"}],
         "note": "", "entry_id": 3},
    ]
    out = _today(
        "var rows = %s;\n"
        "console.log(JSON.stringify({ card: todayMealsCardHtml(rows), sheet: todayMealSheetHtml(rows[0]),"
        " cook: todayMealSheetHtml(rows[1]) }));" % json.dumps(rows)
    )
    assert '<span class="day-meal-note">This morning: reheat in the microwave for 30 seconds.</span>' in out["card"]
    assert out["card"].count("This morning") == 1
    assert '<span class="quiet-section-head">This morning</span>' in out["sheet"]
    assert "Reheat in the microwave for 30 seconds." in out["sheet"]
    assert "This morning" not in out["cook"]
