"""
Last week's leftovers ask before they add onto this week (Emily, 2026-09-13).

    "I'm sorting through it and some of the quantities are so high but I
    think it might be because it was adding on from last week's."

It was. approve_weekly_plan poured the new week's recipes onto whatever was
still unbought from the last one: add_grocery_item found last week's
"2 lbs chicken thighs", summed this week's 2 lbs into it and stamped the new
plan's id on the row — 4 lbs, no word on screen about why, and no cleanup
could ever take the old share back off because the row now belonged to the
new week. Any Sunday approval hit it, since clear_stale_grocery_items keeps
a plan alive through its last day.

Now the old lines are set aside first (status 'carried'), this week's
amounts land clean, and the Shop tab asks "Still on the list from last
week — keep or drop?" one screen before the list. These tests pin the
backend half; the screen is exercised in the node harness below.

Updated 2026-09-15 (Loop Board, "Shop: the list hides behind the store
question and the sort screen"): the screen used to hand over to the sort
queue once the last leftover was answered, and "Decide later" left
sorting to come next. SORT is never entered automatically any more —
groMaybeSortFirst became groMaybeCarryFirst, which asks about the
leftovers and nothing else — so the three node tests that expected
"sort" after CARRY now expect the list, with the unsorted things on it.
"""
from __future__ import annotations

import datetime
import json
import shutil
from pathlib import Path

import nodeharness
import pytest

from app import tools
from app.db import get_conn


REPO = Path(__file__).resolve().parent.parent
SHELL_JS = (REPO / "static" / "shell.js").read_text(encoding="utf-8")


def _monday(offset_weeks: int = 0) -> str:
    today = datetime.date.today()
    monday = today - datetime.timedelta(days=today.weekday())
    return (monday + datetime.timedelta(days=7 * offset_weeks)).isoformat()


def _rows() -> dict[str, dict]:
    conn = get_conn()
    rows = conn.execute(
        "SELECT id, item, quantity, status, source_weekly_plan_id, carried_from_plan_id FROM grocery_items ORDER BY id"
    ).fetchall()
    conn.close()
    out: dict[str, dict] = {}
    for r in rows:
        out.setdefault(r["item"], []).append(dict(r))
    return out


def _needed(item: str) -> str | None:
    return next((i["quantity"] for i in tools.list_grocery_list() if i["item"] == item), None)


@pytest.fixture
def curry():
    tools.add_recipe("Chicken curry", ingredients=[
        {"item": "Chicken thighs", "qty": "2 lb", "category": "meat/seafood"},
        {"item": "Onion", "qty": "2", "category": "produce"},
    ])


def _approve_week(offset_weeks: int, day_index: int = 0, meal: str = "Chicken curry") -> int:
    plan_id = tools.create_weekly_plan(_monday(offset_weeks))["weekly_plan_id"]
    if offset_weeks > 0:
        # What generate_weekly_plan does first — and what does NOT clear
        # last week's lines while last week still has a day left.
        tools.clear_stale_grocery_items(current_weekly_plan_id=plan_id)
    day = tools._week_dates(_monday(offset_weeks))[day_index]
    tools.plan_meal(day, meal, slot="dinner", weekly_plan_id=plan_id)
    tools.approve_weekly_plan(plan_id, approved_by="Emily")
    return plan_id


# ---------- the bug ----------

def test_this_weeks_recipes_land_on_their_own_lines(curry):
    """The headline: a week approved after an unfinished list yields this
    week's amounts alone, not this week's plus last week's."""
    week_a = _approve_week(0)
    assert _needed("Chicken thighs") == "2 lbs"
    week_b = _approve_week(1)

    assert _needed("Chicken thighs") == "2 lbs", "this week's recipe alone"
    assert _needed("Onion") == "2"
    rows = _rows()["Chicken thighs"]
    assert [r["status"] for r in rows] == ["carried", "needed"]
    assert rows[0]["carried_from_plan_id"] == week_a
    assert rows[0]["source_weekly_plan_id"] == week_a, "the old line still knows whose it was"
    assert rows[1]["source_weekly_plan_id"] == week_b


def test_the_carried_lines_are_listed_beside_this_weeks_amount(curry):
    _approve_week(0)
    _approve_week(1)
    carried = tools.list_carried_over_items()
    by_name = {c["item"]: c for c in carried}
    assert set(by_name) == {"Chicken thighs", "Onion"}
    assert by_name["Chicken thighs"]["quantity"] == "2 lbs"
    assert by_name["Chicken thighs"]["this_week_quantity"] == "2 lbs"


def test_a_leftover_this_week_does_not_want_shows_no_this_week_amount(curry):
    tools.add_recipe("Toast", ingredients=[{"item": "Bread", "qty": "1 loaf", "category": "pantry"}])
    _approve_week(0)
    _approve_week(1, meal="Toast")
    carried = {c["item"]: c for c in tools.list_carried_over_items()}
    assert carried["Chicken thighs"]["this_week_quantity"] is None
    assert _needed("Chicken thighs") is None, "off the buy list until kept"
    assert _needed("Bread") == "1 loaf"


def test_carried_lines_are_off_every_needed_view_and_count(curry):
    _approve_week(0)
    _approve_week(1)
    by_section = tools.get_grocery_list_by_section()
    names = [it["item"] for s in by_section["sections"] for it in s["items"]]
    assert names.count("Chicken thighs") == 1
    by_store = tools.get_grocery_list_by_store()
    names = [it["item"] for st in by_store["stores"] for s in st["sections"] for it in s["items"]]
    assert names.count("Chicken thighs") == 1


def test_approval_reports_what_it_set_aside(curry):
    _approve_week(0)
    plan_b = tools.create_weekly_plan(_monday(1))["weekly_plan_id"]
    day = tools._week_dates(_monday(1))[0]
    tools.plan_meal(day, "Chicken curry", slot="dinner", weekly_plan_id=plan_b)
    result = tools.approve_weekly_plan(plan_b, approved_by="Emily")
    assert result["carried_over_count"] == 2
    assert {c["item"] for c in result["carried_over"]} == {"Chicken thighs", "Onion"}


# ---------- what is NOT a leftover ----------

def test_a_hand_added_want_is_left_alone(curry):
    """A person's own line is a standing want, not a leftover — it merges
    with the plan exactly as before (and stays a standing want)."""
    tools.add_grocery_item("Chicken thighs", "1 lb", category="meat/seafood")
    _approve_week(1)
    assert _needed("Chicken thighs") == "3 lbs"
    assert tools.list_carried_over_items() == []
    assert _rows()["Chicken thighs"][0]["source_weekly_plan_id"] is None


def test_a_week_that_has_not_started_is_not_last_week(curry):
    """Approving two weeks ahead builds next week's list; nobody has had a
    chance to buy it, so there is nothing to keep or drop."""
    _approve_week(1)
    _approve_week(2)
    assert tools.list_carried_over_items() == []
    assert _needed("Chicken thighs") == "4 lbs", "the pre-carry-over behaviour, unchanged"


def test_an_excluded_or_in_cart_line_is_the_shoppers_not_the_plans(curry):
    _approve_week(0)
    thighs = next(i for i in tools.list_grocery_list() if i["item"] == "Chicken thighs")
    onion = next(i for i in tools.list_grocery_list() if i["item"] == "Onion")
    tools.exclude_grocery_item(thighs["id"])
    tools.mark_grocery_item(onion["id"], status="in_cart")
    _approve_week(1)
    assert tools.list_carried_over_items() == []


def test_a_staple_suggestion_is_not_a_leftover(curry):
    _approve_week(0)
    conn = get_conn()
    conn.execute("UPDATE grocery_items SET staple_id = 99 WHERE item = 'Onion'")
    conn.commit()
    conn.close()
    _approve_week(1)
    assert {c["item"] for c in tools.list_carried_over_items()} == {"Chicken thighs"}


def test_re_approving_sets_nothing_aside(curry):
    week_b_id = None
    _approve_week(0)
    week_b_id = _approve_week(1)
    before = tools.list_carried_over_items()
    tools.approve_weekly_plan(week_b_id, approved_by="Emily")
    assert tools.list_carried_over_items() == before
    assert _needed("Chicken thighs") == "2 lbs"


# ---------- keep / drop / undo ----------

def _carried(item: str) -> dict:
    return next(c for c in tools.list_carried_over_items() if c["item"] == item)


def test_keep_adds_the_old_amount_onto_this_weeks_line_out_loud(curry):
    _approve_week(0)
    _approve_week(1)
    row = _carried("Chicken thighs")
    result = tools.keep_carried_over_item(row["item_id"])
    assert result["kept"] and result["merged_into"] is not None
    assert _needed("Chicken thighs") == "4 lbs", "the sum, now that it was asked for"
    assert [c["item"] for c in tools.list_carried_over_items()] == ["Onion"]
    # The kept row is soft-removed, not on the wrap-up's already-have list.
    assert all(d["item"] != "Chicken thighs" for d in tools.get_already_have_decisions())


def test_keep_restores_a_line_this_week_did_not_want_as_a_standing_want(curry):
    tools.add_recipe("Toast", ingredients=[{"item": "Bread", "qty": "1 loaf", "category": "pantry"}])
    _approve_week(0)
    _approve_week(1, meal="Toast")
    row = _carried("Chicken thighs")
    result = tools.keep_carried_over_item(row["item_id"])
    assert result["merged_into"] is None
    assert _needed("Chicken thighs") == "2 lbs"
    kept = _rows()["Chicken thighs"][0]
    assert kept["status"] == "needed"
    assert kept["source_weekly_plan_id"] is None, "asked for, so never auto-cleared"


def test_drop_takes_it_off_and_undo_brings_the_question_back(curry):
    _approve_week(0)
    _approve_week(1)
    row = _carried("Onion")
    tools.drop_carried_over_item(row["item_id"])
    assert [c["item"] for c in tools.list_carried_over_items()] == ["Chicken thighs"]
    assert _needed("Onion") == "2", "this week's onions untouched"
    assert all(d["item"] != "Onion" for d in tools.get_already_have_decisions())
    tools.undo_carried_over_decision(row["item_id"])
    assert {c["item"] for c in tools.list_carried_over_items()} == {"Chicken thighs", "Onion"}


def test_undo_of_a_merged_keep_takes_the_amount_back_off(curry):
    _approve_week(0)
    _approve_week(1)
    row = _carried("Chicken thighs")
    tools.keep_carried_over_item(row["item_id"])
    assert _needed("Chicken thighs") == "4 lbs"
    tools.undo_carried_over_decision(row["item_id"])
    assert _needed("Chicken thighs") == "2 lbs"
    assert _carried("Chicken thighs")["quantity"] == "2 lbs"


def test_undo_of_a_restored_keep_goes_back_to_waiting(curry):
    tools.add_recipe("Toast", ingredients=[{"item": "Bread", "qty": "1 loaf", "category": "pantry"}])
    week_a = _approve_week(0)
    _approve_week(1, meal="Toast")
    row = _carried("Chicken thighs")
    tools.keep_carried_over_item(row["item_id"])
    tools.undo_carried_over_decision(row["item_id"])
    assert _needed("Chicken thighs") is None
    back = _rows()["Chicken thighs"][0]
    assert back["status"] == "carried" and back["source_weekly_plan_id"] == week_a


def test_undo_of_a_keep_that_cannot_come_back_off_does_not_reopen(curry):
    """Verifier, 2026-09-13: a keep merged into a line it can't be
    subtracted back out of, or a line since bought, can't be undone —
    reopening the question would count it twice. The pair here is a bag
    kept onto a bag of a stated size ("2 bags (2 lb)" has no plain bag to
    take off); it used to be "1 bag + 2 lbs", which Keep no longer writes
    — see test_grocery_unit_families.py."""
    tools.add_recipe("Frozen thighs", ingredients=[
        {"item": "Chicken thighs", "qty": "1 bag", "category": "frozen"}])
    tools.add_recipe("Big bag of thighs", ingredients=[
        {"item": "Chicken thighs", "qty": "2 lb bag", "category": "frozen"}])
    _approve_week(0, meal="Frozen thighs")
    _approve_week(1, meal="Big bag of thighs")
    row = _carried("Chicken thighs")
    tools.keep_carried_over_item(row["item_id"])
    assert _needed("Chicken thighs") == "2 bags (2 lb)"
    answer = tools.undo_carried_over_decision(row["item_id"])
    assert answer["unchanged"] is True and answer["reason"] == "acted_on"
    assert _needed("Chicken thighs") == "2 bags (2 lb)"
    assert all(c["item"] != "Chicken thighs" for c in tools.list_carried_over_items()), "not asked again"


def test_keep_never_glues_last_weeks_amount_onto_a_line_in_another_unit(curry):
    """Last week's "2 lbs" beside this week's "1 bag": Keep used to write
    "1 bag + 2 lbs", which nothing could take back apart. The old line
    comes back on its own instead (the rule in test_grocery_unit_families.py)."""
    tools.add_recipe("Frozen thighs", ingredients=[
        {"item": "Chicken thighs", "qty": "1 bag", "category": "frozen"}])
    _approve_week(0)
    _approve_week(1, meal="Frozen thighs")
    row = _carried("Chicken thighs")
    assert row["this_week_quantity"] is None
    result = tools.keep_carried_over_item(row["item_id"])
    assert result["merged_into"] is None
    assert [r["quantity"] for r in _rows()["Chicken thighs"] if r["status"] == "needed"] == ["2 lbs", "1 bag"]
    tools.undo_carried_over_decision(row["item_id"])
    assert _needed("Chicken thighs") == "1 bag"
    assert _carried("Chicken thighs")["quantity"] == "2 lbs"


def test_undo_of_a_keep_after_the_line_was_bought_does_not_reopen(curry):
    _approve_week(0)
    _approve_week(1)
    row = _carried("Chicken thighs")
    tools.keep_carried_over_item(row["item_id"])
    bought = next(i for i in tools.list_grocery_list() if i["item"] == "Chicken thighs")
    tools.mark_grocery_item(bought["id"], "purchased")
    answer = tools.undo_carried_over_decision(row["item_id"])
    assert answer["unchanged"] is True
    assert all(c["item"] != "Chicken thighs" for c in tools.list_carried_over_items())


def test_answers_are_idempotent_and_undo_leaves_a_never_carried_row_alone(curry):
    _approve_week(0)
    _approve_week(1)
    row = _carried("Onion")
    tools.drop_carried_over_item(row["item_id"])
    assert tools.drop_carried_over_item(row["item_id"])["unchanged"] is True
    assert tools.keep_carried_over_item(row["item_id"])["unchanged"] is True
    this_week = next(i for i in tools.list_grocery_list() if i["item"] == "Onion")
    assert tools.undo_carried_over_decision(this_week["id"])["unchanged"] is True
    assert _needed("Onion") == "2"


def test_a_stale_carried_line_is_cleared_with_the_rest_once_its_week_is_gone(curry):
    """Never answered, and its plan has gone by: the same stale leftover
    clear_stale_grocery_items has always removed."""
    week_a = _approve_week(0)
    _approve_week(1)
    conn = get_conn()
    conn.execute(
        "UPDATE weekly_plans SET week_start_date = ?, content_start_date = '', day_count = 0 WHERE id = ?",
        (_monday(-3), week_a),
    )
    conn.commit()
    conn.close()
    plan_c = tools.create_weekly_plan(_monday(2))["weekly_plan_id"]
    removed = tools.clear_stale_grocery_items(current_weekly_plan_id=plan_c)
    assert sorted(removed["removed_items"]) == ["Chicken thighs", "Onion"]
    assert tools.list_carried_over_items() == []


# ---------- over HTTP ----------

def test_the_routes(signed_in, curry):
    _approve_week(0)
    _approve_week(1)
    res = signed_in.get("/api/grocery-list/carried-over")
    assert res.status_code == 200
    items = res.json()["items"]
    assert {i["item"] for i in items} == {"Chicken thighs", "Onion"}
    thighs = next(i for i in items if i["item"] == "Chicken thighs")
    assert thighs["this_week_quantity"] == "2 lbs"

    res = signed_in.post(f"/api/grocery-list/{thighs['item_id']}/carried-over", json={"decision": "keep"})
    assert res.status_code == 200 and res.json()["kept"]
    assert _needed("Chicken thighs") == "4 lbs"
    res = signed_in.post(f"/api/grocery-list/{thighs['item_id']}/carried-over-undo")
    assert res.status_code == 200
    assert _needed("Chicken thighs") == "2 lbs"
    res = signed_in.post(f"/api/grocery-list/{thighs['item_id']}/carried-over", json={"decision": "maybe"})
    assert res.status_code == 400
    res = signed_in.post("/api/grocery-list/999999/carried-over", json={"decision": "drop"})
    assert res.status_code == 404


# ---------- the screen ----------

_needs_node = pytest.mark.skipif(
    shutil.which("node") is None, reason="node is needed to execute the shell's own functions"
)

_STUB = """
function escapeHtml(s){return String(s == null ? '' : s)
  .replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');}
const POSTS = [];
let CARRIED = [];
let ROWS = [];
// A live panel, so groDo's re-read (loadGrocery) actually runs and the
// handlers see the server's next answer, the way they do on the phone.
function fetch(url, opts) {
  function reply(body) {
    return Promise.resolve({ ok: true, json: function () { return Promise.resolve(body); } });
  }
  if (url === '/api/grocery-list/carried-over') return reply({ items: CARRIED });
  if (url.indexOf('/api/grocery-list/by-store') === 0) {
    return reply({ stores: [{ store: 'Unassigned', sections: [{ section: 'other', items: ROWS }] }] });
  }
  if (opts && opts.method === 'POST') POSTS.push({ url: url, body: JSON.parse((opts && opts.body) || '{}') });
  return reply({});
}
const panels = { grocery: { dataset: { built: '1' }, querySelector: function () { return null; } } };
const TOASTS = [];
function showToast(msg, action, hold) { TOASTS.push({ msg: msg, action: action, hold: hold }); }
function tapUndo() {
  for (let i = TOASTS.length - 1; i >= 0; i--) {
    if (TOASTS[i].action && TOASTS[i].action.onClick) { TOASTS[i].action.onClick(); return true; }
  }
  return false;
}
const STORE = new Map();
const window = {
  localStorage: {
    getItem: function (k) { return STORE.has(k) ? STORE.get(k) : null; },
    setItem: function (k, v) { STORE.set(k, String(v)); },
    removeItem: function (k) { STORE.delete(k); }
  },
  history: { pushState: function () {} },
  confirm: function () { return true; }
};
var scrollEl = null;
function activateTab() {}
var coachState = { householdId: 1 };
function fakeEl(dataset, row) {
  return {
    dataset: dataset, disabled: false,
    closest: function () { return row || null; },
    classList: { toggle: function () {} },
    setAttribute: function () {},
    querySelectorAll: function () { return []; }
  };
}
function click(dataset, row) {
  const el = fakeEl(dataset, row);
  onGroceryClick({ target: { closest: function () { return el; } } });
  return el;
}
function settle(fn) { setTimeout(fn, 30); }
function listOf(n) {
  const rows = [];
  for (let i = 1; i <= n; i++) rows.push({ id: i, item: 'Thing ' + i, quantity: '1', store: '', store_decided: 0 });
  return { stores: { Unassigned: { sections: [{ section: 'other', items: rows }], purchased: [], inCart: [] } } };
}
function setUp(n, carried) {
  groceryState.data = listOf(n);
  ROWS = groceryState.data.stores.Unassigned.sections[0].items;
  groceryState.usualStores = ['Loblaws', 'Costco'];
  groceryState.storesPromptDismissed = true;
  groceryState.carried = carried || [];
  CARRIED = carried || [];
  return groceryState.data;
}
"""


def _grocery_block() -> str:
    start = SHELL_JS.index("  var GRO_ICONS = {")
    end = SHELL_JS.index("  // ---------- Hands-free voice ----------", start)
    return SHELL_JS[start:end]


def _node(body: str):
    res = nodeharness.run_node(_STUB + _grocery_block() + body, timeout=30)
    assert res.returncode == 0, f"node failed: {res.stderr}"
    return json.loads(res.stdout.strip())


_CARRIED = """[
  { item_id: 41, item: 'Chicken thighs', quantity: '2 lbs', category: 'meat/seafood', store: '', this_week_quantity: '2 lbs' },
  { item_id: 42, item: 'Onion', quantity: '2', category: 'produce', store: '', this_week_quantity: null }
]"""


@_needs_node
def test_the_leftovers_come_before_the_list_and_show_both_amounts():
    out = _node("""
setUp(3, %s);
groMaybeCarryFirst();
const head = groHeadFor(groceryState.data, groceryState.step);
const html = groCarryHtml(groceryState.data);
console.log(JSON.stringify({ step: groceryState.step, title: head.title, sub: head.sub, html: html, dock: groDockHtml(groceryState.data, 'carry') }));
""" % _CARRIED)
    assert out["step"] == "carry", "asked before the sort queue, not after"
    assert out["title"] == "Still on the list from last week"
    assert "2 things" in out["sub"]
    html = out["html"]
    assert "Chicken thighs" in html and "Onion" in html
    assert "This week&rsquo;s recipes: 2 lbs" in html or "This week’s recipes: 2 lbs" in html, "this week's amount shown separately, never summed in"
    assert html.count('data-decision="keep"') == 2 and html.count('data-decision="drop"') == 2
    assert 'data-gro="carry-later"' in html, "never a trap"
    assert out["dock"] == "", "no single action, so no dock (rule 2)"


@_needs_node
def test_answering_the_last_one_moves_on_to_the_list():
    out = _node("""
setUp(3, %s);
groMaybeCarryFirst();
click({ gro: 'carry-decide', decision: 'drop', id: '41', name: 'Chicken thighs' });
settle(function () {
  const afterFirst = groceryState.step;
  CARRIED = [];
  click({ gro: 'carry-decide', decision: 'keep', id: '42', name: 'Onion' });
  settle(function () {
    console.log(JSON.stringify({ afterFirst: afterFirst, step: groceryState.step, posts: POSTS,
      toasts: TOASTS.map(function (t) { return [t.msg, t.action ? t.action.label : null]; }) }));
  });
});
""" % _CARRIED)
    assert out["afterFirst"] == "carry", "one answered, one still to go"
    assert out["step"] == "list", "the last answer hands over to the list (2026-09-15; it used to open the sort queue)"
    assert out["posts"][0] == {"url": "/api/grocery-list/41/carried-over", "body": {"decision": "drop"}}
    assert out["posts"][1] == {"url": "/api/grocery-list/42/carried-over", "body": {"decision": "keep"}}
    assert out["toasts"][0] == ["Chicken thighs off the list", "Undo"]
    assert out["toasts"][1] == ["Onion kept", "Undo"]


@_needs_node
def test_undo_goes_to_the_undo_route_and_later_is_remembered_for_the_visit():
    out = _node("""
setUp(3, %s);
groMaybeCarryFirst();
click({ gro: 'carry-decide', decision: 'keep', id: '41', name: 'Chicken thighs' });
settle(function () {
  tapUndo();
  settle(function () {
    const undoPost = POSTS[POSTS.length - 1];
    click({ gro: 'carry-later' });
    const stepAfterLater = groceryState.step;
    groMaybeCarryFirst();
    console.log(JSON.stringify({ undo: undoPost, later: stepAfterLater, again: groceryState.step,
      row: groListHtml(groceryState.data).indexOf('data-gro="goto-carry"') !== -1 }));
  });
});
""" % _CARRIED)
    assert out["undo"] == {"url": "/api/grocery-list/41/carried-over-undo", "body": {}}
    assert out["later"] == "list"
    assert out["again"] == "list", "later means later: the leftovers don't bounce back, and the list never opens SORT itself (2026-09-15)"
    assert out["row"] is True, "the way back in is a row at the top of the list"


@_needs_node
def test_with_nothing_carried_over_the_screen_never_appears():
    out = _node("""
setUp(3, []);
groMaybeCarryFirst();
console.log(JSON.stringify({ step: groceryState.step, row: groListHtml(groceryState.data).indexOf('goto-carry') }));
""")
    assert out["step"] == "list", "the list, with its things to sort on it (until 2026-09-15 this was the sort queue)"
    assert out["row"] == -1
