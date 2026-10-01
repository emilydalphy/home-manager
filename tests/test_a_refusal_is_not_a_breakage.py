"""
A refusal written for a PERSON is an answer, not a breakage.

Found by the morning error check on 2026-10-01, which came back exit 1 —
"demo household: BROKEN — 3 in the last 1d: tool SlotRefused on
plan_meal". Nothing broke. That is allergen_gate.refuse_if_clashing
turning down three dishes somebody at that table cannot eat: the safety
feature doing its job, three times, on one real household in one day.

The asymmetry is the bug. Every ROUTE that can see a SlotRefused answers
a plain 200 {"status": "refused", "message": ...} and logs nothing (four
sites in app/main.py). The chat dispatch had no such arm, so the refusal
fell into the catch-all written for a CRASHED tool: a full
logger.exception traceback, and an error_events row. Tap it on a screen
and it is an answer; ask for the same thing in chat and the app reports
itself broken — which ChoreRefused's own docstring already forbids in so
many words ("An app that did exactly the right thing must not report
itself broken").

Two sibling gates in the very same dispatch function already do this
right and each says the morning report is why: the per-household Chores
switch, and the snack-attendance refusal. This is the third, and the only
one of the three that is currently firing in production.

WHAT THESE TESTS ARE WORTH. Red-against-main is decomposed rather than
quoted as one number, because this file names one symbol main has not
got (agent.REFUSALS_OWED_TO_A_PERSON) and a test that dies on a missing
name is not evidence of anything. Each test's docstring says whether it
is a CATCH (red on main for the reason it is named after), a GUARD (green
either way, pinned by a named mutation) or a NAME (red on main only
because the symbol is new).

Driven through the real agent loop with a stubbed API client — never a
stubbed loop. The defect is a missing `except` arm, and which arm catches
a raise is exactly what a source-marker test cannot see.
"""
from __future__ import annotations

import json
import types

import pytest

from app import agent, tools
from app.db import get_conn


class _Usage:
    input_tokens = cache_read_input_tokens = cache_creation_input_tokens = output_tokens = 0


def _text_block(text):
    return types.SimpleNamespace(type="text", text=text)


def _tool_block(name, tool_input, block_id="tu_1"):
    return types.SimpleNamespace(type="tool_use", name=name, input=tool_input, id=block_id)


class _FakeMessages:
    def __init__(self, responses):
        self._responses = list(responses)
        self.requests = []

    def create(self, **kwargs):
        self.requests.append(kwargs)
        return self._responses.pop(0)


def _stub_client(monkeypatch, responses):
    fake = _FakeMessages(responses)
    monkeypatch.setattr(agent, "_client", lambda: types.SimpleNamespace(messages=fake))
    return fake


def _one_tool_turn(monkeypatch, tool_name, tool_input, reply="Right you are."):
    """The ordinary two-round shape: the model calls one tool, then speaks."""
    return _stub_client(monkeypatch, [
        types.SimpleNamespace(content=[_tool_block(tool_name, tool_input)],
                              stop_reason="tool_use", usage=_Usage()),
        types.SimpleNamespace(content=[_text_block(reply)],
                              stop_reason="end_turn", usage=_Usage()),
    ])


def _error_rows():
    conn = get_conn()
    try:
        return [
            dict(r)
            for r in conn.execute(
                "SELECT kind, where_, detail, occurrences FROM error_events ORDER BY id"
            )
        ]
    finally:
        conn.close()


def _handed_back(conversation):
    """The tool_result the loop put in front of the model on round two."""
    results = [
        m for m in conversation
        if m["role"] == "user" and isinstance(m["content"], list)
    ]
    return results[-1]["content"][0]


# --------------------------------------------------------------------------
# 1. The reproduction: the gate fires, and the report stops calling it broken
# --------------------------------------------------------------------------

REFUSAL_SENTENCE = "Chicken Satay has peanuts in it, and Sam is allergic."


def test_an_allergy_refusal_in_chat_records_no_error(monkeypatch):
    """
    CATCH. The reported bug, through the door it was reported from. On
    main this writes `tool / plan_meal / SlotRefused` and the morning
    report reads the household as BROKEN.
    """
    def _refuse(**kwargs):
        raise tools.SlotRefused(REFUSAL_SENTENCE)

    monkeypatch.setitem(agent.TOOL_FUNCTIONS, "plan_meal", _refuse)
    _one_tool_turn(
        monkeypatch, "plan_meal",
        {"meal_date": "2026-10-05", "meal": "Chicken Satay"},
        reply=REFUSAL_SENTENCE,
    )

    agent.run_agent_turn([], "put chicken satay on Monday")

    assert _error_rows() == [], "a refusal was recorded as breakage"


def test_the_morning_report_does_not_read_it_as_broken(monkeypatch):
    """
    CATCH, end to end through the function the report actually reads.
    get_recent_errors' `total` is what turns the report's lead into
    "BROKEN" and its exit code into 1 — so this is the assertion that
    says the alarm has stopped.
    """
    def _refuse(**kwargs):
        raise tools.SlotRefused(REFUSAL_SENTENCE)

    monkeypatch.setitem(agent.TOOL_FUNCTIONS, "plan_meal", _refuse)
    for n in range(3):
        _one_tool_turn(
            monkeypatch, "plan_meal",
            {"meal_date": f"2026-10-0{n + 5}", "meal": "Chicken Satay"},
        )
        agent.run_agent_turn([], "put chicken satay on there")

    errors = tools.get_recent_errors(days=1)
    assert errors["total"] == 0
    assert errors["by_kind"] == {}
    assert errors["recent"] == []


def test_the_household_sees_exactly_what_it_saw_before(monkeypatch):
    """
    CATCH on the content, GUARD on is_error. Nothing about what the
    household reads changes: the model is handed the same sentence, in the
    same {"error": ...} shape, still flagged is_error so no "planned it"
    card is drawn for a meal that was never planned. The content shape is
    byte-identical to what the catch-all produced, which is why this is a
    pure observability fix.
    """
    def _refuse(**kwargs):
        raise tools.SlotRefused(REFUSAL_SENTENCE)

    monkeypatch.setitem(agent.TOOL_FUNCTIONS, "plan_meal", _refuse)
    fake = _one_tool_turn(
        monkeypatch, "plan_meal",
        {"meal_date": "2026-10-05", "meal": "Chicken Satay"},
        reply=REFUSAL_SENTENCE,
    )

    reply, conversation = agent.run_agent_turn([], "put chicken satay on Monday")

    assert len(fake.requests) == 2, "the loop carried on with the refusal in hand"
    handed = _handed_back(conversation)
    assert handed["type"] == "tool_result"
    assert handed["is_error"] is True
    assert json.loads(handed["content"]) == {"error": REFUSAL_SENTENCE}
    # And the model's own relay of it is what the household reads — not a
    # retraction, because is_error kept the turn from claiming a write.
    assert reply == REFUSAL_SENTENCE


def test_a_refused_turn_still_counts_as_writing_nothing(monkeypatch):
    """
    GUARD, and the one that matters most about keeping is_error True.
    agent._turn_wrote_anything reads only non-error tool results, so a
    model that says "I've planned it" over a refusal is still caught and
    retracted. Mutation: is_error = False in the new arm — then this reads
    the model's false claim back as the reply.
    """
    def _refuse(**kwargs):
        raise tools.SlotRefused(REFUSAL_SENTENCE)

    monkeypatch.setitem(agent.TOOL_FUNCTIONS, "plan_meal", _refuse)
    _one_tool_turn(
        monkeypatch, "plan_meal",
        {"meal_date": "2026-10-05", "meal": "Chicken Satay"},
        reply="Done — I've swapped that onto Monday for you.",
    )

    reply, _ = agent.run_agent_turn([], "put chicken satay on Monday")
    assert reply == agent.CHANGE_CLAIM_RETRACTION


# --------------------------------------------------------------------------
# 2. Every live chat door, and the one that cannot fire yet
# --------------------------------------------------------------------------

# The chat tools that can raise a refusal marker. plan_meal and
# swap_meal_in_plan both go through the allergy gate (and
# swap_meal_in_plan also refuses a night that has already gone);
# discard_draft_plan refuses an approved week in its own words. The four
# chores tools cannot fire while Chores is off — CHORES_TOOLS declines
# above the try — and are here so the set stays honest the day it is on.
_REFUSING_TOOLS = [
    ("plan_meal", {"meal_date": "2026-10-05", "meal": "Chicken Satay"}, tools.SlotRefused),
    ("swap_meal_in_plan", {"weekly_plan_id": 1, "meal_date": "2026-10-05",
                           "new_meal": "Chicken Satay"}, tools.SlotRefused),
    ("discard_draft_plan", {"weekly_plan_id": 1}, tools.SlotRefused),
    ("skip_chore", {"chore_name": "Bins"}, tools.ChoreRefused),
    ("move_chore", {"chore_name": "Bins", "to_date": "2026-10-05"}, tools.ChoreRefused),
    ("hand_chore", {"chore_name": "Bins", "to_person": "Emily"}, tools.ChoreRefused),
    ("complete_chore", {"instance_id": 1}, tools.ChoreRefused),
]


@pytest.mark.parametrize("name, tool_input, marker", _REFUSING_TOOLS)
def test_every_refusing_chat_tool_is_an_answer(monkeypatch, name, tool_input, marker):
    """
    CATCH, all seven — measured, not assumed. The first draft of this
    docstring called the four ChoreRefused cases a NAME miss on the
    grounds that Chores is off on main anyway; they switch Chores ON so
    the dispatch really reaches the tool rather than the switch's own
    decline, so on main the tool runs, raises, and records a row like any
    other. Corrected rather than quietly, because a guard mislabelled as
    a catch is the one test-file error this log keeps having to unpick.

    ChoreRefused is in the marker set for the reason its own docstring
    gives — it says it is "the same marker weekly_plan.SlotRefused is" —
    and handling one while leaving the other is the half-converted shape
    this codebase keeps getting bitten by. It cannot fire in production
    today: Chores is off for every household, so CHORES_TOOLS declines
    above the try.
    """
    if marker is tools.ChoreRefused:
        tools.set_chores_enabled(True)

    def _refuse(**kwargs):
        raise marker("There's nothing there to do that to.")

    monkeypatch.setitem(agent.TOOL_FUNCTIONS, name, _refuse)
    _one_tool_turn(monkeypatch, name, tool_input,
                   reply="There's nothing there to do that to.")

    _, conversation = agent.run_agent_turn([], "do that thing")

    assert _error_rows() == []
    handed = _handed_back(conversation)
    assert handed["is_error"] is True
    assert json.loads(handed["content"]) == {
        "error": "There's nothing there to do that to."
    }


def test_the_marker_set_is_the_two_refusal_types_and_nothing_else():
    """
    GUARD on the set itself. Both are the app's own markers for "a
    sentence written for a person"; widening this to any ValueError would
    swallow require_household_row's deliberately opaque "No chore instance
    with id 7.", which is not for reading and IS worth seeing.
    """
    assert agent.REFUSALS_OWED_TO_A_PERSON == (tools.SlotRefused, tools.ChoreRefused)
    for marker in agent.REFUSALS_OWED_TO_A_PERSON:
        assert issubclass(marker, ValueError)


# --------------------------------------------------------------------------
# 3. What must still be recorded
# --------------------------------------------------------------------------

def test_a_real_crash_is_still_logged_and_recorded(monkeypatch):
    """
    GUARD, and the whole point of making this a marker type rather than
    "stop recording tool errors". Mutation: catch Exception in the new arm
    — then a genuine crash goes silent and the morning report is blind
    again, which is the blind spot error_events was created to close.
    """
    def _boom(**kwargs):
        raise RuntimeError("database is locked")

    monkeypatch.setitem(agent.TOOL_FUNCTIONS, "plan_meal", _boom)
    _one_tool_turn(monkeypatch, "plan_meal",
                   {"meal_date": "2026-10-05", "meal": "Chili"})

    agent.run_agent_turn([], "put chili on Monday")

    rows = _error_rows()
    assert len(rows) == 1
    assert rows[0]["kind"] == "tool"
    assert rows[0]["where_"] == "plan_meal"
    assert rows[0]["detail"] == "RuntimeError"
    assert tools.get_recent_errors(days=1)["total"] == 1


def test_a_plain_value_error_is_still_recorded(monkeypatch):
    """
    GUARD. A bare ValueError is the "No weekly plan with id 7." family —
    opaque, not written for anybody to read, and a sign something is
    wrong. It must not ride in on the marker arm.
    """
    def _bad(**kwargs):
        raise ValueError("No weekly plan with id 7.")

    monkeypatch.setitem(agent.TOOL_FUNCTIONS, "plan_meal", _bad)
    _one_tool_turn(monkeypatch, "plan_meal",
                   {"meal_date": "2026-10-05", "meal": "Chili"})

    agent.run_agent_turn([], "put chili on Monday")

    rows = _error_rows()
    assert [r["detail"] for r in rows] == ["ValueError"]


# The validation markers, deliberately still recorded: each means a
# caller sent a word its own tool schema forbids. That is a model mistake
# worth seeing, not a household being told no — and CLAUDE.md twice
# accepted the row on exactly those grounds.
_VALIDATION_MARKERS = [
    "InvalidMealStatus",
    "InvalidChoreStatus",
    "InvalidGroceryStatus",
    "InvalidAttentionStatus",
    "InvalidSlot",
    "InvalidRecipeRating",
    "DuplicateRecipeName",
]


@pytest.mark.parametrize("marker_name", _VALIDATION_MARKERS)
def test_a_validation_marker_is_still_recorded(monkeypatch, marker_name):
    """
    GUARD, and the line this fix deliberately does not cross. Mutation:
    add any of these to REFUSALS_OWED_TO_A_PERSON — then a model
    routinely sending an invalid enum value becomes invisible.
    """
    marker = getattr(tools, marker_name)
    assert issubclass(marker, ValueError), f"{marker_name} is not a ValueError"

    def _invalid(**kwargs):
        raise marker("that isn't one of the words")

    monkeypatch.setitem(agent.TOOL_FUNCTIONS, "plan_meal", _invalid)
    _one_tool_turn(monkeypatch, "plan_meal",
                   {"meal_date": "2026-10-05", "meal": "Chili"})

    agent.run_agent_turn([], "put chili on Monday")

    rows = _error_rows()
    assert [r["detail"] for r in rows] == [marker_name]
    assert tools.get_recent_errors(days=1)["total"] == 1


# --------------------------------------------------------------------------
# 4. The ordering, which is the whole of whether this works
# --------------------------------------------------------------------------

def test_the_refusal_arm_comes_before_the_catch_all(monkeypatch):
    """
    CATCH, as behaviour rather than as a source marker. Both markers
    subclass ValueError, so an `except REFUSALS_OWED_TO_A_PERSON` placed
    AFTER `except Exception` would never run and this fix would be a
    no-op that looked finished — the same trap the routes' own
    `except tools.SlotRefused` before `except ValueError` exists for, and
    the one app/main.py's recipe-rating work had to pin with a test
    asking for both codes in one breath.

    Checked from the outside: a subclass of BOTH markers' parent cannot
    be used to tell the arms apart, so the proof is that the marker is
    handled at all while a sibling ValueError is not — which the two
    tests either side of this one establish — plus the source order
    below, read comment-stripped so prose cannot satisfy it.
    """
    import ast
    import inspect
    import textwrap

    # run_agent_turn is module-level, so its source is already flush;
    # textwrap.dedent is a no-op here and the guard if it ever moves.
    src = textwrap.dedent(inspect.getsource(agent.run_agent_turn))
    tree = ast.parse(src)

    handler_order = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Try):
            continue
        names = []
        for handler in node.handlers:
            if handler.type is None:
                names.append("bare")
            elif isinstance(handler.type, ast.Name):
                names.append(handler.type.id)
            else:
                names.append(ast.dump(handler.type))
        if any("REFUSALS_OWED_TO_A_PERSON" in n for n in names):
            handler_order.append(names)

    assert handler_order, "no try block catches REFUSALS_OWED_TO_A_PERSON"
    for names in handler_order:
        refusal_at = names.index("REFUSALS_OWED_TO_A_PERSON")
        for broader in ("Exception", "ValueError", "BaseException", "bare"):
            if broader in names:
                assert refusal_at < names.index(broader), (
                    f"except {broader} comes before the refusal arm, so the "
                    "refusal arm can never run"
                )


def test_the_refusal_sentence_never_reaches_the_logs(monkeypatch, caplog):
    """
    GUARD. The sentence names a dish and what it clashes with — and so,
    by implication, a member's dietary restriction. That is household
    content, and the dispatch's own rule two arms down is "argument NAMES
    only, never their values". One INFO line replaces the traceback: the
    tool name and the marker, nothing else.
    """
    def _refuse(**kwargs):
        raise tools.SlotRefused(REFUSAL_SENTENCE)

    monkeypatch.setitem(agent.TOOL_FUNCTIONS, "plan_meal", _refuse)
    _one_tool_turn(monkeypatch, "plan_meal",
                   {"meal_date": "2026-10-05", "meal": "Chicken Satay"})

    with caplog.at_level("INFO", logger="home_manager"):
        agent.run_agent_turn([], "put chicken satay on Monday")

    logged = "\n".join(r.getMessage() for r in caplog.records)
    assert "Sam" not in logged and "peanuts" not in logged
    assert "SlotRefused" in logged and "plan_meal" in logged
    # And no traceback: a working gate is not an exception.
    assert not any(r.exc_info for r in caplog.records)
