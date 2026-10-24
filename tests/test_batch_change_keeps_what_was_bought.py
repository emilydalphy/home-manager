"""
A batch that changes after the shop does not buy the shop again
(hunt-leftovers-1024, 2026-10-24).

weekly_plan._rescale_leftover_source_grocery re-derives a batched recipe's
whole week — reverse every ledger row, ingest again — and a reversal leaves
a ticked line (in the cart, or bought) exactly as it is. So whatever had
already been bought went back on the list in full beside it. Driven on a
throwaway DB, Tuesday's Turkey Chili feeding Thursday, the shop done:

  clear Thursday           "1 lb" turkey + "1 can" beans needed again
  "don't batch the chili"  "2 lbs" turkey + "2 cans" beans needed again

for a week whose food was already in the kitchen. Ticking a line
"purchased" also adds it to inventory, and the ingest's "already have"
hides the re-buy while that row still covers the whole amount — which is
why it shows after a partial shop, or once the batch cooked on Monday
(check_off_meal depletes the kitchen) and the household then says "cook the
chili fresh on Wednesday". The rule a recipe edit
follows on its own branch — a ticked line buys only the difference — now
holds for a batch change too.

Every test says whether it is a CATCH (red on main) or a GUARD.
"""
from __future__ import annotations

import datetime

from conftest import household_today, on_the_weeks_monday

from app import tools
from app.db import get_conn
from app.tools import batch_undo as _batch_undo
from app.tools import cook_ahead as _cook_ahead


def _monday() -> datetime.date:
    today = household_today()
    return today - datetime.timedelta(days=today.weekday())


pytestmark = on_the_weeks_monday(_monday())

DAYS = [(_monday() + datetime.timedelta(days=i)).isoformat() for i in range(7)]
MON, TUE, WED, THU, FRI, SAT, SUN = DAYS


def _chili():
    for n in ("Alex", "Sam"):
        tools.add_member(n)
    tools.add_recipe(
        "Turkey Chili",
        ingredients=[
            {"item": "ground turkey", "qty": "1 lb", "category": "meat"},
            {"item": "kidney beans", "qty": "1 can", "category": "pantry"},
        ],
        default_servings=2, prep_time_minutes=10, cook_time_minutes=30,
    )


def _planner_chain(also_saturday: bool = False) -> int:
    _chili()
    plan = tools.create_weekly_plan(MON)["weekly_plan_id"]
    tools.plan_meal(TUE, "Turkey Chili", slot="dinner", weekly_plan_id=plan)
    tools.plan_meal(THU, "Turkey Chili", slot="dinner", weekly_plan_id=plan,
                    derived_from={"links_to": f"{TUE}:dinner"})
    if also_saturday:
        tools.plan_meal(SAT, "Turkey Chili", slot="dinner", weekly_plan_id=plan)
    tools.repair_leftover_chains(plan)
    tools.approve_weekly_plan(plan)
    return plan


def _lines() -> list[tuple[str, str, str]]:
    conn = get_conn()
    rows = conn.execute("SELECT item, quantity, status FROM grocery_items ORDER BY id").fetchall()
    conn.close()
    return [(r["item"], r["quantity"], r["status"]) for r in rows]


def _tick(item: str | None = None, status: str = "purchased") -> None:
    conn = get_conn()
    ids = [r["id"] for r in conn.execute(
        "SELECT id FROM grocery_items WHERE status = 'needed'" + (" AND item = ?" if item else ""),
        (item,) if item else (),
    ).fetchall()]
    conn.close()
    for i in ids:
        tools.mark_grocery_item(i, status)


def _needed() -> dict[str, str]:
    return {i: q for i, q, s in _lines() if s == "needed"}


def _entry(plan: int, day: str) -> int:
    conn = get_conn()
    row = conn.execute(
        "SELECT id FROM meal_plan_entries WHERE weekly_plan_id = ? AND date = ? AND slot = 'dinner'",
        (plan, day),
    ).fetchone()
    conn.close()
    return row["id"]


def test_clearing_the_reheat_night_after_the_shop_buys_nothing_again():
    """GUARD on main as long as the bought food is still in inventory (the
    ingest's "already have" covers it) — the shape the CATCH below breaks."""
    plan = _planner_chain()
    assert _needed() == {"ground turkey": "2 lbs", "kidney beans": "2 cans"}
    _tick()
    tools.clear_plan_slot(plan, THU, "dinner")
    assert _needed() == {}


def test_clearing_the_reheat_night_after_the_batch_was_cooked_buys_nothing_again():
    """GUARD — Tuesday cooked and the kitchen's turkey depleted, then
    Thursday cleared: nothing for an eaten dinner comes back on the list."""
    plan = _planner_chain()
    _tick()
    tools.check_off_meal(_entry(plan, TUE), "done")
    tools.clear_plan_slot(plan, THU, "dinner")
    assert _needed() == {}


def test_unbatching_after_the_shop_buys_nothing_again():
    """CATCH — the batch cooked on Monday, then "cook the chili fresh on
    Wednesday": the whole "2 lbs" / "2 cans" went back on the list."""
    _chili()
    plan = tools.create_weekly_plan(MON)["weekly_plan_id"]
    tools.plan_meal(MON, "Turkey Chili", slot="dinner", weekly_plan_id=plan)
    tools.plan_meal(WED, "Turkey Chili", slot="dinner", weekly_plan_id=plan)
    tools.approve_weekly_plan(plan)
    conn = get_conn()
    mon_id, wed_id = [r["id"] for r in conn.execute(
        "SELECT id FROM meal_plan_entries WHERE weekly_plan_id = ? ORDER BY date", (plan,))]
    conn.close()
    _cook_ahead.set_cook_ahead(mon_id, [wed_id])
    _tick()
    tools.check_off_meal(mon_id, "done")
    out = _batch_undo.unbatch("chili")
    assert out["status"] == "unbatched"
    assert _needed() == {}
    assert out["list_changed"] is False and "list has changed" not in out["said"]


def test_only_the_untouched_line_is_restated():
    """GUARD — the ticked turkey buys nothing more; the never-ticked beans
    follow the smaller batch as they always did."""
    plan = _planner_chain(also_saturday=True)
    assert _needed() == {"ground turkey": "3 lbs", "kidney beans": "3 cans"}
    _tick("ground turkey")
    tools.clear_plan_slot(plan, THU, "dinner")
    assert _needed() == {"kidney beans": "2 cans"}


def test_a_partial_shop_buys_only_the_difference():
    """CATCH — a ticked line smaller than the week's need: the list keeps
    the difference, not the whole amount again."""
    plan = _planner_chain(also_saturday=True)
    # The shopper found only 1 lb of turkey and ticked that.
    conn = get_conn()
    conn.execute("UPDATE grocery_items SET quantity = '1 lb' WHERE item = 'ground turkey'")
    conn.execute("UPDATE meal_plan_grocery_links SET quantity = '1 lb' WHERE item = 'ground turkey' "
                 "AND id = (SELECT MIN(id) FROM meal_plan_grocery_links WHERE item = 'ground turkey')")
    conn.execute("DELETE FROM meal_plan_grocery_links WHERE item = 'ground turkey' "
                 "AND id != (SELECT MIN(id) FROM meal_plan_grocery_links WHERE item = 'ground turkey')")
    conn.commit()
    conn.close()
    _tick("ground turkey")
    tools.clear_plan_slot(plan, THU, "dinner")
    # Tuesday + Saturday need 2 lbs; 1 lb is already bought.
    assert _needed().get("ground turkey") == "1 lb"


def test_nothing_ticked_behaves_as_before():
    """GUARD — with no tick, clearing the reheat night shrinks the line."""
    plan = _planner_chain()
    tools.clear_plan_slot(plan, THU, "dinner")
    assert _needed() == {"ground turkey": "1 lb", "kidney beans": "1 can"}
