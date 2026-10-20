"""A recipe can't be saved as serving 0 or fewer people.

Overnight hunt, 2026-10-20: update_recipe_details (the chat's "fix this
recipe" tool, and the save behind "Fill in this recipe") stored
default_servings of 0 or -2 as is. The recipe card then said it served 0,
while scale_recipe quietly treated it as 4 — two answers to one question.
The save now refuses a non-positive count with a message the chat can relay,
and "Fill in this recipe" drops a model's 0 rather than losing the steps.
"""
from __future__ import annotations

import pytest

from app import agent, tools


def _recipe() -> None:
    tools.add_recipe(
        "Weeknight Dal",
        ingredients=[{"item": "Red lentils", "qty": "1 cup", "category": "pantry"}],
        instructions=["Simmer the lentils."],
        default_servings=4,
    )


@pytest.mark.parametrize("bad", [0, -2, True, "4"])
def test_a_non_positive_serving_count_is_refused_and_nothing_changes(bad):
    _recipe()
    with pytest.raises(ValueError, match="at least 1"):
        tools.update_recipe_details("Weeknight Dal", default_servings=bad, prep_time_minutes=5)
    recipe = tools.get_recipe("Weeknight Dal")
    assert recipe["default_servings"] == 4
    assert recipe["prep_time_minutes"] != 5  # the whole save was refused


def test_a_positive_serving_count_still_saves_and_scales_from_it():
    _recipe()
    tools.update_recipe_details("Weeknight Dal", default_servings=2)
    assert tools.get_recipe("Weeknight Dal")["default_servings"] == 2
    assert tools.scale_recipe("Weeknight Dal", 4)["base_servings"] == 2


def test_fill_in_drops_a_models_zero_and_keeps_the_steps(monkeypatch):
    tools.add_recipe(
        "Plain Rice",
        ingredients=[{"item": "Rice", "qty": "1 cup", "category": "pantry"}],
        instructions=[],
        default_servings=4,
    )
    monkeypatch.setattr(agent, "generate_recipe_detail_llm", lambda recipe: {
        "instructions": ["Rinse the rice.", "Simmer 15 minutes."],
        "default_servings": 0,
    })
    recipe = agent.fill_in_recipe("Plain Rice")
    assert recipe["instructions"] == ["Rinse the rice.", "Simmer 15 minutes."]
    assert recipe["default_servings"] == 4
