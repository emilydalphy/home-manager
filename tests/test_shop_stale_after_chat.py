"""
A week-tagged chat change refreshes the Shop tab.

Loop Board bug, Phase 1: "A chat change that alters the shopping list leaves
the Shop tab stale until you reload." `refreshStaleTabsFromActions`
(static/shell.js) is the one thing standing between CLAUDE.md's "tab panels
build once per page load" gotcha and a household shopping from a list the
app already knows is out of date — and its `week` branch called
`loadWeekMenu` and `refreshTonightFromPlan` and nothing else.

Measured before the fix, by driving the real function under node:

    {tab:'week'}, Meals built     -> loadWeekMenu, refreshTonightFromPlan
    {tab:'week'}, Meals not built -> refreshTonightFromPlan, refreshDishIndex
    {tab:'grocery'}               -> refreshGroceryPanel, refreshTodayMoves

The `grocery` branch had it right the whole time, one branch below.

Found by the reviewer of overnight/tonight-night-off: the tonight sheet's OWN
night-off tap calls refreshGroceryPanel() explicitly, with a comment saying
why, while the identical change made from chat got nothing. The client
demonstrably knew the list had changed and was wired for one entry point.

HOW THIS FILE IS BUILT, and why it is not a source-marker file. The defect is
a MISSING CALL, which is exactly what reading the source for a name cannot
see (CLAUDE.md, 2026-09-13). So every behavioural test here RUNS shell.js's
own functions under node (tests/nodeharness.py): section 3 drives
`refreshStaleTabsFromActions` against stubs and counts what it called, and
section 4 runs the real `refreshGroceryPanel` -> `loadGrocery` ->
`renderGrocery` against a stubbed fetch and a small fake DOM.

Every test says in its own docstring whether it is a CATCH (red against the
unmodified static/shell.js) or a GUARD (green either way — a promise that
something this change could have broken did not move).
"""
from __future__ import annotations

import datetime
import json
import shutil
from pathlib import Path

import nodeharness
import pytest

from app import tools
from app.main import _WEEK_TOOLS, _categorize_tool
from conftest import household_date, household_today

REPO = Path(__file__).resolve().parents[1]
SHELL_JS = (REPO / "static" / "shell.js").read_text(encoding="utf-8")

_needs_node = pytest.mark.skipif(
    shutil.which("node") is None,
    reason="node is needed to execute the shell's own functions",
)

# The week tools this file drives end to end. Each one is asserted to still
# be IN _WEEK_TOOLS below rather than trusted from here — the tab is the
# contract between the two halves, and a tool quietly re-tagged `grocery`
# would make these tests pass while covering nothing.
WEEK_TOOLS_UNDER_TEST = [
    "approve_weekly_plan",
    "swap_meal_in_plan",
    "discard_draft_plan",
    "take_the_night_off",
]


# ==========================================================================
# Seeding — a real week, on the household's own clock
# ==========================================================================

def _week_start() -> str:
    """The Monday of the household's week. `household_today`, never
    date.today(): the app reads households.timezone and the test process
    reads TZ, and those are different days for four hours of every UTC day
    (CLAUDE.md's dated-test rule)."""
    today = household_today()
    return (today - datetime.timedelta(days=today.weekday())).isoformat()


WEEK = _week_start()
DAYS = tools._week_dates(WEEK)


def _recipe(name: str, own: str) -> None:
    """One shared ingredient and one of its own, so a change to a single
    night both trims a merged line and removes a line outright."""
    tools.add_recipe(
        name,
        ingredients=[
            {"item": "Rice", "qty": "1 cup", "category": "pantry"},
            {"item": own, "qty": "1 bag", "category": "produce"},
        ],
        prep_time_minutes=5,
        cook_time_minutes=20,
        default_servings=4,
    )


def _lines() -> list[tuple[str, str]]:
    return sorted((r["item"], r["quantity"]) for r in tools.list_grocery_list())


def _seed_week(approve: bool = True) -> int:
    plan = tools.create_weekly_plan(WEEK)["weekly_plan_id"]
    for i, day in enumerate(DAYS):
        name = "Dish %d" % i
        _recipe(name, "Greens %d" % i)
        tools.plan_meal(day, name, slot="dinner", weekly_plan_id=plan)
    if approve:
        tools.approve_weekly_plan(plan)
    return plan


# ==========================================================================
# 1. The premise: these tools really do change the shopping list
# ==========================================================================
# GUARDS, all of them — green with or without the shell change. They are
# here because the bug's whole claim is "a week-tagged change alters the
# list", and a fix for a claim nobody measured is a fix for a story. Run
# against the real tools on a throwaway database, never a stub.

def test_approve_weekly_plan_writes_the_whole_list():
    """GUARD (green either way). Approval is the one thing that puts a
    week's ingredients on the list at all — nothing before it does."""
    plan = _seed_week(approve=False)
    before = _lines()
    tools.approve_weekly_plan(plan)
    after = _lines()
    assert before == [], "a draft must not have reached the list"
    assert len(after) == 8, after
    assert ("Rice", "7 cups") in after


def test_swap_meal_in_plan_moves_lines_between_dishes():
    """GUARD (green either way). The outgoing dish's own line goes and the
    new dish's arrives — the list is a different list afterwards."""
    plan = _seed_week()
    before = _lines()
    _recipe("Something Else", "Kale")
    tools.swap_meal_in_plan(plan, DAYS[2], "Something Else", slot="dinner", old_meal="Dish 2")
    after = _lines()
    assert ("Greens 2", "1 bag") in before and ("Greens 2", "1 bag") not in after
    assert ("Kale", "1 bag") not in before and ("Kale", "1 bag") in after


def test_take_the_night_off_puts_back_what_the_dropped_dinner_had_bought():
    """GUARD (green either way). A called-off night reverses whatever is
    still `needed` — its own line goes and the merged one is recomputed
    down, which is the case the tonight sheet's own comment describes."""
    _seed_week()
    today = household_date(0)
    index = DAYS.index(today) if today in DAYS else None
    if index is None:
        pytest.skip("today is outside the seeded week — nothing to call off")
    before = _lines()
    out = tools.tonight_night_off(today)
    assert out.get("status") == "night_off", out
    after = _lines()
    assert ("Greens %d" % index, "1 bag") in before
    assert ("Greens %d" % index, "1 bag") not in after
    assert ("Rice", "7 cups") in before and ("Rice", "6 cups") in after


def test_discarding_a_draft_does_NOT_change_the_list_and_the_card_was_wrong():
    """
    GUARD, and a correction to the ticket rather than support for it.

    The card lists `discard_draft_plan` among the week tools that
    "demonstrably change the list". Measured here, twice — a lone draft and
    a draft sitting over an approved week — it changes nothing, and it
    cannot: a draft never reaches the shopping list (that is the whole point
    of the 2026-09-13 draft-waits-for-approval work, and it is what the drop
    dialog's own second line says out loud, "Nothing from it is on your
    list").

    So the refresh it now triggers is one wasted request on this tool, which
    is the stated cost of refreshing unconditionally — see the branch's own
    comment. Written down rather than quietly dropped from the list: the
    next reader of that comment should not have to re-measure it.
    """
    plan = _seed_week()
    approved = _lines()
    draft = tools.create_weekly_plan(WEEK)["weekly_plan_id"]
    _recipe("Draft Dish", "Draft Greens")
    tools.plan_meal(DAYS[0], "Draft Dish", slot="dinner", weekly_plan_id=draft)
    assert _lines() == approved, "a draft's meals must not reach the list"
    tools.discard_draft_plan(draft)
    assert _lines() == approved
    assert plan  # the approved week is untouched underneath


# ==========================================================================
# 2. The tab is the contract
# ==========================================================================

@pytest.mark.parametrize("tool", WEEK_TOOLS_UNDER_TEST)
def test_each_tool_is_still_tagged_week(tool):
    """GUARD. _categorize_tool gives each action exactly ONE tab, so
    re-tagging any of these `grocery` would swap this bug for a stale Plan
    tab. Asked of the app rather than hard-coded, so a re-tag fails here."""
    assert tool in _WEEK_TOOLS
    assert _categorize_tool(tool)[1] == "week"


# ==========================================================================
# 3. ...so the Shop tab refreshes. The catches.
# ==========================================================================

_REFRESH_STUB = """
var CALLS = [];
var panels = { today: { dataset: { built: '1' } }, week: { dataset: { built: '1' } } };
function refreshHolding() { CALLS.push('holding'); }
function loadWeekMenu() { CALLS.push('weekmenu'); }
// The real one calls refreshTodayMoves itself (both are no-ops on an
// unbuilt Today), so the stub records both — otherwise a test asking
// "does the week branch need its own refreshTodayMoves?" would be asking
// about a call this stub had swallowed.
function refreshTonightFromPlan() { CALLS.push('tonight'); refreshTodayMoves(); }
function refreshDishIndex() { CALLS.push('dishindex'); }
function refreshKitchenPanel() { CALLS.push('kitchen'); }
function refreshTodayMoves() { CALLS.push('todaymoves'); }
function hrefSheetKey(h) { return h === '/memory' ? 'memory' : null; }
function prefsInvalidate() { CALLS.push('prefs'); }
function refreshGroceryPanel() { CALLS.push('grocery'); }
function loadNeedsYou() { CALLS.push('needsyou'); }
function loadTodayMoves() { CALLS.push('todaypanel'); }
function loadChores() { CALLS.push('chores-now'); }
function loadPlanChores() { CALLS.push('chores-plan'); }
"""


def _function(name: str) -> str:
    """The body of one top-level function in shell.js, to its closing brace."""
    marker = "  async function %s(" % name
    if marker not in SHELL_JS:
        marker = "  function %s(" % name
    start = SHELL_JS.index(marker)
    end = SHELL_JS.index("\n  }\n", start)
    return SHELL_JS[start:end] + "\n  }\n"


def _refresh(actions: list[dict], week_built: bool = True) -> list[str]:
    """Run the real refreshStaleTabsFromActions and report what it called."""
    script = (
        _REFRESH_STUB
        + _function("refreshStaleTabsFromActions")
        + ("\n" if week_built else "\ndelete panels.week.dataset.built;\n")
        + "refreshStaleTabsFromActions(%s);\n" % json.dumps(actions)
        + "console.log(JSON.stringify(CALLS));\n"
    )
    res = nodeharness.run_node(script, timeout=30)
    assert res.returncode == 0, "node failed: %s" % res.stderr
    return json.loads(res.stdout.strip().splitlines()[-1])


@_needs_node
@pytest.mark.parametrize("tool", WEEK_TOOLS_UNDER_TEST)
def test_the_shop_tab_refreshes_for_each_week_tool(tool):
    """
    CATCH (red against the unmodified shell.js, for all four).

    One test per tool, and the tab each action carries is asked of
    _categorize_tool rather than written here — so this is the real chain
    from the tool name the assistant called through to the refresh, not a
    hard-coded 'week' string agreeing with itself.
    """
    _category, tab, _href = _categorize_tool(tool)
    calls = _refresh([{"tab": tab, "kicker": "Week updated", "change": tool}])
    assert "grocery" in calls, (
        "a %s said in chat leaves Shop showing the list as it read at build "
        "time: %r" % (tool, calls)
    )


@_needs_node
def test_it_refreshes_shop_even_when_meals_has_never_been_opened():
    """
    CATCH (red against the unmodified shell.js).

    The two arms of the `week` branch split on whether MEALS was built, and
    Shop is built independently of it — somebody can have opened Shop and
    never Plan. This is the same lesson the `today` branch was taught on
    2026-09-12, when loadPlanChores was put outside its panels.today guard.
    """
    calls = _refresh([{"tab": "week"}], week_built=False)
    assert "grocery" in calls, calls
    # and the arm still does its own job
    assert "dishindex" in calls, calls


@_needs_node
def test_the_week_branch_does_not_ask_today_twice():
    """
    GUARD — green against the unmodified shell.js, because the duplicate it
    forbids is one the fix could have introduced and did not. (Said that way
    round deliberately: a test that reads as a catch and is not is the one
    kind of error this project keeps having to unpick.)

    The `grocery` branch calls refreshTodayMoves() beside its panel refresh,
    and the obvious move was to copy that pair wholesale. It would have been
    wrong: refreshTonightFromPlan already calls refreshTodayMoves, so the
    week branch reaches Today either way and a second call is two fetches
    for one answer (the same waste tests/test_kitchen_refresh_once.py exists
    for).
    """
    assert _refresh([{"tab": "week"}]).count("todaymoves") == 1
    assert _refresh([{"tab": "week"}], week_built=False).count("todaymoves") == 1
    assert "refreshTodayMoves();" in _function("refreshTonightFromPlan")


@_needs_node
def test_shop_is_refreshed_once_for_one_action():
    """
    CATCH, though a weak one, and it is worth saying which: it is red
    against the unmodified shell.js for the same one missing call as the
    four above, not for a second reason of its own. What it adds is the
    other direction — both arms carry the call, and an action must take
    exactly one of them, so a restructure that let both run would double
    every refresh.
    """
    assert _refresh([{"tab": "week"}]).count("grocery") == 1
    assert _refresh([{"tab": "week"}], week_built=False).count("grocery") == 1


@_needs_node
def test_a_turn_that_changed_nothing_about_the_week_leaves_shop_alone():
    """
    GUARD (green either way). The refresh is unconditional WITHIN the week
    branch, not unconditional full stop: a kitchen or memory action still
    must not reach Shop, or every "we finished the chicken" would refetch
    the list.
    """
    assert "grocery" not in _refresh([{"tab": "kitchen"}])
    assert "grocery" not in _refresh([{"href": "/memory"}])
    assert "grocery" not in _refresh([{"tab": "today"}])
    assert _refresh([]) == []


@_needs_node
def test_kitchen_goes_stale_in_the_same_arm_and_is_NOT_fixed_here():
    """
    CHARACTERISATION of a SEPARATE bug, found while measuring this one and
    deliberately left. Invert this test when it is fixed.

    `refreshKitchenPanel()` is called from inside `loadWeekMenu` — on
    purpose, so that the six things which reload the plan get the cook's tab
    for free (that function's own comment says so). But `loadWeekMenu` only
    runs in the arm where MEALS was built. So on a page view where somebody
    has opened Shop and Kitchen and never Plan, a week-tagged chat change
    refreshes Now, Shop and the dish index, and leaves KITCHEN — today's
    cooks, the rest of the week, cook mode — reading whatever it loaded at
    build time.

    Exactly the shape this ticket fixed for Shop, one branch over, and not
    this ticket's to widen: the fix is to hoist `refreshKitchenPanel()` out
    of `loadWeekMenu` the way the grocery refresh is hoisted here, which
    changes behaviour for every one of that function's callers.
    """
    assert "kitchen" not in _refresh([{"tab": "week"}], week_built=False)
    # And it is reached in the other arm only through loadWeekMenu, which is
    # what makes the two arms disagree.
    assert "refreshKitchenPanel();" in _function("loadWeekMenu")
    body = _function("refreshStaleTabsFromActions")
    week_arms = body[body.index("action.tab === 'week'"):body.index("action.tab === 'kitchen'")]
    assert "refreshKitchenPanel" not in week_arms


@_needs_node
def test_the_grocery_branch_still_refreshes_both_surfaces():
    """GUARD (green either way). The branch below this one is the model the
    fix was read off; it must not be disturbed by it."""
    calls = _refresh([{"tab": "grocery"}])
    assert calls == ["grocery", "todaymoves"], calls


@_needs_node
def test_two_actions_in_one_turn_each_do_their_own_work():
    """
    CATCH, and weak in the same way as the one above — on the unmodified
    shell.js it counts one refresh where it wants two, which is the same
    missing line again. Its own claim is that a turn which swaps a meal AND
    adds an item produces two cards, and the week one must not swallow the
    grocery one.
    """
    calls = _refresh([{"tab": "week"}, {"tab": "grocery"}])
    assert calls.count("grocery") == 2, calls
    assert "weekmenu" in calls


# ==========================================================================
# 4. What the refresh does to somebody already using the Shop tab
# ==========================================================================
# The acceptance criterion says the scroll position and any in-flight
# sorting state survive, and that VERIFYING it is the job rather than
# assuming it. So these run the real refreshGroceryPanel -> loadGrocery ->
# renderGrocery against a stubbed fetch and a fake DOM, and read the state
# back afterwards.
#
# All GUARDS — refreshGroceryPanel behaved this way before the fix. What
# changes is that this branch now reaches it, so "does reaching it hurt?" is
# a question the fix has to answer.


def _grocery_block() -> str:
    """The whole Grocery region, up to the hands-free voice code (which
    wants a SpeechRecognition engine). The same slice
    tests/test_grocery_fast_sort.py takes."""
    start = SHELL_JS.index("  var GRO_CATEGORY_LABELS = {")
    end = SHELL_JS.index("  // ---------- Hands-free voice ----------", start)
    return SHELL_JS[start:end]


def _band_identity() -> str:
    """shell.js's own BAND_IDENTITY line. groBandEyebrow reads it, and a
    stubbed copy would go on saying 'wordmark' after the real one moved."""
    line = "  var BAND_IDENTITY = "
    start = SHELL_JS.index(line)
    return SHELL_JS[start:SHELL_JS.index("\n", start) + 1]


_SHOP_STUB = """
function escapeHtml(s){return String(s == null ? '' : s)
  .replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');}
function setRootBand() {}
function bandDateLabel() { return 'Wednesday, Sep 17'; }
function emptyMomentHtml() { return '<div class="empty-moment"></div>'; }
function snwLink() { return ''; }
function showToast() {}
function activateTab() {}
var coachState = { householdId: 1 };
var scrollEl = { scrollTop: 0 };
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
// Enough of an element for renderGrocery to write into. It reads nothing
// back that these tests assert on — the claims here are about groceryState
// and the scroll, not about markup.
//
// WRITING TO innerHTML DROPS THE SCROLL TO 0, and that is the whole reason
// the scroll test has any teeth. A browser does this for real: replacing
// the content under a scroller with something shorter puts the scroll
// wherever it can, which for a list rebuilt from nothing is the top. A
// scrollTop that simply sat where it was put would make
// "the scroll survives" pass whether or not renderGrocery restores it —
// checked by mutation, and the first version of this harness did exactly
// that.
function el() {
  var node = { hidden: false, textContent: '',
    classList: { toggle: function () {}, add: function () {}, remove: function () {},
                 contains: function () { return false; } },
    querySelector: function () { return null; }, querySelectorAll: function () { return []; },
    addEventListener: function () {}, setAttribute: function () {},
    getAttribute: function () { return null; } };
  var html = '';
  Object.defineProperty(node, 'innerHTML', {
    get: function () { return html; },
    set: function (v) { html = v; if (scrollEl) scrollEl.scrollTop = 0; }
  });
  return node;
}
var GRO_NODES = {};
['#gro-back','#gro-head','#gro-band','#gro-title','#gro-sub','#gro-body','#gro-foot','#gro-dock']
  .forEach(function (s) { GRO_NODES[s] = el(); });
var document = { activeElement: null, createElement: function () { return el(); } };
var panels = { grocery: { dataset: { built: '1' },
  querySelector: function (s) { return GRO_NODES[s] || null; } } };
var FETCHED = [];
// The list the server hands back AFTER the chat change — two shops, one
// row each, plus one row nobody has sorted yet.
function fetch(url) {
  FETCHED.push(url);
  var body = {};
  if (url.indexOf('by-store') !== -1) {
    body = { stores: [
      { store: 'Loblaws', sections: [{ section: 'other', items: [
        { id: 1, item: 'Rice', quantity: '1 cup', store: 'Loblaws', store_decided: 1 }] }] },
      { store: 'Costco', sections: [{ section: 'other', items: [
        { id: 3, item: 'Oats', quantity: '1 bag', store: 'Costco', store_decided: 1 }] }] }
    ] };
    if (UNSORTED) {
      // The bucket is named 'Unassigned' and its ROWS carry store: '' —
      // what /api/grocery-list/by-store really sends, and what groUnsorted
      // looks for by name.
      body.stores.push({ store: 'Unassigned', sections: [{ section: 'other', items: [
        { id: 2, item: 'Kale', quantity: '1 bag', store: '', store_decided: 0 }] }] });
    }
  } else if (url.indexOf('/api/grocery-list?') !== -1) body = { sections: [] };
  else if (url.indexOf('carried') !== -1) body = { items: CARRIED };
  else if (url.indexOf('pre-shop') !== -1) body = { flags: [] };
  else if (url.indexOf('already-have') !== -1) body = { already_have: [], elsewhere: [] };
  else if (url.indexOf('staples') !== -1) body = { staples: [], sections: [] };
  else if (url.indexOf('spices') !== -1) body = { items: [], recently_bought: [] };
  return Promise.resolve({ ok: true, status: 200,
    json: function () { return Promise.resolve(body); } });
}
var UNSORTED = true;
var CARRIED = [];
"""


def _shop(body: str) -> dict:
    script = _SHOP_STUB + _band_identity() + _grocery_block() + body
    res = nodeharness.run_node(script, timeout=30)
    assert res.returncode == 0, "node failed: %s" % res.stderr
    return json.loads(res.stdout.strip().splitlines()[-1])


@_needs_node
def test_a_refresh_mid_trip_does_not_yank_the_shopper_out_of_the_shop():
    """
    GUARD. The worst thing this fix could do is end somebody's trip from a
    phone in another room. It doesn't: loadGrocery never touches
    groceryState.step or the trip snapshot, so the stop stays open, the
    stops already behind stay behind, and what came home this trip is still
    counted. All it changes is the list the stop is drawn from — which is
    the whole point.
    """
    out = _shop("""
groceryState.usualStores = ['Loblaws', 'Costco'];
groceryState.storesPromptDismissed = true;
groceryState.step = 'trip';
groceryState.tripStops = ['Loblaws', 'Costco'];
groceryState.tripIndex = 1;
groceryState.tripDone = { Loblaws: true };
groceryState.tripStartedAt = Date.now();
groceryState.tripTotal = 9;
groceryState.tripBought = 4;
groceryState.tripRestored = true;
scrollEl.scrollTop = 412;
refreshGroceryPanel();
setTimeout(function () {
  console.log(JSON.stringify({
    step: groceryState.step, stops: groceryState.tripStops,
    index: groceryState.tripIndex, done: groceryState.tripDone,
    bought: groceryState.tripBought, scroll: scrollEl.scrollTop,
    fetched: FETCHED.length
  }));
}, 60);
""")
    assert out["step"] == "trip"
    assert out["stops"] == ["Loblaws", "Costco"]
    assert out["index"] == 1
    assert out["done"] == {"Loblaws": True}
    assert out["bought"] == 4
    assert out["scroll"] == 412, "a refresh must not scroll the shopper's stop away"
    assert out["fetched"] > 0, "the harness never reached the network at all"


@_needs_node
def test_the_scroll_position_survives_the_refresh():
    """
    GUARD, and the one the 2026-09-02 entry asks to be matched: the native
    Grocery panel replaced an iframe whose src-reload threw the whole screen
    away, "scroll position and all". renderGrocery reads scrollTop before it
    writes and puts it back after.
    """
    out = _shop("""
groceryState.usualStores = ['Loblaws', 'Costco'];
groceryState.storesPromptDismissed = true;
scrollEl.scrollTop = 733;
refreshGroceryPanel();
setTimeout(function () {
  console.log(JSON.stringify({ scroll: scrollEl.scrollTop, step: groceryState.step }));
}, 60);
""")
    assert out["scroll"] == 733
    assert out["step"] == "list"


@_needs_node
def test_a_refresh_mid_sort_leaves_the_sorting_screen_where_it_was():
    """
    GUARD. SORT and SORT ALL stay up, and so does the row whose "Use
    something else" field is open.

    Note what this does and does not measure: the STEP and the open row
    survive, which is what is asserted. Whether the half-typed text inside
    that field survives is groCaptureSubstInput/groRestoreSubstInput's job
    and is READ from shell.js rather than measured here — the fake DOM
    below answers null to every querySelector, so those two run as no-ops
    in this harness. Said rather than implied, because a claim nobody
    measured is the kind this log keeps having to unpick.

    Worth knowing while reading this: there are no STAGED sort picks to lose
    any more. SORT ALL staged every pick behind a save button when it shipped
    (2026-09-09) and stopped on 2026-09-13 — a tap WRITES now and the row
    leaves the screen, which is why groSortAllRender exists. The ticket's
    "staged sort picks" describes a screen that no longer works that way.
    """
    out = _shop("""
groceryState.usualStores = ['Loblaws', 'Costco'];
groceryState.storesPromptDismissed = true;
groceryState.step = 'sortall';
groceryState.substOpenId = '2';
groceryState.sortTotal = 3;
refreshGroceryPanel();
setTimeout(function () {
  console.log(JSON.stringify({
    step: groceryState.step, subst: groceryState.substOpenId,
    total: groceryState.sortTotal
  }));
}, 60);
""")
    assert out["step"] == "sortall"
    assert out["subst"] == "2"
    assert out["total"] == 3


@_needs_node
def test_sorting_folds_back_to_the_list_when_the_change_left_nothing_to_sort():
    """
    CHARACTERISATION (green either way), so the one screen change a chat
    refresh really can make is written down rather than found later.

    If the week-tagged change removed the very rows somebody was sorting,
    renderGrocery's own fallback puts them on LIST — "a step that stopped
    making sense under its own feet falls back to the root rather than
    rendering a screen about nothing". That rule predates this fix and is
    right; the fix only makes it reachable from chat as well as from the
    shopper's own taps.
    """
    out = _shop("""
groceryState.usualStores = ['Loblaws', 'Costco'];
groceryState.storesPromptDismissed = true;
groceryState.step = 'sort';
UNSORTED = false;   // the chat change sorted, bought or dropped the last one
refreshGroceryPanel();
setTimeout(function () { console.log(JSON.stringify({ step: groceryState.step })); }, 60);
""")
    assert out["step"] == "list"


@_needs_node
def test_a_deferred_leftovers_question_is_asked_again_and_that_is_the_cost():
    """
    CHARACTERISATION (green either way), and the one behaviour cost of
    routing the week branch through refreshGroceryPanel rather than straight
    to loadGrocery.

    refreshGroceryPanel clears carryDeferred, so a household that said
    "later" to last week's keep-or-drop gets asked again. That is correct for
    approve_weekly_plan — the function's own docstring says a refill is a new
    list, and an approval IS a refill — and it is what the Approve button
    already does through refreshGrocerySurfaces. It is over-eager for a week
    tool that only moved a night around. Kept rather than special-cased,
    because approval is the week tool that matters most here and because
    two doors into the same refresh that disagree about this is worse than
    one door that is occasionally keen.

    It can only happen on LIST: groMaybeCarryFirst returns early on any other
    step, which is what keeps it away from a trip.
    """
    out = _shop("""
groceryState.usualStores = ['Loblaws', 'Costco'];
groceryState.storesPromptDismissed = true;
CARRIED = [{ id: 9, item: 'Spinach', quantity: '1 bag' }];
groceryState.carried = CARRIED;
groceryState.carryDeferred = true;
refreshGroceryPanel();
setTimeout(function () {
  console.log(JSON.stringify({ step: groceryState.step, deferred: groceryState.carryDeferred }));
}, 60);
""")
    assert out["deferred"] is False
    assert out["step"] == "carry"


@_needs_node
def test_refreshing_a_shop_tab_that_was_never_opened_costs_nothing():
    """
    GUARD. The call is unconditional inside the week branch, so it runs for
    every household — including the many who never open Shop in a page view.
    An unbuilt panel costs nothing, which is what makes "refresh
    unconditionally" cheap enough to be the right answer.

    It pins the PROPERTY, not one line: there are two guards behind it
    (refreshGroceryPanel's `if (groIsBuilt())` and loadGrocery's own
    `if (!panel || !panel.dataset.built) return;`), so removing either one
    alone leaves this green. Measured — removing both makes it fail with
    eight requests where it wants none. Defence in depth is the right shape
    here and the test says what it can honestly see.
    """
    out = _shop("""
delete panels.grocery.dataset.built;
refreshGroceryPanel();
setTimeout(function () {
  console.log(JSON.stringify({ fetched: FETCHED.length, data: groceryState.data }));
}, 60);
""")
    assert out["fetched"] == 0
    assert out["data"] is None
