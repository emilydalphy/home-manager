"""
Un-tick and re-tick a bought grocery line and the kitchen holds ONE of it.

THE BUG (Loop Board, Phase 0 — Beta-ready). grocery.mark_grocery_item adds
a purchased line to tracked inventory on every transition INTO 'purchased'.
Its 2026-09-11 no-op guard compares the incoming status with the row's
current one, which catches the offline replay it was written for
(purchased -> purchased, the request landed but the reply didn't) and
nothing else: purchased -> needed -> purchased passes the guard because
the status genuinely changed in between. Measured against the code as it
stood:

    Eggs, 12 -> tick -> 12 -> untick -> 12 -> tick again -> 24

Same class as the cook tick one door over (tests/test_cook_tick_double_
depletes.py, meal_plan_entries.inventory_depleted_at): status forgets, so
the memory has to be its own column. Here it is
grocery_items.inventory_added_at, claimed inside the same BEGIN IMMEDIATE
transaction that flips the status, so two "purchased" posts landing at the
same instant cannot both add.

UN-TICKING DOES PUT IT BACK — when, and only when, the kitchen row is
still exactly as the tick left it. That is the opposite call from the cook
tick, and deliberately: a purchase KNOWS what it wrote. The tick records a
receipt (grocery_items.inventory_receipt_json — which inventory row, whether
it was a fresh row or a merge into stock already there, and the row's
fields before and after the write). An untick checks the row still reads
what the receipt says the write left it at (quantity and updated_at both;
every inventory writer bumps updated_at) and then does the exact inverse:
deletes a row the tick created, or puts a merged row's quantity, category,
source and expiry back to what they were. If the household has touched the
row since — used some, edited it, removed it — nothing is guessed: the row
is left alone, the result says inventory_restored: False, and the stamp
STAYS so a later re-tick does not add a second helping on top of what is
already there. Either way the kitchen never holds two of one purchase.
"""
from __future__ import annotations

import json
import re
import threading
from pathlib import Path

import pytest

from app import tools
from app.db import _MIGRATIONS, _run_migrations, get_conn

REPO = Path(__file__).resolve().parent.parent


def _eggs():
    rows = [r for r in tools.get_inventory() if r["item"].lower() == "eggs"]
    assert len(rows) <= 1, rows
    return rows[0] if rows else None


def _qty():
    row = _eggs()
    return row["quantity"] if row else None


def _line(item_id):
    conn = get_conn()
    row = conn.execute(
        "SELECT status, inventory_added_at, inventory_receipt_json FROM grocery_items WHERE id = ?",
        (item_id,),
    ).fetchone()
    conn.close()
    return row


def _bought_eggs(quantity="12"):
    item = tools.add_grocery_item("Eggs", quantity=quantity, category="dairy")
    tools.mark_grocery_item(item["item_id"], "purchased")
    return item["item_id"]


def _touch_updated_at(inventory_id, when="2000-01-01 00:00:00"):
    """Move a row's updated_at so a same-second edit still reads as 'touched'."""
    conn = get_conn()
    conn.execute("UPDATE inventory_items SET updated_at = ? WHERE id = ?", (when, inventory_id))
    conn.commit()
    conn.close()


# ---------- the reproduction ----------

def test_tick_untick_retick_puts_eggs_in_the_kitchen_once():
    """The card's sequence, exactly. RED on the status-only guard: 24."""
    item_id = _bought_eggs()
    assert _qty() == "12"
    tools.mark_grocery_item(item_id, "needed")
    tools.mark_grocery_item(item_id, "purchased")
    assert _qty() == "12"


def test_purchased_to_in_cart_to_purchased_also_adds_once():
    """The other way back out of 'purchased' the route accepts."""
    item_id = _bought_eggs()
    tools.mark_grocery_item(item_id, "in_cart")
    tools.mark_grocery_item(item_id, "purchased")
    assert _qty() == "12"


def test_a_stamp_survives_however_many_times_the_line_is_toggled():
    item_id = _bought_eggs()
    for _ in range(5):
        tools.mark_grocery_item(item_id, "needed")
        tools.mark_grocery_item(item_id, "purchased")
    assert _qty() == "12"


# ---------- the replay path the old guard was written for keeps working ----------

def test_replaying_purchased_is_still_a_no_op_that_says_so():
    """tests/test_grocery_offline.py pins this too; here so the two paths
    sit side by side. A no-op must report unchanged, not add, not restore."""
    item_id = _bought_eggs()
    again = tools.mark_grocery_item(item_id, "purchased")
    assert again == {"item_id": item_id, "status": "purchased", "unchanged": True}
    assert _qty() == "12"


def test_replaying_needed_after_an_untick_is_a_no_op_too():
    item_id = _bought_eggs()
    tools.mark_grocery_item(item_id, "needed")
    assert _eggs() is None
    again = tools.mark_grocery_item(item_id, "needed")
    assert again.get("unchanged") is True
    assert _eggs() is None


# ---------- the un-tick puts it back, exactly, when it can prove what it added ----------

def test_untick_of_a_fresh_row_deletes_that_row_and_says_so():
    """Nothing by that name in the kitchen before the tick, so the tick's
    row is the whole of the evidence — the untick removes it outright."""
    item_id = _bought_eggs()
    res = tools.mark_grocery_item(item_id, "needed")
    assert res["status"] == "needed"
    assert res["inventory_restored"] is True
    assert _eggs() is None


def test_untick_then_retick_adds_it_back_once():
    """The stamp is cleared by a successful restore, so the re-tick is a
    first tick again: one helping, not zero and not two."""
    item_id = _bought_eggs()
    tools.mark_grocery_item(item_id, "needed")
    res = tools.mark_grocery_item(item_id, "purchased")
    assert res.get("inventory_added") is True
    assert _qty() == "12"
    assert _line(item_id)["inventory_added_at"] is not None


def test_untick_of_a_merge_puts_the_existing_stock_back_as_it_was():
    """Six eggs already in the kitchen from chat, in the fridge, with a
    hand-set expiry. Buying twelve merges to 18 and stamps the row
    grocery_checkoff. The untick puts back exactly 6 / chat / that expiry —
    not "18 minus 12", the recorded before-state."""
    tools.update_inventory("Eggs", "add", quantity="6", category="dairy", expiration_date="2030-01-01", location="fridge")
    before = _eggs()
    assert before["quantity"] == "6" and before["source"] == "chat"
    item_id = _bought_eggs()
    merged = _eggs()
    assert merged["id"] == before["id"]
    assert merged["quantity"] == "18"
    assert merged["source"] == "grocery_checkoff"

    res = tools.mark_grocery_item(item_id, "needed")
    assert res["inventory_restored"] is True
    after = _eggs()
    assert after["id"] == before["id"]
    assert (after["quantity"], after["source"], after["category"], after["expiration_date"], after["location"]) == (
        "6", "chat", "dairy", "2030-01-01", "fridge",
    )


def test_untick_of_a_merge_puts_back_a_category_the_tick_overwrote():
    """The grocery line's category overwrites the kitchen row's on merge
    (see _add_to_inventory). A 'pantry' row bought under 'other' must come
    back 'pantry', and its 'pantry'-estimated expiry must not be replaced
    by an 'other' guess either."""
    tools.update_inventory("Rice", "add", quantity="1 kg", category="pantry")
    was = next(r for r in tools.get_inventory() if r["item"] == "Rice")
    item = tools.add_grocery_item("Rice", quantity="1 kg", category="other")
    tools.mark_grocery_item(item["item_id"], "purchased")
    now = next(r for r in tools.get_inventory() if r["item"] == "Rice")
    assert now["quantity"] == "2 kg" and now["category"] == "other"

    tools.mark_grocery_item(item["item_id"], "needed")
    back = next(r for r in tools.get_inventory() if r["item"] == "Rice")
    assert (back["quantity"], back["category"], back["expiration_date"], back["source"]) == (
        was["quantity"], was["category"], was["expiration_date"], was["source"],
    )


def test_merge_untick_retick_lands_on_the_merged_total_once():
    tools.update_inventory("Eggs", "add", quantity="6", category="dairy")
    item_id = _bought_eggs()
    assert _qty() == "18"
    tools.mark_grocery_item(item_id, "needed")
    assert _qty() == "6"
    tools.mark_grocery_item(item_id, "purchased")
    assert _qty() == "18"


def test_a_line_with_no_quantity_is_still_tracked_and_still_reversible():
    """A hand-added "Eggs" with no amount goes in as a row with a blank
    quantity; the untick still knows it created that row."""
    item = tools.add_grocery_item("Eggs", category="dairy")
    tools.mark_grocery_item(item["item_id"], "purchased")
    assert _eggs() is not None and _qty() == ""
    tools.mark_grocery_item(item["item_id"], "needed")
    assert _eggs() is None


def test_the_receipt_is_written_with_the_stamp_and_cleared_with_it():
    item_id = _bought_eggs()
    line = _line(item_id)
    assert line["inventory_added_at"] is not None
    receipt = json.loads(line["inventory_receipt_json"])
    assert receipt["fresh"] is True
    assert receipt["inventory_id"] == _eggs()["id"]
    assert receipt["after"]["quantity"] == "12"
    tools.mark_grocery_item(item_id, "needed")
    line = _line(item_id)
    assert line["inventory_added_at"] is None
    assert line["inventory_receipt_json"] is None


# ---------- ...and leaves the kitchen alone when it cannot ----------

def test_untick_after_some_were_used_leaves_the_row_and_says_not_restored():
    """Bought 12, used 4, then unticked. The 8 is the household's own
    number now, so it stays; the stamp stays with it, so a re-tick does not
    put 12 more on top."""
    item_id = _bought_eggs()
    tools.update_inventory("Eggs", "use", quantity="4")
    assert _qty() == "8"
    res = tools.mark_grocery_item(item_id, "needed")
    assert res["inventory_restored"] is False
    assert _qty() == "8"
    assert _line(item_id)["inventory_added_at"] is not None
    tools.mark_grocery_item(item_id, "purchased")
    assert _qty() == "8"


def test_untick_after_the_row_was_removed_restores_nothing_and_retick_adds_nothing():
    """"We're out of eggs" deleted the row. The eggs the tick put in have
    been eaten, so an untick has nothing to take back and a re-tick would
    be a second helping of the same purchase."""
    item_id = _bought_eggs()
    tools.remove_inventory_item(_eggs()["id"])
    res = tools.mark_grocery_item(item_id, "needed")
    assert res["inventory_restored"] is False
    assert _eggs() is None
    tools.mark_grocery_item(item_id, "purchased")
    assert _eggs() is None


def test_untick_after_a_stepper_nudge_leaves_the_row_alone():
    item_id = _bought_eggs()
    tools.step_inventory_quantity(_eggs()["id"], -1)
    assert _qty() == "11"
    res = tools.mark_grocery_item(item_id, "needed")
    assert res["inventory_restored"] is False
    assert _qty() == "11"


def test_a_same_number_written_by_hand_is_not_mistaken_for_ours():
    """Bought 12, set to 6, set back to 12 — the quantity reads what the
    tick left, but updated_at says somebody has been here. Leave it."""
    item_id = _bought_eggs()
    tools.update_inventory("Eggs", "set", quantity="6")
    tools.update_inventory("Eggs", "set", quantity="12")
    _touch_updated_at(_eggs()["id"])  # the two sets above may land in the tick's own second
    res = tools.mark_grocery_item(item_id, "needed")
    assert res["inventory_restored"] is False
    assert _qty() == "12"


def test_untick_of_a_merge_the_household_has_used_from_leaves_it():
    tools.update_inventory("Eggs", "add", quantity="6", category="dairy")
    item_id = _bought_eggs()
    tools.update_inventory("Eggs", "use", quantity="10")
    assert _qty() == "8"
    res = tools.mark_grocery_item(item_id, "needed")
    assert res["inventory_restored"] is False
    assert _qty() == "8"
    tools.mark_grocery_item(item_id, "purchased")
    assert _qty() == "8"


def test_a_line_bought_before_the_column_existed_is_not_restored_but_is_not_doubled():
    """A row already 'purchased' when this deployed has a NULL stamp and no
    receipt (nothing is backfilled). An untick there has nothing to prove
    with, so it leaves the kitchen alone. The re-tick DOES add once, which
    is the bounded one-off the not-backfilling buys — the same trade the
    cook-tick column made."""
    item = tools.add_grocery_item("Eggs", quantity="12", category="dairy")
    conn = get_conn()
    conn.execute("UPDATE grocery_items SET status = 'purchased' WHERE id = ?", (item["item_id"],))
    conn.commit()
    conn.close()
    tools.update_inventory("Eggs", "add", quantity="12", category="dairy")  # the add that old code did
    res = tools.mark_grocery_item(item["item_id"], "needed")
    assert res["inventory_restored"] is False
    assert _qty() == "12"
    tools.mark_grocery_item(item["item_id"], "purchased")
    assert _qty() == "24"  # once more, then never again:
    tools.mark_grocery_item(item["item_id"], "needed")
    assert _qty() == "12"
    tools.mark_grocery_item(item["item_id"], "purchased")
    assert _qty() == "24"


# ---------- two hands at once ----------

def test_two_purchased_posts_at_the_same_instant_add_once():
    """The status route is a sync def, so Starlette runs it in a
    threadpool and two panels (or a retry racing its original) really do
    post together. The claim is taken inside the BEGIN IMMEDIATE that
    flips the status, so the loser sees 'purchased' already and no-ops.
    Twenty trials; the pre-fix shape doubled on most of them."""
    doubled = 0
    for trial in range(20):
        name = f"Trial{trial}"
        item = tools.add_grocery_item(name, quantity="12", category="dairy")
        barrier = threading.Barrier(2)
        errors = []

        def go():
            try:
                barrier.wait(timeout=5)
                tools.mark_grocery_item(item["item_id"], "purchased")
            except Exception as e:  # pragma: no cover - reported below
                errors.append(e)

        threads = [threading.Thread(target=go) for _ in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)
        assert not errors, errors
        rows = [r for r in tools.get_inventory() if r["item"] == name]
        assert len(rows) == 1, rows
        if rows[0]["quantity"] != "12":
            doubled += 1
    assert doubled == 0


# ---------- the route and the chat tool ----------

def test_the_status_route_carries_the_same_answer(signed_in):
    item = tools.add_grocery_item("Eggs", quantity="12", category="dairy")
    url = f"/api/grocery-list/{item['item_id']}/status"
    first = signed_in.post(url, json={"status": "purchased"}).json()
    assert first.get("inventory_added") is True
    back = signed_in.post(url, json={"status": "needed"}).json()
    assert back.get("inventory_restored") is True
    again = signed_in.post(url, json={"status": "purchased"}).json()
    assert again.get("inventory_added") is True
    assert _qty() == "12"


def test_a_no_op_retick_says_it_added_nothing():
    """When the stamp holds (the restore could not run), the re-tick still
    flips the status but reports inventory_added: False so the chat agent
    does not tell the household it just put eggs in the kitchen."""
    item_id = _bought_eggs()
    tools.update_inventory("Eggs", "use", quantity="4")
    tools.mark_grocery_item(item_id, "needed")
    res = tools.mark_grocery_item(item_id, "purchased")
    assert res["status"] == "purchased"
    assert res["inventory_added"] is False


# ---------- the upgrade path ----------

ADDED_HERE = [("grocery_items", "inventory_added_at"), ("grocery_items", "inventory_receipt_json")]


def _without_column(schema: str, table: str, column: str) -> tuple[str, int]:
    start = schema.index(f"CREATE TABLE IF NOT EXISTS {table} (")
    end = schema.index(");", start)
    block = schema[start:end]
    stripped, count = re.subn(rf"^\s*{re.escape(column)}\s+[^\n]*\n", "", block, flags=re.M)
    return schema[:start] + stripped + schema[end:], count


def test_a_database_made_before_these_columns_migrates_cleanly_twice(tmp_path):
    """Derived from today's schema.sql by removing exactly the two columns
    (the pattern tests/test_chore_owner_mode.py uses, and for its reason):
    the real upgrade path is a file that has purchased rows and no
    columns, opened by this build. Nothing is backfilled — every existing
    row reads NULL, i.e. 'nothing proven', which is the truth."""
    import sqlite3

    schema = (REPO / "app" / "schema.sql").read_text(encoding="utf-8")
    for table, column in ADDED_HERE:
        assert (table, column) in [(t, c) for t, c, _ in _MIGRATIONS]
        schema, count = _without_column(schema, table, column)
        assert count == 1, f"{table}.{column} must be declared on its own line in schema.sql"
    conn = sqlite3.connect(tmp_path / "old.db")
    conn.row_factory = sqlite3.Row
    conn.executescript(schema)
    conn.execute(
        "INSERT INTO grocery_items (household_id, item, quantity, category, status) VALUES (1, 'Eggs', '12', 'dairy', 'purchased')"
    )
    conn.commit()
    _run_migrations(conn)
    _run_migrations(conn)
    conn.commit()
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(grocery_items)")}
    assert {"inventory_added_at", "inventory_receipt_json"} <= cols
    row = conn.execute("SELECT status, inventory_added_at, inventory_receipt_json FROM grocery_items").fetchone()
    conn.close()
    assert (row["status"], row["inventory_added_at"], row["inventory_receipt_json"]) == ("purchased", None, None)
