"""
An approved week, and the household is out on a night that was cooking
double for a later one.

MEASURED through POST /api/week/{week}/attendance on a throwaway DB before
the fix: Tuesday's Bulgogi Wraps (cooked double, Thursday reheating it) went
planned_empty, its whole doubled batch came off the list — beef and lettuce
gone entirely — and Thursday stayed on the week as an ordinary cook of the
dish, its links_to naming a night now marked nobody home, with nothing
bought for it. The night off and the Review "−" already move the cook onto
the first night it fed; the away did not.

MUTATIONS RUN (red counts over this file, read off the run):
  1. set_slot_need's _cook_on_the_fed_night call removed (main)  -> 2 failed
  2. the approved-week rescale skipped                           -> 2 failed
  3. fed[0] replaced by fed[-1] (lands on the LAST fed night)    -> 1 failed
"""
from __future__ import annotations

import datetime

from app import tools
from app.db import get_conn
from app.tools import leftovers


def _monday() -> datetime.date:
    from conftest import household_today
    today = household_today()
    return today - datetime.timedelta(days=today.weekday())


# Next week, so no night has gone by.
WEEK = (_monday() + datetime.timedelta(days=7)).isoformat()
MON, TUE, WED, THU, FRI, SAT, SUN = tools._week_dates(WEEK)


def _week(fed: tuple[str, ...]) -> int:
    tools.add_member("Alex")
    tools.add_member("Sam")
    tools.add_recipe("Bulgogi Wraps", ingredients=[
        {"item": "beef", "qty": "1 lb"}, {"item": "lettuce", "qty": "1 head"},
    ], default_servings=2)
    tools.add_recipe("Soup", ingredients=[{"item": "stock", "qty": "1 l"}], default_servings=2)
    plan = tools.create_weekly_plan(WEEK)["weekly_plan_id"]
    for d in (MON, TUE, WED, THU, FRI, SAT, SUN):
        if d == TUE:
            tools.plan_meal(d, "Bulgogi Wraps", slot="dinner", weekly_plan_id=plan)
        elif d in fed:
            tools.plan_meal(d, "Bulgogi Wraps", slot="dinner", weekly_plan_id=plan,
                            derived_from={"links_to": f"{TUE}:dinner"})
        else:
            tools.plan_meal(d, "Soup", slot="dinner", weekly_plan_id=plan)
    tools.repair_leftover_chains(plan)
    tools.approve_weekly_plan(plan, "Alex")
    return plan


def _dinner(day: str) -> dict:
    conn = get_conn()
    rows = conn.execute(
        "SELECT mpe.id, mpe.slot_state, r.name AS meal FROM meal_plan_entries mpe "
        "LEFT JOIN recipes r ON r.id = mpe.recipe_id "
        "WHERE mpe.household_id = ? AND mpe.date = ? AND mpe.slot = 'dinner'",
        (tools.household_id(), day),
    ).fetchall()
    conn.close()
    assert len(rows) == 1, rows
    return dict(rows[0])


def _list() -> dict:
    return {g["item"]: g["quantity"] for g in tools.list_grocery_list()}


def _everyone_out_tuesday(signed_in) -> None:
    for name in ("Alex", "Sam"):
        r = signed_in.post(f"/api/week/{WEEK}/attendance",
                        json={"date": TUE, "slot": "dinner", "member": name, "present": False})
        assert r.status_code == 200, r.text


def test_out_on_the_cook_night_moves_the_cook_onto_the_night_it_fed(signed_in):
    plan = _week(fed=(THU,))
    assert _list()["beef"] == "2 lbs"

    _everyone_out_tuesday(signed_in)

    assert _dinner(TUE)["slot_state"] == "planned_empty"
    thu = _dinner(THU)
    assert (thu["slot_state"], thu["meal"]) == ("planned", "Bulgogi Wraps")
    # Thursday is the cook now, not a reheat of a night nobody cooked.
    assert thu["id"] not in leftovers.plan_leftover_chains(plan)["leftovers"]
    # Bought for the one night still eating it — not 2 lbs, and not nothing.
    assert (_list().get("beef"), _list().get("lettuce")) == ("1 lb", "1 head")
    audit = tools.audit_plan_slots(plan)
    assert audit["duplicated"] == [] and not [m for m in audit["missing"] if m["slot"] == "dinner"]


def test_a_later_fed_night_keeps_reheating_from_the_new_cook_night(signed_in):
    plan = _week(fed=(THU, FRI))
    assert _list()["beef"] == "3 lbs"

    _everyone_out_tuesday(signed_in)

    thu, fri = _dinner(THU), _dinner(FRI)
    chain = leftovers.plan_leftover_chains(plan)["leftovers"].get(fri["id"])
    assert chain is not None and chain["source"]["entry_id"] == thu["id"]
    assert thu["id"] not in leftovers.plan_leftover_chains(plan)["leftovers"]
    assert _list()["beef"] == "2 lbs"
