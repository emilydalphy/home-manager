"""
A nights swap could put a cook AFTER the night eating its frozen portion.

Defect hunt, 2026-10-20. Monday's Turkey Chili cooks a portion for the
freezer; Friday is "Leftovers from the freezer — Monday's Turkey Chili".
Dragging Monday's dinner to Saturday (Plan's drag, the chat tool and Now's
"Something else" all go through swap_dinner_nights) answered `swapped`:
Saturday cooked "for 4, plus 2 for the freezer", Friday still reheated a
portion that would not exist until the next day, and Thursday still said
"Move the Turkey Chili to the fridge". The Move sheet has refused exactly
this since it was built ("would end up after its frozen portion"), but the
shared writer (_redate_plan_rows) only ever checked `links_to`, and a
freezer night carries `from_freezer` instead.

And the swap that IS allowed (Monday's chili to Tuesday) left Friday
naming "Monday's Turkey Chili" over a Monday that now has tacos — the
Move sheet re-says that name (meal_move._resay_freezer_nights), the swap
did not.
"""
import datetime

from conftest import household_today, on_the_weeks_monday

from app import tools
from app.db import get_conn

pytestmark = on_the_weeks_monday()


def _monday() -> datetime.date:
    today = household_today()
    return today - datetime.timedelta(days=today.weekday())


def _day(offset: int) -> str:
    return (_monday() + datetime.timedelta(days=offset)).isoformat()


MON, TUE, FRI, SAT = _day(0), _day(1), _day(4), _day(5)


def _week():
    """Monday's chili feeds Friday — four days on, so the chain repair
    turns Friday into a freezer night and Monday cooks the portion extra."""
    tools.add_member("Emily")
    tools.add_member("Jamie")
    tools.add_recipe("Turkey Chili", ingredients=[{"item": "Ground turkey", "qty": "1.5 lbs"}],
                     default_servings=2)
    tools.add_recipe("Tacos", ingredients=[{"item": "Tortillas", "qty": "1 pack"}], default_servings=2)
    tools.add_recipe("Stir Fry", ingredients=[{"item": "Broccoli", "qty": "1 lb"}], default_servings=2)
    plan_id = tools.create_weekly_plan(MON)["weekly_plan_id"]
    cook = tools.plan_meal(MON, "Turkey Chili", slot="dinner", weekly_plan_id=plan_id)["entry_id"]
    tools.plan_meal(TUE, "Tacos", slot="dinner", weekly_plan_id=plan_id)
    tools.plan_meal(FRI, "Turkey Chili", slot="dinner", weekly_plan_id=plan_id,
                    derived_from={"links_to": f"{MON}:dinner"})
    tools.plan_meal(SAT, "Stir Fry", slot="dinner", weekly_plan_id=plan_id)
    tools.repair_leftover_chains(plan_id)
    tools.approve_weekly_plan(plan_id, approved_by="Emily")
    return plan_id, cook


def _dinners(plan_id):
    conn = get_conn()
    rows = conn.execute(
        "SELECT mpe.id, mpe.date, COALESCE(r.name, mpe.freeform_meal) AS meal FROM meal_plan_entries mpe "
        "LEFT JOIN recipes r ON r.id = mpe.recipe_id WHERE mpe.weekly_plan_id = ? AND mpe.slot = 'dinner'",
        (plan_id,),
    ).fetchall()
    conn.close()
    return {r["date"]: (r["id"], r["meal"]) for r in rows}


def test_the_swap_will_not_cook_the_chili_after_its_frozen_portion(signed_in):
    plan_id, cook = _week()
    assert _dinners(plan_id)[FRI][1] == "Leftovers from the freezer — Monday’s Turkey Chili"

    res = signed_in.post(f"/api/week/{MON}/swap-nights", json={"date_a": MON, "date_b": SAT})

    assert res.status_code == 200
    body = res.json()
    assert body["status"] == "refused", body
    assert "Friday" in body["message"]
    after = _dinners(plan_id)
    assert after[MON] == (cook, "Turkey Chili"), "nothing moved"
    assert after[SAT][1] == "Stir Fry"


def test_a_swap_that_keeps_the_order_still_goes_through_and_renames_the_freezer_night():
    """GUARD for the refusal (the cook only moves EARLIER here), and the
    second catch: Friday names the day the chili is cooked now."""
    plan_id, cook = _week()

    out = tools.swap_dinner_nights(plan_id, MON, TUE)

    assert out["status"] == "swapped", out
    after = _dinners(plan_id)
    assert after[TUE] == (cook, "Turkey Chili")
    assert after[FRI][1] == "Leftovers from the freezer — Tuesday’s Turkey Chili"

    # Undo puts the name back with the night.
    assert tools.undo_dinner_nights_swap(plan_id, MON, TUE)["status"] == "restored"
    assert _dinners(plan_id)[FRI][1] == "Leftovers from the freezer — Monday’s Turkey Chili"
