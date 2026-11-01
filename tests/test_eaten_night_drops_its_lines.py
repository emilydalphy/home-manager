"""
A night ticked eaten takes off the list the unbought lines only it wanted.

Loop Board "the cook count moves to the dish we actually ate", Emily's
call of 2026-10-31, option (b): after a night is ticked eaten its unbought
grocery lines (the curry's chicken, say) stayed on the list as still to
buy — after the curry was eaten, or after "we had tacos instead". Now a
plan line that night alone was buying for comes off with the tick; a line
another night still wants stays; and an untick puts the very row back.
"""
from __future__ import annotations

from app import tools
from conftest import household_today


def _setup():
    tools.add_member("Emily")
    tools.add_recipe("Chicken Curry", [
        {"item": "Chicken thighs", "qty": "2 lbs", "category": "meat-seafood"},
        {"item": "Onion", "qty": "1", "category": "produce"},
    ], instructions=["Cook."], default_servings=1)
    tools.add_recipe("Onion Soup", [{"item": "Onion", "qty": "3", "category": "produce"}],
                     instructions=["Simmer."], default_servings=1)
    tacos = tools.add_recipe("Beef Tacos", [{"item": "Beef mince", "qty": "1 lb", "category": "meat-seafood"}],
                             instructions=["Brown."], default_servings=1)
    return tacos["recipe_id"]


def _two_nights(second: str = "Onion Soup") -> tuple[int, int]:
    today = household_today()
    plan = tools.create_weekly_plan(today.isoformat(), day_count=2)["weekly_plan_id"]
    tonight = tools.plan_meal(today.isoformat(), "Chicken Curry", slot="dinner", weekly_plan_id=plan)["entry_id"]
    import datetime
    tomorrow = (today + datetime.timedelta(days=1)).isoformat()
    other = tools.plan_meal(tomorrow, second, slot="dinner", weekly_plan_id=plan)["entry_id"]
    tools.approve_weekly_plan(plan, approved_by="Emily", confirm_hard_conflicts=True)
    return tonight, other


def _needed() -> dict:
    return {g["item"]: g["quantity"] for g in tools.list_grocery_list(status="needed")}


def test_ticking_eaten_drops_only_the_lines_that_night_alone_wanted(signed_in):
    _setup()
    tonight, _ = _two_nights()
    assert "Chicken thighs" in _needed() and "Onion" in _needed()

    res = signed_in.post("/api/cooker/check-meal", json={"entry_id": tonight, "status": "done"})
    assert res.status_code == 200

    needed = _needed()
    assert "Chicken thighs" not in needed
    # Tomorrow's soup still wants onions: that line stays as it was.
    assert "Onion" in needed


def test_unticking_puts_the_line_back(signed_in):
    _setup()
    tonight, _ = _two_nights()
    before = _needed()
    tools.check_off_meal(tonight, "done")
    tools.check_off_meal(tonight, "pending")
    assert _needed() == before


def test_a_line_already_in_the_cart_is_left_alone(signed_in):
    _setup()
    tonight, _ = _two_nights()
    chicken = next(g for g in tools.list_grocery_list() if g["item"] == "Chicken thighs")
    tools.mark_grocery_item(chicken["id"], status="in_cart")
    tools.check_off_meal(tonight, "done")
    assert [g["status"] for g in tools.list_grocery_list(status="all") if g["item"] == "Chicken thighs"] == ["in_cart"]


def test_a_households_own_line_is_never_taken(signed_in):
    _setup()
    tools.add_grocery_item("Chicken thighs", quantity="1 lb", category="meat-seafood")
    tonight, _ = _two_nights()
    tools.check_off_meal(tonight, "done")
    assert "Chicken thighs" in _needed()


def test_we_had_tacos_instead_then_unticked_does_not_bring_the_curry_back(signed_in):
    tacos = _setup()
    tonight, _ = _two_nights()
    tools.check_off_meal(tonight, "done")
    assert tools.change_meal_recipe(tonight, tacos)["status"] == "changed"
    assert "Chicken thighs" not in _needed()
    tools.check_off_meal(tonight, "pending")
    assert "Chicken thighs" not in _needed()


def test_a_line_two_nights_shared_comes_off_when_the_last_of_them_is_eaten(signed_in):
    # The shape of a leftovers batch too: one line linked to the cook night
    # AND the reheat night. "No other meal at all" kept it on the list for
    # ever; a night already ticked done wants nothing more (review,
    # 2026-11-01).
    _setup()
    tonight, other = _two_nights()
    tools.check_off_meal(tonight, "done")
    assert "Onion" in _needed()
    tools.check_off_meal(other, "done")
    assert "Onion" not in _needed()
    # Unticking either night makes the onions wanted again.
    tools.check_off_meal(tonight, "pending")
    assert "Onion" in _needed()
