"""
The Shop tab's node harness, shared by every test file that runs
static/shell.js's Grocery region under node (the house standard — see
tests/nodeharness.py for why the code runs rather than being read).

It used to live in tests/test_shop_trip_exit.py and
tests/test_grocery_fast_sort.py, and five other files imported it from
there. The trip screens went on 2026-09-18 ("the list is the checklist")
and that file with them, so the harness moved here — a module, not a
test file, so pytest never collects it.

What it gives a test: a stub of everything the region reaches for outside
itself (escapeHtml, fetch, showToast, toastSaved, panels, window,
localStorage, the band's date), the region itself (`grocery_block()`), the
click machinery (`CLICK`: onGroceryClick wants an event whose target can
find a [data-gro] element and an element it can disable, not a document),
and a small list fixture (`FIXTURE`).

The click machinery has two spellings, and the difference is the point.
`clickIfRendered(dataset, row)` is a TAP: it builds the current step's own
HTML out of the region's renderers and refuses to dispatch a control that
screen does not draw. `clickHandlerDirectly(dataset, row)` is the old
`click()` — it fabricates an element and dispatches, no questions asked —
kept for the few controls that genuinely live outside the step's body.
Before 2026-09-21 there was only the second one, under the first one's
name, so a test could press a button the screen had never rendered and
the handler would run: an end-to-end walk could pass against a broken
screen.
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import nodeharness
import pytest

REPO = Path(__file__).resolve().parent.parent
SHELL_JS = (REPO / "static" / "shell.js").read_text(encoding="utf-8")
SHELL_CSS = (REPO / "static" / "shell.css").read_text(encoding="utf-8")

needs_node = pytest.mark.skipif(
    shutil.which("node") is None, reason="node is needed to execute the shell's own functions"
)


def grocery_block() -> str:
    """The whole Grocery region INCLUDING onGroceryClick, up to the
    hands-free voice code (which wants a SpeechRecognition engine)."""
    start = SHELL_JS.index("  var GRO_ICONS = {")
    end = SHELL_JS.index("  // ---------- Hands-free voice ----------", start)
    return SHELL_JS[start:end]


STUB = """
function escapeHtml(s){return String(s == null ? '' : s)
  .replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');}
const POSTS = [];
// Fails the Nth POST (1-based) when FAIL_ON is set — how a dropped
// connection is injected without a server.
let FAIL_ON = 0;
function fetch(url, opts) {
  POSTS.push({ url: url, body: JSON.parse((opts && opts.body) || '{}') });
  const ok = POSTS.length !== FAIL_ON;
  return Promise.resolve({ ok: ok, status: ok ? 200 : 500, json: function () { return Promise.resolve({}); } });
}
function posts(url) { return POSTS.filter(function (p) { return p.url.indexOf(url) !== -1; }); }
function closes() { return POSTS.filter(function (p) { return p.url === '/api/shopping-trips/close'; }); }
function purchases() { return POSTS.filter(function (p) { return p.body && p.body.status === 'purchased'; }).map(function (p) { return p.url; }); }
const panels = {};
const TOASTS = [];
function showToast(msg, action, hold) { TOASTS.push({ msg: msg, action: action, hold: hold }); }
var CHANGES_SAVED = 'Changes saved';
function toastSaved(action, holdMs) { showToast(CHANGES_SAVED, action || null, holdMs); }
function lastToast() {
  const t = TOASTS[TOASTS.length - 1];
  return t ? { msg: t.msg, action: t.action ? t.action.label : null, hold: t.hold } : null;
}
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
var navigator = { onLine: true };
var scrollEl = null;
var TAB_SWITCHES = [];
function activateTab(key) { TAB_SWITCHES.push(key); }
var coachState = { householdId: 1 };
var BAND_IDENTITY = 'wordmark';
function bandDateLabel() { return 'Friday, Sep 18'; }
function emptyMomentHtml(icon, sentence, detail) { return '<div class="empty-moment">' + sentence + '</div>'; }
"""

CLICK = """
function fakeEl(dataset, row) {
  return {
    dataset: dataset, disabled: false,
    closest: function () { return row || null; },
    classList: { toggle: function () {}, add: function () {}, remove: function () {}, contains: function () { return false; } },
    setAttribute: function () {},
    querySelectorAll: function () { return []; }
  };
}

// ---------- what is on the screen right now ----------
//
// renderGrocery() wants a real panel, and there is no DOM here, so the
// step's HTML is built the way renderGrocery builds it: its own three
// body renderers and its dock, chosen by the same step, after the same
// "a step that stopped making sense under its own feet falls back to the
// root" fallbacks. It is a MIRROR of that dispatch, not a second opinion
// about which controls a step has — every string below comes out of the
// region's own renderers. test_grocery_stub_click_if_rendered.py pins the
// mirror against renderGrocery's own source, so a fourth step renderer
// fails loudly rather than quietly leaving its controls unguarded.
function groScreenStep() {
  const data = groceryState.data;
  let step = groceryState.step;
  if (step === 'sortall' && !groUnsorted(data).length && !groceryState.sortAllDone) step = 'list';
  if (step === 'carry' && !groceryState.carried.length) step = 'list';
  if (step !== 'carry' && step !== 'sortall') step = 'list';
  return step;
}
function screenHtml() {
  const data = groceryState.data;
  if (!data) return '';
  const step = groScreenStep();
  const body = step === 'carry' ? groCarryHtml(data)
    : step === 'sortall' ? groSortAllHtml(data)
    : groListHtml(data);
  // The crumb belongs to no step renderer — it lives in the panel
  // scaffold and renderGrocery shows or hides it by exactly this rule
  // (`back.hidden = !groHeadFor(data, step).back`, and unconditionally on
  // the root). It is as rendered as anything in the body, so a test can
  // tap it where it is drawn and only there. The head's mic and refresh
  // buttons sit beside it and are NOT modelled: SHOW_GRO_HEADER_TOOLS is
  // false, so they render `hidden` and nothing can tap them — the guard
  // test fails if that flag flips. The scan sheet is not modelled either:
  // it is built at body level, outside this panel.
  const back = step === 'list' ? '' : groHeadFor(data, step).back;
  const crumb = back ? '<button class="crumb" id="gro-back" data-gro="step-back">' + back + '</button>' : '';
  return crumb + body + groDockHtml(data, step);
}

// Every opening tag on the screen that carries this action.
function groControlsFor(html, action) {
  const out = [];
  const re = /<[a-zA-Z][^>]*>/g;
  let m;
  // escapeHtml turns a > inside an attribute value into &gt;, so a tag
  // never ends early.
  while ((m = re.exec(html)) !== null) {
    if (m[0].indexOf('data-gro="' + action + '"') !== -1) out.push(m[0]);
  }
  return out;
}
function groDataAttr(key) {
  return 'data-' + key.replace(/[A-Z]/g, function (c) { return '-' + c.toLowerCase(); });
}
// null when this control really is on the screen; otherwise the sentence
// to fail with.
function groNotRendered(dataset) {
  const action = dataset.gro;
  const step = groScreenStep();
  const tags = groControlsFor(screenHtml(), action);
  if (!tags.length) {
    return 'the ' + step.toUpperCase() + ' screen renders no [data-gro="' + action + '"] at all';
  }
  // Only the keys this action's markup actually expresses are a claim
  // about the screen. A key the test supplies that no such element
  // carries (a display name the handler reads off the dataset for a
  // toast, say) is the caller's business, not the screen's.
  const keys = Object.keys(dataset).filter(function (k) {
    if (k === 'gro') return false;
    const attr = groDataAttr(k) + '="';
    return tags.some(function (t) { return t.indexOf(attr) !== -1; });
  });
  const want = keys.map(function (k) { return groDataAttr(k) + '="' + String(dataset[k]) + '"'; });
  const hit = tags.some(function (t) {
    return want.every(function (w) { return t.indexOf(w) !== -1; });
  });
  if (hit) return null;
  return 'the ' + step.toUpperCase() + ' screen renders ' + tags.length + ' [data-gro="' + action +
    '"], none of them ' + want.join(' ');
}

// THE ONE TO REACH FOR. Asserts the control is on the screen the
// household is looking at before dispatching — so a test can only press
// what the renderers actually drew. Fails loudly, naming the control and
// the step it was not found in.
function clickIfRendered(dataset, row) {
  const complaint = groNotRendered(dataset);
  if (complaint) throw new Error('clickIfRendered: ' + complaint);
  return clickHandlerDirectly(dataset, row);
}

// The old click(): fabricates an element and dispatches straight into
// onGroceryClick, with no check that anything rendered it. Named for what
// it does, so a call site that means "call the handler directly" says so
// and every other call site reads as a tap. Each surviving use carries a
// one-line reason.
function clickHandlerDirectly(dataset, row) {
  const el = fakeEl(dataset, row);
  onGroceryClick({ target: { closest: function () { return el; } } });
  return el;
}

// Handlers write, then re-read, then render — all through promises. Give
// them a few turns of the loop before reading the state back.
function settle(fn) { setTimeout(fn, 30); }
"""

# A household with two shops and a list where nothing has been sorted yet.
# `n` rows of "Thing 1..n", plus whatever extra store buckets a test wants.
FIXTURE = """
function unsortedRow(i) {
  return { id: i, item: 'Thing ' + i, quantity: '1', store: '', store_decided: 0, category: 'other', status: 'needed' };
}
function listOf(n, extra) {
  const rows = [];
  for (let i = 1; i <= n; i++) rows.push(unsortedRow(i));
  const data = { stores: { Unassigned: { sections: [{ section: 'other', items: rows }], purchased: [], inCart: [] } } };
  (extra || []).forEach(function (s) {
    data.stores[s.store] = {
      sections: s.items && s.items.length ? [{ section: 'other', items: s.items }] : [],
      purchased: s.purchased || [], inCart: s.inCart || []
    };
  });
  return data;
}
function setUp(n, extra, shops) {
  groceryState.data = listOf(n, extra);
  groceryState.usualStores = shops === undefined ? ['Loblaws', 'Costco'] : shops;
  groceryState.storesPromptDismissed = true;
  groceryState.step = 'list';
  return groceryState.data;
}
function pickedChip(html, id) {
  const row = new RegExp('data-row-for="' + id + '"[\\\\s\\\\S]*?</div>\\\\s*</div>').exec(html);
  const m = /data-store="([^"]*)" aria-pressed="true"/.exec(row ? row[0] : '');
  return m ? m[1] : null;
}
function cards(html) {
  return (html.match(/gro-store-name">([^<]*)</g) || []).map(function (m) { return /gro-store-name">([^<]*)</.exec(m)[1]; });
}
function counts(html) {
  return (html.match(/gro-store-count">([^<]*)</g) || []).map(function (m) { return /gro-store-count">([^<]*)</.exec(m)[1]; });
}
"""


def run(body: str, timeout: int = 30):
    """Run `body` after the stub, the region, the click machinery and the
    fixture; return whatever the script printed as JSON."""
    res = nodeharness.run_node(STUB + grocery_block() + CLICK + FIXTURE + body, timeout=timeout)
    assert res.returncode == 0, f"node failed: {res.stderr}"
    return json.loads(res.stdout.strip())
