"""
DESIGN_SYSTEM.md §2b S10 (Emily, 2026-09-13): a decision is saved on
purpose, and the app says so.

The audit that day (Shaping the Draft canvas, "Save audit" board) found
the decisions that saved in silence: Swap · I'll pick, anything changed
through the chat, every What we know edit, a chore tick on Now and on
Plan | Chores, Cook's prep tick / attention answers / usage log / "Mark
not cooked", and Plan the week's who's-here, holiday and away saves.
These tests run the shell's own handlers under node (tests/nodeharness.py,
the house standard) and ask each one the S10 question: did it say so?
Where a handler is too entangled to slice (the chat send), the test reads
the source for the exact line instead, and says so.

**Reworded 2026-09-23** (copy sweep finding 1, Emily 2026-09-22: *"I
don't like the AI written style"*). The pop-up used to read "Changes
saved" — one sentence answering twenty-seven different actions, every
one of which knew the name of the thing it had just changed. It now says
`<thing> was <verbed>`: "Carrots was ticked off", "Your rhythm was
saved". So these tests ask a sharper question than they used to: not
only *did it say so*, but *did it say WHICH*. The seam is unchanged —
every save toast still goes through one `toastSaved`, and its wording
through `savedLine` / `savedCount`.

One thing deliberately NOT changed here: `DESIGN_SYSTEM.md`. S10 is
marked Tier 2 — Emily's decision, not an agent's (§9 Governance) — and
she has already said yes to it separately: branch
`design-system-plain-copy` carries the matching rewording of §2b S10 and
§7. This branch is the code half only, so the assertion below checks the
rule is still written down and deliberately says nothing about its exact
wording — the two branches land in either order without a test failing
over which one got there first.
"""
from __future__ import annotations

import json
import os
import shutil

import pytest

import nodeharness
import shop_harness

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SHELL_JS = open(os.path.join(ROOT, "static", "shell.js"), encoding="utf-8").read()
PLAN_WEEK = open(os.path.join(ROOT, "static", "plan-week.html"), encoding="utf-8").read()
DESIGN = open(os.path.join(ROOT, "DESIGN_SYSTEM.md"), encoding="utf-8").read()

_needs_node = pytest.mark.skipif(
    shutil.which("node") is None, reason="node is needed to execute the shell's own handlers"
)


def _function(name: str) -> str:
    marker = "  async function %s(" % name
    if marker not in SHELL_JS:
        marker = "  function %s(" % name
    start = SHELL_JS.index(marker)
    end = SHELL_JS.index("\n  }\n", start)
    return SHELL_JS[start:end] + "\n  }\n"


def _node(script: str):
    res = nodeharness.run_node(script, timeout=30)
    assert res.returncode == 0, f"node failed: {res.stderr}"
    return json.loads(res.stdout.strip().splitlines()[-1])


# The helper every site goes through, sliced out of the shell rather than
# retyped so a rewording there is a rewording here (shop_harness.toast_core).
_TOAST_CORE = shop_harness.toast_core()

_STUBS = """
var TOASTS = [];
function showToast(m, action, hold) { TOASTS.push({ msg: m, action: action || null, hold: hold || null }); }
console.warn = function () {};
var FAIL_NEXT = false;
var POSTS = [];
var REPLY = { ok: true };
function fetch(url, opts) {
  POSTS.push({ url: url, body: opts && opts.body ? JSON.parse(opts.body) : null });
  if (FAIL_NEXT) { FAIL_NEXT = false; return Promise.resolve({ ok: false }); }
  return Promise.resolve({ ok: true, json: function () { return Promise.resolve(REPLY); } });
}
function tick() { return Promise.resolve().then(function () {}).then(function () {}).then(function () {}).then(function () {}); }
"""


def test_the_helper_is_defined_once_and_every_site_goes_through_it():
    assert SHELL_JS.count("var SAVED_PLAIN = 'Saved';") == 1
    assert SHELL_JS.count("function toastSaved(") == 1
    assert SHELL_JS.count("function savedLine(") == 1
    # Nobody spells a save line out by hand — one voice, one builder.
    assert "showToast('Changes saved'" not in SHELL_JS
    assert "'Changes saved'" not in SHELL_JS
    assert SHELL_JS.count("toastSaved(") >= 10


def test_almost_every_save_toast_names_what_it_saved():
    """The point of the rewrite. Two callers pass nothing on purpose and
    get the plain word: a chat turn (its action cards under the reply
    already name what changed) and the freezer step answered "nothing's
    frozen". Chores' two ticks are the third and fourth — Chores is
    paused (Emily, 2026-09-18) and was left exactly as it was, so they
    fall through to the same plain word rather than being reworded."""
    bare = SHELL_JS.count("toastSaved();")
    assert bare == 4, "a nameless save toast needs a reason — see this test"
    assert SHELL_JS.count("toastSaved(savedLine(") >= 10


def test_a_long_name_is_cut_to_one_breath_rather_than_dropped():
    """A held thing is a whole sentence in the household's own words."""
    script = _STUBS + _TOAST_CORE + """
var long = 'Ask Mel whether the cottage weekend is still happening in October';
console.log(JSON.stringify({
  plain: savedLine('', 'ticked off'),
  named: savedLine('Carrots', 'ticked off'),
  many: savedCount(2, 'put back'),
  one: savedCount(1, 'put back'),
  cut: savedLine(long, 'ticked off')
}));
"""
    out = _node(script)
    assert out["plain"] == "Saved"
    assert out["named"] == "Carrots was ticked off"
    assert out["many"] == "2 things were put back"
    assert out["one"] == "1 thing was put back"
    assert out["cut"].endswith("… was ticked off")
    assert len(out["cut"]) < len("Ask Mel whether the cottage weekend is still happening in October")
    assert out["cut"].startswith("Ask Mel whether the cottage")


def test_the_rule_is_written_down_where_the_others_are():
    assert "S10 · A decision is saved on purpose, and the app says so." in DESIGN
    # The words S10 quotes are not asserted here on purpose — see this
    # file's docstring. The rule is what this test guards.
    assert "Save (or Done)" in DESIGN


# ---------- What we know ----------

@_needs_node
def test_a_what_we_know_edit_names_the_section_and_a_failed_one_does_not():
    script = _STUBS + _TOAST_CORE + """
var prefsState = { memory: { a: 1 }, open: false };
var wwkState = { facts: [], seq: 0 };
var WWK_SECTIONS = [{ key: 'taste', title: 'How you eat' }];
var FLASHED = [];
function wwkRenderHead() {}
function wwkRenderSection() {}
function renderPrefsRows() {}
function wwkFlashSaved(k) { FLASHED.push(k); }
""" + _function("wwkSection") + _function("wwkCommit") + """
(async function () {
  var ok = await wwkCommit('taste', function () { prefsState.memory.a = 2; },
    function () { return Promise.resolve({ a: 2 }); }, function () {});
  var firstToasts = TOASTS.slice();
  TOASTS.length = 0;
  var bad = await wwkCommit('taste', function () { prefsState.memory.a = 3; },
    function () { return Promise.reject(new Error('down')); }, function () {});
  console.log(JSON.stringify({ ok: ok, bad: bad, first: firstToasts, second: TOASTS, flashed: FLASHED,
    memory: prefsState.memory }));
})();
"""
    out = _node(script)
    assert out["ok"] is True
    # The section by name — not "Changes saved", and not the key either.
    assert [t["msg"] for t in out["first"]] == ["How you eat was saved"]
    assert out["flashed"] == ["taste"]  # the section's own flash still runs
    assert out["bad"] is False
    assert [t["msg"] for t in out["second"]] == ["That didn’t save. Try it again."]
    assert out["memory"] == {"a": 2}  # the failed edit was put back


# ---------- chore ticks ----------
# Chores is PAUSED (Emily, 2026-09-18), so its two ticks were left exactly
# as they were and now get the plain word from the shared helper. That is
# the smallest possible change to a paused screen — the alternative was
# rewording Chores, which is the thing not to do.

@_needs_node
def test_a_chore_tick_on_now_still_says_it_saved_and_a_failed_one_says_so_too():
    script = _STUBS + _TOAST_CORE + """
var panels = {};
var RENDERS = 0;
function renderChores() { RENDERS += 1; }
function loadPlanChores() {}
""" + _function("toggleChore") + """
(async function () {
  var chores = [{ id: 7, status: 'pending' }];
  var row = { dataset: { id: '7' } };
  await toggleChore({}, row, chores);
  var after = { status: chores[0].status, toasts: TOASTS.slice() };
  TOASTS.length = 0;
  FAIL_NEXT = true;
  await toggleChore({}, row, chores);
  console.log(JSON.stringify({ first: after, second: { status: chores[0].status, toasts: TOASTS }, posts: POSTS }));
})();
"""
    out = _node(script)
    assert out["first"]["status"] == "done"
    assert [t["msg"] for t in out["first"]["toasts"]] == ["Saved"]
    # The failed un-tick rolls back to done and, since 2026-09-13, says so.
    assert out["second"]["status"] == "done"
    assert [t["msg"] for t in out["second"]["toasts"]] == ["That didn’t save. Try it again in a moment."]
    assert out["posts"][0]["url"] == "/api/chores/7/status"


@_needs_node
def test_a_chore_tick_on_plan_still_says_it_saved():
    script = _STUBS + _TOAST_CORE + """
var panels = {};
var weekState = { step: 'chores', chores: { chores: [{ id: 3, status: 'pending' }] } };
function renderMealsStep() {}
function loadChores() {}
""" + _function("togglePlanChore") + """
(async function () {
  await togglePlanChore({}, 3);
  console.log(JSON.stringify({ status: weekState.chores.chores[0].status, toasts: TOASTS }));
})();
"""
    out = _node(script)
    assert out["status"] == "done"
    assert [t["msg"] for t in out["toasts"]] == ["Saved"]


@_needs_node
def test_a_chore_undo_says_put_back():
    """Chores keeps its own "Put back." — paused, and left alone."""
    script = _STUBS + _TOAST_CORE + """
var REFRESHED = 0;
""" + _function("runChoreUndo") + """
(async function () {
  await runChoreUndo({ refresh: function () { REFRESHED += 1; }, redraw: function () {} }, 5, 'status', { status: 'pending' });
  console.log(JSON.stringify({ toasts: TOASTS, refreshed: REFRESHED }));
})();
"""
    out = _node(script)
    assert [t["msg"] for t in out["toasts"]] == ["Put back."]
    assert out["refreshed"] == 1


# ---------- Swap · I'll pick ----------

def _swap_region() -> str:
    """The swap's state and its two handlers, nothing that draws."""
    return (
        "var swapState = null;\nvar swapUndoTimer = null;\nvar SWAP_UNDO_MS = 8000;\n"
        "var SWAP_TROUBLE = 'That didn’t work just now — nothing changed.';\n"
        + _function("swapStateFor") + _function("clearSwapUndoTimer") + _function("weekStartForSwap")
        + _function("mealDisplayName")
        + _function("runSwapInPlace") + _function("runSwapUndo")
    )


@_needs_node
def test_a_swap_names_the_dish_with_an_undo_and_the_undo_names_it_back():
    region = _swap_region()
    script = _STUBS + _TOAST_CORE + """
function escapeHtml(s) { return String(s == null ? '' : s); }
function daySlotEntry(day, slot) { return day[slot]; }
function dayName(iso, opts) { return 'Tuesday'; }
var weekState = { data: { week_start_date: '2026-09-14' } };
function renderMealsStep() {}
function spliceSwappedDay() {}
function loadWeekMenu() { return Promise.resolve(); }
var TIMERS = [];
var setTimeout = function (fn, ms) { TIMERS.push(ms); return 1; };
var clearTimeout = function () {};
""" + region + """
(async function () {
  var day = { date: '2026-09-15', dinner: { entry_id: 42, state: 'planned', title: 'Turkey burgers' } };
  var after = { date: '2026-09-15', dinner: { entry_id: 42, state: 'planned', title: 'Beef chili' } };
  REPLY = { status: 'swapped', reason: 'Beef instead of turkey', day: after, avoid: ['Turkey burgers'] };
  await runSwapInPlace({}, day, 'dinner');
  var afterSwap = TOASTS.slice();
  var undoAction = afterSwap[0] && afterSwap[0].action;
  TOASTS.length = 0;
  REPLY = { day: day };
  // The pop-up's Undo is the card's Undo: same handler, same request.
  await undoAction.onClick();
  await tick();
  console.log(JSON.stringify({ swap: afterSwap.map(function (t) { return { msg: t.msg, label: t.action && t.action.label, hold: t.hold }; }),
    undo: TOASTS.map(function (t) { return t.msg; }), posts: POSTS.map(function (p) { return p.url; }) }));
})();
"""
    out = _node(script)
    # The dish that ARRIVED, not the one that left — "swapped in" says
    # which way round it went.
    assert out["swap"] == [{"msg": "Beef chili was swapped in", "label": "Undo", "hold": 8000}]
    assert out["undo"] == ["Turkey burgers is back on Tuesday."]
    assert out["posts"] == ["/api/week/2026-09-14/swap-in-place", "/api/week/2026-09-14/swap-undo"]


@_needs_node
def test_a_refused_swap_does_not_claim_it_saved():
    region = _swap_region()
    script = _STUBS + _TOAST_CORE + """
function escapeHtml(s) { return String(s == null ? '' : s); }
function daySlotEntry(day, slot) { return day[slot]; }
function dayName(iso, opts) { return 'Tuesday'; }
var weekState = { data: { week_start_date: '2026-09-14' } };
function renderMealsStep() {}
function spliceSwappedDay() {}
function loadWeekMenu() { return Promise.resolve(); }
var setTimeout = function () { return 1; };
var clearTimeout = function () {};
""" + region + """
(async function () {
  var day = { date: '2026-09-15', dinner: { entry_id: 42, state: 'planned' } };
  REPLY = { status: 'refused', message: 'Nothing I can find gets round the peanut allergy — the plan is as it was.' };
  await runSwapInPlace({}, day, 'dinner');
  console.log(JSON.stringify({ toasts: TOASTS, said: swapState && swapState.message }));
})();
"""
    out = _node(script)
    assert out["toasts"] == []
    assert "as it was" in out["said"]


# ---------- Cook ----------

@_needs_node
def test_cooks_smaller_decisions_name_what_they_changed():
    script = _STUBS + _TOAST_CORE + """
var cookState = { attention: [{ id: 9, detail: { ingredient: 'Chickpeas' } }] };
function renderCookFrom() {}
function renderCook() {}
function refreshPlanSurfacesAfterCook() {}
function refreshCookAttention() {}
function toastMealLogged() { TOASTS.push({ msg: 'LOGGED' }); }
function cookPost(url, body) { POSTS.push({ url: url, body: body }); return Promise.resolve({ items: [] }); }
function el(attrs) { return { disabled: false, getAttribute: function (n) { return attrs[n] === undefined ? null : attrs[n]; } }; }
var document = { querySelector: function () { return { value: '2 cups' }; } };
""" + _function("cookNotCookedLine") + _function("cookAttentionName") \
        + _function("cookCheckPrep") + _function("cookResolveAttention") + _function("cookLogUsage") \
        + _function("cookFocusCheckMeal") + _function("cookCheckMeal") + """
(async function () {
  await cookCheckPrep(el({ 'data-prep-id': '4', 'data-next': 'done', 'data-name': 'Defrost the chicken' }));
  await cookResolveAttention(el({ 'data-attn-id': '9', 'data-status': 'resolved' }));
  cookState.attention = [{ id: 9, detail: { ingredient: 'Chickpeas' } }];
  await cookLogUsage(el({ 'data-attn-id': '9' }));
  await cookFocusCheckMeal(el({ 'data-entry-id': '12', 'data-next': 'pending', 'data-name': 'Chicken Skewers' }));
  // The Cook root's own row toggle, un-cooking — the same decision from
  // the other screen (found by the branch's verifier, 2026-09-13).
  await cookCheckMeal(el({ 'data-entry-id': '12', 'data-next': 'pending', 'data-name': 'Chicken Skewers',
    'aria-label': 'Mark not cooked' }));
  var uncook = TOASTS.map(function (t) { return t.msg; });
  TOASTS.length = 0;
  await cookFocusCheckMeal(el({ 'data-entry-id': '12', 'data-next': 'done', 'data-name': 'Chicken Skewers' }));
  await cookCheckMeal(el({ 'data-entry-id': '12', 'data-next': 'done', 'data-name': 'Chicken Skewers',
    'aria-label': 'Mark cooked' }));
  console.log(JSON.stringify({ five: uncook, cooked: TOASTS.map(function (t) { return t.msg; }) }));
})();
"""
    out = _node(script)
    assert out["five"] == [
        "Defrost the chicken was ticked off",
        "Chickpeas was marked handled",
        "Chickpeas was logged",
        "Chicken Skewers isn’t cooked yet",
        "Chicken Skewers isn’t cooked yet",
    ]
    # "Mark it cooked" keeps its own, richer line on both screens — it is not replaced.
    assert out["cooked"] == ["LOGGED", "LOGGED"]


@_needs_node
def test_a_skipped_attention_row_says_skipped_not_handled():
    """The two buttons on a row do different things, so they say different
    things — a toast that named the right thing and the wrong verb would
    be worse than the old "Changes saved"."""
    script = _STUBS + _TOAST_CORE + """
var cookState = { attention: [{ id: 9, detail: { ingredient: 'Chickpeas' } }] };
function renderCook() {}
function cookPost(url, body) { POSTS.push({ url: url, body: body }); return Promise.resolve({ items: [] }); }
function el(attrs) { return { disabled: false, getAttribute: function (n) { return attrs[n] === undefined ? null : attrs[n]; } }; }
""" + _function("cookAttentionName") + _function("cookResolveAttention") + """
(async function () {
  await cookResolveAttention(el({ 'data-attn-id': '9', 'data-status': 'dismissed' }));
  console.log(JSON.stringify({ toasts: TOASTS.map(function (t) { return t.msg; }) }));
})();
"""
    out = _node(script)
    assert out["toasts"] == ["Chickpeas was skipped"]


# ---------- the chat ----------

def test_a_chat_turn_that_changed_something_says_it_saved():
    """The send handler streams and touches the DOM throughout, so this
    one reads the source: the pop-up follows the action cards, and only
    when the turn carried any. It is the one save toast with nothing of
    its own to name — the action cards under the reply already say what
    changed — so it is the plain word on purpose."""
    i = SHELL_JS.index("      offerNextStepChips(data.actions, data.staple_offer);\n")
    tail = SHELL_JS[i:i + 1200]
    # ...and not when the turn came back with a change card — the week is
    # not saved yet, and the card's own Save says so (test_chat_change_card).
    assert "if (data.actions && data.actions.length && !data.proposal) toastSaved();" in tail


# ---------- Plan the week ----------

def test_plan_the_week_has_the_pop_up_and_every_silent_save_names_itself():
    assert '<div id="toast" class="toast" hidden></div>' in PLAN_WEEK
    assert "var SAVED_PLAIN = 'Saved';" in PLAN_WEEK
    assert PLAN_WEEK.count("function toastSaved(") == 1
    assert "'Changes saved'" not in PLAN_WEEK
    # The day sheet's Done (one write for who's out and the guests, since
    # the row-per-person sheet of 2026-09-21 — the per-tap guest stepper
    # and presence toggle went with it), holiday answer, away stretch.
    # All three name what they saved.
    assert PLAN_WEEK.count("toastSaved();") == 0
    for marker in ("paintCta();\n      toastSaved(savedLine(weekdayName(draft.date), 'saved'));",
                   "reseedSheet(date);\n      toastSaved(savedLine(h.name, 'saved'));",
                   "closeAwaySheet();\n      toastSaved(awayWho.length"):
        assert marker in PLAN_WEEK, marker


@_needs_node
def test_plan_the_weeks_toast_names_the_day_and_hides_itself():
    start = PLAN_WEEK.index("  // ---------- the pop-up ----------")
    end = PLAN_WEEK.index("  // ---------- state ----------")
    region = PLAN_WEEK[start:end]
    script = """
var cls = [];
var el = { hidden: true, textContent: '', offsetWidth: 1,
  classList: { remove: function (c) { cls = cls.filter(function (x) { return x !== c; }); }, add: function (c) { cls.push(c); } } };
var document = { getElementById: function () { return el; } };
var TIMEOUT = null;
var setTimeout = function (fn, ms) { TIMEOUT = { fn: fn, ms: ms }; return 1; };
var clearTimeout = function () {};
""" + region + """
toastSaved(savedLine('Thursday', 'saved'));
var shown = { text: el.textContent, hidden: el.hidden, cls: cls.slice(), ms: TIMEOUT.ms };
toastSaved();
console.log(JSON.stringify({ shown: shown, nameless: el.textContent, hiddenAfter: (TIMEOUT.fn(), el.hidden) }));
"""
    out = _node(script)
    assert out["shown"] == {"text": "Thursday was saved", "hidden": False, "cls": ["pop-in"], "ms": 2200}
    assert out["nameless"] == "Saved"
    assert out["hiddenAfter"] is True
