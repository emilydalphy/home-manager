"""
Sweet, savory or both (Loop Board "Setup: ask if snacks should be sweet,
savory or both", Emily 2026-11-05).

The snack step asks it; Settings → Food → How you eat changes it; it is
meal_preferences.snack_taste ('both' until chosen, for every household
that finished setup before the question existed). Snack planning reads it
through everyday_snacks.eligible: a sweet-only or savory-only house keeps
only list snacks carrying its word, AFTER the allergy / diet checks, so a
preference never adds a snack someone can't eat. 'both' takes the two in
turn so the week is a mix.

The model is stubbed at agent.generate_weekly_plan_llm, as in
tests/test_everyday_snacks.py, whose helpers this reuses.
"""
from __future__ import annotations

import pytest

from app import tools
from app.tools import everyday_snacks, swap_in_place
from test_everyday_snacks import _monday, _plan, _snacks, _week


@pytest.fixture
def household(monkeypatch):
    tools.add_member("Emily")
    tools.add_member("Sam")
    tools.set_household_meal_preferences(snacks_per_day=2, mark_complete=False)
    tools.edit_preference("snack_dishes_per_week", 4)
    monkeypatch.setattr(swap_in_place, "_pick_replacement", lambda ctx: {})


def _taste_of(name: str) -> list[str]:
    for s in everyday_snacks.load_list():
        for v in (s, s["swap"]):
            if v and v["name"] == name:
                return v["taste"]
    raise AssertionError(f"{name} is not on the list")


def test_every_list_snack_says_sweet_or_savory():
    for s in everyday_snacks.load_list():
        assert s["taste"] and set(s["taste"]) <= {"sweet", "savory"}, s["name"]
        if s["swap"]:
            assert s["swap"]["taste"] == s["taste"], s["name"]
    # Enough of each kind for a week of different snacks at either answer.
    sweet = [s for s in everyday_snacks.load_list() if "sweet" in s["taste"]]
    assert len(sweet) >= 7


def test_unset_reads_both_and_a_bad_word_is_refused(household):
    assert tools.get_household_memory()["snack_taste"] == "both"
    tools.edit_preference("snack_taste", "Savoury")
    assert tools.get_household_memory()["snack_taste"] == "savory"
    with pytest.raises(ValueError):
        tools.edit_preference("snack_taste", "spicy")
    assert tools.get_household_memory()["snack_taste"] == "savory"


def test_a_sweet_house_is_planned_only_sweet_snacks(household, monkeypatch):
    """The model sends savory list snacks; every one is replaced by a sweet one."""
    tools.edit_preference("snack_taste", "sweet")
    shortlist = everyday_snacks.prompt_context(tools.get_household_memory(), _monday())
    assert shortlist["taste"] == "sweet"
    assert all("sweet" in _taste_of(s["name"]) for s in shortlist["pick_from"]), shortlist
    savory = ["Roasted Chickpeas", "Edamame with Sea Salt", "Mini Quiche Cups", "Elote Cups"]
    names = {m["meal"] for m in _snacks(_plan(monkeypatch, _monday(), _week(_monday(), snacks=savory)))}
    assert len(names) == 4, names
    assert all("sweet" in _taste_of(n) for n in names), names


def test_a_savory_house_with_a_nut_allergy_never_gets_a_nut(household, monkeypatch):
    """Allergies come first: the preference only narrows what is left."""
    tools.edit_preference("snack_taste", "savory")
    tools.set_member_dietary_restrictions("Sam", ["tree nut allergy", "peanut allergy"])
    memory = tools.get_household_memory()
    allowed = everyday_snacks.eligible(memory)
    assert allowed and all("savory" in v["taste"] for v in allowed)
    assert not any({"nuts", "peanuts"} & set(v["allergens"]) for v in allowed), allowed
    sweet = ["Chia Pudding with Mango", "Overnight Oats Jars with Fruit",
             "Frozen Yogurt Bark with Berries", "Dates Stuffed with Peanut Butter"]
    names = {m["meal"] for m in _snacks(_plan(monkeypatch, _monday(), _week(_monday(), snacks=sweet)))}
    assert all("savory" in _taste_of(n) for n in names), names
    assert not any("peanut" in n.lower() or "almond" in n.lower() for n in names), names


def test_both_is_a_mix(household, monkeypatch):
    """Even when every savory snack is least recently used (so it would all
    sort first), the shortlist takes the two kinds in turn."""
    list_order = everyday_snacks.load_list()
    savory_first = {everyday_snacks._key(s["name"]): ("" if s["taste"] != ["sweet"] else "2099-01-01")
                    for s in list_order}
    monkeypatch.setattr(everyday_snacks, "last_used", lambda *a, **k: savory_first)
    picks = everyday_snacks.rotation(tools.get_household_memory(), _monday())
    kinds = ["sweet" if v["taste"] == ["sweet"] else "savory" for v in picks[:6]]
    assert kinds == ["savory", "sweet"] * 3, [v["name"] for v in picks[:6]]


def test_setup_and_settings_save_it(signed_in):
    res = signed_in.post("/api/onboarding/answers", json={
        "member_names": ["Emily"], "household_restrictions": {}, "eating_style": "",
        "wont_eat": [], "excited_about": [], "snacks_per_day": 2, "snack_taste": "sweet",
    })
    assert res.status_code == 200, res.text
    assert signed_in.get("/api/memory").json()["snack_taste"] == "sweet"
    res = signed_in.post("/api/memory/edit", json={"field": "snack_taste", "value": "both"})
    assert res.status_code == 200, res.text
    assert res.json()["snack_taste"] == "both"
    assert signed_in.post("/api/memory/edit", json={"field": "snack_taste", "value": "crunchy"}).status_code == 400
