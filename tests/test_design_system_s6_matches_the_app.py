"""
DESIGN_SYSTEM §6 has to describe the app that exists (Loop Board:
"DESIGN_SYSTEM §6 still describes the old tabs and the old home for
cooking", Emily approved the correction 2026-09-10).

§6 is what an agent reads before touching navigation, so a stale §6 is not
a documentation nit — it is a brief to build the wrong thing. It went stale
twice in three days: the tabs were renamed on 2026-09-09 and cooking moved
out of Meals on 2026-09-08, and both bullets were left describing the app
as it had been. This file is the tripwire, and it pins §6 against the CODE
rather than against a copy of the prose — a test that only quoted the doc
back to itself would have passed happily through both of those drifts.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest


REPO = Path(__file__).resolve().parent.parent
DESIGN_SYSTEM = (REPO / "DESIGN_SYSTEM.md").read_text(encoding="utf-8")
SHELL_JS = (REPO / "static" / "shell.js").read_text(encoding="utf-8")


def _section_six() -> str:
    start = DESIGN_SYSTEM.index("## 6. Navigation rules")
    return DESIGN_SYSTEM[start:DESIGN_SYSTEM.index("\n## ", start + 1)]


def _tab_labels_from_code() -> list[str]:
    """The labels a household actually reads, off shell.js's own TABS."""
    block = SHELL_JS[SHELL_JS.index("var TABS = ["):]
    block = block[: block.index("\n  ];")]
    return re.findall(r"label: '([^']+)'", block)


def test_section_six_names_the_tabs_the_household_reads():
    """Not a hard-coded list — the labels come out of TABS, so renaming a
    tab in the code and not here fails rather than rots."""
    labels = _tab_labels_from_code()
    assert labels == ["Now", "Plan", "Shop", "Cook"], labels
    opening = _section_six().split("\n")[2]
    for label in labels:
        assert label in opening, f"§6's opening bullet never names {label}"


def test_section_six_does_not_still_claim_the_old_four_are_the_four():
    """The old names may appear as history ("They were Today, Meals…"), but
    never as the statement of what the four screens are."""
    opening = _section_six().split("\n")[2]
    claim = opening[: opening.index("(")]
    for old in ("Today", "Meals", "Grocery", "Kitchen"):
        assert old not in claim, f"§6 still asserts the tabs are {old}…"


def test_section_six_says_cooking_is_a_step_of_cook():
    six = _section_six()
    assert "**Cooking is a *step* of Cook**" in six
    # The control that bullet used to describe is gone from the app; §6 must
    # not still offer it as the current shape.
    assert "a `Plan | Cook` segmented state at `/week`), not as its own tab" not in six


def test_the_code_agrees_that_cook_mode_is_a_step_of_that_tab():
    """The bullet's source of truth. shell.js's TABS comment is where this
    was recorded when it changed; if that comment goes, §6 is unanchored
    and this test should be the thing that notices."""
    assert "Cook mode is a STEP of this tab" in SHELL_JS


def test_the_no_primary_action_rule_survives_and_is_about_the_root():
    """Acceptance criterion: the rule stays, because rule 2 leans on it
    ("a screen with no single action has no dock"). What changed is that it
    names the root — cook mode one step down really does carry an apricot."""
    six = _section_six()
    assert "**Cook's root has no primary action.**" in six
    assert "A screen with no single action has no dock" in six
    assert "Mark it cooked" in six


def test_cook_mode_really_does_carry_the_tabs_one_apricot():
    """The half of that bullet that would be a lie if the button moved."""
    assert "Mark it cooked" in SHELL_JS


@pytest.mark.parametrize("rule", [
    # Reworded 2026-09-11 when the chat became an icon (Build 1); the rule
    # that the sheet belongs to the shell is the part that survived.
    "and the sheet it opens is part of the shell, not any one screen.**",
    "**One way back, and it names its parent**",
    "**One dock**",
    "**Refresh policy**",
])
def test_no_other_rule_in_section_six_was_lost(rule):
    """The edit was a factual correction, not a redesign — Emily's approval
    covers exactly that and nothing wider."""
    assert rule in _section_six()
