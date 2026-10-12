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

import json

from app import tools
from app.db import get_conn


def _proteins():
    return tools.get_household_memory()["protein_preferences"]


def test_a_new_rating_replaces_the_old_answer_whatever_it_was_filed_under(signed_in):
    # Seeded straight into the row, 2026-10-12: "more" is the OLD setup's
    # word, and the writers refuse it now (ratings are 1-5 —
    # tests/test_preferences_refuse_bad_input.py). A row already holding it
    # is exactly what this test is about, so it is put there the way the old
    # setup left it rather than through today's writer.
    tools.edit_preference("protein_preferences", {"Chicken": 5, "beef": 3})
    conn = get_conn()
    conn.execute(
        "UPDATE meal_preferences SET protein_preferences_json = ? WHERE household_id = ?",
        (json.dumps({"Chicken": 5, "Fish / seafood": "more", "beef": 3}), tools.household_id()),
    )
    conn.commit()
    conn.close()

    # Exactly what the chip posts on a tap.
    assert signed_in.post("/api/memory/edit", json={"field": "protein_preferences", "value": {"chicken": 1}}).status_code == 200
    assert signed_in.post("/api/memory/edit", json={"field": "protein_preferences", "value": {"fish": 1}}).status_code == 200

    assert _proteins() == {"beef": 3, "chicken": 1, "fish": 1}


def test_a_more_specific_answer_survives_a_rating_of_the_protein():
    """Review, 2026-10-08: a first-word rule wiped "chicken sausage: 1" — an
    explicit avoid — the moment chicken was rated 5."""
    tools.edit_preference("protein_preferences", {
        "chicken sausage": 1, "chicken thighs": 5, "fish tacos": 5, "pork, ground": 2, "tofurky": 4,
    })
    for rating in ({"chicken": 5}, {"fish": 3}, {"pork": 5}, {"tofu": 1}):
        tools.edit_preference("protein_preferences", rating)

    assert _proteins() == {
        "chicken sausage": 1, "chicken thighs": 5, "fish tacos": 5, "pork, ground": 2, "tofurky": 4,
        "chicken": 5, "fish": 3, "pork": 5, "tofu": 1,
    }
