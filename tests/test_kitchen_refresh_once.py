"""
A chat change that touches the Cook tab refreshes it ONCE.

Loop Board "Cook refreshes itself twice after any chat change that touches
it", found 2026-09-12 by reading the function. `refreshStaleTabsFromActions`
is what keeps an already-built tab from going stale after a chat turn
writes something (CLAUDE.md's "tab panels build once per page load"
gotcha), and its `kitchen` branch carried `refreshKitchenPanel();` twice in
a row — directly under a comment reading "One call covers the whole tool
set now", which is what makes it a slip rather than a decision.

`refreshKitchenPanel` -> `loadKitchen` is a Promise.all over three
endpoints, so the duplicate cost six requests and two racing renders on
every turn tagged `kitchen` (check_off_meal, check_off_prep_step,
resolve_attention_item, update_inventory — app/main.py's _KITCHEN_TOOLS).
Both calls read the same state, so nothing was ever WRONG on screen; this
is waste, and a duplicate that misleads the next reader of that branch.

This repo has no JavaScript harness for a function that only calls other
functions, so — like tests/test_cook_voice_hidden.py — these pin the source
text. Tests 1 and 3 are both catches — red on main, for the same one line.
Test 2 is the only guard here, and says so in its own docstring.
"""
from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SHELL_JS = (REPO / "static" / "shell.js").read_text()


def _refresh_stale_tabs_body() -> str:
    """The source of refreshStaleTabsFromActions, brace-matched."""
    start = SHELL_JS.index("function refreshStaleTabsFromActions")
    depth = 0
    for i in range(SHELL_JS.index("{", start), len(SHELL_JS)):
        if SHELL_JS[i] == "{":
            depth += 1
        elif SHELL_JS[i] == "}":
            depth -= 1
            if depth == 0:
                return SHELL_JS[start:i + 1]
    raise AssertionError("refreshStaleTabsFromActions is not brace-balanced")


def test_a_kitchen_tagged_chat_action_refreshes_the_cook_panel_once():
    """The catch: two calls in a row meant six requests where three would do."""
    body = _refresh_stale_tabs_body()
    assert body.count("refreshKitchenPanel();") == 1, (
        "refreshStaleTabsFromActions should ask the Cook panel to refresh "
        "exactly once per kitchen-tagged action"
    )


def test_todays_moves_still_refresh_alongside_it():
    """
    A no-regression guard, and it says so: refreshTodayMoves() sits beside
    the kitchen refresh for a different surface (a fridge move ticked from
    chat is the same table read by Now), and its own comment explains why.
    Deduplicating the neighbour must not take it with it.
    """
    body = _refresh_stale_tabs_body()
    kitchen = body[body.index("action.tab === 'kitchen'"):]
    kitchen = kitchen[:kitchen.index("else if")]
    assert "refreshTodayMoves();" in kitchen


def test_no_statement_call_is_written_twice_in_a_row_anywhere_in_the_shell():
    """
    The wider form of test 1, and red on main for the same line — the
    narrow test says the Cook branch is clean, this one says the whole file
    is. Worth both: the duplicate had no sibling anywhere in 14k lines, so
    a new one is a new mistake rather than a house habit, and this catches
    it in whichever branch it lands in next.
    """
    dupes = []
    previous = None
    for number, raw in enumerate(SHELL_JS.splitlines(), start=1):
        line = raw.strip()
        if re.fullmatch(r"[A-Za-z_$][A-Za-z0-9_$.]*\([^;]*\);", line) and line == previous:
            dupes.append(f"{number}: {line}")
        previous = line
    assert dupes == [], f"back-to-back duplicate calls in static/shell.js: {dupes}"
