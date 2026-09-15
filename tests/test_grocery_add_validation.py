"""
Loop Board (LOW, Bug, Phase 0): the grocery list API accepted blank and
unbounded item names -- a stray tap (e.g. holding a key down) or a
mis-parsed scanned receipt / chat line could leave a blank or enormous row
on the list forever, since nothing could ever match an empty name to tick
or clear it. Reproduced on main: POST /api/grocery-list/add with
{"item": "   "} returned 200, and a 5,000-character name did too.

The fix lives in tools.add_grocery_item (app/tools/grocery.py) so every
caller benefits -- the direct-add route, the chat tools, and the
scan-review path all funnel through it -- plus a one-off cleanup
(app/db.py's _delete_blank_grocery_items) for any blank rows a database
already has, and the route (app/main.py's /api/grocery-list/add) turning
the tool's ValueError into a plain 400.

Follow-up (same day): add_grocery_item raising on a blank name is right
for a person typing on the list, but three other callers feed it names
they don't control -- an AI-drafted recipe's ingredient list
(app/tools/recipes.py, two call sites: the per-recipe ingredient loop and
WeekGroceryBuffer.flush()) and a household's own staples
(app/tools/staples.py's sync_due_staples). A blank ingredient there used
to become a ghost row (the original bug); now, unguarded, it would raise
ValueError and 500 the whole plan approval or list read over one bad
line -- worse than the ghost row. Those three sites now skip a blank/
whitespace-only item (logged at debug) instead of calling
add_grocery_item at all. test_recipe_with_one_blank_ingredient_still_
adds_the_rest below covers the recipe-ingredient path end to end.
"""
from __future__ import annotations

import datetime

import pytest

from app import db, tools
from app.db import get_conn
from app.tools._shared import household_id
from app.tools.grocery import MAX_ITEM_NAME_LENGTH, MAX_QUANTITY_LENGTH


# ---------------------------------------------------------------------------
# tools.add_grocery_item -- the shared validation every caller gets
# ---------------------------------------------------------------------------

def test_blank_name_is_rejected():
    with pytest.raises(ValueError):
        tools.add_grocery_item("")


def test_whitespace_only_name_is_rejected():
    with pytest.raises(ValueError):
        tools.add_grocery_item("   ")


def test_blank_name_error_is_plain_pomona_voice():
    # Calm and plain, no exclamation mark (DESIGN_SYSTEM.md §8's
    # calm-in-trouble rule) -- and short enough to read as a sentence,
    # not a stack trace.
    with pytest.raises(ValueError) as excinfo:
        tools.add_grocery_item("\t\n  ")
    assert str(excinfo.value) == "I need a name for that."


def test_overlong_name_is_capped_not_rejected():
    long_name = "a" * 5000
    result = tools.add_grocery_item(long_name)
    assert len(result["item"]) == MAX_ITEM_NAME_LENGTH
    assert result["item"] == "a" * MAX_ITEM_NAME_LENGTH

    row = get_conn().execute(
        "SELECT item FROM grocery_items WHERE id = ?", (result["item_id"],)
    ).fetchone()
    assert len(row["item"]) == MAX_ITEM_NAME_LENGTH


def test_overlong_quantity_is_capped():
    long_qty = "1 " + ("x" * 100)
    result = tools.add_grocery_item("Flour", quantity=long_qty)
    assert len(result["quantity"]) <= MAX_QUANTITY_LENGTH


def test_a_name_that_only_needs_trimming_still_merges_like_today():
    # Existing merge behaviour (app/tools/grocery.py's _merge_target):
    # a second add of the same item consolidates onto the one row
    # instead of creating a duplicate line. Trimming the name first must
    # not break that -- the merge key already ignores surrounding
    # whitespace, so this only fails if trimming changed where the
    # comparison happens.
    first = tools.add_grocery_item("Milk", quantity="1")
    assert first["merged"] is False

    second = tools.add_grocery_item("  Milk  ", quantity="1")
    assert second["merged"] is True
    assert second["item_id"] == first["item_id"]
    # The row keeps the name it already had (see add_grocery_item's own
    # comment on why: the shopper sees the line as it already reads).
    assert second["item"] == "Milk"

    rows = [r for r in tools.list_grocery_list() if r["id"] == first["item_id"]]
    assert len(rows) == 1


# ---------------------------------------------------------------------------
# POST /api/grocery-list/add -- the route translates ValueError to a 400
# ---------------------------------------------------------------------------

def test_route_rejects_blank_name_with_400(signed_in):
    res = signed_in.post("/api/grocery-list/add", json={"item": "   "})
    assert res.status_code == 400
    assert res.json()["detail"] == "I need a name for that."


def test_route_still_accepts_a_real_item(signed_in):
    res = signed_in.post("/api/grocery-list/add", json={"item": "Bananas"})
    assert res.status_code == 200
    assert res.json()["item"] == "Bananas"


def test_route_caps_an_overlong_name(signed_in):
    res = signed_in.post("/api/grocery-list/add", json={"item": "b" * 5000})
    assert res.status_code == 200
    assert len(res.json()["item"]) == MAX_ITEM_NAME_LENGTH


# ---------------------------------------------------------------------------
# app/db.py's one-off cleanup of pre-existing blank rows
# ---------------------------------------------------------------------------

def _blank_row_count() -> int:
    return get_conn().execute(
        "SELECT COUNT(*) AS c FROM grocery_items WHERE household_id = ? AND TRIM(item) = ''",
        (household_id(),),
    ).fetchone()["c"]


def test_migration_deletes_pre_existing_blank_rows():
    # Simulate a database written before add_grocery_item trimmed and
    # rejected a blank name: insert directly, bypassing the tool.
    conn = get_conn()
    conn.execute(
        "INSERT INTO grocery_items (household_id, item) VALUES (?, '   ')",
        (household_id(),),
    )
    conn.execute(
        "INSERT INTO grocery_items (household_id, item) VALUES (?, '')",
        (household_id(),),
    )
    conn.execute(
        "INSERT INTO grocery_items (household_id, item) VALUES (?, 'Eggs')",
        (household_id(),),
    )
    conn.commit()
    conn.close()
    assert _blank_row_count() == 2

    conn = get_conn()
    db._delete_blank_grocery_items(conn)
    conn.commit()
    conn.close()

    assert _blank_row_count() == 0
    remaining = [r["item"] for r in tools.list_grocery_list()]
    assert "Eggs" in remaining


def test_migration_cleanup_is_idempotent():
    conn = get_conn()
    conn.execute(
        "INSERT INTO grocery_items (household_id, item) VALUES (?, '  ')",
        (household_id(),),
    )
    conn.commit()
    conn.close()

    conn = get_conn()
    db._delete_blank_grocery_items(conn)
    db._delete_blank_grocery_items(conn)  # running twice changes nothing further
    conn.commit()
    conn.close()

    assert _blank_row_count() == 0


# ---------------------------------------------------------------------------
# Callers that feed add_grocery_item names they don't control -- an
# AI-drafted recipe's ingredients, a household's staples -- must not let a
# single blank name turn into a raised ValueError and a 500 mid-approval /
# mid-read. They skip that one line instead of calling add_grocery_item.
# ---------------------------------------------------------------------------

def _monday(offset_weeks: int = 0) -> str:
    today = datetime.date.today()
    monday = today - datetime.timedelta(days=today.weekday())
    return (monday + datetime.timedelta(days=7 * offset_weeks)).isoformat()


def test_recipe_with_one_blank_ingredient_still_adds_the_rest():
    # An AI-drafted recipe with one ingredient whose name came back blank
    # (or whitespace-only) -- approving the week must not raise, and the
    # recipe's other, real ingredients still land on the list.
    tools.add_recipe("Mystery Ingredient Stew", ingredients=[
        {"item": "Chicken thighs", "qty": "2 lb", "category": "meat/seafood"},
        {"item": "   ", "qty": "1 cup", "category": "pantry"},
        {"item": "Onion", "qty": "2", "category": "produce"},
    ])
    plan_id = tools.create_weekly_plan(_monday())["weekly_plan_id"]
    day = tools._week_dates(_monday())[0]
    tools.plan_meal(day, "Mystery Ingredient Stew", slot="dinner", weekly_plan_id=plan_id)

    # Must not raise -- this is the regression: add_grocery_item's own
    # ValueError on a blank name used to propagate straight out of
    # approval.
    tools.approve_weekly_plan(plan_id, approved_by="Emily")

    names = [r["item"] for r in tools.list_grocery_list()]
    assert "Chicken thighs" in names
    assert "Onion" in names
    assert not any(not n.strip() for n in names)
