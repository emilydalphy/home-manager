"""
A count of PORTIONS scales with the table (defect hunt, 2026-10-29).

A recipe for two saying "2 salmon fillets", planned for a household of four
with two guests, bought 3 fillets while the Cook screen said "6": the bare
count took attendance.count_scale_factor, whose cap is for the lemon that
flavours a dish for two or for three, and Cook (scale_recipe) scaled it
per portion. A fillet, chop, breast or bun is one a plate, so it scales
like any per-portion amount; a lemon or a tomato keeps the cap.
"""
import datetime

from app import tools
from conftest import household_today, on_the_weeks_monday


def _monday() -> datetime.date:
    today = household_today()
    return today - datetime.timedelta(days=today.weekday())


pytestmark = on_the_weeks_monday(_monday())

WED = (_monday() + datetime.timedelta(days=2)).isoformat()


def _list() -> dict:
    return {i["item"]: i["quantity"] for i in tools.list_grocery_list(status="needed")}


def _approve_salmon_for_four():
    for n in ("Alex", "Sam", "Rae", "Jo"):
        tools.add_member(n)
    tools.add_recipe("Salmon and Lemon", default_servings=2, ingredients=[
        {"item": "salmon fillets", "qty": "2"},
        {"item": "lemon", "qty": "1"},
        {"item": "steak sauce", "qty": "1"},
    ])
    plan_id = tools.create_weekly_plan(_monday().isoformat())["weekly_plan_id"]
    tools.plan_meal(WED, "Salmon and Lemon", slot="dinner", weekly_plan_id=plan_id)
    tools.approve_weekly_plan(plan_id, "Alex")
    return plan_id


def _cook_qty(plan_id: int, item: str) -> str:
    view = tools.get_cooker_view(plan_id)
    meal = next(m for m in view["meals"] if m["date"] == WED)
    return next(i["qty"] for i in meal["ingredients"] if i["item"] == item)


def test_a_fillet_each_is_bought_for_everyone_at_the_table_and_cook_agrees():
    plan_id = _approve_salmon_for_four()
    assert _list()["salmon fillets"] == "4", "a recipe for two, four eating: four fillets"
    assert _cook_qty(plan_id, "salmon fillets") == "4"


def test_guests_after_approval_grow_the_fillets_to_the_table():
    plan_id = _approve_salmon_for_four()
    tools.set_guest_count(WED, "dinner", 2)
    assert _list()["salmon fillets"] == "6"
    assert _cook_qty(plan_id, "salmon fillets") == "6"


def test_a_lemon_and_a_head_noun_that_is_not_a_portion_keep_the_cap():
    _approve_salmon_for_four()
    lines = _list()
    assert lines["lemon"] == "1", "one lemon flavours the dish — the 2026-10-04 cap stands"
    assert lines["steak sauce"] == "1", "'steak' is not the head noun of steak sauce"
    from app.tools import recipes
    assert recipes.is_portion_count("Boneless chicken breasts, halved")
    assert not recipes.is_portion_count("chopped tomatoes")
