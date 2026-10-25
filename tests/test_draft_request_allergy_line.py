"""
Emily's snag rule 9 (2026-09-23, "When your requests and your week don't
line up"): "A request that clashes with an allergy or hard no: the allergy
wins, plus one line saying why." Now: the allergy wins, and the dish is
replaced with a safe one. Decided: no change to that, but add one line so
the household knows why their request isn't there.

WHAT MAIN DID BEFORE THIS, measured through generation with the model
stubbed: the allergen gate (allergen_gate.split_safe / repick_held) held
the peanut noodles back and put a safe dish on the night — and the draft
said nothing about it. The request simply wasn't there.

The line is the gate's own record (the dish it dropped, the food it
matched, who can't have it) plus the household's own words — never the
model's prose — and only for a dish they ASKED FOR.

Same fixtures as tests/test_draft_request_on_day_out.py: the week model,
the cap pass's picker and the gate's quick pick are stubbed; everything
else is the real generation.
"""
from __future__ import annotations

import pytest

from app import tools
from app.tools import allergen_gate, draft_flags

from test_draft_request_on_day_out import _asked, _monday
from test_rush_cap_enforced import (  # noqa: E402 — the fixtures this is about
    _filler, _recipe, _week,
    picker, run, stub_model,  # noqa: F401 — pytest fixtures
)


@pytest.fixture
def replaced_with(monkeypatch):
    """The gate's re-pick hands back `name` (a dish with nothing in it
    anyone here can't have) and records what it was asked."""
    asked = []

    def _set(name):
        def quick(context):
            asked.append(context)
            return {"meal_name": name, "ingredients": [{"item": "rice", "qty": "1 cup"}],
                    "dish_note": "Simple.", "food_groups": ["protein", "vegetable", "carb"],
                    "prep_time_minutes": 0, "cook_time_minutes": 15}
        monkeypatch.setattr(allergen_gate, "quick_pick", quick)
        return asked
    return _set


def _texts(plan_id):
    return [f["text"] for f in draft_flags.plan_flags(plan_id)]


def _week_with(dates, day, dish, said, **item):
    days = _week(dates, ["Quick Eggs"] * 7, **{dates[day]: {**_asked(said), **item}})
    for d in days:
        if d["date"] == dates[day] and d["slot"] == "dinner":
            d.update(meal_name=dish, is_new_recipe=True)
    return days


def test_a_request_with_someone_s_allergen_is_replaced_and_the_draft_says_why(stub_model, run, replaced_with):
    """CATCH (main: Sesame Noodles on Tuesday, and not a word about the
    peanut noodles they asked for). The model called them "Spicy Peanut
    Noodles"; the line uses their words, which are what name the peanuts."""
    tools.add_member("Sam")
    tools.set_member_dietary_restrictions("Sam", ["peanuts"])
    week = _monday()
    dates = tools._week_dates(week)
    _filler()
    _recipe("Quick Eggs", 10)
    said = "Peanut noodles one night please"
    tools.save_week_intake(week, freeform=said)
    replaced_with("Sesame Noodles")
    stub_model(_week_with(dates, 1, "Spicy Peanut Noodles", said))

    plan_id, _ = run(week, None)

    names = {m["meal"] for m in tools.get_weekly_plan(plan_id)["meals"] if m.get("meal")}
    assert "Spicy Peanut Noodles" not in names, "the allergy wins"
    assert _texts(plan_id) == [
        "You asked for peanut noodles, but Sam can’t have peanuts, so Tuesday is Sesame Noodles instead."
    ]
    menu = tools.get_week_menu(plan_id)
    assert [f["text"] for f in menu.get("draft_flags") or []] == _texts(plan_id)
    assert all(not f["fixes"] for f in menu["draft_flags"]), "one line, no buttons"


def test_a_household_wide_hard_no_is_said_as_one(stub_model, run, replaced_with):
    """A hard What-we-know fact that names nobody: no person is invented."""
    tools.add_fact("people", "No pork in this house", hard=True)
    week = _monday()
    dates = tools._week_dates(week)
    _filler()
    _recipe("Quick Eggs", 10)
    said = "Pork chops on Thursday"
    tools.save_week_intake(week, freeform=said)
    replaced_with("Chicken Thighs")
    stub_model(_week_with(dates, 3, "Pork Chops", said))

    plan_id, _ = run(week, None)

    assert _texts(plan_id) == [
        "You asked for pork chops, but pork is a hard no here, so Thursday is Chicken Thighs instead."
    ]


@pytest.mark.parametrize("replacement, said", [
    ("Chicken Stir-Fry", ["You asked for pad thai, but Sam can’t have peanuts, so Wednesday is "
                          "Chicken Stir-Fry instead."]),
    # Still their pad thai, just without the peanuts: the request IS there.
    ("Chicken Pad Thai", []),
])
def test_a_dish_they_named_whose_list_had_the_allergen(stub_model, run, replaced_with, replacement, said):
    """Their words ("pad thai") don't name the peanuts — the gate's Pad
    Thai had them in its list. Said only when what replaced it is not their
    dish any more."""
    tools.add_member("Sam")
    tools.set_member_dietary_restrictions("Sam", ["peanuts"])
    week = _monday()
    dates = tools._week_dates(week)
    _filler()
    _recipe("Quick Eggs", 10)
    words = "pad thai on wednesday"
    tools.save_week_intake(week, freeform=words)
    replaced_with(replacement)
    stub_model(_week_with(dates, 2, "Pad Thai", words,
                          ingredients=[{"item": "rice noodles", "qty": "1"}, {"item": "crushed peanuts", "qty": "1"}]))

    plan_id, _ = run(week, None)

    assert _texts(plan_id) == said


def test_a_swapped_dish_they_did_not_ask_for_gets_no_line(stub_model, run, replaced_with):
    """
    "Something Mexican" cited on the model's pineapple tacos is not a
    request for pineapple tacos: the gate swaps them (Emily's pineapple),
    and nothing is said — "You asked for mexican…" would be untrue.
    """
    tools.add_member("Emily")
    tools.set_member_dietary_restrictions("Emily", ["pineapple allergy"])
    week = _monday()
    dates = tools._week_dates(week)
    _filler()
    _recipe("Quick Eggs", 10)
    said = "Something mexican please"
    tools.save_week_intake(week, freeform=said)
    asked = replaced_with("Chicken Burrito Bowls")
    stub_model(_week_with(dates, 4, "Mexican Pineapple Tacos", said))

    plan_id, _ = run(week, None)

    assert asked, "the gate did swap it"
    assert _texts(plan_id) == []
