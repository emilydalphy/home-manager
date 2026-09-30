"""
The recipes are written while the household reads the draft (2026-09-30).

Production: approving seven new dishes waited 16 seconds on "Writing up
the recipes…". Now the pass starts the moment a draft is saved, on a
background thread, and Approve waits only for what isn't finished — never
writing a recipe twice, never held up by a dish that has been swapped out,
and writing the recipes itself, as before, when the background pass
failed or never ran (a redeploy kills its thread; the pending flags on
disk are what approval works from).

Every model call is stubbed. The suite turns the background pass off
(conftest: DISABLE_BACKGROUND_RECIPES); these tests turn it back on and
join the thread, so nothing outlives its test.
"""
import threading
import time

import pytest

from app import agent, tools
from app.tools import allergen_gate
from app.db import get_conn

from test_menu_first_generation import (  # noqa: F401  (fixtures)
    DETAILS, _menu_week, _week_start, household, menu_model, recipe_model,
)


@pytest.fixture
def background(monkeypatch):
    """Background pass on; every thread it starts recorded for joining.
    The cache warm-up is stubbed so no test reaches for the network."""
    monkeypatch.delenv("DISABLE_BACKGROUND_RECIPES", raising=False)
    monkeypatch.setattr(agent, "_warm_recipe_details_cache", lambda: True)
    started = []
    real = agent.start_background_recipe_pass

    def _recording(plan_id):
        thread = real(plan_id)
        if thread is not None:
            started.append(thread)
        return thread
    monkeypatch.setattr(agent, "start_background_recipe_pass", _recording)
    yield started
    for thread in started:
        thread.join(timeout=10)


def _recipe(name):
    return tools.get_recipe(name)


def test_saving_a_draft_starts_writing_its_recipes(household, menu_model, recipe_model, background):
    week = _week_start()
    seen = recipe_model()
    menu_model(_menu_week(week))

    plan = agent.generate_weekly_plan(week)

    assert len(background) == 1, "the draft's save started one background pass"
    background[0].join(timeout=10)
    assert [s["name"] for s in seen["specs"]] == ["Chettinad Pepper Chicken"]
    assert _recipe("Chettinad Pepper Chicken")["details_pending"] is False
    assert tools.pending_recipes_for_plan(plan["weekly_plan_id"]) == []
    # Still a draft: writing recipes is not approving.
    assert tools.get_weekly_plan(plan["weekly_plan_id"])["status"] != "approved"


def test_approve_after_the_pass_finished_writes_nothing_more(household, menu_model, recipe_model, background):
    week = _week_start()
    seen = recipe_model()
    menu_model(_menu_week(week))
    plan = agent.generate_weekly_plan(week)
    background[0].join(timeout=10)

    result = tools.approve_weekly_plan(plan["weekly_plan_id"])

    assert result["status"] == "approved"
    assert len(seen["specs"]) == 1, "approval found the recipe already written"
    items = {g["item"].lower() for g in tools.list_grocery_list("needed")}
    assert "chicken thighs" in items and "cauliflower" in items


def test_approve_while_the_pass_is_running_waits_for_it_and_writes_nothing_twice(
    household, menu_model, recipe_model, background,
):
    week = _week_start()
    days = []
    for i, d in enumerate(_menu_week(week)):
        if d["slot"] == "dinner":
            d = {**d, "meal_name": f"New Dinner {i}"}
        days.append(d)
    seen = recipe_model(delay=0.4)
    menu_model(days)
    plan = agent.generate_weekly_plan(week)
    assert background and background[0].is_alive(), "the pass is still writing"

    result = tools.approve_weekly_plan(plan["weekly_plan_id"])

    assert result["status"] == "approved"
    names = [s["name"] for s in seen["specs"]]
    assert len(names) == 7 and len(set(names)) == 7, "every dish written exactly once"
    # And the list was built from what the background pass wrote — which
    # means approval waited for it rather than approving around it.
    assert tools.pending_recipes_for_plan(plan["weekly_plan_id"]) == []
    items = {g["item"].lower() for g in tools.list_grocery_list("needed")}
    assert "chicken thighs" in items


def test_if_the_background_pass_fails_approve_writes_the_recipe(household, menu_model, recipe_model, background):
    week = _week_start()
    calls = {"n": 0}

    def _answer(spec):
        calls["n"] += 1
        # The background pass's two tries come back empty (an API hiccup).
        return {} if calls["n"] <= 2 else DETAILS
    seen = recipe_model(answer=_answer)
    menu_model(_menu_week(week))
    plan = agent.generate_weekly_plan(week)
    background[0].join(timeout=10)
    assert _recipe("Chettinad Pepper Chicken")["details_pending"] is True

    result = tools.approve_weekly_plan(plan["weekly_plan_id"])

    assert result["status"] == "approved"
    assert len(seen["specs"]) == 3
    assert _recipe("Chettinad Pepper Chicken")["details_pending"] is False
    items = {g["item"].lower() for g in tools.list_grocery_list("needed")}
    assert "chicken thighs" in items


def test_a_pass_that_never_ran_leaves_approve_to_write_them(household, menu_model, recipe_model, monkeypatch):
    """A redeploy kills the daemon thread: no claim survives, the pending
    flag does, and approval writes the recipe as it always has."""
    monkeypatch.setenv("DISABLE_BACKGROUND_RECIPES", "1")
    week = _week_start()
    seen = recipe_model()
    menu_model(_menu_week(week))
    plan = agent.generate_weekly_plan(week)
    assert seen["specs"] == []

    tools.approve_weekly_plan(plan["weekly_plan_id"])

    assert len(seen["specs"]) == 1
    assert _recipe("Chettinad Pepper Chicken")["details_pending"] is False


def test_a_dish_swapped_out_mid_pass_does_not_hold_up_approval(household, menu_model, recipe_model, background):
    week = _week_start()
    seen = recipe_model(delay=1.5)
    menu_model(_menu_week(week))
    plan = agent.generate_weekly_plan(week)
    plan_id = plan["weekly_plan_id"]
    assert background[0].is_alive()
    # Every Chettinad night swapped for Toast while it is being written.
    toast = _recipe("Toast")
    conn = get_conn()
    conn.execute(
        "UPDATE meal_plan_entries SET recipe_id = ? "
        "WHERE weekly_plan_id = ? AND recipe_id = (SELECT id FROM recipes WHERE name = 'Chettinad Pepper Chicken')",
        (toast["id"], plan_id),
    )
    conn.commit()
    conn.close()

    started = time.perf_counter()
    result = tools.approve_weekly_plan(plan_id)
    elapsed = time.perf_counter() - started

    assert result["status"] == "approved"
    assert elapsed < 1.0, f"approval waited {elapsed:.1f}s on a dish no longer in the week"
    assert len(seen["specs"]) == 1


def test_one_pass_per_draft_at_a_time(household, menu_model, recipe_model, background):
    week = _week_start()
    recipe_model(delay=0.4)
    menu_model(_menu_week(week))
    plan = agent.generate_weekly_plan(week)
    assert background[0].is_alive()

    assert agent.start_background_recipe_pass(plan["weekly_plan_id"]) is None
    assert len(background) == 1


def test_a_clash_in_the_background_is_left_for_approval_to_re_pick(
    household, menu_model, recipe_model, background, monkeypatch,
):
    """The draft doesn't change under the household while they read it: a
    dish written with a must-avoid in it stays pending, and approval —
    which re-picks, as before — handles it."""
    tools.set_member_dietary_restrictions("Ana", ["pineapple allergy"])
    swept = []

    def _fake_sweep(plan_id, budget=None, picker=None, known_clashes=None):
        swept.append(known_clashes)
        return {"sides_removed": 0, "dishes_repicked": 0, "slots_opened": 0}
    monkeypatch.setattr(allergen_gate, "sweep_plan", _fake_sweep)
    week = _week_start()
    bad = {**DETAILS, "ingredients": DETAILS["ingredients"] + [{"item": "Pineapple", "qty": "1", "category": "produce"}]}
    recipe_model(answer=bad)
    menu_model(_menu_week(week))
    plan = agent.generate_weekly_plan(week)
    background[0].join(timeout=10)

    # (The draft's own allergen gate sweeps with no known clashes; the
    # recipe pass's re-pick is the one that names them.)
    assert [k for k in swept if k] == [], "the background pass re-picked a dish on the draft"
    assert _recipe("Chettinad Pepper Chicken")["details_pending"] is True

    tools.approve_weekly_plan(plan["weekly_plan_id"])

    named = [k for k in swept if k]
    assert len(named) == 1 and list(named[0]) == ["chettinad pepper chicken"]


def test_the_background_pass_writes_into_the_household_that_drafted(monkeypatch, recipe_model, background):
    """The thread carries the household (a contextvar). A bare thread
    would see household 1 and find nothing to write — or write into the
    wrong kitchen."""
    from app import households

    beta = households.create_household("The Beta Testers", "beta-passphrase-for-the-test")
    week = _week_start()
    with tools.use_household(beta):
        tools.add_member("Julia")
        tools.add_recipe("Toast", ingredients=[{"item": "bread", "qty": "1 loaf"}])
        tools.edit_preference("complete_plates", False)
        days = [
            {**d, "meal_name": "Paneer Tikka Wrap", "is_new_recipe": True, "dish_note": "sear the paneer"}
            if d["slot"] == "lunch" else d
            for d in _menu_week(week)
        ]
        seen = recipe_model()
        monkeypatch.setattr(agent, "generate_weekly_plan_llm", lambda context: days)
        agent.generate_weekly_plan(week)
    background[0].join(timeout=10)

    assert len(seen["specs"]) == 2
    assert all(s["serves"] == 1 for s in seen["specs"]), "written for Julia's table, not household 1's"
    with tools.use_household(beta):
        for name in ("Chettinad Pepper Chicken", "Paneer Tikka Wrap"):
            assert _recipe(name)["details_pending"] is False, name
    with tools.use_household(1):
        names = {r["name"] for r in tools.list_recipes()}
        assert not names & {"Chettinad Pepper Chicken", "Paneer Tikka Wrap"}


def test_two_approvals_and_the_background_pass_write_each_recipe_once(
    household, menu_model, recipe_model, background,
):
    week = _week_start()
    seen = recipe_model(delay=0.3)
    menu_model(_menu_week(week))
    plan = agent.generate_weekly_plan(week)
    errors = []

    def approve():
        try:
            tools.approve_weekly_plan(plan["weekly_plan_id"])
        except Exception as e:  # pragma: no cover - reported below
            errors.append(e)

    a, b = threading.Thread(target=approve), threading.Thread(target=approve)
    a.start(); b.start()
    a.join(); b.join()

    assert not errors
    assert len(seen["specs"]) == 1
