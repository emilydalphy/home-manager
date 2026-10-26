"""
Shop's "Freezing it?" is never about a night that reheats a batch.

Defect hunt 2026-10-26, on a throwaway DB over HTTP: Turkey Chili on
Monday, Wednesday and Friday, approved; on Monday the Cook card's
cook-ahead made Monday's pot cover Wednesday and Friday. The ground turkey
line on Shop still offered "Freezing it? … for Wednesday" — the ledger
keeps each night's share (cook_ahead deliberately leaves groceries alone),
and defrost._grocery_line_meals read Wednesday as a cook night. A yes
booked "Move the ground turkey to the fridge — for Wednesday's Turkey
Chili" (3 lbs) on Today, for meat that goes in the pot tonight.

A reheat night's share is cooked on its source night, so it is not a night
the pack can be thawed for: the offer skips it, and so does the "keep
Wednesday's in the fridge" label.
"""
from __future__ import annotations

import datetime

from app import tools
from app.db import get_conn
from app.tools._shared import household_id
from conftest import household_today

TODAY = household_today()


def _day(n: int) -> str:
    return (TODAY + datetime.timedelta(days=n)).isoformat()


def _chili_plan(days):
    tools.add_member("Alex")
    tools.add_member("Sam")
    tools.add_recipe("Turkey Chili", ingredients=[{"item": "Ground turkey", "qty": "1 lb", "category": "meat/seafood"}],
                     default_servings=2, prep_time_minutes=10, cook_time_minutes=30)
    plan_id = tools.create_weekly_plan(TODAY.isoformat())["weekly_plan_id"]
    ids = {}
    for n in days:
        ids[n] = tools.plan_meal(_day(n), "Turkey Chili", slot="dinner", weekly_plan_id=plan_id,
                                 add_ingredients_to_grocery_list=True)["entry_id"]
    return plan_id, ids


def _turkey(client):
    view = client.get("/api/grocery-list").json()
    for s in view["sections"]:
        for it in s["items"]:
            if it["item"].lower() == "ground turkey":
                return it
    raise AssertionError("ground turkey is not on the list")


def _defrost_rows():
    conn = get_conn()
    rows = conn.execute(
        "SELECT meal_plan_entry_id FROM prep_tasks WHERE household_id = ? AND task_type = 'defrost'",
        (household_id(),),
    ).fetchall()
    conn.close()
    return [r["meal_plan_entry_id"] for r in rows]


def test_a_batch_cooked_tonight_is_not_offered_for_the_night_it_feeds(signed_in):
    plan_id, ids = _chili_plan((0, 2))
    line = _turkey(signed_in)
    assert line["freezing"]["entry_id"] == ids[2], "unbatched, the later cook night is asked about"

    assert not isinstance(tools.set_cook_ahead(ids[0], [ids[2]]), str)

    line = _turkey(signed_in)
    assert "freezing" not in line, "every pound goes in tonight's pot — there is nothing to thaw for"
    res = signed_in.post(f"/api/grocery-list/{line['id']}/freezing", json={"answer": "freezer"})
    assert res.status_code == 400
    assert _defrost_rows() == []


def test_the_offer_moves_past_the_reheat_to_the_next_real_cook(signed_in):
    plan_id, ids = _chili_plan((0, 2, 5))
    assert not isinstance(tools.set_cook_ahead(ids[0], [ids[2]]), str)

    offer = _turkey(signed_in)["freezing"]
    assert offer["entry_id"] == ids[5]
    assert offer["cook_date"] == _day(5)
    # Tonight's batch is the share that stays fresh; Wednesday's reheat is
    # not a night of its own to keep a share back for.
    assert offer["keep_fresh_label"] == "today’s"


def test_a_meat_side_cooked_fresh_on_the_reheat_night_is_still_asked_about(signed_in):
    """Review 2026-10-26: the first version dropped the reheat from EVERY
    line. A side cooked on the reheat night is bought under the reheat's own
    id (reheat_buys_it) and has no share on the source, so its thaw is
    still the household's to book — or it gets one nowhere."""
    from app.tools import plates as _plates

    tools.add_member("Alex")
    tools.add_member("Sam")
    tools.add_recipe("Veggie Lasagna", ingredients=[{"item": "Lasagna noodles", "qty": "1 box", "category": "pantry"}],
                     default_servings=2, prep_time_minutes=20, cook_time_minutes=45)
    plan_id = tools.create_weekly_plan(TODAY.isoformat())["weekly_plan_id"]
    cook = tools.plan_meal(_day(0), "Veggie Lasagna", slot="dinner", weekly_plan_id=plan_id)["entry_id"]
    reheat = tools.plan_meal(_day(4), "Veggie Lasagna", slot="dinner", weekly_plan_id=plan_id)["entry_id"]
    tools.approve_weekly_plan(plan_id, approved_by="Alex")
    assert not isinstance(tools.set_cook_ahead(cook, [reheat]), str)
    _plates.carry_sides([{
        "name": "Grilled chicken thighs",
        "ingredients": [{"item": "Chicken thighs", "qty": "1 lb", "category": "meat/seafood"}],
        "covers": ["protein"],
    }], reheat)

    view = signed_in.get("/api/grocery-list").json()
    thighs = [it for s in view["sections"] for it in s["items"] if it["item"].lower() == "chicken thighs"]
    assert thighs, "the side is bought for the reheat night"
    assert thighs[0]["freezing"]["entry_id"] == reheat
    assert thighs[0]["freezing"]["cook_date"] == _day(4)
