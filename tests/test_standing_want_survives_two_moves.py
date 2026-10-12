"""
A household's own pound of beef survives a batch cook moving twice.

Loop Board bug (Low), 2026-10-09: "Moving a batch cook twice turns the
household's own 1 lb of beef into '12 oz'". Hand-added 1 lb of ground beef,
a cook feeding two later nights; drop the cook night (the cook moves onto
the first fed night), then drop that one (it moves onto the last). The card
read the list as "12 oz" and the pound as gone, coming back once the last
night went too.

NOT REPRODUCIBLE AS A LOSS on main (1328b1a), and these pin why. After the
second move the list holds TWO rows named "Ground beef": the household's
own "1 lb" (standing, source_weekly_plan_id NULL, restated correctly by
_restate_standing_want) and the plan's "12 oz" on a plan-owned line of its
own. Twelve ounces is under a pound, so the re-ingest's amount is written
in oz, and a plan's share in another unit never joins a household's line
(_merge_target / _try_consolidate_quantity — kept that way on purpose, see
the "Same-family units add up on a plan's line" decision-log entry: summing
across units on a standing want ratchets it). Read the list as
{item: quantity} and the second row overwrites the first — "12 oz", the
pound "gone", and "back" when the 12 oz row is deleted with the last night.
That is exactly the card's symptom, and it is the reading, not the list.

So these read ROWS, never a dict keyed by name.
"""
from __future__ import annotations

import datetime
import json

from conftest import household_today
from app import tools
from app.db import get_conn

TODAY = household_today()
WEEK_START = (TODAY - datetime.timedelta(days=1)).isoformat()


def D(n: int) -> str:
    return (TODAY + datetime.timedelta(days=n)).isoformat()


def _ids(day: str, slot: str) -> list[int]:
    conn = get_conn()
    rows = conn.execute(
        "SELECT id FROM meal_plan_entries WHERE household_id = ? AND date = ? AND slot = ? ORDER BY id",
        (tools.household_id(), day, slot)).fetchall()
    conn.close()
    return [r["id"] for r in rows]


def _beef() -> list[tuple[str, bool]]:
    """Every needed Ground beef row, as (quantity, is the household's own)."""
    conn = get_conn()
    rows = conn.execute(
        "SELECT quantity, source_weekly_plan_id FROM grocery_items "
        "WHERE household_id = ? AND status = 'needed' AND item = 'Ground beef' ORDER BY id",
        (tools.household_id(),)).fetchall()
    conn.close()
    return [(r["quantity"], r["source_weekly_plan_id"] is None) for r in rows]


def _week() -> tuple[int, int]:
    """Four at the table, a recipe for four wanting 12 oz, a hand-added
    1 lb, and tomorrow's dinner cooked for two later dinners too (the
    card's Tuesday/Thursday/Friday, put on the household clock). Approved:
    1 lb + three nights' 2.25 lbs = 3.25 lbs, one line."""
    for name in ("Emily", "Vineeth", "Kid", "Baby"):
        tools.add_member(name)
    tools.add_recipe(
        "Beef Ragu",
        ingredients=[{"item": "Ground beef", "qty": "12 oz", "category": "meat"}],
        default_servings=4, instructions=["Brown the beef."],
        prep_time_minutes=10, cook_time_minutes=20,
    )
    tools.add_grocery_item("Ground beef", quantity="1 lb", category="meat")
    plan = tools.create_weekly_plan(WEEK_START)["weekly_plan_id"]
    cook, fed = D(1), [D(3), D(4)]
    tools.plan_meal(cook, "Beef Ragu", slot="dinner", weekly_plan_id=plan)
    source = _ids(cook, "dinner")[-1]
    reheats = []
    for day in fed:
        tools.plan_meal(day, "Beef Ragu", slot="dinner", weekly_plan_id=plan)
        reheats.append(_ids(day, "dinner")[-1])
    # Written after the plan_meal calls, never between them: each opens its
    # own connection, and an open write here would lock it out.
    conn = get_conn()
    for entry_id in reheats:
        conn.execute("UPDATE meal_plan_entries SET derived_from_json = ? WHERE id = ?",
                     (json.dumps({"links_to": f"{cook}:dinner"}), entry_id))
    conn.execute("UPDATE meal_plan_entries SET derived_from_json = ? WHERE id = ?",
                 (json.dumps({"make_double_for": [f"{d}:dinner" for d in fed]}), source))
    conn.commit()
    conn.close()
    assert source in tools.plan_leftover_chains(plan)["sources"]
    tools.approve_weekly_plan(plan)
    assert _beef() == [("3.25 lbs", True)]
    return plan, source


def test_the_households_pound_is_still_on_the_list_after_two_moves():
    """The card's sequence through the Review "−". The pound is its own
    line, the plan's one remaining night is 12 oz beside it — 1.75 lbs in
    all, nothing of the household's taken."""
    plan, source = _week()

    assert tools.drop_dish_from_day(plan, source)["moved_to"] == D(3)
    assert _beef() == [("2.5 lbs", True)]

    assert tools.drop_dish_from_day(plan, source)["moved_to"] == D(4)
    assert _beef() == [("1 lb", True), ("12 oz", False)]

    # The last night going takes only the plan's line with it.
    tools.drop_dish_from_day(plan, source)
    assert _beef() == [("1 lb", True)]


def test_undoing_both_moves_puts_one_line_back_at_its_size():
    """The undo record restores the line it changed, and the plan's own
    12 oz line goes with it — never 2.5 lbs AND 12 oz."""
    plan, source = _week()
    first = tools.drop_dish_from_day(plan, source)
    second = tools.drop_dish_from_day(plan, source)

    assert tools.drop_dish_undo(plan, second["undo_entry_id"])["status"] == "restored"
    assert _beef() == [("2.5 lbs", True)]
    assert tools.drop_dish_undo(plan, first["undo_entry_id"])["status"] == "restored"
    assert _beef() == [("3.25 lbs", True)]


def test_everyone_out_twice_moves_the_cook_without_taking_the_pound():
    """The card's other door: nobody home on the cook night, then on the
    night it moved to. Same shared rescale, same answer."""
    plan, source = _week()

    tools.set_slot_need(D(1), "dinner", "away")
    tools.set_slot_need(D(3), "dinner", "away")

    assert _ids(D(4), "dinner") == [source], "the cook moved twice, onto the last night"
    assert _beef() == [("1 lb", True), ("12 oz", False)]
