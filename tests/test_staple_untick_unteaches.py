"""
Un-ticking a bought staple un-teaches it (Loop Board, Medium, Phase 0).

THE BUG. grocery.mark_grocery_item calls staples.record_staple_purchase on
every line that turns 'purchased', which writes one 'bought' event per
staple per day and re-learns the cadence from it. The un-tick path
(purchased -> needed) put the kitchen back when it could, and never
touched the staple: a purchase that did not happen stayed on record, the
staple's last-bought date read today, next-due sat a whole cadence out,
and a cadence could turn "learned" from a date nobody bought on.

THE FIX. The bought event remembers which grocery line created it
(staple_events.grocery_item_id) and what the staple's rhythm fields read on
either side of the tick (staple_events.receipt_json). Un-ticking that line
removes TODAY's event — only if this line created it, and only if nothing
else bought the same thing today (a second line or a non-list source the
same day makes the event nobody's: grocery_item_id NULL, and it stays).
Then the rhythm is put back to the recorded "before" when the staple still
reads exactly the tick's "after"; otherwise the event goes and the cadence
is re-learned from the dates that remain. Earlier days are never touched;
a re-tick records the purchase again, still one per day.
"""

from __future__ import annotations

import json
import re
from datetime import date, timedelta
from pathlib import Path

import pytest

from app import tools
from app.db import _MIGRATIONS, _run_migrations, get_conn
from app.tools import staples as st

REPO = Path(__file__).resolve().parent.parent


@pytest.fixture(autouse=True)
def _pin_today(monkeypatch):
    monkeypatch.setattr(st, "_TODAY_OVERRIDE", date(2026, 9, 16))
    yield


def travel(days: int) -> date:
    st._TODAY_OVERRIDE = st._TODAY_OVERRIDE + timedelta(days=days)
    return st._TODAY_OVERRIDE


def today() -> str:
    return st._TODAY_OVERRIDE.isoformat()


def bought_events() -> list[dict]:
    conn = get_conn()
    rows = conn.execute(
        "SELECT id, on_date, source, grocery_item_id, receipt_json FROM staple_events WHERE kind = 'bought' ORDER BY id"
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def staple() -> dict:
    (s,) = tools.list_staples()
    return s


def add_line(name="Coffee", category="pantry") -> int:
    return tools.add_grocery_item(name, category=category)["item_id"]


def tick(line_id: int) -> dict:
    return tools.mark_grocery_item(line_id, "purchased")


def untick(line_id: int, to: str = "needed") -> dict:
    return tools.mark_grocery_item(line_id, to)


# ---------- the card's four sequences ----------


def test_tick_then_untick_clears_todays_bought_event():
    """RED before: the event and the taught date survived the untick."""
    tools.add_staple("Coffee", every_days=30, category="pantry")
    travel(10)
    line = add_line()
    tick(line)
    assert [e["on_date"] for e in bought_events()] == [today()]
    assert staple()["last_bought_at"] == today()
    untick(line)
    assert bought_events() == []
    s = staple()
    # Back to exactly what the staple read before the tick: added on the
    # 16th (not running low, so "you have some now" anchors it there),
    # told 30 days -> due 2026-10-16, last bought the add date.
    assert s["last_bought_at"] == "2026-09-16"
    assert s["next_due_at"] == "2026-10-16"
    assert s["cadence_source"] == "told" and s["cadence_days"] == 30


def test_a_same_day_purchase_from_another_source_survives_the_untick():
    """A receipt scan (or anything else that is not this list line) bought
    the same thing today: the event has two parents and the untick of one
    of them must not take it away. Today nothing but the tick calls
    record_staple_purchase; this drives the contract that a receipt-scan
    hook would go through."""
    tools.add_staple("Coffee", every_days=30, category="pantry")
    travel(10)
    line = add_line()
    tick(line)
    st.record_staple_purchase("Coffee", source="receipt")
    (ev,) = bought_events()
    assert ev["grocery_item_id"] is None  # no longer this line's alone
    res = untick(line)
    assert res["status"] == "needed"
    (ev,) = bought_events()
    assert ev["on_date"] == today()
    assert staple()["last_bought_at"] == today()


def test_a_receipt_first_then_a_tick_then_an_untick_also_keeps_it():
    """The other order: the event was never this line's to begin with."""
    tools.add_staple("Coffee", every_days=30, category="pantry")
    travel(10)
    st.record_staple_purchase("Coffee", source="receipt")
    line = add_line()
    tick(line)
    untick(line)
    (ev,) = bought_events()
    assert ev["source"] == "receipt" and ev["grocery_item_id"] is None
    assert staple()["last_bought_at"] == today()


def test_yesterdays_purchase_is_never_touched():
    tools.add_staple("Coffee", every_days=30, category="pantry")
    travel(10)
    tick(add_line())  # bought on the 26th
    travel(1)
    line = add_line()
    tick(line)  # bought again on the 27th
    assert [e["on_date"] for e in bought_events()] == ["2026-09-26", "2026-09-27"]
    untick(line)
    assert [e["on_date"] for e in bought_events()] == ["2026-09-26"]
    s = staple()
    assert s["last_bought_at"] == "2026-09-26"
    assert s["next_due_at"] == "2026-10-26"


def test_a_re_tick_records_the_purchase_again_and_still_once_per_day():
    tools.add_staple("Coffee", every_days=30, category="pantry")
    travel(10)
    line = add_line()
    tick(line)
    untick(line)
    assert bought_events() == []
    tick(line)
    (ev,) = bought_events()
    assert ev["on_date"] == today() and ev["grocery_item_id"] == line
    assert staple()["last_bought_at"] == today()
    untick(line)
    tick(line)
    untick(line, to="in_cart")
    tick(line)
    assert len(bought_events()) == 1


# ---------- the rhythm reflects the removal immediately ----------


def test_a_cadence_learned_from_the_unticked_date_is_unlearned():
    """Two real intervals learn a rhythm. If the second interval ends on the
    date being un-bought, the staple must go back to its told cadence —
    _relearn alone cannot do that (it keeps whatever cadence it finds when
    there are too few intervals), which is what the receipt is for."""
    tools.add_staple("Coffee", every_days=30, category="pantry")
    for gap in (10, 10):
        travel(gap)
        tick(add_line())
    assert staple()["cadence_source"] == "told"  # one interval is a coincidence
    travel(10)
    line = add_line()
    tick(line)
    s = staple()
    assert (s["cadence_source"], s["cadence_days"]) == ("learned", 10)
    untick(line)
    s = staple()
    assert (s["cadence_source"], s["cadence_days"]) == ("told", 30)
    assert s["last_bought_at"] == "2026-10-06"
    assert s["next_due_at"] == "2026-11-05"


def test_a_due_date_a_skip_had_set_comes_back():
    """"Not this trip" moved next_due a few days; a hand-added line ticked
    and un-ticked the same day must not leave next_due a whole cadence out."""
    s = tools.add_staple("Oat milk", every_days=21, category="dairy", running_low=True)
    tools.get_grocery_list_by_section(status="needed")
    (suggested,) = [r for r in tools.list_grocery_list(status="needed") if r["staple_id"]]
    skipped = tools.decide_staple_line(suggested["id"], "skip")
    assert skipped["next_due_at"] > today()
    assert skipped["skip_streak"] == 1
    line = add_line("Oat milk", category="dairy")
    tick(line)
    assert staple()["skip_streak"] == 0
    untick(line)
    after = staple()
    assert after["next_due_at"] == skipped["next_due_at"]
    assert after["skip_streak"] == 1
    assert after["last_bought_at"] is None


def test_a_staple_edited_between_tick_and_untick_keeps_the_edit():
    """The rhythm is only put back when it still reads exactly what the
    tick left it at. Told a new cadence in between: the event still goes,
    the new cadence stays, and next_due is re-learned from what remains."""
    tools.add_staple("Coffee", every_days=30, category="pantry")
    travel(10)
    line = add_line()
    tick(line)
    tools.add_staple("Coffee", every_days=14, category="pantry")
    untick(line)
    assert bought_events() == []
    s = staple()
    assert (s["cadence_source"], s["cadence_days"]) == ("told", 14)
    assert s["last_bought_at"] is None  # not today: that purchase did not happen
    # Re-anchored on the staple's creation date (real clock, not the pinned
    # one — created_at is SQLite's) plus the told 14 days.
    conn = get_conn()
    created = conn.execute("SELECT created_at FROM staples").fetchone()["created_at"][:10]
    conn.close()
    assert s["next_due_at"] == (date.fromisoformat(created) + timedelta(days=14)).isoformat()


def test_an_event_from_before_the_column_is_never_removed():
    """A 'bought' row with no grocery_item_id (every row from before this
    deploy) belongs to nobody an untick can identify."""
    tools.add_staple("Coffee", every_days=30, category="pantry")
    travel(10)
    line = add_line()
    tick(line)
    conn = get_conn()
    conn.execute("UPDATE staple_events SET grocery_item_id = NULL, receipt_json = NULL WHERE kind = 'bought'")
    conn.commit()
    conn.close()
    untick(line)
    assert len(bought_events()) == 1
    assert staple()["last_bought_at"] == today()


def test_two_lines_bought_the_same_day_the_untick_of_one_keeps_the_day():
    """test_staples pins that two lines on one day are one purchase; here,
    un-ticking one of them leaves the day bought (the other line still is).
    The known cost: un-ticking BOTH also leaves it, one phantom date, since
    the shared event records no second parent — listed on the card."""
    tools.add_staple("Coffee", every_days=30, category="pantry")
    travel(10)
    a = add_line()
    tick(a)
    b = add_line()
    tick(b)
    untick(a)
    (ev,) = bought_events()
    assert ev["on_date"] == today() and ev["grocery_item_id"] is None


def test_unticking_a_non_staple_line_is_nothing_here():
    line = tools.add_grocery_item("Bananas", category="produce")["item_id"]
    tick(line)
    res = untick(line)
    assert res["status"] == "needed"
    assert tools.list_staples() == [] and bought_events() == []


def test_the_receipt_records_what_the_tick_changed():
    tools.add_staple("Coffee", every_days=30, category="pantry")
    travel(10)
    line = add_line()
    tick(line)
    (ev,) = bought_events()
    assert ev["grocery_item_id"] == line and ev["source"] == "grocery"
    receipt = json.loads(ev["receipt_json"])
    assert receipt["before"]["last_bought_at"] == "2026-09-16"
    assert receipt["after"]["last_bought_at"] == today()
    assert receipt["after"]["next_due_at"] == "2026-10-26"
    assert set(receipt["before"]) == set(st._RHYTHM_FIELDS)


def test_the_kitchen_reversal_and_the_staple_reversal_travel_together():
    """Same untick, both undone: the kitchen row the tick created is gone
    and the taught date is gone."""
    tools.add_staple("Coffee", every_days=30, category="pantry")
    travel(10)
    line = tools.add_grocery_item("Coffee", quantity="1 bag", category="pantry")["item_id"]
    tick(line)
    assert [r["item"] for r in tools.get_inventory()] == ["Coffee"]
    res = untick(line)
    assert res["inventory_restored"] is True
    assert tools.get_inventory() == [] and bought_events() == []


# ---------- the upgrade path ----------

ADDED_HERE = [("staple_events", "grocery_item_id"), ("staple_events", "receipt_json")]


def _without_column(schema: str, table: str, column: str) -> tuple[str, int]:
    start = schema.index(f"CREATE TABLE IF NOT EXISTS {table} (")
    end = start + re.search(r"^\);", schema[start:], flags=re.M).start()
    block = schema[start:end]
    stripped, count = re.subn(rf"^\s*{re.escape(column)}\s+[^\n]*\n", "", block, flags=re.M)
    lines = stripped.split("\n")
    for i in range(len(lines) - 1, -1, -1):
        if lines[i].strip() and not lines[i].strip().startswith("--"):
            lines[i] = re.sub(r",\s*$", "", lines[i])
            break
    return schema[:start] + "\n".join(lines) + schema[end:], count


def test_a_database_made_before_these_columns_migrates_cleanly_twice(tmp_path):
    """Derived from today's schema.sql minus the two columns (the
    test_chore_owner_mode pattern). Nothing is backfilled: an existing
    bought event reads NULL for both, which the untick treats as "not
    mine" — the truth, since nothing recorded which line wrote it."""
    import sqlite3

    schema = (REPO / "app" / "schema.sql").read_text(encoding="utf-8")
    for table, column in ADDED_HERE:
        assert (table, column) in [(t, c) for t, c, _ in _MIGRATIONS]
        schema, count = _without_column(schema, table, column)
        assert count == 1, f"{table}.{column} must be declared on its own line in schema.sql"
    conn = sqlite3.connect(tmp_path / "old.db")
    conn.row_factory = sqlite3.Row
    conn.executescript(schema)
    assert "grocery_item_id" not in {r["name"] for r in conn.execute("PRAGMA table_info(staple_events)")}
    conn.execute("INSERT INTO staples (household_id, item, next_due_at) VALUES (1, 'Coffee', '2026-10-01')")
    conn.execute("INSERT INTO staple_events (household_id, staple_id, kind, source, on_date) VALUES (1, 1, 'bought', 'grocery', '2026-09-10')")
    conn.commit()
    _run_migrations(conn)
    _run_migrations(conn)
    conn.commit()
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(staple_events)")}
    assert {"grocery_item_id", "receipt_json"} <= cols
    row = conn.execute("SELECT on_date, grocery_item_id, receipt_json FROM staple_events").fetchone()
    assert (row["on_date"], row["grocery_item_id"], row["receipt_json"]) == ("2026-09-10", None, None)
    conn.close()
