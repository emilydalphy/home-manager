"""
Monday morning right after the week turns over (overnight defect hunt,
2026-10-26). A two-adult household with a kid, last week approved and
partly cooked and shopped, this week approved Sunday night — walked at
390px through Today, Plan, Shop and Cook at 07:30, 12:00 and 18:00, and the
morning text rendered. Each test here is one thing that walk found wrong:

  * a tin got a fresh tomato's shelf life;
  * with no new week approved, Shop drew last week's ticks as this list's;
  * cooking a "1 lb" recipe took nothing off a "12 oz" shelf row.
"""
from __future__ import annotations

import datetime as _dt

import pytest

from app import tools
from app.tools import quantities
from conftest import household_pin

MON = _dt.date(2026, 10, 26)
LAST_MON = "2026-10-19"


def test_a_tin_keeps_like_a_tin_whatever_is_in_it():
    """CATCH (red on main): canned diced tomatoes matched "tomato" (7 days)."""
    assert quantities._lookup_item_shelf_life_days("diced tomatoes", "canned") == 365
    assert quantities._lookup_item_shelf_life_days("coconut milk", "canned") == 365
    assert quantities._lookup_item_shelf_life_days("tomatoes", "produce") == 7
    # A tin added by chat, a scan or the form, filed by what's in it.
    assert quantities._lookup_item_shelf_life_days("canned diced tomatoes", "pantry") == 365
    assert quantities._lookup_item_shelf_life_days("coconut milk", "pantry") == 365
    assert quantities._lookup_item_shelf_life_days("canned salmon", "meat/seafood") == 365
    assert quantities._lookup_item_shelf_life_days("salmon", "meat/seafood") == 2


# ---------- Shop: last week's ticks end with last week ----------

def test_last_weeks_ticks_are_not_this_lists_once_that_week_is_over(frozen_today):
    """CATCH (red on main): this week still a draft (or nothing planned),
    Shop opened on "11 of 17" with last Monday's shop struck through. Inside
    the week they are still this list's ticks."""
    frozen_today(household_pin(20, on=_dt.date(2026, 10, 18)))
    tools.add_recipe("Beef Tacos", ingredients=[{"item": "ground beef", "qty": "1 lb"}], default_servings=4)
    plan_id = tools.create_weekly_plan(LAST_MON)["weekly_plan_id"]
    tools.plan_meal(LAST_MON, "Beef Tacos", slot="dinner", weekly_plan_id=plan_id)
    tools.approve_weekly_plan(plan_id, approved_by="Maya")
    frozen_today(household_pin(10, on=_dt.date(2026, 10, 19)))
    beef = next(i for i in tools.list_grocery_list("needed") if i["item"] == "ground beef")
    tools.mark_grocery_item(beef["id"], "purchased")
    frozen_today(household_pin(21, on=_dt.date(2026, 10, 25)))
    assert [i["item"] for i in tools.list_grocery_list("bought")] == ["ground beef"]
    frozen_today(household_pin(7, 30, on=MON))
    assert tools.list_grocery_list("bought") == []


def test_reapproving_last_week_does_not_hide_next_weeks_sunday_shop(frozen_today):
    """GUARD (review, 2026-10-26): next week approved Saturday, last week
    reopened and re-approved Sunday morning, next week's chicken bought
    Sunday afternoon. Monday it is still this list's tick: the window ends
    with the LATEST approved period, not the latest approval's."""
    def week(mon, recipe, item):
        tools.add_recipe(recipe, ingredients=[{"item": item, "qty": "1 lb"}], default_servings=4)
        pid = tools.create_weekly_plan(mon)["weekly_plan_id"]
        tools.plan_meal(mon, recipe, slot="dinner", weekly_plan_id=pid)
        return pid
    frozen_today(household_pin(20, on=_dt.date(2026, 10, 18)))
    last = week(LAST_MON, "Beef Tacos", "ground beef")
    tools.approve_weekly_plan(last, approved_by="Maya")
    frozen_today(household_pin(10, on=_dt.date(2026, 10, 24)))
    tools.approve_weekly_plan(week(MON.isoformat(), "Chicken Stir Fry", "chicken thighs"), approved_by="Maya")
    frozen_today(household_pin(9, on=_dt.date(2026, 10, 25)))
    tools.reopen_weekly_plan(last)
    tools.approve_weekly_plan(last, approved_by="Maya")
    frozen_today(household_pin(14, on=_dt.date(2026, 10, 25)))
    thighs = next(i for i in tools.list_grocery_list("needed") if i["item"] == "chicken thighs")
    tools.mark_grocery_item(thighs["id"], "purchased")
    frozen_today(household_pin(7, 30, on=MON))
    assert [i["item"] for i in tools.list_grocery_list("bought")] == ["chicken thighs"]


# ---------- cooking takes the food off the shelf, whatever unit it came home in ----------

def test_cooking_takes_a_pound_off_twelve_ounces():
    """CATCH (red on main): a recipe's "1 lb" chicken came home as "12 oz"
    and the cook took nothing off, so next week's approval read last
    Monday's chicken as on hand and left Saturday's off the list."""
    from app.tools import inventory
    assert inventory._try_subtract_quantity("12 oz", "1 lb") == (None, True)
    assert inventory._try_subtract_quantity("1.5 lb", "8 oz") == ("1 lb", True)
    # Across units, said in the finer unit, never rounded up.
    assert inventory._try_subtract_quantity("1 cup", "1 tbsp") == ("15 tbsp", True)
    assert inventory._try_subtract_quantity("1 lb", "0.1 oz") == ("15.9 oz", True)
    # Different families are still not guessed at.
    assert inventory._try_subtract_quantity("2 cups", "1 lb") == ("2 cups", False)
