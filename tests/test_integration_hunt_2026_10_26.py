"""
Two seams between branches merged together on 2026-10-25, found by living
a household's two weeks end to end over HTTP (integration hunt, 2026-10-26:
sign up, onboard, plan, edit the draft, approve, shop, cook, plan the next
week on the Sunday, re-plan it on the Monday).

1. "Yes, freezing it" at the shop vs. bring-over's use-by check. The Shop
   checklist's freezing answer books the fridge move and nothing else; the
   kitchen row stays in the fridge on a fridge use-by. Bring-over (2026-10-25)
   re-buys an ingredient whose every kitchen row is past its use-by unless
   the row is in the freezer — so a traybake skipped on Wednesday and brought
   over to next Monday put the chicken the household had frozen back on the
   list.

2. Next week approved on the Sunday vs. this week's waiting pantry line.
   A plan's amount never joins another plan's line — except a spice-rack
   line, one jar whatever the week. The 2026-10-19 pantry work widened that
   exception to every sauce and pantry staple, which carry real amounts: next
   week's lasagna noodles summed onto this week's unanswered "2 boxes",
   stamped next week's id on the lot, and Monday's sweep of the ended week
   no longer saw them — "3 boxes" for one lasagna, for good.

Each test says CATCH (red on main d0ff5d93) or GUARD.
"""
from __future__ import annotations

from app import tools
from app.db import get_conn
from app.tools import bring_over

WEEK = "2026-11-02"       # a Monday
WED = "2026-11-04"
SAT = "2026-11-07"
NEXT = "2026-11-09"       # the Monday after
NEXT_SAT = "2026-11-14"


def _approved_week(start: str, dish: str, day: str, ingredients: list[dict]) -> tuple[int, int]:
    plan = tools.create_weekly_plan(start)["weekly_plan_id"]
    if not any(r["name"] == dish for r in tools.list_recipes()):
        tools.add_recipe(dish, ingredients=ingredients, prep_time_minutes=10,
                         cook_time_minutes=30, default_servings=4)
    entry = tools.plan_meal(day, dish, slot="dinner", weekly_plan_id=plan)["entry_id"]
    tools.approve_weekly_plan(plan)
    return plan, entry


def _lines(item: str) -> list[dict]:
    conn = get_conn()
    rows = conn.execute(
        "SELECT id, quantity, status, source_weekly_plan_id FROM grocery_items "
        "WHERE household_id = 1 AND lower(item) = ? ORDER BY id", (item,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ==========================================================================
# 1. Frozen at the shop is not past its use-by
# ==========================================================================

def _frozen_at_the_shop(frozen_today) -> int:
    """Monday's shop for Wednesday's traybake, the chicken answered
    "Yes, freezing it". Returns the traybake's entry."""
    frozen_today(f"{WEEK} 09:00:00")
    _plan, entry = _approved_week(WEEK, "Chicken Traybake", WED, [
        {"item": "Chicken thighs", "qty": "2 lb", "category": "meat/seafood"},
        {"item": "Carrots", "qty": "1 lb", "category": "produce"},
    ])
    conn = get_conn()
    line_ids = [r[0] for r in conn.execute(
        "SELECT DISTINCT grocery_item_id FROM meal_plan_grocery_links WHERE meal_plan_entry_id = ?", (entry,))]
    conn.close()
    for line_id in line_ids:
        tools.mark_grocery_item(line_id, "purchased")
    [chicken] = [line for line in _lines("chicken thighs")]
    booked = tools.book_defrost_for_grocery_line(chicken["id"], freezing=True)
    assert booked["freezing"] and booked["related_meal"] == "Chicken Traybake", booked
    return entry


def test_meat_frozen_at_the_shop_is_not_bought_again_for_the_brought_over_dinner(frozen_today):
    """CATCH. Wednesday's traybake never happened; the Sunday after, it is
    brought over. The chicken went in the freezer at the shop (its fridge
    move still waiting), so it stays bought."""
    entry = _frozen_at_the_shop(frozen_today)
    frozen_today("2026-11-04 09:00:00")
    assert "chicken thighs" in bring_over.bought_items([entry])


def test_a_fridge_move_left_unticked_for_days_is_not_trusted(frozen_today):
    """GUARD (review, 2026-10-26). The move was dated Monday and never
    ticked; six days on it may well have been done untold, and chicken
    thawed that long is bought again (the use-by rule), not counted frozen.
    Two days on, the move is still read as the food being frozen."""
    entry = _frozen_at_the_shop(frozen_today)
    frozen_today("2026-11-04 09:00:00")  # the move's date + 2
    assert "chicken thighs" in bring_over.bought_items([entry])
    frozen_today("2026-11-08 09:00:00")  # the move's date + 6
    assert "chicken thighs" not in bring_over.bought_items([entry])


def test_meat_thawed_in_the_fridge_last_week_is_bought_again(frozen_today):
    """GUARD. The fridge move was ticked done on Monday night: the chicken
    has been in the fridge since, past its use-by, so it is bought again —
    the 2026-10-25 rule, untouched."""
    entry = _frozen_at_the_shop(frozen_today)
    conn = get_conn()
    conn.execute("UPDATE prep_tasks SET status = 'done' WHERE task_type = 'defrost' AND meal_plan_entry_id = ?",
                 (entry,))
    conn.commit()
    conn.close()
    frozen_today("2026-11-08 09:00:00")
    assert "chicken thighs" not in bring_over.bought_items([entry])


# ==========================================================================
# 2. Next week's pantry staple keeps to its own week
# ==========================================================================

NOODLES = [{"item": "Lasagna noodles", "qty": "2 boxes", "category": "pantry"},
           {"item": "Ricotta", "qty": "15 oz", "category": "dairy"}]


def test_next_weeks_pantry_staple_does_not_join_this_weeks_waiting_line(frozen_today):
    """CATCH. This week's lasagna left its noodles unanswered in "Pantry
    staples this week"; next week is approved on the Sunday with another
    lasagna. Each week's noodles stand on their own line, and on Monday only
    next week's are left — never this week's and next week's summed."""
    frozen_today(f"{WEEK} 09:00:00")
    plan_a, _ = _approved_week(WEEK, "Big Lasagna", SAT, NOODLES)
    [mine] = _lines("lasagna noodles")
    assert mine["status"] == "spice" and mine["source_weekly_plan_id"] == plan_a, "premise: a waiting pantry line"
    this_weeks = mine["quantity"]

    frozen_today("2026-11-08 10:00:00")  # the Sunday
    plan_b, _ = _approved_week(NEXT, "Big Lasagna", NEXT_SAT, NOODLES)
    by_plan = {line["source_weekly_plan_id"]: line["quantity"] for line in _lines("lasagna noodles")}
    assert by_plan == {plan_a: this_weeks, plan_b: this_weeks}

    frozen_today(f"{NEXT} 08:00:00")  # Monday: last week has ended
    tools.set_aside_ended_weeks()
    assert [(line["source_weekly_plan_id"], line["quantity"]) for line in _lines("lasagna noodles")] == [
        (plan_b, this_weeks)]


def test_a_spice_rack_jar_is_still_one_line_across_two_weeks(frozen_today):
    """GUARD. The rack is one jar per name, whichever week asks — the
    exception the pantry staples were taken back out of."""
    cumin = [{"item": "Cumin", "qty": "1 tsp", "category": "pantry"},
             {"item": "Black beans", "qty": "1 can", "category": "pantry"}]
    frozen_today(f"{WEEK} 09:00:00")
    _approved_week(WEEK, "Bean Tacos", SAT, cumin)
    frozen_today("2026-11-08 10:00:00")
    _approved_week(NEXT, "Bean Tacos", NEXT_SAT, cumin)
    assert len(_lines("cumin")) == 1
