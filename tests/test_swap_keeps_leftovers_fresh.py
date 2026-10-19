"""
A nights swap never makes leftovers older than the food-safety days
(leftovers.MAX_LEFTOVER_DAYS, Emily 2026-09-23).

The Move sheet (meal_move) has refused "The Stew's leftovers would be 4
days old" since 2026-09-28, but the nights swap — Plan's drag, the chat
tool, Now's "Something else" — shares only the writer
(weekly_plan._redate_plan_rows), which checks a chain's ORDER and never
its AGE. Driven on main (overnight hunt, 2026-10-19): Monday's stew with
its leftovers Tuesday, POST /swap-nights {Tue, Fri} answered "swapped" and
Friday's Today read "A Stew · leftovers from Monday" — day four, a chain
the generator and repair_leftover_chains would never have written.
"""
from __future__ import annotations

import datetime
import json

from app import tools
from app.db import get_conn


def _monday() -> datetime.date:
    from conftest import household_today
    today = household_today()
    return today - datetime.timedelta(days=today.weekday())


# Next week, so no night is already gone.
WEEK = (_monday() + datetime.timedelta(days=7)).isoformat()
MON, TUE, WED, THU, FRI, SAT, SUN = tools._week_dates(WEEK)


def _recipe(name):
    tools.add_recipe(name, ingredients=[{"item": "Onion", "qty": "1", "category": "produce"}],
                     prep_time_minutes=10, cook_time_minutes=20, default_servings=4)


def _dinner(day: str) -> dict:
    conn = get_conn()
    row = conn.execute(
        "SELECT mpe.id, mpe.derived_from_json, COALESCE(r.name, mpe.freeform_meal) AS meal "
        "FROM meal_plan_entries mpe LEFT JOIN recipes r ON r.id = mpe.recipe_id "
        "WHERE mpe.household_id = ? AND mpe.date = ? AND mpe.slot = 'dinner'",
        (tools.household_id(), day),
    ).fetchone()
    conn.close()
    out = dict(row)
    out["derived"] = json.loads(out.pop("derived_from_json") or "{}")
    return out


def _week(cook_day: str, reheat_day: str, others: dict[str, str], repair: bool = True) -> int:
    _recipe("Chicken Stew")
    for name in set(others.values()):
        _recipe(name)
    plan = tools.create_weekly_plan(WEEK)["weekly_plan_id"]
    tools.plan_meal(cook_day, "Chicken Stew", slot="dinner", weekly_plan_id=plan)
    tools.plan_meal(reheat_day, "Chicken Stew", slot="dinner", weekly_plan_id=plan,
                    derived_from={"links_to": f"{cook_day}:dinner"})
    for day, name in others.items():
        tools.plan_meal(day, name, slot="dinner", weekly_plan_id=plan)
    if repair:
        tools.repair_leftover_chains(plan)
        assert tools.plan_leftover_chains(plan)["leftovers"], "the chain should be confirmed first"
    return plan


def test_dragging_the_leftovers_night_four_days_from_its_cook_is_refused():
    plan = _week(MON, TUE, {FRI: "Salmon Bowls"})
    before = (_dinner(TUE), _dinner(FRI))

    out = tools.swap_dinner_nights(plan, TUE, FRI)

    assert out["status"] == "refused"
    assert out["message"] == (
        "Chicken Stew’s leftovers would be 4 days old by Friday — I’ve left both nights as they are."
    )
    assert (_dinner(TUE), _dinner(FRI)) == before


def test_moving_the_cook_earlier_cannot_stretch_the_gap_either():
    """Thursday's stew feeds Friday; trading the cook onto Monday would
    make Friday's leftovers four days old."""
    plan = _week(THU, FRI, {MON: "Salmon Bowls"})

    out = tools.swap_dinner_nights(plan, MON, THU)

    assert out["status"] == "refused"
    assert _dinner(THU)["meal"] == "Chicken Stew" and _dinner(MON)["meal"] == "Salmon Bowls"


def test_a_move_inside_the_three_days_still_swaps_and_undoes():
    plan = _week(MON, TUE, {THU: "Salmon Bowls"})

    out = tools.swap_dinner_nights(plan, TUE, THU)

    assert out["status"] == "swapped"
    assert _dinner(THU)["derived"]["links_to"] == f"{MON}:dinner"
    assert tools.undo_dinner_nights_swap(plan, TUE, THU)["status"] == "restored"
    assert _dinner(TUE)["derived"]["links_to"] == f"{MON}:dinner"


def test_a_chain_already_too_long_can_still_be_shortened():
    """A week that already holds a five-day chain is not this move's to
    judge: bringing the reheat a night closer goes through, even though
    four days is still past the limit — it is better than it was."""
    plan = _week(MON, SAT, {FRI: "Salmon Bowls"}, repair=False)

    out = tools.swap_dinner_nights(plan, FRI, SAT)

    assert out["status"] == "swapped"
    assert _dinner(FRI)["derived"]["links_to"] == f"{MON}:dinner"


def test_the_route_answers_the_refusal_as_a_200(signed_in):
    _week(MON, TUE, {FRI: "Salmon Bowls"})

    resp = signed_in.post(f"/api/week/{WEEK}/swap-nights", json={"date_a": TUE, "date_b": FRI})

    assert resp.status_code == 200
    assert resp.json()["status"] == "refused"
    assert "4 days old" in resp.json()["message"]
