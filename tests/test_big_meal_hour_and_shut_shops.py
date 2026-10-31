"""
A hosted big meal on Today, and its shops on the days the stores are shut
(overnight hunt, holidays and hosting, 2026-10-31).

1. The hour the host gave ("on the table at 4") is that day's dinner time.
   Today said "Chili · 30 min · 6:30" and "Shop for tonight · by 6:00" — the
   household's usual clock — while the big meal's own timeline worked back
   from 4:00.
2. A big meal's shop trip never lands on a day the household said every one
   of its stores is shut. Boxing Day's fresh trip is "the day before":
   Christmas Day. With Farm Boy saved as closed on holidays, Today on the 25th
   said "The fresh shop for Boxing Day · Farm Boy's closed now — opens 8:00
   Sunday", the day after the meal.
"""
import pytest

from app import tools
from app.db import get_conn
from tests.test_big_meal import family, proposer, recipes, _holiday_tasks  # noqa: F401

PINNED = pytest.mark.today("2026-12-14T09:00:00-05:00")  # a Monday, before both


def _approved_week(start_day: int = 21) -> int:
    plan_id = tools.create_weekly_plan(f"2026-12-{start_day}")["weekly_plan_id"]
    for i in range(start_day, start_day + 7):
        tools.plan_meal(f"2026-12-{i}", "Chili", slot="dinner", weekly_plan_id=plan_id)
    tools.approve_weekly_plan(plan_id, approved_by="Emily")
    return plan_id


def _moves(client, frozen_today, day: str, hour: str = "08:00") -> list[dict]:
    frozen_today(f"{day}T{hour}:00-05:00")
    res = client.get("/api/today/moves")
    assert res.status_code == 200, res.text
    return res.json()["moves"]


def _shut_on_holidays():
    tools.add_usual_stores(["Farm Boy"])
    every_day = {d: ["08:00", "21:00"] for d in ("mon", "tue", "wed", "thu", "fri", "sat", "sun")}
    tools.set_store_hours("Farm Boy", {"days": every_day, "closed_on_holidays": True})


def _fresh_shop_days(name: str) -> list[str]:
    return [t["task_date"] for t in _holiday_tasks()
            if t["related_meal"] == "Shop" and t["description"].startswith(f"The fresh shop for {name}")]


# ---------- the host's hour is dinner time that day ----------

@PINNED
def test_today_says_the_big_meals_hour_not_the_usual_dinner_clock(family, recipes, proposer, signed_in, frozen_today):
    _approved_week()
    tools.answer_holiday("2026-12-25", "hosting", headcount=6, on_table_at="4pm")

    on_the_day = _moves(signed_in, frozen_today, "2026-12-25", "07:00")
    cook = next(m for m in on_the_day if m["kind"] == "cook")
    assert cook["detail"].endswith("· 4:00"), cook["detail"]
    shop = next(m for m in on_the_day if m["kind"] == "shop")
    # The bags are back before the cook starts: 4:00 less the chili's 30 minutes.
    assert "by 3:30" in shop["detail"], shop["detail"]

    # An ordinary night beside it keeps the household's own clock.
    the_day_after = _moves(signed_in, frozen_today, "2026-12-26")
    assert next(m for m in the_day_after if m["kind"] == "cook")["detail"].endswith("· 6:30")


@PINNED
def test_a_big_meal_with_no_hour_said_keeps_the_usual_clock(family, recipes, proposer, signed_in, frozen_today):
    _approved_week()
    tools.answer_holiday("2026-12-25", "hosting", headcount=6)
    cook = next(m for m in _moves(signed_in, frozen_today, "2026-12-25") if m["kind"] == "cook")
    assert cook["detail"].endswith("· 6:30"), cook["detail"]


# ---------- no trip on a day every store is shut ----------

@PINNED
def test_boxing_days_fresh_shop_leaves_a_shut_christmas_day(family, recipes, proposer, signed_in, frozen_today):
    _shut_on_holidays()
    _approved_week()
    tools.answer_holiday("2026-12-25", "hosting", headcount=6, on_table_at="5pm")
    tools.answer_holiday("2026-12-26", "hosting", headcount=4, on_table_at="6pm")

    assert _fresh_shop_days("Boxing Day") == ["2026-12-24"]
    assert _fresh_shop_days("Christmas Day") == ["2026-12-24"]

    christmas = _moves(signed_in, frozen_today, "2026-12-25")
    assert not any("shop" in m["title"].lower() and "Boxing" in m["title"] for m in christmas)
    # Christmas Eve holds both meals' fresh trips, and they are one trip
    # with one list: asked once, not twice.
    eve = [m for m in _moves(signed_in, frozen_today, "2026-12-24") if m["kind"] == "shop"]
    assert len(eve) == 1, [m["title"] for m in eve]


@PINNED
def test_a_household_with_no_store_hours_is_planned_as_before(family, recipes, proposer):
    _approved_week()
    tools.answer_holiday("2026-12-25", "hosting", headcount=6, on_table_at="5pm")
    tools.answer_holiday("2026-12-26", "hosting", headcount=4, on_table_at="6pm")
    # Nothing says the shop is shut, so "the day before" stands.
    assert _fresh_shop_days("Boxing Day") == ["2026-12-25"]


@PINNED
def test_one_store_still_open_keeps_the_trip(family, recipes, proposer):
    _shut_on_holidays()
    tools.add_usual_stores(["Corner Store"])  # no hours: unknown, so maybe open
    _approved_week()
    tools.answer_holiday("2026-12-26", "hosting", headcount=4, on_table_at="6pm")
    assert _fresh_shop_days("Boxing Day") == ["2026-12-25"]
