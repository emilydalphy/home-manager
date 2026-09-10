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

// One history stack, behaving the way a browser's does: pushState grows it,
// replaceState overwrites the top, back() pops and hands the entry it lands
// on to the popstate listener. That is what lets a test drive the phone's
// own back gesture rather than assert about it.
const HISTORY = [];
const POP_LISTENERS = [];
const window = {
  location: { pathname: '/onboarding' },
  scrollTo: function () {},
  addEventListener: function (evt, fn) { if (evt === 'popstate') POP_LISTENERS.push(fn); },
  history: {
    get length() { return HISTORY.length; },
    pushState: function (s) { HISTORY.push(s); },
    replaceState: function (s) { if (HISTORY.length) HISTORY[HISTORY.length - 1] = s; else HISTORY.push(s); },
    back: function () {
      HISTORY.pop();
      const s = HISTORY.length ? HISTORY[HISTORY.length - 1] : null;
      POP_LISTENERS.forEach(function (fn) { fn({ state: s }); });
    }
  }
};
function gesture() { window.history.back(); }
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


def _nav_harness(builders: str = "") -> str:
    """
    The navigation block, running for real. `builders` supplies the nine
    step builders — a test that cares only about where back goes stubs them
    to record that they ran; the rhythm test hands over the page's own.
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
        _fn("pushStepHistory"),
        # Declared with let/var outside any function on the page, so they
        # are restated here rather than lifted.
        "var currentStep = 'household';",
        "var backLinkTarget = '';",
        _fn("showStep"),
        _fn("goBackFrom"),
        _popstate_handler(),
        # The page's own wiring, restated: each back button runs goBackFrom
        # with the key of the step it lives on.
        """
document.querySelectorAll('[data-step-back]').forEach(function (b) {
  b.addEventListener('click', function () { goBackFrom(b.dataset.stepBack); });
});
function tapBack(step) { document.querySelector('[data-step-back="' + step + '"]').click(); }
function backLabel(step) { return document.querySelector('[data-step-back="' + step + '"]').textContent; }
showStep('household', { replace: true });
""",
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
console.log(JSON.stringify({ seen: seen, depth: window.history.length }));
""")
    assert out["seen"] == ["restrictions", "rhythm-2", "rhythm-1", "household"]
    assert out["depth"] == 1, "the gesture didn't unwind the stack it walked in on"


@_needs_node
def test_the_control_and_the_gesture_agree_rather_than_fighting_each_other():
    """
    Tapping the named control walks history back rather than pushing a new
    entry, so the gesture right after it goes on backwards instead of
    bouncing forward onto the step you just left.
    """
    out = _run(_nav_harness() + """
['rhythm-1', 'rhythm-2', 'restrictions'].forEach(showStep);
const depthBefore = window.history.length;
tapBack('restrictions');
const afterTap = { on: currentStep, depth: window.history.length };
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
    By the time the reveal renders the answers are saved and a week has been
    asked for. A gesture back into the finished wizard would offer to
    re-answer what is already written down, and coming forward again would
    build a second first week.
    """
    out = _run(_nav_harness() + """
['rhythm-1', 'rhythm-2'].forEach(showStep);
const before = window.history.length;
showStep('reveal');
console.log(JSON.stringify({ before: before, after: window.history.length, top: HISTORY[HISTORY.length - 1] }));
""")
    assert out["before"] == 3
    assert out["after"] == 3, "the reveal pushed a history entry of its own"
    assert out["top"] == {"onboardingStep": "reveal"}


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
