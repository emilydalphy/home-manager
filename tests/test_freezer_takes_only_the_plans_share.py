"""
"It's in the freezer" takes off only the plan's own amount (Loop Board,
decided 2026-10-12).

A grocery line can hold the plan's salmon AND the household's own. Setting
the whole line aside took their own two pounds off the list with the
plan's one (a hand add before approval); and after a hand add following
approval, "−" on a batched salmon night then Undo put the frozen pound back
("3 lbs"). Now the plan's recorded share is split onto a set-aside line of
its own: their amount stays to buy, and "not frozen after all" puts back
exactly the plan's amount, once.
"""
from __future__ import annotations

import datetime

import pytest

from app import tools
from app.db import get_conn

pytestmark = pytest.mark.today("2026-10-12 10:00")
MON = datetime.date(2026, 10, 12)


def _day(n: int) -> str:
    return (MON + datetime.timedelta(days=n)).isoformat()


def _salmon_week(own_before: str | None = None):
    """Salmon Bowls Wednesday and Friday, batched at approval (a prep day):
    the plan's share is 1 lb."""
    tools.add_member("Emily")
    tools.add_member("Sam")
    tools.set_prep_days([{"weekday": "sunday"}])
    tools.add_recipe(
        "Salmon Bowls",
        ingredients=[
            {"item": "Salmon fillets", "qty": "1 lb", "category": "seafood"},
            {"item": "Rice", "qty": "2 cups", "category": "pantry"},
        ],
        instructions=["Cook the rice.", "Bake the salmon."],
    )
    if own_before:
        tools.add_grocery_item("Salmon fillets", quantity=own_before, category="seafood", added_by="Emily")
    plan_id = tools.create_weekly_plan(MON.isoformat())["weekly_plan_id"]
    wed = tools.plan_meal(_day(2), "Salmon Bowls", slot="dinner", weekly_plan_id=plan_id)["entry_id"]
    fri = tools.plan_meal(_day(4), "Salmon Bowls", slot="dinner", weekly_plan_id=plan_id)["entry_id"]
    tools.approve_weekly_plan(plan_id, approved_by="Emily", confirm_hard_conflicts=True)
    return plan_id, wed, fri


def _to_buy(item="Salmon fillets"):
    conn = get_conn()
    rows = conn.execute(
        "SELECT quantity FROM grocery_items WHERE item = ? AND status IN ('needed', 'in_cart') "
        "AND excluded_from_list = 0 ORDER BY id", (item,),
    ).fetchall()
    conn.close()
    return [r["quantity"] for r in rows]


def test_the_households_own_amount_stays_when_the_plans_is_frozen(client):
    """CATCH 1: own 2 lbs added before approving, the plan's 1 lb joins it
    (3 lbs); "salmon's in the freezer" set aside all 3."""
    plan_id, _wed, _fri = _salmon_week(own_before="2 lbs")
    assert _to_buy() == ["3 lbs"], "premise: one line, theirs plus the plan's"

    tools.confirm_frozen_items(plan_id, ["Salmon fillets"])

    assert _to_buy() == ["2 lbs"], "their own two pounds are still to buy"


def test_not_frozen_after_all_puts_back_exactly_the_plans_amount_once(client):
    plan_id, _wed, _fri = _salmon_week(own_before="2 lbs")
    tools.confirm_frozen_items(plan_id, ["Salmon fillets"])

    tools.confirm_frozen_items(plan_id, [])

    assert sorted(_to_buy()) == ["1 lb", "2 lbs"]


def test_a_step_down_and_its_undo_never_bring_the_frozen_pound_back(client):
    """CATCH 2: own 2 lbs added AFTER approving (the line becomes theirs),
    salmon said frozen, "−" on a batched night then Undo showed 3 lbs."""
    plan_id, wed, _fri = _salmon_week()
    tools.add_grocery_item("Salmon fillets", quantity="2 lbs", category="seafood", added_by="Emily")
    assert _to_buy() == ["3 lbs"], "premise: the hand add joined the plan's line"
    tools.confirm_frozen_items(plan_id, ["Salmon fillets"])
    assert _to_buy() == ["2 lbs"]

    out = tools.drop_dish_from_day(plan_id, wed)
    assert _to_buy() == ["2 lbs"]
    tools.drop_dish_undo(plan_id, out["undo_entry_id"])

    assert _to_buy() == ["2 lbs"], "the pound they said is frozen stays off"


def test_a_plan_only_line_is_set_aside_whole_as_before(client):
    plan_id, _wed, _fri = _salmon_week()
    tools.confirm_frozen_items(plan_id, ["Salmon fillets"])
    assert _to_buy() == []
    tools.confirm_frozen_items(plan_id, [])
    assert _to_buy() == ["1 lb"]


def test_a_swap_of_the_reheat_night_leaves_their_amount_and_buys_no_frozen_salmon(client):
    plan_id, _wed, _fri = _salmon_week(own_before="2 lbs")
    tools.confirm_frozen_items(plan_id, ["Salmon fillets"])
    tools.add_recipe("Bean Chili", ingredients=[{"item": "Black beans", "qty": "2 cans", "category": "pantry"}],
                     instructions=["Simmer."])

    tools.swap_meal_in_plan(plan_id, _day(4), "Bean Chili", slot="dinner")

    assert _to_buy() == ["2 lbs"]
