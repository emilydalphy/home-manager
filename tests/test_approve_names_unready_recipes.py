"""
Approve says which new dish isn't written up, and one unreachable dish no
longer holds the week back (Emily's picks #27 and #75, 2026-10-26).

- #27: a new dish that couldn't be written up at approval stayed pending
  and the week approved in silence — the list simply had nothing for it.
  The approval now names it, in one line, on the route and the toast.
- #75: one dish Claude couldn't be reached for blocked Approve for the
  whole week. Now approval stops only when NONE could be reached; one
  unreachable beside others that were written approves, and is named.

Every model call is stubbed; no test reaches the network.
"""
from __future__ import annotations

import httpx
import pytest
from anthropic import APIConnectionError

from app import agent, tools
from app.tools import weekly_plan

from test_menu_first_generation import (  # noqa: F401  (fixtures)
    DETAILS, _menu_week, _recipe, _week_start, household, menu_model, recipe_model,
)


def _two_new_dinners(week: str) -> list[dict]:
    days = []
    for i, d in enumerate(_menu_week(week)):
        if d["slot"] == "dinner":
            d = {**d, "meal_name": "Reachable Dinner" if i % 2 else "Unreachable Dinner"}
        days.append(d)
    return days


def _writer_that_cannot_reach(name: str):
    def _fake(spec):
        if spec.get("name") == name:
            raise agent._gave_up("generate_recipe_details_llm", APIConnectionError(
                request=httpx.Request("POST", "https://api.anthropic.com/v1/messages")))
        return dict(DETAILS)
    return _fake


def test_a_dish_that_failed_to_write_is_named_on_approval(household, menu_model, recipe_model):
    week = _week_start()
    menu_model(_menu_week(week))
    plan = agent.generate_weekly_plan(week)
    recipe_model(answer=lambda spec: {})

    result = tools.approve_weekly_plan(plan["weekly_plan_id"])

    assert result["status"] == "approved"
    assert result["recipes_not_ready"] == ["Chettinad Pepper Chicken"]
    assert result["recipes_not_ready_line"] == (
        "Chettinad Pepper Chicken isn’t written up yet, so its groceries aren’t on the list. "
        "You can fill it in from Cook."
    )


def test_a_week_with_every_recipe_written_says_nothing(household, menu_model, recipe_model):
    week = _week_start()
    menu_model(_menu_week(week))
    plan = agent.generate_weekly_plan(week)
    recipe_model()

    result = tools.approve_weekly_plan(plan["weekly_plan_id"])

    assert result["recipes_not_ready"] == []
    assert result["recipes_not_ready_line"] is None


def test_one_unreachable_dish_beside_a_written_one_still_approves(household, menu_model, monkeypatch):
    week = _week_start()
    menu_model(_two_new_dinners(week))
    plan = agent.generate_weekly_plan(week)
    monkeypatch.setattr(agent, "generate_recipe_details_llm", _writer_that_cannot_reach("Unreachable Dinner"))

    result = tools.approve_weekly_plan(plan["weekly_plan_id"])

    assert result["status"] == "approved"
    assert result["recipes_not_ready"] == ["Unreachable Dinner"]
    assert "Unreachable Dinner isn’t written up yet" in result["recipes_not_ready_line"]
    assert _recipe("Reachable Dinner")["details_pending"] is False
    items = {g["item"].lower() for g in tools.list_grocery_list("needed")}
    assert "chicken thighs" in items, "the dish that WAS written got nothing on the list"


def test_when_no_dish_could_be_reached_approval_still_stops(household, menu_model, monkeypatch):
    week = _week_start()
    menu_model(_two_new_dinners(week))
    plan = agent.generate_weekly_plan(week)

    def _never(spec):
        raise agent._gave_up("generate_recipe_details_llm", APIConnectionError(
            request=httpx.Request("POST", "https://api.anthropic.com/v1/messages")))
    monkeypatch.setattr(agent, "generate_recipe_details_llm", _never)

    with pytest.raises(agent.AssistantUnavailableError):
        tools.approve_weekly_plan(plan["weekly_plan_id"])
    assert tools.get_weekly_plan(plan["weekly_plan_id"])["status"] == "draft"


def test_the_route_carries_the_line(household, menu_model, monkeypatch, signed_in):
    week = _week_start()
    menu_model(_two_new_dinners(week))
    plan = agent.generate_weekly_plan(week)
    monkeypatch.setattr(agent, "generate_recipe_details_llm", _writer_that_cannot_reach("Unreachable Dinner"))

    res = signed_in.post(f"/api/week/{plan['week_start_date']}/approve", json={})

    assert res.status_code == 200
    body = res.json()
    assert body["status"] == "approved"
    assert body["recipes_not_ready"] == ["Unreachable Dinner"]
    assert body["recipes_not_ready_line"].startswith("Unreachable Dinner isn’t written up yet")


@pytest.mark.parametrize("written,stops", [
    ({}, False),
    ({"unreachable": ["A"], "failed": ["A"]}, True),
    ({"unreachable": ["A", "B"], "failed": ["A", "B"]}, True),
    ({"unreachable": ["A"], "failed": ["A"], "filled": ["B"]}, False),
    # A clash can be a name refused before any call: not a sign Claude answered.
    ({"unreachable": ["A"], "failed": ["A"], "clashed": ["B"]}, True),
    # B was reached and came back wrong: Claude answered, so not "none reached".
    ({"unreachable": ["A"], "failed": ["A", "B"]}, False),
])
def test_none_reached_is_only_every_attempt_unreachable(written, stops):
    assert weekly_plan._none_reached(written) is stops


def test_two_dishes_are_said_together():
    assert weekly_plan.recipes_not_ready_line(["Dal", "Tacos"]) == (
        "Dal and Tacos aren’t written up yet, so their groceries aren’t on the list. "
        "You can fill them in from Cook."
    )


def test_the_toast_reads_the_servers_line():
    src = open("static/shell.js", encoding="utf-8").read()
    start = src.index("async function submitWeekApproval(")
    body = src[start:src.index("function showApproveConfirm(", start)]
    assert "approval.recipes_not_ready_line" in body
