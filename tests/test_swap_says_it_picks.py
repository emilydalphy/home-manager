"""Swap says up front that it picks something different.

Emily, 2026-09-13: "When you click the 'swap' button, it goes with
something totally different. which is fine, but it should make that clear
that it'll be something random."

The control reads "Swap · I'll pick" wherever the in-place swap is offered
— the Day step's card and the Meal step's dock — against "Tell me what
instead" beside it, where the household picks. One constant (SWAP_LABEL)
so the two sites can never say different things about the same call. The
post-swap state is unchanged and pinned here: the reason, Undo and the
chat link on the line, with the swap itself still there to go again.
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import nodeharness
import pytest

REPO = Path(__file__).resolve().parents[1]
SHELL_JS = (REPO / "static" / "shell.js").read_text(encoding="utf-8")

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


def _run_node(harness: str):
    res = nodeharness.run_node(harness, timeout=30)
    assert res.returncode == 0, f"node failed: {res.stderr}"
    return json.loads(res.stdout.strip())


_ESCAPE = (
    "function escapeHtml(s){return String(s == null ? '' : s)"
    ".replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/\"/g,'&quot;');}\n"
)


def test_one_label_at_every_site():
    assert f"var SWAP_LABEL = '{LABEL}';" in SHELL_JS
    acts = _extract("slotActionsHtml")
    assert acts.count("SWAP_LABEL") == 2, "the card's swap beside Cook this, and the row it takes alone"
    dock = _extract("mealDockHtml")
    assert dock.count("SWAP_LABEL") == 2, "the dock's quiet link, and the primary it becomes"
    # No bare "Swap" button is left at either site.
    for site in (acts, dock):
        assert ">Swap</button>" not in site and ">Swap this meal</button>" not in site


def test_the_words_follow_the_voice_rules():
    # A contraction, first person, and the fact — never the word "random",
    # which would sell the pick short (it works around what the table
    # can't have, the week's other dishes, the night's time cap).
    assert "I’ll" in LABEL
    assert "random" not in LABEL.lower()
    assert "Tell me what instead" in SHELL_JS


def _card(swap_state) -> str:
    entry = {"state": "planned", "title": "Turkey burgers", "source": "plan", "meta": "25 min",
             "entry_id": 9, "sides": [], "food_groups": [], "defrost": None, "plate_note": ""}
    day = {"date": "2026-09-15", "isPast": False, "dinner": entry, "snacks": []}
    harness = (
        _ESCAPE
        + f"var swapState = {json.dumps(swap_state)};\n"
        + "var REHEAT_ACTION_LABEL = 'Mark eaten';\n"
        + f"var SWAP_LABEL = '{LABEL}';\n"
        + "function planCookableNow() { return true; }\n"
        + "".join(_extract(n) + "\n" for n in (
            "isSnackSlot", "daySlotEntry", "isRealCook", "cookTimeChip",
            "swapStateFor", "swapLineHtml", "slotActionsHtml"))
        + f"console.log(JSON.stringify(slotActionsHtml({json.dumps(day)}, 'dinner', false)));\n"
    )
    return _run_node(harness)


@_needs_node
def test_the_card_says_who_picks_on_each_way_out():
    html = _card(None)
    assert f'class="wk-act wk-act-swap" data-wk-swap="dinner">{LABEL}<' in html
    assert 'data-wk-cook="dinner">Cook this · 25 min<' in html
    assert 'data-wk-tell="dinner">Tell me what instead<' in html
    # The app's pick comes first in the row, the household's own ask on
    # the quiet line under it — read top to bottom, "I'll pick" then
    # "tell me what instead" is the choice Emily asked to see.
    assert html.index(LABEL) < html.index("Tell me what instead")


@_needs_node
def test_after_a_swap_the_new_dish_can_be_undone_or_swapped_again():
    swapped = {"date": "2026-09-15", "slot": "dinner", "avoid": ["Turkey burgers"],
               "reason": "Lighter than the burgers, and nothing to thaw.", "canUndo": True}
    html = _card(swapped)
    assert "Lighter than the burgers, and nothing to thaw." in html
    assert 'data-wk-undo="dinner">Undo<' in html
    assert 'data-wk-tell="dinner">Tell me what instead<' in html
    # ...and the swap is still offered, so "not that one either" is one tap.
    assert f'data-wk-swap="dinner">{LABEL}<' in html
    # While the call is out, the line says so and offers nothing to tap.
    busy = _card({"date": "2026-09-15", "slot": "dinner", "busy": True, "avoid": []})
    assert "Finding something else…" in busy
    assert "data-wk-undo" not in busy


@_needs_node
def test_the_meal_docks_swap_carries_the_same_words():
    entry = {"state": "planned", "title": "Turkey burgers", "source": "plan", "meta": "25 min",
             "entry_id": 9, "sides": [], "food_groups": [], "defrost": None, "plate_note": ""}
    day = {"date": "2026-09-15", "isPast": False, "dinner": entry, "snacks": []}
    harness = (
        _ESCAPE
        + "var swapState = null;\n"
        + "var REHEAT_ACTION_LABEL = 'Mark eaten';\n"
        + f"var SWAP_LABEL = '{LABEL}';\n"
        + "var cookable = true;\n"
        + "function planCookableNow() { return cookable; }\n"
        + "function mealCookUnderway() { return false; }\n"
        + "function clockLabel(m) { return '6:00'; }\n"
        + "".join(_extract(n) + "\n" for n in (
            "isSnackSlot", "daySlotEntry", "isRealCook", "swapStateFor", "swapLineHtml", "mealDockHtml"))
        + f"var day = {json.dumps(day)};\n"
        + "var out = { now: mealDockHtml(day, 'dinner', { cookMeal: null, start: null }) };\n"
        + "cookable = false;\n"
        + "out.later = mealDockHtml(day, 'dinner', { cookMeal: null, start: null });\n"
        + "console.log(JSON.stringify(out));\n"
    )
    got = _run_node(harness)
    assert f'class="dock-link wk-act-swap" data-wk-swap="dinner">{LABEL}<' in got["now"]
    assert f'class="dock-primary wk-act-swap" data-wk-swap="dinner">{LABEL}<' in got["later"]
