"""
A failed week generation must leave nothing behind, and one week must
never be generated twice at once.

Both of these were real, observed failures on 2026-08-31: one question
ran a full generation twice, and left a weekly_plans row with zero meals
sharing a week with the good plan. Neither had any test cover, which is
part of why an earlier fix for the same shape of bug could regress
without anything noticing.
"""
import threading
import time

import pytest

from app import agent, tools
from app.db import get_conn


def _plan_rows():
    conn = get_conn()
    rows = conn.execute(
        "SELECT id, week_start_date, (SELECT COUNT(*) FROM meal_plan_entries m "
        "WHERE m.weekly_plan_id = weekly_plans.id) AS meals FROM weekly_plans ORDER BY id"
    ).fetchall()
    conn.close()
    return rows


def _one_day(date_str="2026-08-31"):
    return [{"date": date_str, "slot": "dinner", "meal_name": "Chili", "food_groups": []}]


def test_a_generation_that_fails_partway_leaves_no_plan_behind(monkeypatch):
    """
    The exact 2026-08-31 failure: the model call succeeded, the plan row
    was created, and something after it threw. Before this, the row
    survived as an empty week that the app then treated as "this week".
    """
    monkeypatch.setattr(agent, "generate_weekly_plan_llm", lambda ctx: _one_day())

    def explode(*args, **kwargs):
        raise RuntimeError("grocery cleanup blew up")

    monkeypatch.setattr(tools, "clear_stale_grocery_items", explode)

    assert _plan_rows() == []
    with pytest.raises(RuntimeError):
        agent.generate_weekly_plan("2026-08-31")

    assert _plan_rows() == [], "a failed generation must leave no weekly_plans row at all"


def test_the_rollback_takes_any_half_written_meals_with_it(monkeypatch):
    """
    A failure late in population leaves meals already attached. Removing
    the plan but orphaning its meals would be a worse mess than the one
    being fixed.
    """
    monkeypatch.setattr(agent, "generate_weekly_plan_llm", lambda ctx: _one_day())

    def explode(*args, **kwargs):
        raise RuntimeError("finishing the week blew up")

    monkeypatch.setattr(agent, "_finish_week_slots", explode)

    with pytest.raises(RuntimeError):
        agent.generate_weekly_plan("2026-08-31")

    assert _plan_rows() == []
    conn = get_conn()
    orphans = conn.execute("SELECT COUNT(*) FROM meal_plan_entries").fetchone()[0]
    conn.close()
    assert orphans == 0, "meals from the abandoned plan must go with it"


def test_the_original_error_survives_the_rollback(monkeypatch):
    """
    The rollback runs while an exception is propagating. If it raised, it
    would replace the real error with its own — hiding the thing that
    actually went wrong, which is the problem this whole area exists to
    fix.
    """
    monkeypatch.setattr(agent, "generate_weekly_plan_llm", lambda ctx: _one_day())
    monkeypatch.setattr(tools, "clear_stale_grocery_items",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("the real problem")))

    def rollback_also_fails(*args, **kwargs):
        raise RuntimeError("and the rollback broke too")

    monkeypatch.setattr(tools, "discard_failed_plan", rollback_also_fails)

    with pytest.raises(RuntimeError, match="the real problem"):
        agent.generate_weekly_plan("2026-08-31")


def test_a_successful_generation_still_saves_its_plan(monkeypatch):
    """The guard must not eat the normal case."""
    monkeypatch.setattr(agent, "generate_weekly_plan_llm", lambda ctx: _one_day())

    plan = agent.generate_weekly_plan("2026-08-31")

    rows = _plan_rows()
    assert len(rows) == 1
    assert rows[0]["meals"] > 0, "a good generation attaches meals"
    assert plan["week_start_date"] == "2026-08-31"


def test_two_requests_for_the_same_week_produce_one_plan(monkeypatch):
    """
    Three routes can start a generation — chat, onboarding, and the plan
    screen — and a double-tap or a chat request racing a screen request
    used to run two full generations for one week. That's ~40 seconds and
    ~9 cents of pure waste each time, plus two plans for the same week.
    """
    calls = []

    def slow_generation(ctx):
        calls.append(1)
        time.sleep(0.4)   # long enough for the second request to arrive mid-flight
        return _one_day()

    monkeypatch.setattr(agent, "generate_weekly_plan_llm", slow_generation)

    results = []

    def go():
        results.append(agent.generate_weekly_plan("2026-08-31"))

    first = threading.Thread(target=go)
    second = threading.Thread(target=go)
    first.start()
    time.sleep(0.1)       # let the first take the lock, as a real double-tap would
    second.start()
    first.join(timeout=15)
    second.join(timeout=15)

    assert len(calls) == 1, "the second request must not run its own generation"
    assert len(_plan_rows()) == 1, "two concurrent requests must not create two plans"
    assert len(results) == 2, "both callers still get an answer"
    assert results[0]["weekly_plan_id"] == results[1]["weekly_plan_id"], (
        "the waiting caller should be handed the plan that was just built"
    )


def test_an_intentional_regeneration_still_works(monkeypatch):
    """
    Serializing must not turn "plan it again" into a no-op — a household
    that asks for a fresh week after seeing one should get a fresh week.
    """
    monkeypatch.setattr(agent, "generate_weekly_plan_llm", lambda ctx: _one_day())

    first = agent.generate_weekly_plan("2026-08-31")
    second = agent.generate_weekly_plan("2026-08-31")

    assert second["weekly_plan_id"] != first["weekly_plan_id"], (
        "a sequential re-plan is a real request, not a duplicate to swallow"
    )


def test_a_failed_generation_does_not_empty_the_grocery_list(monkeypatch):
    """
    Clearing last week's leftover items is a hard DELETE that nothing can
    undo. Running it before the plan was safely built meant a generation
    that died partway emptied the household's list and then failed —
    an error, no new plan, and a shopping list quietly missing things they
    still needed.
    """
    monkeypatch.setattr(agent, "generate_weekly_plan_llm", lambda ctx: _one_day())
    tools.add_grocery_item("beans")
    tools.add_grocery_item("rice")
    assert len(tools.list_grocery_list()) == 2

    monkeypatch.setattr(agent, "_finish_week_slots",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("died late")))
    with pytest.raises(RuntimeError):
        agent.generate_weekly_plan("2026-08-31")

    assert len(tools.list_grocery_list()) == 2, (
        "a failed generation must not take the grocery list with it"
    )


def test_a_failed_generation_does_not_leave_recipes_looking_cooked(monkeypatch):
    """
    Planning a meal bumps times_cooked/last_cooked_date. Those feed the
    variety rules, so residue from an abandoned attempt biases the NEXT
    week away from meals nobody actually ate.
    """
    tools.add_recipe("Chili", ingredients=[{"item": "beans", "qty": "1 tin"}])
    before = {r["name"]: r["times_cooked"] for r in tools.list_recipes()}

    monkeypatch.setattr(agent, "generate_weekly_plan_llm", lambda ctx: _one_day())
    monkeypatch.setattr(agent, "_finish_week_slots",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("died late")))
    with pytest.raises(RuntimeError):
        agent.generate_weekly_plan("2026-08-31")

    after = {r["name"]: r["times_cooked"] for r in tools.list_recipes()}
    assert after == before, "an abandoned plan must not leave recipes looking recently cooked"
    assert all(not r["last_cooked_date"] for r in tools.list_recipes())


def test_a_waiting_caller_is_not_handed_a_stale_plan_when_the_other_failed(monkeypatch):
    """
    The nastiest version: the household already has this week's plan and
    taps "re-plan" twice. The first attempt dies. Handing the second
    caller the OLD plan would report a failure as success — the screen
    re-renders the very week they were trying to replace.
    """
    monkeypatch.setattr(agent, "generate_weekly_plan_llm", lambda ctx: _one_day())
    existing = agent.generate_weekly_plan("2026-08-31")

    attempts = []

    def failing_then_working(ctx):
        attempts.append(1)
        if len(attempts) == 1:
            time.sleep(0.4)
            raise RuntimeError("Claude blew up mid-generation")
        return _one_day()

    monkeypatch.setattr(agent, "generate_weekly_plan_llm", failing_then_working)

    errors, results = [], []

    def leader():
        try:
            agent.generate_weekly_plan("2026-08-31")
        except Exception as e:
            errors.append(e)

    def waiter():
        results.append(agent.generate_weekly_plan("2026-08-31"))

    t1, t2 = threading.Thread(target=leader), threading.Thread(target=waiter)
    t1.start()
    time.sleep(0.1)
    t2.start()
    t1.join(timeout=15)
    t2.join(timeout=15)

    assert len(errors) == 1, "the failing caller must still see its error"
    assert len(results) == 1
    assert results[0]["weekly_plan_id"] != existing["weekly_plan_id"], (
        "the waiter must generate for itself, not be handed the plan it was replacing"
    )
    assert len(attempts) == 2, "the waiter must actually run its own generation"


def test_a_waiting_caller_with_different_instructions_is_not_swallowed(monkeypatch):
    """
    Two requests racing aren't always the same request. A second caller
    carrying a constraint — "no fish this week" — must not have it
    silently discarded because someone else's generation happened to be
    in flight. An allergy is the worst version of this.
    """
    seen = []

    def slow(ctx):
        seen.append(ctx.get("constraints_notes"))
        time.sleep(0.4)
        return _one_day()

    monkeypatch.setattr(agent, "generate_weekly_plan_llm", slow)

    def plain():
        agent.generate_weekly_plan("2026-08-31")

    def with_constraint():
        agent.generate_weekly_plan("2026-08-31", constraints_notes="NO FISH - allergy")

    t1, t2 = threading.Thread(target=plain), threading.Thread(target=with_constraint)
    t1.start()
    time.sleep(0.1)
    t2.start()
    t1.join(timeout=15)
    t2.join(timeout=15)

    assert "NO FISH - allergy" in seen, "the second caller's constraint must reach generation"


def test_onboarding_and_the_plan_screen_lock_against_each_other(monkeypatch):
    """
    Onboarding keys its plan by today's date while the plan screen uses
    the week's Monday. Locking on the raw string put them in different
    buckets six days out of seven — so the two callers most likely to
    overlap during a household's first hour never serialized at all.
    """
    monday = "2026-08-31"
    wednesday = "2026-09-02"
    assert agent._week_lock_key(wednesday) == monday
    assert agent._week_lock_key(monday) == monday
    # A date that won't parse locks on itself rather than raising.
    assert agent._week_lock_key("not-a-date") == "not-a-date"
