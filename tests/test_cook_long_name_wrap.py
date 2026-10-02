"""A long dish name must never push a Cook screen sideways on a phone.

QA walk 2 (2026-10-02): `.cook-cooker-eyebrow { white-space: normal }` sat at
one class of specificity, and the later `.cook-eyebrow { white-space: nowrap }`
(same specificity, later in the file) beat it, so "DISH NAME · STEP 10 OF 10"
ran 422px wide at 375px and the whole step screen slid left. These tests read
shell.css as text, like the other static-source tests, and pin the fix.
"""
from __future__ import annotations

import re
from pathlib import Path

SHELL_CSS = (Path(__file__).resolve().parent.parent / "static" / "shell.css").read_text(
    encoding="utf-8"
)


def _rule(selector: str) -> str:
    """The declarations of the one rule whose selector is exactly `selector`."""
    hits = [
        m.group(1)
        for m in re.finditer(
            r"(?:^|[}/])\s*" + re.escape(selector) + r"\s*\{([^}]*)\}", SHELL_CSS
        )
    ]
    assert len(hits) == 1, f"expected one `{selector}` rule, found {len(hits)}"
    return hits[0]


def test_the_cooker_eyebrow_wraps_and_out_ranks_the_nowrap_label():
    # Two classes beat the one-class `.cook-eyebrow { white-space: nowrap }`
    # wherever each sits in the file.
    rule = _rule(".cook-eyebrow.cook-cooker-eyebrow")
    assert "white-space: normal" in rule
    assert "overflow-wrap: anywhere" in rule
    # And the bare one-class rule must not be what carries the wrap again.
    assert not re.search(r"(?:^|[}/])\s*\.cook-cooker-eyebrow\s*\{", SHELL_CSS)
    # The short labels keep their nowrap; only the dish-name line wraps.
    assert "white-space: nowrap" in _rule(".cook-eyebrow")


def test_the_cook_badge_that_can_carry_a_dish_name_shrinks_and_wraps():
    # `feeds` is a meal's name; a no-shrink nowrap chip pushed the row wide.
    rule = _rule(".cook-badge")
    assert "white-space: nowrap" not in rule
    assert "flex: 0 0 auto" not in rule
    assert "min-width: 0" in rule
    assert "overflow-wrap: anywhere" in rule
