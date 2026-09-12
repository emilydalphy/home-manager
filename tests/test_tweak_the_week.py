"""
Loop Board "Tweak-the-week chat: after a swap the flow dies" (Emily,
2026-09-08).

Two decisions, tested at the two levels they live at.

(1) The reply is trimmed to one or two sentences, and the balanced-plate
    nudge is turned off — but ONLY in the flow reached from the draft
    week's "or tweak it with me" link. That is a prompt change, and the
    thing worth pinning is that it is scoped: a tweak turn gets the extra
    system block, an ordinary chat turn's system blocks are byte-for-byte
    what they always were. No API call is made — the client is stubbed the
    same way tests/test_agent_turn_recording.py stubs it.

(2) The chat offers a "See your week" chip beside "Approve this week"
    after a turn that edited the draft, and nothing at all after a turn
    that changed nothing. computeNextStepChips is pure (actions in, chip
    descriptors out), so it is lifted out of static/shell.js and run under
    node — the same harness idea as tests/test_leftovers_batch.py, which
    exists because "assert the source mentions the word" is not a test of
    what the screen does.

Also covered: the ChatAction date/slot the chip navigates by
(app/main.py _changed_day), including the calls that legitimately have no
single day to point at.
"""
from __future__ import annotations

import datetime
import json
import shutil

import nodeharness
import types
from pathlib import Path

import pytest

from app import agent, main as app_main


# ---------- (1) the prompt, scoped ----------


class _Usage:
    input_tokens = 0
    cache_read_input_tokens = 0
    cache_creation_input_tokens = 0
    output_tokens = 0


class _CapturingMessages:
    """Answers in one round and keeps every kwarg it was handed."""

    def __init__(self):
        self.calls: list[dict] = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return types.SimpleNamespace(
            content=[types.SimpleNamespace(type="text", text="Done.")],
            stop_reason="end_turn",
            usage=_Usage(),
        )


def _system_blocks_for(monkeypatch, message: str) -> list[dict]:
    captured = _CapturingMessages()
    monkeypatch.setattr(agent, "_client", lambda: types.SimpleNamespace(messages=captured))
    agent.run_agent_turn([], message)
    assert captured.calls, "the turn should have reached the API layer"
    return captured.calls[0]["system"]


def _all_system_text(blocks) -> str:
    return "\n".join(b["text"] for b in blocks)


TWEAK_MESSAGE = "Let’s tweak this week — swap Thursday for burgers"


def test_a_tweak_turn_is_told_to_keep_the_reply_to_one_or_two_sentences(monkeypatch):
    text = _all_system_text(_system_blocks_for(monkeypatch, TWEAK_MESSAGE))
    assert "one or two sentences" in text
    assert "or tweak it with me" in text, "the block should say which flow it is scoping"
    assert "nothing's on your list until you approve" in text, "Emily's example of the length"


def test_a_tweak_turn_is_told_not_to_offer_the_balanced_plate_nudge(monkeypatch):
    """The nudge lives in the frozen, cached SYSTEM_PROMPT, so it is turned
    off by an explicit override rather than by editing the prompt out from
    under every other kind of turn."""
    blocks = _system_blocks_for(monkeypatch, TWEAK_MESSAGE)
    tweak_block = blocks[-1]["text"]
    assert "Do not offer the balanced-plate suggestion" in tweak_block
    assert "food_groups_missing" in tweak_block
    # And the base prompt still carries the nudge for everyone else.
    assert "food_groups_missing" in agent.SYSTEM_PROMPT


def test_the_first_sample_week_tweak_gets_the_same_treatment(monkeypatch):
    """The reveal screen's own tweak link uses a different prefill; both
    are the same flow and must not drift apart."""
    text = _all_system_text(
        _system_blocks_for(monkeypatch, "Let’s tweak my first sample week — less chicken")
    )
    assert "one or two sentences" in text


def test_an_ordinary_chat_turn_is_left_exactly_as_it_was(monkeypatch):
    """The scoping is the whole point: if this block leaked into every
    turn, the app would start giving one-sentence answers to questions
    that need a real answer."""
    blocks = _system_blocks_for(monkeypatch, "What should I prep today?")
    text = _all_system_text(blocks)
    assert "one or two sentences" not in text
    assert "Do not offer the balanced-plate suggestion" not in text
    # Same shape it has always had: the frozen prompt, then today's date.
    assert len(blocks) == 2
    assert blocks[0]["text"] == agent.SYSTEM_PROMPT


@pytest.mark.parametrize(
    "message",
    [
        "Let's tweak this week — swap Thursday",  # straight apostrophe
        "  let’s tweak this week — swap thursday",  # leading space, lowercased
        "Let’s tweak this week —",  # nothing typed after the prefill yet
    ],
)
def test_the_marker_survives_the_shapes_a_real_message_arrives_in(message):
    assert agent._is_tweak_context(message)


@pytest.mark.parametrize(
    "message",
    [
        "Can we tweak this week?",
        "Let's talk about this week",
        "",
        "I'd like to approve this week's plan.",
    ],
)
def test_a_message_that_only_sounds_like_the_marker_is_not_the_tweak_flow(message):
    assert not agent._is_tweak_context(message)


# ---------- the day the chip navigates to ----------


def _turn_with_tool_call(name: str, args: dict, result: dict):
    """Same minimal before/after pair tests/test_loop_handoffs.py builds."""
    return [], [
        {"role": "assistant", "content": [
            {"type": "tool_use", "id": "toolu_1", "name": name, "input": args},
        ]},
        {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "toolu_1", "content": json.dumps(result), "is_error": False},
        ]},
    ]


def _week_card(name: str, args: dict, result: dict | None = None):
    before, after = _turn_with_tool_call(name, args, result if result is not None else {"status": "ok"})
    cards = [a for a in app_main.summarize_chat_actions(before, after) if a.tab == "week"]
    assert len(cards) == 1, f"expected exactly one week card, got {cards}"
    return cards[0]


THURSDAY = (datetime.date.today() + datetime.timedelta(days=3)).isoformat()


def test_a_swap_says_which_day_and_slot_it_changed():
    card = _week_card("swap_meal_in_plan", {
        "weekly_plan_id": 1, "meal_date": THURSDAY, "new_meal": "Beef Burgers", "slot": "dinner",
    })
    assert (card.date, card.slot) == (THURSDAY, "dinner")


def test_planning_a_non_dinner_slot_carries_that_slot():
    card = _week_card("plan_meal", {"meal_date": THURSDAY, "meal": "Oatmeal", "slot": "breakfast"})
    assert (card.date, card.slot) == (THURSDAY, "breakfast")


def test_an_omitted_slot_means_dinner_the_same_way_the_tools_default_it():
    card = _week_card("plan_meal", {"meal_date": THURSDAY, "meal": "Tacos"})
    assert (card.date, card.slot) == (THURSDAY, "dinner")


def test_a_whole_week_generation_points_at_no_single_day():
    """There is no "the day that changed" when every day changed — the chip
    should show the week as it stands rather than picking one arbitrarily."""
    card = _week_card("generate_weekly_plan", {"week_start_date": THURSDAY, "day_count": 7})
    assert (card.date, card.slot) == (None, None)


def test_a_component_swap_has_no_date_at_all_and_says_so():
    card = _week_card("swap_component_in_plan", {
        "weekly_plan_id": 1, "component_category": "protein",
        "old_meal": "Chicken Thighs", "new_meal": "Pork Loin",
    })
    assert (card.date, card.slot) == (None, None)


def test_a_junk_date_is_dropped_rather_than_handed_to_the_screen():
    card = _week_card("plan_meal", {"meal_date": "next Thursday", "meal": "Tacos"})
    assert card.date is None


def test_a_grocery_card_never_carries_a_day():
    before, after = _turn_with_tool_call("add_grocery_item", {"item": "milk"}, {"status": "ok"})
    grocery = [a for a in app_main.summarize_chat_actions(before, after) if a.tab == "grocery"]
    assert grocery and grocery[0].date is None and grocery[0].slot is None


# ---------- (2) the chips themselves, run rather than read ----------

SHELL_JS = Path(__file__).resolve().parent.parent / "static" / "shell.js"

_needs_node = pytest.mark.skipif(
    shutil.which("node") is None, reason="node is needed to execute the screen's own functions"
)


def _extract(name: str, source: str) -> str:
    """Lift one brace-balanced `function name(...) {...}` out of the file."""
    start = source.index(f"function {name}(")
    i = source.index("{", start)
    depth, j = 0, i
    while True:
        if source[j] == "{":
            depth += 1
        elif source[j] == "}":
            depth -= 1
            if depth == 0:
                break
        j += 1
    return source[start : j + 1]


def _chips(actions: list[dict]) -> list[dict]:
    """Run computeNextStepChips under node and report each chip's label,
    whether it navigates (onClick) or sends a message, and — for the
    navigating ones — what it actually did when clicked."""
    src = SHELL_JS.read_text()
    harness = (
        "var navCalls = [];\n"
        "function activateTab(key, push, opts){ navCalls.push(['activateTab', key, opts || null]); }\n"
        "function closeAskSheet(){ navCalls.push(['closeAskSheet']); }\n"
        "function focusChangedWeekDay(date, slot){ navCalls.push(['focusChangedWeekDay', date || null, slot || null]); }\n"
        + _extract("computeNextStepChips", src) + "\n"
        f"var chips = computeNextStepChips({json.dumps(actions)});\n"
        "console.log(JSON.stringify(chips.map(function (c) {\n"
        "  navCalls = [];\n"
        "  if (c.onClick) c.onClick();\n"
        "  return { label: c.label, msg: c.msg || null, navigates: !!c.onClick, did: navCalls };\n"
        "})));\n"
    )
    res = nodeharness.run_node(harness, timeout=30)
    assert res.returncode == 0, f"node failed: {res.stderr}"
    return json.loads(res.stdout.strip())


def _week_action(change="Updated Beef Burgers", date=THURSDAY, slot="dinner"):
    return {"kicker": "WEEK UPDATED", "change": change, "tab": "week", "date": date, "slot": slot}


@_needs_node
def test_a_turn_that_changed_nothing_offers_no_chips():
    """A question answered is not a step in a flow — per Emily's decision,
    nothing to offer here."""
    assert _chips([]) == []


@_needs_node
def test_a_draft_edit_offers_seeing_the_week_before_approving_it():
    chips = _chips([_week_action()])
    assert [c["label"] for c in chips] == ["See your week", "Approve this week"]
    # Looking is free and reversible; approving is neither — so looking is
    # first, and it navigates rather than sending yet another message.
    assert chips[0]["navigates"] is True
    assert chips[1]["navigates"] is False
    assert chips[1]["msg"] == "I’d like to approve this week’s plan."


@_needs_node
def test_seeing_the_week_closes_the_sheet_and_lands_on_the_day_that_changed():
    did = _chips([_week_action()])[0]["did"]
    assert did[0] == ["closeAskSheet"], "the receipt stays readable until this is tapped"
    assert did[1] == ["focusChangedWeekDay", THURSDAY, "dinner"]


@_needs_node
def test_a_change_with_no_day_still_offers_the_week_just_without_a_day():
    did = _chips([_week_action(date=None, slot=None)])[0]["did"]
    assert did[1] == ["focusChangedWeekDay", None, None]


@_needs_node
def test_an_approval_is_not_a_tweak_and_sends_you_to_the_list_instead():
    chips = _chips([
        {"kicker": "WEEK UPDATED", "change": "Week approved — your list is ready", "tab": "week"},
        {"kicker": "LIST UPDATED", "change": "12 items ready to shop", "tab": "grocery"},
    ])
    assert [c["label"] for c in chips] == ["Open the list"]
    assert chips[0]["did"] == [["activateTab", "grocery", None]]


@_needs_node
def test_a_grocery_only_turn_is_unchanged_by_any_of_this():
    chips = _chips([{"kicker": "LIST UPDATED", "change": "Added milk", "tab": "grocery"}])
    assert [c["label"] for c in chips] == ["Plan my stops"]
    assert chips[0]["did"] == [["activateTab", "grocery", {"groScreen": "plan"}]]


@_needs_node
def test_a_draft_edit_that_also_touched_the_list_still_offers_the_week():
    """Both areas changed: the primary keeps its documented priority
    (grocery over a draft edit), but the week is still what the household
    was just looking at, so the look-at-it chip stays."""
    chips = _chips([_week_action(), {"kicker": "LIST UPDATED", "change": "Added buns", "tab": "grocery"}])
    assert [c["label"] for c in chips] == ["See your week", "Plan my stops"]
