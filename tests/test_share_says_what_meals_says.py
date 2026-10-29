"""
The share link says what the Meals card says for the same night (defect
hunt, 2026-10-29).

Seeded a real approved week and read /share/<token> beside /api/week-menu:
a leftovers night read "Beef Chili" (as if cooked again) where Meals says
"Leftovers — Monday's Beef Chili", and a day's second snack never reached
the page, because the menu's `snack` key holds only the first.

An out night is the one deliberate difference: Meals says "Out — nothing to
cook", the public link says nothing, because when the house is empty stays
off it (2026-10-11).
"""
from __future__ import annotations

import datetime

from conftest import household_today

from app import households, tools
from app.tools._shared import use_household

OUT = "Out — nothing to cook"


def _monday() -> datetime.date:
    today = household_today()
    return today - datetime.timedelta(days=today.weekday())


def _week(made_ahead: bool = False) -> tuple[int, list[str]]:
    start = _monday()
    days = [(start + datetime.timedelta(days=i)).isoformat() for i in range(7)]
    for dish in ("Beef Chili", "Lemon Salmon", "Apple Slices", "Hummus Plate"):
        tools.add_recipe(dish, [{"item": "Salt", "qty": "1 tsp"}])
    pid = tools.create_weekly_plan(start.isoformat())["weekly_plan_id"]
    source = {"make_double_for": [f"{days[1]}:dinner"]}
    target = {"links_to": f"{days[0]}:dinner"}
    if made_ahead:
        target["cook_ahead"] = True
    tools.plan_meal(days[0], "Beef Chili", weekly_plan_id=pid, derived_from=source)
    tools.plan_meal(days[1], "Beef Chili", weekly_plan_id=pid, derived_from=target)
    tools.plan_slot_empty(pid, days[2], "dinner", "You’re out — I’ve planned nothing and bought nothing.",
                          derived_from={"tags": ["out"], "constraint": "nobody_home"})
    # A day left out of the week is "Not planned" on Meals, not an out night.
    tools.plan_slot_empty(pid, days[3], "dinner", "Not planned.", derived_from={"constraint": "skipped_day"})
    for d in days[4:]:
        tools.plan_meal(d, "Lemon Salmon", weekly_plan_id=pid)
    tools.plan_meal(days[0], "Apple Slices", slot="snack", weekly_plan_id=pid)
    tools.plan_meal(days[0], "Hummus Plate", slot="snack", weekly_plan_id=pid)
    tools.approve_weekly_plan(pid, approved_by="Emily", confirm_hard_conflicts=True)
    return pid, days


def _share_menu(client) -> dict:
    token = tools.get_or_create_share_link()["token"]
    client.cookies.clear()
    plan = client.get(f"/api/share/{token}").json()
    return {day["date"]: day for day in plan["menu"]}


def test_share_dinners_read_as_the_meals_card_does(signed_in):
    _, days = _week()
    meals = {d["date"]: (d.get("dinner") or {}).get("title")
             for d in signed_in.get("/api/week-menu").json()["days"]}
    shared = _share_menu(signed_in)
    assert shared[days[1]]["dinner"] == "Leftovers — Monday’s Beef Chili" == meals[days[1]]
    assert shared[days[0]]["dinner"] == "Beef Chili" == meals[days[0]]


def test_an_out_night_does_not_say_the_house_is_empty(client):
    _, days = _week()
    token = tools.get_or_create_share_link()["token"]
    client.cookies.clear()
    plan = client.get(f"/api/share/{token}").json()
    shared = {day["date"]: day for day in plan["menu"]}
    assert shared[days[2]]["dinner"] is None and shared[days[3]]["dinner"] is None
    assert OUT not in str(plan) and "nobody" not in str(plan).lower()


def test_every_snack_of_the_day_is_shared(client):
    _, days = _week()
    assert _share_menu(client)[days[0]]["snacks"] == ["Apple Slices", "Hummus Plate"]


def test_a_made_ahead_night_says_made_ahead(client):
    _, days = _week(made_ahead=True)
    assert _share_menu(client)[days[1]]["dinner"] == "Made ahead — Monday’s Beef Chili"


def test_a_second_household_link_reads_its_own_week(client):
    _week()
    beta = households.create_household("The Beta Testers", "beta-pass")
    with use_household(beta):
        start = _monday()
        tools.add_recipe("Veggie Curry", [{"item": "Rice", "qty": "1 cup"}])
        pid = tools.create_weekly_plan(start.isoformat())["weekly_plan_id"]
        for i in range(7):
            tools.plan_meal((start + datetime.timedelta(days=i)).isoformat(), "Veggie Curry", weekly_plan_id=pid)
        tools.approve_weekly_plan(pid, approved_by="Beta", confirm_hard_conflicts=True)
        token = tools.get_or_create_share_link()["token"]
    client.cookies.clear()
    plan = client.get(f"/api/share/{token}").json()
    assert plan["household_name"] == "The Beta Testers"
    text = str(plan)
    assert "Beef Chili" not in text and OUT not in text and "Leftovers" not in text
    assert {d["dinner"] for d in plan["menu"]} == {"Veggie Curry"}


def test_the_page_draws_every_snack():
    html = open("static/share.html", encoding="utf-8").read()
    assert "day.snacks" in html
