"""
Onboarding has a way back (Emily, 2026-09-09: "add a go back option in case
I want to go back to change responses").

Onboarding did have a back control before this — a bare "← Back" sitting
underneath Continue, at the bottom of the step. Emily reported it as
missing, which is the more useful bug report: a control placed after the
primary action, named after the gesture rather than after its destination,
is one you find only after you have already given up on the screen. Setup is
also the first thing a household ever does with this app, so it is the worst
possible place to be unable to correct yourself.

What is actually being pinned here is not the control but the four things
that have to be true around it:

- the control names where it goes, and every step after the first has one;
- arriving at a step draws the answers as they stand, so going back shows
  what you said rather than a blank question;
- an answer that depends on an earlier one is re-asked when that one
  changes, and the stale version is dropped rather than left lying around;
- the phone's back gesture does the same thing the control does.

And the fifth, which is the one with teeth: nothing reaches the household
until setup finishes. add_member is get-or-create by NAME and nothing in
this app deletes a member, so a name written down before it can be
corrected is a name that stays — test_a_corrected_name_would_leave_two_of_
the_same_person below is the characterisation of exactly that, and it is
why the writes moved to the end rather than being made "safe" to repeat.

Style follows tests/test_coaching.py: the page's own functions are lifted
out and RUN under node against a DOM small enough to state in one screen,
rather than the source being read for the right words. The original bug —
buildRestrictionsStep wiping what it was supposed to redraw — is exactly the
kind a source-marker test cannot see.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from app import tools


ONBOARDING_PATH = Path(__file__).resolve().parent.parent / "static" / "onboarding.html"
ONBOARDING = ONBOARDING_PATH.read_text()

_needs_node = pytest.mark.skipif(
    shutil.which("node") is None,
    reason="node is needed to run the page's own navigation for real",
)

STEPS_AFTER_THE_FIRST = [
    "rhythm-1", "rhythm-2", "restrictions", "eating-style", "wont-eat",
    "excited-about", "dinners", "typical-week", "kit-repeats",
]


# ---------- lifting the page's own code ----------
# Same two helpers tests/test_onboarding_done_receipt.py uses.

def _balanced(source: str, start: int, opener: str = "{") -> str:
    closer = {"{": "}", "[": "]"}[opener]
    i = source.index(opener, start)
    depth, j = 0, i
    while True:
        if source[j] == opener:
            depth += 1
        elif source[j] == closer:
            depth -= 1
            if depth == 0:
                break
        j += 1
    return source[start : j + 1]


def _fn(name: str) -> str:
    return _balanced(ONBOARDING, ONBOARDING.index(f"function {name}("))


def _async_fn(name: str) -> str:
    """Same, keeping the `async` — the body has awaits in it."""
    return "async " + _fn(name)


def _const(name: str) -> str:
    """Lift one `const NAME = ...` — an object, an array, or a single line."""
    start = ONBOARDING.index(f"const {name} = ")
    rest = ONBOARDING[start:]
    first = rest[len(f"const {name} = ")]
    if first in "{[":
        return _balanced(ONBOARDING, start, first) + ";"
    return rest[: rest.index("\n")]


def _popstate_handler() -> str:
    """The one popstate listener, lifted whole."""
    start = ONBOARDING.index("window.addEventListener('popstate'")
    return _balanced(ONBOARDING, start) + ");"


def _step_markup(step_id: str) -> str:
    start = ONBOARDING.index(f'<div id="{step_id}"')
    nxt = ONBOARDING.find('<div id="step-', start + 1)
    end = ONBOARDING.index("<script>", start) if nxt == -1 else nxt
    return ONBOARDING[start:end]


# ---------- a DOM the size of what these functions actually touch ----------
# It knows five things: an element has a style/dataset/classList/hidden and
# some text, setting innerHTML replaces its children, appendChild adds one,
# and a selector is matched against class names and data attributes. The
# member block is the one place the page writes a template string, so
# innerHTML also reads back the classed elements that markup declares — the
# same regex-over-real-markup trick test_coaching.py uses, and for the same
# reason: the markup is what a browser would be handed.
_DOM_STUB = r"""
function makeEl(tag) {
  const el = {
    tagName: (tag || 'div').toUpperCase(),
    _classes: new Set(), _children: [], _parent: null,
    dataset: {}, style: {}, hidden: false, textContent: '', value: '',
    _listeners: {}, onclick: null,
    get className() { return [...this._classes].join(' '); },
    set className(v) { this._classes = new Set(String(v).split(/\s+/).filter(Boolean)); },
    get innerHTML() { return this._html || ''; },
    set innerHTML(v) {
      this._html = v;
      this._children = [];
      const re = /<(\w+)([^>]*)>/g;
      let m;
      while ((m = re.exec(v)) !== null) {
        const child = makeEl(m[1]);
        const attrs = m[2];
        const cls = /class="([^"]*)"/.exec(attrs);
        if (cls) cls[1].split(/\s+/).filter(Boolean).forEach(c => child._classes.add(c));
        const data = /data-member="([^"]*)"/.exec(attrs);
        if (data) child.dataset.member = data[1];
        child._parent = this;
        this._children.push(child);
      }
    },
    classList: {
      add: function (c) { el._classes.add(c); },
      remove: function (c) { el._classes.delete(c); },
      toggle: function (c) { if (el._classes.has(c)) el._classes.delete(c); else el._classes.add(c); },
      contains: function (c) { return el._classes.has(c); }
    },
    appendChild: function (c) { c._parent = el; el._children.push(c); return c; },
    addEventListener: function (evt, fn) { (el._listeners[evt] = el._listeners[evt] || []).push(fn); },
    click: function () {
      if (el.onclick) el.onclick();
      (el._listeners.click || []).forEach(function (fn) { fn(); });
    },
    closest: function (sel) {
      let node = el;
      while (node) { if (matches(node, sel)) return node; node = node._parent; }
      return null;
    },
    querySelectorAll: function (sel) { return descendants(el).filter(n => matches(n, sel)); },
    querySelector: function (sel) { return el.querySelectorAll(sel)[0] || null; }
  };
  return el;
}
function descendants(el) {
  let out = [];
  el._children.forEach(function (c) { out.push(c); out = out.concat(descendants(c)); });
  return out;
}
// Selectors the page actually writes: a class or a chain of them
// ('.chip.active'), and a data attribute with or without a value.
function matches(el, sel) {
  if (sel.charAt(0) === '.') {
    return sel.slice(1).split('.').every(function (c) { return el._classes.has(c); });
  }
  const attr = /^\[([\w-]+)(?:="([^"]*)")?\]$/.exec(sel);
  if (attr) {
    const key = attr[1].replace(/^data-/, '').replace(/-(\w)/g, function (_, c) { return c.toUpperCase(); });
    if (attr[2] === undefined) return el.dataset[key] !== undefined;
    return el.dataset[key] === attr[2];
  }
  return false;
}

const ELS = {};
function el(id) { return (ELS[id] = ELS[id] || makeEl('div')); }
const ROOT = makeEl('div');
const document = {
  getElementById: function (id) { return ELS[id] || null; },
  createElement: function (tag) { return makeEl(tag); },
  querySelector: function (sel) { return ROOT.querySelectorAll(sel)[0] || null; },
  querySelectorAll: function (sel) { return ROOT.querySelectorAll(sel); }
};

// One history stack with a CURSOR, which is what a browser actually has:
// pushState throws away everything AHEAD of the cursor and appends,
// replaceState overwrites the entry the cursor is on, back()/forward() move
// the cursor and hand the entry it lands on to the popstate listener, and
// back() from the bottom leaves the page rather than firing anything.
//
// This stub used to implement back() as a POP, with no history.state on it
// at all. Two things follow, and being exact about them matters, because
// the blocker that shipped (one back gesture off the reveal, two taps of
// Continue, a second generated week destroying the first) is a bug the old
// stub COULD have shown — nobody asked it to. What it could not do is
// (a) hold a forward entry: back() destroyed the entry it left, so "the
// reveal is still there in front of you and gets put back" — the actual
// fix — was not a question the harness could be asked; and (b) answer
// window.history.state, which is what a reloaded page reads to know it is
// a reload, so the reload half below was inexpressible outright.
// Meanwhile the one reveal test that did exist asserted the stack didn't
// GROW, which was true, is still true, and never had anything to do with
// whether the wizard behind it was reachable. That is how a harness reads
// as coverage while providing none.
const HISTORY = [];
let CURSOR = -1;
let LEFT_PAGE = false;
const POP_LISTENERS = [];
function fire() { POP_LISTENERS.forEach(function (fn) { fn({ state: CURSOR >= 0 ? HISTORY[CURSOR] : null }); }); }
const window = {
  location: { pathname: '/onboarding' },
  scrollTo: function () {},
  addEventListener: function (evt, fn) { if (evt === 'popstate') POP_LISTENERS.push(fn); },
  history: {
    get length() { return HISTORY.length; },
    get state() { return CURSOR >= 0 ? HISTORY[CURSOR] : null; },
    pushState: function (s) { HISTORY.length = CURSOR + 1; HISTORY.push(s); CURSOR = HISTORY.length - 1; },
    replaceState: function (s) { if (CURSOR < 0) { HISTORY.push(s); CURSOR = 0; } else HISTORY[CURSOR] = s; },
    back: function () {
      if (CURSOR <= 0) { LEFT_PAGE = true; return; }   // a real browser leaves the page here
      CURSOR -= 1;
      fire();
    },
    forward: function () {
      if (CURSOR >= HISTORY.length - 1) return;
      CURSOR += 1;
      fire();
    }
  }
};
function gesture() { window.history.back(); }
function forwardGesture() { window.history.forward(); }
// How many entries are behind-or-at the cursor — "how deep into this page's
// own stack am I", which is what the depth assertions in these tests mean.
function depth() { return CURSOR + 1; }
function top() { return CURSOR >= 0 ? HISTORY[CURSOR] : null; }
// One entry as an earlier LOAD of this page would have left it: same shape,
// a stamp that is not this page's.
function staleEntry(step) { return { onboardingStep: step, onboardingLoad: 'a-previous-load' }; }
"""

# Every step div and the progress strip, plus one back button per step
# carrying its own key — the same shape the page ships.
_STEP_ELEMENTS = """
['%s'].forEach(function (k) { ELS['step-' + k] = makeEl('div'); });
ELS['progress'] = makeEl('div');
['%s'].forEach(function (k) {
  const b = makeEl('button');
  b.dataset.stepBack = k;
  b.hidden = true;
  ROOT.appendChild(b);
});
""" % ("', '".join(
    ["household", "rhythm-1", "rhythm-2", "restrictions", "eating-style", "wont-eat",
     "excited-about", "dinners", "typical-week", "kit-repeats", "reveal"]),
    "', '".join(STEPS_AFTER_THE_FIRST))


def _nav_harness(builders: str = "", seed: str = "") -> str:
    """
    The navigation block, running for real. `builders` supplies the nine
    step builders — a test that cares only about where back goes stubs them
    to record that they ran; the rhythm test hands over the page's own.
    `seed` runs just before the page's own first line, which is how a test
    puts entries from an EARLIER page load into history before this one
    starts.
    """
    return "\n".join([
        _DOM_STUB,
        _STEP_ELEMENTS,
        builders or """
const BUILT = [];
function buildRhythmStep1() { BUILT.push('rhythm-1'); }
function buildRhythmStep2() { BUILT.push('rhythm-2'); }
function buildRestrictionsStep() { BUILT.push('restrictions'); }
function buildEatingStyleStep() { BUILT.push('eating-style'); }
function buildWontEatStep() { BUILT.push('wont-eat'); }
function buildExcitedStep() { BUILT.push('excited-about'); }
function buildDinnersStep() { BUILT.push('dinners'); }
function buildTypicalWeekStep() { BUILT.push('typical-week'); }
function buildKitRepeatsStep() { BUILT.push('kit-repeats'); }
""",
        _const("ALL_STEPS"),
        _const("STEP_TITLES"),
        _fn("stepFlow"),
        _fn("stepBefore"),
        _fn("resolveStep"),
        _const("STEP_BUILDERS"),
        _fn("renderProgress"),
        _fn("renderBackLink"),
        _const("PAGE_LOAD"),
        _fn("pushStepHistory"),
        # Declared with let/var outside any function on the page, so they
        # are restated here rather than lifted.
        "var currentStep = 'household';",
        "var revealReached = false;",
        "var backLinkTarget = '';",
        _fn("showStep"),
        _fn("goBackFrom"),
        _popstate_handler(),
        _fn("startOnboarding"),
        # The page's own wiring, restated: each back button runs goBackFrom
        # with the key of the step it lives on.
        """
document.querySelectorAll('[data-step-back]').forEach(function (b) {
  b.addEventListener('click', function () { goBackFrom(b.dataset.stepBack); });
});
function tapBack(step) { document.querySelector('[data-step-back="' + step + '"]').click(); }
function backLabel(step) { return document.querySelector('[data-step-back="' + step + '"]').textContent; }
""",
        seed,
        # The page's own first line, run for real rather than restated — a
        # reload test needs the version that decides whether to replace or
        # push, and a restated one would be free to be wrong about it.
        "startOnboarding();",
    ])


def _run(script: str):
    res = subprocess.run(["node", "-e", script], capture_output=True, text=True, timeout=30)
    assert res.returncode == 0, f"node failed: {res.stderr}"
    return json.loads(res.stdout)


# ---------- the control itself ----------


def test_every_step_after_the_first_carries_a_back_control_and_the_first_does_not():
    for step in STEPS_AFTER_THE_FIRST:
        markup = _step_markup(f"step-{step}")
        assert f'data-step-back="{step}"' in markup, f"{step} has no way back"
        assert "back-link" in markup, f"{step}'s back control isn't the back-link component"
    assert "data-step-back" not in _step_markup("step-household"), (
        "the first step has nothing behind it and must not offer a way back"
    )
    assert "data-step-back" not in _step_markup("step-reveal"), (
        "setup is saved by the time the reveal renders; there is nothing to go back to"
    )


def test_the_back_destination_is_derived_rather_than_stamped_on_the_markup():
    """
    Each button is tagged with the step it LIVES ON, not the step it goes
    to. The old `data-back="restrictions"` form was a table somebody had to
    keep in step with the flow by hand, and the first conditional step would
    have made every one of those attributes a lie.
    """
    assert "data-back=" not in ONBOARDING
    assert re.search(r"function stepBefore\(", ONBOARDING)


def test_the_back_control_sits_above_the_question_not_under_continue():
    """
    Emily reported the control as missing, and it wasn't — it was below the
    primary action, which is where somebody looks only after they have given
    up on the screen.
    """
    for step in STEPS_AFTER_THE_FIRST:
        markup = _step_markup(f"step-{step}")
        assert markup.index("data-step-back") < markup.index("<h1"), (
            f"{step}'s back control is below its own question"
        )


@_needs_node
def test_each_step_names_the_step_it_goes_back_to():
    out = _run(_nav_harness() + """
const labels = {};
%s.forEach(function (step) { showStep(step); labels[step] = backLabel(step); });
console.log(JSON.stringify(labels));
""" % json.dumps(STEPS_AFTER_THE_FIRST))
    assert out == {
        "rhythm-1": "‹ Who's here",
        "rhythm-2": "‹ Your rhythm",
        "restrictions": "‹ Your timing",
        "eating-style": "‹ Restrictions",
        "wont-eat": "‹ How you eat",
        "excited-about": "‹ Never recommend",
        "dinners": "‹ What you're into",
        "typical-week": "‹ How many recipes",
        "kit-repeats": "‹ A normal week",
    }


@_needs_node
def test_tapping_back_on_each_step_lands_on_the_step_before_it():
    out = _run(_nav_harness() + """
const landed = {};
%s.forEach(function (step) { showStep(step); tapBack(step); landed[step] = currentStep; });
console.log(JSON.stringify(landed));
""" % json.dumps(STEPS_AFTER_THE_FIRST))
    flow = ["household"] + STEPS_AFTER_THE_FIRST
    assert out == {s: flow[flow.index(s) - 1] for s in STEPS_AFTER_THE_FIRST}


@_needs_node
def test_arriving_at_a_step_redraws_it_so_the_answer_is_already_filled_in():
    """
    The rule is about arriving, not about going back: every arrival runs the
    step's builder, which draws from the answers as they stand. That is what
    makes coming back show what you said, and it is why buildRestrictionsStep
    can rebuild itself without eating the answer.
    """
    out = _run(_nav_harness() + """
BUILT.length = 0;
showStep('restrictions');
const forward = BUILT.slice();
BUILT.length = 0;
tapBack('restrictions');
const back = BUILT.slice();
console.log(JSON.stringify({ forward: forward, back: back, on: currentStep }));
""")
    assert out["forward"] == ["restrictions"]
    assert out["back"] == ["rhythm-2"], "going back left the step it landed on undrawn"
    assert out["on"] == "rhythm-2"


# ---------- the phone's own back gesture ----------


@_needs_node
def test_the_back_gesture_walks_the_flow_backwards_one_step_at_a_time():
    out = _run(_nav_harness() + """
['rhythm-1', 'rhythm-2', 'restrictions', 'eating-style'].forEach(showStep);
const seen = [];
for (let i = 0; i < 4; i++) { gesture(); seen.push(currentStep); }
console.log(JSON.stringify({ seen: seen, depth: depth(), left: LEFT_PAGE }));
""")
    assert out["seen"] == ["restrictions", "rhythm-2", "rhythm-1", "household"]
    assert out["depth"] == 1, "the gesture didn't unwind the stack it walked in on"
    assert out["left"] is False, "it walked off the page early"


@_needs_node
def test_the_control_and_the_gesture_agree_rather_than_fighting_each_other():
    """
    Tapping the named control walks history back rather than pushing a new
    entry, so the gesture right after it goes on backwards instead of
    bouncing forward onto the step you just left.
    """
    out = _run(_nav_harness() + """
['rhythm-1', 'rhythm-2', 'restrictions'].forEach(showStep);
const depthBefore = depth();
tapBack('restrictions');
const afterTap = { on: currentStep, depth: depth() };
gesture();
console.log(JSON.stringify({ before: depthBefore, afterTap: afterTap, thenGesture: currentStep }));
""")
    assert out["before"] == 4
    assert out["afterTap"] == {"on": "rhythm-2", "depth": 3}
    assert out["thenGesture"] == "rhythm-1", (
        "the gesture after a back tap went forward again — the control pushed "
        "an entry instead of walking one back"
    )


@_needs_node
def test_the_gesture_off_the_first_step_is_left_to_the_browser():
    """
    There is no step behind the first one. A handler that swallowed that
    gesture would trap somebody on question one, which is worse than the bug
    this branch is fixing.
    """
    out = _run(_nav_harness() + """
const handled = [];
POP_LISTENERS.forEach(function (fn) { fn({ state: null }); handled.push(currentStep); });
POP_LISTENERS.forEach(function (fn) { fn({ state: { some: 'other page' } }); handled.push(currentStep); });
console.log(JSON.stringify(handled));
""")
    assert out == ["household", "household"]


@_needs_node
def test_the_reveal_takes_over_its_entry_instead_of_adding_one():
    """
    Arriving at the reveal doesn't grow the stack. That is all replacing
    does, though — the nine entries behind it are untouched, which is what
    the trap below exists for.
    """
    out = _run(_nav_harness() + """
['rhythm-1', 'rhythm-2'].forEach(showStep);
const before = depth();
showStep('reveal');
console.log(JSON.stringify({ before: before, after: depth(), top: top().onboardingStep }));
""")
    assert out["before"] == 3
    assert out["after"] == 3, "the reveal pushed a history entry of its own"
    assert out["top"] == "reveal"


@_needs_node
def test_the_back_gesture_cannot_get_off_the_reveal_and_back_into_the_wizard():
    """
    THE BLOCKER, reproduced. replaceState rewrites ONE entry — the one you
    are standing on — and an independent reviewer found the other nine still
    behind it in a real Chromium: one back swipe landed on typical-week with
    every answer still in memory, and two taps of Continue ran the whole
    finish a second time. A second generated week runs
    tools.retire_overlapping_plans over the first, destroying its meals and
    reversing its grocery lines.

    Checked against the pre-fix page through this same harness: it walks
    reveal -> typical-week -> dinners -> excited-about, which is the escape,
    so this test earns its keep rather than passing by construction.
    """
    out = _run(_nav_harness() + """
%s.forEach(showStep);
showStep('reveal');
const atReveal = { on: currentStep, depth: depth(), len: HISTORY.length };
const seen = [];
for (let i = 0; i < 4; i++) { gesture(); seen.push(currentStep); }
console.log(JSON.stringify({
  atReveal: atReveal, seen: seen, len: HISTORY.length,
  top: top().onboardingStep, left: LEFT_PAGE
}));
""" % json.dumps(STEPS_AFTER_THE_FIRST))
    assert out["atReveal"] == {"on": "reveal", "depth": 10, "len": 10}
    assert out["seen"] == ["reveal"] * 4, (
        "a back gesture off the reveal got back into the finished wizard"
    )
    assert out["top"] == "reveal", "the entry the reveal stands on is no longer the reveal"
    assert out["len"] == 10, "trapping the gesture grew the stack without bound"
    assert out["left"] is False


@_needs_node
def test_a_forward_gesture_from_the_reveal_stays_on_the_reveal_too():
    """
    The other direction. A back gesture is answered by pushing the reveal
    entry back on, which truncates the forward stack, so there is nothing
    ahead to swipe to either.
    """
    out = _run(_nav_harness() + """
['rhythm-1', 'rhythm-2'].forEach(showStep);
showStep('reveal');
gesture();
forwardGesture();
console.log(JSON.stringify({ on: currentStep, top: top().onboardingStep }));
""")
    assert out == {"on": "reveal", "top": "reveal"}


# ---------- a reload empties the answers but not the history ----------
# Reloading mid-flow leaves entries this page pushed naming steps whose
# answers are gone. Honouring one hands back a later question with every
# block blank, and — before the guard in finishSetupAndReveal — let a
# household of nobody finish setup and be given a generated week.


@_needs_node
def test_a_reload_mid_setup_throws_away_the_entries_in_front_of_it():
    """
    A forward swipe after a reload must not reach a later question. Pushing
    the household entry truncates the forward stack, which is what actually
    removes them rather than merely declining to honour them.
    """
    out = _run(_nav_harness(seed="""
// Where a previous load of this page had got to: four steps in, with the
// browser sitting on the third of them.
[staleEntry('household'), staleEntry('rhythm-1'), staleEntry('rhythm-2'),
 staleEntry('restrictions')].forEach(function (s) { HISTORY.push(s); });
CURSOR = 2;
""") + """
const afterLoad = { on: currentStep, len: HISTORY.length, at: depth() };
forwardGesture();
console.log(JSON.stringify({ afterLoad: afterLoad, forward: currentStep, len: HISTORY.length }));
""")
    assert out["afterLoad"]["on"] == "household", "a reload didn't start on the first step"
    assert out["afterLoad"]["len"] == 4, (
        "the stale forward entry survived the reload — a forward swipe reaches "
        "a question whose answers are gone"
    )
    assert out["afterLoad"]["at"] == 4
    assert out["forward"] == "household"


@_needs_node
def test_a_back_gesture_onto_an_entry_from_before_the_reload_collapses_onto_the_first_step():
    out = _run(_nav_harness(seed="""
[staleEntry('household'), staleEntry('rhythm-1'), staleEntry('rhythm-2')]
  .forEach(function (s) { HISTORY.push(s); });
CURSOR = 2;
""") + """
const seen = [];
const stamps = [];
for (let i = 0; i < 2; i++) {
  gesture();
  seen.push(currentStep);
  stamps.push(top().onboardingLoad === PAGE_LOAD);
}
console.log(JSON.stringify({ seen: seen, retaken: stamps }));
""")
    assert out["seen"] == ["household", "household"], (
        "a gesture landed on a step whose answers the reload had emptied"
    )
    assert out["retaken"] == [True, True], (
        "the stale entry was left stale, so the same gesture goes round in a circle"
    )


@_needs_node
def test_an_ordinary_arrival_still_replaces_rather_than_pushes():
    """
    The reload branch must not cost a normal first visit an extra entry.
    """
    out = _run(_nav_harness() + """
console.log(JSON.stringify({ len: HISTORY.length, on: currentStep, top: top().onboardingStep }));
""")
    assert out == {"len": 1, "on": "household", "top": "household"}


# ---------- setup finishes once, and never with nobody in the house ----------


def _finish_harness(members: str = "[{ name: 'Robin', age_group: 'adult' }]",
                    save_ms: int = 0) -> str:
    """
    finishSetupAndReveal itself, over the same navigation, with the four
    writes and the generation call stubbed to record that they happened.
    Nothing here reaches a network or a database — what is being pinned is
    how many times the page ASKS.

    `save_ms` is the round trip each of the four saves takes. It defaults to
    0, which is localhost and is exactly why the concurrency hole below was
    invisible; the tests that care set it to a phone's.
    """
    return "\n".join([
        _nav_harness(),
        """
ELS['household-empty'] = makeEl('p');
ELS['household-empty'].hidden = true;
ELS['kit-repeats-next'] = makeEl('button');
ELS['kit-repeats-next'].disabled = false;
ELS['kit-repeats-skip'] = makeEl('span');
ELS['kit-repeats-skip'].textContent = "Skip — I'll tell you as we go";
const POSTS = [];
const SAVE_MS = %d;
const wait = () => new Promise(r => setTimeout(r, SAVE_MS));
var MEMBERS = %s;
var kitchenKit = [];
function currentMembers() { return MEMBERS; }
async function saveHouseholdMembers() { POSTS.push('household'); await wait(); }
async function saveRhythmAnswers() { POSTS.push('rhythm'); await wait(); }
async function saveOnboardingAnswers() { POSTS.push('answers'); await wait(); return { member_names: [] }; }
async function savePlanTheWeekAnswers() { POSTS.push('plan-the-week'); await wait(); }
async function generateFirstPlanAndReveal() { POSTS.push('generate-first-plan'); }
var setupRun = 'idle';
function alert() {}
""" % (save_ms, members),
        _fn("showHouseholdEmptyNote"),
        _async_fn("finishSetupAndReveal"),
        _fn("setKitRepeatsBusy"),
        _const("KIT_SKIP_LABEL"),
        # The two controls' own handlers, restated: what the page wires them
        # to, which is the whole of what a tap does.
        """
// The page's handlers discard the promise; these hand it back so a test can
// wait for the run to end. That is the only difference, and it changes
// nothing about when the guard is read.
function tapContinue() { return finishSetupAndReveal(); }
function tapSkip() { kitchenKit = []; return finishSetupAndReveal(); }
function skipIsTappable() {
  return !ELS['kit-repeats-skip']._classes.has('is-busy');
}
function skipSays() { return ELS['kit-repeats-skip'].textContent; }
""",
    ])


@_needs_node
def test_generation_can_never_be_asked_for_twice_from_this_page():
    """
    The invariant with teeth. However the finish button is reached — the
    history trap makes it unreachable, and this is the guard behind that —
    /api/onboarding/generate-first-plan is asked for at most once per load,
    because the second week generated takes over the first and destroys it.
    """
    out = _run(_finish_harness() + """
(async function () {
  %s.forEach(showStep);
  await tapContinue();
  const first = POSTS.slice();
  gesture();                  // the phone's back gesture off the reveal
  const afterBack = currentStep;
  await tapContinue();        // and the finish it used to be able to reach
  await tapSkip();
  console.log(JSON.stringify({ first: first, afterBack: afterBack, posts: POSTS, on: currentStep }));
})();
""" % json.dumps(STEPS_AFTER_THE_FIRST))
    assert out["first"] == ["household", "rhythm", "answers", "plan-the-week", "generate-first-plan"]
    assert out["afterBack"] == "reveal"
    assert out["posts"] == out["first"], (
        "setup ran a second time — a second generated week takes over the first"
    )
    assert out["on"] == "reveal"


@_needs_node
def test_a_failed_save_leaves_setup_finishable():
    """
    The guard is about a week that got built, not about one attempt. A save
    that throws never reaches the generation call, so trying again has to
    still work.
    """
    out = _run(_finish_harness() + """
(async function () {
  let firstTry = true;
  saveRhythmAnswers = async function () {
    POSTS.push('rhythm');
    if (firstTry) { firstTry = false; throw new Error('nope'); }
  };
  showStep('kit-repeats');
  await tapSkip();
  const afterFailure = POSTS.slice();
  const controlsFreed = { tappable: skipIsTappable(), says: skipSays() };
  await tapSkip();
  console.log(JSON.stringify({
    afterFailure: afterFailure, controlsFreed: controlsFreed,
    posts: POSTS, on: currentStep
  }));
})();
""")
    assert out["afterFailure"] == ["household", "rhythm"]
    assert out["posts"][-1] == "generate-first-plan"
    assert out["on"] == "reveal"
    assert out["controlsFreed"] == {"tappable": True, "says": "Skip — I'll tell you as we go"}, (
        "the controls stayed in their in-progress state after a failure, so the "
        "household cannot try again"
    )


@_needs_node
def test_setup_cannot_complete_with_nobody_in_the_household():
    """
    The "add at least one person" check lived only on the household step's
    own Continue, and a history gesture can reach the finish button without
    it ever having run. A household of zero people posted and was handed a
    generated week.
    """
    out = _run(_finish_harness(members="[]") + """
(async function () {
  showStep('kit-repeats');
  await tapSkip();
  console.log(JSON.stringify({
    posts: POSTS, on: currentStep, noteShown: !ELS['household-empty'].hidden
  }));
})();
""")
    assert out["posts"] == [], "a household of nobody wrote itself down"
    assert out["on"] == "household", "it didn't send them back to the step that fixes it"
    assert out["noteShown"] is True


def test_the_empty_household_is_told_in_a_line_on_the_step_not_an_alert():
    """
    DESIGN_SYSTEM §8: a problem is stated plainly and paired with its way out
    in the same breath. An alert also covers the list of names it is talking
    about.
    """
    markup = _step_markup("step-household")
    assert 'id="household-empty"' in markup
    assert "add whoever's eating" in markup
    # Both doors to the check say it the same way, and neither says it in an
    # alert. (The alert left in finishSetupAndReveal's catch is a different
    # thing entirely — a save that failed on the network, untouched here.)
    assert "showHouseholdEmptyNote(true)" in _fn("finishSetupAndReveal")
    assert "showHouseholdEmptyNote(true)" in ONBOARDING.split("household-next")[2]
    assert "alert('Add at least one person" not in ONBOARDING


# ---------- two taps of the same control ----------
# The guard's first version set its flag AFTER the four awaited saves and
# leaned on `btn.disabled` for the window in between. That covered Continue
# — a disabled button dispatches no click — and covered the SKIP LINK not at
# all, because it is a <span> and was passed no button to disable. Two
# genuine taps started two CONCURRENT runs: eight POSTs interleaved, two
# generations in flight, each retiring the other's week.
#
# save_ms is why it survived local verification. At 0ms (localhost) the
# window is a couple of ticks wide; at a phone's 120-600ms it is most of a
# second in which the link looks untouched.


@_needs_node
def test_two_taps_of_the_skip_link_start_one_run_not_two():
    out = _run(_finish_harness(save_ms=120) + """
(async function () {
  showStep('kit-repeats');
  const first = tapSkip();          // not awaited: the second tap lands mid-flight
  await new Promise(r => setTimeout(r, 60));
  const second = tapSkip();
  await Promise.all([first, second]);
  console.log(JSON.stringify({ posts: POSTS, on: currentStep }));
})();
""")
    assert out["posts"].count("generate-first-plan") == 1, (
        "two taps of Skip asked for two first weeks — the second generation "
        "retires the first"
    )
    assert out["posts"] == ["household", "rhythm", "answers", "plan-the-week",
                            "generate-first-plan"], (
        "the four saves were posted more than once (interleaved = two runs at once)"
    )
    assert out["on"] == "reveal"


@_needs_node
def test_the_same_holds_at_a_poor_signal_and_for_a_slow_second_tap():
    """
    The reviewer's worst measured row: a 600ms round trip and a second tap a
    second and a half later, still inside the first run.
    """
    out = _run(_finish_harness(save_ms=600) + """
(async function () {
  showStep('kit-repeats');
  const first = tapSkip();
  await new Promise(r => setTimeout(r, 1500));
  const second = tapSkip();
  await Promise.all([first, second]);
  console.log(JSON.stringify({ posts: POSTS }));
})();
""")
    assert out["posts"].count("generate-first-plan") == 1
    assert out["posts"].count("household") == 1


@_needs_node
def test_continue_and_skip_are_the_same_guard_not_two():
    """
    Tapping one and then the other is the same hole, and neither control is
    trusted to be the thing that stops it.
    """
    out = _run(_finish_harness(save_ms=120) + """
(async function () {
  showStep('kit-repeats');
  const first = tapContinue();
  await new Promise(r => setTimeout(r, 60));
  const second = tapSkip();
  await Promise.all([first, second]);
  console.log(JSON.stringify({ posts: POSTS }));
})();
""")
    assert out["posts"].count("generate-first-plan") == 1
    assert out["posts"].count("household") == 1


@_needs_node
def test_the_skip_link_says_it_is_working_and_stops_being_tappable():
    """
    A control that does nothing visible for two seconds is what invites the
    second tap. Continue greys out on its own; the span had to be told.
    """
    out = _run(_finish_harness(save_ms=120) + """
(async function () {
  showStep('kit-repeats');
  const before = { tappable: skipIsTappable(), says: skipSays(),
                   btn: ELS['kit-repeats-next'].disabled };
  const run = tapSkip();
  const during = { tappable: skipIsTappable(), says: skipSays(),
                   btn: ELS['kit-repeats-next'].disabled };
  await run;
  console.log(JSON.stringify({ before: before, during: during }));
})();
""")
    assert out["before"] == {"tappable": True, "says": "Skip — I'll tell you as we go",
                            "btn": False}
    assert out["during"]["tappable"] is False, "the skip link stayed tappable mid-save"
    assert out["during"]["btn"] is True, "Continue stayed enabled mid-save"
    assert out["during"]["says"] == "Saving your answers…", (
        "the skip link gave no sign it had been tapped"
    )


def test_the_guard_does_not_depend_on_a_button_being_passed_in():
    """
    Source-level, and worth pinning because it is the shape of the bug: the
    function takes no control to disable, and the flag it reads is set
    before the first await rather than after the four saves.
    """
    finish = _fn("finishSetupAndReveal")
    assert "function finishSetupAndReveal()" in finish, (
        "finishSetupAndReveal takes a control again — the btn.disabled idiom "
        "only ever covered the button, never the skip link"
    )
    assert "btn.disabled" not in finish
    assert finish.index("setupRun = 'running'") < finish.index("await save"), (
        "the re-entry flag is set after an await, so two taps get through"
    )
    assert "setupRun = 'idle'" in finish, "a failed save can never be retried"
    # Every caller goes through the one guard.
    assert ONBOARDING.count("finishSetupAndReveal(") == 3  # definition + two taps


# ---------- what depends on what ----------


def _rhythm_harness() -> str:
    """
    The rhythm step's own code, over the same DOM. currentMembers is the one
    thing stubbed — these tests are about what happens when the household
    changes, so the household is the input.
    """
    return "\n".join([
        _DOM_STUB,
        """
['rhythm-lunch-people', 'rhythm-meals-together-chips', 'rhythm-cooking-role-chips',
 'rhythm-cooking-who-chips', 'rhythm-cooking-who-wrap', 'rhythm-cooking-hint',
 'rhythm-meals-together-card', 'rhythm-cooking-card', 'rhythm-1-next'].forEach(function (id) { el(id); });
// The lunch card is what renderLunchPeople reaches for with closest().
ELS['rhythm-lunch-people']._classes.add('rhythm-lunch-people');
const LUNCH_CARD = makeEl('div');
LUNCH_CARD._classes.add('rhythm-card');
LUNCH_CARD.appendChild(ELS['rhythm-lunch-people']);
var MEMBERS = [];
function currentMembers() { return MEMBERS; }
""",
        _const("LUNCH_LOCATION_OPTIONS"),
        _const("MEALS_TOGETHER_OPTIONS"),
        _const("COOKING_ROLE_OPTIONS"),
        "var rhythmLunchLocation = {};",
        "var rhythmMealsTogether = '';",
        "var rhythmCookingRole = '';",
        "var rhythmCookingWho = '';",
        "var rhythmSoloDefaultsApplied = false;",
        _fn("buildSingleSelectChips"),
        _fn("isCookEligible"),
        _fn("eligibleCooks"),
        _fn("renderLunchRow"),
        _fn("renderLunchPeople"),
        _fn("renderMealsTogetherChips"),
        _fn("renderCookingWhoChips"),
        _fn("renderCookingRoleChips"),
        _fn("isSoloAdultHousehold"),
        _fn("applySoloAdultDefaults"),
        _fn("rhythm1Complete"),
        _fn("updateRhythm1ContinueState"),
        _fn("buildRhythmStep1"),
    ])


@_needs_node
def test_adding_a_second_person_re_asks_who_cooks_and_clears_the_filled_in_answer():
    """
    A household of exactly one adult isn't asked which meals it eats
    together or who cooks — both have one possible answer, and the page
    fills them in. Going back and adding somebody makes both questions real
    again, and the answer nobody gave has to go rather than being shipped as
    though they had.
    """
    out = _run(_rhythm_harness() + """
MEMBERS = [{ name: 'Robin', age_group: 'adult' }];
buildRhythmStep1();
const solo = {
  together: rhythmMealsTogether, role: rhythmCookingRole, who: rhythmCookingWho,
  togetherCardHidden: ELS['rhythm-meals-together-card'].style.display === 'none',
  complete: rhythm1Complete()
};
MEMBERS = [{ name: 'Robin', age_group: 'adult' }, { name: 'Sam', age_group: 'adult' }];
buildRhythmStep1();
const pair = {
  together: rhythmMealsTogether, role: rhythmCookingRole, who: rhythmCookingWho,
  togetherCardHidden: ELS['rhythm-meals-together-card'].style.display === 'none',
  complete: rhythm1Complete()
};
console.log(JSON.stringify({ solo: solo, pair: pair }));
""")
    assert out["solo"] == {
        "together": "most_meals", "role": "one_person", "who": "Robin",
        "togetherCardHidden": True, "complete": False,
    }
    assert out["pair"] == {
        "together": "", "role": "", "who": "",
        "togetherCardHidden": False, "complete": False,
    }


@_needs_node
def test_a_lunch_answer_for_somebody_no_longer_here_is_dropped():
    out = _run(_rhythm_harness() + """
MEMBERS = [{ name: 'Robin', age_group: 'adult' }, { name: 'Jamie', age_group: 'adult' }];
buildRhythmStep1();
rhythmLunchLocation = { Robin: 'home', Jamie: 'out' };
MEMBERS = [{ name: 'Robin', age_group: 'adult' }, { name: 'James', age_group: 'adult' }];
buildRhythmStep1();
console.log(JSON.stringify({ lunch: rhythmLunchLocation }));
""")
    assert out["lunch"] == {"Robin": "home"}, (
        "a lunch answer survived the person it was about being renamed away"
    )


def _restrictions_harness() -> str:
    return "\n".join([
        _DOM_STUB,
        """
el('diet-by-member');
var MEMBERS = [];
function currentMembers() { return MEMBERS; }
""",
        _const("DIET_OPTIONS"),
        _fn("escapeHtmlLocal"),
        _fn("chipWithCustom"),
        _fn("getActiveChips"),
        "var restrictionAnswers = {};",
        _fn("pruneRestrictionAnswers"),
        _fn("buildRestrictionsStep"),
        _fn("currentRestrictions"),
        """
// Tap one chip in one person's row, the way a finger would.
function tapDiet(name, value) {
  const row = ELS['diet-by-member'].querySelectorAll('.diet-chips')
    .filter(function (r) { return r.dataset.member === name; })[0];
  row.querySelectorAll('.chip').filter(function (c) { return c.dataset.value === value; })[0].click();
}
function typeAllergy(name, text) {
  const row = ELS['diet-by-member'].querySelectorAll('.diet-chips')
    .filter(function (r) { return r.dataset.member === name; })[0];
  const input = row.closest('.member-block').querySelector('.diet-allergy-input');
  input.value = text;
  (input._listeners.input || []).forEach(function (fn) { fn(); });
}
function activeFor(name) {
  const row = ELS['diet-by-member'].querySelectorAll('.diet-chips')
    .filter(function (r) { return r.dataset.member === name; })[0];
  return getActiveChips(row);
}
""",
    ])


@_needs_node
def test_a_restriction_survives_the_step_being_rebuilt_around_it():
    """
    The original bug: the step redrew itself from the household every time
    it was reached, so coming forward after any change handed back an empty
    set of chips. The answers live in restrictionAnswers now, and the chips
    are drawn from them.
    """
    out = _run(_restrictions_harness() + """
MEMBERS = [{ name: 'Robin' }, { name: 'Sam' }];
buildRestrictionsStep();
tapDiet('Robin', 'Vegetarian');
tapDiet('Sam', 'Allergy');
typeAllergy('Sam', 'peanuts, shellfish');
buildRestrictionsStep();   // arriving again, e.g. on the way back
console.log(JSON.stringify({
  robinChips: activeFor('Robin'),
  samChips: activeFor('Sam'),
  saved: currentRestrictions()
}));
""")
    assert out["robinChips"] == ["Vegetarian"]
    assert out["samChips"] == ["Allergy"]
    assert out["saved"] == {
        "Robin": ["Vegetarian"],
        "Sam": ["allergy: peanuts", "allergy: shellfish"],
    }


@_needs_node
def test_a_restriction_for_somebody_no_longer_here_is_dropped_rather_than_shipped():
    out = _run(_restrictions_harness() + """
MEMBERS = [{ name: 'Robin' }, { name: 'Jamie' }];
buildRestrictionsStep();
tapDiet('Jamie', 'Vegan');
MEMBERS = [{ name: 'Robin' }, { name: 'James' }];
buildRestrictionsStep();
console.log(JSON.stringify({ saved: currentRestrictions(), held: Object.keys(restrictionAnswers) }));
""")
    assert out["saved"] == {}
    assert out["held"] == ["Robin", "James"], (
        "a restriction stayed on file for a name nobody in the household answers to"
    )


def _household_edit_harness() -> str:
    """
    The household step's own rows, with the real remove/rename handlers on
    them, over the restrictions harness — which is what makes the reuse case
    below expressible: it edits the household the way a finger does, and
    lets the prune fall where the page puts it.
    """
    return "\n".join([
        _restrictions_harness().replace(
            "var MEMBERS = [];\nfunction currentMembers() { return MEMBERS; }",
            """
// The real thing, reading rows out of a container, rather than a stub list.
const membersDiv = el('members');
var rhythmLunchLocation = {};
var rhythmCookingWho = '';
function addMemberRow(name) {
  const block = makeEl('div');
  block._classes.add('member-block');
  const input = makeEl('input');
  input._classes.add('member-name');
  input.value = name || '';
  block.appendChild(input);
  const remove = makeEl('button');
  remove._classes.add('remove-btn');
  block.appendChild(remove);
  remove.onclick = function () {
    block._parent._children = block._parent._children.filter(function (c) { return c !== block; });
    pruneMemberKeyedAnswers();
  };
  input.addEventListener('input', pruneMemberKeyedAnswers);
  membersDiv.appendChild(block);
  return block;
}
function rename(block, to) {
  block.querySelector('.member-name').value = to;
  (block.querySelector('.member-name')._listeners.input || []).forEach(function (fn) { fn(); });
}
function removeRow(block) { block.querySelector('.remove-btn').click(); }
""",
        ),
        # The page's own reader of the rows, and its own prune. addMemberRow
        # is the one thing stubbed above (its markup is a template string and
        # its age chips are a different question) — the two handlers it wires
        # are copied onto the stub rows verbatim.
        _fn("currentMembers"),
        _fn("pruneMemberKeyedAnswers"),
    ])


@_needs_node
def test_an_allergy_cannot_transfer_to_a_different_person_when_a_name_is_reused():
    """
    Sam and Alex; Sam says "allergy: peanuts". Go back, remove Sam, rename
    Alex to Sam. The prune used to live only inside buildRestrictionsStep,
    so a history jump that skipped that rebuild carried Sam's allergy onto
    Alex — by then there IS a Sam in the household, so nothing downstream
    could tell. This app treats a wrong allergy attribution as a safety bug.

    The household edits themselves prune now, which catches the removal
    while the name is still gone.
    """
    out = _run(_household_edit_harness() + """
const sam = addMemberRow('Sam');
const alex = addMemberRow('Alex');
buildRestrictionsStep();
tapDiet('Sam', 'Allergy');
typeAllergy('Sam', 'peanuts');
const answered = currentRestrictions();
// Back to the household step: Sam goes, Alex takes the name.
removeRow(sam);
rename(alex, 'Sam');
// ...and forward past restrictions without that step being rebuilt.
console.log(JSON.stringify({
  answered: answered,
  after: currentRestrictions(),
  held: Object.keys(restrictionAnswers),
  members: currentMembers().map(function (m) { return m.name; })
}));
""")
    assert out["answered"] == {"Sam": ["allergy: peanuts"]}
    assert out["members"] == ["Sam"]
    assert out["after"] == {}, (
        "a peanut allergy Sam declared was shipped as Alex's, because Alex is "
        "now called Sam"
    )
    assert out["held"] == []


@_needs_node
def test_a_lunch_answer_cannot_transfer_the_same_way():
    """
    The same shape one door along: saveRhythmAnswers posts
    rhythmLunchLocation verbatim, keyed by name, on the same end-of-setup
    request.
    """
    out = _run(_household_edit_harness() + """
const sam = addMemberRow('Sam');
const alex = addMemberRow('Alex');
rhythmLunchLocation = { Sam: 'out', Alex: 'home' };
rhythmCookingWho = 'Sam';
removeRow(sam);
rename(alex, 'Sam');
console.log(JSON.stringify({ lunch: rhythmLunchLocation, who: rhythmCookingWho }));
""")
    assert out["lunch"] == {}, "a lunch location moved onto a different person"
    assert out["who"] == "", "the cook pick stayed pointed at a name that is now somebody else"


def test_the_payload_builder_prunes_too_rather_than_trusting_the_route():
    """
    NIT-level but the point of the whole fix: currentRestrictions() is the
    function that writes the payload, so it prunes as well, and the
    guarantee stops depending on which screens were drawn on the way.
    """
    assert "pruneRestrictionAnswers();" in _fn("currentRestrictions")
    assert "pruneRestrictionAnswers();" in _fn("buildRestrictionsStep")
    assert "pruneMemberKeyedAnswers" in _fn("addMemberRow")


# ---------- nothing is written until setup finishes ----------


def test_no_step_writes_anything_on_the_way_past():
    """
    Every save happens once, at the end, in finishSetupAndReveal. Each of
    the four should appear exactly twice in the page: its own definition and
    that one call.
    """
    finish = _fn("finishSetupAndReveal")
    for name in ("saveHouseholdMembers", "saveRhythmAnswers",
                 "saveOnboardingAnswers", "savePlanTheWeekAnswers"):
        assert f"{name}(" in finish, f"{name} isn't called from finishSetupAndReveal"
        assert ONBOARDING.count(f"{name}(") == 2, (
            f"{name} is called from somewhere other than finishSetupAndReveal — "
            "an answer written before it can be corrected is an answer that stays"
        )
    # Members before the rhythm and the answer set, both of which name them.
    assert finish.index("saveHouseholdMembers(") < finish.index("saveRhythmAnswers(")
    assert finish.index("saveRhythmAnswers(") < finish.index("saveOnboardingAnswers(")


def test_the_rhythm_step_no_longer_promises_a_save_it_does_not_make():
    assert "Save my rhythm" not in re.sub(r"<!--.*?-->", "", ONBOARDING, flags=re.S)


def test_a_corrected_name_would_leave_two_of_the_same_person(signed_in):
    """
    Why the writes moved to the end, characterised against the real route.
    add_member is get-or-create by NAME and nothing in this app removes a
    member, so posting the household twice with a corrected spelling leaves
    both. There is no fix on this side — the fix is not to post until the
    answer is final.
    """
    signed_in.post("/api/onboarding/household", json={
        "members": [{"name": "Robin", "age_group": "adult"},
                    {"name": "Jamie", "age_group": "adult"}],
    })
    signed_in.post("/api/onboarding/household", json={
        "members": [{"name": "Robin", "age_group": "adult"},
                    {"name": "James", "age_group": "adult"}],
    })
    names = sorted(m["name"] for m in tools.list_members())
    assert names == ["James", "Jamie", "Robin"]


def test_one_pass_through_setup_writes_each_person_once(signed_in):
    """
    The other half: the single end-of-setup pass posts the household and
    then the answer set, both keyed by the same names, and lands on one row
    per person.
    """
    signed_in.post("/api/onboarding/household", json={
        "members": [{"name": "Robin", "age_group": "adult"},
                    {"name": "James", "age_group": "adult"}],
    })
    signed_in.post("/api/onboarding/answers", json={
        "member_names": ["Robin", "James"],
        "household_restrictions": {"James": ["allergy: peanuts"]},
        "eating_style": "", "wont_eat": [], "excited_about": [],
        "dinners_per_week": 5,
    })
    names = sorted(m["name"] for m in tools.list_members())
    assert names == ["James", "Robin"]
