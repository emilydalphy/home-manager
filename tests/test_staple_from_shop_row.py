"""
Making a staple from a Shop row (⋯ → "Make it a staple") — defect found
2026-10-09 by driving the app.

The row menu posted /api/staples/add with only the line's NAME, so the new
staple was made as if typed: "last bought today, due in 3 weeks" — a
purchase nobody made — and the line the person tapped kept no staple_id, so
the staple and its own line were strangers. Now the client passes the row
(`grocery_item_id`), and the server:

- links THAT line (staple_id, this household's line only) and adds no other;
- invents no purchase: no last-bought date, and the staple reads due now —
  it is already on the list, which is what "due" means;
- refuses a line that isn't this household's.

Making a staple by name (chat, "Before you shop") is unchanged — pinned
here too.
"""

from __future__ import annotations

from datetime import date

import pytest

from app import tools
from app.db import get_conn
from app.tools import staples as st
from app.tools._shared import use_household

TODAY = date(2026, 9, 16)

pytestmark = pytest.mark.today(TODAY)


@pytest.fixture(autouse=True)
def _pin_today(monkeypatch):
    monkeypatch.setattr(st, "_TODAY_OVERRIDE", TODAY)
    yield


def _neighbour() -> int:
    conn = get_conn()
    hid = conn.execute("INSERT INTO households (name) VALUES ('Next door')").lastrowid
    conn.commit()
    conn.close()
    return hid


def _line(item_id: int) -> dict:
    conn = get_conn()
    row = conn.execute(
        "SELECT id, household_id, item, status, staple_id, added_by FROM grocery_items WHERE id = ?", (item_id,)
    ).fetchone()
    conn.close()
    return dict(row) if row else {}


def _lines_named(name: str) -> list[dict]:
    conn = get_conn()
    rows = conn.execute(
        "SELECT id, status, staple_id FROM grocery_items WHERE household_id = 1 AND lower(item) = lower(?) "
        "AND status != 'removed'",
        (name,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def test_a_staple_made_from_a_shop_row_links_that_row_and_invents_no_purchase():
    line_id = tools.add_grocery_item("Oat milk", category="dairy")["item_id"]

    s = tools.add_staple("Oat milk", category="dairy", grocery_item_id=line_id)

    assert s["created"] is True
    assert _line(line_id)["staple_id"] == s["id"]
    # Nothing was bought: no last-bought date, and it is due NOW — the line
    # is on the list — rather than "a cadence from a purchase that didn't
    # happen" (the defect read "due in 3 weeks").
    assert s["last_bought_at"] is None
    assert s["due"] is True
    assert s["next_due_at"] == TODAY.isoformat()
    # Still the person's own line — not Pomona's "Probably running low".
    assert _line(line_id)["added_by"] != st.ADDED_BY_STAPLE
    # Reading the list (what puts due staples on it) adds no second line.
    tools.get_grocery_list_by_section(status="needed")
    assert [r["id"] for r in _lines_named("Oat milk")] == [line_id]


def test_buying_the_linked_row_is_the_staples_first_purchase():
    line_id = tools.add_grocery_item("Oat milk", category="dairy")["item_id"]
    s = tools.add_staple("Oat milk", category="dairy", grocery_item_id=line_id)

    tools.mark_grocery_item(line_id, "purchased")

    after = next(x for x in tools.list_staples() if x["id"] == s["id"])
    assert after["last_bought_at"] == TODAY.isoformat()
    assert after["due"] is False
    conn = get_conn()
    ev = conn.execute(
        "SELECT grocery_item_id FROM staple_events WHERE staple_id = ? AND kind = 'bought'", (s["id"],)
    ).fetchall()
    conn.close()
    assert [e["grocery_item_id"] for e in ev] == [line_id]


def test_a_spice_made_from_a_shop_row_gets_no_second_line():
    """A spice is the one staple add_staple puts on the list itself (when
    running low) — the row it came from must be that line, not a twin."""
    line_id = tools.add_grocery_item("Cumin", quantity="1 jar", category="pantry")["item_id"]
    s = tools.add_staple("Cumin", quantity="1 jar", category="pantry", grocery_item_id=line_id, running_low=True)
    assert _line(line_id)["staple_id"] == s["id"]
    assert [r["id"] for r in _lines_named("Cumin")] == [line_id]
    # ...and not "merged" into itself either: still the one jar.
    assert tools.list_grocery_list(status="needed")[0]["quantity"] == "1 jar"


def test_another_households_row_is_refused_and_left_alone():
    with use_household(_neighbour()):
        theirs = tools.add_grocery_item("Oat milk", category="dairy")["item_id"]

    with pytest.raises(ValueError):
        tools.add_staple("Oat milk", category="dairy", grocery_item_id=theirs)

    assert _line(theirs)["staple_id"] is None
    assert tools.list_staples() == []


def test_the_route_passes_the_row_and_refuses_a_stranger(signed_in):
    mine = tools.add_grocery_item("Dish soap", category="household")["item_id"]
    with use_household(_neighbour()):
        theirs = tools.add_grocery_item("Paper towels", category="household")["item_id"]

    bad = signed_in.post("/api/staples/add", json={"item": "Paper towels", "grocery_item_id": theirs})
    assert bad.status_code == 400
    assert _line(theirs)["staple_id"] is None

    ok = signed_in.post("/api/staples/add", json={"item": "Dish soap", "category": "household", "grocery_item_id": mine})
    assert ok.status_code == 200
    assert _line(mine)["staple_id"] == ok.json()["id"]
    assert ok.json()["last_bought_at"] is None


def test_by_name_is_unchanged():
    """Chat's add_staple and the typed regular carry no row: a staple you
    aren't out of is had now, due a cadence away — as before."""
    line_id = tools.add_grocery_item("Dish soap", category="household")["item_id"]
    s = tools.add_staple("Dish soap", category="household")
    assert s["last_bought_at"] == TODAY.isoformat()
    assert s["due"] is False
    assert _line(line_id)["staple_id"] is None


def test_the_row_menu_sends_the_row_it_was_tapped_on():
    from pathlib import Path

    js = (Path(__file__).resolve().parent.parent / "static" / "shell.js").read_text(encoding="utf-8")
    start = js.index("case 'row-staple':")
    case = js[start:js.index("return;", start)]
    assert "grocery_item_id" in case
