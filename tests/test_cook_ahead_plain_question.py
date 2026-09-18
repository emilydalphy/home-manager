"""The cook-ahead picker asks the plain question (Emily, 2026-09-13).

Loop Board "Cook-ahead ask: ask the plain question". Her rewrite, now
DESIGN_SYSTEM §8 Sounding-human rule 7 ("Clear beats warm"): *"Do you want
to batch cook this? Which meals should be included?"* — the question, then
the choice, then the buttons that commit, nothing decorative in between.

Since 2026-09-18 the approval-time ask card (All set / the Plan root) is
gone — batch cooking is assumed from prep days (tests/
test_batch_from_prep_days.py) — so what is left to hold to the rule is the
Cook card's own picker (cookAheadHtml), which the Meal step borrows.

These run shell.js's own functions under node (tests/nodeharness.py) so
they see the words a phone would, not markers in the source.
"""
from __future__ import annotations

import json
import re
import shutil
from pathlib import Path

import pytest

from tests import nodeharness

REPO = Path(__file__).resolve().parents[1]
SHELL_JS = (REPO / "static" / "shell.js").read_text(encoding="utf-8")
SHELL_CSS = (REPO / "static" / "shell.css").read_text(encoding="utf-8")

_needs_node = pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")


def _function(name: str) -> str:
    start = SHELL_JS.index(f"function {name}(")
    i = SHELL_JS.index("{", start)
    depth, j = 0, i
    while True:
        if SHELL_JS[j] == "{":
            depth += 1
        elif SHELL_JS[j] == "}":
            depth -= 1
            if depth == 0:
                break
        j += 1
    return SHELL_JS[start : j + 1]


_PRELUDE = (
    _function("escapeHtml") + "\n"  # the real one: it turns ' into &#39;, and the question has one
    "function dayName(dateStr, opts){ return new Date(dateStr + 'T00:00:00').toLocaleDateString('en-US', opts); }\n"
    "function dayNameShort(iso){ return new Date(iso + 'T00:00:00').toLocaleDateString('en-US', { weekday: 'short' }); }\n"
    "var cookState = { cookAheadPicks: {} };\n"
    + _function("cookSlotWord") + "\n"
    + _function("cookAheadTallyLine") + "\n"
    + _function("cookAheadPicks") + "\n"
    + _function("cookAheadHtml") + "\n"
)


def _node(script: str):
    res = nodeharness.run_node(_PRELUDE + script, timeout=30)
    assert res.returncode == 0, f"node failed: {res.stderr}"
    return json.loads(res.stdout.strip())


MON, TUE, THU, SAT, SUN = "2026-09-14", "2026-09-15", "2026-09-17", "2026-09-19", "2026-09-20"


# ---------- the cook card's picker ----------

@_needs_node
def test_the_cook_card_picker_asks_the_plain_question():
    meal = {
        "entry_id": 1, "date": MON, "slot": "dinner",
        "attendance": {"headcount": 2},
        "cook_ahead": {"days": [
            {"entry_id": 11, "date": TUE, "eaters": 2, "selected": False},
            {"entry_id": 12, "date": THU, "eaters": 2, "selected": True},
        ]},
    }
    out = _node("console.log(JSON.stringify(cookAheadHtml(%s)));" % json.dumps(meal))
    assert "Do you want to batch cook this? Which other nights should it cover?" in out
    assert "One cook on Monday feeds Mon and Thu · 4 plates" in out
    assert "Makes " not in out and "Cooking ahead?" not in out


@_needs_node
def test_the_tally_reads_as_a_sentence_not_arithmetic():
    out = _node("console.log(JSON.stringify([cookAheadTallyLine(%s, [], 2), cookAheadTallyLine(%s, [%s, %s], 6)]));"
                % (json.dumps(MON), json.dumps(MON), json.dumps(TUE), json.dumps(THU)))
    assert out == ["One cook on Monday, for Monday only · 2 plates", "One cook on Monday feeds Mon, Tue and Thu · 6 plates"]


# ---------- two states on touch ----------

def _hover_is_pointer_only(selector: str) -> None:
    """Every :hover rule for this chip sits inside @media (hover: hover)."""
    for m in re.finditer(re.escape(selector) + r":hover", SHELL_CSS):
        before = SHELL_CSS[: m.start()]
        opened = before.rfind("@media (hover: hover)")
        assert opened != -1, f"{selector}:hover is not wrapped in @media (hover: hover)"
        # The media block opened last must still be open: its closing brace
        # would be a "}" on its own line after the rule inside it.
        closed = before.find("\n}", opened)
        assert closed == -1, f"{selector}:hover sits outside the last @media (hover: hover) block"


def test_chip_hover_is_only_for_pointers_so_a_tap_leaves_no_ghost():
    _hover_is_pointer_only(".defrost-chip")
    _hover_is_pointer_only(".cook-ahead-day")
    _hover_is_pointer_only(".wk-card .cook-ahead-day")


# ---------- the words themselves (DESIGN_SYSTEM §8) ----------

def test_no_old_arithmetic_or_dashboard_phrasing_survives():
    for gone in ("'Makes '", "'. Cook ahead?'", "Cook anything ahead?", "Cook ahead for these",
                 "Cooking ahead? Tick", "Tick the days a batch should cover", "Make them all at once",
                 "' all at once?'"):
        assert gone not in SHELL_JS, f"{gone!r} is back"
