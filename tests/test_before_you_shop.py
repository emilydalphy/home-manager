"""
"Before you shop" — the pass in front of sorting the grocery list.

Loop Board 'Shop: "Before you shop" — regulars, then spices and oils, then
already-have-it, ending on Sort the list' (High, Phase 1). Gowthami's
household, Sunday 2026-10-04, after a week of real use:

    "Sorting option is hidden and needs to be clearer that's the next step."

She also never saw the staples card and didn't understand the spices
section. The card's answer is one errand in front of the list: Shop's dock
carries ONE apricot that names the next thing, and behind it a short pass
of one-question screens that ends ON the sort.

SLICE 1 — THE FRAME, which is what this file covers. The card's own Notes
allow slices and name them: "(1) frame + sort as the clear last step, (2)
regulars step, (3) spices step, (4) already-have-it step." The three
content steps are slices 2-4 and are NOT built, so BEFORE_SHOP_STEPS ships
empty and the dock reads "Sort the list (N)" — which is honest, and is
already the whole of the complaint above. The label becomes "Before you
shop" by itself the moment a step is registered.

So the frame is what is tested, and it is tested the way slice 2 will
reach it: with a PROBE pushed into BEFORE_SHOP_STEPS. That is deliberate.
A frame whose only exercised path is the empty one is a frame nobody has
driven, and the card explicitly asks for one a step can be added to at the
FRONT ("step 0: update your inventory" is coming and is not this card).

Red against the parent commit: not quoted, and it would mean nothing —
app/tools/before_shop.py, the route, the column and every one of the
screen functions are new, so the whole file is a name error there rather
than a behaviour catch. The evidence is the mutation table in the report.
"""
from __future__ import annotations

import datetime
import json
import shutil
import sqlite3
from pathlib import Path
from unittest import mock

import nodeharness
import pytest
from conftest import household_today
from shop_harness import CLICK, FIXTURE, STUB, cook_progress, grocery_block

from app import tools
from app.db import get_conn

REPO = Path(__file__).resolve().parent.parent
SHELL_JS = (REPO / "static" / "shell.js").read_text(encoding="utf-8")
SHELL_CSS = (REPO / "static" / "shell.css").read_text(encoding="utf-8")

needs_node = pytest.mark.skipif(
    shutil.which("node") is None, reason="node runs the screen's own functions"
)


# ---------------------------------------------------------------------------
# 1. The stamp: once per week, and the week is the plan
# ---------------------------------------------------------------------------


def _plan(start=None, days=7, approved=False):
    start = start or household_today()
    pid = tools.create_weekly_plan(start.isoformat(), day_count=days)["weekly_plan_id"]
    if approved:
        # A DRAFT whose last day has passed is deliberately never the
        # current plan (Emily, 2026-09-11 — the Plan tab opened on a
        # twelve-day-dead draft). An approved one still is: it was the
        # household's real week. So last week's plan has to be approved
        # for "this used to be the current plan" to be true of it, which
        # is also what it would be in life.
        conn = get_conn()
        try:
            conn.execute("UPDATE weekly_plans SET status = 'approved' WHERE id = ?", (pid,))
            conn.commit()
        finally:
            conn.close()
    return pid


def _asked(plan_id):
    conn = get_conn()
    try:
        row = conn.execute(
            "SELECT before_shop_asked_at FROM weekly_plans WHERE id = ?", (plan_id,)
        ).fetchone()
        return row["before_shop_asked_at"] if row else None
    finally:
        conn.close()


def test_a_fresh_week_has_not_been_through_the_pass():
    """CATCH. NULL means "never run", which is what makes the pass get
    offered — the same reading defrost_asked_at and cook_ahead_asked_at
    already have."""
    pid = _plan()
    assert _asked(pid) is None
    assert tools.before_shop_state() == {"done": False, "weekly_plan_id": pid}


def test_finishing_it_once_is_what_stops_it_coming_back():
    """CATCH. The whole card criterion: "Done once per week; afterwards the
    list opens straight to its sorted view.\""""
    pid = _plan()
    assert tools.mark_before_shop_done() == {"done": True, "weekly_plan_id": pid}
    assert tools.before_shop_state() == {"done": True, "weekly_plan_id": pid}
    assert _asked(pid) is not None


def test_finishing_it_twice_says_the_same_thing():
    """CATCH. Two taps, a retried POST, a replay — none of them is an error
    and none of them changes the answer."""
    pid = _plan()
    first = tools.mark_before_shop_done()
    second = tools.mark_before_shop_done()
    assert first == second == {"done": True, "weekly_plan_id": pid}


def test_a_new_week_is_a_new_plan_so_the_pass_comes_back():
    """CATCH, and it is the reason the stamp is a column on the plan rather
    than a date anything compares. "Once per week" falls out of the row
    being new — which is also what makes it right for a household planning
    five days, or two weeks at once, where "a week" is not seven days and a
    date comparison would have to guess."""
    today = household_today()
    first = _plan(today - datetime.timedelta(days=7), approved=True)
    tools.mark_before_shop_done()
    assert tools.before_shop_state()["weekly_plan_id"] == first

    _plan(today)
    state = tools.before_shop_state()
    assert state["weekly_plan_id"] != first
    assert state["done"] is False, "a new week asks again"
    assert _asked(first) is not None, "and last week's answer is left where it was"


def test_a_household_with_no_plan_at_all_is_offered_the_pass_and_stamps_nothing():
    """CATCH. There is nowhere to record it, so the honest answer is to
    offer it: an extra tap beats hiding the pass from a household whose
    list came from somewhere other than a week. Marking it must not raise
    — the only caller is the grocery payload."""
    assert tools.before_shop_state() == {"done": False, "weekly_plan_id": None}
    assert tools.mark_before_shop_done() == {"done": False, "weekly_plan_id": None}


def test_the_stamp_is_scoped_to_the_household():
    """CATCH. Every write in this app is, and this one names the household
    in its WHERE even though it has already resolved the row by id — the
    belt-and-braces rule the 2026-09-26 sweep put on every owned table."""
    mine = _plan()
    conn = get_conn()
    try:
        conn.execute("INSERT INTO households (id, name) VALUES (99, 'Next door')")
        conn.commit()
    finally:
        conn.close()
    with tools.use_household(99):
        theirs = _plan()
    tools.mark_before_shop_done()
    assert _asked(mine) is not None
    assert _asked(theirs) is None, "the other household's week is untouched"
    with tools.use_household(99):
        assert tools.before_shop_state() == {"done": False, "weekly_plan_id": theirs}


def test_a_read_that_fails_does_not_take_the_grocery_list_down_with_it():
    """CATCH. before_shop_state is called from inside the list payload, so
    raising there is a list that will not load — much worse than a pass
    offered one time too many."""
    _plan()
    with mock.patch("app.tools.before_shop.get_conn", side_effect=sqlite3.OperationalError("nope")):
        assert tools.before_shop_state() == {"done": False, "weekly_plan_id": None}


def test_a_failed_write_lets_them_on_to_the_sort_anyway():
    """CATCH. They are on their way to the sort screen; stopping them over
    bookkeeping would be the worse answer. A dropped write means the pass
    is offered once more, which is the safe direction — asking twice is
    cheaper than never asking."""
    _plan()
    with mock.patch("app.tools.before_shop.get_conn", side_effect=sqlite3.OperationalError("nope")):
        assert tools.mark_before_shop_done() == {"done": False, "weekly_plan_id": None}


# ---------------------------------------------------------------------------
# 2. The routes
# ---------------------------------------------------------------------------


def test_the_state_rides_on_the_view_the_shop_tab_actually_opens(signed_in):
    """CATCH, and it is the bug the first cut had. groLoadAllData reads
    /api/grocery-list/by-store for the needed half; the plain
    /api/grocery-list is only read for the bought one. Stamping the plain
    view alone put the answer somewhere the screen never looks."""
    pid = _plan()
    tools.add_grocery_item("Orzo", "1 box", category="pantry")
    by_store = signed_in.get("/api/grocery-list/by-store?status=needed").json()
    assert by_store["before_shop"] == {"done": False, "weekly_plan_id": pid}
    plain = signed_in.get("/api/grocery-list?status=needed").json()
    assert plain["before_shop"] == by_store["before_shop"], "both needed views agree"


def test_the_bought_view_says_nothing_about_a_pass_it_cannot_offer(signed_in):
    """GUARD. Pinned by the "state on every status" mutation: the bought
    view is read by screens with no pass in front of them, and a key there
    would be an answer to a question nobody asked."""
    _plan()
    tools.add_grocery_item("Orzo", "1 box", category="pantry")
    assert "before_shop" not in signed_in.get("/api/grocery-list?status=bought").json()


def test_the_route_records_the_pass_and_is_safe_to_call_twice(signed_in):
    """CATCH."""
    pid = _plan()
    first = signed_in.post("/api/grocery-list/before-shop-done")
    assert first.status_code == 200
    assert first.json() == {"done": True, "weekly_plan_id": pid}
    assert signed_in.post("/api/grocery-list/before-shop-done").json() == first.json()


def test_the_route_needs_a_signed_in_household(client):
    """GUARD on the app's own auth middleware, pinned here because this
    route writes."""
    assert client.post("/api/grocery-list/before-shop-done").status_code == 401


# ---------------------------------------------------------------------------
# 3. The dock: Shop's one apricot says what to do next
# ---------------------------------------------------------------------------

# A probe pass. This is how slice 2 will register a step, and registering
# one is the only way to drive the frame at all — see the module docstring.
# `has` is what makes a step with nothing to ask disappear rather than
# render empty, which is the card's own rule.
_PROBE = """
function probe(key, opts) {
  opts = opts || {};
  return {
    key: key,
    title: opts.title || ('Probe ' + key),
    line: opts.line || ('The ' + key + ' line.'),
    has: opts.has || function () { return true; },
    body: opts.body || function () { return '<p class="probe-' + key + '">body</p>'; },
    dock: opts.dock || null
  };
}
function withSteps() {
  BEFORE_SHOP_STEPS.length = 0;
  for (var i = 0; i < arguments.length; i++) BEFORE_SHOP_STEPS.push(arguments[i]);
}
// Two shops with one thing each plus a loose thing, so there is always
// something unsorted and sorting is a real question (groCanSort).
function unsortedList() {
  return setUp(1, [
    { store: 'Costco', items: [{ id: 3, item: 'Orzo', quantity: '1 box', store: 'Costco', store_decided: 1, category: 'pantry', status: 'needed' }] },
    { store: 'Loblaws', items: [{ id: 10, item: 'Lemons', quantity: '3', store: 'Loblaws', store_decided: 1, category: 'produce', status: 'needed' }] }
  ], ['Costco', 'Loblaws']);
}
function sortedList() {
  return setUp(0, [
    { store: 'Costco', items: [{ id: 3, item: 'Orzo', quantity: '1 box', store: 'Costco', store_decided: 1, category: 'pantry', status: 'needed' }] },
    { store: 'Loblaws', items: [{ id: 10, item: 'Lemons', quantity: '3', store: 'Loblaws', store_decided: 1, category: 'produce', status: 'needed' }] }
  ], ['Costco', 'Loblaws']);
}
"""


def _node(body: str, timeout: int = 30):
    res = nodeharness.run_node(
        STUB + cook_progress() + grocery_block() + CLICK + FIXTURE + _PROBE + body,
        timeout=timeout,
    )
    assert res.returncode == 0, f"node failed: {res.stderr}"
    return json.loads(res.stdout.strip())


@needs_node
def test_the_screen_carries_the_state_off_the_by_store_payload():
    """CATCH, and the client half of the bug above. groLoadAllData BUILDS
    the object the screen reads rather than passing the response through,
    so a key the server sends and that function does not name never
    arrives — which is exactly how before_shop went missing on the first
    cut. Driving the real function is the only way to see that: every
    other test here hands `data.beforeShop` over by name, so the mapping
    itself is covered by nothing."""
    out = _node("""
// api.js resolves `fetch` by name at request time, in this same scope, so
// re-pointing the binding is what lets the real groLoadAllData read a real
// by-store payload. The bought view is read second and says nothing about
// the pass, which is the other half of the contract.
var BY_STORE = {
  stores: [{ store: 'Costco', sections: [{ items: [{ id: 3, item: 'Orzo', status: 'needed' }] }] }],
  before_shop: { done: false, weekly_plan_id: 7 }
};
fetch = function (url) {
  var body = url.indexOf('by-store') !== -1 ? BY_STORE : { sections: [] };
  return Promise.resolve({ ok: true, status: 200, json: function () { return Promise.resolve(body); } });
};
groLoadAllData().then(function (built) {
  console.log(JSON.stringify({ beforeShop: built.beforeShop }));
});
""")
    assert out["beforeShop"] == {"done": False, "weekly_plan_id": 7}


@needs_node
def test_a_payload_with_no_state_on_it_leaves_the_screen_with_none():
    """GUARD. A deployment older than the column answers without the key,
    and the frame reads a missing state as "no pass to offer" rather than
    crashing on it — the stance every other optional key on this payload
    takes."""
    out = _node("""
fetch = function () {
  return Promise.resolve({ ok: true, status: 200, json: function () { return Promise.resolve({ sections: [] }); } });
};
groLoadAllData().then(function (built) {
  console.log(JSON.stringify({ beforeShop: built.beforeShop }));
});
""")
    assert out["beforeShop"] is None


@needs_node
def test_with_nothing_registered_the_dock_is_the_sort_and_says_how_many():
    """CATCH, and it is slice 1 shipping honestly. No steps built yet, so
    the pass has nothing to ask and the dock goes straight to the thing the
    tester could not find — named, counted, and the list's one apricot."""
    out = _node("""
var data = unsortedList();
console.log(JSON.stringify({
  steps: BEFORE_SHOP_STEPS.length,
  dock: groDockHtml(data, 'list')
}));
""")
    assert out["steps"] == 0, "the frame ships with no steps — slices 2-4 register them"
    dock = out["dock"]
    assert 'data-gro="goto-sort"' in dock and ">Sort the list (1)<" in dock
    assert "Before you shop" not in dock, "nothing to ask means nothing to open"
    assert dock.count("dock-primary") == 1, "one apricot (rule 5)"


@needs_node
def test_a_step_with_something_to_ask_turns_the_dock_into_the_pass():
    """CATCH. The moment slice 2 registers its step, the label becomes the
    card's own words — with no change to this function."""
    out = _node("""
var data = unsortedList();
withSteps(probe('a'));
console.log(JSON.stringify({ dock: groDockHtml(data, 'list'), label: BEFORE_SHOP_LABEL }));
""")
    assert out["label"] == "Before you shop"
    assert 'data-gro="goto-beforeshop"' in out["dock"]
    assert ">Before you shop<" in out["dock"]
    assert "goto-sort" not in out["dock"], "the sort is the pass's last button, not a second one"
    assert out["dock"].count("dock-primary") == 1


@needs_node
def test_a_step_with_nothing_to_ask_this_week_is_not_counted_at_all():
    """CATCH. "Skipped entirely when there is nothing to show" — the card's
    rule for step 3, applied by the frame to every step, so a step never
    has to render an empty screen."""
    out = _node("""
var data = unsortedList();
withSteps(probe('a', { has: function () { return false; } }), probe('b'));
console.log(JSON.stringify({
  live: beforeShopSteps(data).map(function (s) { return s.key; }),
  registered: BEFORE_SHOP_STEPS.length
}));
""")
    assert out["registered"] == 2
    assert out["live"] == ["b"], "only the step with something to ask"


@needs_node
def test_a_step_whose_has_throws_is_dropped_rather_than_breaking_the_dock():
    """GUARD, pinned by the "no try around has()" mutation. A step's own
    question is its own business and slice 2's will read staples; a throw
    there must cost that step, never Shop's dock."""
    out = _node("""
var data = unsortedList();
withSteps(probe('a', { has: function () { throw new Error('nope'); } }), probe('b'));
console.log(JSON.stringify({
  live: beforeShopSteps(data).map(function (s) { return s.key; }),
  dock: groDockHtml(data, 'list')
}));
""")
    assert out["live"] == ["b"]
    assert 'data-gro="goto-beforeshop"' in out["dock"]


@needs_node
def test_a_pass_already_run_this_week_sends_you_straight_to_the_sort():
    """CATCH. The card: "Done once per week; afterwards the list opens
    straight to its sorted view.\""""
    out = _node("""
var data = unsortedList();
data.beforeShop = { done: true, weekly_plan_id: 7 };
withSteps(probe('a'));
console.log(JSON.stringify({ dock: groDockHtml(data, 'list') }));
""")
    assert ">Sort the list (1)<" in out["dock"]
    assert "goto-beforeshop" not in out["dock"]


@needs_node
def test_the_devices_own_answer_covers_the_beat_before_the_server_catches_up():
    """CATCH. The POST is not waited on, so between finishing the pass and
    the re-read landing the dock would otherwise offer it again. The local
    flag holds a PLAN ID rather than a boolean: a new week is a new row, so
    it stops matching by itself and nothing has to remember to clear it."""
    out = _node("""
var data = unsortedList();
data.beforeShop = { done: false, weekly_plan_id: 7 };
withSteps(probe('a'));
var before = beforeShopIsDone(data);
groceryState.beforeShopDoneFor = 7;
var sameWeek = beforeShopIsDone(data);
data.beforeShop = { done: false, weekly_plan_id: 8 };
var nextWeek = beforeShopIsDone(data);
console.log(JSON.stringify({ before: before, sameWeek: sameWeek, nextWeek: nextWeek }));
""")
    assert out["before"] is False
    assert out["sameWeek"] is True, "this device just finished it"
    assert out["nextWeek"] is False, "a new week asks again, with nothing to clear"


@needs_node
def test_a_household_with_no_plan_still_gets_the_pass_offered():
    """GUARD, pinned by the "done when there is no plan" mutation. There is
    nothing to stamp, so the honest answer is to ask."""
    out = _node("""
var data = unsortedList();
data.beforeShop = { done: false, weekly_plan_id: null };
groceryState.beforeShopDoneFor = null;
withSteps(probe('a'));
console.log(JSON.stringify({ done: beforeShopIsDone(data), dock: groDockHtml(data, 'list') }));
""")
    assert out["done"] is False
    assert 'data-gro="goto-beforeshop"' in out["dock"]


@needs_node
def test_a_fully_sorted_list_grows_no_action_at_all():
    """GUARD, and the rule it protects is nav v2's: "a screen with no single
    action has NO dock". Nothing to sort means nothing to say, so the add
    outline is the whole dock, exactly as it was before this card."""
    out = _node("""
var data = sortedList();
withSteps(probe('a'));
console.log(JSON.stringify({
  unsorted: groUnsorted(data).length,
  dock: groDockHtml(data, 'list')
}));
""")
    assert out["unsorted"] == 0
    assert "dock-primary" not in out["dock"], "no sort to offer, no apricot"
    assert 'data-gro="add-open"' in out["dock"], "the add outline is still there"


@needs_node
def test_the_shops_question_is_answered_before_anything_else():
    """GUARD, pinned by the "no stores-prompt guard" mutation. That card
    carries its own primary, so a second one beside it would be two
    apricots on one screen — and it asks where the household shops, which
    sorting by store depends on."""
    out = _node("""
var data = unsortedList();
data.usualStores = [];
groceryState.usualStores = [];
groceryState.storesPromptDismissed = false;
withSteps(probe('a'));
console.log(JSON.stringify({
  shows: groStoresPromptShouldShow(),
  pass: beforeShopDockHtml(data)
}));
""")
    assert out["shows"] is True
    assert out["pass"] == "", "nothing until the shops question is answered"


@needs_node
def test_a_one_shop_household_is_never_sent_to_sort_anything():
    """GUARD. Emily, 2026-09-09: a household with one shop or none has no
    sorting question — groUnsorted returns nothing for them, which takes
    the pass's last button off with it rather than offering a screen that
    asks them to choose between one option and itself."""
    out = _node("""
var data = setUp(2, [], ['Costco']);
withSteps(probe('a'));
console.log(JSON.stringify({
  canSort: groCanSort(data),
  unsorted: groUnsorted(data).length,
  dock: groDockHtml(data, 'list')
}));
""")
    assert out["canSort"] is False
    assert out["unsorted"] == 0
    assert "dock-primary" not in out["dock"]


# ---------------------------------------------------------------------------
# 4. The pass itself: one screen, one question, one primary
# ---------------------------------------------------------------------------


@needs_node
def test_tapping_the_dock_opens_the_pass_at_its_first_step():
    out = _node("""
var data = unsortedList();
withSteps(probe('a'), probe('b'));
groceryState.beforeShopIndex = 9;
clickIfRendered({ gro: 'goto-beforeshop' });
console.log(JSON.stringify({ step: groceryState.step, at: groceryState.beforeShopIndex }));
""")
    assert out["step"] == "beforeshop"
    assert out["at"] == 0, "the pass starts at the start, whatever a previous visit left"


@needs_node
def test_the_head_names_the_errand_and_the_body_asks_the_question():
    """CATCH. The mockup's own structure: "Before you shop" stays put in the
    head while the screens change under it — that is what makes it read as
    one errand — and the step's question is the h4 at the top of the body,
    under the progress bar."""
    out = _node("""
var data = unsortedList();
withSteps(probe('a', { title: 'Need any of your regulars?', line: 'I have ticked the ones you are probably low on.' }), probe('b'));
console.log(JSON.stringify({
  head: groHeadFor(data, 'beforeshop'),
  body: beforeShopBodyHtml(data)
}));
""")
    assert out["head"]["title"] == "Before you shop"
    assert out["head"]["back"] == "‹ Shop"
    assert out["head"]["sub"] == "", "the bar says where you are; the head does not say it twice"
    body = out["body"]
    assert '<h4 class="gro-bs-title">Need any of your regulars?</h4>' in body
    assert '<p class="gro-bs-line">I have ticked the ones you are probably low on.</p>' in body
    assert body.index("cook-progress") < body.index("gro-bs-title") < body.index("gro-bs-line")
    assert body.endswith('<p class="probe-a">body</p>'), "the step's own body, last"


@needs_node
def test_the_progress_bar_is_the_apps_own_and_counts_the_steps_that_are_live():
    """CATCH. One bar in the app (cookProgressHtml, Cook's), not a second —
    "two renderers is how two screens end up saying different things".
    A step with nothing to ask is not in it, because it is not in the pass."""
    out = _node("""
var data = unsortedList();
withSteps(probe('a'), probe('b', { has: function () { return false; } }), probe('c'));
var first = beforeShopProgressHtml(data);
groceryState.beforeShopIndex = 1;
var second = beforeShopProgressHtml(data);
console.log(JSON.stringify({ first: first, second: second }));
""")
    assert 'class="cook-progress"' in out["first"]
    assert out["first"].count("cook-progress-seg") == 2, "two live steps, two segments"
    assert out["first"].count("is-done") == 1, "lit up to the one you are on"
    assert 'aria-valuemax="2" aria-valuenow="1"' in out["first"]
    assert out["second"].count("is-done") == 2
    assert 'aria-valuenow="2"' in out["second"]


@needs_node
def test_a_one_screen_pass_draws_no_bar_because_there_is_no_position_to_show():
    """GUARD, pinned by the "bar at one step too" mutation. A full bar over
    a one-screen errand is saying something untrue (§8)."""
    out = _node("""
var data = unsortedList();
withSteps(probe('a'));
console.log(JSON.stringify({ bar: beforeShopProgressHtml(data), body: beforeShopBodyHtml(data) }));
""")
    assert out["bar"] == ""
    assert "cook-progress" not in out["body"]
    assert "gro-bs-title" in out["body"], "the question is still asked"


@needs_node
def test_the_last_steps_primary_is_the_sort_and_the_ones_before_it_are_not():
    """CATCH. The card: "The last button is 'Sort the list', which opens the
    existing SORT ALL screen." The pass ends ON the thing it was in front
    of, not on a screen saying it is finished."""
    out = _node("""
var data = unsortedList();
withSteps(probe('a'), probe('b'));
var first = beforeShopStepDockHtml(data);
groceryState.beforeShopIndex = 1;
var last = beforeShopStepDockHtml(data);
console.log(JSON.stringify({ first: first, last: last }));
""")
    assert ">Next</button>" in out["first"]
    assert ">Sort the list</button>" in out["last"]
    for dock in (out["first"], out["last"]):
        assert dock.count("dock-primary") == 1, "one primary per step (rule 5)"
        assert 'data-gro="bs-skip"' in dock, "any step can be skipped"


@needs_node
def test_a_step_may_bring_its_own_dock_and_is_told_whether_it_is_the_last():
    """CATCH. The seam slices 2-4 need: the mockup's step 1 is "Add 2 to the
    list" over a sand "None this week", step 2 is one button reading "Next:
    sort the list". Neither is the frame's business, so a step that brings
    a dock gets it rendered instead of the fallback."""
    out = _node("""
var data = unsortedList();
withSteps(
  probe('a', { dock: function (d, last) { return '<i>a:' + last + '</i>'; } }),
  probe('b', { dock: function (d, last) { return '<i>b:' + last + '</i>'; } })
);
var first = beforeShopStepDockHtml(data);
groceryState.beforeShopIndex = 1;
var last = beforeShopStepDockHtml(data);
console.log(JSON.stringify({ first: first, last: last }));
""")
    assert out["first"] == "<i>a:false</i>"
    assert out["last"] == "<i>b:true</i>"


@needs_node
def test_next_and_skip_both_move_on_and_neither_leaves_the_pass_early():
    out = _node("""
var data = unsortedList();
withSteps(probe('a'), probe('b'), probe('c'));
groceryState.step = 'beforeshop';
clickIfRendered({ gro: 'bs-next' });
var afterNext = { step: groceryState.step, at: groceryState.beforeShopIndex };
clickIfRendered({ gro: 'bs-skip' });
var afterSkip = { step: groceryState.step, at: groceryState.beforeShopIndex };
console.log(JSON.stringify({ afterNext: afterNext, afterSkip: afterSkip }));
""")
    assert out["afterNext"] == {"step": "beforeshop", "at": 1}
    assert out["afterSkip"] == {"step": "beforeshop", "at": 2}


@needs_node
def test_the_end_of_the_pass_lands_on_sort_all_and_records_the_pass_once():
    """CATCH. Both ways out of the last step come through one place, so the
    sort and a skip past it are the same answer to "have you been through
    this?\""""
    out = _node("""
var data = unsortedList();
data.beforeShop = { done: false, weekly_plan_id: 4 };
withSteps(probe('a'));
groceryState.step = 'beforeshop';
clickIfRendered({ gro: 'bs-next' });
console.log(JSON.stringify({
  step: groceryState.step,
  doneFor: groceryState.beforeShopDoneFor,
  posts: posts('/api/grocery-list/before-shop-done').length
}));
""")
    assert out["step"] == "sortall", "the pass ends on the sort"
    assert out["doneFor"] == 4
    assert out["posts"] == 1


@needs_node
def test_skipping_every_step_lands_on_the_same_sort_screen():
    """CATCH, and it is the card's "any step can be skipped" taken to its
    end: a household that answers nothing is exactly where they were
    before this card existed."""
    out = _node("""
var data = unsortedList();
data.beforeShop = { done: false, weekly_plan_id: 4 };
withSteps(probe('a'), probe('b'), probe('c'));
groceryState.step = 'beforeshop';
clickIfRendered({ gro: 'bs-skip' });
clickIfRendered({ gro: 'bs-skip' });
clickIfRendered({ gro: 'bs-skip' });
console.log(JSON.stringify({ step: groceryState.step, doneFor: groceryState.beforeShopDoneFor }));
""")
    assert out["step"] == "sortall"
    assert out["doneFor"] == 4


@needs_node
def test_sorting_without_going_through_the_pass_records_nothing():
    """CATCH, and it is the judgment call of this slice. "Has this
    household been through the pass this week?" is the pass's own
    question, and sorting is not an answer to it — a household that taps
    the quiet row in the list, or the dock's sort while the pass had
    nothing to ask, has been asked nothing. Stamping the plan there would
    record something that did not happen (§8), and the cost of not doing
    it is one more tap for somebody who sorted without being asked."""
    out = _node("""
var data = unsortedList();
data.beforeShop = { done: false, weekly_plan_id: 4 };
clickIfRendered({ gro: 'goto-sort' });
console.log(JSON.stringify({
  step: groceryState.step,
  doneFor: groceryState.beforeShopDoneFor,
  posts: posts('/api/grocery-list/before-shop-done').length
}));
""")
    assert out["step"] == "sortall", "the sort still opens"
    assert out["doneFor"] is None
    assert out["posts"] == 0


@needs_node
def test_a_pass_already_recorded_is_not_recorded_again():
    """GUARD, pinned by the "always post" mutation. A re-entered pass, a
    retried tap or a replay would otherwise each be a write."""
    out = _node("""
var data = unsortedList();
data.beforeShop = { done: true, weekly_plan_id: 4 };
withSteps(probe('a'));
groceryState.step = 'beforeshop';
clickIfRendered({ gro: 'bs-next' });
console.log(JSON.stringify({ posts: posts('/api/grocery-list/before-shop-done').length }));
""")
    assert out["posts"] == 0


@needs_node
def test_a_dropped_write_is_not_said_out_loud():
    """GUARD. They are on their way to the sort; a toast about bookkeeping
    would stop them for nothing, and the server's answer is that the pass
    comes back once — which is the safe direction."""
    out = _node("""
var data = unsortedList();
data.beforeShop = { done: false, weekly_plan_id: 4 };
withSteps(probe('a'));
groceryState.step = 'beforeshop';
FAIL_ON = 1;
clickIfRendered({ gro: 'bs-next' });
var step = groceryState.step;
// The write is a promise, so its rejection lands on a microtask AFTER
// this line would have run. Reading TOASTS synchronously would pass
// whatever the catch does, which is not a test of anything — the read is
// in a timer so every microtask has drained by the time it happens.
setTimeout(function () {
  console.log(JSON.stringify({ step: step, toasts: TOASTS.slice() }));
}, 0);
""")
    assert out["step"] == "sortall", "the sort opens anyway"
    assert out["toasts"] == []


@needs_node
def test_a_step_whose_question_went_away_under_it_folds_back_to_the_list():
    """CATCH. The list is live — the other adult can tick the last spice
    while this screen is open — so a step about nothing is a screen about
    nothing. The same fold-back CARRY and SORT ALL already have."""
    out = _node("""
var data = unsortedList();
groceryState.data = data;
withSteps(probe('a', { has: function () { return LIVE; } }));
var LIVE = true;
groceryState.step = 'beforeshop';
var openOk = groScreenStep();
LIVE = false;
console.log(JSON.stringify({ openOk: openOk, after: groScreenStep() }));
""")
    assert out["openOk"] == "beforeshop"
    assert out["after"] == "list"


@needs_node
def test_the_position_is_re_read_against_the_live_steps_rather_than_trusted():
    """CATCH. Same reason: the steps can shrink while the pass is open, and
    an index past the end would render nothing at all."""
    out = _node("""
var data = unsortedList();
withSteps(probe('a'), probe('b'), probe('c'));
groceryState.beforeShopIndex = 2;
var deep = beforeShopIndex(data);
withSteps(probe('a'));
var shrunk = beforeShopIndex(data);
groceryState.beforeShopIndex = -4;
var silly = beforeShopIndex(data);
console.log(JSON.stringify({ deep: deep, shrunk: shrunk, silly: silly }));
""")
    assert out["deep"] == 2
    assert out["shrunk"] == 0, "clamped to the steps that are left"
    assert out["silly"] == 0


@needs_node
def test_the_crumb_is_the_way_out_and_it_names_shop():
    """GUARD on nav v2 rule 1 — one way back per screen, naming its parent,
    never a bare arrow and never history.back()."""
    out = _node("""
var data = unsortedList();
groceryState.data = data;
withSteps(probe('a'));
groceryState.step = 'beforeshop';
var html = screenHtml();
clickIfRendered({ gro: 'step-back' });
console.log(JSON.stringify({
  crumbs: (html.match(/class="crumb"/g) || []).length,
  back: groHeadFor(data, 'beforeshop').back,
  landed: groceryState.step
}));
""")
    assert out["crumbs"] == 1
    assert out["back"] == "‹ Shop"
    assert out["landed"] == "list"


# ---------------------------------------------------------------------------
# 5. Guards on the frame itself
# ---------------------------------------------------------------------------


def _fn(name: str) -> str:
    start = SHELL_JS.index("function " + name + "(")
    depth = 0
    i = SHELL_JS.index("{", start)
    for j in range(i, len(SHELL_JS)):
        if SHELL_JS[j] == "{":
            depth += 1
        elif SHELL_JS[j] == "}":
            depth -= 1
            if depth == 0:
                return SHELL_JS[start:j + 1]
    raise AssertionError(name)


def test_a_new_step_goes_in_one_array_and_nothing_reads_a_step_by_name():
    """GUARD, and it is the card's own requirement: "Build the frame so a
    step can be added IN FRONT — a 'step 0: update your inventory' is
    coming and is explicitly NOT this card." A frame that read a step by
    name or by index would make that a rewrite instead of one line."""
    frame = SHELL_JS[
        SHELL_JS.index('// ---------- "Before you shop" ----------'):
        SHELL_JS.index("  function groSortRowHtml(")
    ]
    code = "\n".join(
        line for line in frame.splitlines()
        if not line.strip().startswith("//")
    )
    for named in ("'regulars'", '"regulars"', "'spices'", '"spices"', "BEFORE_SHOP_STEPS[0]"):
        assert named not in code, f"the frame reads a step by {named} — a step cannot be added in front"
    assert "BEFORE_SHOP_STEPS.filter(" in code, "the live steps are derived, never listed"


def test_the_frame_ships_with_no_steps_so_slice_1_says_only_what_is_true():
    """GUARD. BEFORE_SHOP_STEPS is empty until slice 2, which is why the
    dock reads "Sort the list (N)" today. If this goes red because a step
    landed, the dock's label changed with it and that is the point — come
    and invert it."""
    assert "var BEFORE_SHOP_STEPS = [];" in SHELL_JS


def test_the_pass_reuses_the_apps_one_progress_bar():
    """GUARD, pinned by the "a second bar" mutation. .cook-progress is
    Cook's step bar and is already exactly this; a Grocery copy of the same
    four rules is how two screens end up disagreeing about what a step bar
    looks like."""
    assert "return cookProgressHtml(steps.length, beforeShopIndex(data));" in _fn("beforeShopProgressHtml")
    for invented in (".gro-bs-bar", ".gro-bs-pip"):
        assert invented not in SHELL_CSS, f"{invented} is a second progress bar"
    assert ".cook-progress-seg.is-done { background: var(--apricot); }" in SHELL_CSS


def test_the_passs_two_shared_rules_are_tokens_only():
    """GUARD on hard rule 9."""
    block = SHELL_CSS[
        SHELL_CSS.index('/* ---------- "Before you shop"'):
        SHELL_CSS.index("/* ---------- The crumb:")
    ]
    import re
    assert not re.search(r"#[0-9a-fA-F]{3,8}\b", block.replace("390x844", "")), "tokens only"
    assert "--ink-secondary" in block and "--ink)" in block


def test_the_dock_delegates_rather_than_keeping_a_second_copy_of_the_rule():
    """GUARD. One function decides what Shop's one primary says, so the
    label, the count and "has this been run" can never disagree."""
    dock = _fn("groDockHtml")
    assert "return beforeShopDockHtml(data) + groAddButtonHtml();" in dock
    assert "if (step === 'beforeshop') return beforeShopStepDockHtml(data);" in dock
    assert "Sort the list" not in dock, "the wording lives in beforeShopDockHtml, once"


def test_only_the_pass_itself_records_having_been_through_the_pass():
    """GUARD, pinned by the "goto-sort finishes the pass" mutation — which
    is what the first cut did, and it quietly meant a household who sorted
    from the list's own row never got asked the regulars question that
    week."""
    handlers = SHELL_JS[SHELL_JS.index("case 'goto-sort':"):]
    handlers = handlers[:handlers.index("case 'sortall-pick':")]
    assert "beforeShopFinish()" not in handlers.split("case 'bs-next':")[0], \
        "sorting is not an answer to a question nobody asked"
    assert handlers.count("beforeShopFinish();") == 1, "the pass's last step, and nothing else"
