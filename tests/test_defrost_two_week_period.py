"""
The freezer step asks about EVERY night of a plan longer than a week —
including the second of two dinners on the same weekday.

Measured on a throwaway DB (defect hunt 2026-10-31): a 14-day plan with
Chicken Curry on both Sundays and Beef Tacos on both Mondays, approved on
the first Sunday. GET /api/week/{ws}/defrost-items offered Whole Chicken and
Salmon but never the chicken thighs or the beef — not even for the SECOND
week, whose thaws had days in hand. Nothing ever told the household to take
that meat out.

Why: defrost keys a move night by its _describe() sentence, "Move the
Chicken thighs to the fridge — for Sunday's Chicken Curry.", which names the
day by WEEKDAY. The first Sunday was too late to thaw for, so its sentence
was "settled" — and the second Sunday's identical sentence went with it.

Each test says whether it is a CATCH (red before the fix) or a GUARD.
"""
from datetime import timedelta

import pytest

from app import tools
from app.db import get_conn

from conftest import household_today


@pytest.fixture
def two_week_plan():
    """Roast Chicken (a 72h thaw) tonight and again a week tonight, in one 14-day plan."""
    tools.add_member("Emily")
    tools.set_member_age_group("Emily", "adult")
    tools.add_recipe("Roast Chicken",
                     ingredients=[{"item": "Whole chicken", "qty": "1", "category": "meat"}],
                     prep_time_minutes=15, cook_time_minutes=60)
    start = household_today()
    pid = tools.create_weekly_plan(start.isoformat(), content_start_date=start.isoformat(),
                                   day_count=14)["weekly_plan_id"]
    first = start.isoformat()
    second = (start + timedelta(days=7)).isoformat()
    tools.plan_meal(first, "Roast Chicken", slot="dinner", weekly_plan_id=pid)
    tools.plan_meal(second, "Roast Chicken", slot="dinner", weekly_plan_id=pid)
    tools.approve_weekly_plan(pid, approved_by="Emily", confirm_hard_conflicts=True)
    return pid, first, second


def _offered_nights(pid):
    return {i["item"]: [n["date"] for n in i["nights"]] for i in tools.meat_items_for_plan(pid)}


def test_the_second_weeks_night_is_still_asked_about(two_week_plan):
    """CATCH — tonight is too late to thaw for; a week tonight is not, and
    shares tonight's weekday, dish and sentence. It used to vanish with it."""
    pid, first, second = two_week_plan
    assert _offered_nights(pid) == {"Whole chicken": [second]}


def test_the_route_offers_it_and_the_yes_books_that_night(two_week_plan, signed_in):
    client = signed_in
    """CATCH — through the freezer step's own doors: the chip is there, and
    tapping it books exactly one move, for the second week's dinner."""
    pid, first, second = two_week_plan
    ws = household_today().isoformat()
    items = client.get(f"/api/week/{ws}/defrost-items", params={"weekly_plan_id": pid}).json()["items"]
    assert [i["item"] for i in items] == ["Whole chicken"]

    res = client.post(f"/api/week/{ws}/defrost-confirm",
                      json={"items": ["Whole chicken"], "weekly_plan_id": pid})
    assert res.status_code == 200, res.text
    conn = get_conn()
    rows = conn.execute(
        "SELECT e.date FROM prep_tasks p JOIN meal_plan_entries e ON e.id = p.meal_plan_entry_id "
        "WHERE p.weekly_plan_id = ? AND p.task_type = 'defrost'", (pid,),
    ).fetchall()
    conn.close()
    assert [r["date"] for r in rows] == [second]


def test_a_booked_first_week_move_reads_frozen_only_for_its_own_night():
    """GUARD — a move booked for one night is that night's answer. With both
    nights still thawable, both are offered, and answering marks both."""
    tools.add_member("Emily")
    tools.set_member_age_group("Emily", "adult")
    tools.add_recipe("Roast Chicken",
                     ingredients=[{"item": "Whole chicken", "qty": "1", "category": "meat"}])
    start = household_today()
    pid = tools.create_weekly_plan(start.isoformat(), content_start_date=start.isoformat(),
                                   day_count=14)["weekly_plan_id"]
    a = (start + timedelta(days=4)).isoformat()
    b = (start + timedelta(days=11)).isoformat()
    tools.plan_meal(a, "Roast Chicken", slot="dinner", weekly_plan_id=pid)
    tools.plan_meal(b, "Roast Chicken", slot="dinner", weekly_plan_id=pid)
    tools.approve_weekly_plan(pid, approved_by="Emily", confirm_hard_conflicts=True)
    assert _offered_nights(pid) == {"Whole chicken": [a, b]}
    tools.confirm_frozen_items(pid, ["Whole chicken"])
    offered = tools.meat_items_for_plan(pid)
    assert [n["date"] for n in offered[0]["nights"]] == [a, b] and offered[0]["frozen"]
