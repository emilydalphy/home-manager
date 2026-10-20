"""No door saves a recipe as serving 0 or fewer people.

Overnight hunt, 2026-10-20 (follow-on to overnight/recipe-serves-at-least-one,
which guards update_recipe_details and fill_in_recipe). add_recipe kept a
negative (`default_servings or 4` only catches 0), fill_recipe_details and
the Tweak sheet's rewrite wrote `int(x or 4)`, so a model's -2 was saved and
the recipe card said -2 while scale_recipe treated it as 4. Where the count
is the model's, a bad one is dropped for the usual default; where the chat
names it on purpose, it is refused in update_recipe_details' words.
"""
from __future__ import annotations

import pytest

from app import agent, tools

from test_recipe_change import TUE, _entry, _plan, _recipe


@pytest.mark.parametrize("bad", [-2, 0, "lots", 2.5])
def test_the_chat_refuses_a_recipe_serving_fewer_than_one(bad):
    with pytest.raises(ValueError, match="A recipe has to serve at least 1"):
        agent.TOOL_FUNCTIONS["add_recipe"](
            name="Weeknight Dal",
            ingredients=[{"item": "Red lentils", "qty": "1 cup", "category": "pantry"}],
            instructions=["Simmer the lentils."],
            default_servings=bad,
        )
    assert tools.existing_recipe_named("Weeknight Dal") is None


def test_the_chat_still_saves_a_real_count_even_as_text():
    agent.TOOL_FUNCTIONS["add_recipe"](
        name="Weeknight Dal",
        ingredients=[{"item": "Red lentils", "qty": "1 cup", "category": "pantry"}],
        default_servings="6",
    )
    assert tools.get_recipe("Weeknight Dal")["default_servings"] == 6


def test_a_models_negative_count_on_a_week_save_keeps_the_default():
    """The week, a swap and a big meal all hand add_recipe the model's count."""
    tools.add_recipe("Rice Bowl", ingredients=[{"item": "Rice", "qty": "1 cup"}], default_servings=-2)
    assert tools.get_recipe("Rice Bowl")["default_servings"] == 4
    assert tools.scale_recipe("Rice Bowl", 4)["base_servings"] == 4


def test_the_recipe_pass_drops_a_models_negative_for_the_households_table(monkeypatch):
    tools.add_recipe("Lentil Soup", ingredients=[], details_pending=True, dish_note="lemony")
    monkeypatch.setattr(agent, "generate_recipe_details_llm", lambda spec: {
        "ingredients": [{"item": "Red lentils", "qty": "1 cup", "category": "pantry"}],
        "instructions": ["Simmer the lentils.", "Finish with lemon."],
        "default_servings": -3,
    })
    out = agent._write_one_pending_recipe(tools.get_recipe("Lentil Soup"), "dinner", {"serves": 3}, [],
                                          research=False)
    assert out["ok"], out
    assert tools.get_recipe("Lentil Soup")["default_servings"] == 3


def test_fill_recipe_details_never_saves_a_negative():
    tools.add_recipe("Stub", ingredients=[], details_pending=True, dish_note="roast it")
    back = tools.fill_recipe_details("Stub", ingredients=[{"item": "Carrots", "qty": "4"}],
                                     instructions=["Roast."], default_servings=-1)
    assert back["default_servings"] == 4


def test_a_rewrite_with_a_negative_count_keeps_the_recipes_own(signed_in):
    plan = _plan("draft")
    _recipe("Chana Masala", [("Chickpeas", "2 cans")], default_servings=3)
    entry = _entry(plan, TUE, "Chana Masala")
    spec = tools.rewrite_spec(entry)
    res = tools.apply_rewrite(spec, {
        "ingredients": [{"item": "Dried chickpeas", "qty": "250 g"}],
        "instructions": ["Soak them.", "Simmer."],
        "default_servings": -2,
    }, "Use dried chickpeas")
    assert res["status"] == "rewritten", res
    assert tools.get_recipe("Chana Masala")["default_servings"] == 3
