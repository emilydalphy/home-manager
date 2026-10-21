"""
Reopening the week keeps this week's ticks on Shop (2026-10-21, defect hunt).

Shop draws its ticked rows from /api/grocery-list?status=bought: the lines
bought since the list was last BUILT (grocery.list_built_at). That used to
be the newest weekly_plans.approved_at — which a reopen clears and the
re-approval re-stamps. So: approve Monday, tick the beef on Tuesday, reopen
on Wednesday to add Friday's salad, approve again, and the beef was gone
from the checklist (under a toast that says "Nothing has come off your
shopping list"); while the week sat reopened, LAST week's ticks were drawn
in its place. The window is now a plan's first approval
(weekly_plans.list_built_at), which a reopen never clears.
"""
from __future__ import annotations

import datetime as dt

from app import tools
from conftest import household_pin, household_today


def _names(client, status: str) -> list[str]:
    body = client.get(f"/api/grocery-list?status={status}").json()
    return sorted(it["item"] for s in body["sections"] for it in s["items"])


def _ids(client) -> dict[str, int]:
    body = client.get("/api/grocery-list").json()
    return {it["item"]: it["id"] for s in body["sections"] for it in s["items"]}


def _recipes():
    tools.add_recipe("Tacos", ingredients=[
        {"item": "Ground beef", "qty": "1 lb", "category": "meat/seafood"},
        {"item": "Cheddar cheese", "qty": "8 oz", "category": "dairy"},
    ])
    tools.add_recipe("Salad", ingredients=[{"item": "Lettuce", "qty": "1 head", "category": "produce"}])


def _week(client, monday: dt.date, meal: str = "Tacos", day: int = 2) -> int:
    plan = tools.create_weekly_plan(monday.isoformat())["weekly_plan_id"]
    tools.plan_meal(tools._week_dates(monday.isoformat())[day], meal, slot="dinner", weekly_plan_id=plan)
    assert client.post(f"/api/week/{monday}/approve", json={"approved_by": "Emily"}).status_code == 200
    return plan


def _this_monday() -> dt.date:
    today = household_today()
    return today - dt.timedelta(days=today.weekday())


def test_a_tick_survives_reopen_and_reapprove(signed_in, frozen_today):
    _recipes()
    monday = _this_monday()
    frozen_today(household_pin(9, on=monday))
    plan = _week(signed_in, monday)
    frozen_today(household_pin(17, on=monday + dt.timedelta(days=1)))
    beef = _ids(signed_in)["Ground beef"]
    signed_in.post(f"/api/grocery-list/{beef}/status", json={"status": "purchased"})
    assert _names(signed_in, "bought") == ["Ground beef"]

    frozen_today(household_pin(9, on=monday + dt.timedelta(days=2)))
    assert signed_in.post(f"/api/week/{monday}/reopen").status_code == 200
    tools.plan_meal(tools._week_dates(monday.isoformat())[4], "Salad", slot="dinner", weekly_plan_id=plan)
    res = signed_in.post(f"/api/week/{monday}/approve", json={"approved_by": "Emily"}).json()
    assert res["was_already_approved"] is False and res["groceries_added"] == 1

    assert _names(signed_in, "bought") == ["Ground beef"], "Tuesday's tick is still this list's"
    assert _names(signed_in, "needed") == ["Cheddar cheese", "Lettuce"]


def test_while_reopened_last_weeks_ticks_do_not_come_back(signed_in, frozen_today):
    _recipes()
    monday = _this_monday()
    last_monday = monday - dt.timedelta(days=7)
    frozen_today(household_pin(9, on=last_monday))
    _week(signed_in, last_monday, meal="Salad")
    frozen_today(household_pin(17, on=last_monday + dt.timedelta(days=1)))
    signed_in.post(f"/api/grocery-list/{_ids(signed_in)['Lettuce']}/status", json={"status": "purchased"})

    frozen_today(household_pin(9, on=monday))
    _week(signed_in, monday)
    frozen_today(household_pin(17, on=monday + dt.timedelta(days=1)))
    signed_in.post(f"/api/grocery-list/{_ids(signed_in)['Ground beef']}/status", json={"status": "purchased"})
    assert signed_in.post(f"/api/week/{monday}/reopen").status_code == 200

    assert _names(signed_in, "bought") == ["Ground beef"], "last week's lettuce is not this list's"


def test_the_next_weeks_approval_still_starts_a_new_list(signed_in, frozen_today):
    _recipes()
    monday = _this_monday()
    last_monday = monday - dt.timedelta(days=7)
    frozen_today(household_pin(9, on=last_monday))
    _week(signed_in, last_monday)
    frozen_today(household_pin(17, on=last_monday + dt.timedelta(days=1)))
    signed_in.post(f"/api/grocery-list/{_ids(signed_in)['Ground beef']}/status", json={"status": "purchased"})
    assert _names(signed_in, "bought") == ["Ground beef"]

    frozen_today(household_pin(9, on=monday))
    _week(signed_in, monday, meal="Salad")
    assert _names(signed_in, "bought") == [], "last week's beef is drawn as last week's, not this one's"
