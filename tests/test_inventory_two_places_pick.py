"""
An item kept in two places lands on ONE stated row (Loop Board, Low,
Phase 1).

THE BUG. inventory._add_to_inventory matched an existing row by LOWER(item)
with fetchone() and no ORDER BY when the caller gave no location; the
"set" and "use" paths had the same shape (the "use" one already called it
a known limitation). With "BBQ sauce" opened in the fridge and unopened in
the pantry, SQLite returned whichever row it liked — an add could merge
into one and the next "used some" subtract from the other.

THE RULE. Without a location hint, every name-only path (add, set, use)
takes the most recently written row, ties to the newer one
(updated_at DESC, id DESC) — _find_row_by_name. The row the household last
touched is the one in play, and add-then-use land on the same row. Nothing
per-item records a "usual" place (location is an explicit hint or the
category default at insert), so there is nothing better to prefer; a
caller that knows the place passes location and skips the rule. One
matching row: unchanged behaviour.
"""

from __future__ import annotations

from app import tools
from app.db import get_conn


def _row(inventory_id: int) -> dict | None:
    conn = get_conn()
    r = conn.execute("SELECT id, item, quantity, location, category FROM inventory_items WHERE id = ?", (inventory_id,)).fetchone()
    conn.close()
    return dict(r) if r else None


def _insert(item: str, quantity: str, location: str, updated_at: str, category: str = "pantry") -> int:
    conn = get_conn()
    cur = conn.execute(
        "INSERT INTO inventory_items (household_id, item, quantity, source, category, location, updated_at) "
        "VALUES (1, ?, ?, 'chat', ?, ?, ?)",
        (item, quantity, category, location, updated_at),
    )
    conn.commit()
    conn.close()
    return cur.lastrowid


def _sauce_in_two_places(recent: str = "fridge"):
    """Two rows, the fridge one written a day after the pantry one unless
    told otherwise. Inserted pantry-first so the id order does not happen
    to agree with the recency order."""
    stale, fresh = ("2026-09-10 10:00:00", "2026-09-11 10:00:00")
    pantry = _insert("BBQ sauce", "1 bottle", "pantry", fresh if recent == "pantry" else stale)
    fridge = _insert("BBQ sauce", "1 bottle", "fridge", fresh if recent == "fridge" else stale)
    return pantry, fridge


# ---------- one row: nothing changes ----------


def test_one_matching_row_is_merged_into_as_before():
    only = _insert("Olive oil", "1 bottle", "pantry", "2026-09-10 10:00:00")
    res = tools.update_inventory("olive oil", "add", quantity="1 bottle")
    assert res["item_id"] == only
    assert _row(only)["quantity"] == "2 bottles"
    tools.update_inventory("Olive oil", "use", quantity="1 bottle")
    assert _row(only)["quantity"] == "1 bottle"


# ---------- two rows, no location: the most recently written one ----------


def test_add_without_a_location_merges_into_the_most_recently_written_row():
    pantry, fridge = _sauce_in_two_places(recent="fridge")
    res = tools.update_inventory("BBQ sauce", "add", quantity="1 bottle")
    assert res["item_id"] == fridge
    assert _row(fridge)["quantity"] == "2 bottles" and _row(pantry)["quantity"] == "1 bottle"


def test_the_pick_follows_recency_not_insertion_order():
    """Same two rows, the pantry one written last: the pantry row wins,
    although the fridge row has the higher id."""
    pantry, fridge = _sauce_in_two_places(recent="pantry")
    assert fridge > pantry
    res = tools.update_inventory("BBQ sauce", "add", quantity="1 bottle")
    assert res["item_id"] == pantry


def test_a_tie_on_updated_at_goes_to_the_newer_row():
    same = "2026-09-10 10:00:00"
    pantry = _insert("BBQ sauce", "1 bottle", "pantry", same)
    fridge = _insert("BBQ sauce", "1 bottle", "fridge", same)
    assert tools.update_inventory("BBQ sauce", "add", quantity="1 bottle")["item_id"] == fridge
    assert _row(pantry)["quantity"] == "1 bottle"


def test_use_without_a_location_takes_from_the_same_row_add_would():
    pantry, fridge = _sauce_in_two_places(recent="fridge")
    res = tools.update_inventory("BBQ sauce", "use", quantity="1 bottle")
    assert res["removed"] is True  # 1 bottle minus 1 bottle: the fridge row is gone
    assert _row(fridge) is None and _row(pantry)["quantity"] == "1 bottle"


def test_set_without_a_location_lands_on_the_same_row_too():
    pantry, fridge = _sauce_in_two_places(recent="fridge")
    res = tools.update_inventory("BBQ sauce", "set", quantity="3 bottles")
    assert res["item_id"] == fridge
    assert _row(fridge)["quantity"] == "3 bottles" and _row(pantry)["quantity"] == "1 bottle"


def test_add_then_use_agree_on_one_row():
    """The add touches the row it merged into, which makes it the most
    recent — so the "used some" that follows comes off the same row, and
    the other place is never involved."""
    pantry, fridge = _sauce_in_two_places(recent="pantry")
    tools.update_inventory("BBQ sauce", "add", quantity="1 bottle")
    assert _row(pantry)["quantity"] == "2 bottles"
    tools.update_inventory("BBQ sauce", "use", quantity="1 bottle")
    assert _row(pantry)["quantity"] == "1 bottle" and _row(fridge)["quantity"] == "1 bottle"


# ---------- a location hint still wins outright ----------


def test_a_location_hint_picks_that_place_whatever_was_written_last():
    pantry, fridge = _sauce_in_two_places(recent="fridge")
    assert tools.update_inventory("BBQ sauce", "add", quantity="1 bottle", location="pantry")["item_id"] == pantry
    tools.update_inventory("BBQ sauce", "use", quantity="1 bottle", location="pantry")
    assert _row(pantry)["quantity"] == "1 bottle" and _row(fridge)["quantity"] == "1 bottle"


def test_a_location_with_no_row_there_is_still_a_fresh_row():
    pantry, fridge = _sauce_in_two_places()
    res = tools.update_inventory("BBQ sauce", "add", quantity="1 bottle", location="freezer")
    assert res["item_id"] not in (pantry, fridge)
    assert _row(res["item_id"])["location"] == "freezer"


# ---------- the grocery re-tick still restores the right row ----------


def test_the_grocery_tick_merges_into_the_recent_row_and_the_untick_restores_it_by_id():
    """The tick's receipt records the inventory row's id; the untick puts
    THAT row back even after the other place has been edited — and become
    the most recently written row — in between. A name lookup at untick
    time would have hit the wrong one."""
    pantry, fridge = _sauce_in_two_places(recent="fridge")
    line = tools.add_grocery_item("BBQ sauce", quantity="1 bottle", category="pantry")["item_id"]
    tools.mark_grocery_item(line, "purchased")
    assert _row(fridge)["quantity"] == "2 bottles" and _row(pantry)["quantity"] == "1 bottle"
    tools.update_inventory("BBQ sauce", "set", quantity="4 bottles", location="pantry")  # the pantry row is now the newest
    res = tools.mark_grocery_item(line, "needed")
    assert res["inventory_restored"] is True
    assert _row(fridge)["quantity"] == "1 bottle"
    assert _row(pantry)["quantity"] == "4 bottles"
