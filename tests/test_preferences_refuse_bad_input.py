"""
Five small input defects in What we know's writers (overnight, 2026-10-12),
each reproduced on a throwaway database before it was fixed:

  1. POST /api/memory/edit {"field": "eating_style", "value": ["keto"]}
     was a 500 (sqlite could not bind a list). Now a 400 in a sentence.
  2. novelty_preference stored any word ("banana", 200). Now only the
     three the app offers.
  3. A protein rating of 99 was stored. Now refused off the 1-5 scale.
  4. update_fact could blank a fact that add_fact would never have stored.
  5. edit_preference("notes", "") and the cooking-time answer kept the old
     words while reporting success. Now "" clears them, as it clears every
     other text field edit_preference writes.

Assertions read the DATABASE back (get_household_memory / get_facts), never
only the returned dict — see test_household_scope_sweep's note on why.
"""
from __future__ import annotations

import pytest

from app import tools


# 1 -------------------------------------------------------------------------

def test_eating_style_as_a_list_is_a_400_not_a_500(signed_in):
    tools.edit_preference("eating_style", "high-protein")
    res = signed_in.post("/api/memory/edit", json={"field": "eating_style", "value": ["keto"]})
    assert res.status_code == 400
    assert "eating_style" in res.json()["detail"]
    assert tools.get_household_memory()["eating_style"] == "high-protein"


def test_notes_as_null_is_refused_rather_than_kept_silently():
    tools.edit_preference("notes", "we eat late")
    with pytest.raises(ValueError, match="notes"):
        tools.edit_preference("notes", None)
    assert tools.get_household_memory()["notes"] == "we eat late"


# 2 -------------------------------------------------------------------------

def test_novelty_takes_only_the_three_answers_the_app_offers(signed_in):
    res = signed_in.post("/api/memory/edit", json={"field": "novelty_preference", "value": "banana"})
    assert res.status_code == 400
    assert "surprise_me_often" in res.json()["detail"]
    assert tools.get_household_memory()["novelty_preference"] == "balanced"
    for answer in ("mostly_favorites", "surprise_me_often", "balanced"):
        tools.edit_preference("novelty_preference", answer)
        assert tools.get_household_memory()["novelty_preference"] == answer


def test_chats_own_preferences_tool_refuses_a_made_up_novelty_too():
    with pytest.raises(ValueError, match="novelty_preference"):
        tools.set_household_meal_preferences(novelty_preference="Balanced", mark_complete=False)


# 3 -------------------------------------------------------------------------

def test_a_protein_rating_off_the_one_to_five_scale_is_refused(signed_in):
    tools.edit_preference("protein_preferences", {"chicken": 5})
    for bad in (99, 0, 6, -1, 4.5, "lots", True):
        res = signed_in.post("/api/memory/edit",
                             json={"field": "protein_preferences", "value": {"beef": bad}})
        assert res.status_code == 400, bad
    # Nothing half-written: a refused batch leaves the stored ratings alone.
    with pytest.raises(ValueError, match="beef"):
        tools.edit_preference("protein_preferences", {"pork": 3, "beef": 99})
    assert tools.get_household_memory()["protein_preferences"] == {"chicken": 5}


def test_the_ends_of_the_scale_and_a_number_sent_as_text_are_kept():
    tools.edit_preference("protein_preferences", {"chicken": 1, "beef": 5, "tofu": "3"})
    assert tools.get_household_memory()["protein_preferences"] == {"chicken": 1, "beef": 5, "tofu": 3}


# 4 -------------------------------------------------------------------------

def test_update_fact_refuses_blank_text_as_add_fact_does(signed_in):
    fact = tools.add_fact("taste", "likes tea")
    for blank in ("", "   "):
        with pytest.raises(ValueError, match="blank"):
            tools.update_fact(fact["id"], text=blank)
    res = signed_in.post(f"/api/facts/{fact['id']}/update", json={"text": " "})
    assert res.status_code == 400
    assert [f["text"] for f in tools.get_facts()] == ["likes tea"]


def test_update_fact_still_flips_hard_alone_and_still_404s_a_missing_fact(signed_in):
    fact = tools.add_fact("taste", "no pork")
    tools.update_fact(fact["id"], hard=True)
    assert [(f["text"], f["hard"]) for f in tools.get_facts()] == [("no pork", True)]
    assert signed_in.post("/api/facts/99999/update", json={"text": "x"}).status_code == 404


# 5 -------------------------------------------------------------------------

@pytest.mark.parametrize("field", ["notes", "cooking_time_preference"])
def test_an_empty_string_clears_the_answer(field):
    tools.edit_preference(field, "quick and late")
    tools.edit_preference(field, "")
    assert tools.get_household_memory()[field] == ""


def test_clearing_notes_leaves_the_rest_of_the_row_alone():
    tools.edit_preference("notes", "we eat late")
    tools.edit_preference("novelty_preference", "surprise_me_often")
    tools.edit_preference("notes", "")
    memory = tools.get_household_memory()
    assert memory["notes"] == ""
    assert memory["novelty_preference"] == "surprise_me_often"
