"""
Named one-tap intents in the ask sheet (Loop Board: "Ask sheet: offer named
one-tap intents instead of only a blank box", Emily 2026-09-10).

A blank box makes a household guess the magic words. Four real jobs, said
the way a person says them, teach the app's range in one glance. Emily
decided the set and the wording; this file's job is to keep both honest.

The tests mostly RUN shell.js's own functions under node rather than reading
the source for a marker — the pattern from tests/test_coaching.py, and for
the same reason. Two of the three things this card promises ("tapping runs
it immediately", "the chips make sense before the network answers") are
behaviour, and a source-marker test cannot see either.

Every user-facing string here is asserted verbatim, because Emily approved
these four lines specifically. If the copy is deliberately reworded, change
the constant in the same commit and say so — do not delete the test.
"""
from __future__ import annotations

import json
import shutil

import nodeharness
from pathlib import Path

import pytest


REPO = Path(__file__).resolve().parent.parent
SHELL_JS = (REPO / "static" / "shell.js").read_text(encoding="utf-8")
SHELL_CSS = (REPO / "static" / "shell.css").read_text(encoding="utf-8")


# The four, exactly as Emily wrote them on the card.
THE_FOUR = [
    "Plan the rest of my week",
    "What should I cook tonight?",
    "Swap tonight for something quicker",
    "What do I need to defrost?",
]


_needs_node = pytest.mark.skipif(
    shutil.which("node") is None, reason="node is needed to execute the shell's own functions"
)


def _node(script: str):
    res = nodeharness.run_node(script, timeout=30)
    assert res.returncode == 0, f"node failed: {res.stderr}"
    return json.loads(res.stdout.strip())


def _slice(start: str, end: str) -> str:
    a = SHELL_JS.index(start)
    b = SHELL_JS.index(end, a)
    return SHELL_JS[a:b]


def _intents_block() -> str:
    """ASK_INTENTS and loadQuickActionChips, which is the whole change."""
    return _slice(
        "var ASK_INTENTS = [",
        "  // ---------- A dish name in a chat reply is a link too ----------",
    )


def _render_block() -> str:
    return _slice(
        "  function renderAskChips(actions) {",
        '  // "core loop handoffs, slice 2" item B',
    )


def _examples_block() -> str:
    return _slice("  function renderAskExamples(prompts) {", "\n\n", )


# The coaching examples and the intents share the desktop Ask column and
# nothing else. This stub is the two surfaces side by side so the yielding
# rule can be run rather than read.
_EXAMPLES_STUB = """
const EL = {};
['ask-chips','today-ask-chips','ask-examples','today-ask-examples'].forEach(function (id) {
  EL[id] = { id: id, innerHTML: '', hidden: true, children: [],
             querySelectorAll: function () { return []; } };
});
const document = { getElementById: function (id) { return EL[id] || null; } };
function coachExampleTargets() { return [EL['ask-examples'], EL['today-ask-examples']]; }
function fillIntents(id) { EL[id].innerHTML = '<button>x</button>'; EL[id].hidden = false; }
function state() {
  return { dock: !EL['ask-examples'].hidden, column: !EL['today-ask-examples'].hidden };
}
"""


def _run_examples(tail: str):
    return _node(
        "function escapeHtml(s){return String(s);}\n"
        + _EXAMPLES_STUB
        + "function openAskSheet(){} function sendAskMessage(){}\n"
        + _examples_block()
        + tail
    )


# Two chip containers, because the shell renders this row twice — inside the
# ask SHEET (`#ask-chips`, the phone) and in the desktop Ask column
# (`#today-ask-chips`) — and askChipTargets feeds both. Not the dock: that is
# `#ask-bar-dock`, which holds the coaching examples, and the distinction
# matters because the intents are only on screen once the sheet is open. The
# stub reads its own markup back with a regex rather than parsing it, which
# is the point: the markup is what a browser would be handed.
_DOM_STUB = """
function escapeHtml(s){return String(s == null ? '' : s)
  .replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');}
function makeEl() {
  return {
    innerHTML: '', hidden: true, handlers: {},
    querySelectorAll: function () {
      const el = this, out = [], re = /data-i="(\\d+)"/g;
      let m;
      while ((m = re.exec(el.innerHTML)) !== null) {
        (function (i) {
          out.push({ dataset: { i: String(i) },
                     addEventListener: function (_evt, fn) { el.handlers[i] = fn; } });
        })(Number(m[1]));
      }
      return out;
    },
    labels: function () {
      const out = [], re = />([^<]+)<\\/button>/g;
      let m;
      while ((m = re.exec(this.innerHTML)) !== null) out.push(m[1]);
      return out;
    }
  };
}
const SHEET = makeEl(), COLUMN = makeEl();
function askChipTargets() { return [SHEET, COLUMN]; }
const SENT = [], OPENED = [], INDEXED = [];
function sendAskMessage(t) { SENT.push(t); }
function openAskSheet(p) { OPENED.push(p === undefined ? null : p); }
function setDishIndex(m) { INDEXED.push(m); }
"""


def _fetch_stub(*, ok: bool = True, rejects: bool = False) -> str:
    """/api/week-menu, answered on a later turn of the event loop — so a test
    can look at the chips in the gap and see whether they waited."""
    if rejects:
        body = "return Promise.reject(new Error('offline'));"
    elif ok:
        body = "return Promise.resolve({ ok: true, json: function () { return { weekly_plan_id: 7 }; } });"
    else:
        body = "return Promise.resolve({ ok: false });"
    return "const FETCHED = [];\nfunction fetch(u) { FETCHED.push(u); " + body + " }\n"


def _run(tail: str, *, ok: bool = True, rejects: bool = False):
    return _node(
        _DOM_STUB
        + _fetch_stub(ok=ok, rejects=rejects)
        + _render_block()
        + _intents_block()
        + tail
    )


# --- 1. the four, and nothing hidden behind them --------------------------


@_needs_node
def test_the_ask_sheet_offers_emilys_four_intents_in_her_order():
    assert _run("loadQuickActionChips();\nconsole.log(JSON.stringify(SHEET.labels()));") == THE_FOUR


@_needs_node
def test_a_chip_sends_its_own_label_word_for_word():
    """The label IS the message. A chip carrying a sentence the household
    cannot see is one nobody can learn from, and teaching what you're
    allowed to say is this card's whole job."""
    out = _run(
        "loadQuickActionChips();\n"
        "Object.keys(SHEET.handlers).forEach(function (i) { SHEET.handlers[i](); });\n"
        "console.log(JSON.stringify(SENT));"
    )
    assert out == THE_FOUR


@_needs_node
def test_tapping_an_intent_runs_it_and_never_merely_prefills_the_box():
    """Acceptance criterion, and the one the old pair broke: "Add … to the
    grocery list" focused the composer instead of doing anything."""
    out = _run(
        "loadQuickActionChips();\n"
        "Object.keys(SHEET.handlers).forEach(function (i) { SHEET.handlers[i](); });\n"
        "console.log(JSON.stringify({ sent: SENT.length, opened: OPENED }));"
    )
    assert out == {"sent": 4, "opened": []}


@_needs_node
def test_no_intent_pre_fills_the_composer_any_more():
    """Nothing produces a `prefill` any more, so renderAskChips' branch for
    it is gone with the chip it was written for. Asserted on the intents
    themselves rather than on the file, because the comment recording why
    that chip went is worth keeping and would match a source-text check."""
    out = _run(
        "console.log(JSON.stringify(ASK_INTENTS.map(function (q) {\n"
        "  return { prefill: !!q.prefill, onClick: !!q.onClick, msg: q.msg };\n"
        "})));"
    )
    assert out == [{"prefill": False, "onClick": False, "msg": label} for label in THE_FOUR]


# --- 2. fixed: the same four, every visit, every tab ----------------------


@_needs_node
def test_both_chip_rows_get_the_same_four():
    """The shell draws this row twice — inside the ask sheet on a phone, and
    in the desktop Ask column. They cannot disagree about what the app can
    do."""
    out = _run("loadQuickActionChips();\nconsole.log(JSON.stringify([SHEET.labels(), COLUMN.labels()]));")
    assert out == [THE_FOUR, THE_FOUR]


@_needs_node
def test_the_four_do_not_change_between_visits():
    """Emily's call: fixed now, context-aware later. A set that moved
    underneath the household would tell us nothing about which intents get
    tapped, which is the reason the fixed set ships first."""
    out = _run(
        "loadQuickActionChips();\n"
        "const first = SHEET.labels();\n"
        "loadQuickActionChips();\n"
        "console.log(JSON.stringify([first, SHEET.labels()]));"
    )
    assert out[0] == out[1] == THE_FOUR


# --- 3. they no longer wait on, or depend on, the network -----------------


@_needs_node
def test_the_chips_are_up_before_the_plan_fetch_answers():
    """They used to be rendered inside the fetch's .then, so on a slow
    connection the ask sheet opened with an empty row where the suggestions
    should be. Read SYNCHRONOUSLY, on the same turn as the call — the fetch
    has gone out and its .then has not run, which is the exact gap the old
    code rendered nothing in. (Distinct from the order test above, which
    only says the four are eventually right.)"""
    out = _run(
        "loadQuickActionChips();\n"
        "const now = { labels: SHEET.labels(), fetched: FETCHED.length, indexed: INDEXED.length };\n"
        "console.log(JSON.stringify(now));"
    )
    assert out == {"labels": THE_FOUR, "fetched": 1, "indexed": 0}


@_needs_node
def test_a_failed_plan_fetch_leaves_the_four_standing():
    """The old code degraded to a guess ("Plan my week") when the fetch
    failed — a wrong suggestion rather than none. Now a dropped request
    costs the dish links in replies and nothing else."""
    tail = (
        "loadQuickActionChips();\n"
        "setTimeout(function () {\n"
        "  console.log(JSON.stringify({ labels: SHEET.labels(), indexed: INDEXED.length }));\n"
        "}, 0);"
    )
    assert _run(tail, rejects=True) == {"labels": THE_FOUR, "indexed": 0}
    assert _run(tail, ok=False) == {"labels": THE_FOUR, "indexed": 0}


@_needs_node
def test_the_plan_is_still_fetched_for_the_dish_index():
    """The fetch is not dead weight: /api/week-menu is what setDishIndex
    reads, so a reply naming a dish can link it without a request of its
    own. Removing it with the chip logic would have broken that silently."""
    out = _run(
        "loadQuickActionChips();\n"
        "setTimeout(function () {\n"
        "  console.log(JSON.stringify({ urls: FETCHED, indexed: INDEXED }));\n"
        "}, 0);"
    )
    assert out["urls"] == ["/api/week-menu"]
    assert out["indexed"] == [{"weekly_plan_id": 7}]


# --- 4. the voice, and the blank box that stays -------------------------


@_needs_node
def test_the_intents_carry_no_exclamation_marks_and_nothing_cute():
    """DESIGN_SYSTEM.md §8. Each line is a job, said plainly.

    Read off the chips shell.js RENDERS, not off THE_FOUR — asserting a
    Python literal against itself is a test no product change can turn red,
    which is what the first version of this did."""
    rendered = _run("loadQuickActionChips();\nconsole.log(JSON.stringify(SHEET.labels()));")
    assert rendered, "no chips rendered at all"
    for line in rendered:
        assert "!" not in line, line
        assert line == line.strip(), line
        # §8's "never a dashboard/task-manager register" — a job a person
        # says, not a feature name or a category label.
        assert not line.endswith(":"), line
        assert line[0].isupper(), line


def test_four_is_the_whole_set_not_a_wall():
    """The card's own target is 3-4. A long list is the blank box's problem
    wearing a different hat."""
    block = _intents_block()
    body = block[block.index("["):block.index("].map(")]
    assert body.count("'") == len(THE_FOUR) * 2


def test_the_blank_input_is_untouched():
    """The intents are an addition, never a replacement — the composer is
    exactly as it was. (Its hint became one line for every tab on
    2026-09-11, when the chat became an icon; see test_chat_icon.)"""
    assert "var ASK_HINTS = {" in SHELL_JS
    assert "id=\"ask-input\"" in (REPO / "static" / "shell.html").read_text(encoding="utf-8")


def test_the_chips_still_meet_the_44px_floor_and_wrap():
    """Four chips do not fit one phone row, so the row has to wrap rather
    than scroll sideways — DESIGN_SYSTEM.md rule 6 and the no-sideways-
    scroll rule. Both were already true of .ask-chip; this pins them now
    that the row is twice as long."""
    chips_rule = SHELL_CSS[SHELL_CSS.index(".ask-chips {"):SHELL_CSS.index(".ask-chip:hover")]
    assert "flex-wrap: wrap" in chips_rule
    assert "min-height: 44px" in chips_rule


def test_this_card_ships_no_new_backend():
    """Every intent maps to tools Pomona already has — the chips send a
    sentence through the chat turn that was already there. The only route
    this code names is the one it already named."""
    block = _intents_block()
    assert block.count("fetch(") == 1
    assert "'/api/week-menu'" in block


# --- 5. the coaching examples and the intents don't stack ----------------


@_needs_node
def test_the_desktop_column_drops_its_coaching_examples_when_the_intents_are_up():
    """Two teaching rows in one 347px-wide column is 348px of chips, and it
    pushed the composer off a 1280x900 screen — measured, composer bottom
    857 before the intents landed and 961 after. The examples were a
    three-visit stand-in for exactly what the intents now say permanently
    (COACH_EXAMPLES even carries "I'm short on time tonight", which is
    "Swap tonight for something quicker" in other words), so they yield."""
    out = _run_examples(
        "fillIntents('today-ask-chips');\n"
        "renderAskExamples(['a', 'b']);\n"
        "console.log(JSON.stringify(state()));"
    )
    assert out["column"] is False


@_needs_node
def test_the_phones_examples_yield_too_now_that_both_live_in_the_sheet():
    """Until 2026-09-11 this test pinned the opposite: the phone's examples
    sat in the ask-bar dock while the intents sat in the sheet, so a guard
    that read the sheet's chips hid the dock's examples permanently. The
    chat became an icon that day (Build 1 of the screen-by-screen
    redesign) and the examples moved INTO the sheet, beside the intents —
    so the same yielding now holds at both widths, for the same reason it
    held on desktop: two teaching rows in one place, saying the same things."""
    out = _run_examples(
        "fillIntents('ask-chips');\n"
        "renderAskExamples(['a', 'b']);\n"
        "console.log(JSON.stringify(state()));"
    )
    assert out["dock"] is False


@_needs_node
def test_the_column_still_shows_examples_when_no_intents_are_up():
    """The examples yield to the intents, they are not switched off — after
    the household's first message the intents are gone and this row is the
    per-tab teaching aid again, exactly as before."""
    out = _run_examples("renderAskExamples(['a', 'b']);\nconsole.log(JSON.stringify(state()));")
    assert out == {"dock": True, "column": True}
