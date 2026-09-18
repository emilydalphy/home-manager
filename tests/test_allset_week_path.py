"""After the plan, seeing the week is a first-class path — with the list
still the enticing next step (Emily, 2026-09-13).

On her phone, over All set: "After you have the plan, make the loop easier
to go and see the week and not go straight to the list. but make the CTA
to go to the list enticing."

What was there: "See the week" as a 13px text link above a full-width
"Open the list"; the approved week's tiles with no way to the list but the
tab bar; the Shop list with no way to the week but the tab bar; and All
set staying on the Plan tab after "Open the list" — come back by the tab
bar and the finished-planning screen is still there, because nothing but
"See the week" ever moved weekState.step off 'allset'.

Now: "See the week" is a real secondary button (never a second apricot —
Rule 5), "Open the list · 53 ingredients" keeps the pull, the approved
week's review step docks "Open the list", the Shop list's dock carries
"See the week" beside "Start the trip", and leaving Plan by any door ends
the All set screen. Behaviour runs under node against shell.js's own
functions where it can (the way tests/test_shop_trip_exit.py does).
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

import nodeharness
from test_contrast import contrast
from test_prep_questions_step import _light_and_dark
from shop_harness import run as _gro_node
from test_week_seven_tiles import _extract, _extract_var

REPO = Path(__file__).resolve().parent.parent
SHELL_JS = (REPO / "static" / "shell.js").read_text(encoding="utf-8")
SHELL_CSS = (REPO / "static" / "shell.css").read_text(encoding="utf-8")
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


# The All set screen and the review dock, with only what they read.
_PLAN_PRELUDE = (
    _extract("escapeHtml", SHELL_JS) + "\n"
    + "var READY_CHECK = '<svg></svg>';\n"
    + "function periodRangeLabel(start, n) { return start + ' +' + n; }\n"
    + "function countOpenSlots() { return 0; }\n"
    + "function approveWithOpenLabel() { return 'Approve anyway'; }\n"
    + _extract("weekPlanState", SHELL_JS) + "\n"
    + _extract("openListLabel", SHELL_JS) + "\n"
    + _extract("allSetStepHtml", SHELL_JS) + "\n"
    + _extract("reviewDecideHtml", SHELL_JS) + "\n"
)

_APPROVED = {
    "weekly_plan_id": 7, "status": "approved", "days": [{"date": "2026-09-14"}],
    "week_label": "Sep 14–20",
    "receipt": {"meals": 6, "recipes": 5, "cooks": 6, "list_count": 53, "thaw_line": ""},
}


# ---------- All set: two doors, one apricot ----------


@_needs_node
def test_see_the_week_is_a_button_above_the_list_with_its_count():
    html = _run(_PLAN_PRELUDE + f"console.log(JSON.stringify(allSetStepHtml({json.dumps(_APPROVED)}, [])));")
    dock = html[html.index('<div class="dock wk-allset-dock">'):]
    see = dock.index('class="dock-secondary" id="wk-allset-see">See the week</button>')
    go = dock.index('class="dock-primary" id="wk-allset-go">Open the list · 53 ingredients</button>')
    assert see < go, "the week is offered first; the list is the apricot at the thumb"
    assert dock.count("dock-primary") == 1, "one apricot (Rule 5)"
    assert "dock-link" not in dock, "a real button, not a small text link"


@_needs_node
@pytest.mark.parametrize(
    "count,label",
    [(0, "Open the list"), (None, "Open the list"), (1, "Open the list · 1 ingredient"), (53, "Open the list · 53 ingredients")],
)
def test_the_list_button_says_how_many_only_when_there_are_some(count, label):
    out = _run(_PLAN_PRELUDE + f"console.log(JSON.stringify(openListLabel({{ list_count: {json.dumps(count)} }})));")
    assert out == label


def test_the_secondary_is_a_sand_button_re_inked_for_spruce_and_measured():
    base = _rule(".dock-secondary")
    assert "background: var(--sand)" in base and "min-height: 52px" in base
    assert "apricot" not in base, "never a second apricot"
    on_spruce = _rule(".wk-allset-dock .dock-secondary")
    assert "background: var(--spruce-raised)" in on_spruce
    assert "color: var(--ivory-ink)" in on_spruce
    assert "border-color: var(--apricot-rule)" in on_spruce
    light, dark = _light_and_dark()
    for mode, tokens in (("light", light), ("dark", dark)):
        for ground in ("--spruce-raised", "--spruce-hover"):
            ratio = contrast(tokens["--ivory-ink"], tokens[ground])
            assert ratio >= 4.5, f"--ivory-ink on {ground} is {ratio:.2f}:1 in {mode}"
        ratio = contrast(tokens["--ink"], tokens["--sand"])
        assert ratio >= 4.5, f"--ink on --sand is {ratio:.2f}:1 in {mode}"
    assert "`.dock-secondary`" in DESIGN, "the dock's second door is written down where the dock is"


# ---------- From the week, the list is one tap ----------


@_needs_node
def test_an_approved_week_docks_open_the_list_where_approve_was():
    out = _run(
        _PLAN_PRELUDE
        + f"var set = reviewDecideHtml({json.dumps(_APPROVED)});\n"
        + "var draft = reviewDecideHtml({ weekly_plan_id: 7, status: 'draft', days: [{}] });\n"
        + "var none = reviewDecideHtml({});\n"
        + "console.log(JSON.stringify({ set: set, draft: draft, none: none }));"
    )
    assert out["set"].startswith('<div class="wk-decide dock">')
    assert 'id="wk-review-go">Open the list · 53 ingredients</button>' in out["set"]
    assert out["set"].count("dock-primary") == 1 and "week-approve-btn" not in out["set"]
    assert 'id="week-approve-btn">Approve and build my shopping list</button>' in out["draft"]
    assert "wk-review-go" not in out["draft"]
    assert out["none"] == ""
    # One dock on the review, whichever state it is in (test_nav_v2_back_and_dock).
    assert SHELL_JS.count('<div class="wk-decide dock">') == 1


def test_the_review_dock_is_wired_to_the_same_landing_as_all_set():
    wire = SHELL_JS[SHELL_JS.index("  function wireMealsStep("):]
    wire = wire[:wire.index("\n  }\n")]
    assert "steps.querySelector('#wk-review-go')" in wire
    assert "activateTab('grocery', true, { groScreen: 'plan' });" in wire


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
        "function closeKitchenSheet() {} function stopCookVoice() {} function animateTabPanelIn() {}\n"
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
def test_leaving_plan_from_all_set_by_any_door_folds_it_to_the_week(door):
    """"Open the list" is activateTab('grocery'); the tab bar is the other
    three. Either way, the next look at Plan is the week, not the
    finished-planning screen — re-rendered while hidden, so nothing moves
    under a thumb."""
    out = _run(_activate_tab_harness("allset", door))
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


# ---------- What was already there, and stays ----------


def test_the_root_receipt_card_keeps_its_two_equal_buttons():
    """A week approved in another session opens Plan on the receipt card,
    whose "Open the list" / "See the week" were already two buttons in one
    row — the shape All set now matches. Untouched."""
    receipt = SHELL_JS[SHELL_JS.index("  function renderWeekReceipt("):]
    receipt = receipt[:receipt.index("\n  }\n")]
    assert '<button type="button" class="btn-gold week-receipt-go" id="week-receipt-go">Open the list</button>' in receipt
    assert '<button type="button" class="week-receipt-see" id="week-receipt-see">See the week</button>' in receipt
    assert "grid-template-columns: 1fr 1fr" in _rule(".week-receipt-acts")
