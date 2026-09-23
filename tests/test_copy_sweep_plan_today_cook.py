"""
Plain copy on Plan, Today and Cook — the non-toast half of the sweep.

COPY_SWEEP_2026-09-23.md, findings 16, 18, 19, 20, 21, 25, 27, 28 and 29,
plus the change card's leftover "Put back." footer. Emily, 2026-09-22:
*"Can we make sure that copy throughout is more straight forward like
this. I don't like the AI written style."*

The rules being applied are `.claude/skills/pomona-copywriter/SKILL.md`
rule 2 (a button says the action; nothing announces what I was going to
do anyway) and rule 3 (the house toast pattern). Sister branches did the
toast layer (`copy-toasts-name-the-thing`) and Shop's own lines.

These pin the lines verbatim, the same rule the rest of the suite uses
for user-facing copy: if one is deliberately reworded, update it in the
same commit rather than deleting the test.

Two of them are here because a clause was CUT, not reworded, and the
question is whether the cut took real information with it:

  * finding 20 — "I'll keep those to food that travels well" is gone from
    the lunch step. The fact survives where it is used: `wkMenuFact`
    puts the slot's asked-for fact ("travels well") on the menu row.
  * finding 25 — "I won't ask again this week" is gone from the dismissed
    plan-week card. The way back survives in the same card: it still
    says the offer is under Plan, and still carries "Plan the week →"
    straight into the flow.

Chores is NOT swept (paused by Emily, 2026-09-18), and its own "Put
back." is left standing — test_changes_saved.py pins that on purpose.
"""
from __future__ import annotations

import os
import re

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _read(*parts: str) -> str:
    with open(os.path.join(_ROOT, *parts), encoding="utf-8") as handle:
        return handle.read()


def _code_lines(source: str) -> str:
    """The source without its own commentary. Crude on purpose — a line
    that starts with // or * is a comment, anything else is code — but it
    is enough to ask "is the old wording still on a screen, or only in a
    note about the change?" without a regex eating the // in a URL."""
    kept = [line for line in source.splitlines()
            if not line.lstrip().startswith(("//", "*", "/*", "<!--"))]
    return "\n".join(kept)


SHELL_JS = _read("static", "shell.js")
PLAN_WEEK = _read("static", "plan-week.html")
COOKER_PY = _read("app", "tools", "cooker.py")


# ==========================================================================
# Plan
# ==========================================================================

def test_16_adding_a_cuisine_says_it_is_in_and_stops_there():
    """The chip is visibly on the list; a sentence about the filing is
    the announcing-memory shape Emily cut."""
    assert "inLine: '’s in.'" in PLAN_WEEK
    assert "I’ll remember it." not in PLAN_WEEK


def test_18_the_arrival_lines_do_not_explain_the_controls_under_them():
    """The Swap buttons and Approve say what can be done; the toast
    doesn't have to."""
    assert "showToast('Here’s your week.');" in SHELL_JS
    assert "showToast('Here’s your first week.');" in SHELL_JS
    assert "change anything before you approve it" not in SHELL_JS
    assert "I'll re-plan around it" not in SHELL_JS


def test_19_the_suggested_week_note_sounds_like_a_person():
    """Nobody says "assembles freely" at a kitchen table."""
    assert "One way to put it together — take what you like." in SHELL_JS
    assert "assembles freely" not in SHELL_JS


def test_20_the_lunch_step_names_what_was_ticked_and_nothing_else():
    assert "prefilled.lunches ? 'I’ve ticked your usual days.' : ''" in PLAN_WEEK
    assert "travels well" not in _code_lines(PLAN_WEEK)
    # An empty sub is a real state on this screen, not a gap: .step-sub
    # collapses when it has nothing in it (the moods' sub already ships
    # empty), so the question stands alone when nothing was pre-ticked.
    assert ".step-sub:empty { display: none; }" in PLAN_WEEK
    assert '<p class="step-sub" id="lunch-sub"></p>' in PLAN_WEEK


def test_20_travels_well_still_reaches_the_person_where_it_is_used():
    """The cut clause is not the only place the fact is said: a planned
    slot carries what it was asked for, and the menu row shows it."""
    assert 'if (dish.days[i].entry.asked) return dish.days[i].entry.asked;' in SHELL_JS
    assert '"travels well, good cold or reheated"' in _read("app", "agent.py")


def test_21_one_wording_for_the_way_into_chat():
    """Three spellings of one button — "Something else — tell me" on the
    swap sheet, "Tell me what instead" on the card and on the dock."""
    for fragment in (
        '\'<button type="button" class="wk-swap-else" id="wk-swap-tell"\' + wait + \'>Ask for something else</button>\'',
        '\'<button type="button" class="wk-swap-tell" data-wk-tell="\' + slot + \'">\' +\n      \'Ask for something else</button>\'',
        '\'<button type="button" class="dock-link wk-swap-tell" data-wk-tell="\' + slot + \'">Ask for something else</button>\'',
    ):
        assert fragment in SHELL_JS, fragment
    code = _code_lines(SHELL_JS)
    assert "Something else — tell me" not in code
    assert "Tell me what instead" not in code


# ==========================================================================
# Today
# ==========================================================================

def test_25_the_dismissed_nudge_says_where_the_offer_went_and_no_more():
    assert "<div class=\"plan-nudge-body\">It’ll be waiting under Plan.</div>" in SHELL_JS
    assert "I won’t ask again this week" not in SHELL_JS


def test_25_the_dismissed_card_still_carries_the_way_back():
    """The clause that went announced the remembering. The card itself is
    the way back, so nothing is stranded."""
    dismissed = SHELL_JS.split("function dismissPlanWeekNudge")[1].split("\n  function ")[0]
    assert "plan-nudge-dismissed" in dismissed
    assert 'id="plan-nudge-later">Plan the week →</button>' in dismissed
    assert "startPlanningWeek(nudge.week_start" in dismissed


def test_27_the_held_row_s_action_is_the_verb():
    assert 'data-held-done="\' + h.id + \'">Done</button>' in SHELL_JS
    assert "Done with this" not in _code_lines(SHELL_JS)


# ==========================================================================
# Cook
# ==========================================================================

def test_28_the_recipes_chat_opens_with_a_question_not_its_own_label():
    """The row above already says "Ask about our recipes"; the opener
    asks something instead of saying that back. It stays a real opener
    rather than being cut: the thread is shared across tabs, so a line
    that named nothing would leave Plan's greeting standing over a
    question about recipes (ensureAskSheetBuilt)."""
    assert "'What are you looking for in our recipes?'" in SHELL_JS
    assert "Ask me anything about the recipes" not in SHELL_JS


def test_29_starting_a_cook_on_the_wrong_day_is_refused_plainly():
    assert 'f"That one’s for {when}."' in COOKER_PY
    assert "note the start when you cook" not in COOKER_PY


# ==========================================================================
# The chat change card's footer — the toast branch's leftover
# ==========================================================================

def test_the_change_card_footer_matches_its_undo_toast():
    """Undoing the card reverts the week and the toast says "The week is
    back as it was."; the footer underneath still read "Put back." """
    assert "(state.putBack ? 'Back as it was.' : 'Left as it was.')" in SHELL_JS
    assert "showToast('The week is back as it was.');" in SHELL_JS


def test_chores_keeps_its_own_put_back():
    """Paused since 2026-09-18 — not swept, and deliberately untouched."""
    undo = SHELL_JS.split("async function runChoreUndo")[1].split("\n  // =====")[0]
    assert "showToast('Put back.');" in undo


@pytest.mark.parametrize("line", [
    "Here’s your week.",
    "Here’s your first week.",
    "One way to put it together — take what you like.",
    "I’ve ticked your usual days.",
    "Ask for something else",
    "It’ll be waiting under Plan.",
    "What are you looking for in our recipes?",
])
def test_none_of_the_new_lines_shouts(line: str) -> None:
    """DESIGN_SYSTEM §8: at most one exclamation mark per screen, and
    none of these is the one that earns it (the chat's own greeting is)."""
    assert "!" not in line
