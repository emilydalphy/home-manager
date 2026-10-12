"""
Cooking ahead takes the reheat night's fridge move with it — and letting
go of a batch books one back. defrost.restate_frozen_after_batch_change,
run from cook_ahead.set_cook_ahead itself.

Found by hand on a throwaway DB: Salmon Bowls on two nights, approved,
the freezer step answered "Salmon fillets: in the freezer" (a move booked
for each cook night), then the later night batched onto the first.
The day before it, Today still said "Move the Salmon fillets to the
fridge — for Saturday's Salmon Bowls" above a "Salmon Bowls · reheat"
row: fish the household had cooked days earlier, or none left in the
freezer to move. Letting the batch go again left the opposite — a cook
night with no move. Only batch_undo's rebatch ran the restate; every
other writer of a batch goes through set_cook_ahead and never did.

The door a household has on screen today is re-approval: approve with no
prep day, answer the freezer step, set a prep day, "Change the week",
approve again — approval's own batching (apply_prep_day_batches) writes
the chain. The /api/cooker/cook-ahead route is driven too; no screen calls
it since the Cook card's picker came off on 2026-09-18, but it is live.

Every batch here is within three days (leftovers.MAX_LEFTOVER_DAYS), so
nothing in this file leans on a chain the app's food-safety rule refuses.
"""
from __future__ import annotations

import datetime

import pytest

from app import tools
from app.db import get_conn
from app.tools import moves as _moves_mod

# A Monday, mid-morning: every move this week is still ahead.
pytestmark = pytest.mark.today("2026-10-12 10:00")
MON = datetime.date(2026, 10, 12)


def _day(n: int) -> str:
    return (MON + datetime.timedelta(days=n)).isoformat()


def _week(frozen=("Salmon fillets",)):
    """Salmon on Wednesday and Saturday, approved with no prep days (so
    nothing is batched at approval), freezer step answered."""
    tools.add_member("Emily")
    tools.add_member("Sam")
    tools.add_recipe(
        "Salmon Bowls",
        ingredients=[
            {"item": "Salmon fillets", "qty": "1 lb", "category": "seafood"},
            {"item": "Rice", "qty": "2 cups", "category": "pantry"},
        ],
        instructions=["Cook the rice.", "Bake the salmon."],
        default_servings=4,
    )
    plan_id = tools.create_weekly_plan(MON.isoformat())["weekly_plan_id"]
    wed = tools.plan_meal(_day(2), "Salmon Bowls", slot="dinner", weekly_plan_id=plan_id)["entry_id"]
    sat = tools.plan_meal(_day(5), "Salmon Bowls", slot="dinner", weekly_plan_id=plan_id)["entry_id"]
    tools.approve_weekly_plan(plan_id, approved_by="Emily", confirm_hard_conflicts=True)
    assert not tools.plan_leftover_chains(plan_id)["leftovers"], "premise: nothing batched yet"
    tools.confirm_frozen_items(plan_id, list(frozen))
    return plan_id, wed, sat


def _moves(plan_id):
    conn = get_conn()
    rows = conn.execute(
        "SELECT meal_plan_entry_id, task_date, status FROM prep_tasks "
        "WHERE weekly_plan_id = ? AND task_type = 'defrost' ORDER BY meal_plan_entry_id",
        (plan_id,),
    ).fetchall()
    conn.close()
    return {r["meal_plan_entry_id"]: (r["task_date"], r["status"]) for r in rows}


def _fridge_titles_on(day: str) -> list[str]:
    out = _moves_mod.today_moves(day=day, now=datetime.datetime.fromisoformat(day + "T09:00"))
    return [m["title"] + " — " + m["reason"] for m in out["moves"] if m["kind"] == "fridge"]


def test_cooking_ahead_drops_the_reheat_nights_fridge_move(client):
    """CATCH: red on main — Saturday's pending move survived the batch."""
    plan_id, wed, sat = _week()
    assert set(_moves(plan_id)) == {wed, sat}, "premise: one move per cook night"

    result = tools.set_cook_ahead(wed, [sat])
    assert result["covered_entry_ids"] == [sat]

    assert set(_moves(plan_id)) == {wed}, "Saturday reheats Wednesday's batch; nothing to thaw for it"
    assert _fridge_titles_on(_day(4)) == [], "Friday's Today says nothing about Saturday's salmon"


def test_re_approving_with_prep_days_drops_it_too(client):
    """CATCH, through doors a household has on screen today: the week is
    approved with no prep day and the freezer step answered (a move per
    cook night), then a prep day is set ("we prep on Sundays now"), the
    week is changed and approved again — and approval's own batching
    (apply_prep_day_batches) makes Thursday a reheat of Tuesday. On main
    Thursday's move stayed."""
    tools.add_member("Emily")
    tools.add_member("Sam")
    tools.add_recipe(
        "Salmon Bowls",
        ingredients=[{"item": "Salmon fillets", "qty": "1 lb", "category": "seafood"}],
        instructions=["Bake the salmon."],
    )
    plan_id = tools.create_weekly_plan(MON.isoformat())["weekly_plan_id"]
    tue = tools.plan_meal(_day(1), "Salmon Bowls", slot="dinner", weekly_plan_id=plan_id)["entry_id"]
    thu = tools.plan_meal(_day(3), "Salmon Bowls", slot="dinner", weekly_plan_id=plan_id)["entry_id"]
    tools.approve_weekly_plan(plan_id, approved_by="Emily", confirm_hard_conflicts=True)
    tools.confirm_frozen_items(plan_id, ["Salmon fillets"])
    assert set(_moves(plan_id)) == {tue, thu}, "premise: one move per cook night"

    tools.set_prep_days([{"weekday": "sunday"}])
    tools.reopen_weekly_plan(plan_id)
    tools.approve_weekly_plan(plan_id, approved_by="Emily", confirm_hard_conflicts=True)
    assert thu in tools.plan_leftover_chains(plan_id)["leftovers"], "premise: re-approval batched Thursday"

    assert set(_moves(plan_id)) == {tue}


def test_the_cook_card_route_drops_it_too(signed_in):
    """The door the bug was found through — over HTTP, the Cook card's own write."""
    plan_id, wed, sat = _week()

    res = signed_in.post("/api/cooker/cook-ahead", json={"source_entry_id": wed, "covered_entry_ids": [sat]})
    assert res.status_code == 200, res.text

    assert set(_moves(plan_id)) == {wed}


def test_letting_go_of_the_batch_books_the_freed_night_again(client):
    """CATCH: red on main — releasing Saturday left a cook night with no move.
    The batch is made BEFORE the freezer answer here (the approval card's
    order), so the step books one move, for Wednesday, and Saturday has
    never had one of its own to fall back on."""
    plan_id, wed, sat = _week(frozen=())
    tools.set_cook_ahead(wed, [sat])
    tools.confirm_frozen_items(plan_id, ["Salmon fillets"])
    assert set(_moves(plan_id)) == {wed}, "premise: the step books cook nights only"

    tools.set_cook_ahead(wed, [])

    assert _moves(plan_id) == {wed: (_day(1), "pending"), sat: (_day(4), "pending")}
    assert _fridge_titles_on(_day(4)) == [
        "Move the Salmon fillets to the fridge — for Saturday's Salmon Bowls"
    ]


def test_a_move_already_done_is_food_in_the_fridge_and_stays(client):
    """A ticked move for the night now covered is not undone by the batch."""
    plan_id, wed, sat = _week()
    conn = get_conn()
    task_id = conn.execute(
        "SELECT id FROM prep_tasks WHERE meal_plan_entry_id = ? AND task_type = 'defrost'", (sat,)
    ).fetchone()["id"]
    conn.close()
    tools.check_off_prep_step(task_id, "done")

    tools.set_cook_ahead(wed, [sat])

    assert _moves(plan_id)[sat][1] == "done"


def test_no_freezer_answer_books_nothing(client):
    """Nobody said anything was frozen: a batch must not invent a fridge move."""
    plan_id, wed, sat = _week(frozen=())
    assert _moves(plan_id) == {}

    tools.set_cook_ahead(wed, [sat])
    tools.set_cook_ahead(wed, [])

    assert _moves(plan_id) == {}
