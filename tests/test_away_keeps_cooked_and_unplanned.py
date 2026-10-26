"""
Marking a meal nobody-home keeps what the household already decided.

Overnight hunt, 2026-10-22. Two holes in the attendance door
(slot_needs.set_slot_need / _reopen_away_slot):

1. Taking the last person out of a dinner already COOKED (or on the stove)
   deleted the cooked row and reversed its groceries. Every other door that
   can take a meal off a night — the night off, Move, the nights swap, the
   Review "−" — keeps a cooked meal as the record it is.
2. A meal the household plans none of ("lunches per week: 0") that was
   marked away and then back again came back as an OPEN question — "tell
   me what you'd like and I'll shop for it" — for a meal they never wanted.
"""
from __future__ import annotations

import datetime

from conftest import household_date

from app import tools
from app.db import get_conn


def _setup(start: str, approve: bool = True) -> tuple[int, str]:
    for name in ("Emily", "Vineeth"):
        tools.add_member(name)
    for name in ("Bean Chili", "Fish Tacos", "Pasta Bake"):
        tools.add_recipe(name, ingredients=[{"item": f"{name} beans", "qty": "1 lb"}],
                         prep_time_minutes=10, cook_time_minutes=20)
    first = datetime.date.fromisoformat(start)
    monday = (first - datetime.timedelta(days=first.weekday())).isoformat()
    plan = tools.create_weekly_plan(monday, content_start_date=start, day_count=7)["weekly_plan_id"]
    names = ["Bean Chili", "Fish Tacos", "Pasta Bake"]
    for i in range(7):
        d = (first + datetime.timedelta(days=i)).isoformat()
        tools.plan_meal(d, f"Toast {i}", slot="breakfast", weekly_plan_id=plan)
        tools.plan_meal(d, f"Wraps {i}", slot="lunch", weekly_plan_id=plan)
        tools.plan_meal(d, names[i % 3], slot="dinner", weekly_plan_id=plan)
    if approve:
        tools.approve_weekly_plan(plan)
    return plan, monday


def _row(plan: int, day: str, slot: str) -> dict:
    conn = get_conn()
    row = conn.execute(
        "SELECT mpe.id, slot_state, cooked_status, open_reason, derived_from_json, "
        "COALESCE(r.name, mpe.freeform_meal) AS meal FROM meal_plan_entries mpe "
        "LEFT JOIN recipes r ON r.id = mpe.recipe_id "
        "WHERE weekly_plan_id = ? AND date = ? AND slot = ?", (plan, day, slot),
    ).fetchone()
    conn.close()
    return dict(row)


def _groceries() -> list[tuple]:
    conn = get_conn()
    rows = conn.execute("SELECT item, quantity, status FROM grocery_items ORDER BY item").fetchall()
    conn.close()
    return [tuple(r) for r in rows]


def _everyone_out(client, week: str, day: str, slot: str):
    names = [m["name"] for m in tools.list_members()]
    return client.post(f"/api/week/{week}/day-attendance",
                       json={"date": day, "slots": {slot: {"absent": names}}})


def _everyone_in(client, week: str, day: str, slot: str):
    return client.post(f"/api/week/{week}/day-attendance",
                       json={"date": day, "slots": {slot: {"absent": []}}})


def test_a_cooked_dinner_stays_when_nobody_is_home(signed_in):
    plan, week = _setup(household_date(0))
    today = household_date(0)
    cooked = _row(plan, today, "dinner")
    tools.check_off_meal(cooked["id"], "done")
    before = _groceries()

    res = _everyone_out(signed_in, week, today, "dinner")

    assert res.status_code == 200, res.text
    after = _row(plan, today, "dinner")
    assert after["id"] == cooked["id"] and after["slot_state"] == "planned"
    assert after["cooked_status"] == "done" and after["meal"] == cooked["meal"]
    assert _groceries() == before, "nothing comes off the list for a dinner already cooked"
    assert tools.get_slot_need(today, "dinner")["need"] == "away", "the away itself is still recorded"
    assert tools.audit_plan_slots(plan)["complete"]
    # Meals shows the dinner that was eaten — no "Away" badge, no "nothing
    # planned, nothing bought" line over it.
    menu = signed_in.get("/api/week-menu").json()
    entry = next(d for d in menu["days"] if d["date"] == today)["dinner"]
    assert entry["cooked"] and "need" not in entry and "need_reason" not in entry
    assert "attendance_summary" not in entry


def test_a_dinner_on_the_stove_stays_too(signed_in):
    plan, week = _setup(household_date(0))
    today = household_date(0)
    row = _row(plan, today, "dinner")
    conn = get_conn()
    conn.execute("UPDATE meal_plan_entries SET cook_started_at = datetime('now') WHERE id = ?", (row["id"],))
    conn.commit()
    conn.close()

    _everyone_out(signed_in, week, today, "dinner")

    assert _row(plan, today, "dinner")["id"] == row["id"]


def test_an_uncooked_dinner_still_empties(signed_in):
    plan, week = _setup(household_date(0))
    tomorrow = household_date(1)

    _everyone_out(signed_in, week, tomorrow, "dinner")

    assert _row(plan, tomorrow, "dinner")["slot_state"] == "planned_empty"


def test_a_lunch_they_plan_none_of_goes_back_to_empty_not_a_question(signed_in):
    plan, week = _setup(household_date(1))
    day = household_date(2)
    tools.edit_preference("lunches_per_week", 0)
    tools.clear_plan_slot(plan, day, "lunch")
    tools.plan_slot_empty(weekly_plan_id=plan, meal_date=day, slot="lunch",
                          reason="You’ve asked me not to plan lunches — I’ve left this to you.",
                          derived_from={"constraint": "lunchs_per_week:0"})

    _everyone_out(signed_in, week, day, "lunch")
    res = _everyone_in(signed_in, week, day, "lunch")

    assert res.status_code == 200, res.text
    back = _row(plan, day, "lunch")
    assert back["slot_state"] == "planned_empty", back
    assert "lunchs_per_week:0" in back["derived_from_json"]
    assert tools.audit_plan_slots(plan)["complete"]


def test_a_lunch_they_do_plan_still_comes_back_as_a_question(signed_in):
    plan, week = _setup(household_date(1))
    day = household_date(2)

    _everyone_out(signed_in, week, day, "lunch")
    _everyone_in(signed_in, week, day, "lunch")

    back = _row(plan, day, "lunch")
    assert back["slot_state"] == "open"
    assert "will be here after all" in back["open_reason"]
