"""
Grocery: keep working in a store with no signal (Loop Board, Emily
2026-09-10).

Three layers, each tested the way it runs:

1. static/grocery-offline.js — the list's copy on the phone and the queue
   of ticks made without signal. Pure and DOM-free, so these tests RUN it
   under node (the pattern from tests/test_ask_sheet_named_intents.py)
   rather than reading the source for markers: order, last-write-wins per
   row, surviving a reload, idempotent replay, and what happens when the
   server refuses versus never answers are all behaviour.
2. static/service-worker.js — run under node with a stub `self`, `caches`
   and `fetch`, to prove it never answers an /api/ request from cache and
   that an offline navigation to /grocery lands on the shell even when
   only "/" was ever cached.
3. app/tools/grocery.py — the status route is idempotent, because the
   queue may send a tick twice when the first reply was lost.

Every user-facing line is asserted verbatim: Emily may reword any of
them, and when she does the constant changes in the same commit.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from app import tools


REPO = Path(__file__).resolve().parent.parent
MODULE = REPO / "static" / "grocery-offline.js"
SW = REPO / "static" / "service-worker.js"
SHELL_JS = (REPO / "static" / "shell.js").read_text(encoding="utf-8")
SHELL_HTML = (REPO / "static" / "shell.html").read_text(encoding="utf-8")
SHELL_CSS = (REPO / "static" / "shell.css").read_text(encoding="utf-8")

_needs_node = pytest.mark.skipif(
    shutil.which("node") is None, reason="node is needed to execute the browser code"
)


def _node(script: str):
    res = subprocess.run(["node", "-e", script], capture_output=True, text=True, timeout=30)
    assert res.returncode == 0, f"node failed: {res.stderr}"
    return json.loads(res.stdout.strip())


# A localStorage stand-in plus a small list in the exact shape shell.js
# renders (groLoadAllData): storeName -> { sections, purchased, inCart }.
_PRELUDE = f"""
const M = require({json.dumps(str(MODULE))});
function storage() {{
  const mem = {{}};
  return {{
    mem,
    getItem: (k) => (k in mem ? mem[k] : null),
    setItem: (k, v) => {{ mem[k] = String(v); }},
    removeItem: (k) => {{ delete mem[k]; }},
  }};
}}
function list() {{
  return {{ stores: {{ Loblaws: {{
    sections: [
      {{ section: 'produce', items: [ {{ id: 1, item: 'Bananas', quantity: '6', category: 'produce', status: 'needed' }} ] }},
      {{ section: 'dairy',   items: [ {{ id: 2, item: 'Milk',    quantity: '2 l', category: 'dairy',   status: 'needed' }} ] }},
      {{ section: 'pantry',  items: [ {{ id: 3, item: 'Rice',    quantity: '1 bag', category: 'pantry', status: 'needed' }} ] }},
    ],
    purchased: [], inCart: [],
  }} }} }};
}}
function ids(bucket) {{ return bucket.map((it) => it.id); }}
function needed(data, store) {{
  return (data.stores[store || 'Loblaws'].sections).map((s) => [s.section, ids(s.items)]);
}}
const out = (v) => console.log(JSON.stringify(v));
"""


def _run(tail: str):
    return _node(_PRELUDE + tail)


# ---------------------------------------------------------------------------
# 1. The queue
# ---------------------------------------------------------------------------

@_needs_node
def test_ticks_are_sent_in_the_order_they_were_made():
    got = _run("""
      const q = M.create({ storage: storage(), householdId: 1 });
      q.queueStatus(3, 'in_cart'); q.queueStatus(1, 'in_cart'); q.queueStatus(2, 'in_cart');
      const calls = [];
      q.replay((url, body) => { calls.push([url, body.status]); return Promise.resolve({ ok: true, status: 200 }); })
        .then((r) => out({ r, calls, left: q.pending().length }));
    """)
    assert got["calls"] == [
        ["/api/grocery-list/3/status", "in_cart"],
        ["/api/grocery-list/1/status", "in_cart"],
        ["/api/grocery-list/2/status", "in_cart"],
    ]
    assert got["r"] == {"sent": 3, "dropped": 0, "kept": 0}
    assert got["left"] == 0


@_needs_node
def test_a_second_change_to_the_same_row_replaces_the_first():
    """Tick then untick sends 'needed' once — last-write-wins per row, and
    the row keeps its place in the order among the OTHER rows' changes."""
    got = _run("""
      const q = M.create({ storage: storage(), householdId: 1 });
      q.queueStatus(1, 'in_cart'); q.queueStatus(2, 'in_cart'); q.queueStatus(1, 'needed');
      out(q.pending().map((op) => [op.id, op.status]));
    """)
    assert got == [["2", "in_cart"], ["1", "needed"]]


@_needs_node
def test_the_copy_and_the_queue_survive_a_reload():
    """A fresh instance over the same storage (what a reload is) sees both."""
    got = _run("""
      const s = storage();
      const a = M.create({ storage: s, householdId: 1 });
      a.saveList(list()); a.queueStatus(2, 'in_cart');
      const b = M.create({ storage: s, householdId: 1 });
      const shown = b.applyPending(b.readList().data);
      out({ pending: b.pending().map((op) => op.id), inCart: ids(shown.stores.Loblaws.inCart), needed: needed(shown) });
    """)
    assert got["pending"] == ["2"]
    assert got["inCart"] == [2]
    assert got["needed"] == [["produce", [1]], ["pantry", [3]]]


@_needs_node
def test_replay_is_idempotent_and_a_lost_reply_is_sent_again():
    """First attempt: the request reaches nobody (fetch throws). The tick
    stays queued. Second attempt: sent once. Third: nothing to send."""
    got = _run("""
      const q = M.create({ storage: storage(), householdId: 1 });
      q.queueStatus(1, 'in_cart');
      let calls = 0;
      const dead = () => { calls++; return Promise.reject(new TypeError('Failed to fetch')); };
      const alive = () => { calls++; return Promise.resolve({ ok: true, status: 200 }); };
      (async () => {
        const first = await q.replay(dead);
        const afterFirst = q.pending().length;
        const second = await q.replay(alive);
        const third = await q.replay(alive);
        out({ first, afterFirst, second, third, calls });
      })();
    """)
    assert got["first"] == {"sent": 0, "dropped": 0, "kept": 1}
    assert got["afterFirst"] == 1
    assert got["second"] == {"sent": 1, "dropped": 0, "kept": 0}
    assert got["third"] == {"sent": 0, "dropped": 0, "kept": 0}
    assert got["calls"] == 2


@_needs_node
def test_a_tick_the_server_refuses_is_dropped_not_retried_forever():
    """404: the row is gone (a partner removed it). Sending it again would
    not bring it back, so the op is spent and the queue moves on."""
    got = _run("""
      const q = M.create({ storage: storage(), householdId: 1 });
      q.queueStatus(9, 'in_cart'); q.queueStatus(1, 'in_cart');
      const calls = [];
      q.replay((url) => { calls.push(url); return Promise.resolve({ ok: !url.includes('/9/'), status: url.includes('/9/') ? 404 : 200 }); })
        .then((r) => out({ r, calls, left: q.pending().length }));
    """)
    assert got["r"] == {"sent": 1, "dropped": 1, "kept": 0}
    assert got["calls"] == ["/api/grocery-list/9/status", "/api/grocery-list/1/status"]
    assert got["left"] == 0


@_needs_node
def test_signal_dropping_mid_replay_keeps_the_rest_in_order():
    got = _run("""
      const q = M.create({ storage: storage(), householdId: 1 });
      q.queueStatus(1, 'in_cart'); q.queueStatus(2, 'in_cart'); q.queueStatus(3, 'in_cart');
      let n = 0;
      q.replay(() => { n++; return n === 2 ? Promise.reject(new TypeError('Failed to fetch')) : Promise.resolve({ ok: true, status: 200 }); })
        .then((r) => out({ r, left: q.pending().map((op) => op.id) }));
    """)
    assert got["r"] == {"sent": 1, "dropped": 0, "kept": 2}
    assert got["left"] == ["2", "3"]


@_needs_node
def test_two_replays_at_once_never_send_a_tick_twice():
    """The second call waits for the first and then tries afresh — which
    finds nothing left. Never two runs interleaving over one queue."""
    got = _run("""
      const q = M.create({ storage: storage(), householdId: 1 });
      q.queueStatus(1, 'in_cart'); q.queueStatus(2, 'in_cart');
      let calls = 0;
      const post = () => { calls++; return new Promise((res) => setTimeout(() => res({ ok: true, status: 200 }), 5)); };
      Promise.all([q.replay(post), q.replay(post)]).then(([a, b]) => out({ calls, a, b }));
    """)
    assert got["calls"] == 2
    assert got["a"] == {"sent": 2, "dropped": 0, "kept": 0}
    assert got["b"] == {"sent": 0, "dropped": 0, "kept": 0}


@_needs_node
def test_signal_returning_during_a_failed_replay_still_sends():
    """The race the live drive found: a replay called while the previous
    one is still settling its 'no signal' answer must make its own attempt
    rather than inherit that answer."""
    got = _run("""
      const q = M.create({ storage: storage(), householdId: 1 });
      q.queueStatus(1, 'in_cart');
      let calls = 0;
      // The first request finds no signal; anything after it gets through.
      const post = () => (++calls === 1 ? Promise.reject(new TypeError('Failed to fetch')) : Promise.resolve({ ok: true, status: 200 }));
      const first = q.replay(post);
      const second = q.replay(post);
      Promise.all([first, second]).then(([a, b]) => out({ a, b, left: q.pending().length }));
    """)
    assert got["a"] == {"sent": 0, "dropped": 0, "kept": 1}
    assert got["b"] == {"sent": 1, "dropped": 0, "kept": 0}
    assert got["left"] == 0


# ---------------------------------------------------------------------------
# 1b. The copy
# ---------------------------------------------------------------------------

@_needs_node
def test_apply_pending_moves_rows_between_buckets_without_touching_the_copy():
    got = _run("""
      const q = M.create({ storage: storage(), householdId: 1 });
      q.saveList(list());
      q.queueStatus(1, 'in_cart'); q.queueStatus(3, 'in_cart'); q.queueStatus(3, 'needed'); q.queueStatus(42, 'in_cart');
      const shown = q.applyPending(q.readList().data);
      const copy = q.readList().data;
      out({ inCart: ids(shown.stores.Loblaws.inCart), needed: needed(shown), copyNeeded: needed(copy), copyInCart: ids(copy.stores.Loblaws.inCart),
            status: shown.stores.Loblaws.inCart[0].status });
    """)
    # 1 ticked; 3 ticked then put back (so it is where the server would put
    # it: in its aisle, in section order); 42 is nobody, and stays nobody.
    assert got["inCart"] == [1]
    assert got["needed"] == [["dairy", [2]], ["pantry", [3]]]
    assert got["status"] == "in_cart"
    # The stored copy is server truth and applyPending never mutates it.
    assert got["copyNeeded"] == [["produce", [1]], ["dairy", [2]], ["pantry", [3]]]
    assert got["copyInCart"] == []


@_needs_node
def test_an_untick_puts_the_row_back_in_section_order():
    """Untick something whose aisle had emptied: the section comes back in
    the right place (dairy between produce and pantry), not at the end."""
    got = _run("""
      const data = list();
      M.applyStatus(data, 2, 'in_cart');
      const afterTick = needed(data);
      M.applyStatus(data, 2, 'needed');
      out({ afterTick, afterUntick: needed(data) });
    """)
    assert got["afterTick"] == [["produce", [1]], ["pantry", [3]]]
    assert got["afterUntick"] == [["produce", [1]], ["dairy", [2]], ["pantry", [3]]]


@_needs_node
def test_copy_and_queue_are_kept_per_household():
    got = _run("""
      const s = storage();
      const one = M.create({ storage: s, householdId: 1 });
      one.saveList(list()); one.queueStatus(1, 'in_cart');
      const two = M.create({ storage: s, householdId: 2 });
      out({ twoCopy: two.readList(), twoPending: two.pending().length, keys: Object.keys(s.mem).sort() });
    """)
    assert got["twoCopy"] is None
    assert got["twoPending"] == 0
    assert got["keys"] == ["pomona.grocery.copy.h1", "pomona.grocery.queue.h1"]


@_needs_node
def test_the_household_is_remembered_for_next_time_and_offline():
    """The shell learns the household from /api/coaching — which cannot
    answer offline — so the id is written down, and a fresh instance with
    no id of its own starts from it."""
    got = _run("""
      const s = storage();
      const a = M.create({ storage: s });
      const before = a.household();
      a.setHousehold(7); a.saveList(list());
      const b = M.create({ storage: s });
      out({ before, after: b.household(), sees: !!b.readList() });
    """)
    assert got["before"] is None
    assert got["after"] == "7"
    assert got["sees"] is True


# --- Household isolation (verifier, 2026-09-11): the copy is the SESSION's ---

@_needs_node
def test_sign_out_clears_every_grocery_key_and_the_pointer():
    got = _run("""
      const s = storage();
      const a = M.create({ storage: s, householdId: 1 });
      a.saveList(list()); a.queueStatus(1, 'in_cart'); a.saveShops({ usualStores: ['Loblaws'], dismissed: true });
      s.mem['pomona.grocery.copy.h9'] = 'left over from an older build';
      s.length = Object.keys(s.mem).length; s.key = (i) => Object.keys(s.mem)[i];
      a.forget();
      const b = M.create({ storage: s });
      out({ keys: Object.keys(s.mem), known: b.known(), copy: b.readList(), pending: b.pending().length });
    """)
    assert got == {"keys": [], "known": False, "copy": None, "pending": 0}


@_needs_node
def test_an_unknown_household_offline_shows_nothing_whatever_storage_holds():
    """No pointer (signed out, or this device has never heard from the
    server this session): a copy sitting in storage is not shown, and a
    tick made now is not written to storage under anyone's name."""
    got = _run("""
      const s = storage();
      M.create({ storage: s, householdId: 1 }).saveList(list());   // no pointer written: nobody signed in
      const q = M.create({ storage: s });
      q.queueStatus(1, 'in_cart');
      out({ known: q.known(), copy: q.readList(), shops: q.readShops(), pendingInMemory: q.pending().length,
            keys: Object.keys(s.mem).sort() });
    """)
    assert got["known"] is False
    assert got["copy"] is None and got["shops"] is None
    assert got["pendingInMemory"] == 1
    assert got["keys"] == ["pomona.grocery.copy.h1"]


@_needs_node
def test_a_different_household_answering_purges_the_previous_one():
    """Household B signs in on A's phone. The moment the server names B,
    A's copy, queue and shops are gone and nothing of A's is readable."""
    got = _run("""
      const s = storage();
      const a = M.create({ storage: s, householdId: 'A' });
      a.saveList(list()); a.queueStatus(1, 'in_cart'); a.saveShops({ usualStores: ['Loblaws'], dismissed: true });
      const changed = a.setHousehold('B');
      out({ changed, household: a.household(), copy: a.readList(), pending: a.pending().length, shops: a.readShops(),
            keys: Object.keys(s.mem).sort() });
    """)
    assert got["changed"] is True
    assert got["household"] == "B"
    assert got["copy"] is None and got["pending"] == 0 and got["shops"] is None
    assert got["keys"] == ["pomona.grocery.household"]


@_needs_node
def test_what_was_fetched_before_the_household_was_known_moves_under_its_key():
    """A page loads the list before /api/coaching answers. Held in memory,
    then written under the right household — and a tick made in between
    joins that household's queue."""
    got = _run("""
      const s = storage();
      const q = M.create({ storage: s });
      q.saveList(list()); q.queueStatus(2, 'in_cart');
      const beforeKeys = Object.keys(s.mem).sort();
      const changed = q.setHousehold(1);
      out({ beforeKeys, changed, keys: Object.keys(s.mem).sort(), copy: !!q.readList(), pending: q.pending().map((op) => op.id) });
    """)
    assert got["beforeKeys"] == []
    assert got["changed"] is True
    assert got["keys"] == ["pomona.grocery.copy.h1", "pomona.grocery.household", "pomona.grocery.queue.h1"]
    assert got["copy"] is True and got["pending"] == ["2"]


@_needs_node
def test_a_malformed_queue_entry_is_dropped_not_replayed_forever():
    got = _run("""
      const s = storage();
      s.mem['pomona.grocery.queue.h1'] = JSON.stringify([null, { id: '1', status: 'in_cart' }, { foo: 1 }, { id: '2', status: 'flying' }, 'x']);
      const q = M.create({ storage: s, householdId: 1 });
      const seen = q.pending().map((op) => op.id);
      const calls = [];
      q.replay((url) => { calls.push(url); return Promise.resolve({ ok: true, status: 200 }); })
        .then((r) => out({ seen, r, calls, left: q.pending().length }));
    """)
    assert got["seen"] == ["1"]
    assert got["r"] == {"sent": 1, "dropped": 0, "kept": 0}
    assert got["calls"] == ["/api/grocery-list/1/status"]
    assert got["left"] == 0


@_needs_node
def test_the_shops_answer_is_kept_with_the_copy():
    """/api/memory fails offline like everything else. Without the last
    answer, an empty usualStores would put the "where do you shop?" card
    up over the list and take "Start the trip" away."""
    got = _run("""
      const s = storage();
      const a = M.create({ storage: s, householdId: 1 });
      const before = a.readShops();
      a.saveShops({ usualStores: ['Loblaws'], dismissed: true });
      const b = M.create({ storage: s, householdId: 1 });
      out({ before, after: b.readShops(), other: M.create({ storage: s, householdId: 2 }).readShops() });
    """)
    assert got["before"] is None
    assert got["after"] == {"usualStores": ["Loblaws"], "dismissed": True}
    assert got["other"] is None


def test_the_shell_reads_the_kept_shops_answer_when_memory_cannot_load():
    block = SHELL_JS[SHELL_JS.index("async function groLoadUsualStores()"):]
    block = block[:block.index("async function groLoadStorePrefs()")]
    assert "groOffline.saveShops({ usualStores: groceryState.usualStores, dismissed: groceryState.storesPromptDismissed })" in block
    assert "groOffline.readShops()" in block


@_needs_node
def test_storage_that_throws_still_works_for_this_page_view():
    """Safari private mode throws on localStorage. A tick must not."""
    got = _run("""
      const angry = { getItem() { throw new Error('no'); }, setItem() { throw new Error('no'); }, removeItem() { throw new Error('no'); } };
      const q = M.create({ storage: angry, householdId: 1 });
      q.saveList(list()); q.queueStatus(1, 'in_cart');
      out({ pending: q.pending().length, copy: !!q.readList() });
    """)
    assert got == {"pending": 1, "copy": True}


# ---------------------------------------------------------------------------
# 2. The service worker
# ---------------------------------------------------------------------------

# A stub browser: `self` collects the worker's listeners; `caches` is one
# in-memory cache keyed by URL; `fetch` is whatever the test says. The
# worker's own source is run unmodified.
_SW_HARNESS = f"""
const fs = require('fs');
const src = fs.readFileSync({json.dumps(str(SW))}, 'utf8');
const listeners = {{}};
const store = new Map();
const cache = {{
  addAll: async () => {{}},
  put: async (req, res) => {{ store.set(typeof req === 'string' ? req : req.url, res); }},
  match: async (req) => {{
    const key = typeof req === 'string' ? new URL(req, 'http://app.test').href : req.url;
    return store.get(key);
  }},
}};
const caches = {{
  open: async () => cache,
  keys: async () => [],
  delete: async () => true,
  match: async (req) => cache.match(req),
}};
const self = {{
  addEventListener: (name, fn) => {{ listeners[name] = fn; }},
  skipWaiting: () => {{}},
  clients: {{ claim: () => {{}} }},
  location: {{ href: 'http://app.test/static/service-worker.js' }},
}};
let fetch = async () => {{ throw new TypeError('Failed to fetch'); }};
new Function('self', 'caches', 'fetch', 'URL', src)(self, caches, (...a) => fetch(...a), URL);
function fetchEvent(path, mode) {{
  const ev = {{ request: {{ url: 'http://app.test' + path, mode: mode || 'cors' }}, responded: null,
    respondWith(p) {{ this.responded = Promise.resolve(p); }} }};
  listeners.fetch(ev);
  return ev;
}}
const out = (v) => console.log(JSON.stringify(v));
"""


@_needs_node
def test_service_worker_never_answers_an_api_request():
    """The grocery list's offline copy is page-level, keyed per household,
    and carries the unsent ticks; a worker-level cache of /api/ would be a
    second, dumber copy that could show one household another's list."""
    got = _node(_SW_HARNESS + """
      store.set('http://app.test/api/grocery-list/by-store?status=needed', { cached: true });
      const api = fetchEvent('/api/grocery-list/by-store?status=needed');
      const post = fetchEvent('/api/grocery-list/1/status');
      const page = fetchEvent('/grocery', 'navigate');
      out({ api: api.responded === null, post: post.responded === null, page: page.responded !== null });
    """)
    assert got == {"api": True, "post": True, "page": True}


@_needs_node
def test_offline_navigation_to_grocery_lands_on_the_cached_shell():
    """Only "/" was ever fetched as a navigation (Shop was reached by
    tapping the tab). Reloading /grocery in the store must still open."""
    got = _node(_SW_HARNESS + """
      store.set('http://app.test/', { body: 'shell' });
      fetchEvent('/grocery', 'navigate').responded.then((res) => out({ served: res && res.body }));
    """)
    assert got == {"served": "shell"}


@_needs_node
def test_a_signed_out_redirect_is_never_cached_as_the_shell():
    got = _node(_SW_HARNESS + """
      fetch = async () => ({ ok: true, redirected: true, clone() { return this; }, body: 'login' });
      (async () => {
        await fetchEvent('/', 'navigate').responded;
        await new Promise((r) => setTimeout(r, 5));
        const cachedLogin = store.has('http://app.test/');
        fetch = async () => ({ ok: true, redirected: false, clone() { return this; }, body: 'shell' });
        await fetchEvent('/', 'navigate').responded;
        await new Promise((r) => setTimeout(r, 5));
        out({ cachedLogin, cachedShell: store.get('http://app.test/').body });
      })();
    """)
    assert got == {"cachedLogin": False, "cachedShell": "shell"}


def test_service_worker_cache_version_was_bumped_for_the_new_fallback():
    src = SW.read_text(encoding="utf-8")
    assert 'const CACHE_NAME = "pomona-shell-v6";' in src
    assert 'if (url.pathname.startsWith("/api/")) return;' in src


# ---------------------------------------------------------------------------
# 3. The shell's wiring (markers — the behaviour is in the module above)
# ---------------------------------------------------------------------------

def test_shell_loads_the_offline_module_before_its_own_script():
    a = SHELL_HTML.index('<script src="/static/grocery-offline.js"></script>')
    b = SHELL_HTML.index('<script src="/static/shell.js"></script>')
    assert a < b


def test_both_ticks_go_through_the_offline_path():
    trip = SHELL_JS.index("case 'trip-toggle':")
    assert "groTick(id, 'in_cart');" in SHELL_JS[trip:trip + 200]
    un = SHELL_JS.index("case 'uncheck':")
    assert "groTick(id, 'needed');" in SHELL_JS[un:un + 200]


def test_a_failed_load_falls_back_to_the_copy_and_keeps_the_step():
    load = SHELL_JS[SHELL_JS.index("async function loadGrocery()"):]
    load = load[:load.index("function refreshGroceryPanel()")]
    assert "groOffline.readList()" in load
    assert "groOffline.applyPending(copy.data)" in load
    assert "groOffline.saveList(pair[0])" in load
    # The step is never reset here: a reload mid-trip stays mid-trip.
    assert "groceryState.step" not in load


def test_the_status_line_copy_is_exactly_what_emily_saw():
    assert 'var GRO_OFFLINE_LINE = "No signal — I’ll save your ticks when you’re back.";' in SHELL_JS
    assert 'var GRO_CATCHING_UP_LINE = "Saving your ticks…";' in SHELL_JS
    assert 'var GRO_CAUGHT_UP_TOAST = "Back online — your ticks are saved.";' in SHELL_JS
    assert 'var GRO_NO_SIGNAL_TOAST = "No signal — try that once you’re back.";' in SHELL_JS
    assert 'var GRO_NO_COPY_LINE = "No signal — I’ll show your list as soon as you’re back.";' in SHELL_JS
    assert 'var ASK_NO_SIGNAL_LINE = "I need a signal for this one — try again when you’re back.";' in SHELL_JS
    assert '<p class="gro-offline" id="gro-offline" hidden></p>' in SHELL_JS
    assert ".gro-offline {" in SHELL_CSS
    # Secondary ink: no signal in a supermarket is ordinary, not urgent.
    block = SHELL_CSS[SHELL_CSS.index(".gro-offline {"):]
    block = block[:block.index("}")]
    assert "var(--ink-secondary)" in block
    assert "urgent" not in block


@_needs_node
def test_the_dropped_ticks_toast_counts_and_stays_plain():
    block = SHELL_JS[SHELL_JS.index("function groTicksDroppedToast(n)"):]
    block = block[:block.index("\n  }\n") + 4]
    got = _node(block + "console.log(JSON.stringify([groTicksDroppedToast(1), groTicksDroppedToast(3)]));")
    assert got == [
        "One tick couldn’t be saved — the list is up to date now.",
        "3 ticks couldn’t be saved — the list is up to date now.",
    ]
    assert "stick" not in block


def test_sign_out_and_a_401_forget_the_copy():
    signout = SHELL_JS[SHELL_JS.index("if (what === 'signout') {"):]
    signout = signout[:signout.index("window.location.href = '/logout';")]
    assert "groForgetOffline();" in signout
    assert "if (res.status === 401) groForgetOffline();" in SHELL_JS  # groPostJson and /api/coaching
    assert "if (results.some(function (r) { return r.status === 401; })) groForgetOffline();" in SHELL_JS
    coaching = SHELL_JS[SHELL_JS.index("function loadCoachingState()"):]
    coaching = coaching[:coaching.index("renderCoachCard();")]
    assert "groOffline.setHousehold(state.household_id) && groceryState.offline" in coaching


def test_claude_features_say_they_need_a_signal_rather_than_looking_broken():
    ask = SHELL_JS[SHELL_JS.index("async function sendAskMessage(message)"):]
    ask = ask[:ask.index("Composer auto-grow")]
    assert "addAskMessage('assistant', askNoSignal ? ASK_NO_SIGNAL_LINE : 'Error: ' + err.message);" in ask
    inv = (REPO / "static" / "inventory.html").read_text(encoding="utf-8")
    assert 'noSignal ? "I need a signal for this one" : "Couldn\'t read that photo"' in inv
    assert 'noSignal ? "Try again when you’re back."' in inv


def test_online_and_offline_events_are_wired_when_the_screen_is_built():
    wire = SHELL_JS[SHELL_JS.index("function groWireConnectivity()"):]
    wire = wire[:wire.index("\n  }\n")]
    assert "window.addEventListener('online'," in wire
    assert "window.addEventListener('offline'," in wire
    build = SHELL_JS[SHELL_JS.index("function buildGroceryPanel(panel)"):]
    build = build[:build.index("async function groLoadUsualStores()")]
    assert "groWireConnectivity();" in build
    assert "groOffline.setHousehold(state.household_id)" in SHELL_JS


# ---------------------------------------------------------------------------
# 3b. The shell's own functions, run — loadGrocery, groTick, groReplayQueue
# ---------------------------------------------------------------------------

def _grocery_region() -> str:
    """The whole Grocery region, from its icons to the hands-free voice
    code — the same slice tests/test_grocery_fast_sort.py runs."""
    start = SHELL_JS.index("  var GRO_STORE_PALETTE = [")
    end = SHELL_JS.index("  // ---------- Hands-free voice ----------", start)
    return SHELL_JS[start:end]


# A browser in forty lines: localStorage, a fetch that can be switched
# between "dead" (throws, as a phone with no signal does) and "alive"
# (answers the three list GETs from FIXTURE and every POST with 200), a
# panel that has ONLY the no-signal line — renderGrocery finds none of its
# other elements and returns early, which is exactly what keeps this a
# test of the data path rather than of markup — and the module itself.
_SHELL_HARNESS = f"""
function escapeHtml(s){{return String(s == null ? '' : s);}}
const STORE = new Map();
// The household the shell learned last time it had signal (it comes from
// /api/coaching, which is outside this slice), as a real phone remembers.
STORE.set('pomona.grocery.household', '1');
const window = {{
  localStorage: {{
    getItem: (k) => (STORE.has(k) ? STORE.get(k) : null),
    setItem: (k, v) => {{ STORE.set(k, String(v)); }},
    removeItem: (k) => {{ STORE.delete(k); }},
  }},
  addEventListener: () => {{}},
  history: {{ pushState: () => {{}} }},
  PomonaGroceryOffline: require({json.dumps(str(MODULE))}),
}};
const navigator = {{ onLine: true }};
let ALIVE = true;
const POSTS = [];
const FIXTURE = {{ stores: [ {{ store: 'Loblaws', sections: [
  {{ section: 'produce', items: [ {{ id: 1, item: 'Bananas', quantity: '6', category: 'produce', status: 'needed', store: 'Loblaws' }} ] }},
  {{ section: 'dairy',   items: [ {{ id: 2, item: 'Milk',    quantity: '2 l', category: 'dairy',  status: 'needed', store: 'Loblaws' }} ] }},
] }} ] }};
function fetch(url, opts) {{
  if (!ALIVE) return Promise.reject(new TypeError('Failed to fetch'));
  if (opts && opts.method === 'POST') {{
    POSTS.push([url, JSON.parse(opts.body || '{{}}').status]);
    return Promise.resolve({{ ok: true, status: 200, json: () => Promise.resolve({{}}) }});
  }}
  const body = url.startsWith('/api/grocery-list/by-store') ? FIXTURE
    : url.startsWith('/api/grocery-list?') ? {{ sections: [] }}
    : url === '/api/memory' ? {{ usual_stores: ['Loblaws'], stores_prompt_dismissed: true }}
    : {{}};
  return Promise.resolve({{ ok: true, status: 200, json: () => Promise.resolve(body) }});
}}
const LINE = {{ hidden: true, textContent: '' }};
const panels = {{ grocery: {{ dataset: {{ built: '1' }}, querySelector: (sel) => (sel === '#gro-offline' ? LINE : null) }} }};
const TOASTS = [];
function showToast(msg) {{ TOASTS.push(msg); }}
var scrollEl = null;
function activateTab() {{}}
var coachState = {{ householdId: 1 }};
// The retry timer (groReplayQueue's 30s interval) keeps node alive while
// anything is still queued, so the harness exits once it has answered.
const out = (v) => {{ console.log(JSON.stringify(v)); setTimeout(() => process.exit(0), 0); }};
"""


def _run_shell(tail: str):
    return _node(_SHELL_HARNESS + _grocery_region() + tail)


@_needs_node
def test_a_tick_with_no_signal_shows_at_once_and_is_sent_when_signal_returns():
    """The whole store visit, in order: load with signal; lose it; tick two
    things (both move to the cart, the line appears, nothing is sent); get
    signal back; the queue sends both in order, the line goes, and the
    household hears it caught up."""
    got = _run_shell("""
      (async () => {
        await loadGrocery();
        const loaded = { needed: groceryState.data.stores.Loblaws.sections.length, line: LINE.hidden };
        ALIVE = false;
        groTick('1', 'in_cart');
        groTick('2', 'in_cart');
        await new Promise((r) => setTimeout(r, 30));
        const offline = {
          inCart: groceryState.data.stores.Loblaws.inCart.map((it) => it.item),
          line: LINE.textContent, hidden: LINE.hidden,
          queued: JSON.parse(STORE.get('pomona.grocery.queue.h1')).map((op) => [op.id, op.status]),
          posts: POSTS.length,
        };
        ALIVE = true;
        await groReplayQueue();
        await new Promise((r) => setTimeout(r, 30));
        out({ loaded, offline, back: { posts: POSTS, queueLeft: STORE.has('pomona.grocery.queue.h1'),
              line: LINE.hidden, toasts: TOASTS, offline: groceryState.offline } });
      })();
    """)
    assert got["loaded"] == {"needed": 2, "line": True}
    assert got["offline"]["inCart"] == ["Bananas", "Milk"]
    assert got["offline"]["line"] == "No signal — I’ll save your ticks when you’re back."
    assert got["offline"]["hidden"] is False
    assert got["offline"]["queued"] == [["1", "in_cart"], ["2", "in_cart"]]
    assert got["offline"]["posts"] == 0
    assert got["back"]["posts"] == [["/api/grocery-list/1/status", "in_cart"], ["/api/grocery-list/2/status", "in_cart"]]
    assert got["back"]["queueLeft"] is False
    assert got["back"]["line"] is True
    assert got["back"]["toasts"] == ["Back online — your ticks are saved."]
    assert got["back"]["offline"] is False


@_needs_node
def test_a_reload_with_no_signal_still_shows_the_list_with_its_ticks():
    """What used to be "Couldn't load the grocery list": the copy from the
    last signal, with the unsent ticks on top, and no error."""
    got = _run_shell("""
      (async () => {
        await loadGrocery();
        ALIVE = false;
        groTick('2', 'in_cart');
        await new Promise((r) => setTimeout(r, 30));
        // A reload: fresh page state over the same storage, still no signal.
        groceryState.data = null; groceryState.offline = false; groceryState.loadError = false;
        await loadGrocery();
        out({ loadError: groceryState.loadError, offline: groceryState.offline, line: LINE.textContent,
              needed: groceryState.data.stores.Loblaws.sections.map((s) => s.items.map((it) => it.item)),
              inCart: groceryState.data.stores.Loblaws.inCart.map((it) => it.item) });
      })();
    """)
    assert got["loadError"] is False
    assert got["offline"] is True
    assert got["line"] == "No signal — I’ll save your ticks when you’re back."
    assert got["needed"] == [["Bananas"]]
    assert got["inCart"] == ["Milk"]


@_needs_node
def test_a_reload_with_no_signal_and_no_known_household_shows_the_wait_not_a_list():
    """Signed out (or never identified this session) and offline: no copy
    is shown, whatever storage holds, and it is a wait rather than an error."""
    got = _run_shell("""
      (async () => {
        await loadGrocery();               // signed in as household 1, copy written
        groForgetOffline();                // sign-out
        ALIVE = false;
        groceryState.data = null;
        await loadGrocery();
        out({ data: groceryState.data, loadError: groceryState.loadError, offline: groceryState.offline, keys: [...STORE.keys()] });
      })();
    """)
    assert got == {"data": None, "loadError": "no-signal", "offline": False, "keys": []}


@_needs_node
def test_the_shops_answer_survives_a_reload_with_no_signal():
    """usualStores comes from /api/memory. Offline it would be empty, which
    is what puts the shops card up over the list; the kept answer stops it."""
    got = _run_shell("""
      (async () => {
        await groLoadUsualStores();
        const online = { shops: groceryState.usualStores.slice(), card: groStoresPromptShouldShow() };
        ALIVE = false;
        groceryState.usualStores = []; groceryState.storesPromptDismissed = false;
        await groLoadUsualStores();
        out({ online, offline: { shops: groceryState.usualStores, card: groStoresPromptShouldShow() } });
      })();
    """)
    assert got["online"] == {"shops": ["Loblaws"], "card": False}
    assert got["offline"] == {"shops": ["Loblaws"], "card": False}


@_needs_node
def test_a_tick_with_signal_goes_straight_to_the_server_and_queues_nothing():
    """A user who is never offline sees no change at all."""
    got = _run_shell("""
      (async () => {
        await loadGrocery();
        groTick('1', 'in_cart');
        await new Promise((r) => setTimeout(r, 30));
        out({ posts: POSTS, queued: STORE.has('pomona.grocery.queue.h1'), line: LINE.hidden, toasts: TOASTS, offline: groceryState.offline });
      })();
    """)
    assert got == {"posts": [["/api/grocery-list/1/status", "in_cart"]], "queued": False, "line": True, "toasts": [], "offline": False}


# ---------------------------------------------------------------------------
# 4. The server: the status route is safe to replay
# ---------------------------------------------------------------------------

def _inventory_quantity(name: str) -> str | None:
    for row in tools.get_inventory():
        if row["item"].lower() == name.lower():
            return row["quantity"]
    return None


def test_marking_purchased_twice_adds_to_inventory_once():
    item = tools.add_grocery_item("Bananas", quantity="6", category="produce")
    tools.mark_grocery_item(item["item_id"], "purchased")
    once = _inventory_quantity("Bananas")
    again = tools.mark_grocery_item(item["item_id"], "purchased")
    assert again.get("unchanged") is True
    assert _inventory_quantity("Bananas") == once
    assert once is not None


def test_the_status_route_answers_a_replayed_tick_the_same_way(signed_in):
    item = tools.add_grocery_item("Milk", quantity="2 l", category="dairy")
    url = f"/api/grocery-list/{item['item_id']}/status"
    first = signed_in.post(url, json={"status": "in_cart"})
    second = signed_in.post(url, json={"status": "in_cart"})
    assert first.status_code == 200 and second.status_code == 200
    rows = signed_in.get("/api/grocery-list?status=in_cart").json()["sections"]
    assert [it["item"] for s in rows for it in s["items"]] == ["Milk"]
    back = signed_in.post(url, json={"status": "needed"})
    assert back.status_code == 200
    assert signed_in.get("/api/grocery-list?status=in_cart").json()["sections"] == []


def test_a_tick_for_a_row_that_is_gone_is_a_404_so_the_queue_drops_it(signed_in):
    item = tools.add_grocery_item("Peas", quantity="1 bag", category="frozen")
    tools.remove_grocery_item(item["item_id"])
    res = signed_in.post(f"/api/grocery-list/{item['item_id']}/status", json={"status": "in_cart"})
    assert res.status_code == 404
