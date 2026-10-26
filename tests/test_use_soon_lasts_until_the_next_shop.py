"""
A night off's "Use the … soon" note lasts until the next shop (Loop Board
card 4021f4c0523181cb9562c77d55287b76; Emily 2026-10-26 "go with your
pick"). Before, it stayed until ticked in Cook, and the morning text and
chat repeated it every day.
"""
from __future__ import annotations

from app import tools
from app.db import get_conn
from app.tools import attention, tonight


def _note(summary="Use the Ground turkey soon — Turkey Chili came off the plan.", detail=None):
    res = attention.add_attention_item(
        tonight.USE_SOON_KIND, summary, detail or {"items": ["Ground turkey"], "dish": "Turkey Chili"},
    )
    conn = get_conn()
    conn.execute("UPDATE attention_items SET created_at = '2026-10-20 12:00:00' WHERE id = ?", (res["id"],))
    conn.commit()
    conn.close()
    return res["id"]


def _pending_ids():
    return [i["id"] for i in attention.get_attention_items() if i["kind"] == tonight.USE_SOON_KIND]


def test_the_note_stays_until_a_shop(client):
    note = _note()
    assert note in _pending_ids()
    assert note in _pending_ids(), "reading it again doesn't settle it"


def test_the_next_shop_settles_it(client):
    note = _note()
    tools.close_shopping_trip("Costco", item_count=4)

    assert note not in _pending_ids()
    conn = get_conn()
    status = conn.execute("SELECT status FROM attention_items WHERE id = ?", (note,)).fetchone()["status"]
    conn.close()
    assert status == "resolved", "resolved, not deleted"


def test_a_note_written_after_the_shop_stays(client):
    tools.close_shopping_trip("Costco", item_count=4)
    conn = get_conn()
    conn.execute("UPDATE shopping_trips SET finished_at = '2026-10-19 12:00:00'")
    conn.commit()
    conn.close()
    note = _note()
    assert note in _pending_ids()


def test_a_frozen_portions_note_is_not_about_the_shop(client):
    note = _note("Turkey Chili has been in the freezer 4 weeks — worth eating soon.",
                 {"items": ["Turkey Chili"], "dish": "Turkey Chili", "inventory_item_id": 999})
    tools.close_shopping_trip("Costco", item_count=4)
    # Called directly: the freezer sweep on get_attention_items may settle
    # it for its own reasons (no such portion); what matters here is that
    # this sweep's rule leaves it alone.
    assert tonight.expire_use_soon_after_shop() == 0
    conn = get_conn()
    status = conn.execute("SELECT status FROM attention_items WHERE id = ?", (note,)).fetchone()["status"]
    conn.close()
    assert status == "pending"
