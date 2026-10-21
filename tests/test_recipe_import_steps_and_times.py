"""
A linked recipe keeps the amounts its steps open on, and reads its times
the way they are written (overnight defect hunt, 2026-10-21). The fake
network is test_recipe_import's.
"""
from __future__ import annotations

import json

import pytest

from app import recipe_import as ri, tools

from test_recipe_import import _html_response, _page, _wire


# A step that opens on an amount lost its first number to the strip meant
# for a site's own step numbering: "1.5 kg potatoes" saved as "5 kg", and
# "2-3 minutes per side" as "3 minutes". The time is the Java-printer
# spelling of 35 minutes, which used to save as no time at all.
NUMBERED_RECIPE = json.dumps({
    "@context": "https://schema.org",
    "@type": "Recipe",
    "name": "Potato Stew",
    "recipeYield": "4",
    "prepTime": "P0Y0M0DT0H15M0.000S",
    "cookTime": "P0Y0M0DT0H35M0.000S",
    "recipeIngredient": ["1.5 kg potatoes", "2 tbsp olive oil"],
    "recipeInstructions": [
        {"@type": "HowToStep", "text": "1. Heat the oil."},
        {"@type": "HowToStep", "text": "1.5 kg potatoes go into the pot."},
        {"@type": "HowToStep", "text": "2-3 minutes per side, then turn."},
        {"@type": "HowToStep", "text": "Step 4: Serve."},
    ],
})


def test_a_step_that_opens_on_an_amount_keeps_it_through_the_save(signed_in, monkeypatch):
    _wire(monkeypatch, {"/stew": _html_response(_page(NUMBERED_RECIPE))})
    res = signed_in.post("/api/recipes/import-url", json={"url": "https://recipes.example.com/stew"})
    assert res.status_code == 200, res.text
    draft = res.json()["draft"]
    assert draft["prep_time_minutes"] == 15 and draft["cook_time_minutes"] == 35
    assert signed_in.post("/api/recipes/add", json=draft).status_code == 200

    saved = tools.get_recipe("Potato Stew")
    assert saved["instructions"] == [
        "Heat the oil.",
        "1.5 kg potatoes go into the pot.",
        "2-3 minutes per side, then turn.",
        "Serve.",
    ]
    assert saved["prep_time_minutes"] == 15 and saved["cook_time_minutes"] == 35


@pytest.mark.parametrize("step, kept", [
    ("1. 2-3 cloves garlic, sliced.", "2-3 cloves garlic, sliced."),  # the number goes, the amount stays
    ("2) Stir.", "Stir."),
    ("3:30 is when the oven goes on.", "3:30 is when the oven goes on."),
    ("1 - 2 minutes more.", "1 - 2 minutes more."),
])
def test_only_a_step_number_is_dropped(step, kept):
    assert ri.parse_instructions([step]) == [kept]


@pytest.mark.parametrize("value, minutes", [
    ("1 1/2 hours", 90), ("1½ hrs", 90), ("1h30m", 90),
    ("P30M", 30),  # a site's "thirty minutes", not thirty months
    ("P0Y0M1DT0H0M", 1440),
])
def test_times_read_the_way_they_are_written(value, minutes):
    assert ri.parse_duration_minutes(value) == minutes
