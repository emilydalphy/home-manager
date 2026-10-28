"""
As a shopper, lasagna noodles are on my list to buy, not tucked under
Spices (overnight hunt, 2026-10-28).

Seen in passing: an approved week's lasagna noodles reported "in the Spices
section". Driven end to end (the library's own Lasagna, approved, then
GET /api/grocery-list/spices) they are NOT a spice: they wait in "Pantry
staples this week", which is Emily's pantry list (pantry._PANTRY,
2026-10-18: "lasagna noodle"). That is pinned here so it stays true.

The sweep of every distinct ingredient name in the recipe library through
spices.is_spice found three that were wrong — foods that merely carry a
spice-ish word: "Vietnamese coriander" (a fresh herb, rau răm), "Shrimp
paste in soybean oil" (read as oil) and "Minced crab paste in spices" (read
as a blend). Each went to Spices this week, assumed at home, and was never
bought. A representative handful of the rest is pinned, both ways.
"""
from __future__ import annotations

import datetime
import json
from pathlib import Path

import pytest
from conftest import household_today

from app import tools
from app.tools import spices

REPO = Path(__file__).resolve().parent.parent


@pytest.mark.parametrize("name", [
    "Lasagna noodles",
    "Vietnamese coriander",
    "Shrimp paste in soybean oil",
    "Minced crab paste in spices",
    "Tuna in olive oil",          # the 2026-10-23 rule, still holding
    "Krachai (fingerroot) in brine",
    "Tuna, drained, in olive oil",     # a comma never switches the rule off
    "Anchovy fillets, packed in olive oil",
    "Chipotle peppers in adobo",
    "Fresh basil",
    "Red bell pepper",
    "Garlic cloves",
])
def test_a_food_with_a_spice_ish_word_is_not_a_spice(name):
    assert not spices.is_spice(name)
    assert spices.assumed_section(name) != spices.SECTION_SPICES


@pytest.mark.parametrize("name", [
    "Ground cumin",
    "Dried oregano",
    "Kosher salt",
    "Bay leaves",
    "Saffron threads",
    "Saffron, bloomed in water",  # " in " after a comma is a method, not a tin
    "Mustard seeds in hot oil",   # "hot" is no oil's kind: still mustard seeds
    "Olive oil",
    "Ground coriander",
    "Dried Vietnamese coriander",  # dried, it is the rack's like any herb
    "Lebanese seven spice",
])
def test_real_spices_and_dried_herbs_still_go_to_spices(name):
    assert spices.is_spice(name)
    assert spices.assumed_section(name) == spices.SECTION_SPICES


def test_the_librarys_lasagna_keeps_its_noodles_out_of_spices():
    """Through approval: the noodles wait under Pantry staples, the
    seasoning under Spices, and the noodles are never a Spices row."""
    dish = json.loads((REPO / "data/recipe_library_batches/lasagna.json").read_text(encoding="utf-8"))
    ingredients = dish["ingredients"] + [
        {"item": "Vietnamese coriander", "qty": "1 bunch", "category": "produce"},
        {"item": "Shrimp paste in soybean oil", "qty": "1 jar", "category": "pantry"},
    ]
    tools.add_recipe("Lasagna", ingredients=ingredients, prep_time_minutes=20,
                     cook_time_minutes=60, default_servings=4)
    today = household_today()
    monday = today - datetime.timedelta(days=today.weekday())
    plan = tools.create_weekly_plan(monday.isoformat())["weekly_plan_id"]
    tools.plan_meal((monday + datetime.timedelta(days=6)).isoformat(), "Lasagna",
                    slot="dinner", weekly_plan_id=plan)
    tools.approve_weekly_plan(plan)

    sections = {sp["item"]: sp["section"] for sp in tools.list_spices_this_week()["items"]}
    assert sections.get("Lasagna noodles") == spices.SECTION_PANTRY
    assert sections.get("Italian seasoning") == spices.SECTION_SPICES
    assert "Vietnamese coriander" not in sections
    assert "Shrimp paste in soybean oil" not in sections

    to_buy = {i["item"] for i in tools.list_grocery_list()}
    assert {"Vietnamese coriander", "Shrimp paste in soybean oil"} <= to_buy
