"""
A night off's freezer portion goes when the dish it was going to be does.

Overnight hunt, 2026-11-03 (Plan the Week). Tonight's Garlic Shrimp is
cooked double to feed Friday. Tonight off: the cook moves onto Friday and
tonight's share is written into the freezer ahead of the cook
(tonight._shrink_chain_into_freezer — Friday's row carries
leftovers.FREEZER_EXTRA_KEY naming the inventory row). Then the household
changes Friday on Plan:

  * Swap it to Fish Tacos — the shrimp came off the list, but the freezer
    still held "Garlic Shrimp (cooked)" for next week's draft to plan a
    night around, and the swap carried the portion onto the TACOS, so
    Cook scaled the tacos up for a freezer share nobody had bought for.
  * The Review "−" on Friday — the cook is gone, and the same portion was
    left in the Kitchen's freezer as food in the house.

Each test says whether it is a CATCH (red on main) or a GUARD (green there).
"""
from __future__ import annotations

import datetime
import json

from conftest import household_today, on_the_weeks_monday

from app import tools
from app.db import get_conn
from app.tools import freezer_portions as _fp
from app.tools import leftovers as _leftovers
from app.tools import meal_move as _meal_move
from app.tools import swap_in_place as _swap
from app.tools import tonight as _tonight

ON_MONDAY = on_the_weeks_monday()


def _monday() -> datetime.date:
    today = household_today()
    return today - datetime.timedelta(days=today.weekday())


WEEK = _monday().isoformat()
DAYS = tools._week_dates(WEEK)
MON, TUE, WED, THU, FRI, SAT, SUN = DAYS
TONIGHT = WED
AFTERNOON = datetime.datetime.fromisoformat(f"{TONIGHT}T15:50:00")
DISHES = ["Toast Soldiers", "Bean Chili", "Garlic Shrimp",
          "Lentil Soup", "Fish Tacos", "Pizza Night", "Roast Veg"]


def _recipe(name):
    tools.add_recipe(
        name,
        ingredients=[{"item": f"Greens for {name}", "qty": "2 lb", "category": "produce"}],
        prep_time_minutes=10, cook_time_minutes=20, default_servings=4,
    )


def _night_off_with_a_portion():
    """Shrimp Wednesday cooked double for Friday, approved, tonight off."""
    tools.add_member("Emily")
    tools.add_member("Sam")
    plan = tools.create_weekly_plan(WEEK)["weekly_plan_id"]
    for day, dish in zip(DAYS, DISHES):
        _recipe(dish)
        if day == FRI:
            tools.plan_meal(day, DISHES[2], slot="dinner", weekly_plan_id=plan,
                            derived_from={"links_to": f"{TONIGHT}:dinner"})
            continue
        tools.plan_meal(day, dish, slot="dinner", weekly_plan_id=plan)
    tools.repair_leftover_chains(plan)
    tools.approve_weekly_plan(plan)
    tapped = _tonight.tonight_night_off(now=AFTERNOON)
    assert tapped["status"] == "night_off" and tapped["kind"] == "cook_on_fed", tapped
    assert tapped["frozen"], "premise: the night off counted a portion for the freezer"
    assert _freezer_rows() == ["Garlic Shrimp (cooked)"], "premise: written ahead of the cook"
    return plan


def _friday():
    conn = get_conn()
    row = conn.execute(
        "SELECT mpe.id, mpe.derived_from_json, COALESCE(r.name, mpe.freeform_meal) AS meal, mpe.slot_state "
        "FROM meal_plan_entries mpe LEFT JOIN recipes r ON r.id = mpe.recipe_id "
        "WHERE mpe.date = ? AND mpe.slot = 'dinner' AND mpe.household_id = 1",
        (FRI,),
    ).fetchone()
    conn.close()
    return {"id": row["id"], "meal": row["meal"], "state": row["slot_state"],
            "derived": json.loads(row["derived_from_json"] or "{}")}


def _freezer_rows():
    conn = get_conn()
    rows = conn.execute(
        "SELECT item FROM inventory_items WHERE household_id = 1 AND location = 'freezer' ORDER BY id"
    ).fetchall()
    conn.close()
    return [r["item"] for r in rows]


def _to_buy(item):
    conn = get_conn()
    rows = conn.execute(
        "SELECT quantity FROM grocery_items WHERE household_id = 1 AND item = ? AND status = 'needed'",
        (item,),
    ).fetchall()
    conn.close()
    return [r["quantity"] for r in rows]


def _picker(name):
    return lambda ctx: {"meal_name": name, "reason": "a change", "is_new_recipe": False, "food_groups": []}


@ON_MONDAY
def test_swapping_the_cook_takes_the_portion_out_of_the_freezer():
    """CATCH. Friday's shrimp swapped to Lentil Soup: no shrimp is cooked,
    so there is no portion — not in the freezer, not for next week."""
    plan = _night_off_with_a_portion()

    out = _swap.swap_meal_in_place(plan, _friday()["id"], picker=_picker("Lentil Soup"))

    assert out["status"] == "swapped", out
    assert _freezer_rows() == []
    assert _fp.portions_to_plan() == []


@ON_MONDAY
def test_the_new_dish_is_cooked_and_bought_for_the_table_only():
    """CATCH. The soup does not inherit the shrimp's freezer share: Cook
    would have scaled it to four servings with two bought."""
    plan = _night_off_with_a_portion()

    _swap.swap_meal_in_place(plan, _friday()["id"], picker=_picker("Lentil Soup"))

    fri = _friday()
    assert fri["meal"] == "Lentil Soup"
    assert _leftovers.FREEZER_EXTRA_KEY not in fri["derived"]
    assert _leftovers.freezer_servings(fri["derived"]) == 0
    # Thursday's soup and Friday's, one pound each for a table of two —
    # never Friday's table plus a freezer share for it.
    assert _to_buy("Greens for Lentil Soup") == ["2 lbs"]
    assert _to_buy("Greens for Garlic Shrimp") == []


@ON_MONDAY
def test_cutting_the_cook_back_takes_the_portion_out_of_the_freezer():
    """CATCH. The Review "−" on Friday: no cook, no portion."""
    plan = _night_off_with_a_portion()

    out = tools.drop_dish_from_day(plan, _friday()["id"])

    assert out["status"] == "dropped", out
    assert _friday()["state"] == "open"
    assert _freezer_rows() == []


@ON_MONDAY
def test_moving_the_cook_keeps_its_portion():
    """GUARD. The same shrimp on another night is still the batch that makes
    the portion — the portion goes with it, still hidden until it's cooked."""
    plan = _night_off_with_a_portion()

    out = _meal_move.move_meal(plan, _friday()["id"], SAT)

    assert out["status"] == "moved", out
    conn = get_conn()
    hidden = _fp.not_yet_cooked_portion_ids(conn)
    conn.close()
    assert _freezer_rows() == ["Garlic Shrimp (cooked)"]
    assert hidden, "still waiting on its cook"


@ON_MONDAY
def test_undoing_the_swap_brings_the_dish_back_without_a_ghost_portion():
    """CATCH. Undo puts the shrimp back for Friday's table, and the list and
    the freezer agree with that (on main the portion rode back on, with the
    list bought for the table alone)."""
    plan = _night_off_with_a_portion()
    swapped = _swap.swap_meal_in_place(plan, _friday()["id"], picker=_picker("Lentil Soup"))

    back = _swap.undo_meal_swap(plan, swapped["entry_id"])

    assert back["status"] == "restored", back
    fri = _friday()
    assert fri["meal"] == "Garlic Shrimp"
    assert _leftovers.freezer_servings(fri["derived"]) == 0
    assert _freezer_rows() == []
    assert _to_buy("Greens for Garlic Shrimp") == ["1 lb"]


# ------------------------------------------------ review, 2026-11-03 (FIX FIRST)

def _inventory_id():
    conn = get_conn()
    row = conn.execute("SELECT id FROM inventory_items WHERE household_id = 1 AND location = 'freezer'").fetchone()
    conn.close()
    return row["id"]


@ON_MONDAY
def test_a_cooked_batch_keeps_its_real_portion_when_swapped_afterwards():
    """CATCH (of the first version of this fix). Friday's shrimp ticked done:
    the portion is real food in the freezer now. Swapping Friday afterwards
    must not delete it — and the soup still doesn't carry it."""
    plan = _night_off_with_a_portion()
    tools.check_off_meal(_friday()["id"], "done")

    _swap.swap_meal_in_place(plan, _friday()["id"], picker=_picker("Lentil Soup"))

    assert _freezer_rows() == ["Garlic Shrimp (cooked)"]
    assert _leftovers.FREEZER_EXTRA_KEY not in _friday()["derived"]
    conn = get_conn()
    hidden = _fp.not_yet_cooked_portion_ids(conn)
    conn.close()
    assert not hidden, "real food: shown in the freezer, not waiting on a cook"


@ON_MONDAY
def test_a_portion_next_week_already_eats_is_left_as_it_was():
    """CATCH (of the first version). Next week's draft has Tuesday eating the
    portion. Swapping Friday neither deletes it (that night would eat food
    nobody made) nor strips the key (it would read as real food): the week
    is left exactly as it was."""
    plan = _night_off_with_a_portion()
    inv_id = _inventory_id()
    _recipe("Next Week Tacos")
    later = tools.create_weekly_plan((_monday() + datetime.timedelta(days=7)).isoformat())["weekly_plan_id"]
    tools.plan_meal((_monday() + datetime.timedelta(days=8)).isoformat(), "Next Week Tacos", slot="dinner",
                    weekly_plan_id=later,
                    derived_from={_fp.KEY: {"inventory_item_id": inv_id, "dish": "Garlic Shrimp"}})

    _swap.swap_meal_in_place(plan, _friday()["id"], picker=_picker("Lentil Soup"))

    assert _freezer_rows() == ["Garlic Shrimp (cooked)"]
    extra = _friday()["derived"].get(_leftovers.FREEZER_EXTRA_KEY) or {}
    assert extra.get("inventory_item_ids") == [inv_id]


@ON_MONDAY
def test_typing_the_same_dish_over_keeps_its_portion():
    """CATCH (of the first version). "garlic shrimp " typed over Garlic
    Shrimp is the same batch: the new row carries the portion, so a later
    swap still finds it and lets it go."""
    plan = _night_off_with_a_portion()

    tools.swap_meal_in_plan(plan, FRI, "garlic shrimp ")

    extra = _friday()["derived"].get(_leftovers.FREEZER_EXTRA_KEY) or {}
    assert extra.get("inventory_item_ids") == [_inventory_id()]
    assert _freezer_rows() == ["Garlic Shrimp (cooked)"]

    _swap.swap_meal_in_place(plan, _friday()["id"], picker=_picker("Lentil Soup"))
    assert _freezer_rows() == []
