"""
Two adults on one list at the same moment — the writes land whole.

Found 2026-10-29 driving a real uvicorn with two signed-in phones (one
household, Maya and Theo picked in "Who's this?"), each pair of requests
released together from a barrier:

  1. Theo adds "Oat milk 1 carton" while Maya ticks the Oat milk already
     on the list. add_grocery_item read the 'needed' lines, the tick
     committed, and the add then summed onto the line Maya had just BOUGHT:
     the kitchen got 1 carton, the line read "2 cartons · bought", and
     Theo's carton was on nobody's list. 26 of 40 pairs.
  2. Maya puts a staple line back while Theo ticks it again. The staple's
     rhythm is written after mark_grocery_item commits, on its own
     connection, so the untick's "take back today's purchase" could run
     AFTER the re-tick's "record the purchase": a bought line with no
     purchase on record. 5 of 40 pairs.
  3. Maya removes a staple while Theo taps "We have plenty" on its line:
     the staple was deleted between decide_staple_line's read and its
     event insert — a FOREIGN KEY 500 on Theo's phone, and the leaked
     connection kept the write lock, so the next list read and the next
     add 500'd "database is locked" too. 1 of 5 pairs.

Each test makes the bad interleaving happen on purpose (the other phone's
request is started at the exact point the race needs it, on its own
thread) rather than hoping threads collide.

MUTATIONS RUN (read off the run):
  1. add_grocery_item's BEGIN IMMEDIATE wrapper removed         -> 1 failed
  2. unrecord_staple_purchase's line-status re-read removed     -> 1 failed
  3. decide_staple_line without its BEGIN IMMEDIATE            -> 2 failed
"""
from __future__ import annotations

import datetime as _dt
import threading

import pytest

from app import tools
from app.db import get_conn
from app.tools import grocery as gr
from app.tools import staples as st
from conftest import household_pin

pytestmark = pytest.mark.today(household_pin(10, on=_dt.date(2026, 10, 21)))


def _one(sql: str, *args):
    conn = get_conn()
    try:
        row = conn.execute(sql, args).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def _all(sql: str, *args) -> list[dict]:
    conn = get_conn()
    try:
        return [dict(r) for r in conn.execute(sql, args).fetchall()]
    finally:
        conn.close()


class _OtherPhone:
    """Runs the other adult's request on its own thread at the moment the
    race needs it, and gives it a second to land. Without the fix it lands
    inside that second (nothing holds it off); with it, it waits for the
    first request's commit and lands after."""

    def __init__(self, fn):
        self.fn = fn
        self.error: BaseException | None = None
        self.thread: threading.Thread | None = None

    def go(self):
        def run():
            try:
                self.fn()
            except BaseException as e:  # pragma: no cover - surfaced below
                self.error = e
        self.thread = threading.Thread(target=run)
        self.thread.start()
        self.thread.join(1.0)

    def finish(self):
        if self.thread is not None:
            self.thread.join(10)
            assert not self.thread.is_alive()
        if self.error is not None:
            raise self.error


def test_an_add_racing_the_other_phones_tick_is_never_summed_onto_a_bought_line(monkeypatch):
    line = tools.add_grocery_item("Oat milk", quantity="1 carton", category="dairy")["item_id"]
    other = _OtherPhone(lambda: tools.mark_grocery_item(line, "purchased"))
    real = gr._merge_target
    fired = []

    def merge_target_then_tick(*a, **kw):
        # The add has read the 'needed' lines; the other phone ticks now.
        if not fired:
            fired.append(True)
            other.go()
        return real(*a, **kw)

    monkeypatch.setattr(gr, "_merge_target", merge_target_then_tick)
    tools.add_grocery_item("Oat milk", quantity="1 carton", category="dairy")
    other.finish()

    lines = _all("SELECT id, status, quantity FROM grocery_items WHERE item = 'Oat milk' ORDER BY id")
    kitchen = _all("SELECT quantity FROM inventory_items WHERE item = 'Oat milk'")
    bought = [x for x in lines if x["status"] == "purchased"]
    needed = [x for x in lines if x["status"] == "needed"]
    assert len(bought) == 1 and len(kitchen) == 1
    # What the kitchen got is what the bought line says was bought, and the
    # second carton is somewhere: on that bought line (the add landed first,
    # the tick bought both) or on a line of its own still to buy.
    assert kitchen[0]["quantity"] == bought[0]["quantity"]
    total = sum(int(x["quantity"].split()[0]) for x in lines)
    assert total == 2, lines
    assert needed or bought[0]["quantity"] == "2 cartons"


def test_an_add_given_a_connection_still_leaves_committing_to_its_caller():
    # The grocery ingest's shape (swap_meal_in_plan): reads and writes on
    # the caller's connection, commits nothing.
    conn = get_conn()
    try:
        conn.execute("BEGIN IMMEDIATE")
        tools.add_grocery_item("Leeks", quantity="2", conn=conn)
        conn.rollback()
    finally:
        conn.close()
    assert _one("SELECT id FROM grocery_items WHERE item = 'Leeks'") is None


def _soap_line() -> tuple[int, int]:
    sid = tools.add_staple("Dish soap", category="household", every_days=21, running_low=True)["id"]
    st.sync_due_staples()
    line = _one("SELECT id FROM grocery_items WHERE staple_id = ? AND status = 'needed'", sid)
    assert line is not None
    return sid, line["id"]


def test_a_retick_racing_the_other_phones_put_back_keeps_the_purchase(monkeypatch):
    sid, line = _soap_line()
    tools.mark_grocery_item(line, "purchased")
    real_unrecord = st.unrecord_staple_purchase
    held = []

    def unrecord_late(*a, **kw):
        # Maya's put-back has committed; its staple follow-up is held until
        # Theo's re-tick has finished, follow-up and all.
        held.append((a, kw))

    monkeypatch.setattr(st, "unrecord_staple_purchase", unrecord_late)
    tools.mark_grocery_item(line, "needed")
    monkeypatch.setattr(st, "unrecord_staple_purchase", real_unrecord)
    tools.mark_grocery_item(line, "purchased")
    (a, kw), = held
    real_unrecord(*a, **kw)

    assert _one("SELECT status FROM grocery_items WHERE id = ?", line)["status"] == "purchased"
    bought = _all("SELECT on_date FROM staple_events WHERE staple_id = ? AND kind = 'bought'", sid)
    assert len(bought) == 1
    assert _one("SELECT last_bought_at FROM staples WHERE id = ?", sid)["last_bought_at"] == bought[0]["on_date"]


def test_a_tick_whose_follow_up_lands_after_the_put_back_teaches_nothing(monkeypatch):
    # The mirror: Theo's tick committed, Maya's put-back committed and
    # ran its follow-up, THEN Theo's "record the purchase" arrives.
    sid, line = _soap_line()
    real_record = st.record_staple_purchase
    held = []
    monkeypatch.setattr(st, "record_staple_purchase", lambda *a, **kw: held.append((a, kw)))
    tools.mark_grocery_item(line, "purchased")
    monkeypatch.setattr(st, "record_staple_purchase", real_record)
    tools.mark_grocery_item(line, "needed")
    (a, kw), = held
    real_record(*a, **kw)

    assert _one("SELECT status FROM grocery_items WHERE id = ?", line)["status"] == "needed"
    assert _all("SELECT id FROM staple_events WHERE staple_id = ? AND kind = 'bought'", sid) == []
    assert _one("SELECT last_bought_at FROM staples WHERE id = ?", sid)["last_bought_at"] is None


@pytest.mark.parametrize("decision", ["plenty", "skip"])
def test_an_answer_racing_the_other_phones_remove_is_not_a_500(monkeypatch, decision):
    sid, line = _soap_line()
    other = _OtherPhone(lambda: tools.remove_staple_by_id(sid))
    real_row = st._row
    fired = []

    def row_then_remove(conn, staple_id):
        out = real_row(conn, staple_id)
        # decide_staple_line has read the line and the staple; Maya's
        # Remove arrives now.
        if staple_id == sid and not fired:
            fired.append(True)
            other.go()
        return out

    monkeypatch.setattr(st, "_row", row_then_remove)
    out = tools.decide_staple_line(line, decision)
    monkeypatch.setattr(st, "_row", real_row)
    other.finish()

    assert out["decision"] == decision
    # The remove landed after the answer: the staple and its history are
    # gone, the answered line stays off the list.
    assert _one("SELECT id FROM staples WHERE id = ?", sid) is None
    assert _one("SELECT status FROM grocery_items WHERE id = ?", line)["status"] == "removed"
    # And nothing is left holding the write lock.
    conn = get_conn()
    conn.execute("PRAGMA busy_timeout = 0")
    try:
        conn.execute("BEGIN IMMEDIATE")
        conn.rollback()
    finally:
        conn.close()
