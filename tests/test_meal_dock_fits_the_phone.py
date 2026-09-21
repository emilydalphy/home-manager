"""
The recipe screen's footer fits the phone (Loop Board "Recipe screen
footer fits the phone", Emily, 2026-09-21).

Her screenshot at 390px, on a meal opened from Check the week: the Meal
step's dock (mealDockHtml, the branch for a week that isn't cooking yet)
showed "Swap · I'll pick" wrapped into a column about 40px wide on the
left, "Tell me what instead" centred, and the chat icon on the right.

The cause was a class collision, not a lack of room. The swap SHEET's
"Something else — tell me" and the dock's quiet "Tell me what instead"
both carried `wk-swap-tell`, and the sheet's rule — `width: 100%`, a
full-width outline button — reached the dock's link: it took the whole
row, and the apricot Swap (flex 1 1 auto, min-width 0) was squeezed to
12px and wrapped one word per line. Measured in a browser with the real
faces before the fix: Swap 12px wide, link 286px.

Fixed: the sheet's button has its own class (`wk-swap-else`); in the meal
dock the Swap is a real-width button that never wraps (`flex: 0 0 auto`,
`white-space: nowrap`), and the link is what gives on a narrower phone —
at a word, never mid-word. Measured after: Swap 137px, link 130px, gap
14, in the 286px the dock leaves beside the chat icon at 390 — one row,
18px clear of the icon; at 375 the link takes two lines inside its 44px.

The node harness renders the dock the way test_swap_says_it_picks does
(no layout engine here — the widths above are the browser's; what this
file pins is the markup and the rules the widths follow from).
"""
from __future__ import annotations

import json
import re
import shutil
from pathlib import Path

import nodeharness
import pytest

REPO = Path(__file__).resolve().parents[1]
SHELL_JS = (REPO / "static" / "shell.js").read_text(encoding="utf-8")
SHELL_CSS = (REPO / "static" / "shell.css").read_text(encoding="utf-8")

_needs_node = pytest.mark.skipif(
    shutil.which("node") is None,
    reason="node is needed to execute the screen's own functions",
)

LABEL = "Swap · I’ll pick"


def _extract(name: str, source: str = SHELL_JS) -> str:
    start = source.index(f"function {name}(")
    i = source.index("{", start)
    depth, j = 0, i
    while True:
        if source[j] == "{":
            depth += 1
        elif source[j] == "}":
            depth -= 1
            if depth == 0:
                break
        j += 1
    return source[start : j + 1]


_NO_COMMENTS = re.sub(r"/\*.*?\*/", "", SHELL_CSS, flags=re.S)


def _rule(selector: str) -> str:
    """The declarations of the rule whose selector list is exactly
    `selector` (at the start of a line), as one string."""
    m = re.search(r"^" + re.escape(selector) + r"\s*\{([^}]*)\}", _NO_COMMENTS, flags=re.M)
    assert m, f"no rule for {selector}"
    return " ".join(m.group(1).split())


_ESCAPE = (
    "function escapeHtml(s){return String(s == null ? '' : s)"
    ".replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/\"/g,'&quot;');}\n"
)


def _render_dock(cookable: bool) -> str:
    entry = {"state": "planned", "title": "Turkey burgers", "source": "plan", "meta": "25 min",
             "entry_id": 9, "sides": [], "food_groups": [], "defrost": None, "plate_note": ""}
    day = {"date": "2026-09-22", "isPast": False, "dinner": entry, "snacks": []}
    harness = (
        _ESCAPE
        + "var swapState = null;\n"
        + "var REHEAT_ACTION_LABEL = 'Mark eaten';\n"
        + f"var SWAP_LABEL = '{LABEL}';\n"
        + f"function planCookableNow() {{ return {json.dumps(cookable)}; }}\n"
        + "function mealCookUnderway() { return false; }\n"
        + "".join(_extract(n) + "\n" for n in (
            "isSnackSlot", "daySlotEntry", "isRealCook", "swapStateFor", "swapLineHtml", "mealDockHtml"))
        + f"var day = {json.dumps(day)};\n"
        + "console.log(JSON.stringify(mealDockHtml(day, 'dinner', { cookMeal: null, start: null })));\n"
    )
    res = nodeharness.run_node(harness, timeout=30)
    assert res.returncode == 0, f"node failed: {res.stderr}"
    return json.loads(res.stdout.strip())


@_needs_node
def test_the_footer_is_one_row_of_swap_and_its_quiet_link():
    """Check the week's meal (the week isn't cooking yet): the Swap is the
    row's apricot, "Tell me what instead" its one quiet link, and nothing
    else is in the row — the chat icon floats at the dock's end on its own
    (the dock's 84px right padding keeps the row clear of it)."""
    html = _render_dock(cookable=False)
    row = re.search(r'<div class="dock-row">(.*?)</div>', html).group(1)
    buttons = re.findall(r"<button[^>]*>.*?</button>", row)
    assert len(buttons) == 2, buttons
    assert f'class="dock-primary wk-act-swap" data-wk-swap="dinner">{LABEL}</button>' in buttons[0]
    assert 'class="dock-link wk-swap-tell" data-wk-tell="dinner">Tell me what instead</button>' in buttons[1]
    assert html.startswith('<div class="wk-decide dock wk-meal-dock">'), "the rules below key off .wk-meal-dock"
    # Nothing else on the dock: the swap line only appears once a swap is
    # under way (its idle state stays on the Day step's cards).
    assert "wk-swap-line" not in html


@_needs_node
def test_a_cooking_week_keeps_start_cooking_as_the_row_and_swap_as_its_link():
    html = _render_dock(cookable=True)
    assert 'class="dock-primary" data-wk-cook="dinner" data-wk-start="1">Start cooking</button>' in html
    assert f'class="dock-link wk-act-swap" data-wk-swap="dinner">{LABEL}</button>' in html


def test_the_swap_label_is_a_real_width_that_never_wraps():
    swap = _rule(".wk-meal-dock .dock-row .dock-primary.wk-act-swap")
    assert "white-space: nowrap" in swap
    assert "flex: 0 0 auto" in swap, "a real width — the shared .dock-row .dock-primary rule is flex 1 1 auto with min-width 0, which is what let it be squeezed"
    assert "width: auto" in swap
    # The 44px floor (DESIGN_SYSTEM.md rule 6) comes from .dock-primary's
    # own 52px min-height and .dock-link's 44px; neither is overridden here.
    assert "min-height: 52px" in _rule(".dock-primary")
    assert "min-height: 44px" in _rule(".dock-link")
    assert "min-height" not in swap


def test_the_quiet_link_gives_at_a_word_and_never_mid_word():
    link = _rule(".wk-meal-dock .dock-row .dock-link.wk-swap-tell")
    assert "white-space: normal" in link, "the link is the one thing allowed to take two lines"
    assert "min-width: 0" in link and "flex: 0 1 auto" in link
    for rule in (link, _rule(".wk-meal-dock .dock-row .dock-primary.wk-act-swap")):
        assert "word-break" not in rule and "overflow-wrap" not in rule and "hyphens" not in rule, (
            "nothing here breaks a word in the middle"
        )


def test_the_sheets_full_width_button_no_longer_reaches_the_dock():
    """The bug: one class on two different buttons. The sheet's outline
    button is .wk-swap-else now; no .wk-swap-tell rule is full-width."""
    assert 'class="wk-swap-else" id="wk-swap-tell">Something else — tell me</button>' in SHELL_JS
    assert 'class="wk-swap-tell" id="wk-swap-tell"' not in SHELL_JS
    assert "width: 100%" in _rule(".wk-swap-else")
    for m in re.finditer(r"([^{}]*\.wk-swap-tell[^{}]*)\{([^}]*)\}", _NO_COMMENTS):
        selectors, body = m.group(1), m.group(2)
        assert "width: 100%" not in body, f"{selectors.strip()} is full-width again"
        assert "width:100%" not in body


def test_the_dock_stays_in_flow_so_the_page_still_clears_it():
    """The dock is sticky at the foot of the scroll, not fixed over it: the
    screen's content sits above it in flow, so scrolling to the bottom
    reaches everything — nothing is ever hidden behind the footer."""
    dock = _rule(".dock")
    assert "position: sticky" in dock and "bottom: 0" in dock
    assert "padding: 8px 84px 14px 20px" in dock, "the row stops 84px short of the chat icon"
    assert "position: fixed" not in _rule(".wk-decide")
