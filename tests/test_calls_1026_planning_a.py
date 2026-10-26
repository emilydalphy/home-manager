"""
Emily's answers to the small calls, 2026-10-26 ("go with your picks") —
the planning-the-week batch. One section per pick.
"""
from __future__ import annotations

from datetime import date, timedelta

import pytest

from app import tools
from app.tools import usual_week as _usual_week


def _week(monday: str) -> list[str]:
    start = date.fromisoformat(monday)
    return [(start + timedelta(days=i)).isoformat() for i in range(7)]


# ---------- 16. A night a cook-ahead turns into a reheat has nothing to prep ----------

def _prep_cuts(plan_id: int) -> list[tuple]:
    from app.db import get_conn

    conn = get_conn()
    rows = conn.execute(
        "SELECT meal_plan_entry_id, task_date, description, status FROM prep_tasks "
        "WHERE weekly_plan_id = ? AND task_type = 'prep_cut' ORDER BY id",
        (plan_id,),
    ).fetchall()
    conn.close()
    return [tuple(r) for r in rows]


def test_a_cook_ahead_takes_the_reheat_nights_prep_off_and_unbatching_puts_it_back():
    for n in ("Alex", "Sam"):
        tools.add_member(n)
    tools.add_recipe("Chicken Bowls", ingredients=[{"item": "romaine", "qty": "1 head"}], default_servings=2)
    monday = _this_monday()
    days = tools._week_dates(monday)
    plan_id = tools.create_weekly_plan(monday)["weekly_plan_id"]
    mon = tools.plan_meal(days[0], "Chicken Bowls", slot="dinner", weekly_plan_id=plan_id)["entry_id"]
    thu = tools.plan_meal(days[3], "Chicken Bowls", slot="dinner", weekly_plan_id=plan_id)["entry_id"]
    tools.add_prep_cut(plan_id, days[2], "Cut up romaine", entry_ids=[thu])
    tools.add_prep_cut(plan_id, days[0], "Cut up romaine", entry_ids=[mon])

    tools.set_cook_ahead(mon, [thu])
    # Thursday reheats Monday's batch: its prep is gone; Monday's stays.
    assert _prep_cuts(plan_id) == [(mon, days[0], "Cut up romaine", "pending")]

    tools.set_cook_ahead(mon, [])
    assert sorted(_prep_cuts(plan_id)) == sorted([
        (mon, days[0], "Cut up romaine", "pending"),
        (thu, days[2], "Cut up romaine", "pending"),
    ])


def test_a_ticked_prep_step_stays_as_the_record_it_is():
    for n in ("Alex", "Sam"):
        tools.add_member(n)
    tools.add_recipe("Chicken Bowls", ingredients=[{"item": "romaine", "qty": "1 head"}], default_servings=2)
    monday = _this_monday()
    days = tools._week_dates(monday)
    plan_id = tools.create_weekly_plan(monday)["weekly_plan_id"]
    mon = tools.plan_meal(days[0], "Chicken Bowls", slot="dinner", weekly_plan_id=plan_id)["entry_id"]
    thu = tools.plan_meal(days[3], "Chicken Bowls", slot="dinner", weekly_plan_id=plan_id)["entry_id"]
    task = tools.add_prep_cut(plan_id, days[2], "Cut up romaine", entry_ids=[thu])["tasks"][0]["prep_task_id"]
    tools.check_off_prep_step(task, "done")
    tools.set_cook_ahead(mon, [thu])
    assert _prep_cuts(plan_id) == [(thu, days[2], "Cut up romaine", "done")]


# ---------- 18. "Cut it to four dinners" in chat is this week's ----------

def test_chat_count_is_saved_on_the_week_and_leaves_the_usual_count_alone():
    tools.set_household_meal_preferences(dinners_per_week=6, mark_complete=False)
    result = tools.set_week_dish_count("dinner", 4, week_start_date="2026-11-02")
    assert result["applies_to"] == "this week only"
    assert tools.get_meal_planning_preferences()["meal_counts"]["dinners_per_week"] == 6
    intake = tools.get_week_intake("2026-11-02")
    assert intake["week_answers"]["dishes"] == {"dinner": 4}
    with _usual_week.this_week(intake["week_answers"]):
        plan = _usual_week.generation_plan(_week("2026-11-02"))
    assert plan["answered"] is True
    assert plan["targets"]["dinner"] == 4
    # Another week is untouched.
    assert _usual_week.generation_plan(_week("2026-11-09"))["targets"].get("dinner") in (None, 6)


def test_chat_count_keeps_the_weeks_other_answers():
    tools.save_week_intake("2026-11-02", freeform="tacos Friday", week_answers={"prep": {"days": ["sunday"]}})
    tools.set_week_dish_count("lunch", 2, week_start_date="2026-11-02")
    intake = tools.get_week_intake("2026-11-02")
    assert intake["freeform"] == "tacos Friday"
    assert intake["week_answers"]["prep"] == {"days": ["sunday"]}
    assert intake["week_answers"]["dishes"] == {"lunch": 2}


@pytest.mark.parametrize("meal, count", [("dinner", 0), ("dinner", 8), ("snack", 3), ("dinner", True)])
def test_chat_count_refuses_a_number_outside_the_week(meal, count):
    with pytest.raises(ValueError):
        tools.set_week_dish_count(meal, count, week_start_date="2026-11-02")
    assert tools.get_week_intake("2026-11-02") is None


def test_the_chat_tool_is_offered_and_routed():
    from app import agent

    names = {t["name"] for t in agent.TOOL_DEFINITIONS}
    assert "set_week_dish_count" in names
    assert agent.TOOL_FUNCTIONS["set_week_dish_count"] is tools.set_week_dish_count


# ---------- 19. The week's newest answers, everywhere ----------

def _this_monday() -> str:
    from conftest import household_today

    today = household_today()
    return (today - timedelta(days=today.weekday())).isoformat()


def test_the_week_readers_follow_the_newest_answers_not_the_drafted_ones():
    week = _this_monday()
    tools.save_week_intake(week, week_answers={"prep": {"days": ["sunday"]}})
    plan_id = tools.create_weekly_plan(week)["weekly_plan_id"]
    tools.attach_intake_to_plan(plan_id, tools.get_week_intake(week)["intake_id"])
    # Re-plan answered, never drafted: a newer revision the plan wasn't made from.
    tools.save_week_intake(week, week_answers={"prep": {"days": ["saturday"]}})
    assert _usual_week.plan_week_answers(plan_id) == {"prep": {"days": ["saturday"]}}
    assert _usual_week.week_answers_on(week) == {"prep": {"days": ["saturday"]}}
    assert {"prep": {"days": ["saturday"]}} in _usual_week.live_week_answers()


def test_the_headline_never_says_out_over_a_dinner_the_week_still_has():
    week = _this_monday()
    wednesday = tools._week_dates(week)[2]
    friday = tools._week_dates(week)[4]
    plan_id = tools.create_weekly_plan(week)["weekly_plan_id"]
    tools.add_recipe("Chili", ingredients=[{"item": "beans", "qty": "1 tin"}])
    for day in tools._week_dates(week):
        if day == friday:
            tools.plan_slot_empty(plan_id, day, "dinner", "You’re out — I’ve planned nothing and bought nothing.")
        else:
            tools.plan_meal(day, "Chili", slot="dinner", weekly_plan_id=plan_id)
    # Friday was drafted out; Wednesday was tagged out afterwards and never drafted.
    tools.save_week_intake(week, night_tags={friday: ["out"], wednesday: ["out"]})
    headline = tools.get_week_menu(plan_id)["headline"]
    assert headline == "That’s 6 dinners you’ll cook this week rather than 7 — you’re out Friday."


# ---------- 21. A past night nobody ticked cooked can't be marked nobody home ----------

def _past_week(signed_in_unused=None) -> tuple[int, str, str]:
    from conftest import household_date

    for name in ("Emily", "Vineeth"):
        tools.add_member(name)
    tools.add_recipe("Bean Chili", ingredients=[{"item": "beans", "qty": "1 lb"}],
                     prep_time_minutes=10, cook_time_minutes=20)
    start = household_date(-3)
    first = date.fromisoformat(start)
    monday = (first - timedelta(days=first.weekday())).isoformat()
    plan = tools.create_weekly_plan(monday, content_start_date=start, day_count=7)["weekly_plan_id"]
    for i in range(7):
        tools.plan_meal((first + timedelta(days=i)).isoformat(), "Bean Chili", slot="dinner", weekly_plan_id=plan)
    tools.approve_weekly_plan(plan)
    return plan, monday, household_date(-1)


def _dinner(plan: int, day: str) -> dict:
    from app.db import get_conn

    conn = get_conn()
    row = conn.execute(
        "SELECT id, slot_state, cooked_status FROM meal_plan_entries "
        "WHERE weekly_plan_id = ? AND date = ? AND slot = 'dinner' AND component_category IS NULL",
        (plan, day),
    ).fetchone()
    conn.close()
    return dict(row)


def test_the_day_sheet_refuses_nobody_home_on_a_past_uncooked_dinner(signed_in):
    plan, week, yesterday = _past_week()
    res = signed_in.post(f"/api/week/{week}/day-attendance",
                         json={"date": yesterday, "slots": {"dinner": {"absent": ["Emily", "Vineeth"]}}})
    assert res.status_code == 400
    assert res.json()["detail"] == "That night’s already gone."
    assert _dinner(plan, yesterday)["slot_state"] == "planned"
    assert tools.get_slot_attendance(yesterday, "dinner")["nobody_home"] is False


def test_the_last_toggle_and_chat_refuse_it_too():
    plan, _week, yesterday = _past_week()
    tools.set_member_attendance(yesterday, "dinner", "Emily", present=False)  # one out is fine
    with pytest.raises(ValueError, match="That night’s already gone."):
        tools.set_member_attendance(yesterday, "dinner", "Vineeth", present=False)
    with pytest.raises(ValueError, match="That night’s already gone."):
        tools.set_slot_need_for_chat(yesterday, "dinner", "away")
    assert _dinner(plan, yesterday)["slot_state"] == "planned"


def test_a_past_dinner_ticked_cooked_and_tonight_are_still_allowed():
    from conftest import household_date

    plan, _week, yesterday = _past_week()
    tools.check_off_meal(_dinner(plan, yesterday)["id"], "done")
    tools.set_day_attendance(yesterday, {"dinner": {"absent": ["Emily", "Vineeth"]}})
    assert _dinner(plan, yesterday)["cooked_status"] == "done"
    today = household_date(0)
    tools.set_day_attendance(today, {"dinner": {"absent": ["Emily", "Vineeth"]}})
    assert _dinner(plan, today)["slot_state"] == "planned_empty"


# ---------- 23. A cold packed lunch past the lunch number is said on the week ----------

def _lunch_week(names: list[str]) -> int:
    monday = _this_monday()
    days = tools._week_dates(monday)
    plan_id = tools.create_weekly_plan(monday)["weekly_plan_id"]
    for name in set(names):
        tools.add_recipe(name, ingredients=[{"item": "bread", "qty": "1 loaf"}], default_servings=2)
    for day, name in zip(days, names):
        tools.plan_meal(day, name, slot="lunch", weekly_plan_id=plan_id)
    return plan_id


def test_the_week_says_when_a_cold_lunch_put_it_past_the_lunch_number():
    from app.tools import draft_rules

    plan_id = _lunch_week(["Turkey Wraps", "Turkey Wraps", "Pasta Salad", "Pasta Salad", "Rice Paper Rolls"])
    changed = [{"date": "x", "was": "Fried Rice", "now": "Rice Paper Rolls", "for": ["Maya"]}]
    assert draft_rules.cold_lunch_count_line(plan_id, 2, changed) == (
        "Three lunch dishes this week, not two, because Maya needs a cold packed lunch."
    )
    # Within the number, no cold re-pick, or no number: nothing to say.
    assert draft_rules.cold_lunch_count_line(plan_id, 3, changed) == ""
    assert draft_rules.cold_lunch_count_line(plan_id, 2, []) == ""
    assert draft_rules.cold_lunch_count_line(plan_id, None, changed) == ""


def test_the_line_lands_in_the_weeks_said_lines():
    plan_id = _lunch_week(["Turkey Wraps"])
    line = "Three lunch dishes this week, not two, because Maya needs a cold packed lunch."
    tools.record_plan_requests(plan_id, {"said_lines": [line]})
    assert tools.plan_requests(plan_id)["said"] == [line]


# ---------- 26. A member's own "I'm out" survives chat's "back to normal" ----------

def test_back_to_normal_from_chat_keeps_a_members_own_out():
    from conftest import household_date

    from app import agent

    plan, _week, _yesterday = _past_week()
    friday = household_date(2)
    chat = agent.TOOL_FUNCTIONS["set_slot_need"]
    tools.set_member_attendance(friday, "dinner", "Vineeth", present=False)
    chat(date_str=friday, slot="dinner", need="away")
    assert tools.get_slot_attendance(friday, "dinner")["nobody_home"]
    chat(date_str=friday, slot="dinner", need="normal")
    att = tools.get_slot_attendance(friday, "dinner")
    assert att["absent_names"] == ["Vineeth"]
    assert att["present_names"] == ["Emily"]
    assert tools.get_slot_need(friday, "dinner")["need"] == "normal"
    row = _dinner(plan, friday)
    assert row["slot_state"] == "open"


def test_back_to_normal_with_no_own_outs_is_still_everyone_home():
    from conftest import household_date

    from app import agent

    _past_week()
    friday = household_date(2)
    chat = agent.TOOL_FUNCTIONS["set_slot_need"]
    chat(date_str=friday, slot="dinner", need="away")
    chat(date_str=friday, slot="dinner", need="normal")
    assert tools.get_slot_attendance(friday, "dinner")["everyone_home"]
