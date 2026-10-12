"""
A typed amount merged into a staple's line makes the line the person's
(2026-10-10, overnight hunt in the Shop tab).

Repro, through the real routes on a throwaway database: Coffee is a staple
running low, so the list carries "Coffee · 1 bag · Probably running low".
Somebody types "coffee · 2 bags" on Shop's add sheet; it merges — "Coffee ·
3 bags" — and the line still drew "Probably running low" with "We have
plenty" / "Not this trip". Either one took the whole row off: the two bags
the person had just asked for went with the staple's guess.

Now the line is theirs once their amount is on it (add_regulars' rule): no
note, no buttons, and still linked to the staple, so buying it teaches the
rhythm.
"""
from __future__ import annotations

from app import tools
from app.db import get_conn
from app.tools import staples as st


def _coffee() -> dict:
    conn = get_conn()
    row = conn.execute(
        "SELECT id, quantity, added_by, staple_id, status FROM grocery_items WHERE lower(item) = 'coffee'"
    ).fetchone()
    conn.close()
    return dict(row)


def _running_low_coffee() -> dict:
    st.add_staple("Coffee", running_low=True, quantity="1 bag")
    tools.get_grocery_list_by_store(status="needed")
    line = _coffee()
    assert line["added_by"] == "staple" and line["staple_id"]
    return line


def test_a_typed_amount_on_a_staple_line_makes_it_the_persons(signed_in):
    line = _running_low_coffee()
    res = signed_in.post("/api/grocery-list/add", json={"item": "coffee", "quantity": "2 bags"})
    assert res.status_code == 200 and res.json()["merged"] is True
    after = _coffee()
    assert after["quantity"] == "3 bags"
    assert after["added_by"] not in ("staple", ""), "still drawn as Pomona's 'Probably running low'"
    assert after["staple_id"] == line["staple_id"], "buying it must still teach the rhythm"
    # And buying it does.
    tools.mark_grocery_item(after["id"], status="purchased")
    assert st.list_staples()[0]["last_bought_at"] is not None


def test_the_staples_own_line_and_a_weeks_ingest_leave_it_the_staples():
    """Only a person's add takes it over: the staple re-putting its own
    line, or a week's meals joining it, are not somebody asking for it."""
    _running_low_coffee()
    tools.add_grocery_item("coffee", quantity="1 bag", added_by="staple")
    tools.add_grocery_item("coffee", quantity="1 bag", added_by="ai", source_weekly_plan_id=99)
    assert _coffee()["added_by"] == "staple"


# --- review round: the other two doors onto the staple's line ---


def test_pausing_the_staple_leaves_the_persons_line(signed_in):
    line = _running_low_coffee()
    tools.add_grocery_item("coffee", quantity="2 bags")
    res = signed_in.post(f"/api/staples/{line['staple_id']}/pause")
    assert res.status_code == 200 and res.json()["paused"] is True
    after = _coffee()
    assert (after["status"], after["quantity"]) == ("needed", "3 bags")


def test_plenty_from_chat_leaves_the_persons_line():
    _running_low_coffee()
    # The chat's own add door credits the merge the same way.
    st.add_grocery_item_for_chat("coffee", quantity="2 bags")
    assert _coffee()["added_by"] not in ("staple", "")
    out = st.mark_staple_plenty("coffee")
    assert out["found"] is True and out["removed_line"] is None
    after = _coffee()
    assert (after["status"], after["quantity"]) == ("needed", "3 bags")
