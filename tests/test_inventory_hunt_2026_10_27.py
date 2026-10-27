"""
Kitchen inventory defect hunt, 2026-10-27 (branch
overnight/hunt-inventory-1027), found walking the list -> kitchen -> cook
loop over HTTP on a throwaway database.

1. Cooking took the RECIPE's amount off the shelf, not what the night
   cooked. A cook-once-eat-twice chain bought 4 lbs of chicken for two
   nights, the cook took 2 lbs off and the reheat (rightly) nothing, so
   2 lbs of chicken that had been eaten sat in the fridge as on hand; a
   table of two cooking a recipe for four took a whole 2 lbs off for 1 lb.
2. Buying more of a thing in a sibling unit wrote "2 lbs + 12 oz" on the
   row, which nothing parses: cooking took nothing off it, "used a pound"
   in chat deleted all of it.
3. The recipe pass was handed current_inventory with every qty null (it
   read a key the inventory rows do not have).
"""
from __future__ import annotations

import datetime

from conftest import household_today

from app import agent, tools


def _next_week() -> list[str]:
    today = household_today()
    monday = today - datetime.timedelta(days=today.weekday()) + datetime.timedelta(days=7)
    return tools._week_dates(monday.isoformat())


def _kitchen(client) -> dict:
    data = client.get("/api/inventory/by-location").json()
    return {i["item"]: i["quantity"] for loc in data["locations"] for i in loc["items"]}


def _chicken_bake(default_servings: int = 4) -> None:
    tools.add_recipe(
        "Chicken Bake",
        ingredients=[{"item": "Chicken thighs", "qty": "2 lb", "category": "meat/seafood"}],
        default_servings=default_servings, prep_time_minutes=10, cook_time_minutes=30,
    )


def _tick_everything(client) -> None:
    for section in client.get("/api/grocery-list").json()["sections"]:
        for line in section["items"]:
            res = client.post(f"/api/grocery-list/{line['id']}/status", json={"status": "purchased"})
            assert res.status_code == 200, res.text


# ---------- 1. cooking takes what the night cooked ----------

def test_a_cook_once_eat_twice_chain_leaves_no_phantom_chicken(signed_in):
    client = signed_in
    for name in ("Alex", "Sam", "Rae", "Jo"):
        tools.add_member(name)
    _chicken_bake()
    dates = _next_week()
    plan_id = tools.create_weekly_plan(dates[0])["weekly_plan_id"]
    cook = tools.plan_meal(dates[0], "Chicken Bake", slot="dinner", weekly_plan_id=plan_id)
    reheat = tools.plan_meal(dates[1], "Chicken Bake", slot="dinner", weekly_plan_id=plan_id,
                             derived_from={"links_to": f"{dates[0]}:dinner"})
    tools.repair_leftover_chains(plan_id)
    assert client.post(f"/api/week/{dates[0]}/approve", json={}).status_code == 200
    _tick_everything(client)
    assert _kitchen(client) == {"Chicken thighs": "4 lbs"}, "the list bought for both nights"

    res = client.post("/api/cooker/check-meal", json={"entry_id": cook["entry_id"], "status": "done"})
    assert res.status_code == 200, res.text
    assert "Chicken thighs" not in _kitchen(client), "the batch cooked all of it"

    client.post("/api/cooker/check-meal", json={"entry_id": reheat["entry_id"], "status": "done"})
    assert "Chicken thighs" not in _kitchen(client)


def test_a_table_of_two_takes_off_its_own_share(signed_in):
    client = signed_in
    tools.add_member("Alex")
    tools.add_member("Sam")
    _chicken_bake()
    client.post("/api/inventory/update", json={"item": "Chicken thighs", "action": "set",
                                              "quantity": "3 lbs", "location": "fridge"})
    dates = _next_week()
    plan_id = tools.create_weekly_plan(dates[0])["weekly_plan_id"]
    cook = tools.plan_meal(dates[2], "Chicken Bake", slot="dinner", weekly_plan_id=plan_id)

    client.post("/api/cooker/check-meal", json={"entry_id": cook["entry_id"], "status": "done"})

    assert _kitchen(client) == {"Chicken thighs": "2 lbs"}, "two of four servings is 1 lb, not 2"


def test_a_recipe_written_for_the_table_still_takes_its_amount(signed_in):
    client = signed_in
    for name in ("Alex", "Sam", "Rae", "Jo"):
        tools.add_member(name)
    _chicken_bake()
    client.post("/api/inventory/update", json={"item": "Chicken thighs", "action": "set",
                                              "quantity": "3 lbs", "location": "fridge"})
    dates = _next_week()
    plan_id = tools.create_weekly_plan(dates[0])["weekly_plan_id"]
    cook = tools.plan_meal(dates[2], "Chicken Bake", slot="dinner", weekly_plan_id=plan_id)

    client.post("/api/cooker/check-meal", json={"entry_id": cook["entry_id"], "status": "done"})

    assert _kitchen(client) == {"Chicken thighs": "1 lb"}


# ---------- 2. a second purchase in a sibling unit adds up ----------

def test_buying_ounces_onto_pounds_adds_up_and_can_be_used(signed_in):
    client = signed_in
    client.post("/api/inventory/update", json={"item": "Chicken thighs", "action": "set",
                                              "quantity": "2 lbs", "location": "fridge"})
    added = client.post("/api/grocery-list/add", json={"item": "Chicken thighs", "quantity": "12 oz",
                                                      "category": "meat/seafood"}).json()
    client.post(f"/api/grocery-list/{added['item_id']}/status", json={"status": "purchased"})
    assert _kitchen(client) == {"Chicken thighs": "2.75 lbs"}

    client.post("/api/inventory/update", json={"item": "Chicken thighs", "action": "use", "quantity": "1 lb"})
    assert _kitchen(client) == {"Chicken thighs": "1.75 lbs"}, "using a pound is not using all of it"


def test_an_untick_still_takes_the_ounces_back_off(signed_in):
    client = signed_in
    client.post("/api/inventory/update", json={"item": "Chicken thighs", "action": "set",
                                              "quantity": "2 lbs", "location": "fridge"})
    added = client.post("/api/grocery-list/add", json={"item": "Chicken thighs", "quantity": "12 oz",
                                                      "category": "meat/seafood"}).json()
    client.post(f"/api/grocery-list/{added['item_id']}/status", json={"status": "purchased"})
    client.post(f"/api/grocery-list/{added['item_id']}/status", json={"status": "needed"})
    assert _kitchen(client) == {"Chicken thighs": "2 lbs"}


def test_an_odd_volume_total_reads_in_the_finer_unit():
    from app.tools import inventory

    assert inventory._merged_quantity("2 cups", "3 tbsp") == "35 tbsp"
    assert inventory._merged_quantity("1 kg", "250 g") == "1.25 kg"
    # No family between them: left joined, as before.
    assert inventory._merged_quantity("1 lb", "500 g") == "1 lb + 500 g"


# ---------- 3. the recipe pass sees how much is on hand ----------

def test_the_recipe_pass_is_told_the_amount_on_hand():
    tools.update_inventory("Chicken thighs", "add", quantity="2 lbs", location="fridge")
    shared = agent._shared_recipe_details_context()
    assert shared["current_inventory"] == [{"item": "Chicken thighs", "qty": "2 lbs"}]
