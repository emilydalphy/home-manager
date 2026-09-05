"""
Eating style and cuisines have to be editable somewhere in the app.

Both are collected during onboarding and both drive every week the app
plans — and until this, neither was editable anywhere: not on the What We
Know screen, not on the meal-setup screen, not anywhere in the UI at all.
The only way to change them was to mention it in chat, and only if you
thought to. A household whose eating changed had no way to say so on the
screen literally named "What we know".

The write path already existed (`/api/memory/edit` and `/api/memory/delete`
both accept these fields); what was missing was that the screen never
*read* them, so there was nothing to edit. These tests cover the read side
— that the Taste tab is handed the current values — plus the round trip,
since a form that saves to nowhere looks identical to one that works.

Emily's call (2026-09-02): these live in What We Know, next to the
restrictions and the won't-eat list, because they are facts about the
household's taste. The per-week meal counts stay on the meal-setup screen,
because those are a planning setting rather than a fact — and duplicating
them would create two places to change one number.
"""
from __future__ import annotations

import pytest

from app import tools


@pytest.fixture
def household_with_taste():
    tools.save_onboarding_answers(
        member_names=["Emily"],
        household_restrictions={},
        eating_style="high-protein, low-carb",
        wont_eat=["olives"],
        excited_about=["Italian", "Thai"],
        dinners_per_week=5,
        breakfasts_per_week=7,
        lunches_per_week=7,
    )


def test_the_taste_tab_is_given_the_values_it_needs_to_show(signed_in, household_with_taste):
    """
    The read side, which is the half that was missing. Without this the
    screen has nothing to render and there is nothing to edit.
    """
    body = signed_in.get("/api/facts?category=taste").json()

    assert body.get("preferences") is not None, "the Taste tab was handed no preferences at all"
    assert body["preferences"]["eating_style"] == "high-protein, low-carb"
    assert body["preferences"]["cuisines"] == ["Italian", "Thai"]


def test_the_other_tabs_are_unaffected(signed_in, household_with_taste):
    """
    The People tab has its own extra block and must keep it; Stores gets
    neither. Rhythm has grown its own preferences card since (Loop Board
    "Onboarding asks about leftovers twice", 2026-09-05 — see
    test_leftovers_and_snacks.py), but it must be RHYTHM's own shape
    (leftovers_stance), not the Taste tab's eating_style/cuisines card
    leaking across tabs — that would be the obvious way to get this wrong.
    """
    people = signed_in.get("/api/facts?category=people").json()
    assert people["onboarding"] is not None, "People lost its onboarding block"
    assert people.get("preferences") is None, "People should not get the taste card"

    rhythm = signed_in.get("/api/facts?category=rhythm").json()
    assert set(rhythm.get("preferences") or {}) == {"leftovers_stance"}, \
        "rhythm should get its own leftovers card, not the taste card"
    assert rhythm.get("onboarding") is None, "rhythm should not get the onboarding block"

    stores = signed_in.get("/api/facts?category=stores").json()
    assert stores.get("preferences") is None, "stores should not get any preferences card"
    assert stores.get("onboarding") is None, "stores should not get the onboarding block"


def test_editing_the_eating_style_reaches_the_planner(signed_in, household_with_taste):
    """
    Round trip through the same endpoint the screen calls. `get_household_memory`
    is what the week generator reads, so asserting there rather than on the
    response proves the edit actually lands where planning will see it.
    """
    res = signed_in.post(
        "/api/memory/edit",
        json={"field": "eating_style", "value": "mostly vegetarian, high fibre"},
    )
    assert res.status_code == 200

    assert tools.get_household_memory()["eating_style"] == "mostly vegetarian, high fibre"
    assert signed_in.get("/api/facts?category=taste").json()["preferences"]["eating_style"] == (
        "mostly vegetarian, high fibre"
    )


def test_clearing_the_eating_style_is_a_real_answer(signed_in, household_with_taste):
    """
    Unlike an abandoned freeform fact, an empty eating style means "I don't
    follow one" and must save rather than being treated as a cancelled edit.
    """
    assert signed_in.post("/api/memory/edit", json={"field": "eating_style", "value": ""}).status_code == 200
    assert tools.get_household_memory()["eating_style"] == ""


def test_adding_and_removing_a_cuisine(signed_in, household_with_taste):
    """
    Add replaces the whole list; remove takes one item. Both are what the
    screen's controls call, so both are covered.
    """
    add = signed_in.post(
        "/api/memory/edit",
        json={"field": "cuisine_preferences", "value": ["Italian", "Thai", "Sichuan"]},
    )
    assert add.status_code == 200
    assert tools.get_household_memory()["cuisine_preferences"] == ["Italian", "Thai", "Sichuan"]

    remove = signed_in.post(
        "/api/memory/delete", json={"field": "cuisine_preferences", "item": "Thai"}
    )
    assert remove.status_code == 200
    remaining = tools.get_household_memory()["cuisine_preferences"]
    assert "Thai" not in remaining
    assert remaining == ["Italian", "Sichuan"], (
        "removing one cuisine should leave the others exactly as they were"
    )


def test_a_household_that_answered_nothing_still_gets_a_usable_tab(signed_in):
    """
    No onboarding at all — the screen must offer empty fields to fill in
    rather than erroring or returning nothing to render.
    """
    body = signed_in.get("/api/facts?category=taste").json()
    assert body["preferences"] == {"eating_style": "", "cuisines": []}
