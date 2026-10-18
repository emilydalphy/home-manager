"""
A chat tool that answered without writing draws no "updated" card, and does
not back a reply that claims a change (overnight/nothing-changed-no-card).

Repro on main, through the real dispatch: swap_dinner_nights on a dinner
already cooked returns 200-shaped {"status": "refused", "message": ...}
(is_error False), so summarize_chat_actions drew "Week updated · Swapped in
Bean Chili on Tuesday has already been cooked — I'…" and
_turn_wrote_anything counted it, letting "I've swapped those dinners" go
out as true. remove_staple on a name that matched nothing ({"found":
false}) drew "Grocery updated · Removed oat milk". And explain_meal_choice
— a read, not named get_/list_ — drew "Household info updated · Saved
what you told me" for "why chili this week?".
"""
from __future__ import annotations

import datetime
import types

from app import agent, main, tools


class _Usage:
    input_tokens = cache_read_input_tokens = cache_creation_input_tokens = output_tokens = 0


def _turn(monkeypatch, name, args, reply="Right."):
    responses = [
        types.SimpleNamespace(
            content=[types.SimpleNamespace(type="tool_use", name=name, input=args, id="tu_1")],
            stop_reason="tool_use", usage=_Usage()),
        types.SimpleNamespace(
            content=[types.SimpleNamespace(type="text", text=reply)],
            stop_reason="end_turn", usage=_Usage()),
    ]
    fake = types.SimpleNamespace(create=lambda **kw: responses.pop(0))
    monkeypatch.setattr(agent, "_client", lambda: types.SimpleNamespace(messages=fake))
    text, conversation = agent.run_agent_turn([], "x")
    return text, main.summarize_chat_actions([], conversation)


def _week():
    monday = datetime.date.today() - datetime.timedelta(days=datetime.date.today().weekday())
    days = tools._week_dates((monday + datetime.timedelta(days=7)).isoformat())
    for name in ("Bean Chili", "Chicken Traybake"):
        tools.add_recipe(name, ingredients=[{"item": "Onion", "qty": "1", "category": "produce"}],
                         prep_time_minutes=10, cook_time_minutes=20, default_servings=4)
    plan = tools.create_weekly_plan(days[0])["weekly_plan_id"]
    cooked = tools.plan_meal(days[1], "Bean Chili", slot="dinner", weekly_plan_id=plan)["entry_id"]
    tools.plan_meal(days[4], "Chicken Traybake", slot="dinner", weekly_plan_id=plan)
    return plan, cooked, days[1], days[4]


def test_a_refused_swap_draws_no_card_and_backs_no_claim(monkeypatch):
    plan, cooked, tue, fri = _week()
    tools.check_off_meal(cooked)

    text, actions = _turn(monkeypatch, "swap_dinner_nights",
                          {"weekly_plan_id": plan, "date_a": tue, "date_b": fri},
                          reply="I've swapped those dinners for you.")

    assert actions == []
    assert text == agent.CHANGE_CLAIM_RETRACTION


def test_a_swap_that_happened_still_draws_its_card(monkeypatch):
    plan, _cooked, tue, fri = _week()

    text, actions = _turn(monkeypatch, "swap_dinner_nights",
                          {"weekly_plan_id": plan, "date_a": tue, "date_b": fri},
                          reply="I've swapped those dinners for you.")

    assert [(a.kicker, a.tab) for a in actions] == [("Week updated", "week")]
    assert text == "I've swapped those dinners for you."


def test_a_staple_name_that_matched_nothing_draws_no_card(monkeypatch):
    _text, actions = _turn(monkeypatch, "remove_staple", {"item": "oat milk"})
    assert actions == []


def test_why_this_meal_is_a_read_not_a_save(monkeypatch):
    _week()
    text, actions = _turn(monkeypatch, "explain_meal_choice", {"meal_name": "Bean Chili"},
                          reply="I've updated your plan.")
    assert actions == []
    assert text == agent.CHANGE_CLAIM_RETRACTION
