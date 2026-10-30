"""
The Move sheet says "made ahead" about a night the household cooked ahead
for, the way the Plan tab and the Cook tab already do (defect hunt,
2026-10-29).

Thursday's chili cooked ahead for Saturday (cook_ahead.set_cook_ahead): the
Plan tab's Saturday reads "Made ahead — Thursday's Chili". Tap Move on the
chili, on that same tab, and the sheet said "Saturday's leftovers move with
the Chili", listed Saturday as "Chili leftovers", and the toast said
"Chili was moved to Friday, leftovers to Sunday". A planner-written
leftovers chain keeps its own words — test_move_meal_between_days pins
those.
"""
from __future__ import annotations

import datetime

from app import tools
from app.tools import cook_ahead, meal_move


def _monday() -> datetime.date:
    from conftest import household_today
    today = household_today()
    return today - datetime.timedelta(days=today.weekday())


# Next week, so no day is "already gone".
WEEK = (_monday() + datetime.timedelta(days=7)).isoformat()
MON, TUE, WED, THU, FRI, SAT, SUN = tools._week_dates(WEEK)


def _week(ahead: bool = True) -> dict:
    tools.add_member("Alex")
    tools.add_member("Sam")
    for name in ("Chili", "Tacos", "Pasta Bake", "Lentil Soup", "Salmon Bowls", "Stir Fry"):
        tools.add_recipe(name, ingredients=[{"item": f"{name} onion", "qty": "1", "category": "produce"}],
                         prep_time_minutes=10, cook_time_minutes=20, default_servings=4)
    plan = tools.create_weekly_plan(WEEK)["weekly_plan_id"]
    names = ["Tacos", "Pasta Bake", "Lentil Soup", "Chili", "Salmon Bowls", "Chili", "Stir Fry"]
    ids = []
    for d, n in zip((MON, TUE, WED, THU, FRI, SAT, SUN), names):
        # The planner's own chain is written as a links_to the repair pass
        # confirms; the household's is set_cook_ahead's, below.
        derived = {"links_to": f"{THU}:dinner"} if (d == SAT and not ahead) else None
        ids.append(tools.plan_meal(d, n, slot="dinner", weekly_plan_id=plan, derived_from=derived)["entry_id"])
    if ahead:
        out = cook_ahead.set_cook_ahead(ids[3], [ids[5]])
        assert isinstance(out, dict), out
    else:
        tools.repair_leftover_chains(plan)
    chains = tools.plan_leftover_chains(plan)
    assert ids[5] in chains["leftovers"], "Saturday eats Thursday's chili"
    return {"plan": plan, "chili": ids[3], "saturday": ids[5]}


def test_the_sheet_names_the_made_ahead_night_as_made_ahead():
    w = _week()

    sheet = meal_move.move_options(w["plan"], w["chili"])

    assert sheet["sub"].endswith("Saturday’s made-ahead portion moves with the Chili.")
    days = {d["date"]: d for d in sheet["days"]}
    assert days[SAT]["meal"] == "Chili, made ahead"
    assert days[SUN]["reason"] == "Its made-ahead portion would land after this week"
    assert "leftovers" not in sheet["sub"] + "".join(d["meal"] + d["reason"] for d in sheet["days"])


def test_the_toast_and_chat_say_made_ahead_too():
    w = _week()

    out = meal_move.move_meal(w["plan"], w["chili"], FRI)
    assert out["status"] == "moved"
    assert out["said"] == "Chili was moved to Friday, made-ahead portion to Sunday"

    refused = tools.move_meal_for_chat(w["plan"], SUN, MON)
    assert refused["status"] == "refused"
    assert refused["message"].startswith("A made-ahead portion moves with the meal it comes from.")


def test_a_leftovers_chain_keeps_its_own_words():
    w = _week(ahead=False)

    sheet = meal_move.move_options(w["plan"], w["chili"])

    assert sheet["sub"].endswith("Saturday’s leftovers move with the Chili.")
    assert {d["date"]: d for d in sheet["days"]}[SAT]["meal"] == "Chili leftovers"
    assert meal_move.move_meal(w["plan"], w["chili"], FRI)["said"] == "Chili was moved to Friday, leftovers to Sunday"



def test_a_dinner_feeding_both_kinds_of_night_keeps_the_older_word():
    # Review follow-up: set_cook_ahead keeps a planner leftovers night it
    # never offered, so one dinner can feed a leftovers night AND a
    # made-ahead one, and the source-level flag is true if ANY is ahead.
    # Calling both "made-ahead portions" contradicted the leftovers night's
    # own "Chili leftovers" row, so only an all-ahead chain says it.
    def chains(*flags):
        return {"sources": {1: {"cook_ahead": any(flags),
                                "targets": [{"entry_id": 10 + i, "cook_ahead": f} for i, f in enumerate(flags)]}}}
    assert meal_move._made_ahead(chains(True, True), 1) is True
    assert meal_move._made_ahead(chains(False, True), 1) is False
    assert meal_move._made_ahead(chains(False), 1) is False
    assert meal_move._made_ahead({"sources": {}}, 1) is False
