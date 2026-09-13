"""
Bug (Loop Board, 2026-09-13): "One wrong-typed stored answer stops a What
we know section from opening." Verified against a real server: POST
/api/memory/edit {"field": "cuisine_preferences", "value": "Thai"} answered
200 and /api/memory handed the field straight back as the bare string
"Thai" — not a one-item list. static/shell.js's own comment on
prefsCuisineForms (the one spot that had already worked around this)
spells out why that breaks a screen: ".filter on a string throws... every
row in the sheet stuck on 'Reading it back...'"

Two fixes, both in app/tools/memory.py:
  - edit_preference now coerces a bare string into a list for every
    list-valued field (cuisine_preferences, dislikes, usual_stores,
    kitchen_kit) instead of storing it raw, and refuses anything that
    isn't a string or a list of strings. Comma-split: "Thai, Mexican"
    becomes two items, a single word becomes one (_coerce_str_list).
  - get_household_memory (the backend of GET /api/memory) normalises the
    same four fields on the way OUT, so a row already written bad before
    this fix (or by any other path) doesn't throw on read either
    (_as_str_list). protein_preferences gets the dict-shaped version of
    the same guard on both sides.

This file exercises both write paths edit_preference is reachable from
(the direct call, which is also exactly what the /api/memory/edit route
and the chat tool of the same name both call) and the read path.
"""
from __future__ import annotations

import pytest

from app import tools


# --------------------------------------------------------------------------
# Write side: app/tools/memory.py edit_preference
# --------------------------------------------------------------------------

def test_a_bare_string_is_coerced_to_a_one_item_list():
    """The card's exact repro: {"field": "cuisine_preferences", "value":
    "Thai"} must not be stored as the raw string "Thai"."""
    result = tools.edit_preference("cuisine_preferences", "Thai")
    assert result["cuisine_preferences"] == ["Thai"]
    memory = tools.get_household_memory()
    assert memory["cuisine_preferences"] == ["Thai"]
    assert isinstance(memory["cuisine_preferences"], list)


def test_a_comma_separated_string_splits_into_several_items():
    result = tools.edit_preference("cuisine_preferences", "Thai, Mexican,  Italian ")
    assert result["cuisine_preferences"] == ["Thai", "Mexican", "Italian"]


@pytest.mark.parametrize("field", ["cuisine_preferences", "dislikes", "usual_stores", "kitchen_kit"])
def test_every_list_valued_field_gets_the_same_string_coercion(field):
    result = tools.edit_preference(field, "one thing")
    assert result[field] == ["one thing"]


@pytest.mark.parametrize("field", ["cuisine_preferences", "dislikes", "usual_stores", "kitchen_kit"])
def test_a_non_string_non_list_value_is_refused_not_stored(field):
    with pytest.raises(ValueError, match=field):
        tools.edit_preference(field, {"not": "a list"})
    # Refused before anything was written — What We Know still reads
    # whatever it had before (empty, here), never the bad value.
    memory = tools.get_household_memory()
    assert memory[field] == []


def test_an_ordinary_list_still_works_unchanged():
    result = tools.edit_preference("dislikes", ["olives", "cilantro"])
    assert result["dislikes"] == ["olives", "cilantro"]


@pytest.mark.parametrize("field", ["cuisine_preferences", "dislikes", "usual_stores", "kitchen_kit"])
def test_a_list_with_blank_items_is_trimmed_not_stored_raw(field):
    """Found adversarially, 2026-09-13: the string branch of
    _coerce_str_list already strips and drops blanks ("Thai, , " ->
    ["Thai"]), but the list branch used to return an already-a-list value
    untouched — so a stray blank chip or a padded model list
    (["Thai", "", "  "]) got stored as-is, and What We Know renders one
    empty chip per blank entry (wwkWontEatHtml/wwkFactChip have no
    blank-check of their own). Both branches now trim the same way."""
    result = tools.edit_preference(field, ["Thai", "", "   ", "Mexican"])
    assert result[field] == ["Thai", "Mexican"]
    assert tools.get_household_memory()[field] == ["Thai", "Mexican"]


@pytest.mark.parametrize("field", ["cuisine_preferences", "dislikes", "usual_stores", "kitchen_kit"])
def test_a_list_of_only_blanks_clears_the_field(field):
    result = tools.edit_preference(field, ["", "   "])
    assert result[field] == []
    assert tools.get_household_memory()[field] == []


@pytest.mark.parametrize("field", ["cuisine_preferences", "dislikes", "usual_stores", "kitchen_kit"])
def test_bool_and_none_are_refused_not_coerced_truthy(field):
    """isinstance(True, str/list) is False either way, but worth pinning:
    a bool must not be silently accepted by some other branch."""
    with pytest.raises(ValueError, match=field):
        tools.edit_preference(field, True)
    with pytest.raises(ValueError, match=field):
        tools.edit_preference(field, None)


@pytest.mark.parametrize("field", ["cuisine_preferences", "dislikes", "usual_stores", "kitchen_kit"])
def test_a_list_containing_a_non_string_item_is_refused(field):
    """["Thai", 5] — one bad item spoils the whole list rather than being
    silently dropped or coerced to a string."""
    with pytest.raises(ValueError, match=field):
        tools.edit_preference(field, ["Thai", 5])


def test_comma_split_edge_cases():
    assert tools.edit_preference("dislikes", "Thai,")["dislikes"] == ["Thai"]
    assert tools.edit_preference("dislikes", ",,,")["dislikes"] == []
    assert tools.edit_preference("dislikes", "")["dislikes"] == []
    # Unicode text has no ASCII comma to trip over.
    assert tools.edit_preference("dislikes", "카레, タイ")["dislikes"] == ["카레", "タイ"]


def test_protein_preferences_must_be_a_dict():
    with pytest.raises(ValueError, match="protein_preferences"):
        tools.edit_preference("protein_preferences", "chicken")
    memory = tools.get_household_memory()
    assert memory["protein_preferences"] == {}
    # A real dict still works.
    tools.edit_preference("protein_preferences", {"chicken": 5})
    assert tools.get_household_memory()["protein_preferences"] == {"chicken": 5}


def test_the_api_memory_edit_route_refuses_the_bad_value_with_400(signed_in):
    """The route the card's own repro actually hit — same guard, exercised
    through the HTTP layer rather than calling the tool directly."""
    res = signed_in.post("/api/memory/edit", json={"field": "cuisine_preferences", "value": "Thai"})
    assert res.status_code == 200
    assert res.json()["cuisine_preferences"] == ["Thai"]

    res = signed_in.post("/api/memory/edit", json={"field": "dislikes", "value": {"nope": 1}})
    assert res.status_code == 400
    assert "dislikes" in res.json()["detail"]

    # And /api/memory itself, read right after, must not 500.
    res = signed_in.get("/api/memory")
    assert res.status_code == 200
    assert res.json()["cuisine_preferences"] == ["Thai"]


# --------------------------------------------------------------------------
# Read side: app/tools/memory.py get_household_memory, for a row that was
# already bad on disk before this fix existed (a direct DB write is the
# only way to get one now that edit_preference itself refuses/coerces).
# --------------------------------------------------------------------------

def _write_raw_json(column: str, raw_value: str):
    import json as _json
    from app.db import get_conn
    from app.tools._shared import household_id

    conn = get_conn()
    conn.execute(
        f"INSERT INTO meal_preferences (household_id, {column}, updated_at) "
        f"VALUES (?, ?, datetime('now')) "
        f"ON CONFLICT(household_id) DO UPDATE SET {column} = excluded.{column}, updated_at = datetime('now')",
        (household_id(), _json.dumps(raw_value)),
    )
    conn.commit()
    conn.close()


@pytest.mark.parametrize("column,field", [
    ("cuisine_preferences_json", "cuisine_preferences"),
    ("dislikes_json", "dislikes"),
    ("usual_stores_json", "usual_stores"),
    ("kitchen_kit_json", "kitchen_kit"),
])
def test_an_already_bad_row_on_disk_reads_back_as_a_list_not_a_crash(column, field):
    _write_raw_json(column, "Thai")  # exactly what main used to store
    memory = tools.get_household_memory()
    assert memory[field] == ["Thai"]
    assert isinstance(memory[field], list)


def test_an_already_bad_protein_preferences_row_reads_back_as_a_dict():
    _write_raw_json("protein_preferences_json", "chicken")
    memory = tools.get_household_memory()
    assert memory["protein_preferences"] == {}


def test_the_api_memory_route_does_not_500_on_a_pre_existing_bad_row(signed_in):
    _write_raw_json("dislikes_json", "cilantro")
    res = signed_in.get("/api/memory")
    assert res.status_code == 200
    assert res.json()["dislikes"] == ["cilantro"]


def test_delete_preference_tolerates_an_already_bad_protein_preferences_row():
    """Found adversarially, 2026-09-13: unlike the three list fields,
    delete_preference's protein_preferences branch did dict(json.loads(...))
    with no isinstance guard — a legacy bare-string row (possible before
    this same fix added a write-side dict check to edit_preference) made
    dict() throw ("dictionary update sequence element #0 has length 1; 2 is
    required"), surfaced to the household as a confusing 400 on an ordinary
    "forget this protein" click."""
    _write_raw_json("protein_preferences_json", "chicken")
    tools.delete_preference("protein_preferences", "beef")  # must not raise
    assert tools.get_household_memory()["protein_preferences"] == {}

    tools.edit_preference("protein_preferences", {"chicken": 5, "beef": 2})
    tools.delete_preference("protein_preferences", "beef")
    assert tools.get_household_memory()["protein_preferences"] == {"chicken": 5}


@pytest.mark.parametrize("column,field", [
    ("dislikes_json", "dislikes"),
    ("cuisine_preferences_json", "cuisine_preferences"),
    ("usual_stores_json", "usual_stores"),
])
def test_delete_preference_also_tolerates_an_already_bad_row(column, field):
    """delete_preference (removing one item from a list field) reads the
    same raw JSON get_household_memory does. Without the same _as_str_list
    guard, a bare string doesn't throw here — Python happily iterates a
    string's CHARACTERS — so a bad row would silently get rewritten into
    single-character garbage the next time anything was removed, instead
    of the item just not being found."""
    _write_raw_json(column, "cilantro")
    tools.delete_preference(field, "parsley")  # removing something NOT in the bad value
    assert tools.get_household_memory()[field] == ["cilantro"], \
        "the existing value survives untouched, not exploded into letters"

    _write_raw_json(column, "cilantro")
    tools.delete_preference(field, "cilantro")  # removing the value that WAS the bare string
    assert tools.get_household_memory()[field] == []


def test_usual_stores_pruning_tolerates_an_already_bad_row():
    """Found adversarially, 2026-09-13: edit_preference's usual_stores
    branch prunes store_typical_items_json for any store dropped from the
    list, by diffing the OLD usual_stores_json against the new value. That
    diff used to read the old value with a raw json.loads, not
    _as_str_list — so a legacy bare-string row ("Costco") iterated as
    CHARACTERS, the diff came out as single letters that never match a
    real store name, and the stale store_typical_items entry silently
    survived a write that should have dropped it."""
    import json as _json
    from app.db import get_conn
    from app.tools._shared import household_id

    _write_raw_json("usual_stores_json", "Costco")
    conn = get_conn()
    conn.execute(
        "UPDATE meal_preferences SET store_typical_items_json = ? WHERE household_id = ?",
        (_json.dumps({"Costco": ["paper towels"]}), household_id()),
    )
    conn.commit()
    conn.close()

    tools.edit_preference("usual_stores", [])  # drop every store, including Costco

    memory = tools.get_household_memory()
    assert memory["usual_stores"] == []
    assert memory["store_typical_items"] == {}, "Costco's typical items should have been pruned"
