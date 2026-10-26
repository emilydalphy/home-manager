"""
A person's typed sauce or pantry staple never joins the recipes' waiting
line in an amount that can't be added to it.

Found driving the app (2026-10-24): a week's recipes put "2 cups" of
basmati rice in "Pantry staples this week", unticked. Somebody typed
"Basmati rice · 1 bag" on Shop. The add concatenated onto the waiting line
— "2 cups + 1 bag" — and made it the household's own line, so dropping or
swapping the rice dinner took nothing off: the dinner was gone and the
list still said "2 cups + 1 bag" for good. Soy sauce "2 tbsp" + a typed
"1 bottle", chicken broth "2 cups" + "1 carton", the same.

The spice rack is not this: a jar is a jar whatever is written, and the
rack's own branch settles it (one line, one jar). An amount that adds up
cleanly ("3 cups") still joins and ticks the waiting line, as before.
"""
from __future__ import annotations

import datetime

from conftest import household_today, on_the_weeks_monday

from app import tools
from app.db import get_conn

# Approval buys only from the household's today on: the seeded dinner is
# this week's Monday, so run on that Monday (as test_pantry_staples_assumed).
pytestmark = on_the_weeks_monday()


def _monday() -> str:
    today = household_today()
    return (today - datetime.timedelta(days=today.weekday())).isoformat()


def _lines(name: str) -> list[dict]:
    conn = get_conn()
    rows = conn.execute(
        "SELECT id, quantity, status, source_weekly_plan_id FROM grocery_items "
        "WHERE household_id = 1 AND item = ? AND status != 'removed' ORDER BY id",
        (name,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def _week_with(name: str, qty: str) -> tuple[int, str]:
    tools.add_recipe("Weeknight bowl", ingredients=[
        {"item": name, "qty": qty, "category": "pantry"},
        {"item": "Tofu", "qty": "1 block", "category": "produce"},
    ])
    tools.add_recipe("Plain greens", ingredients=[
        {"item": "Spinach", "qty": "1 bag", "category": "produce"},
    ])
    plan_id = tools.create_weekly_plan(_monday())["weekly_plan_id"]
    day = tools._week_dates(_monday())[0]
    tools.plan_meal(day, "Weeknight bowl", slot="dinner", weekly_plan_id=plan_id)
    tools.approve_weekly_plan(plan_id, approved_by="Emily")
    return plan_id, day


def test_a_typed_bag_of_rice_is_its_own_line_and_the_dinner_takes_its_share_away():
    plan_id, day = _week_with("Basmati rice", "2 cups")
    [waiting] = _lines("Basmati rice")
    assert waiting["status"] == "spice"

    got = tools.add_grocery_item("Basmati rice", "1 bag", category="pantry")
    assert got["quantity"] == "1 bag", "the person's amount, never '2 cups + 1 bag'"
    lines = _lines("Basmati rice")
    assert [(l["quantity"], l["status"], l["source_weekly_plan_id"]) for l in lines] == [
        ("2 cups", "spice", plan_id),   # the recipes' share, still waiting in its section
        ("1 bag", "needed", None),      # theirs, on the list
    ]

    tools.swap_meal_in_plan(plan_id, day, "Plain greens", slot="dinner")
    assert [(l["quantity"], l["status"]) for l in _lines("Basmati rice")] == [("1 bag", "needed")]


def test_a_typed_bottle_of_soy_sauce_leaves_the_recipes_share_to_its_week():
    plan_id, day = _week_with("Soy sauce", "2 tbsp")
    own = tools.add_grocery_item("Soy sauce", "1 bottle", category="pantry")
    on_list = {i["item"]: i["quantity"] for i in tools.list_grocery_list()}
    assert on_list["Soy sauce"] == "1 bottle"

    def waiting():
        return [(s["quantity"], s["ticked"]) for s in tools.list_spices_this_week()["items"]
                if s["item"] == "Soy sauce"]

    # They are buying a bottle, so "Sauces this week" (and the Before you
    # shop step that reads it) does not ask about the recipe's 2 tbsp too.
    assert waiting() == []
    assert "Soy sauce" not in [c["name"] for c in tools.before_shop_steps()["spices"]["choices"]]
    # Their own line off again: the recipe's share is asked about as before.
    tools.remove_grocery_item(own["item_id"])
    assert waiting() == [("2 tbsp", False)]
    tools.add_grocery_item("Soy sauce", "1 bottle", category="pantry")
    assert waiting() == []

    tools.swap_meal_in_plan(plan_id, day, "Plain greens", slot="dinner")
    assert [(l["quantity"], l["status"]) for l in _lines("Soy sauce")] == [("1 bottle", "needed")]


def test_an_amount_that_adds_up_still_joins_and_comes_back_off():
    plan_id, day = _week_with("Basmati rice", "2 cups")
    got = tools.add_grocery_item("Basmati rice", "3 cups", category="pantry")
    assert got["merged"] is True and got["quantity"] == "5 cups"
    tools.swap_meal_in_plan(plan_id, day, "Plain greens", slot="dinner")
    assert [(l["quantity"], l["status"]) for l in _lines("Basmati rice")] == [("3 cups", "needed")]


def test_a_spice_is_still_one_jar_on_one_line():
    _week_with("Ground cumin", "2 tsp")
    tools.add_grocery_item("Ground cumin", "50 g", category="pantry")
    assert [(l["quantity"], l["status"]) for l in _lines("Ground cumin")] == [("1 jar", "needed")]


def _soy_waiting() -> list[tuple]:
    return [(s["quantity"], s["ticked"], s["new"]) for s in tools.list_spices_this_week()["items"]
            if s["item"] == "Soy sauce"]


def test_already_have_on_their_own_line_does_not_ask_about_the_recipes_share():
    # Review, 2026-10-24: "Already have" took their bottle off and the
    # recipes' 2 tbsp came straight back with "Check you have this" — the
    # question they had just answered.
    _week_with("Soy sauce", "2 tbsp")
    own = tools.add_grocery_item("Soy sauce", "1 bottle", category="pantry")
    tools.move_grocery_item_to_inventory(own["item_id"])
    assert _soy_waiting() == []
    assert "Soy sauce" not in [c["name"] for c in tools.before_shop_steps()["spices"]["choices"]]
    assert "Soy sauce" not in {i["item"] for i in tools.list_grocery_list()}
    # Put back from the Review screen: their line is on the list again.
    tools.undo_pre_shop_drop(own["item_id"])
    assert _soy_waiting() == []
    assert {i["item"]: i["quantity"] for i in tools.list_grocery_list()}["Soy sauce"] == "1 bottle"


def test_a_tick_on_the_recipes_share_beside_their_own_line_buys_nothing_twice():
    _week_with("Soy sauce", "2 tbsp")
    tools.add_grocery_item("Soy sauce", "1 bottle", category="pantry")
    [waiting] = [l for l in _lines("Soy sauce") if l["status"] == "spice"]
    got = tools.tick_spice(waiting["id"], ticked=True)
    assert got.get("unchanged") is True and got["said"]
    assert [(l["quantity"], l["status"]) for l in _lines("Soy sauce")] == [
        ("2 tbsp", "spice"), ("1 bottle", "needed"),
    ]
