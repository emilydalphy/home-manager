"""
A dish with its own base never gets a second carb bolted on (Emily,
2026-09-27, on the live draft): Korean-Style Gochujang Beef Bowls, served
over 6 cups of cauliflower rice, came back with a "Steamed rice (small)"
side — 2¼ cups of jasmine rice on the list beside the cauliflower rice.

has_starch rightly says cauliflower rice is not rice, so dish_has_carb said
the dish had no carb, the plate pass found the plate short one and asked
for a side — and the side call was told the dish's NAME only, so its "don't
duplicate what the dish has" rule could not work. Now a low-carb base
(plates.carb_standin) fills the carb; the side call sees the ingredients;
and the plate's Carb line names the base, so Change rewrites it in place.

The model is never called.
"""
from __future__ import annotations

import datetime

import pytest

from conftest import household_today

from app import agent, tools
from app.tools import plate_parts as pp
from app.tools import plates

TODAY = household_today()
D1 = (TODAY + datetime.timedelta(days=1)).isoformat()
BOWLS = "Korean-Style Gochujang Beef Bowls"
INGREDIENTS = [
    {"item": "Flank steak", "qty": "1 lb", "category": "meat/seafood"},
    {"item": "Gochujang", "qty": "2 tbsp", "category": "pantry"},
    {"item": "Fresh ginger", "qty": "1 piece", "category": "produce"},
    {"item": "Broccoli", "qty": "1 head", "category": "produce"},
    {"item": "Cauliflower rice", "qty": "2 cups", "category": "produce"},
]


def test_a_low_carb_base_fills_the_carb_and_is_still_not_a_starch():
    assert plates.dish_has_carb(BOWLS, INGREDIENTS)
    for base in ("Cauliflower rice", "Riced cauliflower", "Zucchini noodles", "Zoodles",
                 "Spaghetti squash", "Butter lettuce cups", "Lettuce wraps", "Spiralized zucchini",
                 "Shirataki noodles"):
        assert plates.dish_has_carb("Something", [{"item": base}]), base
    assert not plates.has_starch("Cauliflower rice"), "has_starch's own answer is unchanged"
    assert not plates.dish_has_carb("Steak and salad", [{"item": "Romaine lettuce"}, {"item": "Cauliflower"}])


@pytest.fixture
def bowls():
    tools.add_member("Emily")
    tools.add_member("Vineeth")
    tools.add_recipe(BOWLS, ingredients=INGREDIENTS, food_groups=["protein", "vegetable"],
                     main_protein="beef", instructions=["Sear.", "Build."], default_servings=2)
    plan_id = tools.create_weekly_plan(TODAY.isoformat())["weekly_plan_id"]
    entry_id = tools.plan_meal(D1, BOWLS, slot="dinner", weekly_plan_id=plan_id)["entry_id"]
    pp._OPTIONS_CACHE.clear()
    return plan_id, entry_id


def test_the_plate_pass_never_bolts_a_carb_onto_its_own_base(bowls, monkeypatch):
    plan_id, entry_id = bowls
    asked = []
    monkeypatch.setattr(agent, "generate_sides_llm", lambda ctx: asked.append(ctx) or [
        {"name": "Steamed rice", "covers": ["carb"],
         "ingredients": [{"item": "Jasmine rice", "qty": "1 cup", "category": "pantry"}],
         "instructions": ["Steam it."]}])
    for level in ("low", "normal"):
        agent._complete_plates_pass(plan_id, {"complete_plates": True, "carb_level": level,
                                              "eating_style": "", "members": []}, None)
    assert asked == [], "the plate is whole: its base is the carb"
    assert plates.get_sides(entry_id) == []


def test_the_side_call_is_told_what_the_dish_is_made_of(bowls):
    _plan_id, entry_id = bowls
    seen = []
    plates.complete_plate(entry_id, {"meal": BOWLS, "slot": "dinner", "missing": ["vegetable"]},
                          side_generator=lambda ctx: seen.append(ctx) or [])
    assert "Cauliflower rice" in seen[0]["dish_ingredients"]
    assert "Broccoli" in seen[0]["dish_ingredients"]


def test_the_carb_line_names_the_base_and_change_rewrites_it(bowls):
    plan_id, entry_id = bowls
    day = next(d for d in tools.get_week_menu(plan_id)["days"] if d["date"] == D1)
    by_role = {p["role"]: p for p in day["dinner"]["plate_parts"]}
    assert by_role["carb"]["name"] == "Cauliflower rice" and by_role["carb"]["source"] == "dish"
    assert by_role["vegetable"]["name"] == "Broccoli", "the base is the carb line, not the veg"
    # A low-carb household: the base is the whole base, not "· small".
    parts = pp.parts_of_plate("dinner", ["protein", "vegetable", "carb"], "beef", [], "",
                              carb_level="low", ingredients=INGREDIENTS)
    assert [p["name"] for p in parts if p["role"] == "carb"] == ["Cauliflower rice"]
    # Change on it reaches the dish, told what the carb is now.
    seen = []
    out = pp.change_part(plan_id, entry_id, "carb", "Jasmine rice", asker=lambda ctx: seen.append(ctx) or {
        "meal_name": f"{BOWLS} with Jasmine Rice", "reason": "Jasmine rice instead of cauliflower rice.",
        "ingredients": [dict(i) for i in INGREDIENTS[:4]] + [
            {"item": "Jasmine rice", "qty": "1 cup", "category": "pantry"}],
        "instructions": ["Sear.", "Cook the rice.", "Build."], "food_groups": ["protein", "vegetable", "carb"],
        "main_protein": "beef"})
    assert seen[0]["current_carb"] == "Cauliflower rice"
    assert out["status"] == "changed"
