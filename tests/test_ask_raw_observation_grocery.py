"""
Loop Board: "Ask: the door says 'hold this', not 'meal edits'" (High, Phase
1 -- Beta, Improvement), 2026-09-15.

One acceptance criterion for that card: typing a raw thought ("we're
nearly out of dish soap") must produce the same result as typing the
command form ("add dish soap to the list") -- because the whole point of
widening the Ask sheet's greeting and chips is that a held thing shouldn't
need to be translated into a command first (see the new "We're nearly out
of..." chip in static/shell.js's COACH_EXAMPLES, whose starter text is
exactly this shape of sentence, unfinished).

Before this card, app.agent.SYSTEM_PROMPT's grocery-item guidance already
covered "in passing or directly" phrasing with one example ("we're out of
paper towels" -- fully out). It had nothing for "nearly out of" -- the
phrasing the new chip starts, and the exact wording of the acceptance
criterion's own example. add_staple's tool description separately teaches
running_low for a *staple* ("Set running_low true when they say they're
out or nearly out right now"), but that only fires once the model has
already decided to call add_staple rather than add_grocery_item, so it
doesn't cover a first-time, one-off mention. So a single line was added to
the "General guidelines" section (just after the existing "we're out of
paper towels" bullet) spelling out that a plain observation is the same
request as a command.

What this file checks, and what it doesn't:

  * SOURCE only. Whether the real model actually changes its behavior for
    "we're nearly out of X" isn't something this repo's test harness can
    exercise: every agent-dispatch test here (test_chores_chat_tools.py,
    test_staples.py, etc.) stubs app.agent._client's messages.create to
    return a *scripted* tool call chosen by the test, never lets a real
    model read the prompt and decide -- so a "does the model call
    add_grocery_item for this sentence" test would only prove the stub
    returns what it was told to return, not that the prompt guidance
    worked. That would be a test that always passes regardless of whether
    the SYSTEM_PROMPT line is there at all, which is worse than no test.
    A real check needs a live model call against the actual API, which
    this suite deliberately never does. So, per this card's own escape
    hatch ("add a test... if there is a tractable one; otherwise
    explain"): this file pins the prompt guidance existing and saying the
    right thing, the same SOURCE pattern test_staples.py's
    test_the_four_staple_tools_are_offered_to_the_model already uses for
    "add_staple" in agent.SYSTEM_PROMPT.
"""
from __future__ import annotations

from app import agent


def test_the_prompt_already_treats_out_of_x_as_a_request_not_just_an_observation():
    """The pre-existing bullet this card's new line sits beside -- pinned so
    a future edit can't silently narrow it back to command-only phrasing."""
    assert "we're out of paper towels" in agent.SYSTEM_PROMPT


def test_the_prompt_now_also_covers_nearly_out_of_phrasing():
    """The line this card added: a raw "nearly out" observation is the same
    request as the imperative form, not a lesser one that waits for the
    household to rephrase it as a command."""
    assert "nearly out of" in agent.SYSTEM_PROMPT
    assert "exactly as \"add dish soap to the list\" would" in agent.SYSTEM_PROMPT


def test_add_grocery_item_is_still_offered_to_the_model():
    """Sanity check that the tool the new line points at actually exists and
    is wired up -- a prompt line referencing a renamed or removed tool would
    be a silent no-op."""
    names = {d["name"] for d in agent.TOOL_DEFINITIONS}
    assert "add_grocery_item" in names and "add_grocery_item" in agent.TOOL_FUNCTIONS
