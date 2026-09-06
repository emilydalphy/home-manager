"""
Loop Board (Emily, 2026-09-04, High): "I should be able to see all the
onboarding information here so the user can edit it if they need to update
it." Before this, What We Know's People/Taste/Rhythm tabs each showed only
a partial slice of what onboarding actually collects — see
test_what_we_know_taste.py for the eating_style/cuisines/leftovers_stance
half of this same gap, closed earlier.

This file covers the rest of it:
  - People: age_group (never editable anywhere; onboarding.html's own UI
    never even asks for it) and dietary restrictions add/remove, via the
    two new routes this ticket adds.
  - Taste: protein_preferences, dislikes, the four per-week recipe counts,
    and kitchen_kit now ride along in /api/facts?category=taste's
    preferences block — all through the pre-existing /api/memory/edit and
    /api/memory/delete routes, so no new route was needed for any of them.
  - Rhythm: lunch_location/meals_together/cooking_role/dinner_window/
    planning_anchor now ride along in /api/facts?category=rhythm's
    preferences block, all writable through the existing
    /api/onboarding/rhythm route (already used for leftovers_stance) since
    it happily accepts a partial body — only the fields present get
    written.
  - A payload-completeness test enumerating every field the ticket's table
    asks for, so a future refactor that silently drops one fails loudly
    here rather than being noticed only by a user staring at a blank row.
"""
from __future__ import annotations

import pytest

from app import tools


@pytest.fixture
def household_with_members():
    tools.add_member("Priya")
    tools.add_member("Sam")


# ---------- People: age_group ----------

def test_age_group_is_unset_by_default(signed_in, household_with_members):
    people = signed_in.get("/api/facts?category=people").json()
    members = {m["name"]: m for m in people["onboarding"]["members"]}
    assert members["Priya"]["age_group"] == ""
    assert members["Sam"]["age_group"] == ""


def test_setting_a_members_age_group_round_trips(signed_in, household_with_members):
    res = signed_in.post("/api/memory/member/age-group", json={"name": "Priya", "age_group": "adult"})
    assert res.status_code == 200

    assert tools.get_household_memory()["members"] == [
        {"name": "Priya", "age_group": "adult", "dietary_restrictions": []},
        {"name": "Sam", "age_group": "", "dietary_restrictions": []},
    ]
    people = signed_in.get("/api/facts?category=people").json()
    members = {m["name"]: m for m in people["onboarding"]["members"]}
    assert members["Priya"]["age_group"] == "adult"


# ---------- People: dietary restrictions add/remove ----------

def test_adding_a_restriction_merges_with_replace_false(signed_in, household_with_members):
    signed_in.post(
        "/api/memory/member/restrictions",
        json={"name": "Priya", "restrictions": ["peanut allergy"], "replace": False},
    )
    res = signed_in.post(
        "/api/memory/member/restrictions",
        json={"name": "Priya", "restrictions": ["vegetarian"], "replace": False},
    )
    assert res.status_code == 200
    restrictions = tools.list_members()
    priya = next(m for m in restrictions if m["name"] == "Priya")
    assert sorted(priya["dietary_restrictions"]) == ["peanut allergy", "vegetarian"]


def test_removing_a_restriction_uses_replace_true_with_the_filtered_list(signed_in, household_with_members):
    signed_in.post(
        "/api/memory/member/restrictions",
        json={"name": "Priya", "restrictions": ["peanut allergy", "vegetarian"], "replace": True},
    )
    # Removing one is the client resending the full remaining list under
    # replace=True — the exact mechanism MemberRestrictionsRequest documents.
    res = signed_in.post(
        "/api/memory/member/restrictions",
        json={"name": "Priya", "restrictions": ["vegetarian"], "replace": True},
    )
    assert res.status_code == 200
    priya = next(m for m in tools.list_members() if m["name"] == "Priya")
    assert priya["dietary_restrictions"] == ["vegetarian"]

    people = signed_in.get("/api/facts?category=people").json()
    members = {m["name"]: m for m in people["onboarding"]["members"]}
    assert members["Priya"]["dietary_restrictions"] == ["vegetarian"]


# ---------- Taste: protein preferences, dislikes, counts, kitchen kit ----------

def test_taste_tab_carries_protein_dislikes_counts_and_kit(signed_in):
    signed_in.post("/api/memory/edit", json={"field": "protein_preferences", "value": {"chicken": 5, "tofu": 2}})
    signed_in.post("/api/memory/edit", json={"field": "dislikes", "value": ["olives", "cilantro"]})
    signed_in.post("/api/memory/edit", json={"field": "dinners_per_week", "value": 4})
    signed_in.post("/api/memory/edit", json={"field": "breakfasts_per_week", "value": 3})
    signed_in.post("/api/memory/edit", json={"field": "lunches_per_week", "value": 2})
    signed_in.post("/api/memory/edit", json={"field": "snacks_per_week", "value": 1})
    signed_in.post("/api/memory/edit", json={"field": "kitchen_kit", "value": ["air_fryer", "slow_cooker"]})

    prefs = signed_in.get("/api/facts?category=taste").json()["preferences"]
    assert prefs["protein_preferences"] == {"chicken": 5, "tofu": 2}
    assert prefs["dislikes"] == ["olives", "cilantro"]
    assert prefs["dinners_per_week"] == 4
    assert prefs["breakfasts_per_week"] == 3
    assert prefs["lunches_per_week"] == 2
    assert prefs["snacks_per_week"] == 1
    assert prefs["kitchen_kit"] == ["air_fryer", "slow_cooker"]


def test_removing_a_protein_preference_and_a_dislike(signed_in):
    signed_in.post("/api/memory/edit", json={"field": "protein_preferences", "value": {"chicken": 5, "tofu": 2}})
    signed_in.post("/api/memory/edit", json={"field": "dislikes", "value": ["olives", "cilantro"]})

    signed_in.post("/api/memory/delete", json={"field": "protein_preferences", "item": "tofu"})
    signed_in.post("/api/memory/delete", json={"field": "dislikes", "item": "olives"})

    prefs = signed_in.get("/api/facts?category=taste").json()["preferences"]
    assert prefs["protein_preferences"] == {"chicken": 5}
    assert prefs["dislikes"] == ["cilantro"]


# ---------- Rhythm: lunch location / meals together / cooking role / dinner window / planning anchor ----------

def test_rhythm_tab_carries_the_full_six_and_member_names(signed_in, household_with_members):
    signed_in.post("/api/onboarding/rhythm", json={"lunch_location": {"Priya": "home", "Sam": "out"}})
    signed_in.post("/api/onboarding/rhythm", json={"meals_together": "most_meals"})
    signed_in.post("/api/onboarding/rhythm", json={"cooking_role": "one_person", "cooking_role_who": "Priya"})
    signed_in.post("/api/onboarding/rhythm", json={"dinner_window": "6_8"})
    signed_in.post("/api/onboarding/rhythm", json={"planning_anchor": "friday"})
    signed_in.post("/api/onboarding/rhythm", json={"leftovers_stance": "love_them"})

    prefs = signed_in.get("/api/facts?category=rhythm").json()["preferences"]
    assert prefs["lunch_location"]["Priya"]["standing"] == "home"
    assert prefs["lunch_location"]["Sam"]["standing"] == "out"
    assert prefs["meals_together"] == "most_meals"
    assert prefs["cooking_role"] == {"value": "one_person", "who": "Priya"}
    assert prefs["dinner_window"] == "6_8"
    assert prefs["planning_anchor"] == "friday"
    assert prefs["planning_anchor_label"] == "Ready on Friday"
    assert prefs["leftovers_stance"] == "love_them"
    assert set(prefs["members"]) == {"Priya", "Sam"}


def test_onboarding_rhythm_route_accepts_a_partial_body_for_just_one_field(signed_in, household_with_members):
    """
    What We Know's Rhythm tab edits one fact at a time (tap dinner window,
    save, move on) rather than resending all six — this is the behavior
    that makes reusing /api/onboarding/rhythm for corrections safe instead
    of accidentally clobbering the other five answers with their empty
    defaults.
    """
    signed_in.post("/api/onboarding/rhythm", json={"dinner_window": "later"})
    signed_in.post("/api/onboarding/rhythm", json={"planning_anchor": "monday"})

    rhythm = tools.get_household_rhythm()
    assert rhythm["dinner_window"] == "later"
    assert rhythm["planning_anchor"] == "monday"
    # Neither call mentioned meals_together/cooking_role — they must stay unset.
    assert rhythm["meals_together"] is None
    assert rhythm["cooking_role"] is None


# ---------- Full payload enumeration ----------

def test_the_full_ticket_field_list_is_present_on_every_tab(signed_in, household_with_members):
    """
    Enumerates every field the ticket's table asked for, across all three
    tabs, in one place — so a future refactor that silently drops one of
    them fails a test instead of only being noticed by someone staring at
    a blank row in the app.
    """
    people = signed_in.get("/api/facts?category=people").json()
    member = people["onboarding"]["members"][0]
    for key in ("name", "age_group", "dietary_restrictions"):
        assert key in member, f"People tab member is missing {key!r}"

    taste_prefs = signed_in.get("/api/facts?category=taste").json()["preferences"]
    for key in (
        "eating_style", "cuisines", "complete_plates", "protein_preferences",
        "dislikes", "dinners_per_week", "breakfasts_per_week", "lunches_per_week",
        "snacks_per_week", "kitchen_kit",
    ):
        assert key in taste_prefs, f"Taste tab preferences is missing {key!r}"

    rhythm_prefs = signed_in.get("/api/facts?category=rhythm").json()["preferences"]
    for key in (
        "lunch_location", "meals_together", "cooking_role", "dinner_window",
        "planning_anchor", "planning_anchor_label", "leftovers_stance", "members",
    ):
        assert key in rhythm_prefs, f"Rhythm tab preferences is missing {key!r}"


def test_get_household_memory_exposes_everything_the_tabs_read():
    """
    get_household_memory (app/tools/memory.py) is the underlying source for
    every one of these fields except the six rhythm facts (which live in
    get_household_rhythm instead, nested under memory['rhythm']) — this
    just pins that contract directly against the tool function, independent
    of the HTTP layer above.
    """
    memory = tools.get_household_memory()
    for key in (
        "members", "eating_style", "cuisine_preferences", "complete_plates",
        "protein_preferences", "dislikes", "dinners_per_week", "breakfasts_per_week",
        "lunches_per_week", "snacks_per_week", "kitchen_kit", "rhythm",
    ):
        assert key in memory, f"get_household_memory is missing {key!r}"
    for key in (
        "lunch_location", "meals_together", "cooking_role", "dinner_window",
        "planning_anchor", "planning_anchor_label", "leftovers_stance",
    ):
        assert key in memory["rhythm"], f"get_household_memory()['rhythm'] is missing {key!r}"
