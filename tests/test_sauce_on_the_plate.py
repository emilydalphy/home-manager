"""
Every dinner Pomona writes has a sauce on the plate.

Emily, 2026-11-02, Tester Home: "Turkey meatball meal this week is dry with
no sauce." The writer's SOMETHING WET rule was there; the recipe met it on
paper with 1/3 cup of broth that the covered meatballs and zucchini soaked
up. Now the writer names the sauce on the plate and how much each person
gets (on_the_plate), and a dinner without at least 2 tbsp a person is
rewritten once.
"""
from __future__ import annotations

import pytest

from app import agent, tools

from test_menu_first_generation import household  # noqa: F401  (fixture)


# ---------- the check ----------

@pytest.mark.parametrize("text", [
    "the marinara they simmer in, about 1/2 cup each",
    "garlic yogurt sauce, 3 tbsp each",
    "salsa verde, 2 tablespoons each",
    "the broth, 1 1/2 cups a bowl",
    "½ cup tomato sauce each",
    "60 ml dressing each",
    "marinara, a 1/4-cup ladle each",
    "4 oz of the sauce each",
])
def test_a_named_sauce_with_enough_of_it_passes(text):
    assert agent._sauce_on_the_plate_problem(text) is None


@pytest.mark.parametrize("text", [
    None, "", "none", "None — it's a dry dish",
    "a few spoonfuls of the broth",       # the meatballs' case: no measure
    "pan juices, 1 tbsp each",            # a film, not a sauce
    "lemon squeezed over, 1 tsp each",
])
def test_no_sauce_or_too_little_is_a_problem(text):
    assert agent._sauce_on_the_plate_problem(text)


def test_the_check_never_raises_on_odd_input():
    for odd in (12, ["sauce"], {"a": 1}, "1/0 cup", "cups"):
        agent._sauce_on_the_plate_problem(odd)


# ---------- the writer is told ----------

def test_the_writer_is_asked_for_it():
    props = agent._RECIPE_DETAILS_TOOL["input_schema"]["properties"]
    assert "on_the_plate" in props
    assert "soaks up" in props["on_the_plate"]["description"]


def test_the_rule_says_a_splash_that_is_soaked_up_does_not_count():
    rule = agent.RECIPE_DETAILS_INSTRUCTIONS
    assert "at least 2 tablespoons" in rule
    assert "soaks up does not" in rule
    assert "Lean ground turkey or chicken" in rule


# ---------- the retry ----------

_DRY = {
    "ingredients": [{"item": "Ground turkey", "qty": "0.75 lb", "category": "meat/seafood"},
                    {"item": "Zucchini", "qty": "2", "category": "produce"},
                    {"item": "Vegetable broth", "qty": "1/3 cup", "category": "pantry"}],
    "instructions": ["Brown the 0.75 lb turkey meatballs.", "Add the zucchini and the 1/3 cup broth, cover, simmer."],
    "default_servings": 2,
    "on_the_plate": "a few spoonfuls of the broth",
}
_SAUCED = {
    **_DRY,
    "ingredients": _DRY["ingredients"] + [{"item": "Marinara sauce", "qty": "1 jar", "category": "pantry"}],
    "instructions": ["Brown the 0.75 lb turkey meatballs.", "Add the zucchini and the marinara, cover, simmer."],
    "on_the_plate": "the marinara they simmer in, about 1/2 cup each",
}


def _pending(name, slot):
    tools.add_recipe(name=name, ingredients=[], details_pending=True, dish_note="weeknight")
    return {**tools.get_recipe(name), "slot": slot, "cook_time_minutes": 25}


def _writer(monkeypatch, *answers):
    specs = []
    queue = list(answers)

    def _fake(spec):
        specs.append(spec)
        return dict(queue.pop(0) if len(queue) > 1 else queue[0])

    monkeypatch.setattr(agent, "generate_recipe_details_llm", _fake)
    return specs


def test_a_dry_dinner_is_rewritten_once_with_the_problem_named(household, monkeypatch):
    specs = _writer(monkeypatch, _DRY, _SAUCED)
    out = agent._write_one_pending_recipe(
        _pending("Turkey Meatballs", "dinner"), "dinner", {"serves": 2, "must_not_contain": []}, [])
    assert out.get("ok") is not False
    assert len(specs) == 2, "one rewrite"
    assert "no real sauce" in specs[1]["note"]
    assert "a few spoonfuls" in specs[1]["note"]
    saved = tools.get_recipe("Turkey Meatballs")
    assert any(i["item"] == "Marinara sauce" for i in saved["ingredients"])


def test_a_second_dry_answer_is_kept_not_lost(household, monkeypatch, caplog):
    """A slightly dull dinner beats no dinner; it is logged instead."""
    specs = _writer(monkeypatch, _DRY, _DRY)
    caplog.set_level("WARNING")
    agent._write_one_pending_recipe(
        _pending("Turkey Meatballs", "dinner"), "dinner", {"serves": 2, "must_not_contain": []}, [])
    assert len(specs) == 2
    assert tools.get_recipe("Turkey Meatballs")["instructions"]
    assert "sauce_on_the_plate" in caplog.text and "keeping it" in caplog.text


def test_a_sauced_dinner_is_written_once(household, monkeypatch):
    specs = _writer(monkeypatch, _SAUCED)
    agent._write_one_pending_recipe(
        _pending("Turkey Meatballs", "dinner"), "dinner", {"serves": 2, "must_not_contain": []}, [])
    assert len(specs) == 1


def test_breakfast_and_lunch_are_not_held_to_it(household, monkeypatch):
    for slot in ("breakfast", "lunch"):
        specs = _writer(monkeypatch, {**_DRY, "on_the_plate": ""})
        agent._write_one_pending_recipe(
            _pending(f"Dish {slot}", slot), slot, {"serves": 2, "must_not_contain": []}, [])
        assert len(specs) == 1, slot


def test_a_failed_rewrite_keeps_the_first_answer(household, monkeypatch):
    """The first, dry answer passed the allergen gate; a rewrite that comes
    back empty must not leave the dish pending instead."""
    specs = _writer(monkeypatch, _DRY, {})
    out = agent._write_one_pending_recipe(
        _pending("Turkey Meatballs", "dinner"), "dinner", {"serves": 2, "must_not_contain": []}, [])
    assert len(specs) == 2
    assert out.get("ok") is not False
    assert tools.get_recipe("Turkey Meatballs")["instructions"]


def test_a_rewrite_that_raises_keeps_the_first_answer(household, monkeypatch):
    calls = []

    def _fake(spec):
        calls.append(spec)
        if len(calls) == 2:
            raise RuntimeError("the API fell over")
        return dict(_DRY)

    monkeypatch.setattr(agent, "generate_recipe_details_llm", _fake)
    out = agent._write_one_pending_recipe(
        _pending("Turkey Meatballs", "dinner"), "dinner", {"serves": 2, "must_not_contain": []}, [])
    assert len(calls) == 2
    assert out.get("ok") is not False
    assert tools.get_recipe("Turkey Meatballs")["instructions"]
