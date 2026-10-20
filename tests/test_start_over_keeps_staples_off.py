"""
"Start over" → the grocery list stays cleared (defect hunt, 2026-10-20).

Driven over HTTP on a throwaway database: two staples due (Milk, Bread),
the Start over dialog's preview said "Removes 2 items still to buy", the
reset answered removed_count 2 — and the very next read of the list showed
Milk and Bread again. tools.clear_grocery_list hard-deleted the staples'
lines, and every read of the list runs staples.sync_due_staples, which
found each staple still due with nothing by its name on the list and put
it straight back. The chat's "wipe the list" goes through the same
function and had the same boomerang.
"""
from __future__ import annotations

from app import tools
from app.db import get_conn


def _shop_items(client) -> list[str]:
    res = client.get("/api/grocery-list")
    assert res.status_code == 200, res.text
    return sorted(i["item"] for s in res.json()["sections"] for i in s["items"])


def _staple_rhythm(item: str) -> tuple:
    conn = get_conn()
    row = conn.execute(
        "SELECT next_due_at, skip_streak, paused FROM staples WHERE item = ?", (item,)
    ).fetchone()
    conn.close()
    return tuple(row)


def test_start_over_clears_staple_lines_and_the_list_stays_clear(signed_in):
    client = signed_in
    tools.add_staple("Milk", category="dairy", running_low=True)
    tools.add_staple("Bread", category="bakery", running_low=True)
    tools.add_grocery_item("Lemons", quantity="2")
    assert _shop_items(client) == ["Bread", "Lemons", "Milk"]
    rhythm_before = _staple_rhythm("Milk")

    preview = client.get("/api/reset/preview").json()
    assert preview["grocery_count"] == 3
    res = client.post("/api/reset", json={"grocery_list": True})
    assert res.status_code == 200, res.text
    assert res.json()["grocery_list"]["removed_count"] == 3

    # The list read is what used to put the staples straight back.
    assert _shop_items(client) == []
    assert _shop_items(client) == []
    assert client.get("/api/reset/preview").json()["grocery_count"] == 0
    # A wipe is not "not this trip": the staple's rhythm is untouched, so
    # no skip counts toward the auto-pause.
    assert _staple_rhythm("Milk") == rhythm_before
    # The ordinary line is gone outright, as before.
    conn = get_conn()
    assert conn.execute("SELECT 1 FROM grocery_items WHERE item = 'Lemons'").fetchone() is None
    conn.close()


def test_chat_wipe_of_everything_keeps_a_staple_off_too(signed_in):
    client = signed_in
    tools.add_staple("Dish soap", category="household", running_low=True)
    assert _shop_items(client) == ["Dish soap"]
    line = tools.list_grocery_list(status="needed")[0]
    tools.mark_grocery_item(line["id"], "in_cart")

    assert tools.clear_grocery_list(status="all")["removed_count"] == 1
    assert _shop_items(client) == []
    assert tools.list_grocery_list(status="in_cart") == []


def test_a_staple_line_already_bought_is_deleted_like_anything_else(signed_in):
    client = signed_in
    tools.add_staple("Eggs", category="dairy", running_low=True)
    assert _shop_items(client) == ["Eggs"]
    line = tools.list_grocery_list(status="needed")[0]
    tools.mark_grocery_item(line["id"], "purchased")

    assert tools.clear_grocery_list(status="purchased")["removed_count"] == 1
    conn = get_conn()
    assert conn.execute("SELECT COUNT(*) FROM grocery_items WHERE item = 'Eggs'").fetchone()[0] == 0
    conn.close()
