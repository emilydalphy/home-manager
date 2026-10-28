"""
Undo on a "this meal only" Tweak works even after someone has said how it
went (overnight defect hunt, 2026-10-28).

A typed change on one night of a dish the week repeats goes on a COPY of
the recipe (recipe_change.apply_rewrite's fork); Put it back repoints the
night at the original and deletes the copy. recipe_notes and
member_recipe_feedback both reference recipes(id) with no ON DELETE
CASCADE, so a cooking note ("used turkey instead") or a person's rating
saved on the copy before Undo made that delete raise "FOREIGN KEY
constraint failed" — a 500, the night left on the copy. What the household
said is kept on the recipe the night goes back to: notes as they are,
and a rating as a NOTE naming the version — never as a rating on the
original, because the verdict was most likely about the change that was
undone (see recipe_change._carry_fork_history).

Second, smaller: a newer change on top of the copy (a second Tweak of the
same night rewrites the copy in place) was silently thrown away by undoing
the older one. That is now refused the way the in-place undo refuses it.
"""
from __future__ import annotations

import pytest

from app import agent, tools
from app.db import get_conn

from conftest import household_date
from test_tweak_something_else import (
    _NO_CILANTRO, _entry_recipe_id, _ingredients, _meal, _plan, _post, _recipe, _writer,
)

TUE = household_date(1)
WED = household_date(2)

_NO_LIME = {
    "ingredients": [{"item": "Chicken thighs", "qty": "1 lb"}, {"item": "Avocado", "qty": "1"}],
    "instructions": ["Grill the chicken.", "Slice the avocado over."],
    "default_servings": 4,
}


def _rows(sql, *args):
    conn = get_conn()
    try:
        return [dict(r) for r in conn.execute(sql, args)]
    finally:
        conn.close()


def _list():
    return sorted(
        (r["item"], r["quantity"], r["status"]) for r in _rows(
            "SELECT item, quantity, status FROM grocery_items WHERE household_id = 1")
    )


def _recipe_name(recipe_id):
    return _rows("SELECT name FROM recipes WHERE id = ?", recipe_id)[0]["name"]


def _tacos_twice(monkeypatch, *details):
    plan = _plan("approved")
    tacos = _recipe("Chicken Tacos", [("Chicken thighs", "1 lb"), ("Cilantro", "1 bunch")])
    tue = _meal(plan, TUE, "Chicken Tacos", buy=True)
    _meal(plan, WED, "Chicken Tacos", buy=True)
    monkeypatch.setattr(agent, "generate_recipe_details_llm", _writer(*(details or (_NO_CILANTRO,))))
    return tacos, tue


@pytest.fixture
def sam():
    tools.add_member("Sam")
    return "Sam"


def _day(date):
    import datetime
    return datetime.date.fromisoformat(date).strftime("%A")


def _feedback_notes(recipe_id):
    return [n["note"] for n in _rows(
        "SELECT note FROM recipe_notes WHERE recipe_id = ? AND note_type = 'feedback' ORDER BY id", recipe_id)]


def test_undo_after_a_cooking_note_and_a_rating_puts_the_night_back_and_keeps_what_was_said(
        signed_in, monkeypatch, sam):
    tacos, tue = _tacos_twice(monkeypatch)
    list_before = _list()
    body = _post(signed_in, tue, "No cilantro").json()
    assert body["forked"] is True, body
    fork = _entry_recipe_id(tue)
    tools.log_cooking_deviation(_recipe_name(fork), "Used lime twice, it was great")
    tools.attribute_recipe_feedback(_recipe_name(fork), sam, rating="disliked")
    tools.mark_recipe_feedback(_recipe_name(fork), rating="disliked", notes="Kids ate it")

    res = signed_in.post("/api/meal-recipe/undo", json={"request_id": body["request_id"]})

    assert res.status_code == 200, res.text
    assert res.json()["status"] == "undone", res.json()
    assert _entry_recipe_id(tue) == tacos
    assert _rows("SELECT id FROM recipes WHERE id = ?", fork) == []
    assert _list() == list_before, "the list comes back exactly as before the Tweak"
    # The cooking note moves across as it is.
    assert [(n["recipe_id"], n["note"]) for n in _rows(
        "SELECT recipe_id, note FROM recipe_notes WHERE note_type = 'deviation'")] == [
        (tacos, "Used lime twice, it was great")]
    # A verdict on the tweak is NOT a verdict on the dish: no rating moves...
    assert _rows("SELECT * FROM member_recipe_feedback") == []
    original = _rows("SELECT rating, feedback_notes FROM recipes WHERE id = ?", tacos)[0]
    assert original == {"rating": "", "feedback_notes": ""}
    # ...it is kept as a note that says which version it was about.
    version = f"the {_day(TUE)} version (No cilantro)"
    assert _feedback_notes(tacos) == [
        f"Sam: disliked {version}", f"Disliked {version}", f"About {version}: Kids ate it"]


def test_a_rating_already_on_the_original_is_not_touched(signed_in, monkeypatch, sam):
    tacos, tue = _tacos_twice(monkeypatch)
    tools.attribute_recipe_feedback("Chicken Tacos", sam, rating="liked")
    tools.mark_recipe_feedback("Chicken Tacos", rating="liked", notes="A keeper")
    before = (_rows("SELECT * FROM member_recipe_feedback"),
              _rows("SELECT rating, feedback_notes FROM recipes WHERE id = ?", tacos))
    body = _post(signed_in, tue, "No cilantro").json()
    fork = _entry_recipe_id(tue)
    tools.attribute_recipe_feedback(_recipe_name(fork), sam, rating="disliked")
    tools.mark_recipe_feedback(_recipe_name(fork), rating="disliked")

    undone = signed_in.post("/api/meal-recipe/undo", json={"request_id": body["request_id"]}).json()

    assert undone["status"] == "undone", undone
    assert (_rows("SELECT * FROM member_recipe_feedback"),
            _rows("SELECT rating, feedback_notes FROM recipes WHERE id = ?", tacos)) == before


def test_another_households_notes_and_ratings_are_not_touched(signed_in, monkeypatch, sam):
    from app import households

    tacos, tue = _tacos_twice(monkeypatch)
    body = _post(signed_in, tue, "No cilantro").json()
    fork = _entry_recipe_id(tue)
    tools.log_cooking_deviation(_recipe_name(fork), "Ours")
    other = households.create_household("Undo Isolation", "undo-isolation-pass")
    with tools.use_household(other):
        tools.add_member("Pat")
        tools.add_recipe("Their Rajma", [{"item": "Kidney beans", "qty": "2 cans"}], instructions=["Cook."])
        tools.log_cooking_deviation("Their Rajma", "Theirs")
        tools.attribute_recipe_feedback("Their Rajma", "Pat", rating="liked")
    theirs_before = (_rows("SELECT * FROM recipe_notes WHERE household_id = ?", other),
                     _rows("SELECT * FROM member_recipe_feedback WHERE household_id = ?", other))

    undone = signed_in.post("/api/meal-recipe/undo", json={"request_id": body["request_id"]}).json()

    assert undone["status"] == "undone", undone
    assert (_rows("SELECT * FROM recipe_notes WHERE household_id = ?", other),
            _rows("SELECT * FROM member_recipe_feedback WHERE household_id = ?", other)) == theirs_before


def test_undoing_the_older_tweak_under_a_newer_one_is_refused_not_both_thrown_away(signed_in, monkeypatch):
    tacos, tue = _tacos_twice(monkeypatch, _NO_CILANTRO, _NO_LIME)
    first = _post(signed_in, tue, "No cilantro").json()
    fork = _entry_recipe_id(tue)
    second = _post(signed_in, tue, "No lime, add avocado").json()
    assert second["status"] == "rewritten" and second["forked"] is False, second
    assert _entry_recipe_id(tue) == fork
    list_with_both = _list()

    older = signed_in.post("/api/meal-recipe/undo", json={"request_id": first["request_id"]}).json()

    assert older["status"] == "refused" and older["said"] == tools.recipe_change.UNDO_NEWER, older
    assert _entry_recipe_id(tue) == fork
    assert "Avocado" in {i["item"] for i in _ingredients(fork)}
    assert _list() == list_with_both
    # In order, both come off: the newer one, then the older one.
    assert signed_in.post("/api/meal-recipe/undo", json={"request_id": second["request_id"]}).json()["status"] == "undone"
    assert signed_in.post("/api/meal-recipe/undo", json={"request_id": first["request_id"]}).json()["status"] == "undone"
    assert _entry_recipe_id(tue) == tacos
    assert _rows("SELECT id FROM recipes WHERE id = ?", fork) == []
