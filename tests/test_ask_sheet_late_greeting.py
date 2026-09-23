"""
Verifier follow-up (2026-09-15) to the "Ask about our recipes" card.

ensureAskSheetBuilt's greeting argument only ran the very first time the ask
sheet was ever built in a page load (askBuilt gates it). That's fine when
Cook's "Ask about our recipes" row is the first thing to open the sheet, but
if Plan's chat had already opened it earlier in the same session — writing
its own "Tell me what you'd like different and I'll rework it." opener —
the sheet was already built, ensureAskSheetBuilt returned immediately, and
the kitchen greeting this ticket added never showed. A household tapping
"Ask about our recipes" after having used Plan's chat once would see Plan's
line standing over a question about the fridge.

Fixed: a caller's greeting is now checked against `lastAssistantAskText` —
the most recently added assistant message in the shared thread — every time
`ensureAskSheetBuilt` runs, not just the first. Different, and it's
appended; the same (a second tap of the same row) is skipped, so the line
isn't stacked twice. The thread itself is untouched otherwise — nothing is
cleared, nothing new is pushed when a caller passes no greeting at all.

Runs the real `ensureAskSheetBuilt`/`addAskMessage` from shell.js under
node. `askMessageTargets` (the DOM lookup) is stubbed to return no targets
and just count its own calls — this file is about which messages get
added to the thread, not about rendering a bubble; tests/test_chat_change_
card.py and friends already cover buildAskMessageEl's own markup.
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import nodeharness
import pytest

REPO = Path(__file__).resolve().parent.parent
SHELL_JS = (REPO / "static" / "shell.js").read_text(encoding="utf-8")

_needs_node = pytest.mark.skipif(
    shutil.which("node") is None, reason="node is needed to execute the shell's own functions"
)


def _slice(start: str, end: str) -> str:
    a = SHELL_JS.index(start)
    b = SHELL_JS.index(end, a)
    return SHELL_JS[a:b]


def _node(script: str):
    res = nodeharness.run_node(script, timeout=30)
    assert res.returncode == 0, f"node failed: {res.stderr}"
    return json.loads(res.stdout.strip())


# The three pieces this behaviour actually lives across, pulled from
# shell.js rather than retyped so a future edit to any of them can't drift
# out of sync with what this file asserts: the shared state (askBuilt /
# lastAssistantAskText), addAskMessage (the only place that writes
# lastAssistantAskText), and ensureAskSheetBuilt itself (the fix).
_STATE = _slice("  var askBuilt = false;", "  var askSending = false;")
_ENSURE = _slice("  var DEFAULT_ASK_GREETING", "\n  function buildAskMessageEl(")
_ADD_ASK_MESSAGE = _slice(
    "  function addAskMessage(role, text, actions) {", "\n  function refreshStaleTabsFromActions("
)

_PRELUDE = """
// askMessageTargets is the DOM half of addAskMessage (finds #ask-messages
// etc.) — out of scope here, so it's stubbed to touch no DOM and just
// count how many times a message was actually appended, which is exactly
// what "was the greeting added" or "was it skipped" comes down to.
let targetCalls = 0;
function askMessageTargets() { targetCalls++; return []; }
function loadQuickActionChips() {}
"""

# Cook's own opener (copy sweep finding 28, 2026-09-23 — was "Ask me
# anything about the recipes we've saved.", which said the row's label
# back at the person).
RECIPES_GREETING = 'What are you looking for in our recipes?'
# Plan's own default opener (DEFAULT_ASK_GREETING in shell.js) — asserted
# verbatim, the same rule test_coaching.py's header states for user-facing
# copy: if it's deliberately reworded, update this constant in the same
# commit rather than deleting the test. Reworded 2026-09-15 (Loop Board:
# "Ask: the door says 'hold this', not 'meal edits'") from "Tell me what
# you'd like different and I'll rework it." — the old line only invited
# plan edits. Reworded again 2026-09-21 (Loop Board "Chat greeting") to
# the line Emily asked for, word for word; test_ask_sheet_greeting.py pins
# it from the source and checks it is the sheet's only exclamation mark.
DEFAULT_ASK_GREETING = 'Tell me what’s on your mind and how I can help!'


def _script(body: str) -> str:
    return _PRELUDE + _STATE + _ENSURE + _ADD_ASK_MESSAGE + body


@_needs_node
def test_plan_opening_first_does_not_swallow_cooks_later_greeting():
    """
    The exact sequence the verifier found: Plan's own chat opener runs
    first (no greeting — the default "rework it" line), THEN Cook's
    "Ask about our recipes" row is tapped. The kitchen greeting must still
    show, appended to the same thread, not silently dropped because the
    sheet was already built.
    """
    out = _node(_script("""
ensureAskSheetBuilt(); // Plan's own opener: no greeting, gets the default
var afterPlan = lastAssistantAskText;
var callsAfterPlan = targetCalls;
ensureAskSheetBuilt('%s'); // Cook's "Ask about our recipes", tapped later
console.log(JSON.stringify({
  afterPlan: afterPlan,
  callsAfterPlan: callsAfterPlan,
  afterCook: lastAssistantAskText,
  callsAfterCook: targetCalls
}));
""" % RECIPES_GREETING))
    assert out["afterPlan"] == DEFAULT_ASK_GREETING
    assert out["callsAfterPlan"] == 1, "the default greeting must still be added on first build"
    assert out["afterCook"] == RECIPES_GREETING, (
        "Cook's own greeting must be appended even though the sheet was already built"
    )
    assert out["callsAfterCook"] == 2, "the kitchen greeting must actually reach the thread"


@_needs_node
def test_a_second_tap_of_the_same_row_does_not_stack_the_greeting():
    """Cook's row opens the sheet, then is tapped again (or a re-render
    calls openAskSheet with the same prefill/greeting again) — the second
    tap must not repeat the same line under itself."""
    out = _node(_script("""
ensureAskSheetBuilt('%(g)s'); // first tap: sheet not yet built, becomes the opener
var callsAfterFirst = targetCalls;
ensureAskSheetBuilt('%(g)s'); // second tap: same greeting, sheet already built
console.log(JSON.stringify({
  callsAfterFirst: callsAfterFirst,
  callsAfterSecond: targetCalls,
  lastMessage: lastAssistantAskText
}));
""" % {"g": RECIPES_GREETING}))
    assert out["callsAfterFirst"] == 1
    assert out["callsAfterSecond"] == 1, "a repeat tap with the same greeting must not add a second copy"
    assert out["lastMessage"] == RECIPES_GREETING


@_needs_node
def test_reopening_with_no_greeting_leaves_the_thread_alone():
    """A caller that doesn't pass a greeting at all (most openAskSheet
    callers) must not disturb whatever the thread already says — this
    fix only ever ADDS a caller's own line, never removes or resets one."""
    out = _node(_script("""
ensureAskSheetBuilt('%s');
var callsAfterGreeting = targetCalls;
ensureAskSheetBuilt(); // a plain re-open, no greeting of its own
console.log(JSON.stringify({
  callsAfterGreeting: callsAfterGreeting,
  callsAfterPlainReopen: targetCalls,
  lastMessage: lastAssistantAskText
}));
""" % RECIPES_GREETING))
    assert out["callsAfterGreeting"] == 1
    assert out["callsAfterPlainReopen"] == 1, "no greeting means nothing new is said"
    assert out["lastMessage"] == RECIPES_GREETING, "the kitchen greeting must still be the last word"
