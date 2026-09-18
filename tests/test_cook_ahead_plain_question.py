"""What is left of the cook-ahead "plain question" (Emily, 2026-09-13).

Loop Board "Cook-ahead ask: ask the plain question". Her rewrite, now
DESIGN_SYSTEM §8 Sounding-human rule 7 ("Clear beats warm").

Since 2026-09-18 both pickers that asked it are gone: the approval-time
ask card (batch cooking is assumed from prep days — tests/
test_batch_from_prep_days.py) and the Cook card's own picker ("The recipe
is the recipe"). What is left to hold is that the old arithmetic phrasing
never comes back, and that the chips the app still has behave on touch.
"""
from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SHELL_JS = (REPO / "static" / "shell.js").read_text(encoding="utf-8")
SHELL_CSS = (REPO / "static" / "shell.css").read_text(encoding="utf-8")


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
    _hover_is_pointer_only(".cook-offer-chip")


# ---------- the words themselves (DESIGN_SYSTEM §8) ----------

def test_no_old_arithmetic_or_dashboard_phrasing_survives():
    for gone in ("'Makes '", "'. Cook ahead?'", "Cook anything ahead?", "Cook ahead for these",
                 "Cooking ahead? Tick", "Tick the days a batch should cover", "Make them all at once",
                 "' all at once?'"):
        assert gone not in SHELL_JS, f"{gone!r} is back"


def test_the_two_pickers_are_gone_for_good():
    """Neither the approval-time ask nor the Cook card's picker: batch
    cooking is assumed from prep days, and the recipe is the recipe."""
    for marker in ("cookAheadAskState", "cookAheadAskCardHtml", "cookAheadHtml", "cookAheadTallyLine",
                   "Do you want to batch cook this?"):
        assert marker not in SHELL_JS, f"{marker} is back"
