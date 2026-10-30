"""
Taking off your own line for a due staple is the staple's "not this trip"
(defect hunt, 2026-10-30). A typed "eggs · 6" is what kept the due Eggs
staple off the list (sync_due_staples counts a person's own line as cover);
Remove hard-deleted it, and the very next read of the list put "Eggs ·
1 dozen · Probably running low" back — eggs the household had just taken
off. Now the line goes the way a staple's line does: soft-removed, the
staple asked again next week, and the toast's Undo brings back their row.
"""

from __future__ import annotations

import datetime as dt

from conftest import _pin

from app import tools

START = dt.date(2026, 11, 2)  # a Monday


def on(n: int):
    return _pin(dt.datetime.combine(START + dt.timedelta(days=n), dt.time(10, 0)))


def iso(n: int) -> str:
    return (START + dt.timedelta(days=n)).isoformat()


def eggs() -> list[tuple]:
    tools.get_grocery_list_by_section("needed")
    return [(r["id"], r["quantity"]) for r in tools.list_grocery_list(status="needed")
            if r["item"].lower() == "eggs"]


def staple() -> dict:
    return next(s for s in tools.list_staples() if s["item"] == "Eggs")


def test_removing_your_own_line_for_a_due_staple_does_not_bring_the_staple_back():
    with on(0):
        tools.add_staple("Eggs", quantity="1 dozen", every_days=7)
    with on(5):
        typed = tools.add_grocery_item("eggs", quantity="6")["item_id"]
    with on(7):
        assert eggs() == [(typed, "6")]
        out = tools.remove_grocery_item(typed)
        # Was [(2, "1 dozen")]: the staple's line, straight back.
        assert eggs() == []
        assert out["staple_id"] == staple()["id"]
        assert staple()["next_due_at"] == iso(14)
        # The toast's Undo: their own row, their amount.
        tools.undo_staple_decision(out["staple_id"])
        assert eggs() == [(typed, "6")]
        assert staple()["skip_streak"] == 0
    with on(8):
        assert eggs() == [(typed, "6")]


def test_a_staple_not_due_yet_leaves_the_typed_line_a_plain_remove():
    with on(0):
        tools.add_staple("Eggs", quantity="1 dozen", every_days=7)
    with on(3):
        typed = tools.add_grocery_item("eggs", quantity="6")["item_id"]
        out = tools.remove_grocery_item(typed)
        assert "staple_id" not in out
        assert eggs() == []
        s = staple()
        assert (s["next_due_at"], s["skip_streak"]) == (iso(7), 0)
