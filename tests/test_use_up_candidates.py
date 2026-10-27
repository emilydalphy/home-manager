"""
The one "what needs using up" list (Loop Board B1/B2, 2026-10-26) and the
one "freeze this row" door (B3; the Kitchen tab reuses it).

Every test says whether it is a CATCH (red on main — the thing did not
exist) or a GUARD.
"""
from __future__ import annotations

import datetime

from conftest import household_today

from app import tools
from app.db import get_conn
from app.tools import use_up


def _row(item, *, days=None, location="fridge", category="produce", source="chat",
         frozen_days_ago=None, quantity="1"):
    exp = None if days is None else (household_today() + datetime.timedelta(days=days)).isoformat()
    frozen_on = None if frozen_days_ago is None else (household_today() - datetime.timedelta(days=frozen_days_ago)).isoformat()
    conn = get_conn()
    cur = conn.execute(
        "INSERT INTO inventory_items (household_id, item, quantity, source, expiration_date, category, location, frozen_on) "
        "VALUES (1, ?, ?, ?, ?, ?, ?, ?)",
        (item, quantity, source, exp, category, location, frozen_on),
    )
    conn.commit()
    conn.close()
    return cur.lastrowid


def test_going_off_soonest_first_then_freezer_oldest_first():
    """CATCH — there was no single list."""
    _row("Feta", days=5, category="dairy")
    _row("Baby spinach", days=2)
    _row("Sweet potatoes", days=4)
    _row("Ground beef", location="freezer", category="meat/seafood", frozen_days_ago=20)
    _row("Chicken thighs", location="freezer", category="meat/seafood", frozen_days_ago=60)
    items = use_up.candidates()
    assert [c["item"] for c in items] == ["Baby spinach", "Sweet potatoes", "Feta", "Chicken thighs", "Ground beef"]
    spinach = items[0]
    turns = household_today() + datetime.timedelta(days=2)
    assert spinach["kind"] == "going_off"
    assert spinach["turns_on"] == turns.isoformat()
    assert spinach["use_by"] == (turns - datetime.timedelta(days=1)).isoformat()
    assert spinach["label"].startswith("Baby spinach · ")
    assert items[3]["kind"] == "freezer" and items[3]["label"] == "Chicken thighs · freezer"


def test_staples_spices_past_dates_far_dates_and_fresh_freezer_rows_are_left_out():
    """CATCH — the list keeps to what genuinely needs using this week."""
    conn = get_conn()
    conn.execute("INSERT INTO staples (household_id, item, next_due_at) VALUES (1, 'Milk', '2099-01-01')")
    conn.commit()
    conn.close()
    _row("Milk", days=2, category="dairy")
    _row("Smoked paprika", days=2, category="pantry", location="pantry")
    _row("Old yogurt", days=-1, category="dairy")
    _row("Rice", days=200, category="pantry", location="pantry")
    _row("No date carrots")
    _row("Peas", location="freezer", category="frozen", frozen_days_ago=3)
    _row("Chili (cooked)", location="freezer", category="frozen", frozen_days_ago=40, source="night_off")
    keep = _row("Cilantro", days=3)
    assert [c["id"] for c in use_up.candidates()] == [keep]


def test_selected_drops_what_was_switched_off_on_the_intake():
    """CATCH — save_week_intake had no use_up_off, so nothing could be switched off."""
    monday = household_today() + datetime.timedelta(days=(7 - household_today().weekday()) % 7 or 7)
    week = monday.isoformat()
    lead = (monday - household_today()).days
    a = _row("Baby spinach", days=lead + 2)
    b = _row("Feta", days=lead + 3, category="dairy")
    saved = tools.save_week_intake(week, use_up_off=[b])
    assert saved["use_up_off"] == [b]
    # A later save of another half keeps it (append-only inherit).
    again = tools.save_week_intake(week, moods=["comfort_food"])
    assert again["use_up_off"] == [b] and again["revision"] == saved["revision"] + 1
    ids = [c["id"] for c in use_up.selected(again, week, 7)]
    assert a in ids and b not in ids


def test_freeze_item_moves_it_dated_today_and_undo_puts_it_back():
    """CATCH — there was no freeze door with an undo."""
    item_id = _row("Half a cabbage", days=2)
    before = get_conn().execute("SELECT expiration_date FROM inventory_items WHERE id = ?", (item_id,)).fetchone()[0]
    out = tools.freeze_item(item_id)
    assert out["location"] == "freezer" and out["frozen_on"] == household_today().isoformat()
    assert out["item"] == "Half a cabbage"
    row = get_conn().execute("SELECT location, frozen_on FROM inventory_items WHERE id = ?", (item_id,)).fetchone()
    assert row["location"] == "freezer" and row["frozen_on"] == household_today().isoformat()
    back = tools.unfreeze_item(out["undo"])
    assert back["undone"] is True
    row = get_conn().execute("SELECT location, frozen_on, expiration_date FROM inventory_items WHERE id = ?", (item_id,)).fetchone()
    assert row["location"] == "fridge" and row["frozen_on"] is None and row["expiration_date"] == before


def test_freezing_a_freezer_row_is_refused():
    """GUARD."""
    item_id = _row("Peas", location="freezer", category="frozen", frozen_days_ago=1)
    try:
        tools.freeze_item(item_id)
    except tools.CannotFreeze:
        return
    raise AssertionError("expected CannotFreeze")


def test_a_frozen_item_comes_back_as_a_freezer_candidate_later(frozen_today):
    """CATCH — frozen food is offered again, oldest first, once it has sat."""
    item_id = _row("Half a cabbage", days=2)
    tools.freeze_item(item_id)
    assert item_id not in [c["id"] for c in use_up.candidates()]
    conn = get_conn()
    old = (household_today() - datetime.timedelta(days=use_up.FREEZER_AFTER_DAYS)).isoformat()
    conn.execute("UPDATE inventory_items SET frozen_on = ? WHERE id = ?", (old, item_id))
    conn.commit()
    conn.close()
    got = [c for c in use_up.candidates() if c["id"] == item_id]
    assert got and got[0]["kind"] == "freezer"


def test_freeze_and_side_tables():
    """GUARD — the B3 actions table."""
    assert use_up.side_ok("Cilantro") and use_up.side_ok("Baby spinach")
    assert use_up.freeze_ok("Half a cabbage") and not use_up.side_ok("Half a cabbage")
    assert not use_up.freeze_ok("Cucumber") and not use_up.freeze_ok("Eggs")
    assert not use_up.freeze_ok("Ground beef", "freezer")


def test_http_use_up_and_freeze_routes(signed_in):
    """CATCH — the routes B1 and the Kitchen tab call."""
    monday = household_today() + datetime.timedelta(days=(7 - household_today().weekday()) % 7 or 7)
    item_id = _row("Baby spinach", days=(monday - household_today()).days + 2)
    r = signed_in.get(f"/api/week/{monday.isoformat()}/use-up")
    assert r.status_code == 200
    assert [i["id"] for i in r.json()["items"]] == [item_id] and r.json()["off"] == []
    r = signed_in.post(f"/api/week/{monday.isoformat()}/intake", json={"use_up_off": [item_id]})
    assert r.status_code == 200 and r.json()["use_up_off"] == [item_id]
    assert signed_in.get(f"/api/week/{monday.isoformat()}/use-up").json()["off"] == [item_id]
    r = signed_in.post(f"/api/inventory/{item_id}/freeze")
    assert r.status_code == 200 and r.json()["location"] == "freezer"
    assert signed_in.post(f"/api/inventory/{item_id}/freeze").status_code == 400
    r = signed_in.post("/api/inventory/unfreeze", json={"undo": r.json()["undo"]})
    assert r.status_code == 200 and r.json()["undone"] is True
