"""
Shared helpers for running shell.js's own functions under node (_extract,
_extract_var, _extract_async) — imported by a dozen renderer tests — and
the one test left from Plan › Which days as seven tiles (Emily, 2026-09-12).

The tiles themselves went on 2026-09-18: Check the week is a carousel of
day cards now (tests/test_plan_cards_2026_09_18.py), and a dinner moves to
another night from the swap sheet's "Move the tacos to another day", still
through POST /api/week/{week}/swap-nights (tests/test_swap_dinner_nights.py
covers the write).
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

import nodeharness

REPO = Path(__file__).resolve().parent.parent
SHELL_JS = (REPO / "static" / "shell.js").read_text(encoding="utf-8")
SHELL_CSS = (REPO / "static" / "shell.css").read_text(encoding="utf-8")

_needs_node = pytest.mark.skipif(
    shutil.which("node") is None, reason="node is needed to execute the screen's own functions"
)


def _extract(name: str, source: str) -> str:
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


def _extract_async(name: str, source: str) -> str:
    return "async " + _extract(name, source)


def _extract_var(name: str, source: str) -> str:
    start = source.index(f"var {name} = ")
    depth, quote, j = 0, "", source.index("=", start) + 1
    while True:
        c = source[j]
        if quote:
            if c == "\\":
                j += 2
                continue
            if c == quote:
                quote = ""
        elif c in "\"'":
            quote = c
        elif c in "([{":
            depth += 1
        elif c in ")]}":
            depth -= 1
        elif c == ";" and depth == 0:
            break
        j += 1
    return source[start : j + 1]


def _run_node(harness: str):
    res = nodeharness.run_node(harness, timeout=30)
    assert res.returncode == 0, f"node failed: {res.stderr}"
    return json.loads(res.stdout.strip())


def test_a_chat_move_refreshes_the_plan_panel():
    """The gotcha in CLAUDE.md: a panel built once and never told to
    refresh goes stale. swap_dinner_nights is tagged `week` on the backend
    (app/main.py _WEEK_TOOLS), and the `week` branch reloads the menu."""
    main_py = (REPO / "app" / "main.py").read_text(encoding="utf-8")
    assert '"swap_dinner_nights"' in main_py.split("_WEEK_TOOLS = ", 1)[1].split("\n", 1)[0]
    refresh = _extract("refreshStaleTabsFromActions", SHELL_JS)
    assert "if (action.tab === 'week' && panels.week && panels.week.dataset.built)" in refresh
    assert "loadWeekMenu(panels.week);" in refresh


def test_the_seven_tiles_are_gone():
    for gone in ("reviewDayTileHtml", "wireReviewTiles", "rv-tile-handle", "runSwapNights("):
        assert gone not in SHELL_JS, gone
    assert ".rv-tile {" not in SHELL_CSS
    # ...and a night still moves, from the swap sheet.
    assert "'/swap-nights'" in SHELL_JS and "'/swap-nights-undo'" in SHELL_JS
