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
