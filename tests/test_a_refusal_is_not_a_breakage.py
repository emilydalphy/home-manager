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

import datetime
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

# The chat tools that can raise a refusal marker, COUNTED rather than
# recalled — the first version of this comment said "three live chat
# doors" and there are SIX, which an adversarial review of this branch
# caught. Measured by walking agent.TOOL_FUNCTIONS and asking, for each,
# which function it really is and whether it or anything one hop under it
# raises a marker:
#
#   plan_meal              -> meal_plans.plan_meal_for_chat      allergy gate
#   swap_meal_in_plan      -> weekly_plan.swap_meal_in_plan_for_chat
#                                            allergy gate AND night-gone
#   swap_component_in_plan -> weekly_plan.swap_component_in_plan allergy gate
#   add_recipe             -> recipes.add_recipe_for_chat        recipe gate
#   discard_draft_plan     -> its own "that week's approved" refusal
#   set_big_meal_dish      -> big_meal's own two allergy gates (section 6)
#
# The chores half is three, not four: skip_chore, move_chore and
# hand_chore reach chores.{skip,move,hand}_chore_instance, which raise
# ChoreRefused; complete_chore raises neither marker anywhere. It is kept
# in the list anyway, and that is the point of it — the arm is keyed on
# the EXCEPTION, never on a tool name, so a tool that cannot raise one
# today behaves correctly the day it can. None of the four can fire in
# production at all while Chores is off, since CHORES_TOOLS declines
# above the try.
_REFUSING_TOOLS = [
    ("plan_meal", {"meal_date": "2026-10-05", "meal": "Chicken Satay"}, tools.SlotRefused),
    ("swap_meal_in_plan", {"weekly_plan_id": 1, "meal_date": "2026-10-05",
                           "new_meal": "Chicken Satay"}, tools.SlotRefused),
    ("swap_component_in_plan", {"weekly_plan_id": 1, "component_category": "protein",
                                "new_meal": "Chicken Satay"}, tools.SlotRefused),
    ("add_recipe", {"name": "Chicken Satay", "ingredients": []}, tools.SlotRefused),
    ("discard_draft_plan", {"weekly_plan_id": 1}, tools.SlotRefused),
    ("set_big_meal_dish", {"date_str": "2026-10-12", "name": "Chicken Satay"}, tools.SlotRefused),
    ("skip_chore", {"chore_name": "Bins"}, tools.ChoreRefused),
    ("move_chore", {"chore_name": "Bins", "to_date": "2026-10-05"}, tools.ChoreRefused),
    ("hand_chore", {"chore_name": "Bins", "to_person": "Emily"}, tools.ChoreRefused),
    ("complete_chore", {"instance_id": 1}, tools.ChoreRefused),
]


@pytest.mark.parametrize("name, tool_input, marker", _REFUSING_TOOLS)
def test_every_refusing_chat_tool_is_an_answer(monkeypatch, name, tool_input, marker):
    """
    CATCH, all ten — measured, not assumed. The first draft of this
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


def test_the_marker_set_is_the_three_refusal_types_and_nothing_else():
    """
    GUARD on the set itself. Both are the app's own markers for "a
    sentence written for a person"; widening this to any ValueError would
    swallow require_household_row's deliberately opaque "No chore instance
    with id 7.", which is not for reading and IS worth seeing.
    """
    assert agent.REFUSALS_OWED_TO_A_PERSON == (
        tools.SlotRefused, tools.ChoreRefused, tools.MoveOwnerRefused)
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


# --------------------------------------------------------------------------
# 6. The big meal's own two allergy gates
# --------------------------------------------------------------------------
#
# Added after the branch's own adversarial review, which found this door
# open and is right that it leaves criterion 1 ("a refusal written for a
# person, raised by a chat tool, is not recorded in error_events and not
# logged as a crash") false as literally written.
#
# `set_big_meal_dish` is an un-gated live chat tool with no route, and its
# two allergy clashes raised a bare ValueError — so the clash fell into
# the catch-all exactly as plan_meal's did, with the extra harm that the
# sentence (which names a member's restriction) went into a traceback.
# Reproduced through this door on a throwaway database before the change:
#
#   DIRECT raised ValueError: Peanut Noodle Salad clashes with allergy:
#     peanuts — pick something else for the table.
#   error_events: [{'kind': 'tool', 'where_': 'set_big_meal_dish',
#                   'detail': 'ValueError', 'occurrences': 1}]
#   get_recent_errors total: 1
#
# and after it: SlotRefused, error_events [], total 0, nothing in the log.

def _thanksgiving() -> str:
    """Next year's, so the plan it sits in is always ahead of today."""
    found = tools.rule_holidays(datetime.date.today().year + 1)
    return next(h["date"] for h in found if h["name"] == "Thanksgiving")


def _hosted_thanksgiving(monkeypatch) -> str:
    """Two adults, one allergic to peanuts, hosting a Thanksgiving dinner."""
    tools.add_member("Emily")
    tools.set_member_age_group("Emily", "adult")
    tools.add_member("Sam")
    tools.set_member_age_group("Sam", "adult")
    tools.set_member_dietary_restrictions("Sam", ["allergy: peanuts"])

    tg = _thanksgiving()
    tools.add_recipe(
        "Roast Chicken",
        ingredients=[{"item": "whole chicken", "qty": "1", "category": "meat/seafood"}],
        prep_time_minutes=20, cook_time_minutes=90, default_servings=2,
    )
    plan_id = tools.create_weekly_plan(tg)["weekly_plan_id"]
    tools.plan_meal(tg, "Roast Chicken", slot="dinner", weekly_plan_id=plan_id)
    # The menu proposer is a model call; this test is about the gate, so it
    # answers with nothing and the menu is the dinner already planned.
    monkeypatch.setattr(agent, "generate_big_meal_llm", lambda context: {"dishes": []})
    tools.answer_holiday(tg, "hosting", headcount=4)
    return tg


PEANUT = {"item": "Peanut butter", "qty": "1 cup", "category": "pantry"}


@pytest.mark.parametrize("role", ["main", "side"])
def test_a_big_meal_allergy_clash_is_an_answer_not_a_crash(monkeypatch, role):
    """
    CATCH on both of set_big_meal_dish's gates — the main's (inside the
    `role == "main"` branch) and the one every other role reaches. Driven
    through the real dispatch with the REAL tool, not a stub: what is
    being pinned is which exception the tool raises, which a stub would
    decide for it.
    """
    tg = _hosted_thanksgiving(monkeypatch)
    _one_tool_turn(
        monkeypatch, "set_big_meal_dish",
        {"date_str": tg, "name": "Peanut Noodle Salad", "role": role,
         "ingredients": [PEANUT]},
        reply="Sam can't have peanuts — pick something else.",
    )

    _, conversation = agent.run_agent_turn([], "put a peanut noodle salad on the table")

    assert _error_rows() == [], "a working allergy gate was recorded as breakage"
    assert tools.get_recent_errors(days=1)["total"] == 0
    handed = _handed_back(conversation)
    assert handed["is_error"] is True
    assert "clashes with allergy: peanuts" in json.loads(handed["content"])["error"]


@pytest.mark.parametrize("role", ["main", "side"])
def test_the_big_meal_gates_raise_the_marker(monkeypatch, role):
    """
    CATCH, stated directly rather than through the dispatch, because the
    whole of the fix is which class is raised and a SlotRefused IS a
    ValueError — so a test written as `pytest.raises(ValueError)` (which
    tests/test_big_meal.py's own clash test is) passes either way and
    cannot see this.
    """
    tg = _hosted_thanksgiving(monkeypatch)
    with pytest.raises(tools.SlotRefused):
        tools.set_big_meal_dish(tg, "Peanut Noodle Salad", role=role, ingredients=[PEANUT])


def test_the_big_meal_clash_sentence_never_reaches_the_logs(monkeypatch, caplog):
    """
    CATCH. The sentence carries the restriction verbatim — "clashes with
    allergy: peanuts" — so on main this door put a member's allergy into
    a logged traceback, which is the one harm the plan_meal half of this
    branch is most careful about.
    """
    tg = _hosted_thanksgiving(monkeypatch)
    _one_tool_turn(
        monkeypatch, "set_big_meal_dish",
        {"date_str": tg, "name": "Peanut Noodle Salad", "role": "side",
         "ingredients": [PEANUT]},
    )

    with caplog.at_level("INFO", logger="home_manager"):
        agent.run_agent_turn([], "put a peanut noodle salad on the table")

    logged = "\n".join(r.getMessage() for r in caplog.records)
    assert "peanuts" not in logged and "Sam" not in logged
    assert "SlotRefused" in logged and "set_big_meal_dish" in logged
    assert not any(r.exc_info for r in caplog.records)


def test_the_big_meals_other_refusals_are_still_recorded(monkeypatch):
    """
    GUARD, and the boundary that keeps this narrow. set_big_meal_dish has
    four other raises and NONE of them moved: a bad role, a main with no
    ingredients and no saved recipe, a dish with no ingredients, and
    _require_menu's "no big meal on that day". Those are a caller sending
    something its own schema forbids — a model mistake, worth seeing in
    the report — which is the same line the branch draws at the
    validation markers.

    Pinned by the mutation that widens the gate's marker to cover them:
    make `role has to be main, side or sweet.` a SlotRefused and this
    goes red.
    """
    tg = _hosted_thanksgiving(monkeypatch)
    _one_tool_turn(
        monkeypatch, "set_big_meal_dish",
        {"date_str": tg, "name": "Peanut Noodle Salad", "role": "pudding",
         "ingredients": [PEANUT]},
    )

    agent.run_agent_turn([], "add it as a pudding")

    rows = _error_rows()
    assert [r["where_"] for r in rows] == ["set_big_meal_dish"]
    assert rows[0]["detail"] == "ValueError"
