"""
Loop Board: "Stores: one bidirectional memory between Kitchen typical-items
and grocery auto-assignment" (3d01f4c0-5231-812d-acaa-db6d73ce829c).

Before this, two stores of item->store memory existed and never talked to
each other: meal_preferences.store_typical_items_json (Kitchen "What we
know" Stores sheet, written by preferences.add_store_typical_items) only
fed "usually get here" suggestion rows, while item_store_preferences
(stores.py, read by grocery.add_grocery_item at add-time) drove
auto-assignment and was written only by grocery-list triage/chat, silently,
with no path back to the Kitchen sheet.

These tests cover the unification: Kitchen -> Grocery, Grocery -> Kitchen
(via the first-time confirm step), the removal rule, the one-source-of-truth
dedupe between the two lists, and preference_events logging (the growth
counter).
"""
from app import db
from app import tools
from app.db import get_conn
from app.tools._shared import household_id
from app.tools.grocery import _merge_key


def _pref_events_count(field: str | None = None) -> int:
    conn = get_conn()
    if field:
        row = conn.execute(
            "SELECT COUNT(*) AS c FROM preference_events WHERE household_id = ? AND field = ?",
            (household_id(), field),
        ).fetchone()
    else:
        row = conn.execute(
            "SELECT COUNT(*) AS c FROM preference_events WHERE household_id = ?", (household_id(),)
        ).fetchone()
    conn.close()
    return row["c"]


# ---------- 1. Kitchen -> Grocery ----------

def test_kitchen_typical_item_add_sets_grocery_preference():
    tools.add_store_typical_items("Costco", ["paper towels"])

    prefs = tools.get_item_store_preferences()
    assert prefs.get("paper towels") == "Costco"

    # A brand new add of that item name should auto-assign to Costco.
    added = tools.add_grocery_item("paper towels")
    conn = get_conn()
    row = conn.execute("SELECT store FROM grocery_items WHERE id = ?", (added["item_id"],)).fetchone()
    conn.close()
    assert row["store"] == "Costco"


def test_kitchen_typical_item_add_logs_one_preference_event():
    before = _pref_events_count()
    tools.add_store_typical_items("Costco", ["rotisserie chicken"])
    after = _pref_events_count()
    # One teaching moment -> one event, not one per underlying table touched.
    assert after == before + 1


def test_kitchen_remove_typical_item_clears_matching_preference():
    tools.add_store_typical_items("Costco", ["paper towels"])
    assert tools.get_item_store_preferences().get("paper towels") == "Costco"

    tools.remove_store_typical_item("Costco", "paper towels")

    assert "paper towels" not in tools.get_item_store_preferences()
    memory = tools.get_household_memory()
    assert "paper towels" not in memory["store_typical_items"].get("Costco", [])


def test_kitchen_remove_typical_item_does_not_clear_unrelated_preference():
    # A stale/duplicate typical-items entry under the WRONG store shouldn't
    # be able to wipe out a real, correct preference for a different store —
    # the sound rule this ticket decided: only clear when the preference
    # currently points at the store being removed from.
    tools.set_item_store("olive oil", "Trader Joe's")
    conn = get_conn()
    conn.execute(
        "UPDATE meal_preferences SET store_typical_items_json = json_set(store_typical_items_json, '$.Costco', json('[\"olive oil\"]')) WHERE household_id = ?",
        (household_id(),),
    )
    conn.commit()
    conn.close()

    tools.remove_store_typical_item("Costco", "olive oil")

    # The Trader Joe's preference survives.
    assert tools.get_item_store_preferences().get("olive oil") == "Trader Joe's"


# ---------- 2. Grocery -> Kitchen (first-time confirm) ----------

def test_first_time_grocery_assign_needs_confirmation_and_does_not_write_preference():
    added = tools.add_grocery_item("kombucha")
    result = tools.set_grocery_item_store(added["item_id"], "Trader Joe's")

    assert result["needs_confirmation"] is True
    assert result["remembered"] is False
    assert "kombucha" not in tools.get_item_store_preferences()
    # The row itself is still assigned immediately, regardless of confirmation.
    conn = get_conn()
    row = conn.execute("SELECT store FROM grocery_items WHERE id = ?", (added["item_id"],)).fetchone()
    conn.close()
    assert row["store"] == "Trader Joe's"


def test_confirming_writes_preference_and_kitchen_typical_items_in_one_event():
    added = tools.add_grocery_item("kombucha")
    tools.set_grocery_item_store(added["item_id"], "Trader Joe's")

    before = _pref_events_count()
    result = tools.confirm_grocery_item_store_preference(added["item_id"])
    after = _pref_events_count()

    assert result["confirmed"] is True
    assert tools.get_item_store_preferences().get("kombucha") == "Trader Joe's"
    memory = tools.get_household_memory()
    assert "kombucha" in memory["store_typical_items"].get("Trader Joe's", [])
    assert after == before + 1


def test_declining_confirmation_leaves_nothing_remembered():
    added = tools.add_grocery_item("kombucha")
    tools.set_grocery_item_store(added["item_id"], "Trader Joe's")
    # "Just this once" — the shopper never calls confirm at all.
    assert "kombucha" not in tools.get_item_store_preferences()

    # Same item assigned again next time still offers the same confirmation.
    added2 = tools.add_grocery_item("kombucha")
    result2 = tools.set_grocery_item_store(added2["item_id"], "Trader Joe's")
    assert result2["needs_confirmation"] is True


def test_subsequent_assignment_of_known_item_updates_quietly():
    tools.add_store_typical_items("Costco", ["paper towels"])  # seeds the preference
    added = tools.add_grocery_item("paper towels")

    before = _pref_events_count()
    result = tools.set_grocery_item_store(added["item_id"], "Walmart")
    after = _pref_events_count()

    assert result["needs_confirmation"] is False
    assert result["remembered"] is True
    assert tools.get_item_store_preferences().get("paper towels") == "Walmart"
    assert after == before + 1  # still logged, just no confirmation step


def test_not_this_time_never_touches_the_preference():
    tools.add_store_typical_items("Costco", ["paper towels"])
    added = tools.add_grocery_item("paper towels")

    result = tools.set_grocery_item_store(added["item_id"], "")

    assert result["remembered"] is False
    assert result["needs_confirmation"] is False
    assert tools.get_item_store_preferences().get("paper towels") == "Costco"


# ---------- 3. One source of truth / dedupe ----------

def test_moving_a_known_item_to_a_new_store_moves_the_kitchen_typical_item_too():
    tools.add_store_typical_items("Costco", ["paper towels"])
    added = tools.add_grocery_item("paper towels")

    tools.set_grocery_item_store(added["item_id"], "Walmart")  # quiet correction path

    memory = tools.get_household_memory()
    assert "paper towels" not in memory["store_typical_items"].get("Costco", [])
    assert "paper towels" in memory["store_typical_items"].get("Walmart", [])


def test_adding_as_typical_elsewhere_moves_it_away_from_the_old_store():
    tools.set_item_store("olive oil", "Trader Joe's")
    memory = tools.get_household_memory()
    assert "olive oil" in memory["store_typical_items"].get("Trader Joe's", [])

    tools.add_store_typical_items("Costco", ["olive oil"])

    memory = tools.get_household_memory()
    assert "olive oil" not in memory["store_typical_items"].get("Trader Joe's", [])
    assert "olive oil" in memory["store_typical_items"].get("Costco", [])
    assert tools.get_item_store_preferences().get("olive oil") == "Costco"


def test_clearing_a_preference_drops_it_from_every_stores_typical_list():
    tools.set_item_store("olive oil", "Trader Joe's")
    assert "olive oil" in tools.get_household_memory()["store_typical_items"].get("Trader Joe's", [])

    tools.set_item_store("olive oil", "")

    memory = tools.get_household_memory()
    for items in memory["store_typical_items"].values():
        assert "olive oil" not in items
    assert "olive oil" not in tools.get_item_store_preferences()


# ---------- 4. Chat parity ----------

def test_chat_set_item_store_lands_on_kitchen_sheet():
    # "we get paper towels at Costco" dispatches to this exact tool call.
    tools.set_item_store("paper towels", "Costco")

    memory = tools.get_household_memory()
    assert "paper towels" in memory["store_typical_items"].get("Costco", [])
    assert tools.get_item_store_preferences().get("paper towels") == "Costco"


# ---------- 5. HTTP-layer wiring (the actual routes the UI calls) ----------

def test_memory_store_items_add_endpoint_writes_grocery_preference(signed_in):
    res = signed_in.post("/api/memory/store-items/add", json={"store": "Costco", "item": "paper towels"})
    assert res.status_code == 200
    assert "paper towels" in res.json()["store_typical_items"].get("Costco", [])
    assert tools.get_item_store_preferences().get("paper towels") == "Costco"


def test_memory_store_items_remove_endpoint_clears_matching_preference(signed_in):
    signed_in.post("/api/memory/store-items/add", json={"store": "Costco", "item": "paper towels"})
    res = signed_in.post("/api/memory/store-items/remove", json={"store": "Costco", "item": "paper towels"})
    assert res.status_code == 200
    assert "paper towels" not in tools.get_item_store_preferences()


def test_grocery_store_confirm_endpoint_round_trip(signed_in):
    added = tools.add_grocery_item("kombucha")
    assign = signed_in.post(f"/api/grocery-list/{added['item_id']}/store", json={"store": "Trader Joe's"})
    assert assign.json()["needs_confirmation"] is True

    confirm = signed_in.post(f"/api/grocery-list/{added['item_id']}/store/confirm")
    assert confirm.status_code == 200
    assert confirm.json()["confirmed"] is True
    assert tools.get_item_store_preferences().get("kombucha") == "Trader Joe's"

    prefs = signed_in.get("/api/grocery-list/store-preferences")
    assert prefs.json()["preferences"].get("kombucha") == "Trader Joe's"


def test_grocery_store_confirm_endpoint_noops_if_row_store_cleared(signed_in):
    added = tools.add_grocery_item("kombucha")
    signed_in.post(f"/api/grocery-list/{added['item_id']}/store", json={"store": "Trader Joe's"})
    signed_in.post(f"/api/grocery-list/{added['item_id']}/store", json={"store": ""})  # "not this time"

    confirm = signed_in.post(f"/api/grocery-list/{added['item_id']}/store/confirm")
    assert confirm.json()["confirmed"] is False
    assert "kombucha" not in tools.get_item_store_preferences()


# ---------- 6. Identity: singular/plural must not fork the memory ----------
#
# Found by independent review (2026-09-03): item_store_preferences and the
# store_typical_items_json dedupe/removal guard matched on exact lowercased
# text, while grocery.add_grocery_item matches item names on _merge_key
# (which singularizes the trailing word). "paper towel" and "paper towels"
# are the same item to the grocery list but were two different identities
# to this memory — violating the "one source of truth" rule (#3) these
# tests otherwise cover.

def _pref_row_count(item_substring: str) -> int:
    conn = get_conn()
    row = conn.execute(
        "SELECT COUNT(*) AS c FROM item_store_preferences WHERE household_id = ? AND item LIKE ?",
        (household_id(), f"%{item_substring}%"),
    ).fetchone()
    conn.close()
    return row["c"]


def test_singular_then_plural_write_collapses_to_one_preference_row():
    tools.set_item_store("paper towel", "Costco")
    tools.add_store_typical_items("Walmart", ["paper towels"])

    # Exactly one row for this identity, not two independent ones.
    assert _pref_row_count("towel") == 1
    # Most-recent write wins, same rule used everywhere else in this file.
    assert tools.get_item_store_preferences().get("paper towels") == "Walmart"


def test_singular_then_plural_write_does_not_leave_item_typical_at_both_stores():
    tools.set_item_store("paper towel", "Costco")
    tools.add_store_typical_items("Walmart", ["paper towels"])

    memory = tools.get_household_memory()
    sti = memory["store_typical_items"]
    assert not any(_merge_key(i) == "paper towel" for i in sti.get("Costco", [])), (
        "the old spelling should have been dropped from Costco's typical list"
    )
    assert any(_merge_key(i) == "paper towel" for i in sti.get("Walmart", []))


def test_singular_then_plural_write_auto_assigns_deterministically():
    tools.set_item_store("paper towel", "Costco")
    tools.add_store_typical_items("Walmart", ["paper towels"])

    added = tools.add_grocery_item("paper towel")
    conn = get_conn()
    row = conn.execute("SELECT store FROM grocery_items WHERE id = ?", (added["item_id"],)).fetchone()
    conn.close()
    assert row["store"] == "Walmart"  # the one surviving, most-recent preference


def test_removal_guard_matches_across_plural_singular_when_it_should():
    tools.set_item_store("paper towel", "Costco")  # preference stored singular
    conn = get_conn()
    conn.execute(
        "UPDATE meal_preferences SET store_typical_items_json = json_set(store_typical_items_json, '$.Costco', json('[\"paper towels\"]')) WHERE household_id = ?",
        (household_id(),),
    )  # typical-items entry stored plural, same store
    conn.commit()
    conn.close()

    tools.remove_store_typical_item("Costco", "paper towels")

    assert "paper towel" not in tools.get_item_store_preferences()


def test_removal_guard_still_protects_unrelated_store_across_plural_singular():
    tools.set_item_store("paper towel", "Trader Joe's")  # the real, correct preference
    conn = get_conn()
    conn.execute(
        "UPDATE meal_preferences SET store_typical_items_json = json_set(store_typical_items_json, '$.Costco', json('[\"paper towels\"]')) WHERE household_id = ?",
        (household_id(),),
    )  # a stale duplicate entry under the WRONG store, plural spelling
    conn.commit()
    conn.close()

    tools.remove_store_typical_item("Costco", "paper towels")

    assert tools.get_item_store_preferences().get("paper towel") == "Trader Joe's"


def test_clearing_a_preference_removes_typical_entry_of_a_different_spelling():
    tools.set_item_store("paper towel", "Costco")
    conn = get_conn()
    conn.execute(
        "UPDATE meal_preferences SET store_typical_items_json = json_set(store_typical_items_json, '$.Costco', json('[\"paper towels\"]')) WHERE household_id = ?",
        (household_id(),),
    )
    conn.commit()
    conn.close()

    tools.set_item_store("paper towel", "")  # chat/UI clears the preference entirely

    memory = tools.get_household_memory()
    for items in memory["store_typical_items"].values():
        assert _merge_key("paper towel") not in {_merge_key(i) for i in items}


def test_migration_merges_pre_existing_duplicate_rows_keeping_the_newest():
    # Simulate a database written before the identity fix: two independent
    # rows for the same underlying item, inserted directly (bypassing
    # set_item_store, which now prevents this from happening).
    conn = get_conn()
    conn.execute(
        "INSERT INTO item_store_preferences (household_id, item, store) VALUES (?, 'paper towel', 'Costco')",
        (household_id(),),
    )
    conn.execute(
        "INSERT INTO item_store_preferences (household_id, item, store) VALUES (?, 'paper towels', 'Walmart')",
        (household_id(),),
    )
    conn.commit()
    conn.close()
    assert _pref_row_count("towel") == 2  # legacy duplicate, pre-cleanup

    conn = get_conn()
    db._merge_duplicate_item_store_preferences(conn)
    conn.commit()
    conn.close()

    assert _pref_row_count("towel") == 1
    # Keeps the most-recently-created (higher id) row.
    assert tools.get_item_store_preferences().get("paper towels") == "Walmart"


def test_migration_is_idempotent_and_leaves_distinct_items_alone():
    tools.set_item_store("paper towel", "Costco")
    tools.set_item_store("olive oil", "Trader Joe's")

    conn = get_conn()
    db._merge_duplicate_item_store_preferences(conn)
    db._merge_duplicate_item_store_preferences(conn)  # running twice changes nothing further
    conn.commit()
    conn.close()

    prefs = tools.get_item_store_preferences()
    assert prefs.get("paper towel") == "Costco"
    assert prefs.get("olive oil") == "Trader Joe's"
