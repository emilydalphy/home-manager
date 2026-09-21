"""
The chat sheet's first bubble (Loop Board "Chat greeting", Emily,
2026-09-21): it reads, word for word,

    Tell me what's on your mind and how I can help!

wherever the sheet opens — a meal row, the Plan tab's ask bar, Today's
icon — because every one of those openers falls through to the one
DEFAULT_ASK_GREETING in shell.js (the server builds no greeting of its
own; /api/chat* answers turns, it never says hello). The line carries an
exclamation mark, which DESIGN_SYSTEM.md §8 allows at most once per
screen, so the second half of this file checks it is the sheet's only one.

Runs the real ensureAskSheetBuilt under node the way
test_ask_sheet_late_greeting.py does, so the assertion is on what reaches
the thread, not on a string sitting in the source.
"""
from __future__ import annotations

import re
import shutil
from pathlib import Path

import pytest

from test_ask_sheet_late_greeting import _node, _script

REPO = Path(__file__).resolve().parent.parent
SHELL_JS = (REPO / "static" / "shell.js").read_text(encoding="utf-8")
SHELL_HTML = (REPO / "static" / "shell.html").read_text(encoding="utf-8")

GREETING = "Tell me what’s on your mind and how I can help!"

_needs_node = pytest.mark.skipif(
    shutil.which("node") is None, reason="node is needed to execute the shell's own functions"
)


def test_the_default_greeting_is_the_line_emily_asked_for():
    assert f"var DEFAULT_ASK_GREETING = '{GREETING}';" in SHELL_JS
    # The old line is gone from the sheet — the tips screen's own opener
    # ("Say it however it comes out.") is a different screen and stays.
    assert "Say it however it comes — I’ll put it where it belongs." not in SHELL_JS


def test_the_server_builds_no_greeting_of_its_own():
    """Every opener shares the one constant; there is no second copy to
    drift. The chat routes answer turns and never write a first bubble."""
    for name in ("agent.py", "main.py"):
        src = (REPO / "app" / name).read_text(encoding="utf-8")
        assert "on your mind and how I can help" not in src
        assert "Say it however it comes" not in src
    assert SHELL_JS.count("on your mind and how I can help") == 1


@_needs_node
def test_every_opener_without_its_own_line_gets_the_greeting():
    """A meal row, the ask bar and Today's icon all call openAskSheet with
    no third argument, so the first build says the default — and a second
    opener later in the session adds nothing (the line is not stacked)."""
    out = _node(_script("""
ensureAskSheetBuilt();
var first = lastAssistantAskText, calls = targetCalls;
ensureAskSheetBuilt();
console.log(JSON.stringify({ first: first, calls: calls, again: lastAssistantAskText, callsAgain: targetCalls }));
"""))
    assert out["first"] == GREETING
    assert out["calls"] == 1
    assert out["again"] == GREETING and out["callsAgain"] == 1, "opened twice, said once"


def _sheet_markup() -> str:
    start = SHELL_HTML.index('<div id="ask-sheet"')
    end = SHELL_HTML.index('id="week-sheet-handle"', start)
    block = SHELL_HTML[start:end]
    return re.sub(r"<!--.*?-->", "", block, flags=re.S)


def test_the_greeting_is_the_sheets_only_exclamation_mark():
    assert "!" not in _sheet_markup(), "the sheet's own markup carries no exclamation mark"
    # Every string literal in shell.js that ends in "!" — the sheet's
    # composer, chips, tips and errors all live in this file. Guards in
    # code ("!==", "!x") are not string literals and don't match.
    shouting = re.findall(r"""['"]([^'"\n]*!)['"]""", SHELL_JS)
    assert shouting == [GREETING], shouting
