"""
Monday morning right after the week turns over (overnight defect hunt,
2026-10-26). A two-adult household with a kid, last week approved and
partly cooked and shopped, this week approved Sunday night — walked at
390px through Today, Plan, Shop and Cook at 07:30, 12:00 and 18:00, and the
morning text rendered. Each test here is one thing that walk found wrong:

  * the Pantry staples / Sauces sections totalled lentils and broth as if
    they were spices ("96 tbsp", "about ½ tsp");
  * the morning text's "things to use this week" counted meat days past
    its date;
  * a tin got a fresh tomato's shelf life;
  * with no new week approved, Shop drew last week's ticks as this list's;
  * cooking a "1 lb" recipe took nothing off a "12 oz" shelf row.
"""
from __future__ import annotations

import datetime as _dt

import pytest

from app import tools
from app.db import get_conn
from app.tools import notifications, quantities
from conftest import household_pin

MON = _dt.date(2026, 10, 26)
LAST_MON = "2026-10-19"


# ---------- Shop: pantry staples and sauces are not spices ----------

@pytest.mark.today(household_pin(7, 30, on=MON))
def test_pantry_staples_and_sauces_show_their_own_amount_not_teaspoons():
    """CATCH (red on main): "red lentils 96 tbsp", "vegetable broth about
    ½ tsp", "salsa about ¼ tsp". The rack still totals in teaspoons."""
    tools.add_recipe("Lentil Soup", ingredients=[
        {"item": "red lentils", "qty": "2 cups"}, {"item": "vegetable broth", "qty": "1 carton"},
        {"item": "salsa", "qty": "1 jar"}, {"item": "ground cumin", "qty": "1 tsp"},
        {"item": "carrots", "qty": "3"},
    ], default_servings=4)
    plan_id = tools.create_weekly_plan(MON.isoformat())["weekly_plan_id"]
    tools.plan_meal(MON.isoformat(), "Lentil Soup", slot="dinner", weekly_plan_id=plan_id)
    tools.approve_weekly_plan(plan_id, approved_by="Maya")
    rows = {sp["item"].lower(): sp for sp in tools.list_spices_this_week()["items"]}
    for name in ("red lentils", "vegetable broth", "salsa"):
        assert rows[name]["section"] != "spices"
        assert rows[name]["total"] == "", rows[name]
    assert "cup" in rows["red lentils"]["quantity"]
    assert rows["ground cumin"]["total"].endswith("tsp"), rows["ground cumin"]


# ---------- the morning text: gone off is not "to use" ----------

def _stock(item: str, category: str, expires: str) -> None:
    conn = get_conn()
    conn.execute(
        "INSERT INTO inventory_items (household_id, item, quantity, category, location, expiration_date) "
        "VALUES (1, ?, '1', ?, 'fridge', ?)", (item, category, expires),
    )
    conn.commit()
    conn.close()


@pytest.mark.today(household_pin(7, 30, on=MON))
def test_food_past_its_date_is_not_offered_as_something_to_use_this_week():
    """CATCH (red on main): "3 things to use this week" counted chicken five
    days past its date. Something turning soon is still named."""
    _stock("chicken breast", "meat/seafood", "2026-10-21")
    _stock("ground beef", "meat/seafood", "2026-10-21")
    _stock("milk", "dairy", "2026-10-27")
    use = [n for n in notifications.get_active_notifications() if n["type"] == "expiring_soon"]
    assert [n["title"] for n in use] == ["Your milk turns soon"]


def test_a_tin_keeps_like_a_tin_whatever_is_in_it():
    """CATCH (red on main): canned diced tomatoes matched "tomato" (7 days)."""
    assert quantities._lookup_item_shelf_life_days("diced tomatoes", "canned") == 365
    assert quantities._lookup_item_shelf_life_days("coconut milk", "canned") == 365
    assert quantities._lookup_item_shelf_life_days("tomatoes", "produce") == 7


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


# ---------- cooking takes the food off the shelf, whatever unit it came home in ----------

def test_cooking_takes_a_pound_off_twelve_ounces():
    """CATCH (red on main): a recipe's "1 lb" chicken came home as "12 oz"
    and the cook took nothing off, so next week's approval read last
    Monday's chicken as on hand and left Saturday's off the list."""
    from app.tools import inventory
    assert inventory._try_subtract_quantity("12 oz", "1 lb") == (None, True)
    assert inventory._try_subtract_quantity("1.5 lb", "8 oz") == ("1 lb", True)
    # Different families are still not guessed at.
    assert inventory._try_subtract_quantity("2 cups", "1 lb") == ("2 cups", False)
