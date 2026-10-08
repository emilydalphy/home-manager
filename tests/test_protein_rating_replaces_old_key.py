"""
Defect hunt, 2026-10-08 (branch overnight/protein-chip-one-answer): a
protein's rating filed under another spelling survived a new rating.

Chat writes {"Chicken": 5}; an older setup wrote {"Fish / seafood": "more"}.
Settings' chip reads both as the chicken / fish answer (shell.js
wwkProteinState), but a tap posted {"chicken": 1} and the merge kept the old
key beside it — so the chip said "skip" while the planner was still handed
"Chicken: 5". Found by tapping Chicken and Fish in How you eat against a
real server and reading /api/memory back.
"""
from __future__ import annotations

from app import tools


def _proteins():
    return tools.get_household_memory()["protein_preferences"]


def test_a_new_rating_replaces_the_old_answer_whatever_it_was_filed_under(signed_in):
    tools.edit_preference("protein_preferences", {"Chicken": 5, "Fish / seafood": "more", "beef": 3})

    # Exactly what the chip posts on a tap.
    assert signed_in.post("/api/memory/edit", json={"field": "protein_preferences", "value": {"chicken": 1}}).status_code == 200
    assert signed_in.post("/api/memory/edit", json={"field": "protein_preferences", "value": {"fish": 1}}).status_code == 200

    assert _proteins() == {"beef": 3, "chicken": 1, "fish": 1}


def test_a_different_protein_that_merely_starts_alike_is_left_alone():
    tools.edit_preference("protein_preferences", {"tofurky": 4, "fish": 2, "Chicken": 5})
    tools.edit_preference("protein_preferences", {"tofu": 1, "white fish": 5})

    # "tofurky" is not tofu; a two-word rating never claims "fish".
    assert _proteins() == {"tofurky": 4, "fish": 2, "Chicken": 5, "tofu": 1, "white fish": 5}
