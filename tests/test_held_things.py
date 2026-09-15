"""
"Noted" must never note nothing — Pomona keeps what it can't act on yet.

Loop Board card of that name (Emily, 2026-09-15; PRODUCT_FLOWS.md flow H1,
"Pomona, hold this", the founding anchor). From the walk that day: "We
ended up going to the in-laws for dinner last night" got "Noted — …" and
nothing was saved. This file pins the fix end to end:

    1. the tool (app/tools/held.py) keeps the person's words, who said it
       and when, per household; blank text is refused; "Done with this"
       takes it off and Undo puts it back;
    2. the routes the two screens read (GET /api/held, /done, /restore);
    3. the weekly planner is handed the held things as context, and is
       handed nothing when nothing is held;
    4. the chat has the tools (TOOL_DEFINITIONS + TOOL_FUNCTIONS agree),
       the system prompt tells it when and what to say;
    5. the in-laws sentence, through the real dispatch loop with a stubbed
       model that chooses hold_thing: a stored row, the one-line reply,
       and the Holding chip on the turn's actions.

Every test here fails on `main` (no table, no tool, no routes).
"""
from __future__ import annotations

import datetime
import inspect
import json
import types

import pytest

from app import agent, households, tools
from app.db import get_conn
from app.main import summarize_chat_actions
from app.tools import held as held_module

IN_LAWS = "We ended up going to the in-laws for dinner last night so didn't make the dinner we had planned"


def _adult(name: str, household: int = 1) -> int:
    with tools.use_household(household):
        member_id = tools.add_member(name)["member_id"]
        tools.set_member_age_group(name, "Adult")
    return member_id


def _week_start(offset_weeks: int = 1) -> str:
    today = datetime.date.today()
    monday = today - datetime.timedelta(days=today.weekday())
    return (monday + datetime.timedelta(days=7 * offset_weeks)).isoformat()


# ==========================================================================
# 1. The tool
# ==========================================================================

def test_hold_thing_keeps_their_words_and_lists_them():
    result = tools.hold_thing("Nana's coming on the 28th")
    assert result["held"] is True
    assert result["already_held"] is False
    assert result["text"] == "Nana's coming on the 28th"
    assert result["when"] == "today"
    # The tool hands back the one line the chat is to say, so the prompt
    # and the code can't drift apart.
    assert result["reply"] == held_module.HOLD_REPLY == "Holding that. I'll bring it up when it's useful."

    held = tools.list_held_things()
    assert [h["text"] for h in held] == ["Nana's coming on the 28th"]
    assert held[0]["id"] == result["id"]


def test_hold_thing_records_who_said_it_from_the_session():
    emily = _adult("Emily")
    _adult("Vineeth")
    with tools.use_member(emily):
        result = tools.hold_thing("ask the dentist about the retainer")
    assert result["member_id"] == emily
    assert result["said_by"] == "Emily"
    assert tools.list_held_things()[0]["said_by"] == "Emily"


def test_a_one_adult_household_is_credited_without_a_pick():
    _adult("Emily")
    result = tools.hold_thing("soccer might move to Thursdays")
    assert result["said_by"] == "Emily"


def test_nobody_picked_is_simply_nobodys():
    _adult("Emily")
    _adult("Vineeth")
    result = tools.hold_thing("we're out of birthday candles somewhere")
    assert result["member_id"] is None
    assert result["said_by"] == ""


def test_blank_text_is_refused_and_holds_nothing():
    assert tools.hold_thing("   ")["held"] is False
    assert tools.hold_thing("")["held"] is False
    assert tools.list_held_things() == []


def test_the_same_words_are_not_held_twice():
    first = tools.hold_thing("Nana's coming on the 28th")
    again = tools.hold_thing("  nana's coming on the 28th ")
    assert again["already_held"] is True
    assert again["id"] == first["id"]
    assert len(tools.list_held_things()) == 1


def test_whitespace_is_squashed_and_a_runaway_paste_is_cut():
    result = tools.hold_thing("Nana's   coming\n\non the 28th")
    assert result["text"] == "Nana's coming on the 28th"
    long = "x" * 1000
    assert len(tools.hold_thing(long)["text"]) <= held_module.MAX_TEXT_LEN


def test_newest_first():
    tools.hold_thing("first")
    tools.hold_thing("second")
    assert [h["text"] for h in tools.list_held_things()] == ["second", "first"]


def test_done_with_this_takes_it_off_and_restore_puts_it_back():
    held_id = tools.hold_thing("ask the dentist about the retainer")["id"]
    result = tools.resolve_held_thing(held_id)
    assert result == {"resolved": True, "id": held_id, "text": "ask the dentist about the retainer"}
    assert tools.list_held_things() == []
    # Resolved, not deleted — Undo has something to put back.
    conn = get_conn()
    row = conn.execute("SELECT resolved_at FROM held_things WHERE id = ?", (held_id,)).fetchone()
    conn.close()
    assert row["resolved_at"] is not None

    restored = tools.restore_held_thing(held_id)
    assert restored["restored"] is True
    assert [h["id"] for h in tools.list_held_things()] == [held_id]


def test_held_things_belong_to_the_household_not_the_member():
    """Both adults see the same list — and another household sees none of it."""
    emily = _adult("Emily")
    vineeth = _adult("Vineeth")
    with tools.use_member(emily):
        tools.hold_thing("Nana's coming on the 28th")
    with tools.use_member(vineeth):
        assert [h["text"] for h in tools.list_held_things()] == ["Nana's coming on the 28th"]

    beta = households.create_household("The Beta Testers", "a-safe-distinct-passphrase")
    with tools.use_household(beta):
        assert tools.list_held_things() == []
        tools.hold_thing("book the car in")
        assert [h["text"] for h in tools.list_held_things()] == ["book the car in"]
    assert [h["text"] for h in tools.list_held_things()] == ["Nana's coming on the 28th"]


def test_another_households_id_cannot_be_resolved_or_restored():
    beta = households.create_household("The Beta Testers", "a-safe-distinct-passphrase")
    with tools.use_household(beta):
        theirs = tools.hold_thing("book the car in")["id"]
    with pytest.raises(ValueError):
        tools.resolve_held_thing(theirs)
    with pytest.raises(ValueError):
        tools.restore_held_thing(theirs)
    with tools.use_household(beta):
        assert [h["id"] for h in tools.list_held_things()] == [theirs]


def test_when_is_said_the_way_a_person_would():
    today = datetime.date(2026, 9, 15)  # a Tuesday
    label = held_module.when_label
    assert label("2026-09-15", today) == "today"
    assert label("2026-09-14", today) == "yesterday"
    assert label("2026-09-12", today) == "Saturday"
    assert label("2026-09-01", today) == "Sep 1"
    assert label("2025-12-24", today) == "Dec 24 2025"
    assert label("not a date", today) == ""


# ==========================================================================
# 2. The routes
# ==========================================================================

def test_the_list_route_reads_the_household_list(signed_in):
    _adult("Emily")
    tools.hold_thing("Nana's coming on the 28th")
    body = signed_in.get("/api/held").json()
    assert [h["text"] for h in body["held"]] == ["Nana's coming on the 28th"]
    assert body["held"][0]["said_by"] == "Emily"
    assert body["held"][0]["when"] == "today"


def test_the_list_route_is_empty_not_missing_when_nothing_is_held(signed_in):
    assert signed_in.get("/api/held").json() == {"held": []}


def test_the_done_and_restore_routes(signed_in):
    held_id = tools.hold_thing("ask the dentist about the retainer")["id"]
    res = signed_in.post(f"/api/held/{held_id}/done")
    assert res.status_code == 200
    assert res.json()["resolved"] is True
    assert signed_in.get("/api/held").json()["held"] == []

    res = signed_in.post(f"/api/held/{held_id}/restore")
    assert res.status_code == 200
    assert res.json()["restored"] is True
    assert [h["id"] for h in signed_in.get("/api/held").json()["held"]] == [held_id]


def test_the_routes_answer_404_for_an_unknown_or_foreign_id(signed_in):
    beta = households.create_household("The Beta Testers", "a-safe-distinct-passphrase")
    with tools.use_household(beta):
        theirs = tools.hold_thing("book the car in")["id"]
    assert signed_in.post(f"/api/held/{theirs}/done").status_code == 404
    assert signed_in.post(f"/api/held/{theirs}/restore").status_code == 404
    assert signed_in.post("/api/held/999999/done").status_code == 404


def test_the_routes_need_a_signed_in_session(client):
    assert client.get("/api/held").status_code in (401, 303, 307)


# ==========================================================================
# 3. The planner is handed the held things
# ==========================================================================

def _full_week(week: str, meal: str = "Chili") -> list[dict]:
    return [
        {"date": day, "slot": slot, "meal_name": meal, "is_new_recipe": False, "reasoning": "fits the week"}
        for day in tools._week_dates(week)
        for slot in tools.WEEK_SLOTS
    ]


@pytest.fixture
def stub_model(monkeypatch):
    seen = {}

    def _stub(days):
        def _fake(context):
            seen["context"] = context
            return days
        monkeypatch.setattr(agent, "generate_weekly_plan_llm", _fake)
        return seen

    return _stub


def test_generation_context_is_their_words_who_and_when():
    emily = _adult("Emily")
    _adult("Vineeth")
    with tools.use_member(emily):
        tools.hold_thing("Nana's coming on the 28th")
    tools.hold_thing("ask the dentist about the retainer")
    ctx = tools.held_generation_context()
    # Oldest first, so a run of related remarks reads in order.
    assert ctx == [
        {"said": "Nana's coming on the 28th", "who": "Emily", "when": "today"},
        {"said": "ask the dentist about the retainer", "who": None, "when": "today"},
    ]


def test_the_planner_receives_held_things(stub_model):
    tools.add_recipe("Chili", ingredients=[{"item": "beans", "qty": "1 tin"}])
    week = _week_start()
    seen = stub_model(_full_week(week))
    tools.hold_thing("Nana's coming on the 28th")

    agent.generate_weekly_plan(week)

    assert "held_things" in seen["context"]
    assert seen["context"]["held_things"][0]["said"] == "Nana's coming on the 28th"


def test_the_planner_gets_no_key_at_all_when_nothing_is_held(stub_model):
    tools.add_recipe("Chili", ingredients=[{"item": "beans", "qty": "1 tin"}])
    week = _week_start()
    seen = stub_model(_full_week(week))
    held_id = tools.hold_thing("ask the dentist about the retainer")["id"]
    tools.resolve_held_thing(held_id)

    agent.generate_weekly_plan(week)

    assert "held_things" not in seen["context"]


def test_the_generation_prompt_tells_the_model_what_held_things_are():
    # The bullet lives in the instructions string generate_weekly_plan_llm
    # builds inside its own body, so the module source is the honest check.
    source = inspect.getsource(agent)
    assert "`held_things`, when present" in source
    assert "held:<a few of their words>" in source


# ==========================================================================
# 4. The chat has the tools and the prompt
# ==========================================================================

def test_the_three_tools_are_declared_and_wired():
    declared = {d["name"] for d in agent.TOOL_DEFINITIONS}
    for name in ("hold_thing", "list_held_things", "resolve_held_thing"):
        assert name in declared, name
        assert name in agent.TOOL_FUNCTIONS, name
    assert agent.TOOL_FUNCTIONS["hold_thing"] is tools.hold_thing
    schema = next(d for d in agent.TOOL_DEFINITIONS if d["name"] == "hold_thing")
    assert schema["input_schema"]["required"] == ["text"]


def test_the_system_prompt_says_when_to_hold_and_what_to_say():
    prompt = agent.SYSTEM_PROMPT
    assert "HOLDING THINGS" in prompt
    assert "hold_thing" in prompt
    assert held_module.HOLD_REPLY in prompt
    # The exceptions the card asks for: act on what you can, and don't
    # hold small talk / "never mind".
    assert "never" in prompt.lower() and "mind" in prompt.lower()
    assert "resolve_held_thing" in prompt


# ==========================================================================
# 5. The in-laws sentence, through the real dispatch loop
# ==========================================================================

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


def test_the_in_laws_sentence_is_held_and_answered_in_one_line(monkeypatch):
    emily = _adult("Emily")
    _adult("Vineeth")
    fake = _stub_client(monkeypatch, [
        types.SimpleNamespace(
            content=[_tool_block("hold_thing", {"text": "We ate at the in-laws last night, so the planned dinner didn't happen"})],
            stop_reason="tool_use", usage=_Usage(),
        ),
        types.SimpleNamespace(content=[_text_block(held_module.HOLD_REPLY)], stop_reason="end_turn", usage=_Usage()),
    ])

    with tools.use_member(emily):
        reply, conversation = agent.run_agent_turn([], IN_LAWS)

    # Stored: their words, who, when.
    held = tools.list_held_things()
    assert len(held) == 1
    assert held[0]["text"] == "We ate at the in-laws last night, so the planned dinner didn't happen"
    assert held[0]["said_by"] == "Emily"
    assert held[0]["when"] == "today"

    # The tool ran cleanly and handed the model the line to say.
    handed = [m for m in conversation if m.get("role") == "user" and isinstance(m.get("content"), list)][-1]["content"][0]
    assert handed["type"] == "tool_result" and not handed.get("is_error")
    assert json.loads(handed["content"])["reply"] == held_module.HOLD_REPLY

    # One line, in the app's voice — not "Noted" on its own, not a paragraph.
    assert reply == "Holding that. I'll bring it up when it's useful."
    assert "\n" not in reply
    assert reply.strip().lower() != "noted"
    # The model was offered the tool in the first place.
    assert any(t["name"] == "hold_thing" for t in fake.requests[0]["tools"])

    # The turn's receipt: a Holding chip with their words, leading to the list.
    actions = summarize_chat_actions([], conversation)
    assert len(actions) == 1
    assert actions[0].held is True
    assert actions[0].kicker == "Holding"
    assert actions[0].change.startswith("We ate at the in-laws")
    assert actions[0].href == "/memory"


def test_a_refused_hold_draws_no_chip(monkeypatch):
    _stub_client(monkeypatch, [
        types.SimpleNamespace(content=[_tool_block("hold_thing", {"text": "   "})], stop_reason="tool_use", usage=_Usage()),
        types.SimpleNamespace(content=[_text_block("Nothing in that to keep.")], stop_reason="end_turn", usage=_Usage()),
    ])
    reply, conversation = agent.run_agent_turn([], "never mind")
    assert tools.list_held_things() == []
    assert summarize_chat_actions([], conversation) == []


def test_done_with_that_through_the_chat(monkeypatch):
    held_id = tools.hold_thing("ask the dentist about the retainer")["id"]
    _stub_client(monkeypatch, [
        types.SimpleNamespace(content=[_tool_block("resolve_held_thing", {"held_id": held_id})], stop_reason="tool_use", usage=_Usage()),
        types.SimpleNamespace(content=[_text_block("Done — the dentist thing's off the list.")], stop_reason="end_turn", usage=_Usage()),
    ])
    reply, conversation = agent.run_agent_turn([], "you can drop the dentist thing")
    assert tools.list_held_things() == []
    actions = summarize_chat_actions([], conversation)
    assert len(actions) == 1 and actions[0].held is True
    assert actions[0].kicker == "Done with this"
    assert actions[0].change == "ask the dentist about the retainer"
