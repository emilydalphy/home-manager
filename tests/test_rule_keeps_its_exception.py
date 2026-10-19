"""
A rule with an exception keeps its exception (Loop Board, "As a new
household, my 'don't suggest cauliflower unless it's cauliflower rice' reads
back exactly that, not 'Never suggest Cauliflower'").

Emily typed "don't suggest any cauliflower unless it's cauliflower rice"
into setup's "Anything else?". The confirm card said "Never recommend
Cauliflower": the reading dropped the exception, the model's capital C came
through mid-sentence, and the plain "cauliflower" it would have saved keeps
cauliflower rice off every plan.

Underneath, the shared matcher had the opposite bug: "No cauliflower unless
cauliflower rice" subtracted the exception's WORDS from the rule, which took
"cauliflower" away and left nothing, so roasted cauliflower got through too.
An exception that is a narrower form of the food ("cauliflower rice" of
"cauliflower") is now a carve-out: the food is still blocked everywhere
except inside the exception's own words.

Stacked on overnight/excluded-options-filtered (the option gate these
matcher tests reach through).
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

from app import tools
from app.tools import coordination
from test_onboarding_anything_else import _stub_model

_HERE = Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location("test_onboarding_anything_else", _HERE / "test_onboarding_anything_else.py")
_ae = sys.modules.get("test_onboarding_anything_else")
if _ae is None:
    _ae = importlib.util.module_from_spec(_spec)
    sys.modules["test_onboarding_anything_else"] = _ae
    _spec.loader.exec_module(_ae)

NOTE = "don't suggest any cauliflower unless it's cauliflower rice"


def _hits(name: str, ingredients=None) -> list[dict]:
    return coordination.check_meal_conflicts(name, ingredients=ingredients or [])


# ---------- the matcher: both directions ----------

@pytest.mark.parametrize("rule", [
    "No cauliflower unless cauliflower rice",
    "No cauliflower unless it's cauliflower rice",
    "No cauliflower except cauliflower rice",
    "No cauliflower, only if it's cauliflower rice",
])
def test_a_hard_rule_blocks_the_food_and_lets_its_exception_through(rule):
    tools.add_fact("people", rule, hard=True)
    assert _hits("Roasted Cauliflower"), "the exception cancelled the whole rule"
    assert _hits("Chicken Traybake", [{"item": "cauliflower florets"}])
    assert _hits("Cauliflower Rice Stir-Fry") == []
    assert _hits("Burrito Bowl", [{"item": "1 bag cauliflower rice"}]) == []


def test_a_won_t_eat_entry_with_its_exception_blocks_and_allows_the_same_way():
    # What setup now saves for Emily's sentence.
    tools.add_food_dislikes(["cauliflower, except cauliflower rice"])
    hit = _hits("Roasted Cauliflower")
    assert hit and hit[0]["source"] == "dislike"
    # Said as the food, so the banner and the chat's "make it cauliflower" read it.
    assert hit[0]["restriction"] == "cauliflower"
    assert _hits("Cauliflower Rice Burrito Bowl") == []


def test_a_typed_won_t_eat_with_unless_is_read_the_same_way():
    tools.add_food_dislikes(["cauliflower unless it's cauliflower rice"])
    assert _hits("Cauliflower Cheese")
    assert _hits("Cauliflower Rice") == []


def test_an_exception_that_is_a_different_food_still_subtracts_as_before():
    # "but peanuts are fine" is not a narrower nut: the old subtraction stands.
    tools.add_fact("people", "Allergic to tree nuts but peanuts are fine", hard=True)
    assert _hits("Walnut Brownies")
    assert _hits("Peanut Noodles") == []


# ---------- the reading: what comes back and what is saved ----------

def test_the_reading_keeps_the_models_exception_in_the_households_spelling(signed_in, monkeypatch):
    _stub_model(monkeypatch, {"wont_eat": [{"food": "Cauliflower", "except": "Cauliflower rice"}]})
    body = signed_in.post("/api/onboarding/read-note", json={"note": NOTE}).json()
    assert body["reading"]["wont_eat"] == ["cauliflower, except cauliflower rice"]


def test_an_exception_the_model_dropped_is_put_back_from_the_note(signed_in, monkeypatch):
    # Enforced in code: the model's plain "Cauliflower" loses the exception
    # the household typed, so it is read back out of their own words.
    _stub_model(monkeypatch, {"wont_eat": ["Cauliflower"]})
    body = signed_in.post("/api/onboarding/read-note", json={"note": NOTE}).json()
    assert body["reading"]["wont_eat"] == ["cauliflower, except cauliflower rice"]


@pytest.mark.parametrize("note,food,expect", [
    ("No fish except salmon please.", "Fish", "fish, except salmon"),
    ("olives only if they're kalamata", "olives", "olives, except kalamata"),
    ("no mushrooms but shiitake is fine", "mushrooms", "mushrooms, except shiitake"),
    # "but" without "fine" is not an exception, and a condition that isn't a food isn't one.
    ("no mushrooms but we love peppers", "mushrooms", "mushrooms"),
    ("no cauliflower unless I ask for it", "cauliflower", "cauliflower"),
    ("no olives, ever", "Olives", "olives"),
])
def test_the_exception_is_read_from_the_note_only_when_it_is_one(note, food, expect):
    from app import main
    assert main._wont_eat_as_said({"wont_eat": [food]}, note)["wont_eat"] == [expect]


def test_a_name_the_household_capitalised_mid_sentence_keeps_its_capital():
    from app import main
    assert main._wont_eat_as_said({"wont_eat": ["doritos"]}, "no Doritos in the lunchbox")["wont_eat"] == ["Doritos"]


def test_what_is_saved_is_the_rule_with_its_exception(signed_in, monkeypatch):
    # The same answers payload "Looks right" sends: the dislike stored is the
    # whole rule, so the planner reads it and the matcher keeps cauliflower rice.
    res = signed_in.post("/api/onboarding/answers", json={
        "member_names": ["Emily"], "wont_eat": ["cauliflower, except cauliflower rice"], "notes": NOTE,
    })
    assert res.status_code == 200, res.text
    assert tools.get_household_memory()["dislikes"] == ["cauliflower, except cauliflower rice"]
    assert _hits("Roasted Cauliflower")
    assert _hits("Cauliflower Rice Bowl") == []


# ---------- the confirm card ----------

@_ae._needs_node
def test_the_confirm_card_reads_the_rule_back_with_its_exception():
    reading = json.dumps({"wont_eat": ["cauliflower, except cauliflower rice", "olives"]})
    out = _ae._run(_ae._harness(reading=reading, note=NOTE, seed="""
anythingElseStage = 'confirm';
buildAnythingElseStep();
const lines = state().lines;
(async function () {
  await runAnythingElseNext();
  console.log(JSON.stringify({ lines: lines, wontEat: state().wontEat }));
})();
"""))
    assert out["lines"] == ["No cauliflower, except cauliflower rice", "Never recommend olives"]
    assert out["wontEat"] == ["cauliflower, except cauliflower rice", "olives"]


def test_the_prompt_asks_for_the_exception_as_its_own_field(signed_in, monkeypatch):
    calls = []
    _stub_model(monkeypatch, {}, record=calls)
    signed_in.post("/api/onboarding/read-note", json={"note": NOTE})
    item = calls[0]["tools"][0]["input_schema"]["properties"]["wont_eat"]["items"]
    assert item["properties"]["except"] and item["required"] == ["food"]
    assert "unless" in calls[0]["messages"][0]["content"]


# ---------- review round, 2026-10-19 ----------

@pytest.mark.parametrize("rule,dish", [
    ("Avoid dairy only when baked, and eggs", "Frittata"),
    ("No shellfish only when Maya is here, or peanuts", "Peanut Noodles"),
])
def test_an_exception_never_swallows_the_rest_of_a_hard_list(rule, dish):
    tools.add_fact("people", rule, hard=True)
    assert _hits(dish, [{"item": "6 eggs"}, {"item": "peanuts"}]), "the exception ate the next avoidance"


def test_an_exception_that_names_both_still_excepts_both():
    tools.add_fact("people", "Allergic to tree nuts but peanuts and almonds are fine", hard=True)
    assert _hits("Walnut Brownies")
    assert _hits("Peanut Noodles") == [] and _hits("Almond Cake") == []


def test_homemade_cauliflower_rice_is_the_exception_not_a_clash():
    tools.add_food_dislikes(["cauliflower, except cauliflower rice"])
    assert _hits("Cauliflower Rice Bowl", [{"item": "1 head cauliflower"}]) == []
    assert _hits("Roasted Veg Bowl", [{"item": "1 head cauliflower"}])


def test_an_allergy_is_still_judged_on_what_goes_in_even_under_its_exceptions_name():
    tools.add_fact("people", "Allergic to peanuts except peanut oil", hard=True)
    assert _hits("Peanut Oil Stir-Fry", [{"item": "2 tbsp peanut butter"}])


def test_forgetting_the_food_removes_the_rule_with_its_exception_and_says_so_honestly():
    tools.add_food_dislikes(["cauliflower, except cauliflower rice", "olives"])
    assert tools.delete_preference("dislikes", "cauliflower")["deleted"] is True
    assert tools.get_household_memory()["dislikes"] == ["olives"]
    assert tools.delete_preference("dislikes", "anchovies")["deleted"] is False
