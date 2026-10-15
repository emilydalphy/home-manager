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


# ---- Review, 2026-10-13: the edit is recorded beside a line that stays
# the plan's (grocery._household_edit_columns). The first cut made an
# edited-up line a standing want, and an edit down was only capped on a
# removal — both drifted.

def _entry(plan_id: int, day: str) -> int:
    conn = get_conn()
    row = conn.execute(
        "SELECT id FROM meal_plan_entries WHERE weekly_plan_id = ? AND date = ? "
        "AND slot = 'dinner' AND slot_state = 'planned'",
        (plan_id, day),
    ).fetchone()
    conn.close()
    return row["id"]


def test_an_edited_up_line_is_asked_about_next_week_not_soaked_into_it(frozen_today):
    """Onion 3 -> 5, unbought; next week's approval wants 2. It used to
    read "7" (then "9" the week after), owned by no plan and never offered
    as a leftover. Now last week's 5 waits for Keep / Don't need, and this
    week's line is this week's 2."""
    _plan_id, _soup, _chili, line_id = _week()
    tools.update_grocery_item(line_id, quantity="5")
    frozen_today(_day(8) + "T12:00:00")
    plan2 = tools.create_weekly_plan(_day(7))["weekly_plan_id"]
    tools.plan_meal(_day(9), "Chili", slot="dinner", weekly_plan_id=plan2)
    tools.approve_weekly_plan(plan2, "Alex")
    assert _onions() == ["2"]
    assert [(c["item"], c["quantity"]) for c in tools.list_carried_over_items()] == [("onion", "5")]


def test_an_edit_down_survives_drop_replan_and_swap_cycles():
    """Onion 3 -> 1 (two at home). A drop and re-plan, a swap away and
    back, and a like-for-like swap all leave the household's 1 — it used
    to climb 1 -> 2 -> 3 as each meal's share was summed back on."""
    plan_id, _soup, _chili, line_id = _week()
    tools.update_grocery_item(line_id, quantity="1")
    for _ in range(2):
        tools.drop_dish_from_day(plan_id, _entry(plan_id, _day(1)))
        assert _onions() == ["1"]
        tools.swap_meal_in_plan(plan_id, _day(1), "Lentil Soup", slot="dinner")
        assert _onions() == ["1"]
    tools.swap_meal_in_plan(plan_id, _day(1), "Pasta", slot="dinner")
    tools.swap_meal_in_plan(plan_id, _day(1), "Lentil Soup", slot="dinner")
    assert _onions() == ["1"]
    tools.add_recipe("Onion Tart", ingredients=[{"item": "onion", "qty": "1"}], default_servings=2)
    tools.swap_meal_in_plan(plan_id, _day(1), "Onion Tart", slot="dinner")
    assert _onions() == ["1"]


def test_an_edit_down_still_buys_a_new_meals_share():
    """The household's edit spoke for what was planned: a dinner added
    later wanting two more onions adds them (3 planned - 2 at home + 2)."""
    plan_id, _soup, _chili, line_id = _week()
    tools.update_grocery_item(line_id, quantity="1")
    tools.add_recipe("French Onion Soup", ingredients=[{"item": "onion", "qty": "2"}], default_servings=2)
    tools.plan_meal(_day(5), "French Onion Soup", slot="dinner", weekly_plan_id=plan_id,
                    add_ingredients_to_grocery_list=True)
    assert _onions() == ["3"]


# ---- Re-review, 2026-10-13: an edit meeting last week's "Keep". The edit
# speaks for the meals' part of the line; the kept amount rides on top of
# it, and undoing the Keep takes only the Keep back off.

def _week_two_with_a_carried_onion(frozen_today, *dishes):
    """Last week's "onion 3" left unbought; this week plans `dishes` (day,
    recipe) and is approved, so last week's 3 waits for Keep / Don't need.
    Returns (this week's plan, this week's onion line, the carried row)."""
    _week()
    frozen_today(_day(7) + "T08:00:00")
    plan2 = tools.create_weekly_plan(_day(7))["weekly_plan_id"]
    for day, dish in dishes:
        tools.plan_meal(_day(day), dish, slot="dinner", weekly_plan_id=plan2)
    tools.approve_weekly_plan(plan2, "Alex")
    conn = get_conn()
    line = conn.execute(
        "SELECT id FROM grocery_items WHERE LOWER(item) = 'onion' AND status = 'needed'"
    ).fetchone()["id"]
    carried = conn.execute(
        "SELECT id FROM grocery_items WHERE LOWER(item) = 'onion' AND status = 'carried'"
    ).fetchone()["id"]
    conn.close()
    return plan2, line, carried


def test_an_edit_down_then_a_keep_survives_a_dropped_night(frozen_today):
    """3 for this week's soup and chili, edited to 1 (two at home), then
    last week's 3 kept: 4. Chili taken off: the soup's 1 under the
    household's edit, plus the kept 3 — still 4. It read 2, the edit
    applied to a total that already held the kept amount."""
    plan2, line, carried = _week_two_with_a_carried_onion(
        frozen_today, (8, "Lentil Soup"), (10, "Chili"))
    assert _onions() == ["3"]
    tools.update_grocery_item(line, quantity="1")
    tools.keep_carried_over_item(carried)
    assert _onions() == ["4"]
    tools.drop_dish_from_day(plan2, _entry(plan2, _day(10)))
    assert _onions() == ["4"]


def test_undoing_a_keep_after_an_edit_leaves_the_households_number(frozen_today):
    """Chili's 2 plus last week's kept 3 is 5; edited to 1; the Keep
    undone. The line went blank with the chili still planned — the edit had
    folded the kept amount away and the undo subtracted it from the 1. The
    household's 1 stands."""
    _plan2, line, carried = _week_two_with_a_carried_onion(frozen_today, (10, "Chili"))
    tools.keep_carried_over_item(carried)
    assert _onions() == ["5"]
    tools.update_grocery_item(line, quantity="1")
    tools.undo_carried_over_decision(carried)
    assert _onions() == ["1"]


def test_an_edit_to_nothing_is_remembered_through_a_drop_and_replan():
    plan_id, _soup, _chili, line_id = _week()
    tools.update_grocery_item(line_id, quantity="0")
    tools.drop_dish_from_day(plan_id, _entry(plan_id, _day(1)))
    tools.swap_meal_in_plan(plan_id, _day(1), "Lentil Soup", slot="dinner")
    assert _onions() == ["0"]


def test_an_unreadable_edit_clears_the_recorded_edit():
    _plan_id, _soup, _chili, line_id = _week()
    tools.update_grocery_item(line_id, quantity="1")
    tools.update_grocery_item(line_id, quantity="a few")
    conn = get_conn()
    row = conn.execute(
        "SELECT kept_amount, short_amount, edited_to FROM grocery_items WHERE id = ?", (line_id,)
    ).fetchone()
    conn.close()
    assert tuple(row) == ("", "", "")
