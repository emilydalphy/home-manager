"""
A staple wanted again the same day goes back on the list that day (defect
hunt, 2026-10-28; branch overnight/hunt-staples-stores-1028b).

sync_due_staples reads a staple line taken off today as this trip's answer
and lists nothing for it until tomorrow. "We have plenty" tapped in the
morning, then "we're out of dish soap" said in chat in the afternoon (the
tool sets running_low), left the staple "probably running low" with NO line
on the list until the next day — while the chat had just said it went on.
The same with Pause then Resume on Regulars.

Acceptance:
- running_low after a same-day plenty / not-this-trip / Remove puts the very
  row back (its store and amount kept), and only one line;
- Resume the same day as Pause puts the paused line back; Resume of a staple
  that is not due puts nothing on;
- a line already covering it (a person's own line) means nothing is put back;
- a row dropped on "Maybe already home" is left where it is (its own Put back).
"""

from __future__ import annotations

from app import tools
from app.db import get_conn
from app.tools import staples as st


def _rows(item: str) -> list[dict]:
    conn = get_conn()
    rows = conn.execute(
        "SELECT id, item, quantity, store, status, staple_id, removed_by FROM grocery_items "
        "WHERE household_id = 1 ORDER BY id"
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows if r["item"].lower() == item.lower()]


def _needed(item: str) -> list[dict]:
    tools.get_grocery_list_by_section(status="needed")
    return [r for r in _rows(item) if r["status"] == "needed"]


def _due_line(item: str, **kw) -> dict:
    tools.add_staple(item, running_low=True, **kw)
    (line,) = _needed(item)
    return line


def test_out_again_after_we_have_plenty_puts_the_same_row_back():
    line = _due_line("Dish soap", quantity="2 bottles")
    tools.set_grocery_item_store(line["id"], "Costco")
    tools.decide_staple_line(line["id"], "plenty")
    assert _needed("Dish soap") == []

    out = tools.add_staple("dish soap", running_low=True)

    assert out["due"] is True
    (back,) = _needed("Dish soap")
    assert back["id"] == line["id"]
    assert back["store"] == "Costco" and back["quantity"] == "2 bottles"


def test_out_again_after_not_this_trip_or_remove_is_one_line():
    line = _due_line("Coffee")
    tools.decide_staple_line(line["id"], "skip")
    tools.add_staple("Coffee", running_low=True)
    assert [r["id"] for r in _needed("Coffee")] == [line["id"]]
    assert tools.list_staples()[0]["skip_streak"] == 0

    # The row's own Remove is "not this trip" too.
    tools.remove_grocery_item(line["id"])
    assert _needed("Coffee") == []
    tools.add_staple("Coffee", running_low=True)
    assert [r["id"] for r in _needed("Coffee")] == [line["id"]]


def test_the_toast_undo_after_it_is_back_makes_no_second_line():
    line = _due_line("Coffee")
    out = tools.decide_staple_line(line["id"], "plenty")
    tools.add_staple("Coffee", running_low=True)
    tools.undo_staple_decision(out["id"])
    assert len(_needed("Coffee")) == 1


def test_resume_the_same_day_puts_the_paused_line_back():
    line = _due_line("Paper towels")
    s = st.list_staples()[0]
    tools.pause_staple(s["id"], paused=True)
    assert _needed("Paper towels") == []

    tools.pause_staple(s["id"], paused=False)
    assert [r["id"] for r in _needed("Paper towels")] == [line["id"]]


def test_resume_of_a_staple_not_due_puts_nothing_on():
    line = _due_line("Paper towels")
    s = st.list_staples()[0]
    tools.decide_staple_line(line["id"], "plenty")  # due a whole cadence out
    tools.pause_staple(s["id"], paused=True)
    tools.pause_staple(s["id"], paused=False)
    assert _needed("Paper towels") == []


def test_a_persons_own_line_already_covering_it_is_left_alone():
    line = _due_line("Coffee")
    tools.decide_staple_line(line["id"], "plenty")
    own = tools.add_grocery_item("Coffee", added_by="user")
    tools.add_staple("Coffee", running_low=True)
    assert [r["id"] for r in _needed("Coffee")] == [own["item_id"]]


def test_a_maybe_already_home_drop_keeps_its_own_put_back():
    line = _due_line("Coffee")
    tools.drop_grocery_item_pre_shop(line["id"], author="Emily")
    tools.add_staple("Coffee", running_low=True)
    (row,) = _rows("Coffee")
    assert row["status"] == "removed" and row["removed_by"] == "Emily"
