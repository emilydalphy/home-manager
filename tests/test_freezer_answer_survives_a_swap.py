"""
Swapping a night away and back keeps the household's freezer answer.

Found by hand (main, 2026-10-13): Salmon Bowls on Tuesday and Friday, the
week approved, the freezer step answered "Salmon fillets: in the freezer"
(both moves booked, the salmon line set aside). Swap Friday to Bean Chili,
then back to Salmon Bowls: the swap-in's ingest cannot merge into a
set-aside line, so it wrote a fresh "Salmon fillets 8 oz" to buy under a
chip still reading "frozen". Every round trip left another set-aside
generation behind, so un-ticking the chip later put them all back at once.
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


def _week():
    """Salmon Tuesday and Friday (no prep day, so no batch), approved,
    freezer step answered."""
    tools.add_member("Emily")
    tools.add_member("Sam")
    tools.add_recipe(
        "Salmon Bowls",
        ingredients=[
            {"item": "Salmon fillets", "qty": "8 oz", "category": "seafood"},
            {"item": "Rice", "qty": "2 cups", "category": "pantry"},
        ],
        instructions=["Cook the rice.", "Bake the salmon."],
    )
    tools.add_recipe("Bean Chili", ingredients=[{"item": "Black beans", "qty": "2 cans", "category": "pantry"}],
                     instructions=["Simmer."])
    plan_id = tools.create_weekly_plan(MON.isoformat())["weekly_plan_id"]
    tue = tools.plan_meal(_day(1), "Salmon Bowls", slot="dinner", weekly_plan_id=plan_id)["entry_id"]
    fri = tools.plan_meal(_day(4), "Salmon Bowls", slot="dinner", weekly_plan_id=plan_id)["entry_id"]
    tools.approve_weekly_plan(plan_id, approved_by="Emily", confirm_hard_conflicts=True)
    assert not tools.plan_leftover_chains(plan_id)["leftovers"], "premise: two cook nights"
    tools.confirm_frozen_items(plan_id, ["Salmon fillets"])
    return plan_id, tue, fri


def _lines(item="Salmon fillets", where="status IN ('needed', 'in_cart') AND excluded_from_list = 0"):
    conn = get_conn()
    rows = conn.execute(
        f"SELECT quantity FROM grocery_items WHERE item = ? AND {where} ORDER BY id", (item,),
    ).fetchall()
    conn.close()
    return [r["quantity"] for r in rows]


def _set_aside(item="Salmon fillets"):
    return _lines(item, "status = 'removed' AND removed_by = 'freezer'")


def _moves(plan_id):
    conn = get_conn()
    rows = conn.execute(
        "SELECT related_meal, task_date FROM prep_tasks WHERE weekly_plan_id = ? AND task_type = 'defrost' "
        "AND status = 'pending' ORDER BY task_date", (plan_id,),
    ).fetchall()
    conn.close()
    return [(r["related_meal"], r["task_date"]) for r in rows]


def _round_trip(plan_id):
    tools.swap_meal_in_plan(plan_id, _day(4), "Bean Chili", slot="dinner")
    tools.swap_meal_in_plan(plan_id, _day(4), "Salmon Bowls", slot="dinner")


def test_swap_away_and_back_buys_no_salmon(client):
    """CATCH: on main the swap back put "Salmon fillets 8 oz" on the list."""
    plan_id, tue, fri = _week()
    assert _lines() == []
    before = _moves(plan_id)
    assert len(before) == 2, "premise: a move for each cook night"
    total_before = _set_aside()

    _round_trip(plan_id)

    assert _lines() == [], "the household said the salmon is in the freezer"
    assert _set_aside() == total_before, "Friday's share joins the set-aside amount, one line"
    assert _moves(plan_id) == before, "Friday's move is booked again, as if it never left"
    item = next(i for i in tools.meat_items_for_plan(plan_id) if i["item"] == "Salmon fillets")
    assert item["frozen"] is True


def test_untapping_after_round_trips_puts_the_plans_amount_back_once(client):
    """CATCH: each round trip left a set-aside line behind, and "not frozen
    after all" put every one of them back together."""
    plan_id, tue, fri = _week()
    week = _set_aside()
    assert week == ["8 oz"], "premise: two nights' share, one line"
    _round_trip(plan_id)
    _round_trip(plan_id)

    tools.confirm_frozen_items(plan_id, [])

    assert _lines() == week, "the week's salmon, once"
    assert _moves(plan_id) == []


def test_swapping_in_a_night_the_answer_never_covered_still_buys_it(client):
    """Edge: no freezer answer at all -- the swap-in buys as it always did."""
    tools.add_member("Emily")
    tools.add_recipe("Salmon Bowls", ingredients=[{"item": "Salmon fillets", "qty": "8 oz", "category": "seafood"}],
                     instructions=["Bake."])
    tools.add_recipe("Bean Chili", ingredients=[{"item": "Black beans", "qty": "2 cans", "category": "pantry"}],
                     instructions=["Simmer."])
    plan_id = tools.create_weekly_plan(MON.isoformat())["weekly_plan_id"]
    tools.plan_meal(_day(1), "Salmon Bowls", slot="dinner", weekly_plan_id=plan_id)
    tools.plan_meal(_day(4), "Bean Chili", slot="dinner", weekly_plan_id=plan_id)
    tools.approve_weekly_plan(plan_id, approved_by="Emily", confirm_hard_conflicts=True)
    tools.confirm_frozen_items(plan_id, [])

    tools.swap_meal_in_plan(plan_id, _day(4), "Salmon Bowls", slot="dinner")

    assert _lines() != [] and _set_aside() == []
    assert _moves(plan_id) == []


def test_a_hand_added_amount_stays_to_buy(client):
    """The household's own salmon, added by hand after the answer, is never
    taken off by a swap carrying the answer across."""
    plan_id, tue, fri = _week()
    tools.add_grocery_item("Salmon fillets", quantity="2 lb", category="seafood")
    assert _lines() == ["2 lbs"]

    _round_trip(plan_id)

    assert _lines() == ["2 lbs"], "their 2 lb, and nothing of Friday's on top"
    assert _set_aside() == ["8 oz"]
