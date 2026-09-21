"""After the plan, one thing at a time (Emily, 2026-09-13 and 2026-09-18).

On her phone, over All set (2026-09-13): "After you have the plan, make the
loop easier to go and see the week and not go straight to the list. but
make the CTA to go to the list enticing." Then on 2026-09-18 (board 11):
All set is ONE thing — the tick, one line, two numbers, one button, "Next ·
Anything in the freezer?" — and the week is checked from Plan's own root
(Check the week, via More) rather than from a second door here.

What this file still pins: the approved week's review docks "Open grocery
list" and goes the freezer-or-list road, the Shop list's dock carries "See
the week" beside "Start the trip", and leaving Plan by any door ends the
All set screen (and, since 2026-09-18, the freezer step). Behaviour runs
under node against shell.js's own functions where it can.
"""
from __future__ import annotations

import json
import re
import shutil
from pathlib import Path

import pytest

import nodeharness
from test_contrast import contrast
from shop_harness import run as _gro_node
from test_week_seven_tiles import _extract, _extract_var

REPO = Path(__file__).resolve().parent.parent
SHELL_JS = (REPO / "static" / "shell.js").read_text(encoding="utf-8")
SHELL_CSS = (REPO / "static" / "shell.css").read_text(encoding="utf-8")
THEME = (REPO / "static" / "theme.css").read_text(encoding="utf-8")
DESIGN = (REPO / "DESIGN_SYSTEM.md").read_text(encoding="utf-8")

_needs_node = pytest.mark.skipif(
    shutil.which("node") is None, reason="node is needed to execute the screen's own functions"
)


def _run(harness: str):
    res = nodeharness.run_node(harness, timeout=30)
    assert res.returncode == 0, f"node failed: {res.stderr}"
    return json.loads(res.stdout.strip())


def _rule(selector: str) -> str:
    start = SHELL_CSS.index(selector + " {")
    return SHELL_CSS[start:SHELL_CSS.index("}", start)]


def _tokens(block: str) -> dict:
    return {m.group(1): m.group(2).strip() for m in re.finditer(r"^\s*(--[\w-]+):\s*([^;]+);", block, re.MULTILINE)}


def _light_and_dark():
    """theme.css's :root values, and the same names as the dark block
    overrides them — a token the dark block leaves alone keeps its light
    value there, exactly as the cascade does."""
    dark_at = THEME.index("\n@media (prefers-color-scheme: dark)")
    light = _tokens(THEME[:dark_at])
    dark = dict(light)
    dark.update(_tokens(THEME[dark_at:]))
    return light, dark


# The All set screen and the review dock, with only what they read.
_PLAN_PRELUDE = (
    _extract("escapeHtml", SHELL_JS) + "\n"
    + "var READY_CHECK = '<svg></svg>';\n"
    + "function periodRangeLabel(start, n) { return start + ' +' + n; }\n"
    + "function countOpenSlots() { return 0; }\n"
    + "function approveWithOpenLabel() { return 'Approve anyway'; }\n"
    + "var defrostAskState = { planId: null, items: null, selected: {} };\n"
    + _extract("weekPlanState", SHELL_JS) + "\n"
    + _extract("allSetStepHtml", SHELL_JS) + "\n"
    + _extract("reviewDecideHtml", SHELL_JS) + "\n"
)

_APPROVED = {
    "weekly_plan_id": 7, "status": "approved", "days": [{"date": "2026-09-14"}],
    "week_label": "Sep 14–20", "defrost_asked_at": None,
    "receipt": {"meals": 6, "recipes": 5, "cooks": 6, "list_count": 53, "thaw_line": ""},
}


# ---------- All set: one door ----------


@_needs_node
def test_all_set_has_one_apricot_and_it_goes_to_the_freezer_question():
    html = _run(_PLAN_PRELUDE + f"console.log(JSON.stringify(allSetStepHtml({json.dumps(_APPROVED)}, [])));")
    dock = html[html.index('<div class="dock wk-allset-dock">'):]
    assert 'class="dock-primary" id="wk-allset-next">Next · Anything in the freezer?</button>' in dock
    assert dock.count("dock-primary") == 1, "one apricot (Rule 5)"
    assert "dock-secondary" not in dock and "See the week" not in html and "dock-link" not in dock


@_needs_node
def test_the_dock_reads_open_grocery_list_when_there_is_nothing_to_ask():
    answered = dict(_APPROVED, defrost_asked_at="2026-09-14T10:00")
    html = _run(_PLAN_PRELUDE + f"console.log(JSON.stringify(allSetStepHtml({json.dumps(answered)}, [])));")
    assert 'id="wk-allset-next">Open grocery list</button>' in html


def test_the_dock_secondary_went_with_see_the_week():
    """All set's sand "See the week" over the apricot was the only
    .dock-secondary; both are gone (2026-09-18). Rule 5 still holds on the
    dock that is left."""
    assert ".dock-secondary" not in SHELL_CSS and "dock-secondary" not in SHELL_JS
    light, dark = _light_and_dark()
    for mode, tokens in (("light", light), ("dark", dark)):
        ratio = contrast(tokens["--on-accent-ink"], tokens["--apricot"])
        assert ratio >= 4.5, f"--on-accent-ink on --apricot is {ratio:.2f}:1 in {mode}"


# ---------- From the week, the list is one tap ----------


@_needs_node
def test_an_approved_week_docks_open_grocery_list_where_approve_was():
    out = _run(
        _PLAN_PRELUDE
        + f"var set = reviewDecideHtml({json.dumps(_APPROVED)});\n"
        + "var draft = reviewDecideHtml({ weekly_plan_id: 7, status: 'draft', days: [{}] });\n"
        + "var none = reviewDecideHtml({});\n"
        + "console.log(JSON.stringify({ set: set, draft: draft, none: none }));"
    )
    assert out["set"].startswith('<div class="wk-decide dock">')
    assert 'id="wk-review-go">Open grocery list</button>' in out["set"]
    assert out["set"].count("dock-primary") == 1 and "week-approve-btn" not in out["set"]
    assert 'id="week-approve-btn">Approve · Open grocery list</button>' in out["draft"]
    assert "wk-review-go" not in out["draft"]
    assert out["none"] == ""
    # One dock on the review, whichever state it is in (test_nav_v2_back_and_dock).
    assert SHELL_JS.count('<div class="wk-decide dock">') == 1


def test_the_review_dock_goes_the_freezer_or_list_road_all_set_goes():
    wire = SHELL_JS[SHELL_JS.index("  function wireMealsStep("):]
    wire = wire[:wire.index("\n  }\n")]
    assert "steps.querySelector('#wk-review-go')" in wire
    assert "goAfterWeekSet(panel, weekState.data || {});" in wire
    road = _extract("goAfterWeekSet", SHELL_JS)
    assert "goMealsStep('freezer')" in road and "goGroceryList()" in road
    assert "activateTab('grocery', true, { groScreen: 'plan' });" in _extract("goGroceryList", SHELL_JS)


# ---------- From the list, the week is one tap ----------


@_needs_node
def test_the_shop_root_has_no_dock_and_no_see_the_week_link():
    """Until 2026-09-18 the list's dock read "Start the trip" with "See the
    week" as its quiet link. The list is the checklist now: the root has
    no dock (ticking a row is the action) and the week is one tap away on
    the tab bar, so the link went with the trip."""
    out = _gro_node("""
setUp(0, [{ store: 'Costco', items: [{ id: 1, item: 'Rice', quantity: '1', store: 'Costco', store_decided: 1, status: 'needed' }] }]);
console.log(JSON.stringify(groDockHtml(groceryState.data, 'list')));
""")
    assert out == ""
    assert 'data-gro="see-week"' not in SHELL_JS
    assert 'data-gro="start-trip"' not in SHELL_JS


# ---------- Leaving Plan ends the All set screen ----------


def _activate_tab_harness(step: str, leave_for: str) -> str:
    return (
        "var TABS = [{ key: 'today', path: '/', real: true }, { key: 'week', path: '/week', week: true },"
        " { key: 'grocery', path: '/grocery', grocery: true }, { key: 'kitchen', path: '/kitchen', kitchen: true }];\n"
        "function el() { return { classList: { toggle: function () {}, contains: function () { return false; } },"
        " dataset: { built: '1' } }; }\n"
        "var panels = { today: el(), week: el(), grocery: el(), kitchen: el() };\n"
        "var document = { querySelectorAll: function () { return []; } };\n"
        "var window = { location: { pathname: '/week' }, history: { pushState: function () {}, replaceState: function () {} } };\n"
        "var cookState = { focusOrigin: null };\n"
        "var scrollEl = null;\n"
        "function closeKitchenSheet() {} function stopCookVoice() {} function groLeaveScreen() {} function animateTabPanelIn() {}\n"
        "function setAskHintForTab() {} function coachOnTabShown() {} function groSetScreen() {}\n"
        "function buildTodayPanel() {} function buildWeekPanel() {} function buildGroceryPanel() {} function buildKitchenPanel() {}\n"
        "function kitchenEnterCook() {} function currentTabKey() { return 'week'; }\n"
        f"var weekState = {{ step: {json.dumps(step)}, data: {{ weekly_plan_id: 7, status: 'approved' }} }};\n"
        "var RENDERS = [];\n"
        "function renderMealsStep(panel) { RENDERS.push(weekState.step); }\n"
        + _extract("activateTab", SHELL_JS)
        + f"\nactivateTab({json.dumps(leave_for)}, true);\n"
        "console.log(JSON.stringify({ step: weekState.step, renders: RENDERS }));"
    )


@_needs_node
@pytest.mark.parametrize("door", ["grocery", "today", "kitchen"])
@pytest.mark.parametrize("finish", ["allset", "freezer"])
def test_leaving_plan_from_a_finish_screen_by_any_door_folds_it_to_the_week(door, finish):
    """"Open the list" is activateTab('grocery'); the tab bar is the other
    three. Either way, the next look at Plan is the week, not the
    finished-planning screen — re-rendered while hidden, so nothing moves
    under a thumb. The freezer step (2026-09-18) is a finish screen too."""
    out = _run(_activate_tab_harness(finish, door))
    assert out == {"step": "week", "renders": ["week"]}


@_needs_node
def test_a_deeper_plan_step_is_left_alone_when_leaving_and_all_set_when_staying():
    """Only All set is one-shot. A day or a meal open on Plan stays open
    across a trip to Shop and back (the refresh policy: nothing reloads on a
    tab switch), and re-activating Plan itself never folds anything."""
    out = _run(_activate_tab_harness("meal", "grocery"))
    assert out == {"step": "meal", "renders": []}
    out = _run(_activate_tab_harness("allset", "week"))
    assert out == {"step": "allset", "renders": []}


# ---------- What went ----------


def test_the_root_receipt_card_is_gone():
    """The approved week's receipt card (Open the list / See the week over
    the week card) went on 2026-09-18 with the asks it carried: the root
    has no apricot now, and the freezer question is a step of its own."""
    assert "renderWeekReceipt" not in SHELL_JS and "week-receipt-go" not in SHELL_JS
    assert ".week-receipt-card" not in SHELL_CSS
