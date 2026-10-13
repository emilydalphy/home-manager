"""
An amount the household edited on a plan's grocery line survives a night
being dropped or swapped (Loop Board, decided 2026-10-12).

Found overnight 2026-10-08: "onion 3" edited to 5, then tonight's Lentil
Soup taken off — the line was recomputed from the meals left and read 2.
Emily: keep the extra as the household's own; dropping or swapping a night
removes only that night's share; an edit below what the plan needs is kept
too.
"""
from __future__ import annotations

from datetime import timedelta

from conftest import household_today

from app import tools
from app.db import get_conn


def _day(n: int) -> str:
    today = household_today()
    return (today - timedelta(days=today.weekday()) + timedelta(days=7 + n)).isoformat()


def _onions() -> list[str]:
    conn = get_conn()
    rows = conn.execute(
        "SELECT quantity FROM grocery_items WHERE LOWER(item) = 'onion' AND status = 'needed'"
    ).fetchall()
    conn.close()
    return [r["quantity"] for r in rows]


def _week() -> tuple[int, int, int, int]:
    """Lentil Soup (1 onion) Tuesday, Chili (2 onions) Thursday: "onion · 3"."""
    for name in ("Alex", "Sam"):
        tools.add_member(name)
    tools.add_recipe("Lentil Soup", ingredients=[{"item": "onion", "qty": "1"}], default_servings=2)
    tools.add_recipe("Chili", ingredients=[{"item": "onion", "qty": "2"}], default_servings=2)
    tools.add_recipe("Pasta", ingredients=[{"item": "spaghetti", "qty": "1 lb"}], default_servings=2)
    plan_id = tools.create_weekly_plan(_day(0))["weekly_plan_id"]
    soup = tools.plan_meal(_day(1), "Lentil Soup", slot="dinner", weekly_plan_id=plan_id)["entry_id"]
    chili = tools.plan_meal(_day(3), "Chili", slot="dinner", weekly_plan_id=plan_id)["entry_id"]
    tools.approve_weekly_plan(plan_id, "Alex")
    conn = get_conn()
    line_id = conn.execute("SELECT id FROM grocery_items WHERE LOWER(item) = 'onion'").fetchone()["id"]
    conn.close()
    assert _onions() == ["3"]
    return plan_id, soup, chili, line_id


def test_an_amount_edited_up_keeps_its_extra_when_a_night_is_dropped():
    plan_id, soup, _chili, line_id = _week()
    tools.update_grocery_item(line_id, quantity="5")
    tools.drop_dish_from_day(plan_id, soup)
    # Only the soup's one onion comes off; the two extra stay.
    assert _onions() == ["4"]


def test_an_amount_edited_up_keeps_its_extra_when_a_night_is_swapped():
    plan_id, _soup, _chili, line_id = _week()
    tools.update_grocery_item(line_id, quantity="5")
    tools.swap_meal_in_plan(plan_id, _day(1), "Pasta", slot="dinner")
    assert _onions() == ["4"]


def test_the_extra_is_still_there_when_every_night_has_gone():
    plan_id, soup, chili, line_id = _week()
    tools.update_grocery_item(line_id, quantity="5")
    tools.drop_dish_from_day(plan_id, soup)
    tools.drop_dish_from_day(plan_id, chili)
    assert _onions() == ["2"]


def test_an_amount_edited_down_is_kept_when_a_night_is_dropped():
    plan_id, soup, chili, line_id = _week()
    tools.update_grocery_item(line_id, quantity="1")
    tools.drop_dish_from_day(plan_id, soup)
    # The meals left want 2, but the household said 1 — not put back up.
    assert _onions() == ["1"]
    # And with no meal left wanting onions, the plan's line goes as usual.
    tools.drop_dish_from_day(plan_id, chili)
    assert _onions() == []


def test_an_unedited_line_still_follows_its_meals():
    plan_id, soup, _chili, _line_id = _week()
    tools.drop_dish_from_day(plan_id, soup)
    assert _onions() == ["2"]
