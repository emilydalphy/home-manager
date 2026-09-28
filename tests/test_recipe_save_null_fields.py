"""
A recipe saved with an explicit null field, and a failed save, never break
the app.

FOUND 2026-09-28 on the live app, twice in a row: the week planner sent a
dish with `"cuisine": null`, `agent._ensure_recipe_saved` passed it straight
to `tools.add_recipe`, and the INSERT failed on recipes.cuisine NOT NULL —
failing the whole draft ("That draft didn't come together"). Worse, the
INSERT sat outside add_recipe's try, so the failed INSERT kept BEGIN
IMMEDIATE's write lock and every later write in the app answered
"database is locked" until the server restarted.
"""
import sqlite3

import pytest

from app import tools
from app.db import get_conn


CURRY = [{"item": "Potatoes", "qty": "2 lbs", "category": "produce"}]


def test_null_cuisine_and_protein_save_as_empty():
    """CATCH. On ea945d9 this raised IntegrityError: recipes.cuisine."""
    out = tools.add_recipe(
        "Japanese Vegetable Curry", CURRY, cuisine=None, main_protein=None,
        advance_prep_notes=None, notes=None, default_servings=None,
    )
    conn = get_conn()
    row = conn.execute(
        "SELECT cuisine, main_protein, notes, advance_prep_notes, default_servings "
        "FROM recipes WHERE id = ?", (out["recipe_id"],),
    ).fetchone()
    conn.close()
    assert (row["cuisine"], row["main_protein"], row["notes"], row["advance_prep_notes"]) == ("", "", "", "")
    assert row["default_servings"] == 4


def test_a_failed_insert_gives_the_write_lock_back(monkeypatch):
    """
    CATCH. Force the INSERT itself to fail; the next write must not see
    "database is locked". On ea945d9 the failed connection kept the lock.
    """
    monkeypatch.setattr(tools.recipes, "household_id", lambda: None)
    with pytest.raises(sqlite3.IntegrityError):
        tools.add_recipe("Lock Holder", CURRY)
    monkeypatch.undo()

    conn = sqlite3.connect(get_conn().execute("PRAGMA database_list").fetchone()[2], timeout=0.5)
    try:
        conn.execute("BEGIN IMMEDIATE")
        conn.rollback()
    finally:
        conn.close()
    # And the app's own write path works straight after.
    assert tools.add_recipe("After The Failure", CURRY)["recipe_id"]
