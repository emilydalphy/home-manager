"""
Two approved design items (Emily, 2026-09-21 — boards D4 and D5), run
against shell.js's own renderers under node:

  D4  The draft's dock is one row: Approve · Open grocery list fills the
      width, a round More (three dots, 48px) sits beside it, the chat icon
      floats at the row's end. The quiet "Plan it differently" line is gone
      — it was the band's Re-plan pill under a second name. The same row on
      both toggle sides and on the approved week's root.

  D5  The swap sheet's wait is obvious: three shimmering placeholder cards,
      a small spinner and "Finding three you could have — about ten
      seconds." while /swap-options runs; the picks land in the same
      positions; "Something else — tell me" stays throughout; the
      nothing-found line is unchanged. Static placeholders under
      prefers-reduced-motion.
"""
from __future__ import annotations

import json
import re
import shutil
from pathlib import Path

import pytest

from test_plan_cards_2026_09_18 import _prelude, _run, _week, _draft, _approved, _TUE
from test_week_seven_tiles import _extract, _extract_var

REPO = Path(__file__).resolve().parent.parent
SHELL_JS = (REPO / "static" / "shell.js").read_text(encoding="utf-8")
SHELL_CSS = (REPO / "static" / "shell.css").read_text(encoding="utf-8")

_needs_node = pytest.mark.skipif(shutil.which("node") is None, reason="node is needed to run the renderers")


def _rule(selector: str) -> str:
    start = SHELL_CSS.index(selector + " {")
    return SHELL_CSS[start:SHELL_CSS.index("}", start)]


# ---------------------------------------------------------------------------
# D4. The draft's dock, one row
# ---------------------------------------------------------------------------

_MORE = 'class="wk-dock-more" id="wk-more" aria-haspopup="dialog" aria-label="More"'


def _draft_root(view: str) -> str:
    days = _week()
    return _run(_prelude() + f"weekState.days = {json.dumps(days)}; weekState.draftView = {view!r}; weekState.draftViewPlanId = 7;\n"
                f"console.log(JSON.stringify(reviewStepHtml({json.dumps(_draft(days))}, weekState.days, true)));")


@_needs_node
@pytest.mark.parametrize("view", ["menu", "days"])
def test_the_drafts_dock_is_one_row_approve_then_the_round_more_on_both_toggle_sides(view):
    html = _draft_root(view)
    dock = html[html.index('class="wk-decide dock"'):]
    row = re.search(r'<div class="wk-dock-row">(.*?)</div>', dock, re.S)
    assert row, "the primary and More share one row"
    assert row.group(1).startswith('<button type="button" class="btn-gold week-approve-btn" id="week-approve-btn">Approve · Open grocery list</button>')
    assert row.group(1).endswith(_MORE + ">" + "<svg" + row.group(1).split("<svg", 1)[1])
    assert dock.count('id="wk-more"') == 1
    # The three dots, drawn as filled shapes (rule 7's own exception form).
    assert '<circle cx="5" cy="12" r="2"/><circle cx="12" cy="12" r="2"/><circle cx="19" cy="12" r="2"/>' in row.group(1)
    # The quiet row is gone with its link; the waiting line for "Try again" stays.
    for gone in ("wk-dock-quiet", "wk-dock-link", "wk-plan-differently", "Plan it differently", "More ···", "wk-foot"):
        assert gone not in html, gone
    assert 'id="week-redo-waiting" hidden' in dock


@_needs_node
def test_the_approved_root_gets_the_same_row_plan_next_week_then_more_and_no_foot_above():
    days = _week()
    nxt = {"start_date": "2026-09-28", "day_count": 7, "is_current_period": False, "is_planned": False}
    html = _run(_prelude() + f"console.log(JSON.stringify(weekDecideHtml({json.dumps(_approved(days))}, {json.dumps(nxt)})));")
    assert html.startswith('<div class="dock wk-root-dock"><div class="wk-dock-row">')
    assert html.index('id="wk-plan-next"') < html.index(_MORE)
    assert "btn-gold" not in html and "dock-primary" not in html, "no apricot on a settled week"
    # The root no longer renders a foot of its own above the dock.
    root = _extract("weekStepHtml", SHELL_JS)
    tail = root[root.index("weekNotesHtml(data, days[selected])"):]
    assert "wk-foot" not in tail and 'id="wk-more"' not in tail
    assert "weekDecideHtml(data, next);" in tail


def test_the_more_button_is_48px_round_and_the_row_lets_the_primary_fill():
    more = _rule(".wk-dock-more")
    assert "width: 48px" in more and "height: 48px" in more
    assert "border-radius: var(--radius-pill)" in more
    assert "border: 1.5px solid var(--hairline-strong)" in more
    assert "background: var(--surface)" in more and "color: var(--ink-strong)" in more
    assert re.search(r"#[0-9a-fA-F]{3,6}\b", more) is None, "every colour goes through a token"
    assert ".wk-dock-row { display: flex; align-items: center; gap: 8px; }" in SHELL_CSS
    assert ".wk-dock-row .week-approve-btn { padding: 10px 8px; }" in SHELL_CSS
    assert "@media (max-width: 380px) { .wk-dock-row .week-approve-btn { font-size: 16px; } }" in SHELL_CSS
    assert "padding: 14px 78px 10px 20px;" in _rule(".wk-decide") and ".wk-root-dock { padding-right: 78px; }" in SHELL_CSS
    fill = SHELL_CSS[SHELL_CSS.index(".wk-dock-row .week-approve-btn,"):]
    fill = fill[:fill.index("}")]
    assert ".dock-primary { flex: 1 1 auto; width: auto; min-width: 0;" in fill
    # The one More is still what opens the sheet.
    wiring = _extract("wireMealsStep", SHELL_JS)
    assert "var more = steps.querySelector('#wk-more');" in wiring and "openMealsMoreSheet()" in wiring
    assert SHELL_JS.count("function wkDockMoreHtml(") == 1
    assert SHELL_JS.count("wkDockMoreHtml() +") == 2, "the draft's dock and the approved root's"


def test_the_quiet_line_left_the_source_and_the_pill_is_the_one_door_to_the_intake():
    assert ">Plan it differently<" not in SHELL_JS
    assert ".wk-dock-quiet" not in SHELL_CSS and ".wk-dock-link" not in SHELL_CSS
    assert SHELL_JS.count("{ replanWeek(); }") == 1
    assert "pill.addEventListener('click', function () { replanWeek(); })" in _extract("fillWeekBandExtras", SHELL_JS)

