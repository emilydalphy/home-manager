"""
Food in the freezer is not on a fridge clock (defect hunt, 2026-10-22).

Every estimated expiry is a fridge/pantry shelf life, so chicken thighs
told to the app as "in the freezer" were dated two days out. From then on
the morning text — whose "kitchen" part is on by default — said "Your
chicken thighs turns soon." every day, in the same text that said "Move
the Chicken Thighs to the fridge — for Friday's Chicken Skewers."
inventory.get_expiring_soon now leaves freezer rows out; the bell and the
morning text both read it.
"""
from __future__ import annotations

import datetime as dt
from datetime import datetime

from app import tools
from app.db import get_conn
from app.tools import defrost, digest
from conftest import household_today

TODAY = household_today()


def _iso(n: int) -> str:
    return (TODAY + dt.timedelta(days=n)).isoformat()


def _week_with_a_thaw() -> None:
    for n in ("Emily", "Vineeth"):
        tools.add_member(n)
        tools.set_member_age_group(n, "adult")
    tools.add_recipe("Chicken Skewers", ingredients=[{"item": "Chicken Thighs", "qty": "1 lb"}],
                     prep_time_minutes=10, cook_time_minutes=15, default_servings=2)
    tools.add_recipe("Pasta", ingredients=[{"item": "Penne", "qty": "1 lb"}],
                     prep_time_minutes=5, cook_time_minutes=15, default_servings=2)
    tools.update_inventory("Chicken Thighs", "add", quantity="1 lb", category="meat/seafood", location="freezer")
    plan_id = tools.create_weekly_plan(_iso(-1))["weekly_plan_id"]
    tools.plan_meal(_iso(0), "Pasta", slot="dinner", weekly_plan_id=plan_id, add_ingredients_to_grocery_list=False)
    tools.plan_meal(_iso(2), "Chicken Skewers", slot="dinner", weekly_plan_id=plan_id,
                    add_ingredients_to_grocery_list=False)
    defrost.sync_defrost_tasks(plan_id)


def test_the_morning_text_moves_frozen_chicken_and_never_says_it_turns():
    """CATCH: red before the fix, with "Your chicken thighs turns soon." last."""
    _week_with_a_thaw()
    text = digest.build_morning_text(datetime.combine(TODAY, dt.time(7)), link=False)
    assert "Move the Chicken Thighs to the fridge" in text
    assert "turns soon" not in text and "turn soon" not in text
    assert all(n["type"] != "expiring_soon" for n in tools.get_active_notifications())


def test_the_same_food_in_the_fridge_still_turns_soon():
    """The fridge clock is untouched: a pack in the fridge is still said."""
    tools.update_inventory("Chicken Thighs", "add", quantity="1 lb", category="meat/seafood", location="fridge")
    tools.update_inventory("Shrimp", "add", quantity="1 lb", category="meat/seafood", location="freezer")
    assert [e["item"] for e in tools.get_expiring_soon(days=2)] == ["Chicken Thighs"]
    bell = next(n for n in tools.get_active_notifications() if n["type"] == "expiring_soon")
    assert "chicken thighs" in bell["title"] and "shrimp" not in bell["title"]


def test_a_row_with_a_blank_location_counts_as_frozen_by_its_category():
    tools.update_inventory("Peas", "add", quantity="1 bag", category="frozen", expiration_date=_iso(1))
    conn = get_conn()
    conn.execute("UPDATE inventory_items SET location = '' WHERE item = 'Peas'")
    conn.commit()
    conn.close()
    assert tools.get_expiring_soon(days=2) == []
