"""
Forgetting a protein rating under the spelling it was not saved under
(defect hunt, 2026-10-12).

The Settings chip saves {"chicken": 5}; chat, asked to "forget the chicken
thing", calls delete_preference("protein_preferences", "Chicken"). That
used to pop the exact key only — nothing went, and the tool still answered
deleted: True, so the household was told it was forgotten while the
planner went on reading chicken as a favourite.

Forgetting now matches the way a new rating REPLACES an old one
(preferences._same_protein): any case, or one alternative of a slashed key,
and nothing looser.
"""
from app import tools


def _proteins() -> dict:
    return tools.get_household_memory()["protein_preferences"]


def test_chat_forgets_a_chip_rating_whatever_the_case():
    tools.edit_preference("protein_preferences", {"chicken": 5, "beef": 2})
    result = tools.delete_preference("protein_preferences", "Chicken")
    assert _proteins() == {"beef": 2}
    assert result["deleted"] is True and result["removed"] == ["chicken"]


def test_one_alternative_of_a_slashed_key_forgets_it():
    tools.edit_preference("protein_preferences", {"Fish / seafood": "more"})
    tools.delete_preference("protein_preferences", "fish")
    assert _proteins() == {}


def test_a_more_specific_answer_is_left_alone():
    """"chicken sausage: 1" is the household's own explicit avoid — forgetting
    chicken must not take it (the same line _same_protein holds for ratings)."""
    tools.edit_preference("protein_preferences", {"chicken": 5, "chicken sausage": 1})
    tools.delete_preference("protein_preferences", "chicken")
    assert _proteins() == {"chicken sausage": 1}


def test_nothing_to_forget_is_said_rather_than_claimed():
    tools.edit_preference("protein_preferences", {"beef": 2})
    result = tools.delete_preference("protein_preferences", "lamb")
    assert result["deleted"] is False and result["removed"] == []
    assert _proteins() == {"beef": 2}


def test_the_settings_route_still_forgets_by_its_exact_key(signed_in):
    tools.edit_preference("protein_preferences", {"tofu": 5})
    res = signed_in.post("/api/memory/delete", json={"field": "protein_preferences", "item": "tofu"})
    assert res.status_code == 200
    assert "tofu" not in res.json()["protein_preferences"]
