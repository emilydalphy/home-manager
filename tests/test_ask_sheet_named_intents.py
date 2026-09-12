"""
Named one-tap intents in the ask sheet (Loop Board: "Ask sheet: offer named
one-tap intents instead of only a blank box", Emily 2026-09-10) — RETIRED
2026-09-11 (design-tidy pass, item 15).

ASK_INTENTS was a fixed set of four chips, the same on every tab by design
("FIXED — the same four on every visit... a fixed set ships now"). That was
exactly the bug the 2026-09-11 screen-by-screen review then caught: "the
example chips are the same on every tab." The per-tab COACH_EXAMPLES trio
(shell.js) replaced it outright, reusing the mechanism that already existed
for per-tab examples (a tab's first three visits) — see
tests/test_coaching.py for that coverage, which now carries what this file
used to.

What's left here: confirming ASK_INTENTS and its always-on chip row are
actually gone (a retirement that silently regresses is worse than a feature
that never shipped), and the one thing loadQuickActionChips kept doing
regardless of the chip logic — priming the dish index off /api/week-menu so
a chat reply naming a dish can link it.
"""
from __future__ import annotations

import json
import shutil

import nodeharness
from pathlib import Path

import pytest


REPO = Path(__file__).resolve().parent.parent
SHELL_JS = (REPO / "static" / "shell.js").read_text(encoding="utf-8")
SHELL_HTML = (REPO / "static" / "shell.html").read_text(encoding="utf-8")


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


def _quick_action_chips_block() -> str:
    return _slice(
        "  function loadQuickActionChips() {",
        "  // ---------- A dish name in a chat reply is a link too ----------",
    )


# --- ASK_INTENTS is actually gone, not just unused -------------------------

def test_ask_intents_no_longer_exists():
    """The name survives in a few comments explaining what replaced it and
    why (see loadQuickActionChips, renderAskExamples, computeNextStepChips)
    — this checks the declaration itself is gone, not every mention."""
    assert "var ASK_INTENTS" not in SHELL_JS


def test_the_fixed_four_no_longer_reach_a_chip_row():
    """The lines Emily wrote for the retired set, checked as a source
    marker so this test goes red if any of the four quietly comes back as
    a literal string anywhere in the chip-rendering path — the retirement
    should be complete, not partial."""
    block = _quick_action_chips_block()
    for line in [
        "Plan the rest of my week",
        "What should I cook tonight?",
        "Swap tonight for something quicker",
        "What do I need to defrost?",
    ]:
        assert line not in block, f"{line!r} is still in loadQuickActionChips"


def test_ensure_ask_sheet_built_no_longer_renders_a_chip_row_itself():
    """loadQuickActionChips used to call renderAskChips(ASK_INTENTS) here —
    the only chip-producing call left in the whole ask-sheet build path is
    the per-tab coaching mechanism (coachOnTabShown, wired into
    activateTab), not anything run once at build time."""
    block = _quick_action_chips_block()
    assert "renderAskChips(" not in block


# --- the one thing that survived the retirement -----------------------------

@_needs_node
def test_the_plan_is_still_fetched_for_the_dish_index():
    """The fetch was never just for the chips: /api/week-menu is what
    setDishIndex reads, so a reply naming a dish can link it without a
    request of its own. Retiring the chip logic must not have taken this
    with it."""
    script = (
        "const FETCHED = [], INDEXED = [];\n"
        "function fetch(u) { FETCHED.push(u); "
        "return Promise.resolve({ ok: true, json: function () { return { weekly_plan_id: 7 }; } }); }\n"
        "function setDishIndex(m) { INDEXED.push(m); }\n"
        + _quick_action_chips_block()
        + "loadQuickActionChips();\n"
        "setTimeout(function () {\n"
        "  console.log(JSON.stringify({ urls: FETCHED, indexed: INDEXED }));\n"
        "}, 0);"
    )
    out = _node(script)
    assert out["urls"] == ["/api/week-menu"]
    assert out["indexed"] == [{"weekly_plan_id": 7}]


@_needs_node
def test_a_failed_plan_fetch_costs_only_the_dish_links():
    """No chips to fall back to any more, so there's nothing left for a
    dropped request to degrade — it just means replies won't link a dish
    name until the next successful fetch."""
    script = (
        "const INDEXED = [];\n"
        "function fetch(u) { return Promise.reject(new Error('offline')); }\n"
        "function setDishIndex(m) { INDEXED.push(m); }\n"
        + _quick_action_chips_block()
        + "loadQuickActionChips();\n"
        "setTimeout(function () { console.log(JSON.stringify({ indexed: INDEXED.length })); }, 0);"
    )
    assert _node(script) == {"indexed": 0}


def test_this_function_ships_no_new_backend():
    """Still true after the retirement: the one route this code names is
    the one it already named."""
    block = _quick_action_chips_block()
    assert block.count("fetch(") == 1
    assert "'/api/week-menu'" in block


def test_the_blank_input_is_untouched():
    """The per-tab examples are an addition to the composer, never a
    replacement — it is exactly as it was."""
    assert "var ASK_HINTS = {" in SHELL_JS
    assert 'id="ask-input"' in SHELL_HTML
