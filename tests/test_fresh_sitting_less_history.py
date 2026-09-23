"""
A new sitting starts from an empty conversation, and a sitting carries
twelve turns rather than forty.

`SESSIONS` in app/main.py is memory-only, keyed by the signed session, and
until 2026-09-23 it held up to FORTY user turns ACROSS DAYS — until the
server happened to restart. Every turn re-sends the whole thing, tool
results included, and a tool result in this app is a whole week's plan as
JSON. Measured in production: about 15K tokens of history per turn on top
of the ~37K briefing, and chat was the biggest line on the month's bill.
So a question on Tuesday was paying to re-read Sunday's conversation.

Nothing the household told Pomona is lost by resetting it, and that is
the whole reason it is safe: Pomona's memory lives in the DATABASE — held
things, facts, preferences, the plan, the taste record — never in the
transcript. What goes is the WORDING of a previous sitting. Anything it
was asked to remember still comes back, because it was never in the
transcript to begin with. These tests assert that directly rather than
asserting it in prose.

The four-hour rule is not new and this change does not invent it: it is
the same `_NEW_SITTING_GAP` that already decided whether to run the
proactive check — exactly the "are we starting something, or carrying
on?" question. Both answers now come from one reading of the clock.

Each test says in its own docstring whether it is a CATCH (red against
main's app/) or a GUARD (green either way, here to say what did not
change), and every GUARD names the mutation that pins it.

ON THE RED-AGAINST-MAIN COUNT, because it is worth much less than it
looks and this project's log keeps having to unpick exactly this: seven
of the nine go red against main's `app/`, and only TWO of those are red
for the reason they are named after — the route marker (main really does
inline the three lines twice) and the twelve-turn cap (main really is
40). The other five die on `AttributeError: _chat_session_state`, a name
main has not got, which is the only kind of red a new function can have.
The real evidence is the five mutations, all of which were run and all of
which bite; each one is named in the docstring of the test it pins.
"""
from __future__ import annotations

import time
import types

import pytest

from app import agent, main, tools
from app.db import get_conn


@pytest.fixture(autouse=True)
def _clean_sessions():
    main.SESSIONS.clear()
    main.SESSION_TOUCHED.clear()
    yield
    main.SESSIONS.clear()
    main.SESSION_TOUCHED.clear()


def _request():
    """Enough of a Request for _chat_session_id; the cookie is the key."""
    return types.SimpleNamespace(cookies={}, headers={}, client=None, url=None)


def _seed(session_id: str, turns: int, *, seconds_ago: float) -> None:
    main.SESSIONS[session_id] = [
        m for i in range(turns)
        for m in ({"role": "user", "content": f"turn {i}"},
                  {"role": "assistant", "content": f"reply {i}"})
    ]
    main.SESSION_TOUCHED[session_id] = time.time() - seconds_ago


def _turns(history: list) -> int:
    return sum(
        1 for m in history
        if m.get("role") == "user" and isinstance(m.get("content"), str)
    )


# --------------------------------------------------------------------------
# The reset
# --------------------------------------------------------------------------


def test_a_turn_after_a_long_gap_starts_from_nothing(monkeypatch):
    """
    CATCH, and the card's own acceptance criterion: two turns five hours
    apart, and the second is sent with no prior messages.
    """
    monkeypatch.setattr(main, "_chat_session_id", lambda r: "s1")
    _seed("s1", turns=6, seconds_ago=5 * 60 * 60)

    session_id, history, is_new_sitting = main._chat_session_state(_request())

    assert session_id == "s1"
    assert history == []
    assert is_new_sitting is True


def test_carrying_on_a_sitting_keeps_the_conversation(monkeypatch):
    """
    GUARD, and the thing that must not break: within a sitting, nothing
    changes at all.

    Pinned by the mutation that returns [] unconditionally: this then
    reads 0 turns and Pomona forgets the sentence before.
    """
    monkeypatch.setattr(main, "_chat_session_id", lambda r: "s1")
    _seed("s1", turns=3, seconds_ago=60)

    _, history, is_new_sitting = main._chat_session_state(_request())

    assert _turns(history) == 3
    assert is_new_sitting is False


def test_a_session_nobody_has_used_before_is_a_new_sitting(monkeypatch):
    """GUARD. The first turn of all: no entry, so no history and a fresh start."""
    monkeypatch.setattr(main, "_chat_session_id", lambda r: "brand-new")

    _, history, is_new_sitting = main._chat_session_state(_request())

    assert history == []
    assert is_new_sitting is True


def test_the_gap_is_the_one_the_proactive_check_already_used(monkeypatch):
    """
    GUARD on the boundary, both sides of it.

    The reset and the proactive check must never disagree about whether
    this is a new sitting — they are the same question, and they now come
    from one reading of the clock.

    Pinned by the mutation that gives the reset its own threshold.
    """
    monkeypatch.setattr(main, "_chat_session_id", lambda r: "s1")

    _seed("s1", turns=2, seconds_ago=main._NEW_SITTING_GAP - 60)
    _, history, new_before = main._chat_session_state(_request())
    assert new_before is False and _turns(history) == 2

    _seed("s1", turns=2, seconds_ago=main._NEW_SITTING_GAP + 60)
    _, history, new_after = main._chat_session_state(_request())
    assert new_after is True and history == []


def test_both_chat_routes_ask_the_same_function(monkeypatch):
    """
    GUARD, source-level. Two copies of one rule is this codebase's named
    recurring bug generator, and the plain and streaming routes had three
    identical lines each.

    Pinned by the mutation that inlines the three lines back into either
    route.
    """
    import inspect

    src = inspect.getsource(main)
    # The CALL form, not the bare name — counting the name matches the
    # `def` line too, which is how the first draft of this assertion read
    # 3 where it meant 2.
    assert src.count("= _chat_session_state(request)") == 2
    # The old inline form must not come back anywhere.
    assert "history = SESSIONS.get(session_id, [])" not in src


# --------------------------------------------------------------------------
# Nothing the household asked Pomona to remember is affected
# --------------------------------------------------------------------------


def test_a_new_sitting_loses_no_held_thing_fact_or_preference(monkeypatch):
    """
    CATCH on the claim the whole change rests on: the reset drops the
    TRANSCRIPT and nothing else.

    Written as a real before/after against the real tables rather than as
    an assertion in prose, because "the memory is in the database" is
    exactly the kind of sentence that is true until somebody moves
    something into the transcript.
    """
    monkeypatch.setattr(main, "_chat_session_id", lambda r: "s1")

    tools.hold_thing("The blue casserole dish is at Mum's")
    tools.add_fact("food", "Nobody here eats fennel")
    tools.set_household_meal_preferences(snacks_per_week=2)

    held_before = [h["text"] for h in tools.list_held_things()]
    facts_before = [f["text"] for f in tools.get_facts()]
    prefs_before = tools.get_household_memory()["snacks_per_week"]

    _seed("s1", turns=8, seconds_ago=6 * 60 * 60)
    _, history, is_new_sitting = main._chat_session_state(_request())

    assert is_new_sitting is True and history == []
    assert [h["text"] for h in tools.list_held_things()] == held_before
    assert [f["text"] for f in tools.get_facts()] == facts_before
    assert tools.get_household_memory()["snacks_per_week"] == prefs_before == 2


# --------------------------------------------------------------------------
# The cap within a sitting
# --------------------------------------------------------------------------


def test_a_sitting_carries_at_most_twelve_turns():
    """
    CATCH. Forty was a memory-growth cap; it was never chosen as a cost
    number, and it is one.
    """
    history = [
        m for i in range(30)
        for m in ({"role": "user", "content": f"turn {i}"},
                  {"role": "assistant", "content": f"reply {i}"})
    ]

    trimmed = agent.trim_conversation(history)

    assert _turns(trimmed) == 12
    assert agent.MAX_CONVERSATION_TURNS == 12


def test_the_cap_lives_in_exactly_one_constant():
    """
    GUARD. The card asks for N pinned in one place.

    Pinned by the mutation that writes a literal into trim_conversation's
    default instead of the constant.
    """
    import inspect

    src = inspect.getsource(agent.trim_conversation)
    assert "max_turns: int = MAX_CONVERSATION_TURNS" in src


def test_the_cap_still_never_cuts_a_tool_use_from_its_result():
    """
    GUARD, and it is the one that would hurt: the Anthropic API rejects a
    request whose tool_use and tool_result don't line up, so a cut in the
    wrong place turns every subsequent turn into an error.

    Lowering the cap makes cutting far more frequent, which is exactly why
    this is asserted at the new number rather than assumed from the old
    one. Pinned by the mutation that cuts ONE MESSAGE PAST the boundary
    (`boundaries[-max_turns] + 1`).

    The first mutation tried here was "cut at a fixed offset" — and it
    reddened nothing, because with four messages per turn the offset it
    computed happened to land on a boundary anyway. A mutation that misses
    by luck says nothing about the test; it says the mutation was badly
    chosen. Named precisely so the next person runs the one that bites.
    """
    history = []
    for i in range(20):
        history.append({"role": "user", "content": f"turn {i}"})
        history.append({"role": "assistant", "content": [
            {"type": "tool_use", "id": f"tu_{i}", "name": "list_grocery_list", "input": {}}
        ]})
        history.append({"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": f"tu_{i}", "content": "[]"}
        ]})
        history.append({"role": "assistant", "content": f"reply {i}"})

    trimmed = agent.trim_conversation(history)

    assert trimmed[0]["role"] == "user" and isinstance(trimmed[0]["content"], str)
    used = [
        b["id"] for m in trimmed if isinstance(m.get("content"), list)
        for b in m["content"] if b.get("type") == "tool_use"
    ]
    got = [
        b["tool_use_id"] for m in trimmed if isinstance(m.get("content"), list)
        for b in m["content"] if b.get("type") == "tool_result"
    ]
    assert used == got, "every tool_use kept must keep its own tool_result"
