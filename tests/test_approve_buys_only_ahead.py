"""
Approving a week part-way through buys only for the nights still ahead.

Loop Board "Approving a week part-way through buys food for nights that are
already over" (High, Bug). MEASURED BEFORE THE FIX on a throwaway household:
on Friday 2026-10-16, approving a draft for Oct 12-18 with Chicken Skewers
on Monday and Tuesday and Tacos on Saturday put the chicken thighs and the
lemons for the two gone nights on the list, and the approval preview counted
them as things to buy. _plan_grocery_candidate_entries — the one query both
the preview and the approval read — had no date filter at all.

Emily, 2026-10-18: "No, don't buy for anything that didn't/won't happen."
That includes a gone cook night that was going to feed a leftovers night
still ahead: nothing is bought for it, and the leftovers night is handed
back as an open question (plan_slot_open, the mechanism every other "this
night is yours to fill" already uses) rather than left as a reheat of a
dinner nobody cooked.

"Before today" is the HOUSEHOLD's today (households.timezone), never the
server's — see the last test.

MUTATIONS RUN (red counts over this file, read off the run):
  1. the date filter removed from _plan_grocery_candidate_entries  -> 6 failed
  2. the gone-cook reheat left as a reheat (re-ask skipped)       -> 1 failed
  3. `date > today` (today's own meals dropped too)               -> 2 failed
"""
from __future__ import annotations

import datetime as _dt

import pytest

from app import tools
from app.db import get_conn
from app.tools import cooker as _cooker
from app.tools import leftovers as _leftovers
from conftest import household_pin

# A Friday, mid-morning on the household's clock (household_pin, so the
# household is on Friday whatever zone the process runs in).
pytestmark = pytest.mark.today(household_pin(10, on=_dt.date(2026, 10, 16)))

WEEK = "2026-10-12"
MON, TUE, WED, THU, FRI, SAT, SUN = tools._week_dates(WEEK)


def _recipes() -> None:
    tools.add_recipe("Chicken Skewers", ingredients=[
        {"item": "chicken thighs", "qty": "2 lbs"}, {"item": "lemons", "qty": "2"},
    ], default_servings=2, cook_time_minutes=35)
    tools.add_recipe("Tacos", ingredients=[
        {"item": "tortillas", "qty": "8"}, {"item": "ground beef", "qty": "1 lb"},
    ], default_servings=2)
    # (Merge 2026-10-19: not a pantry staple, which pantry-staples-assumed
    # leaves off the list as already at home.)
    tools.add_recipe("Pasta", ingredients=[{"item": "italian sausage", "qty": "1 lb"}], default_servings=2)


def _items() -> set[str]:
    return {g["item"].strip().lower() for g in tools.list_grocery_list()}


def _dinner(day: str) -> dict:
    conn = get_conn()
    rows = conn.execute(
        "SELECT mpe.id, mpe.slot_state, mpe.open_reason, mpe.derived_from_json, r.name AS meal "
        "FROM meal_plan_entries mpe LEFT JOIN recipes r ON r.id = mpe.recipe_id "
        "WHERE mpe.household_id = ? AND mpe.date = ? AND mpe.slot = 'dinner'",
        (tools.household_id(), day),
    ).fetchall()
    conn.close()
    assert len(rows) == 1, rows
    return dict(rows[0])


def test_approving_on_friday_buys_nothing_for_monday_and_tuesday():
    _recipes()
    plan = tools.create_weekly_plan(WEEK)["weekly_plan_id"]
    tools.plan_meal(MON, "Chicken Skewers", slot="dinner", weekly_plan_id=plan)
    tools.plan_meal(TUE, "Chicken Skewers", slot="dinner", weekly_plan_id=plan)
    tools.plan_meal(SAT, "Tacos", slot="dinner", weekly_plan_id=plan)

    preview = tools.preview_plan_grocery_impact(plan)
    result = tools.approve_weekly_plan(plan, "Alex")

    assert _items() == {"tortillas", "ground beef"}
    # The pop-up's promise and what the list got are the same number.
    assert preview["would_add_count"] == result["groceries_added_count"] == 2


def test_tonight_is_still_bought_for():
    """Strictly BEFORE today: Friday's own dinner is still ahead."""
    _recipes()
    plan = tools.create_weekly_plan(WEEK)["weekly_plan_id"]
    tools.plan_meal(THU, "Chicken Skewers", slot="dinner", weekly_plan_id=plan)
    tools.plan_meal(FRI, "Pasta", slot="dinner", weekly_plan_id=plan)

    tools.approve_weekly_plan(plan, "Alex")

    assert _items() == {"italian sausage"}


def test_a_gone_cook_night_does_not_buy_for_its_leftovers_and_the_night_is_asked():
    _recipes()
    plan = tools.create_weekly_plan(WEEK)["weekly_plan_id"]
    tools.plan_meal(WED, "Chicken Skewers", slot="dinner", weekly_plan_id=plan)
    tools.plan_meal(SAT, "Chicken Skewers", slot="dinner", weekly_plan_id=plan,
                    derived_from={"links_to": f"{WED}:dinner"})
    tools.plan_meal(SUN, "Tacos", slot="dinner", weekly_plan_id=plan)
    tools.repair_leftover_chains(plan)
    assert _dinner(SAT)["id"] in _leftovers.plan_leftover_chains(plan)["leftovers"]

    preview = tools.preview_plan_grocery_impact(plan)
    result = tools.approve_weekly_plan(plan, "Alex")

    assert _items() == {"tortillas", "ground beef"}
    assert preview["would_add_count"] == result["groceries_added_count"] == 2
    sat = _dinner(SAT)
    assert sat["slot_state"] == "open" and sat["meal"] is None
    assert "Wednesday" in sat["open_reason"] and "Chicken Skewers" in sat["open_reason"]
    audit = tools.audit_plan_slots(plan)
    assert audit["duplicated"] == []
    assert {"date": SAT, "slot": "dinner"} not in audit["missing"]


def test_a_gone_cook_night_that_was_cooked_keeps_its_leftovers_night():
    """Ticked cooked: the leftovers are real, so Saturday still reheats
    them — and still buys nothing, because a reheat never does."""
    _recipes()
    plan = tools.create_weekly_plan(WEEK)["weekly_plan_id"]
    wed = tools.plan_meal(WED, "Chicken Skewers", slot="dinner", weekly_plan_id=plan)
    tools.plan_meal(SAT, "Chicken Skewers", slot="dinner", weekly_plan_id=plan,
                    derived_from={"links_to": f"{WED}:dinner"})
    tools.repair_leftover_chains(plan)
    conn = get_conn()
    conn.execute("UPDATE meal_plan_entries SET cooked_status = 'done' WHERE id = ?", (wed["entry_id"],))
    conn.commit()
    conn.close()

    preview = tools.preview_plan_grocery_impact(plan)
    result = tools.approve_weekly_plan(plan, "Alex")

    sat = _dinner(SAT)
    assert (sat["slot_state"], sat["meal"]) == ("planned", "Chicken Skewers")
    assert sat["id"] in _leftovers.plan_leftover_chains(plan)["leftovers"]
    assert _items() == set()
    # Nor is the reheat's dish promised as a shop (review, 2026-10-19).
    assert preview["would_add_count"] == result["groceries_added_count"] == 0


def test_before_today_is_the_households_today_not_the_servers(monkeypatch):
    """The server says Friday; the household is still on Thursday evening
    (a Toronto house at 9pm, a UTC container already past midnight).
    Thursday's dinner is still ahead of THEM, so it is bought."""
    _recipes()
    plan = tools.create_weekly_plan(WEEK)["weekly_plan_id"]
    tools.plan_meal(WED, "Pasta", slot="dinner", weekly_plan_id=plan)
    tools.plan_meal(THU, "Tacos", slot="dinner", weekly_plan_id=plan)
    thursday_evening = _dt.datetime.combine(_dt.date.fromisoformat(THU), _dt.time(21, 0))
    monkeypatch.setattr(_cooker, "household_now", lambda *a, **k: thursday_evening)

    preview = tools.preview_plan_grocery_impact(plan)
    tools.approve_weekly_plan(plan, "Alex")

    assert _items() == {"tortillas", "ground beef"}
    assert preview["would_add_count"] == 2


def test_a_repeat_whose_first_day_is_gone_is_not_batched_onto_it():
    """A prep-day household batches a repeated dish onto its FIRST day at
    approval (cook_ahead.apply_prep_day_batches). When that first day has
    gone, the later one is the cook — bought for, not a reheat of Monday."""
    _recipes()
    tools.set_prep_days([{"weekday": "sunday", "minutes": 90}])
    plan = tools.create_weekly_plan(WEEK)["weekly_plan_id"]
    tools.plan_meal(THU, "Chicken Skewers", slot="dinner", weekly_plan_id=plan)
    tools.plan_meal(SAT, "Chicken Skewers", slot="dinner", weekly_plan_id=plan)

    tools.approve_weekly_plan(plan, "Alex")

    sat = _dinner(SAT)
    assert (sat["slot_state"], sat["meal"]) == ("planned", "Chicken Skewers")
    assert sat["id"] not in _leftovers.plan_leftover_chains(plan)["leftovers"]
    assert {g["item"]: g["quantity"] for g in tools.list_grocery_list()}["chicken thighs"] == "2 lbs"


def test_dropping_the_reheat_of_an_eaten_cook_night_buys_nothing_for_it():
    """Review, 2026-10-19 (A). Thursday's skewers were cooked, Saturday
    reheats them. Taking Saturday away rescales the cook night's batch —
    and that rescale used to buy Thursday's chicken, for a dinner eaten."""
    _recipes()
    plan = tools.create_weekly_plan(WEEK)["weekly_plan_id"]
    thu = tools.plan_meal(THU, "Chicken Skewers", slot="dinner", weekly_plan_id=plan)
    sat = tools.plan_meal(SAT, "Chicken Skewers", slot="dinner", weekly_plan_id=plan,
                          derived_from={"links_to": f"{THU}:dinner"})
    tools.plan_meal(SUN, "Tacos", slot="dinner", weekly_plan_id=plan)
    tools.repair_leftover_chains(plan)
    conn = get_conn()
    conn.execute("UPDATE meal_plan_entries SET cooked_status = 'done' WHERE id = ?", (thu["entry_id"],))
    conn.commit()
    conn.close()
    tools.approve_weekly_plan(plan, "Alex")

    tools.drop_dish_from_day(plan, sat["entry_id"])

    assert _items() == {"tortillas", "ground beef"}


def test_a_rescale_does_not_buy_a_gone_night_approval_skipped():
    """Review, 2026-10-19 (B). Tuesday's skewers went by unbought; Saturday
    cooks them again, double, for Sunday. Dropping Sunday rescales the
    recipe's nights — Saturday's batch only, not Tuesday's too."""
    _recipes()
    plan = tools.create_weekly_plan(WEEK)["weekly_plan_id"]
    tools.plan_meal(TUE, "Chicken Skewers", slot="dinner", weekly_plan_id=plan)
    tools.plan_meal(SAT, "Chicken Skewers", slot="dinner", weekly_plan_id=plan)
    sun = tools.plan_meal(SUN, "Chicken Skewers", slot="dinner", weekly_plan_id=plan,
                          derived_from={"links_to": f"{SAT}:dinner"})
    tools.repair_leftover_chains(plan)
    tools.approve_weekly_plan(plan, "Alex")

    tools.drop_dish_from_day(plan, sun["entry_id"])

    assert {g["item"]: g["quantity"] for g in tools.list_grocery_list()}["chicken thighs"] == "2 lbs"
