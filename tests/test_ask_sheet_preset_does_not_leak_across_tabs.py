"""
Bug report (Emily, 2026-09-25): she opened chat on the Shop tab and saw the
title "What's on your mind?" (normal), but the assistant's only bubble was
"What are you looking for in our recipes?" and the composer was prefilled
with "What recipes do we have saved?" — Cook's More sheet "Ask about our
recipes" row's own opener (static/shell.js's onKitchenClick, data-kit
"recipes"):

    openAskSheet('What recipes do we have saved?', null,
                 'What are you looking for in our recipes?')

Root cause: two pieces of state the ask sheet keeps for the rest of the
page load never got cleared on close.

1. static/shell.js's `closeAskSheet` (around line 20664) resets
   `askContext` but never `askInput.value` — so a prefill an opener wrote
   into the shared textarea just sat there once the sheet was closed
   unsent.
2. `ensureAskSheetBuilt` (around line 19772) only appends a caller's own
   greeting when it differs from `lastAssistantAskText`, and skips
   entirely once the sheet has been built once (`askBuilt`) — by design,
   for a *continuing* conversation (see its own comment: the thread is
   shared across tabs on purpose). But an abandoned preset that nobody
   ever replied to isn't a conversation; the next, unrelated
   `openAskSheet()` call (no prefill, no greeting — the ordinary way
   every other tab's chat icon opens the sheet, e.g. line ~1028) still
   found `askBuilt` true and did nothing, so the kitchen's opener bubble
   and prefill both silently rode along into Shop's chat.

Fix (in `openAskSheet`): a thread nobody has actually sent a message in
(`askConversationStarted`, flipped true only by a real send in
`sendAskMessage`) is treated as not-yet-started. Opening the sheet again
in that state now resets the thread (`resetAskThread`) before rebuilding,
so an unrelated open gets the tab's own normal default greeting, and a
leftover *preset* prefill (`askDraftIsPreset`, set true only when
`openAskSheet` itself writes the textarea, cleared by a real keystroke) is
cleared rather than reused. A prefill the household typed themselves by
hand is a genuine unsent draft and is deliberately left alone — the same
per-viewer convenience every other draft field in the app gets. A thread
that DID get a real reply is untouched, exactly as the shared-thread
design intends.

Runs the real state vars / ensureAskSheetBuilt / addAskMessage /
openAskSheet / resetAskThread from shell.js under node, with the DOM and
sibling-sheet functions they touch stubbed out — this file is about which
greeting and which draft text survive an open, not about rendering.
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


# Pulled straight from shell.js rather than retyped, the same discipline
# test_ask_sheet_late_greeting.py uses, so a future edit to any of these
# can't silently drift out of sync with what this file asserts.
_STATE = _slice("  var askBuilt = false;", "  var askContextEl = document.getElementById('ask-context');")
_ENSURE = _slice("  var DEFAULT_ASK_GREETING", "\n  function buildAskMessageEl(")
_ADD_ASK_MESSAGE = _slice(
    "  function addAskMessage(role, text, actions) {", "\n  // A chat turn that changes the plan"
)
_OPEN_AND_RESET = _slice(
    "  var askSheetHistoryPushed = false;", "\n  // What the sheet's Back is called"
)

RECIPES_PREFILL = 'What recipes do we have saved?'
RECIPES_GREETING = 'What are you looking for in our recipes?'
DEFAULT_ASK_GREETING = 'Tell me what’s on your mind and how I can help!'

_PRELUDE = """
// Stand-ins for the DOM / sibling-sheet machinery openAskSheet touches.
// None of it is what this file is testing (that's addAskMessage/
// ensureAskSheetBuilt/openAskSheet/resetAskThread, run for real below).
let targetCalls = 0;
function askMessageTargets() { targetCalls++; return []; }
function loadQuickActionChips() {}
function closeWeekSheet() {}
function closeMealsMoreSheet() {}
function openSheet() {}
function setAskBackLabel() {}
function askBackLabel() { return 'Back'; }
function currentTabKey() { return 'grocery'; } // e.g. the Shop tab
function autoGrowAskInput() {}
var askSheetOpen = false;
var askSheet = { hidden: true, classList: { contains: function (c) { return c === 'is-open' && askSheetOpen; } } };
var askScrim = {};
var askMessagesEl = { innerHTML: '' };
var askInput = { value: '', focus: function () {} };
var window = { history: { pushState: function () {} }, location: { pathname: '/' } };
"""


def _script(body: str) -> str:
    return _PRELUDE + _STATE + _ENSURE + _ADD_ASK_MESSAGE + _OPEN_AND_RESET + body


@_needs_node
def test_abandoned_preset_does_not_leak_into_the_next_unrelated_open():
    """The exact bug report: Cook's 'Ask about our recipes' is opened and
    abandoned (never sent), then chat is opened plainly from another tab —
    that second open must show the tab's own normal default greeting and
    an empty composer, not the recipes preset."""
    out = _node(_script("""
openAskSheet('%s', null, '%s'); // Cook's "Ask about our recipes", abandoned
var afterCook = { greeting: lastAssistantAskText, draft: askInput.value };
openAskSheet(); // a plain, unrelated open on another tab later
console.log(JSON.stringify({
  afterCook: afterCook,
  afterPlainReopen: { greeting: lastAssistantAskText, draft: askInput.value }
}));
""" % (RECIPES_PREFILL, RECIPES_GREETING)))
    assert out["afterCook"]["greeting"] == RECIPES_GREETING
    assert out["afterCook"]["draft"] == RECIPES_PREFILL
    assert out["afterPlainReopen"]["greeting"] == DEFAULT_ASK_GREETING, (
        "a later, unrelated chat open must not still show the abandoned "
        "recipes-preset bubble"
    )
    assert out["afterPlainReopen"]["draft"] == "", (
        "a later, unrelated chat open must not still carry the abandoned "
        "recipes-preset prefill"
    )


@_needs_node
def test_ask_about_our_recipes_still_works_when_actually_chosen():
    """The fix must not break the feature it's fixing a side effect of —
    tapping the row itself still shows its own greeting and prefill."""
    out = _node(_script("""
openAskSheet('%s', null, '%s');
console.log(JSON.stringify({ greeting: lastAssistantAskText, draft: askInput.value }));
""" % (RECIPES_PREFILL, RECIPES_GREETING)))
    assert out["greeting"] == RECIPES_GREETING
    assert out["draft"] == RECIPES_PREFILL


@_needs_node
def test_a_genuinely_typed_draft_survives_an_unrelated_reopen():
    """A per-viewer convenience: if the household actually typed something
    themselves (not a preset) and closes without sending, a later chat
    open elsewhere must still have their words waiting, even though the
    thread itself resets to a fresh greeting."""
    out = _node(_script("""
openAskSheet(); // plain open, nothing preset
askInput.value = 'do we have enough flour for the weekend bake'; // the household types by hand
askDraftIsPreset = false; // askInput's real 'input' listener does this on every keystroke
openAskSheet(); // reopened later, elsewhere, with no prefill of its own
console.log(JSON.stringify({ draft: askInput.value, greeting: lastAssistantAskText }));
"""))
    assert out["draft"] == 'do we have enough flour for the weekend bake', (
        "a genuinely hand-typed draft must survive an unrelated reopen"
    )
    assert out["greeting"] == DEFAULT_ASK_GREETING


@_needs_node
def test_a_real_reply_keeps_the_shared_thread_exactly_as_designed():
    """Once the household has actually sent something in the thread
    (askConversationStarted), the shared-thread-across-tabs behaviour this
    fix must NOT touch: a later plain reopen leaves the last assistant
    message standing, per ensureAskSheetBuilt's own design comment."""
    out = _node(_script("""
openAskSheet('%s', null, '%s');
askConversationStarted = true; // what sendAskMessage sets on a real send
addAskMessage('assistant', 'Here\\u2019s what I found.'); // the real reply
openAskSheet(); // reopened elsewhere afterwards
console.log(JSON.stringify({ greeting: lastAssistantAskText }));
""" % (RECIPES_PREFILL, RECIPES_GREETING)))
    assert out["greeting"] == 'Here’s what I found.', (
        "a thread that actually had a reply must not be reset by a later open"
    )


@_needs_node
def test_an_example_chip_tapped_under_a_preset_keeps_the_preset_greeting():
    """Found in review: the example chips call openAskSheet() on their way
    to sending, while the sheet is already open. The preset greeting on
    screen is the one being answered, so it must not flash back to the
    default before the chip's message goes."""
    out = _node(_script("""
openAskSheet('%s', null, '%s');
askSheetOpen = true; // the sheet is up
openAskSheet(); // what the example-chip handler does before sendAskMessage
console.log(JSON.stringify({ greeting: lastAssistantAskText }));
""" % (RECIPES_PREFILL, RECIPES_GREETING)))
    assert out["greeting"] == RECIPES_GREETING
