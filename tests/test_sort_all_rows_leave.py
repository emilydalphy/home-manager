"""
Sort them all: a row leaves the screen the moment I sort it (Emily,
2026-09-13, sorting on her phone):

    "As I sort things on the 'all at once' list, if I put it into a
    category it should no longer be on that screen. Then I get the
    satisfaction of going through the whole list and finishing it."

Before this, SORT ALL staged every pick in groceryState.sortAllPicks and
wrote them all from "That's them sorted" at the foot — so a sorted row sat
exactly where it was, looking undone, and the screen never emptied. The
original author staged for one reason: writing per tap re-rendered forty
rows and moved the list under the thumb. This slice keeps that promise a
different way:

  * a tap writes at once — the one-row /store route the queue uses, with
    remember:false as the bulk path always had — and the row collapses out
    (groSortAllLeave: --motion-fast, --motion-ease-in, gone at once under
    prefers-reduced-motion);
  * the card is never rebuilt once it is up. groSortAllRender takes away
    the row that left, puts back a row that returned (Undo), and leaves
    every other node alone; only the first draw and the finish line are a
    full render;
  * "N to sort" counts down; each pick has its own undo toast ("Eggs →
    Costco · Undo"), which is the existing bulk undo carrying one row so
    the row comes back exactly as it was — never answered, not "Any";
  * zero is a finish: "All sorted." and "Back to the list" in the dock.
    Undoing the last row takes the finish away again;
  * the foot button is gone — nothing is staged, so there was nothing for
    it to save.

The behaviour runs under node against shell.js's own functions
(tests/test_grocery_fast_sort.py's harness, plus a small fake of the row
nodes groSortAllRender touches). The route pair the undo relies on is
driven over real HTTP.
"""
from __future__ import annotations

import json
import re
import shutil
from pathlib import Path

import pytest

import nodeharness
from app import tools
from test_grocery_fast_sort import _CLICK, _FIXTURE, _STUB, _grocery_block

REPO = Path(__file__).resolve().parent.parent
SHELL_JS = (REPO / "static" / "shell.js").read_text(encoding="utf-8")
SHELL_CSS = (REPO / "static" / "shell.css").read_text(encoding="utf-8")
DESIGN_SYSTEM = (REPO / "DESIGN_SYSTEM.md").read_text(encoding="utf-8")

_needs_node = pytest.mark.skipif(
    shutil.which("node") is None, reason="node is needed to execute the shell's own functions"
)

# Just enough of a DOM for groSortAllRender: a body holding one card
# holding row nodes. Rows are parsed out of the HTML the renderer itself
# produces (data-row-for / data-sig are its own attributes), so what is
# asserted is which NODES survive a render, which is the whole point.
_DOM = """
// The harness's tapped element, with the classList a lit chip needs.
function fakeEl(dataset, row) {
  const classes = new Set();
  return {
    dataset: dataset, disabled: false,
    closest: function () { return row || null; },
    classList: { toggle: function (c, on) { if (on) classes.add(c); else classes.delete(c); },
                 add: function (c) { classes.add(c); }, contains: function (c) { return classes.has(c); } },
    setAttribute: function () {},
    querySelectorAll: function () { return []; }
  };
}
var RENDERS = 0;
renderGrocery = function () { RENDERS += 1; };
function emptyMomentHtml(icon, line) { return '<div class="empty-moment" data-icon="' + icon + '">' + line + '</div>'; }
var REDUCE = false;
window.matchMedia = function () { return { matches: REDUCE }; };
var TIMERS = [];
function parseRows(html) {
  const out = [];
  const re = /<div class="gro-sortall-row" data-row-for="([^"]*)" data-sig="([^"]*)">/g;
  let m;
  while ((m = re.exec(html))) out.push(new Row(m[1], m[2]));
  return out;
}
function Row(id, sig) {
  this.id = id; this.sig = sig; this.classes = new Set(); this.style = {};
  this.parent = null; this.offsetHeight = 60; this.attrs = { 'data-row-for': id, 'data-sig': sig };
  this.listeners = {};
}
Row.prototype.getAttribute = function (k) { return this.attrs[k] === undefined ? null : this.attrs[k]; };
Row.prototype.setAttribute = function (k, v) { this.attrs[k] = String(v); };
Row.prototype.removeAttribute = function (k) { delete this.attrs[k]; };
Row.prototype.querySelectorAll = function () { return []; };
Object.defineProperty(Row.prototype, 'classList', { get: function () {
  const self = this;
  return { add: function (c) { self.classes.add(c); }, contains: function (c) { return self.classes.has(c); },
           remove: function (c) { self.classes.delete(c); } };
} });
Row.prototype.addEventListener = function (ev, fn) { this.listeners[ev] = fn; };
Row.prototype.remove = function () { if (this.parent) this.parent.removeChild(this); };
Object.defineProperty(Row.prototype, 'outerHTML', { set: function (html) {
  const fresh = parseRows(html)[0];
  this.parent.replaceChild(fresh, this);
} });
function Card() { this.children = []; }
Card.prototype.querySelectorAll = function () { return this.children.slice(); };
Card.prototype.querySelector = function (sel) {
  const m = /data-row-for="([^"]*)"/.exec(sel);
  return this.children.filter(function (r) { return r.id === m[1]; })[0] || null;
};
Card.prototype.removeChild = function (r) { this.children.splice(this.children.indexOf(r), 1); r.parent = null; };
Card.prototype.replaceChild = function (fresh, old) { this.children[this.children.indexOf(old)] = fresh; fresh.parent = this; old.parent = null; };
Card.prototype.insertBefore = function (node, next) {
  node.parent = this;
  if (!next) { this.children.push(node); return; }
  this.children.splice(this.children.indexOf(next), 0, node);
};
function Body() { this.card = null; this.html = ''; }
Body.prototype.querySelector = function (sel) { return sel === '.gro-sortall' ? this.card : null; };
Object.defineProperty(Body.prototype, 'innerHTML', {
  set: function (html) {
    this.html = html;
    const rows = parseRows(html);
    if (!rows.length) { this.card = null; return; }
    this.card = new Card();
    rows.forEach(function (r) { this.card.insertBefore(r, null); }, this);
  },
  get: function () { return this.html; }
});
const document = { createElement: function () {
  const holder = {};
  Object.defineProperty(holder, 'innerHTML', { set: function (html) { holder.firstChild = parseRows(html)[0]; } });
  return holder;
} };
function ids(body) { return body.card ? body.card.children.map(function (r) { return r.id; }) : []; }
function markSorted(id, store) {
  groUnsorted(groceryState.data).forEach(function (it) {
    if (String(it.id) === String(id)) { it.store_decided = 1; it.store = store || ''; }
  });
}
function markUnsorted(id) {
  groceryState.data.stores.Unassigned.sections[0].items.forEach(function (it) {
    if (String(it.id) === String(id)) { it.store_decided = 0; it.store = ''; }
  });
}
"""


def _node(body: str):
    script = _STUB + _grocery_block() + _CLICK + _FIXTURE + _DOM + body
    res = nodeharness.run_node(script, timeout=30)
    assert res.returncode == 0, f"node failed: {res.stderr}"
    return json.loads(res.stdout.strip())


# --- 1. a tap writes, and the row leaves ---------------------------------


@_needs_node
def test_a_tap_writes_that_one_row_at_once_and_it_leaves_the_queue():
    out = _node("""
setUp(8);
groceryState.step = 'sortall';
const before = groUnsorted(groceryState.data).length;
click({ gro: 'sortall-pick', id: '3', store: 'Costco' });
const leftAtOnce = groUnsorted(groceryState.data).length;
const renderedAtOnce = RENDERS;
settle(function () {
  console.log(JSON.stringify({
    before: before, leftAtOnce: leftAtOnce, renderedAtOnce: renderedAtOnce,
    posts: POSTS.map(function (p) { return [p.url, p.body]; }),
    head: groHeadFor(groceryState.data, 'sortall').sub
  }));
});
""")
    assert out["before"] == 8
    assert out["leftAtOnce"] == 7, "the row is out of the queue before the request comes back"
    assert out["renderedAtOnce"] >= 1, "…and the screen is redrawn (the collapse) right away"
    assert out["posts"][0] == ["/api/grocery-list/3/store", {"store": "Costco", "remember": False}], (
        "one row, the queue's own route, this week only — never the forty-row bulk route"
    )
    assert not any("store-bulk" in p[0] for p in out["posts"])


@_needs_node
def test_the_count_in_the_head_drops_by_one_per_tap():
    out = _node("""
setUp(5);
groceryState.step = 'sortall';
const subs = [groHeadFor(groceryState.data, 'sortall').sub];
['1', '2', '3'].forEach(function (id) {
  click({ gro: 'sortall-pick', id: id, store: 'Loblaws' });
  subs.push(groHeadFor(groceryState.data, 'sortall').sub);
});
console.log(JSON.stringify(subs));
""")
    assert out == ["5 to sort", "4 to sort", "3 to sort", "2 to sort"]


@_needs_node
def test_a_second_tap_on_a_row_that_is_already_going_writes_nothing_more():
    """A double-tap, or a tap on a second chip while the row collapses."""
    out = _node("""
setUp(4);
groceryState.step = 'sortall';
click({ gro: 'sortall-pick', id: '2', store: 'Costco' });
click({ gro: 'sortall-pick', id: '2', store: 'Loblaws' });
click({ gro: 'sortall-pick', id: '4', store: '' });
settle(function () {
  console.log(JSON.stringify(POSTS.map(function (p) { return p.url; })));
});
""")
    assert out == ["/api/grocery-list/2/store", "/api/grocery-list/4/store"], "the first answer stands; the second row is its own write"


# --- 2. the card is not rebuilt: nodes survive a render -------------------


@_needs_node
def test_a_render_takes_away_only_the_row_that_left():
    out = _node("""
setUp(6);
groceryState.step = 'sortall';
const body = new Body();
groSortAllRender(body, groceryState.data);
const firstDraw = ids(body);
const nodesBefore = body.card.children.slice();
markSorted('3', 'Costco');
groSortAllRender(body, groceryState.data);
const leaving = body.card.children.filter(function (r) { return r.classes.has('is-leaving'); }).map(function (r) { return r.id; });
// The collapse's own end — the node goes on transitionend.
body.card.children.forEach(function (r) { if (r.listeners.transitionend) r.listeners.transitionend(); });
const survivors = body.card.children;
const sameNodes = survivors.every(function (r) { return nodesBefore.indexOf(r) !== -1; });
console.log(JSON.stringify({ firstDraw: firstDraw, leaving: leaving, after: ids(body), sameNodes: sameNodes }));
""")
    assert out["firstDraw"] == ["1", "2", "3", "4", "5", "6"]
    assert out["leaving"] == ["3"], "the sorted row collapses; nothing else is touched"
    assert out["after"] == ["1", "2", "4", "5", "6"]
    assert out["sameNodes"] is True, "the other five are the same nodes — the card was not rebuilt"


@_needs_node
def test_under_reduced_motion_the_row_is_simply_gone():
    out = _node("""
setUp(3);
groceryState.step = 'sortall';
REDUCE = true;
const body = new Body();
groSortAllRender(body, groceryState.data);
markSorted('2', 'Costco');
groSortAllRender(body, groceryState.data);
console.log(JSON.stringify({ after: ids(body), anyLeaving: body.card.children.some(function (r) { return r.classes.has('is-leaving'); }) }));
""")
    assert out == {"after": ["1", "3"], "anyLeaving": False}


@_needs_node
def test_an_undone_row_comes_back_where_it_was():
    out = _node("""
setUp(6);
groceryState.step = 'sortall';
const body = new Body();
groSortAllRender(body, groceryState.data);
markSorted('3', 'Costco');
groSortAllRender(body, groceryState.data);
body.card.children.forEach(function (r) { if (r.listeners.transitionend) r.listeners.transitionend(); });
const gone = ids(body);
const nodesBefore = body.card.children.slice();
markUnsorted('3');
groSortAllRender(body, groceryState.data);
const back = ids(body);
const othersKept = nodesBefore.every(function (n) { return body.card.children.indexOf(n) !== -1; });
console.log(JSON.stringify({ gone: gone, back: back, othersKept: othersKept }));
""")
    assert out["gone"] == ["1", "2", "4", "5", "6"]
    assert out["back"] == ["1", "2", "3", "4", "5", "6"], "back between 2 and 4, not at the end"
    assert out["othersKept"] is True


@_needs_node
def test_a_row_wanted_back_while_still_collapsing_is_the_same_node_not_a_second_one():
    """Found by the verifier: a write that fails inside the 180ms (or an
    undo that quick) put the row back in the list while its node was still
    leaving — and the render drew a second node beside the first. The
    leaving is called off instead, and the old node's own removal timer
    must not take it away afterwards."""
    out = _node("""
setUp(4);
groceryState.step = 'sortall';
const body = new Body();
groSortAllRender(body, groceryState.data);
const original = body.card.children[1];
markSorted('2', 'Costco');
groSortAllRender(body, groceryState.data);
const leaving = original.classes.has('is-leaving');
markUnsorted('2');
groSortAllRender(body, groceryState.data);
const afterBack = { ids: ids(body), sameNode: body.card.children[1] === original,
  stillLeaving: original.classes.has('is-leaving') || original.classes.has('is-gone'),
  hidden: original.getAttribute('aria-hidden'), height: original.style.height };
// The stale leaving's transitionend / timer fire now — the row must stay.
if (original.listeners.transitionend) original.listeners.transitionend();
setTimeout(function () {
  console.log(JSON.stringify({ leaving: leaving, afterBack: afterBack, later: ids(body) }));
}, 300);
""")
    assert out["leaving"] is True
    assert out["afterBack"] == {"ids": ["1", "2", "3", "4"], "sameNode": True, "stillLeaving": False, "hidden": None, "height": ""}
    assert out["later"] == ["1", "2", "3", "4"], "the disowned timer did not remove it"


@_needs_node
def test_a_row_that_did_not_change_is_not_redrawn_and_one_that_did_is():
    """The "Use something else" field opening under a row is a change to
    that row only."""
    out = _node("""
setUp(3);
groceryState.step = 'sortall';
const body = new Body();
groSortAllRender(body, groceryState.data);
const before = body.card.children.slice();
groceryState.substOpenId = '2';
groSortAllRender(body, groceryState.data);
const after = body.card.children;
console.log(JSON.stringify({
  sameFirst: after[0] === before[0], sameLast: after[2] === before[2],
  secondRedrawn: after[1] !== before[1], secondHasField: after[1].sig.indexOf('subst') !== -1,
  order: ids(body)
}));
""")
    assert out == {"sameFirst": True, "sameLast": True, "secondRedrawn": True, "secondHasField": True, "order": ["1", "2", "3"]}


# --- 3. undo: one toast per pick, the row exactly as it was ---------------


@_needs_node
def test_each_pick_has_its_own_undo_that_restores_the_row_unanswered():
    out = _node("""
setUp(4);
groceryState.step = 'sortall';
click({ gro: 'sortall-pick', id: '2', store: 'Costco' });
settle(function () {
  const toast = lastToast();
  const undoPayload = JSON.parse(JSON.stringify(groceryState.bulkUndo));
  tapUndo();
  settle(function () {
    const undoPost = POSTS.filter(function (p) { return p.url === '/api/grocery-list/store-bulk'; });
    console.log(JSON.stringify({
      toast: toast, undoPayload: undoPayload,
      undoPost: undoPost.map(function (p) { return p.body; }),
      afterUndo: lastToast().msg
    }));
  });
});
""")
    assert out["toast"] == {"msg": "Thing 2 → Costco", "action": "Undo", "hold": 8000}
    assert out["undoPayload"] == [{"item_id": 2, "store": "", "decided": False}], "as the row was: never answered"
    assert out["undoPost"] == [{"assignments": [{"item_id": 2, "store": "", "decided": False}], "remember": False}]
    assert out["afterUndo"] == "Put back."


@_needs_node
def test_the_any_chip_says_so_in_its_toast():
    out = _node("""
setUp(2);
groceryState.step = 'sortall';
click({ gro: 'sortall-pick', id: '1', store: '' });
settle(function () { console.log(JSON.stringify(lastToast().msg)); });
""")
    assert out == "Thing 1 → any store"


@_needs_node
def test_undo_after_the_next_pick_undoes_the_latest_pick_only():
    out = _node("""
setUp(4);
groceryState.step = 'sortall';
click({ gro: 'sortall-pick', id: '1', store: 'Costco' });
settle(function () {
  click({ gro: 'sortall-pick', id: '2', store: 'Loblaws' });
  settle(function () {
    tapUndo();
    settle(function () {
      const undo = POSTS.filter(function (p) { return p.url === '/api/grocery-list/store-bulk'; });
      console.log(JSON.stringify(undo.map(function (p) { return p.body.assignments; })));
    });
  });
});
""")
    assert out == [[{"item_id": 2, "store": "", "decided": False}]], "the toast is the latest pick's; Thing 1 stays sorted"


@_needs_node
def test_two_quick_taps_leave_the_latest_taps_undo_on_screen_even_if_its_answer_lands_first():
    """Two requests in flight; the older one's answer arrives second. Its
    toast — and its undo — must not paper over the newer pick's."""
    out = _node("""
setUp(4);
groceryState.step = 'sortall';
const plain = fetch;
fetch = function (url, opts) {
  const p = plain(url, opts);
  if (url === '/api/grocery-list/1/store') return new Promise(function (r) { setTimeout(function () { r(p); }, 20); });
  return p;
};
click({ gro: 'sortall-pick', id: '1', store: 'Costco' });
click({ gro: 'sortall-pick', id: '2', store: 'Loblaws' });
setTimeout(function () {
  console.log(JSON.stringify({ toast: lastToast().msg, undo: groceryState.bulkUndo }));
}, 80);
""")
    assert out["toast"] == "Thing 2 → Loblaws", "the latest tap's toast, not the slowest answer's"
    assert out["undo"] == [{"item_id": 2, "store": "", "decided": False}]


# --- 4. the finish -----------------------------------------------------------


@_needs_node
def test_the_last_row_makes_the_finish_line_and_the_way_back():
    out = _node("""
setUp(2);
groceryState.step = 'sortall';
click({ gro: 'sortall-pick', id: '1', store: 'Costco' });
const dockMid = groDockHtml(groceryState.data, 'sortall');
click({ gro: 'sortall-pick', id: '2', store: 'Costco' });
const body = new Body();
groSortAllRender(body, groceryState.data);
console.log(JSON.stringify({
  dockMid: dockMid,
  done: groceryState.sortAllDone,
  body: body.innerHTML,
  head: groHeadFor(groceryState.data, 'sortall'),
  dock: groDockHtml(groceryState.data, 'sortall')
}));
""")
    assert out["dockMid"] == "", "no dead button while rows are left — the crumb is the way out"
    assert out["done"] is True
    assert "All sorted." in out["body"] and "empty-moment" in out["body"]
    assert out["head"] == {"back": "‹ Shop", "title": "Sort them all", "sub": ""}, "no '0 to sort' over the finish"
    assert 'data-gro="sortall-done"' in out["dock"] and "Back to the list" in out["dock"]
    assert out["dock"].count("gro-primary") == 1, "one apricot (rule 5)"


@_needs_node
def test_the_finish_stays_up_rather_than_folding_to_the_list_and_back_is_the_list():
    """renderGrocery folds a sort step with nothing to sort back to the
    root; the finish is the one exception, and only while it is a finish."""
    src = SHELL_JS
    fold = src[src.index("    // A step that stopped making sense under its own feet"):src.index("    if (groceryState.step === 'carry' && !groceryState.carried.length)")]
    assert "!(groceryState.step === 'sortall' && groceryState.sortAllDone)" in fold
    out = _node("""
setUp(1);
groceryState.step = 'sortall';
click({ gro: 'sortall-pick', id: '1', store: 'Costco' });
const doneAfterPick = groceryState.sortAllDone;
click({ gro: 'sortall-done' });
console.log(JSON.stringify({ doneAfterPick: doneAfterPick, step: groceryState.step, doneAfterBack: groceryState.sortAllDone }));
""")
    assert out == {"doneAfterPick": True, "step": "list", "doneAfterBack": False}


@_needs_node
def test_undoing_the_last_row_takes_the_finish_away_again():
    out = _node("""
setUp(1);
groceryState.step = 'sortall';
const body = new Body();
groSortAllRender(body, groceryState.data);
markSorted('1', 'Costco');
groceryState.sortAllDone = true;
groSortAllRender(body, groceryState.data);
const finish = body.innerHTML.indexOf('All sorted.') !== -1;
markUnsorted('1');
groSortAllRender(body, groceryState.data);
console.log(JSON.stringify({ finish: finish, rows: ids(body), done: groceryState.sortAllDone, dock: groDockHtml(groceryState.data, 'sortall') }));
""")
    assert out["finish"] is True
    assert out["rows"] == ["1"], "the row is back"
    assert out["done"] is False and out["dock"] == "", "and the finish, with its button, is gone"


@_needs_node
def test_have_it_on_the_last_row_finishes_the_same_way():
    """"Have it" and "Use something else" already took a row off at once;
    reaching zero through them is the same finish, not the queue's jump to
    the list."""
    out = _node("""
setUp(1);
groceryState.step = 'sortall';
click({ gro: 'have-it', id: '1', name: 'Thing 1' });
// The harness has no panel, so loadGrocery leaves the data alone: stand in
// for the server's re-read, in which the dropped row is no longer listed.
groceryState.data.stores.Unassigned.sections[0].items = [];
settle(function () {
  console.log(JSON.stringify({ step: groceryState.step, done: groceryState.sortAllDone, toast: lastToast().msg }));
});
""")
    assert out["step"] == "sortall", "stays on the screen for its finish"
    assert out["done"] is True
    assert out["toast"] == "Thing 1 off the list — you have it"


@_needs_node
def test_a_one_shop_household_never_reaches_this_screen():
    out = _node("""
setUp(8, [], ['Costco']);
groceryState.step = 'sortall';
click({ gro: 'sortall-pick', id: '1', store: 'Costco' });
console.log(JSON.stringify({ unsorted: groUnsorted(groceryState.data).length, posts: POSTS.length, html: groSortAllHtml(groceryState.data) }));
""")
    assert out["unsorted"] == 0, "one shop: nothing is ever 'to sort' (Emily, 2026-09-09)"
    assert out["posts"] == 0, "so a stray tap writes nothing"
    assert "gro-sortall-row" not in out["html"]


# --- 5. what is gone, the copy, and the motion --------------------------


def test_nothing_is_staged_and_the_save_button_is_gone():
    assert "sortAllPicks" not in SHELL_JS
    assert "sortall-save" not in SHELL_JS
    assert "them sorted" not in SHELL_JS


def test_the_fast_path_offer_no_longer_promises_tap_only_the_exceptions():
    assert "Tap only the exceptions." not in SHELL_JS
    assert '<span class="gro-howrow-sub">One tap each.</span>' in SHELL_JS


def test_the_collapse_uses_the_leaving_curve_and_the_fast_token_only():
    motion = SHELL_CSS[SHELL_CSS.index("   Motion (Emily, 2026-09-11)."):]
    assert "4. A sorted row leaves SORT ALL" in motion
    rule = re.search(r"\.gro-sortall-row\.is-leaving \{(.*?)\}", motion, re.S)
    assert rule, "the collapse rule lives in the Motion section with the other three"
    body = rule.group(1)
    assert "height var(--motion-fast) var(--motion-ease-in)" in body
    assert "--motion-base" not in body, "≤ 200ms: the fast token, not the base one"
    assert re.search(r"\d+ms", body) is None, "durations come from the tokens, which are 0ms under reduced motion"
    gone = re.search(r"\.gro-sortall-row\.is-leaving\.is-gone \{(.*?)\}", motion, re.S).group(1)
    assert "height: 0" in gone and "opacity: 0" in gone
    # Reduced motion is honoured in code too, not only by the 0ms tokens.
    leave = SHELL_JS[SHELL_JS.index("  function groSortAllLeave(row) {"):]
    leave = leave[:leave.index("\n  }\n") + 4]
    assert "prefers-reduced-motion: reduce" in leave
    assert "row.remove(); return;" in leave


def test_the_design_system_records_the_fourth_animation():
    motion = DESIGN_SYSTEM[DESIGN_SYSTEM.index("- **Motion**"):]
    motion = motion[:motion.index("\n\n")]
    assert "(4) a row on Shop's \"Sort them all\" screen collapses out" in motion
    assert "exactly three animations" not in motion


# --- 6. the route pair the undo relies on --------------------------------


def test_one_pick_and_its_undo_over_http_leave_the_row_exactly_as_it_was(signed_in):
    item_id = signed_in.post("/api/grocery-list/add", json={"item": "Halloumi", "quantity": "1"}).json()["item_id"]
    rows = {it["id"]: it for it in tools.list_grocery_list()}
    assert (rows[item_id]["store"], rows[item_id]["store_decided"]) == ("", 0)

    picked = signed_in.post(f"/api/grocery-list/{item_id}/store", json={"store": "Costco", "remember": False})
    assert picked.status_code == 200
    assert picked.json()["needs_confirmation"] is False, "this week only: no 'Remember?' toast over the undo toast"
    rows = {it["id"]: it for it in tools.list_grocery_list()}
    assert (rows[item_id]["store"], rows[item_id]["store_decided"]) == ("Costco", 1)
    prefs = {p["item"].lower(): p["store"] for p in tools.get_item_store_preferences()}
    assert "halloumi" not in prefs

    undone = signed_in.post(
        "/api/grocery-list/store-bulk",
        json={"assignments": [{"item_id": item_id, "store": "", "decided": False}], "remember": False},
    )
    assert undone.status_code == 200 and undone.json()["updated"] == 1
    rows = {it["id"]: it for it in tools.list_grocery_list()}
    assert (rows[item_id]["store"], rows[item_id]["store_decided"]) == ("", 0), "back in the queue, not sitting at Any"
