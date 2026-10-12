"""
A rhythm answer changed in chat re-reads Now and Cook, not only Preferences.

"We eat later now, around 7:30" runs set_dinner_window. Its card carries
href '/memory' (it falls to _categorize_tool's catch-all), and
refreshStaleTabsFromActions' href branch only dropped the Preferences
cache — so Now went on saying "Start by 4:55" until a reload while the
answer saved in Preferences re-reads Now (wwkSaveRhythm). Driven in a
browser on 2026-10-11 before this was written.

Runs shell.js's own function under node (tests/nodeharness.py), over the
card the REAL summarize_chat_actions builds.
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import nodeharness
import pytest

from app.main import summarize_chat_actions

REPO = Path(__file__).resolve().parents[1]
SHELL_JS = (REPO / "static" / "shell.js").read_text(encoding="utf-8")

pytestmark = pytest.mark.skipif(
    shutil.which("node") is None,
    reason="node is needed to execute the shell's own functions",
)

_STUB = """
var CALLS = [];
var panels = { today: { dataset: { built: '1' } }, week: { dataset: { built: '1' } } };
function refreshHolding() { CALLS.push('holding'); }
function loadWeekMenu() { CALLS.push('weekmenu'); }
function refreshTonightFromPlan() { CALLS.push('tonight'); }
function refreshDishIndex() { CALLS.push('dishindex'); }
function refreshKitchenPanel() { CALLS.push('kitchen'); }
function refreshTodayMoves() { CALLS.push('todaymoves'); }
function refreshYesterdayCheck() { CALLS.push('yesterday'); }
function hrefSheetKey(h) { return h === '/memory' ? 'memory' : null; }
function prefsInvalidate() { CALLS.push('prefs'); }
function refreshGroceryPanel() { CALLS.push('grocery'); }
function loadNeedsYou() { CALLS.push('needsyou'); }
function loadTodayMoves() { CALLS.push('todaypanel'); }
function loadChores() { CALLS.push('chores-now'); }
function loadPlanChores() { CALLS.push('chores-plan'); }
"""


def _function(name: str) -> str:
    start = SHELL_JS.index("  function %s(" % name)
    end = SHELL_JS.index("\n  }\n", start)
    return SHELL_JS[start:end] + "\n  }\n"


def _refresh(actions: list[dict]) -> list[str]:
    script = (
        _STUB + _function("refreshStaleTabsFromActions")
        + "refreshStaleTabsFromActions(%s);\n" % json.dumps(actions)
        + "console.log(JSON.stringify(CALLS));\n"
    )
    res = nodeharness.run_node(script, timeout=30)
    assert res.returncode == 0, "node failed: %s" % res.stderr
    return json.loads(res.stdout.strip().splitlines()[-1])


def _card_for(tool: str, args: dict, result: dict) -> list[dict]:
    """The card(s) the real summarize_chat_actions gives one successful call."""
    after = [
        {"role": "assistant", "content": [
            {"type": "tool_use", "name": tool, "id": "tu_1", "input": args}]},
        {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "tu_1", "content": json.dumps(result)}]},
    ]
    return [a.model_dump() for a in summarize_chat_actions([], after)]


@pytest.mark.parametrize("tool,args,result", [
    ("set_dinner_window", {"value": "later"}, {"dinner_window": "later"}),
    ("set_prep_days", {"days": [], "this_week_only": True},
     {"this_week_only": True, "skip_prep_this_week": True}),
    ("set_cooking_role", {"value": "one_person", "who": "Emily"},
     {"cooking_role": "one_person", "who": "Emily"}),
])
def test_a_rhythm_answer_from_chat_re_reads_now_and_cook(tool, args, result):
    cards = _card_for(tool, args, result)
    assert [(c["tab"], c["href"]) for c in cards] == [(None, "/memory")]
    calls = _refresh(cards)
    assert "prefs" in calls
    assert "todaymoves" in calls
    assert "kitchen" in calls


def test_a_held_thing_still_takes_its_own_branch_only():
    calls = _refresh([{"tab": None, "href": "/memory", "held": True}])
    assert calls == ["holding"]
