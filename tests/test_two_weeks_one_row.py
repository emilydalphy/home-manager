"""
Shop shows one row for a thing two live weeks both need (Loop Board "Shop
shows one line per item while two weeks are live", Emily 2026-10-03;
rebuilt on main 2026-10-13).

Next week approved while this week runs: each week keeps its own onion
line on purpose (a swap in one week takes off only that week's amount),
and Shop used to draw both — "Onions 2" and "Onions 3". Now the server
marks the pair (app/tools/week_lines.py, display only) and the screen
draws "Onions 5 · 2 this week · 3 next week", sending each tap to both
lines (static/shell.js groCollapseWeeks / groIdsFor).
"""
from __future__ import annotations

import datetime

import pytest

import shop_harness
from app import tools
from app.db import get_conn
from app.tools import grocery as _grocery


def _plan(start: datetime.date, days: int = 7) -> int:
    return tools.create_weekly_plan(start.isoformat(), day_count=days)["weekly_plan_id"]


def _line(item: str, qty: str, plan: int | None, *, status: str = "needed", staple: int | None = None) -> int:
    conn = get_conn()
    cur = conn.execute(
        "INSERT INTO grocery_items (household_id, item, quantity, category, added_by, source_weekly_plan_id, status, staple_id) "
        "VALUES (1, ?, ?, 'produce', 'Emily', ?, ?, ?)",
        (item, qty, plan, status, staple),
    )
    conn.commit()
    conn.close()
    return cur.lastrowid


@pytest.fixture
def two_weeks():
    """This week running, next week approved early — two plans, no meals
    needed: the lines are what Shop reads."""
    today = _grocery._household_today()
    this_week = _plan(today - datetime.timedelta(days=2), days=5)
    next_week = _plan(today + datetime.timedelta(days=3))
    return this_week, next_week


def _needed_rows(client) -> dict[int, dict]:
    body = client.get("/api/grocery-list/by-store?status=needed").json()
    return {it["id"]: it for st in body["stores"] for s in st["sections"] for it in s["items"]}


def test_two_weeks_of_onions_are_one_row_with_the_total_and_the_note(signed_in, two_weeks):
    this_week, next_week = two_weeks
    a = _line("Onions", "2", this_week)
    b = _line("Onions", "3", next_week)
    rows = _needed_rows(signed_in)
    group = rows[a]["week_group"]
    assert rows[b]["week_group"] == group, "both lines carry the same group"
    assert group["lead_id"] == a and group["ids"] == [a, b]
    assert group["total"] == "5"
    assert group["note"] == "2 this week · 3 next week"
    assert [p["label"] for p in group["parts"]] == ["this week", "next week"]
    # Display only: underneath, each week still has its own line.
    conn = get_conn()
    assert [r["quantity"] for r in conn.execute(
        "SELECT quantity FROM grocery_items WHERE item = 'Onions' ORDER BY id").fetchall()] == ["2", "3"]
    conn.close()


def test_one_weeks_line_and_a_hand_add_look_as_they_always_did(signed_in, two_weeks):
    this_week, next_week = two_weeks
    lone = _line("Carrots", "4", this_week)
    mine = _line("Milk", "1", None)
    _line("Milk", "2", next_week)
    rows = _needed_rows(signed_in)
    assert "week_group" not in rows[lone]
    assert "week_group" not in rows[mine], "a household's own want is never folded into a week"


def test_a_week_ticked_and_a_week_not_are_two_rows_again_and_both_ticked_are_one(signed_in, two_weeks):
    this_week, next_week = two_weeks
    a = _line("Onions", "2", this_week)
    b = _line("Onions", "3", next_week)
    assert signed_in.post(f"/api/grocery-list/{a}/status", json={"status": "purchased"}).status_code == 200
    assert "week_group" not in _needed_rows(signed_in)[b]
    assert signed_in.post(f"/api/grocery-list/{b}/status", json={"status": "purchased"}).status_code == 200
    bought = signed_in.get("/api/grocery-list?status=bought").json()
    ticked = {it["id"]: it for s in bought["sections"] for it in s["items"]}
    assert ticked[a]["week_group"]["ids"] == [a, b]


def test_had_on_hand_for_both_weeks_is_one_row_to_put_back(signed_in, two_weeks):
    this_week, next_week = two_weeks
    a = _line("Onions", "2", this_week)
    b = _line("Onions", "3", next_week)
    for i in (a, b):
        res = signed_in.post(f"/api/grocery-list/{i}/pre-shop", json={"decision": "drop", "author": "user"})
        assert res.status_code == 200, res.text
    summary = signed_in.get("/api/grocery-list/already-have-summary").json()
    groups = {it["id"]: it.get("week_group") for it in summary["already_have"]}
    assert groups[a] and groups[a]["ids"] == [a, b]


def test_maybe_already_home_on_the_row_only_when_the_kitchen_covers_both_weeks(two_weeks):
    this_week, next_week = two_weeks
    a = _line("Onions", "2", this_week)
    b = _line("Onions", "3", next_week)
    flag = {"sentence": "You want 2. Fridge shows 6.", "wantedLabel": "2", "onHandLabel": "6", "onHandLocation": "pantry"}
    # Stock claimed as granted (pre_shop.get_pre_shop_flags): only the first
    # week's line flagged means the shelf covers that week alone.
    one = tools.stamp_week_groups([
        {"id": a, "item": "Onions", "quantity": "2", "pre_shop": flag},
        {"id": b, "item": "Onions", "quantity": "3"},
    ])
    assert one[0]["week_group"]["pre_shop"] is None
    both = tools.stamp_week_groups([
        {"id": a, "item": "Onions", "quantity": "2", "pre_shop": flag},
        {"id": b, "item": "Onions", "quantity": "3", "pre_shop": {**flag, "sentence": "You want 3. Fridge shows 4."}},
    ])
    assert both[0]["week_group"]["pre_shop"]["sentence"] == "You want 5. Fridge shows 6."


# ---------- the screen ----------

_SCREEN = """
const a = { id: 11, item: 'Onions', quantity: '2', store: '', store_decided: 1, category: 'produce', status: 'needed',
  source_weekly_plan_id: 1, week_group: { lead_id: 11, ids: [11, 12], total: '5',
  note: '2 this week \\u00b7 3 next week',
  parts: [{ id: 11, quantity: '2', label: 'this week' }, { id: 12, quantity: '3', label: 'next week' }] } };
const b = Object.assign({}, a, { id: 12, quantity: '3' });
const lone = { id: 13, item: 'Carrots', quantity: '4', store: '', store_decided: 1, category: 'produce', status: 'needed' };
groceryState.data = { stores: { Unassigned: { sections: [{ section: 'produce', items: groCollapseWeeks([a, b, lone]) }], purchased: [], inCart: [] } } };
groceryState.usualStores = [];
groceryState.storesPromptDismissed = true;
groceryState.step = 'list';
"""


@shop_harness.needs_node
def test_the_row_reads_onions_5_with_the_note_and_a_tick_ticks_both_weeks():
    out = shop_harness.run(_SCREEN + """
const html = screenHtml();
const rows = (html.match(/data-gro="line-tick" data-id="\\d+"/g) || []).filter(function (_, i) { return i % 2 === 0; });
clickIfRendered({ gro: 'line-tick', id: '11', bought: '0' });
settle(function () {
  console.log(JSON.stringify({
    onionRows: (html.match(/<span class="gro-name">Onions<\\/span>/g) || []).length,
    note: /<span class="gro-line-weeks">([^<]*)<\\/span>/.exec(html)[1],
    qty: /gro-name">Onions<\\/span><span class="gro-line-weeks">[^<]*<\\/span><\\/span><span class="gro-line-qty">([^<]*)</.exec(html)[1],
    carrotsPlain: html.indexOf('<span class="gro-name">Carrots</span><span class="gro-line-qty">4</span>') !== -1,
    ticked: purchases().sort()
  }));
});
""")
    assert out["onionRows"] == 1
    assert out["note"] == "2 this week · 3 next week"
    assert out["qty"] == "5"
    assert out["carrotsPlain"], "a one-week line looks exactly as it did"
    assert out["ticked"] == ["/api/grocery-list/11/status", "/api/grocery-list/12/status"]


@shop_harness.needs_node
def test_have_it_and_its_undo_reach_both_weeks():
    out = shop_harness.run(_SCREEN + """
groceryState.openRowId = '11';
clickIfRendered({ gro: 'have-it', id: '11', name: 'Onions' });
settle(function () {
  tapUndo();
  settle(function () {
    console.log(JSON.stringify({
      dropped: posts('/pre-shop').filter(function (p) { return p.url.indexOf('undo') === -1; }).map(function (p) { return p.url; }),
      undone: posts('/pre-shop-undo').map(function (p) { return p.url; })
    }));
  });
});
""")
    assert out["dropped"] == ["/api/grocery-list/11/pre-shop", "/api/grocery-list/12/pre-shop"]
    assert out["undone"] == ["/api/grocery-list/11/pre-shop-undo", "/api/grocery-list/12/pre-shop-undo"]


@shop_harness.needs_node
def test_remove_that_fails_for_one_week_puts_the_other_back_and_says_so():
    """Review, 2026-10-13: Remove sent both weeks' /remove at once; if one
    failed, the other was already off with no Undo and no trace."""
    out = shop_harness.run(_SCREEN + """
const REMOVE_BODY = { restorable: true };
fetch = function (url, opts) {
  POSTS.push({ url: url, body: JSON.parse((opts && opts.body) || '{}') });
  const failed = url === '/api/grocery-list/12/remove';
  const body = /\\/remove$/.test(url) ? REMOVE_BODY : {};
  return Promise.resolve({ ok: !failed, status: failed ? 500 : 200, json: function () { return Promise.resolve(body); } });
};
groceryState.openRowId = '11';
clickIfRendered({ gro: 'row-remove', id: '11', name: 'Onions', qty: '5', cat: 'produce', store: '' });
settle(function () {
  console.log(JSON.stringify({
    urls: POSTS.map(function (p) { return p.url; }).filter(function (u) { return /remove/.test(u); }),
    toast: lastToast()
  }));
});
""")
    assert out["urls"] == [
        "/api/grocery-list/11/remove", "/api/grocery-list/12/remove", "/api/grocery-list/11/remove-undo",
    ]
    assert out["toast"]["msg"] == "Couldn't remove that — try again."
    assert out["toast"]["action"] is None, "nothing half-done is left to undo"


@shop_harness.needs_node
def test_a_two_weeks_row_does_not_offer_make_it_a_staple():
    """Review, 2026-10-13: it made a staple of the two weeks' TOTAL tied to
    one week's line. A staple is one line with one amount."""
    out = shop_harness.run(_SCREEN + """
groceryState.openRowId = '11';
const combined = screenHtml();
groceryState.openRowId = '13';
const loneHtml = screenHtml();
console.log(JSON.stringify({
  combined: combined.indexOf('data-gro="row-staple"') !== -1,
  lone: loneHtml.indexOf('data-gro="row-staple"') !== -1
}));
""")
    assert out == {"combined": False, "lone": True}
