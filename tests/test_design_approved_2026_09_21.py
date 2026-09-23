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
      positions; "Ask for something else" stays throughout; the
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


# ---------------------------------------------------------------------------
# D5. The swap sheet's wait
# ---------------------------------------------------------------------------

def _sheet(st: dict) -> str:
    days = _week()
    return _run(_prelude() + f"weekState.days = {json.dumps(days)};\n"
                f"console.log(JSON.stringify(swapSheetBodyHtml({json.dumps(st)})));")


def _waiting(**over) -> dict:
    st = {"date": _TUE, "slot": "dinner", "name": "Black bean tacos", "view": "picks", "busy": False, "trouble": "", "options": None}
    st.update(over)
    return st


@_needs_node
def test_while_the_picks_are_found_the_sheet_shows_a_spinner_the_line_and_three_placeholders():
    html = _sheet(_waiting())
    assert '<div class="wk-swap-wait" role="status"><span class="wk-swap-spinner" aria-hidden="true"></span>' \
           '<p class="wk-swap-loading">Finding three you could have — about ten seconds.</p></div>' in html
    picks = re.search(r'<div class="wk-swap-picks wk-swap-picks-waiting">(.*?)</div>(?=<button)', html, re.S)
    assert picks, "the placeholders sit in the picks' own container"
    card = ('<div class="wk-swap-pick wk-swap-skel" aria-hidden="true"><span class="wk-swap-pick-text">'
            '<span class="wk-swap-skel-row"><span class="wk-swap-skel-line wk-swap-skel-name"></span></span>'
            '<span class="wk-swap-skel-row wk-swap-skel-row-why"><span class="wk-swap-skel-line wk-swap-skel-why"></span></span>'
            '</span></div>')
    assert picks.group(1) == card * 3
    assert "Finding three you could have instead…" not in html
    # The rest of the sheet is as it was: the move line, "Ask for something else".
    assert 'id="wk-swap-move">Move the tacos to another day</button>' in html
    assert html.endswith('<button type="button" class="wk-swap-else" id="wk-swap-tell">Ask for something else</button>')
    assert html.index("wk-swap-picks-waiting") < html.index("wk-swap-move") < html.index("wk-swap-tell")


@_needs_node
def test_the_picks_take_the_placeholders_places_and_nothing_found_reads_as_before():
    options = [{"index": 0, "meal": "Sausage pasta", "reason": "uses the sausages", "minutes": 30},
               {"index": 1, "meal": "Quesadillas", "reason": "same tortillas", "minutes": 25},
               {"index": 2, "meal": "Fried rice", "reason": "no shopping", "minutes": 15}]
    found = _sheet(_waiting(options=options))
    assert "wk-swap-skel" not in found and "wk-swap-wait" not in found and "wk-swap-spinner" not in found
    assert found.count('class="wk-swap-pick" data-wk-swap-pick="') == 3
    # Same container, same position in the sheet — after the title, before the move line.
    assert found.index("wk-swap-title") < found.index('<div class="wk-swap-picks">') < found.index("wk-swap-move")
    waiting = _sheet(_waiting())
    assert waiting.index("wk-swap-title") < waiting.index('<div class="wk-swap-picks wk-swap-picks-waiting">') < waiting.index("wk-swap-move")
    # Nothing to offer: the line stays what it was, with "tell me" under it.
    empty = _sheet(_waiting(options=None, trouble="Nothing I’d put there instead — tell me what you’d like."))
    assert '<p class="wk-swap-trouble">Nothing I’d put there instead — tell me what you’d like.</p>' in empty
    assert "wk-swap-skel" not in empty and "wk-swap-spinner" not in empty
    assert 'id="wk-swap-tell">Ask for something else</button>' in empty
    opened = _extract("openSwapSheet", SHELL_JS)
    assert "'Nothing I’d put there instead — tell me what you’d like.'" in opened
    assert "'I couldn’t think of options just now — tell me what you’d like instead.'" in opened


@_needs_node
def test_the_wait_line_reads_the_one_constant():
    out = _run(_extract_var("SWAP_WAIT_SECONDS", SHELL_JS) + "\n" + _extract("swapWaitLine", SHELL_JS) + "\n"
               + "var a = swapWaitLine(); SWAP_WAIT_SECONDS = 5; var b = swapWaitLine(); SWAP_WAIT_SECONDS = 45; var c = swapWaitLine();\n"
               + "console.log(JSON.stringify([a, b, c]));")
    assert out == ["Finding three you could have — about ten seconds.",
                   "Finding three you could have — about five seconds.",
                   "Finding three you could have — about 45 seconds."]
    assert "var SWAP_WAIT_SECONDS = 10;" in SHELL_JS


def test_the_placeholders_shimmer_by_css_and_sit_still_under_reduced_motion():
    line = _rule(".wk-swap-skel-line")
    assert "animation: wkSwapShimmer 1.2s linear infinite" in line
    assert "background: linear-gradient(90deg, var(--hairline) 25%, var(--ground) 50%, var(--hairline) 75%)" in line
    assert "@keyframes wkSwapShimmer { from { background-position: 200% 0; } to { background-position: -200% 0; } }" in SHELL_CSS
    spinner = _rule(".wk-swap-spinner")
    assert "width: 16px" in spinner and "height: 16px" in spinner and "box-sizing: border-box" in spinner
    assert ".wk-swap-wait .wk-swap-loading { flex: 1 1 auto; min-width: 0; font-size: 13px; }" in SHELL_CSS
    assert "border: 2.5px solid var(--apricot)" in spinner and "border-right-color: transparent" in spinner
    assert "animation: wkSwapSpin .9s linear infinite" in spinner
    assert "@keyframes wkSwapSpin { to { transform: rotate(360deg); } }" in SHELL_CSS
    # The reduced-motion rule, where the animations live.
    assert ("@media (prefers-reduced-motion: reduce) {\n"
            "  .wk-swap-spinner, .wk-swap-skel-line { animation: none; }\n"
            "}") in SHELL_CSS
    # A placeholder is the height a pick will be: the two text rows at their line-heights.
    assert ".wk-swap-skel-row { display: flex; align-items: center; height: calc(15px * 1.3); }" in SHELL_CSS
    assert ".wk-swap-skel-row-why { height: calc(13px * 1.25); }" in SHELL_CSS
    assert "line-height: 1.25" in _rule(".wk-swap-pick-why"), "the real line is pinned to the placeholder's height"
    assert "box-sizing: border-box" in _rule(".wk-swap-skel")
    section = SHELL_CSS[SHELL_CSS.index(".wk-swap-wait {"):SHELL_CSS.index(".wk-swap-picks {")]
    assert re.search(r":\s*#[0-9a-fA-F]{3,6}\b", section) is None, "every colour goes through a token"


def _hold_harness(script: str) -> str:
    """swapSheetHold against a fake body: offsetHeight is what the wait
    measured, style.minHeight and the is-held class are what it set."""
    return (
        _extract_var("SWAP_PLACEHOLDERS", SHELL_JS) + "\n"
        + _extract("swapSheetHold", SHELL_JS) + "\n"
        + "var body = { offsetHeight: 484, style: {}, cls: {}, classList: { toggle: function (c, on) { body.cls[c] = on; } } };\n"
        + "var document = { getElementById: function (id) { return id === 'wk-swap-body' ? body : null; } };\n"
        + "var st = { view: 'picks', options: null, trouble: '', holdHeight: 0 };\n"
        + "var log = [];\n"
        + "function snap(tag) { log.push([tag, st.holdHeight, body.style.minHeight, !!body.cls['is-held']]); }\n"
        + script
        + "console.log(JSON.stringify(log));"
    )


@_needs_node
def test_the_sheet_holds_its_waiting_height_while_the_picks_land_and_lets_go_otherwise():
    """Measured in a browser at 390 (2026-09-21): the sheet is pinned to the
    bottom of the screen, so when the wait line above the placeholders
    went, every card moved down by its height. Held at the wait's height
    with the spare space above the title, the sheet's edge, the three
    cards and the buttons stayed exactly put (tops 472/551/629 before and
    after); only the title settled 29px. Nothing found and the move view
    are shorter on purpose and are not held."""
    out = _run(_hold_harness(
        "swapSheetHold(st); snap('waiting');\n"
        "body.offsetHeight = 0; st.holdHeight = 0; swapSheetHold(st); snap('hidden');\n"
        "body.offsetHeight = 484; swapSheetHold(st); body.offsetHeight = 455;\n"
        "st.options = [{ index: 0 }, { index: 1 }, { index: 2 }]; swapSheetHold(st); snap('landed');\n"
        "st.view = 'move'; swapSheetHold(st); snap('move');\n"
        "st.view = 'picks'; st.options = null; st.trouble = 'Nothing I’d put there instead — tell me what you’d like.'; swapSheetHold(st); snap('trouble');\n"
    ))
    assert out == [
        ["waiting", 484, "", False],
        ["hidden", 0, "", False],
        ["landed", 484, "484px", True],
        ["move", 0, "", False],
        ["trouble", 0, "", False],
    ]
    assert "#wk-swap-body.is-held { justify-content: flex-end; }" in SHELL_CSS
    opened = _extract("openSwapSheet", SHELL_JS)
    assert "openSheet(swapSheetEl, swapScrimEl);\n    swapSheetHold(thisOpen);" in opened, "measured once the sheet is showing"
    assert "swapSheetHold(st);" in _extract("drawSwapSheet", SHELL_JS)


@_needs_node
@pytest.mark.parametrize("count, held", [(3, True), (2, False), (1, False), (0, False)])
def test_the_hold_is_only_for_as_many_picks_as_there_were_placeholders(count, held):
    """Found by the verifier (2026-09-21): the server's gate can hand back
    one or two picks (swap_options._gated, after the dedup and the
    allergen pass), and the hold kept three cards' height over them — the
    pick and the buttons at the bottom of a sheet with ~450px of nothing
    above the title. Three picks land in place, held; fewer sit at the
    top at their own height; an empty list is the nothing-found line
    (openSwapSheet sets trouble), never a hold."""
    empty_to_trouble = "st.trouble = 'Nothing I’d put there instead — tell me what you’d like.'; st.options = null;\n" if count == 0 else ""
    out = _run(_hold_harness(
        "swapSheetHold(st); body.offsetHeight = 455;\n"
        f"st.options = {json.dumps([{'index': i} for i in range(count)])};\n"
        + empty_to_trouble
        + "swapSheetHold(st); snap('landed');\n"
    ))
    assert out == [["landed", 484 if held else 0, "484px" if held else "", held]]
    assert "var SWAP_PLACEHOLDERS = 3;" in SHELL_JS
    assert "card.repeat(SWAP_PLACEHOLDERS)" in _extract("swapWaitHtml", SHELL_JS), "one count for the placeholders and the hold"
