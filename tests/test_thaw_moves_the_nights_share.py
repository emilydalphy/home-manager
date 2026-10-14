"""
A booked fridge move carries the NIGHT'S share of the frozen meat, not the
recipe's full amount (review of overnight/freeze-keeps-the-sooner-night,
2026-10-13: "existing gap … separate fix").

Measured before the fix: a recipe written for 4 ("1 lb ground beef"), a
household of three. The list bought 12 oz, the ledger held 12 oz, Cook
showed 0.75 lb — and the move booked "1 lb". The amount is the same
composition the grocery ingest uses (attendance.servings_scale_factor x
the batch factor), so the three agree.
"""
import datetime

from app import tools
from app.db import get_conn
from app.tools import defrost, quantities
from conftest import household_today


def _week() -> tuple[str, list[str]]:
    today = household_today()
    monday = today - datetime.timedelta(days=today.weekday()) + datetime.timedelta(days=7)
    week = monday.isoformat()
    return week, tools._week_dates(week)


def _three_at_home():
    for n in ("Alex", "Sam", "Rae"):
        tools.add_member(n)


def _tacos(qty="1 lb", default_servings=4):
    tools.add_recipe(
        "Beef Tacos",
        ingredients=[{"item": "Ground beef", "qty": qty, "category": "meat/seafood"}],
        default_servings=default_servings, prep_time_minutes=10, cook_time_minutes=15,
    )


def _move_quantities() -> list[str]:
    conn = get_conn()
    rows = conn.execute(
        "SELECT quantity FROM prep_tasks WHERE task_type = 'defrost' ORDER BY task_date, id"
    ).fetchall()
    conn.close()
    return [r["quantity"] for r in rows]


def _ledger(entry_id: int) -> str:
    conn = get_conn()
    row = conn.execute(
        "SELECT quantity FROM meal_plan_grocery_links WHERE meal_plan_entry_id = ? AND item = 'Ground beef'",
        (entry_id,),
    ).fetchone()
    conn.close()
    return row["quantity"]


def _same_amount(a: str, b: str) -> bool:
    pa, pb = quantities._parse_quantity(a), quantities._parse_quantity(b)
    assert pa and pb, (a, b)
    converted = quantities._convert_to_unit(pa[0], pa[1], pb[1])
    return converted is not None and abs(converted - pb[0]) < 1e-6


def test_the_move_carries_the_nights_share_not_the_recipes_amount():
    _three_at_home()
    _tacos()
    week, dates = _week()
    plan_id = tools.create_weekly_plan(week)["weekly_plan_id"]
    entry = tools.plan_meal(dates[3], "Beef Tacos", slot="dinner", weekly_plan_id=plan_id,
                            add_ingredients_to_grocery_list=True)

    defrost.confirm_frozen_items(plan_id, ["Ground beef"])

    [qty] = _move_quantities()
    assert _same_amount(qty, "12 oz"), qty
    assert _same_amount(qty, _ledger(entry["entry_id"])), "the same share the list bought for that night"


def test_a_batch_night_thaws_its_whole_cook_amount():
    _three_at_home()
    _tacos()
    week, dates = _week()
    tue, thu = dates[1], dates[3]
    plan_id = tools.create_weekly_plan(week)["weekly_plan_id"]
    cook = tools.plan_meal(tue, "Beef Tacos", slot="dinner", weekly_plan_id=plan_id)
    tools.plan_meal(thu, "Beef Tacos", slot="dinner", weekly_plan_id=plan_id,
                    derived_from={"links_to": f"{tue}:dinner"})
    tools.repair_leftover_chains(plan_id)
    tools.approve_weekly_plan(plan_id)

    defrost.confirm_frozen_items(plan_id, ["Ground beef"])

    [qty] = _move_quantities()
    # Six eaters across the two nights from a recipe written for four.
    assert _same_amount(qty, "1.5 lb"), qty
    assert _same_amount(qty, _ledger(cook["entry_id"])), "the cook night's share covers both nights"


def test_an_amount_with_no_number_still_books_as_written():
    _three_at_home()
    _tacos(qty="some")
    week, dates = _week()
    plan_id = tools.create_weekly_plan(week)["weekly_plan_id"]
    tools.plan_meal(dates[3], "Beef Tacos", slot="dinner", weekly_plan_id=plan_id)

    result = defrost.confirm_frozen_items(plan_id, ["Ground beef"])

    assert len(result["created"]) == 1
    assert _move_quantities() == ["some"]


def test_a_recipe_written_for_the_table_is_left_byte_for_byte():
    _three_at_home()
    _tacos(qty="1 lb", default_servings=3)
    week, dates = _week()
    plan_id = tools.create_weekly_plan(week)["weekly_plan_id"]
    tools.plan_meal(dates[3], "Beef Tacos", slot="dinner", weekly_plan_id=plan_id)

    defrost.confirm_frozen_items(plan_id, ["Ground beef"])

    assert _move_quantities() == ["1 lb"]


def test_the_freezer_inventory_path_books_the_same_share():
    _three_at_home()
    _tacos()
    tools.update_inventory("Ground beef", "add", quantity="2 lbs", category="meat/seafood", location="freezer")
    week, dates = _week()
    plan_id = tools.create_weekly_plan(week)["weekly_plan_id"]
    tools.plan_meal(dates[3], "Beef Tacos", slot="dinner", weekly_plan_id=plan_id)

    [candidate] = tools.defrost_candidates_for_plan(plan_id)

    assert _same_amount(candidate["quantity"], "12 oz"), candidate["quantity"]
