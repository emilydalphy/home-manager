"""
The rhythm save is all-or-nothing.

Defect hunt, 2026-09-13 ("Rhythm half-saves"): /api/onboarding/rhythm used
to call the individual rhythm setters (set_lunch_location,
set_meals_together, set_cooking_role, ...) one at a time, each opening its
own connection and committing on the spot. Every one of those setters
validates only its OWN value, so a bad field partway through a call (an
invalid cooking_role, a prep day naming a weekday that doesn't exist)
raised after the fields ahead of it in the request had already landed — a
rhythm answer half-saved with nothing on screen to say so.

tools.save_rhythm_answers (app/tools/rhythm.py) is the fix: every field in
one call is validated before any of them is written, and the writes
happen on one connection with one commit. These tests fail on `main`
because save_rhythm_answers does not exist there at all — the route still
calls the individual setters in sequence.
"""
from __future__ import annotations

import pytest

from app import tools


def _adult(name: str) -> int:
    member_id = tools.add_member(name)["member_id"]
    tools.set_member_age_group(name, "Adult")
    return member_id


def test_an_invalid_field_saves_nothing_at_all():
    """
    The field order below matters: meals_together is valid and would have
    landed under the old one-setter-at-a-time route before cooking_role's
    bad value raised. With save_rhythm_answers, neither one does.
    """
    with pytest.raises(ValueError, match="not_a_real_role"):
        tools.save_rhythm_answers(
            meals_together="most_meals",
            cooking_role="not_a_real_role",
        )
    rhythm = tools.get_household_rhythm()
    assert rhythm["meals_together"] is None, "an earlier valid field must not have been written"
    assert rhythm["cooking_role"] is None


def test_a_bad_prep_day_saves_nothing_either():
    """
    prep_days is validated (via _normalize_prep_days) before any write,
    same as every other field — a household with an invalid weekday must
    not lose its otherwise-valid dinner_window in the same call.

    UPDATED 2026-09-18 (Card 4 — "any number of days can be on"):
    MAX_PREP_DAYS is no longer a real limit (there are only seven distinct
    weekdays to begin with, all already accepted); a nonexistent weekday
    name is what still makes this call invalid.
    """
    with pytest.raises(ValueError):
        tools.save_rhythm_answers(
            dinner_window="6_8",
            prep_days=[
                {"weekday": "sunday"}, {"weekday": "monday"}, {"weekday": "someday"},
            ],
        )
    rhythm = tools.get_household_rhythm()
    assert rhythm["dinner_window"] is None
    assert rhythm["prep_days"] == []


def test_a_bad_lunch_location_saves_nothing_either():
    _adult("Alex")
    with pytest.raises(ValueError):
        tools.save_rhythm_answers(
            leftovers_stance="love_them",
            lunch_location={"Alex": "not_a_real_place"},
        )
    rhythm = tools.get_household_rhythm()
    assert rhythm["leftovers_stance"] is None
    assert rhythm["lunch_location"] == {}


def test_one_person_cooking_with_nobody_named_saves_nothing():
    with pytest.raises(ValueError, match="who is required"):
        tools.save_rhythm_answers(
            planning_anchor="friday",
            cooking_role="one_person",
            cooking_role_who="",
        )
    rhythm = tools.get_household_rhythm()
    assert rhythm["planning_anchor"] is None
    assert rhythm["cooking_role"] is None


def test_a_fully_valid_call_writes_every_field_in_one_go():
    _adult("Alex")
    result = tools.save_rhythm_answers(
        lunch_location={"Alex": "home"},
        meals_together="most_meals",
        cooking_role="one_person",
        cooking_role_who="Alex",
        dinner_window="6_8",
        planning_anchor="friday",
        leftovers_stance="love_them",
        prep_days=[{"weekday": "sunday", "minutes": 60}],
    )
    assert result["meals_together"] == "most_meals"
    assert result["cooking_role"] == {"value": "one_person", "who": "Alex"}
    assert result["dinner_window"] == "6_8"
    assert result["planning_anchor"] == "friday"
    assert result["leftovers_stance"] == "love_them"
    assert result["lunch_location"]["Alex"]["standing"] == "home"
    assert result["prep_days"] == [{"weekday": "sunday", "minutes": 60, "note": None}]


def test_the_route_saves_nothing_when_one_field_is_bad(signed_in):
    """
    Same guarantee, over the real route: a bad field in the payload must
    leave every other field in that same request unsaved. meals_together
    is deliberately the field the old route wrote FIRST among these two
    (lunch_location, meals_together, cooking_role, ... in that order) — a
    test that only used fields in the other order would pass on `main`
    by accident, because the earlier field would never be reached.
    """
    res = signed_in.post(
        "/api/onboarding/rhythm",
        json={"meals_together": "most_meals", "cooking_role": "not_a_real_role"},
    )
    assert res.status_code == 400
    rhythm = tools.get_household_rhythm()
    assert rhythm["meals_together"] is None
    assert rhythm["cooking_role"] is None


def test_the_route_still_saves_a_fully_valid_partial_body(signed_in):
    """No-regression guard: a normal, valid partial save (what the real
    onboarding wizard and What we know both send) must keep working."""
    res = signed_in.post(
        "/api/onboarding/rhythm",
        json={"dinner_window": "6_8", "leftovers_stance": "fine_sometimes"},
    )
    assert res.status_code == 200
    body = res.json()
    assert body["dinner_window"] == "6_8"
    assert body["leftovers_stance"] == "fine_sometimes"
