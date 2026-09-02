"""
Prep schedules generate themselves, and only when the week needs one.

Before this, prep tasks depended on the chat assistant remembering to
offer them — so a week planned anywhere else (onboarding's first week,
the plan screen) never got one, and even in chat it relied on the model
choosing to every time. In practice it never happened once: the
prep_tasks table was empty from the day the feature shipped, so the
Cooker view had nothing to show.
"""
import pytest

from app import agent, tools


def _week(meal="Chili", date_str="2026-08-31"):
    return [{"date": date_str, "slot": "dinner", "meal_name": meal, "food_groups": []}]


def _prep_calls(monkeypatch):
    """Record prep-schedule generations instead of calling the model."""
    calls = []
    monkeypatch.setattr(agent, "generate_prep_schedule", lambda plan_id: calls.append(plan_id))
    return calls


def test_a_week_with_advance_prep_gets_a_schedule_without_being_asked(monkeypatch):
    tools.add_recipe(
        "Marinated chicken",
        ingredients=[{"item": "chicken", "qty": "1 kg"}],
        advance_prep_notes="Marinate at least 4 hours ahead",
    )
    monkeypatch.setattr(agent, "generate_weekly_plan_llm", lambda ctx: _week("Marinated chicken"))
    calls = _prep_calls(monkeypatch)

    plan = agent.generate_weekly_plan("2026-08-31")

    assert calls == [plan["weekly_plan_id"]], (
        "a week containing a recipe that needs advance prep should build its schedule itself"
    )


def test_a_week_with_nothing_to_prep_does_not_pay_for_a_model_call(monkeypatch):
    """
    generate_prep_schedule makes its own model call. Most weeks genuinely
    have nothing to prep ahead, and running it unconditionally would buy
    an empty answer with a real round trip every single time.
    """
    tools.add_recipe("Chili", ingredients=[{"item": "beans", "qty": "1 tin"}])
    monkeypatch.setattr(agent, "generate_weekly_plan_llm", lambda ctx: _week("Chili"))
    calls = _prep_calls(monkeypatch)

    agent.generate_weekly_plan("2026-08-31")

    assert calls == [], "no advance prep in the week means no reason to call the model"


def test_whitespace_prep_notes_do_not_count_as_needing_prep(monkeypatch):
    tools.add_recipe(
        "Chili",
        ingredients=[{"item": "beans", "qty": "1 tin"}],
        advance_prep_notes="   ",
    )
    monkeypatch.setattr(agent, "generate_weekly_plan_llm", lambda ctx: _week("Chili"))
    calls = _prep_calls(monkeypatch)

    agent.generate_weekly_plan("2026-08-31")

    assert calls == []


def test_a_broken_prep_schedule_does_not_lose_the_week(monkeypatch):
    """
    The plan is the thing that matters. Its optional prep schedule failing
    must not take a successfully generated week down with it.
    """
    tools.add_recipe(
        "Marinated chicken",
        ingredients=[{"item": "chicken", "qty": "1 kg"}],
        advance_prep_notes="Marinate overnight",
    )
    monkeypatch.setattr(agent, "generate_weekly_plan_llm", lambda ctx: _week("Marinated chicken"))

    def explode(plan_id):
        raise RuntimeError("the prep model call blew up")

    monkeypatch.setattr(agent, "generate_prep_schedule", explode)

    plan = agent.generate_weekly_plan("2026-08-31")
    assert plan["weekly_plan_id"], "the week still saved"
    assert tools.get_weekly_plan(plan["weekly_plan_id"])["meals"], "and still has its meals"


def test_the_onboarding_route_gets_a_prep_schedule_too(monkeypatch, signed_in):
    """
    The original bug in one line: onboarding generates a plan with no chat
    turn involved, so there was never an opportunity for the assistant to
    offer a prep schedule. That plan could never have one.
    """
    tools.add_recipe(
        "Marinated chicken",
        ingredients=[{"item": "chicken", "qty": "1 kg"}],
        advance_prep_notes="Marinate at least 4 hours ahead",
    )
    monkeypatch.setattr(agent, "generate_weekly_plan_llm", lambda ctx: _week("Marinated chicken"))
    calls = _prep_calls(monkeypatch)

    res = signed_in.post("/api/onboarding/generate-first-plan")

    assert res.status_code == 200
    assert len(calls) == 1, "a plan generated outside chat should still get its prep schedule"
