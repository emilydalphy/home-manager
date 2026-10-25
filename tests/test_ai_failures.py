"""
When the AI call fails (overnight/ai-failure-hunt, 2026-10-25).

Found by pointing a real server at a fake Anthropic API that dropped the
connection, answered 529, sent prose where a tool call was forced, or an
empty tool call, and driving the routes over HTTP:

- Approve with Claude unreachable APPROVED the week anyway: every new
  dish stayed unwritten, the list got nothing for them, and the toast said
  "Week approved." Now nothing is approved, nothing is written, and Approve
  again works once Claude answers. One recipe that comes back wrong still
  approves, as designed (test_menu_first_generation).
- The morning report never heard of most AI failures: a route that turns
  one into a 200 ("options_unavailable"), a 400, or a pending recipe filed
  nothing. Every call that gives up now files kind "ai" where = the call,
  and the route's own 503 is not filed a second time.
- An unusable answer read as something else: a week with no meals was a
  400 about "the model call"; a Swap with no dish blamed the household's
  allergies; chat's two give-up replies opened "Sorry".

Every model call is stubbed; no test reaches the network.
"""
from __future__ import annotations

import types

import httpx
import pytest
from anthropic import APIConnectionError

from app import agent, tools
from app.db import get_conn
from app.tools import swap_in_place

from test_menu_first_generation import (  # noqa: F401  (fixtures)
    DETAILS, _menu_week, _week_start, household, menu_model, recipe_model,
)


def _rows():
    conn = get_conn()
    rows = [dict(r) for r in conn.execute("SELECT kind, where_, detail FROM error_events ORDER BY id")]
    conn.close()
    return rows


def _forget_rows():
    """Drafting in these tests makes offline model calls of its own (the
    allergen re-pick, refused by conftest's offline client) and those are
    filed too; each test reads only what its own step filed."""
    conn = get_conn()
    conn.execute("DELETE FROM error_events")
    conn.commit()
    conn.close()


def _first_dinner(plan_id: int) -> dict:
    conn = get_conn()
    row = conn.execute(
        "SELECT id FROM meal_plan_entries WHERE weekly_plan_id = ? AND slot = 'dinner' "
        "AND slot_state = 'planned' ORDER BY date LIMIT 1", (plan_id,),
    ).fetchone()
    conn.close()
    return {"id": row["id"]}


def _connection_error():
    return APIConnectionError(request=httpx.Request("POST", "https://api.anthropic.com/v1/messages"))


class _Messages:
    def __init__(self, answer):
        self._answer = answer

    def create(self, **kwargs):
        if isinstance(self._answer, BaseException):
            raise self._answer
        return self._answer

    def stream(self, **kwargs):
        raise self._answer


@pytest.fixture
def anthropic_answers(monkeypatch):
    """Every Anthropic call gets `answer` (raised if it's an exception)."""
    monkeypatch.setattr(agent.time, "sleep", lambda s: None)

    def _set(answer):
        monkeypatch.setattr(agent, "_client", lambda: types.SimpleNamespace(messages=_Messages(answer)))
    return _set


def _reply(text: str):
    usage = types.SimpleNamespace(input_tokens=1, output_tokens=1, cache_read_input_tokens=0,
                                  cache_creation_input_tokens=0, server_tool_use=None)
    return types.SimpleNamespace(
        content=[types.SimpleNamespace(type="text", text=text)],
        stop_reason="end_turn", usage=usage,
    )


# ---------- approve while Claude is unreachable ----------


def _unreachable_writer(spec):
    raise agent._gave_up("generate_recipe_details_llm", _connection_error())


def test_approve_with_claude_unreachable_approves_nothing_and_writes_nothing(
    household, menu_model, monkeypatch, signed_in,
):
    week = _week_start()
    menu_model(_menu_week(week))
    plan = agent.generate_weekly_plan(week)
    monkeypatch.setattr(agent, "generate_recipe_details_llm", _unreachable_writer)
    _forget_rows()

    res = signed_in.post(f"/api/week/{plan['week_start_date']}/approve", json={})

    assert res.status_code == 503
    assert res.json()["detail"] == tools.weekly_plan.RECIPES_UNREACHABLE_LINE
    assert "Sorry" not in res.json()["detail"]
    assert tools.get_weekly_plan(plan["weekly_plan_id"])["status"] == "draft"
    assert tools.list_grocery_list("needed") == []
    # Filed once, as the AI call — not again as the route's 503.
    assert {(r["kind"], r["where_"]) for r in _rows()} == {("ai", "generate_recipe_details_llm")}


def test_approve_again_once_claude_answers_lands_the_week_and_its_list(
    household, menu_model, recipe_model, monkeypatch,
):
    week = _week_start()
    menu_model(_menu_week(week))
    plan = agent.generate_weekly_plan(week)
    monkeypatch.setattr(agent, "generate_recipe_details_llm", _unreachable_writer)
    with pytest.raises(agent.AssistantUnavailableError):
        tools.approve_weekly_plan(plan["weekly_plan_id"])

    recipe_model()
    result = tools.approve_weekly_plan(plan["weekly_plan_id"])

    assert result["status"] == "approved"
    assert result["was_already_approved"] is False
    items = {g["item"].lower() for g in tools.list_grocery_list("needed")}
    assert "chicken thighs" in items and "cauliflower" in items


# ---------- an outage reaches the morning report, once ----------


def test_a_chat_outage_is_filed_as_the_ai_call_and_not_twice(signed_in, anthropic_answers):
    anthropic_answers(_connection_error())

    res = signed_in.post("/api/chat", json={"message": "what's for dinner?"})

    assert res.status_code == 503
    assert [(r["kind"], r["where_"], r["detail"]) for r in _rows()] == [
        ("ai", "run_agent_turn", "APIConnectionError"),
    ]


def test_an_outage_a_route_answers_with_a_200_is_still_filed(household, menu_model, anthropic_answers):
    """Swap's three picks degrade to options_unavailable — a 200 the
    morning report used to never hear of."""
    week = _week_start()
    menu_model(_menu_week(week))
    plan = agent.generate_weekly_plan(week)
    entry = _first_dinner(plan["weekly_plan_id"])
    anthropic_answers(_connection_error())
    _forget_rows()

    out = tools.swap_options(plan["weekly_plan_id"], entry["id"])

    assert out["options_unavailable"] is True
    assert ("ai", "swap_options") in {(r["kind"], r["where_"]) for r in _rows()}


# ---------- an answer with nothing usable in it ----------


def test_a_week_with_no_meals_in_the_answer_is_a_plain_503_and_saves_nothing(
    household, menu_model, signed_in,
):
    week = _week_start()
    menu_model([])
    _forget_rows()

    res = signed_in.post(f"/api/week/{week}/generate", json={})

    assert res.status_code == 503
    detail = res.json()["detail"]
    assert "model" not in detail and "nothing was saved" in detail
    assert tools.get_weekly_plan().get("weekly_plan_id") is None
    assert [(r["kind"], r["where_"]) for r in _rows()] == [("ai", "generate_weekly_plan")]


def test_a_swap_with_no_dish_in_the_answer_does_not_blame_their_allergies(household, menu_model):
    week = _week_start()
    menu_model(_menu_week(week))
    plan = agent.generate_weekly_plan(week)
    entry = _first_dinner(plan["weekly_plan_id"])
    _forget_rows()

    out = tools.swap_meal_in_place(plan["weekly_plan_id"], entry["id"], picker=lambda ctx: {})

    assert out["status"] == "refused"
    assert out["message"] == swap_in_place.NO_PICK
    assert "can’t have" not in out["message"]
    assert ("ai", "swap_in_place") in {(r["kind"], r["where_"]) for r in _rows()}


def test_an_empty_chat_reply_is_plain_and_filed(signed_in, anthropic_answers):
    anthropic_answers(_reply("   "))

    res = signed_in.post("/api/chat", json={"message": "hi"})

    assert res.status_code == 200
    assert res.json()["reply"] == agent.CHAT_EMPTY_REPLY_LINE
    assert not res.json()["reply"].startswith("Sorry")
    assert ("ai", "run_agent_turn", "empty reply") in {
        (r["kind"], r["where_"], r["detail"]) for r in _rows()
    }


# ---------- review round (2026-10-25) ----------


def test_an_outage_at_approve_leaves_an_allergy_labelled_dish_as_it_was(
    household, menu_model, monkeypatch,
):
    """The recipe pass's allergen sweep re-picks (or opens) a dish whose
    name names a must-avoid. Under an outage approval refuses, so the
    sweep must not rewrite the draft underneath that refusal."""
    tools.set_member_dietary_restrictions("Ana", ["dairy allergy"])
    week = _week_start()
    days = _menu_week(week)
    first = next(d for d in days if d["slot"] == "dinner")
    first.update(meal_name="Dairy-Free Alfredo", main_protein="", cuisine="Italian",
                 dish_note="cashew cream sauce over fettuccine")
    menu_model(days)
    plan = agent.generate_weekly_plan(week)
    conn = get_conn()
    before = [dict(r) for r in conn.execute(
        "SELECT id, date, slot, slot_state, recipe_id, open_reason FROM meal_plan_entries "
        "WHERE weekly_plan_id = ? ORDER BY id", (plan["weekly_plan_id"],))]
    conn.close()
    swept = []
    monkeypatch.setattr(agent._allergen_gate, "sweep_plan", lambda *a, **k: swept.append(a))
    monkeypatch.setattr(agent, "generate_recipe_details_llm", _unreachable_writer)

    with pytest.raises(agent.AssistantUnavailableError):
        tools.approve_weekly_plan(plan["weekly_plan_id"])

    assert swept == [], "no re-pick under a refused approval"
    conn = get_conn()
    after = [dict(r) for r in conn.execute(
        "SELECT id, date, slot, slot_state, recipe_id, open_reason FROM meal_plan_entries "
        "WHERE weekly_plan_id = ? ORDER BY id", (plan["weekly_plan_id"],))]
    conn.close()
    assert after == before


def test_a_chat_tool_whose_model_call_gave_up_is_one_row_not_two(signed_in, anthropic_answers, monkeypatch):
    tool_call = types.SimpleNamespace(type="tool_use", id="toolu_1", name="list_members", input={})
    usage = _reply("").usage
    answers = iter([
        types.SimpleNamespace(content=[tool_call], stop_reason="tool_use", usage=usage),
        _reply("I couldn't reach the planner just now."),
    ])

    class _Scripted:
        def create(self, **kwargs):
            if kwargs.get("max_tokens") == 0:
                return _reply("")
            return next(answers)

    monkeypatch.setattr(agent, "_client", lambda: types.SimpleNamespace(messages=_Scripted()))

    def _gives_up(**kwargs):
        raise agent._gave_up("generate_weekly_plan_llm", _connection_error())
    monkeypatch.setitem(agent.TOOL_FUNCTIONS, "list_members", _gives_up)
    _forget_rows()

    res = signed_in.post("/api/chat", json={"message": "who lives here?"})

    assert res.status_code == 200
    assert [(r["kind"], r["where_"]) for r in _rows()] == [("ai", "generate_weekly_plan_llm")]
