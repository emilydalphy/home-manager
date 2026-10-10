"""
"We have plenty" / "Not this trip" on a staple's line keep what this week's
meals need (2026-10-10, overnight hunt in the Shop tab).

Repro, measured on a throwaway database through the real routes: Eggs is a
staple and running low, so the list carries "Eggs · 1 dozen · Probably
running low". The week is then approved with two frittatas wanting 8 eggs
each; the approval merges them into that line — "Eggs · 3 dozen", still the
staple's (staple_id kept, the note and both buttons still drawn). Tapping
"We have plenty" took the whole row off: the list had no eggs at all, with
two dinners still needing sixteen. "Not this trip" on Milk did the same to
the frittatas' cup of milk.

The answer is about the regular dozen, not the dinners. Now the row is cut
back to what its meals add up to (the ledger recompute a dropped night
uses), stops being the staple's, and becomes the week's own line; the toast
says "down to 2 dozen for this week's meals" instead of "off the list"; and
Undo puts the very row back as it was.
"""
from __future__ import annotations

import json
from datetime import timedelta

from conftest import household_today
import nodeharness
from shop_harness import CLICK as _CLICK, STUB as _STUB, grocery_block as _grocery_block, needs_node

from app import tools
from app.db import get_conn
from app.tools import staples as st


def _next_week(day: int) -> str:
    today = household_today()
    monday = today - timedelta(days=today.weekday()) + timedelta(days=7)
    return (monday + timedelta(days=day)).isoformat()


def _line(item_id: int) -> dict:
    conn = get_conn()
    row = conn.execute(
        "SELECT id, item, quantity, status, staple_id, added_by, source_weekly_plan_id "
        "FROM grocery_items WHERE id = ?",
        (item_id,),
    ).fetchone()
    conn.close()
    return dict(row)


def _live(name: str) -> list[dict]:
    conn = get_conn()
    rows = conn.execute(
        "SELECT id, quantity, staple_id FROM grocery_items WHERE lower(item) = lower(?) "
        "AND status IN ('needed', 'in_cart')",
        (name,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def _eggs_line_with_two_frittatas() -> tuple[int, int, list[int]]:
    """A running-low Eggs staple on the list, then an approved week of two
    frittatas merged into it. Returns (line id, plan id, the two entries)."""
    for name in ("Alex", "Sam", "Rae"):
        tools.add_member(name)
    st.add_staple("Eggs", running_low=True, quantity="1 dozen")
    tools.get_grocery_list_by_store(status="needed")  # the read that puts it on
    [line] = _live("Eggs")
    assert line["staple_id"] and line["quantity"] == "1 dozen"
    tools.add_recipe(
        "Frittata",
        ingredients=[{"item": "eggs", "qty": "8"}, {"item": "spinach", "qty": "5 oz"}],
        default_servings=3,
    )
    plan_id = tools.create_weekly_plan(_next_week(0))["weekly_plan_id"]
    entries = [
        tools.plan_meal(_next_week(d), "Frittata", slot="dinner", weekly_plan_id=plan_id)["entry_id"]
        for d in (1, 3)
    ]
    tools.approve_weekly_plan(plan_id, "Alex")
    merged = _line(line["id"])
    # The precondition the bug needs: one row, both wants, still the staple's.
    assert merged["quantity"] == "3 dozen" and merged["staple_id"] and merged["added_by"] == "staple"
    assert len(_live("Eggs")) == 1
    return line["id"], plan_id, entries


def test_we_have_plenty_keeps_the_weeks_eggs_on_the_list(signed_in):
    line_id, plan_id, _ = _eggs_line_with_two_frittatas()

    res = signed_in.post(f"/api/grocery-list/{line_id}/staple", json={"decision": "plenty"})
    assert res.status_code == 200
    body = res.json()
    assert body["removed_line"]["trimmed"]["quantity"] == "2 dozen"

    row = _line(line_id)
    assert row["status"] == "needed", "the frittatas' eggs went with the regular dozen"
    assert row["quantity"] == "2 dozen"
    # No longer the staple's suggestion: no note, no buttons, and it is the
    # week's own line from here on.
    assert row["staple_id"] is None and row["added_by"] == "ai"
    assert row["source_weekly_plan_id"] == plan_id
    # The answer itself still did what it always did to the staple.
    assert body["next_due_at"] == (household_today() + timedelta(days=body["cadence_days"])).isoformat()
    # And a read of the list neither adds a second Eggs line nor re-links.
    signed_in.get("/api/grocery-list/by-store")
    assert [r["id"] for r in _live("Eggs")] == [line_id]


def test_not_this_trip_keeps_it_too_and_undo_puts_the_row_back_exactly(signed_in):
    line_id, _, _ = _eggs_line_with_two_frittatas()
    staple_id = _line(line_id)["staple_id"]

    res = signed_in.post(f"/api/grocery-list/{line_id}/staple", json={"decision": "skip"})
    assert res.status_code == 200
    assert _line(line_id)["quantity"] == "2 dozen"

    undo = signed_in.post(f"/api/staples/{staple_id}/undo")
    assert undo.status_code == 200 and undo.json()["undone"] is True
    row = _line(line_id)
    assert (row["status"], row["quantity"], row["staple_id"], row["added_by"], row["source_weekly_plan_id"]) == (
        "needed", "3 dozen", staple_id, "staple", None,
    )
    assert undo.json()["skip_streak"] == 0
    assert [r["id"] for r in _live("Eggs")] == [line_id], "undo made a second line"


def test_after_plenty_a_dropped_night_recomputes_the_line_from_its_meals():
    """The trimmed row is the week's line now, so dropping one frittata
    leaves what the other needs — not the stale 2 dozen a standing-want
    restate would keep, and not nothing."""
    line_id, plan_id, entries = _eggs_line_with_two_frittatas()
    st.decide_staple_line(line_id, "plenty")
    tools.drop_dish_from_day(plan_id, entries[1])
    row = _line(line_id)
    assert row["status"] == "needed" and row["quantity"] == "1 dozen"


def test_a_staple_line_no_meal_added_to_still_comes_off_whole():
    """The ordinary case is unchanged: nothing of a week's on it, so the
    answer takes the row off (softly — the undo's row)."""
    st.add_staple("Coffee", running_low=True, quantity="1 bag")
    tools.get_grocery_list_by_store(status="needed")
    [line] = _live("Coffee")
    out = st.decide_staple_line(line["id"], "plenty")
    assert "trimmed" not in out["removed_line"]
    assert _line(line["id"])["status"] == "removed"


# --- the toast ---------------------------------------------------------------

_PATCH = """
groIsBuilt = function () { return true; };
renderGrocery = function () {};
fetch = function (url, opts) {
  POSTS.push({ url: url, body: JSON.parse((opts && opts.body) || '{}') });
  const body = url.indexOf('/staple') !== -1 ? RESULT : {};
  return Promise.resolve({ ok: true, status: 200, json: function () { return Promise.resolve(body); } });
};
var RESULT = null;
function eggsRow() {
  groceryState.data = { stores: { Unassigned: { sections: [{ section: 'other', items: [
    { id: 1, item: 'Eggs', quantity: '3 dozen', store: '', store_decided: 0, status: 'needed',
      staple_id: 4, added_by: 'staple' }] }], purchased: [], inCart: [] } } };
  groceryState.usualStores = [];
  groceryState.storesPromptDismissed = true;
  groceryState.step = 'list';
}
const ROW = { querySelectorAll: function () { return []; } };
"""


def _toast(result: dict, decision: str) -> str:
    body = (
        "eggsRow(); RESULT = " + json.dumps(result) + ";\n"
        "clickIfRendered({ gro: 'staple-decide', decision: '" + decision + "', id: '1', name: 'Eggs' }, ROW);\n"
        "settle(function () { console.log(JSON.stringify(lastToast())); });\n"
    )
    res = nodeharness.run_node(_STUB + _grocery_block() + _CLICK + _PATCH + body, timeout=30)
    assert res.returncode == 0, res.stderr
    return json.loads(res.stdout.strip())["msg"]


@needs_node
def test_the_toast_says_the_line_stayed_when_it_was_cut_back():
    trimmed = {"id": 4, "cadence_days": 21, "removed_line": {"item": "Eggs", "trimmed": {"item_id": 1, "quantity": "2 dozen"}}}
    assert _toast(trimmed, "plenty").startswith("Eggs down to 2 dozen for this week’s meals — I’ll ask again in ")
    assert _toast(trimmed, "skip") == "Eggs down to 2 dozen for this week’s meals — I’ll ask again next week"
    whole = {"id": 4, "cadence_days": 21, "removed_line": {"item": "Eggs", "quantity": "1 dozen"}}
    assert _toast(whole, "skip") == "Eggs off the list — I’ll ask again next week"
