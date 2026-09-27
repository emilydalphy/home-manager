"""
"Plan next week" on a draft (Emily, 2026-09-27), run against shell.js's
own renderers under node.

A draft week on the Plan root had no way to plan the week after it — only
the band's Re-plan (this week) and the dock's Approve. "Plan next week"
lived only in the approved week's dock (#wk-plan-next). Now a draft's band
carries it beside Re-plan, on their own row under the brand row (at 375px
the two pills do not fit beside the brand, the Draft chip, the bell and
the gear):

  * same words and same target as the approved dock's button — one
    planNextLabel over nextPeriodFor for the words, one planNextWeek for
    the tap;
  * a secondary on spruce (--spruce-raised + --ivory-ink), never apricot:
    the dock's Approve stays the screen's one (rule 5); Re-plan keeps its
    look;
  * drafts only — an approved week keeps Re-plan top right and "Plan next
    week" in its dock; a Plan with nothing on it has no band extras.
"""
from __future__ import annotations

import json
import re
import shutil
from pathlib import Path

import pytest

from test_plan_cards_2026_09_18 import _run, _week, _draft, _approved
from test_week_seven_tiles import _extract
from test_draft_front_door import _band_prelude, _BAND_DOM

REPO = Path(__file__).resolve().parent.parent
SHELL_JS = (REPO / "static" / "shell.js").read_text(encoding="utf-8")
SHELL_CSS = (REPO / "static" / "shell.css").read_text(encoding="utf-8")

_needs_node = pytest.mark.skipif(shutil.which("node") is None, reason="node is needed to run the renderers")

_NEXT = {"start_date": "2026-09-28", "day_count": 7, "is_current_period": False, "is_planned": False}


def _rule(selector: str) -> str:
    start = SHELL_CSS.index(selector + " {")
    return SHELL_CSS[start:SHELL_CSS.index("}", start)]


@_needs_node
def test_a_draft_offers_plan_next_week_and_an_approved_or_empty_plan_does_not_in_the_band():
    days = _week()
    out = _run(_band_prelude() + f"""
console.log(JSON.stringify({{
  draft: weekBandExtras({json.dumps(dict(_draft(days), next_period=_NEXT))}),
  set: weekBandExtras({json.dumps(dict(_approved(days), next_period=_NEXT))}),
  none: weekBandExtras({{}})
}}));""")
    assert out["draft"]["next"] == "Plan next week"
    assert out["draft"]["pill"] is True, "Re-plan stays on the draft"
    assert out["set"]["next"] is None, "the approved week keeps it in the dock"
    assert out["none"] is None, "the nothing-planned state is unchanged"


@_needs_node
def test_the_label_follows_the_servers_next_period_like_the_dock_does():
    days = _week()
    planned = dict(_NEXT, is_planned=True)
    short = {"start_date": "2026-09-24", "day_count": 3, "is_current_period": False, "is_planned": False}
    out = _run(_band_prelude() + f"""
console.log(JSON.stringify([
  weekBandExtras({json.dumps(dict(_draft(days), next_period=planned))}).next,
  weekBandExtras({json.dumps(dict(_draft(days), next_period=short))}).next,
  planNextLabel({json.dumps(planned)})
]));""")
    assert out[0] == "Re-plan next week", "next week already has a plan — planEntryLabel's own wording"
    assert out[1] == "Plan the 3 after"
    assert out[0] == out[2]
    # One label builder for both buttons: the approved dock uses it too.
    assert "escapeHtml(planNextLabel(next))" in _extract("weekDecideHtml", SHELL_JS)


@_needs_node
def test_on_a_draft_both_buttons_share_a_row_under_the_brand_and_plan_next_opens_the_next_period():
    days = _week()
    data = dict(_draft(days), period_start_date="2026-09-21", week_start_date="2026-09-21", day_count=7,
                next_period=_NEXT)
    out = _run(_band_prelude() + _BAND_DOM + f"""
weekState.data = {json.dumps(data)};
var b = makeBand();
fillWeekBandExtras({{}}, b.slot, weekBandExtras(weekState.data));
b.planNext.handlers.click();
b.pill.handlers.click();
console.log(JSON.stringify({{ tools: b.tools.html, top: b.top.html, calls: CALLS }}));""")
    assert out["tools"] == "", "on a draft nothing is added beside the gear"
    row = out["top"]
    assert row.startswith('|afterend:<div class="wk-band-actions"><button type="button" class="wk-replan" id="wk-replan"')
    assert re.search(r'<button type="button" class="wk-band-next" id="wk-band-plan-next">Plan next week</button></div>$', row)
    assert row.index('id="wk-replan"') < row.index('id="wk-band-plan-next"'), "Re-plan first, then Plan next week"
    assert out["calls"] == [["2026-09-28", 7], ["2026-09-21", 7]], "next period, then this week's own re-plan"


@_needs_node
def test_without_next_period_the_draft_button_falls_back_to_the_day_after_the_plan():
    days = _week()
    data = dict(_draft(days), period_start_date="2026-09-21", week_start_date="2026-09-21", day_count=5)
    out = _run(_band_prelude() + _BAND_DOM + f"""
weekState.data = {json.dumps(data)};
var b = makeBand();
fillWeekBandExtras({{}}, b.slot, weekBandExtras(weekState.data));
b.planNext.handlers.click();
console.log(JSON.stringify(CALLS));""")
    assert out == [["2026-09-26", 5]]


@_needs_node
def test_an_approved_week_keeps_re_plan_top_right_and_no_band_plan_next():
    days = _week()
    data = dict(_approved(days), next_period=_NEXT)
    out = _run(_band_prelude() + _BAND_DOM + f"""
weekState.data = {json.dumps(data)};
var b = makeBand();
fillWeekBandExtras({{}}, b.slot, weekBandExtras(weekState.data));
console.log(JSON.stringify({{ band: b.band.html, top: b.top.html }}));""")
    assert out["top"] == ""
    assert "wk-band-plan-next" not in out["band"] and "wk-band-actions" not in out["band"]
    assert "|tools:<button type=\"button\" class=\"wk-replan\"" in out["band"]


def test_both_plan_next_buttons_share_one_tap():
    wiring = _extract("fillWeekBandExtras", SHELL_JS)
    assert "planNext.addEventListener('click', function () { planNextWeek(); })" in wiring
    assert "next.addEventListener('click', function () { planNextWeek(); })" in SHELL_JS
    assert SHELL_JS.count("{ planNextWeek(); }") == 2


def test_plan_next_on_the_band_is_a_spruce_secondary_with_a_44px_target_and_never_apricot():
    rule = _rule(".wk-band-next")
    assert "background: var(--spruce-raised)" in rule and "color: var(--ivory-ink)" in rule
    assert "apricot" not in rule, "the dock's Approve is the screen's one apricot"
    assert "height: 36px" in rule
    assert '.wk-band-next::before { content: ""; position: absolute; inset: -4px 0; }' in SHELL_CSS
    assert re.search(r"#[0-9a-fA-F]{3,6}\b", rule) is None, "every colour goes through a token"
    row = _rule(".wk-band-actions")
    assert "display: flex" in row and "flex-wrap: wrap" in row
    # Re-plan keeps its look.
    replan = _rule(".wk-replan")
    assert "background: var(--apricot-light)" in replan and "height: 36px" in replan
