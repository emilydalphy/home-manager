"""
Remove's Undo puts back the same line, still tied to its meals
(2026-10-10, overnight hunt in the Shop tab).

Repro, measured on a throwaway database through the real routes: a week
of two frittatas puts "spinach · 10 oz" on the list. Row ⋯ → Remove, then
the toast's Undo. /remove was a hard delete — its meal_plan_grocery_links
rows cascaded away — and the Undo re-added the NAME through
/api/grocery-list/add: a hand-typed line, no source week, no meals. From
then on dropping one frittata left it at 10 oz, dropping both left it at
10 oz, and as a person's standing want it would never clear with the week.

Now a still-needed line with meals behind it is soft-removed
(removed_by 'list') and /remove-undo restores that very row, re-read from
its ledger in case a night changed in between; a line with nothing left to
buy for stays off. A hand-typed line keeps the old hard delete.
"""
from __future__ import annotations

import json
from datetime import timedelta

from conftest import household_today
import nodeharness
from shop_harness import CLICK as _CLICK, STUB as _STUB, grocery_block as _grocery_block, needs_node

from app import tools
from app.db import get_conn


def _next_week(day: int) -> str:
    today = household_today()
    monday = today - timedelta(days=today.weekday()) + timedelta(days=7)
    return (monday + timedelta(days=day)).isoformat()


def _row(item_id: int) -> dict | None:
    conn = get_conn()
    row = conn.execute(
        "SELECT id, item, quantity, status, source_weekly_plan_id, added_by FROM grocery_items WHERE id = ?",
        (item_id,),
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def _live(name: str) -> list[dict]:
    conn = get_conn()
    rows = conn.execute(
        "SELECT id, quantity, source_weekly_plan_id FROM grocery_items WHERE item = ? AND status = 'needed'",
        (name,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def _two_frittatas() -> tuple[int, int, list[int]]:
    for name in ("Alex", "Sam", "Rae"):
        tools.add_member(name)
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
    [line] = _live("spinach")
    assert line["quantity"] == "10 oz"
    return line["id"], plan_id, entries


def test_remove_then_undo_is_the_same_line_still_following_its_meals(signed_in):
    line_id, plan_id, entries = _two_frittatas()
    gone = signed_in.post(f"/api/grocery-list/{line_id}/remove")
    assert gone.status_code == 200 and gone.json()["restorable"] is True
    assert _live("spinach") == []
    # Taken off, not "already had": the wrap-up's put-back list is not for it.
    summary = signed_in.get("/api/grocery-list/already-have-summary").json()
    assert all(r["id"] != line_id for r in summary["already_have"])

    back = signed_in.post(f"/api/grocery-list/{line_id}/remove-undo")
    assert back.status_code == 200 and back.json()["restored"] is True
    assert _live("spinach") == [{"id": line_id, "quantity": "10 oz", "source_weekly_plan_id": plan_id}]

    # Still the meals' line: it shrinks with them and goes with the last.
    tools.drop_dish_from_day(plan_id, entries[1])
    assert _live("spinach")[0]["quantity"] == "5 oz"
    tools.drop_dish_from_day(plan_id, entries[0])
    assert _live("spinach") == []


def test_a_night_dropped_while_it_was_off_is_reflected_when_it_comes_back(signed_in):
    line_id, plan_id, entries = _two_frittatas()
    signed_in.post(f"/api/grocery-list/{line_id}/remove")
    tools.drop_dish_from_day(plan_id, entries[1])
    assert signed_in.post(f"/api/grocery-list/{line_id}/remove-undo").json()["restored"] is True
    assert _live("spinach") == [{"id": line_id, "quantity": "5 oz", "source_weekly_plan_id": plan_id}]


def test_with_every_meal_gone_since_nothing_comes_back(signed_in):
    line_id, plan_id, entries = _two_frittatas()
    signed_in.post(f"/api/grocery-list/{line_id}/remove")
    for e in entries:
        tools.drop_dish_from_day(plan_id, e)
    res = signed_in.post(f"/api/grocery-list/{line_id}/remove-undo")
    assert res.status_code == 200 and res.json()["restored"] is False
    assert _live("spinach") == []


def test_a_hand_typed_line_is_still_deleted_outright(signed_in):
    item_id = tools.add_grocery_item("batteries", quantity="4")["item_id"]
    res = signed_in.post(f"/api/grocery-list/{item_id}/remove").json()
    assert "restorable" not in res
    assert _row(item_id) is None
    # And the undo route will not resurrect some other kind of removal.
    line_id, _, _ = _two_frittatas()
    tools.drop_grocery_item_pre_shop(line_id)
    assert signed_in.post(f"/api/grocery-list/{line_id}/remove-undo").json()["restored"] is False
    assert _row(line_id)["status"] == "removed"


# --- the toast's Undo ----------------------------------------------------------

_PATCH = """
groIsBuilt = function () { return true; };
renderGrocery = function () {};
var REMOVE_BODY = {};
var UNDO_BODY = {};
fetch = function (url, opts) {
  POSTS.push({ url: url, body: JSON.parse((opts && opts.body) || '{}') });
  const body = /\\/remove$/.test(url) ? REMOVE_BODY : /remove-undo$/.test(url) ? UNDO_BODY : {};
  return Promise.resolve({ ok: true, status: 200, json: function () { return Promise.resolve(body); } });
};
function spinachRow() {
  groceryState.data = { stores: { Unassigned: { sections: [{ section: 'other', items: [
    { id: 3, item: 'spinach', quantity: '10 oz', category: 'other', store: '', store_decided: 0, status: 'needed' }] }],
    purchased: [], inCart: [] } } };
  groceryState.usualStores = [];
  groceryState.storesPromptDismissed = true;
  groceryState.step = 'list';
  groceryState.openRowId = '3';
}
"""


def _tap_remove_then_undo(remove_body: dict, undo_body: dict | None = None) -> dict:
    body = (
        "spinachRow(); REMOVE_BODY = " + json.dumps(remove_body) + "; UNDO_BODY = " + json.dumps(undo_body or {}) + ";\n"
        "clickIfRendered({ gro: 'row-remove', id: '3', name: 'spinach', qty: '10 oz', cat: 'other', store: '' });\n"
        "settle(function () { tapUndo(); settle(function () {\n"
        "  console.log(JSON.stringify({ urls: POSTS.map(function (p) { return p.url; }), toast: lastToast() }));\n"
        "}); });\n"
    )
    res = nodeharness.run_node(_STUB + _grocery_block() + _CLICK + _PATCH + body, timeout=30)
    assert res.returncode == 0, res.stderr
    return json.loads(res.stdout.strip())


@needs_node
def test_undo_puts_back_the_row_when_the_server_kept_it():
    out = _tap_remove_then_undo({"item_id": 3, "deleted": True, "restorable": True}, {"restored": True})
    assert out["urls"][-1] == "/api/grocery-list/3/remove-undo"
    assert not any(u.endswith("/add") for u in out["urls"])


@needs_node
def test_undo_says_so_when_there_is_nothing_left_to_buy_for():
    out = _tap_remove_then_undo({"item_id": 3, "deleted": True, "restorable": True}, {"restored": False})
    assert out["toast"]["msg"] == "spinach isn’t needed now — those meals are off the plan"


@needs_node
def test_a_hard_deleted_line_still_comes_back_by_name():
    out = _tap_remove_then_undo({"item_id": 3, "deleted": True})
    assert out["urls"][-1] == "/api/grocery-list/add"
