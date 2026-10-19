"""
Taking a night off a batched dish — the Review stepper's "−", its Undo, or a
Swap — keeps the household's freezer answer.

Found by hand on a throwaway DB (main, 2026-10-12): a week with a prep day
batches Wednesday's Salmon Bowls for Friday at approval; the freezer step
is answered "Salmon fillets: in the freezer" (one move, for Wednesday, the
salmon line set aside). Then "−" on either night. The rescale that brings
the batch down re-ingests the recipe-week, and a set-aside line is not one
the ingest merges into, so it wrote a fresh one: "Salmon fillets 8 oz" on
the shopping list, under a fridge move telling them to thaw the same fish.
The toast's Undo put a whole "1 lb" back. unbatch already ran
defrost.restate_frozen_after_batch_change after its rescale for exactly
this; the "−" and its Undo did not.

And the set-aside line the rescale replaces is retired with it, or
un-tapping the chip afterwards ("actually, it's not frozen") put back every
generation of it at once — measured, 2.5 lb of salmon for a 1 lb week.
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


def _week(frozen=("Salmon fillets",)):
    """Salmon on Wednesday and Friday, batched at approval (a prep day),
    freezer step answered."""
    tools.add_member("Emily")
    tools.add_member("Sam")
    tools.set_prep_days([{"weekday": "sunday"}])
    tools.add_recipe(
        "Salmon Bowls",
        ingredients=[
            {"item": "Salmon fillets", "qty": "1 lb", "category": "seafood"},
            {"item": "Frozen peas", "qty": "2 cups", "category": "pantry"},
        ],
        instructions=["Cook the rice.", "Bake the salmon."],
    )
    plan_id = tools.create_weekly_plan(MON.isoformat())["weekly_plan_id"]
    wed = tools.plan_meal(_day(2), "Salmon Bowls", slot="dinner", weekly_plan_id=plan_id)["entry_id"]
    fri = tools.plan_meal(_day(4), "Salmon Bowls", slot="dinner", weekly_plan_id=plan_id)["entry_id"]
    tools.approve_weekly_plan(plan_id, approved_by="Emily", confirm_hard_conflicts=True)
    assert fri in tools.plan_leftover_chains(plan_id)["leftovers"], "premise: batched at approval"
    tools.confirm_frozen_items(plan_id, list(frozen))
    return plan_id, wed, fri


def _to_buy(item="Salmon fillets"):
    conn = get_conn()
    rows = conn.execute(
        "SELECT quantity FROM grocery_items WHERE item = ? AND status IN ('needed', 'in_cart') "
        "AND excluded_from_list = 0", (item,),
    ).fetchall()
    conn.close()
    return [r["quantity"] for r in rows]


def _move_dates(plan_id):
    conn = get_conn()
    rows = conn.execute(
        "SELECT task_date FROM prep_tasks WHERE weekly_plan_id = ? AND task_type = 'defrost' "
        "AND status = 'pending' ORDER BY task_date", (plan_id,),
    ).fetchall()
    conn.close()
    return [r["task_date"] for r in rows]


def test_stepping_the_reheat_night_down_buys_no_salmon(client):
    """CATCH: on main, "Salmon fillets 8 oz" went on the list."""
    plan_id, wed, fri = _week()
    assert _to_buy() == []

    out = tools.drop_dish_from_day(plan_id, fri)

    assert out["status"] == "dropped"
    assert _to_buy() == [], "the household said the salmon is in the freezer"
    assert _move_dates(plan_id) == [_day(1)], "Wednesday's move stands"
    assert _to_buy("Frozen peas") != [], "the rest of the list is untouched"


def test_stepping_the_cook_night_down_buys_no_salmon_and_moves_the_thaw(signed_in):
    """CATCH, over the Review screen's own route: the cook moves onto
    Friday, and on main the salmon came back on the list."""
    plan_id, wed, fri = _week()

    res = signed_in.post(f"/api/week/{MON.isoformat()}/drop-dish-day", json={"entry_id": wed})

    assert res.status_code == 200, res.text
    assert res.json()["moved_to"] == _day(4)
    assert _to_buy() == []
    assert _move_dates(plan_id) == [_day(3)], "the thaw follows the cook to Friday"


def test_the_undo_keeps_it_off_too(client):
    """CATCH: on main the Undo put a whole 1 lb back on the list."""
    plan_id, wed, fri = _week()
    out = tools.drop_dish_from_day(plan_id, wed)

    back = tools.drop_dish_undo(plan_id, out["undo_entry_id"])

    assert back["status"] == "restored"
    assert _to_buy() == []
    assert _move_dates(plan_id) == [_day(1)]


def test_changing_the_answer_afterwards_puts_back_one_line_of_the_right_size(client):
    """CATCH for the retired line: every rescale's set-aside used to stay
    behind, and un-tapping the chip put all of them back."""
    plan_id, wed, fri = _week()
    out = tools.drop_dish_from_day(plan_id, wed)
    tools.drop_dish_undo(plan_id, out["undo_entry_id"])

    tools.confirm_frozen_items(plan_id, [])

    assert _to_buy() == ["1 lb"], "the week's salmon, once"
    assert _move_dates(plan_id) == []


def test_swapping_the_reheat_night_buys_no_salmon(client):
    """CATCH: the Swap door has the same rescale (_replace_slot_entries,
    inside its transaction) and on main put the same 8 oz back."""
    plan_id, wed, fri = _week()
    tools.add_recipe("Bean Chili", ingredients=[{"item": "Black beans", "qty": "2 cans", "category": "pantry"}],
                     instructions=["Simmer."])

    tools.swap_meal_in_plan(plan_id, _day(4), "Bean Chili", slot="dinner")

    assert _to_buy() == []
    assert _to_buy("Black beans") != [], "the new dish is bought"
    assert _move_dates(plan_id) == [_day(1)]


def test_swapping_the_reheat_night_to_another_salmon_dish_still_buys_that_salmon(client):
    """Review catch (2026-10-12): the swapped-in dish's own salmon merges
    into the batch's fresh line, and nothing books Friday a fridge move —
    so setting that line aside meant salmon neither bought nor thawed. A
    line carrying a meal outside the batch stays needed."""
    plan_id, wed, fri = _week()
    tools.add_recipe("Salmon Tacos", ingredients=[{"item": "Salmon fillets", "qty": "1 lb", "category": "seafood"}],
                     instructions=["Grill.", "Wrap."])

    tools.swap_meal_in_plan(plan_id, _day(4), "Salmon Tacos", slot="dinner")

    assert _to_buy() != [], "Friday's tacos need salmon the household never said it had a move for"


def test_with_no_freezer_answer_the_step_down_still_trims_the_line(client):
    """NO-REGRESSION: nobody said the salmon was frozen, so the "−" is the
    ordinary trim it always was — the line comes down and stays needed."""
    plan_id, wed, fri = _week(frozen=())
    before = _to_buy()
    assert before, "premise: salmon to buy"

    tools.drop_dish_from_day(plan_id, fri)

    after = _to_buy()
    assert len(after) == 1 and after != before
