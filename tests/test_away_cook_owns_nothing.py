"""
A cook who is out that night is not the cook (defect hunt 2026-10-28).

`cooking_role` is a standing answer ("Vineeth cooks", "we take turns") and
knew nothing about the week: with Vineeth marked out for a dinner, Today
and Cook still said he was cooking it, and the tick Emily made on the night
only she was home for was credited to him in move_credits. Now a DEFAULT
owner who is out that night owns nothing — not the cook, not the fridge
move behind it — while a per-move "Who's on it?" pick still stands, and a
night they are home is unchanged.
"""
from __future__ import annotations

import datetime

from app import tools
from app.db import get_conn
from app.tools import cooker as _cooker
from app.tools import moves as _moves
from conftest import household_today

TODAY = household_today()
START = TODAY - datetime.timedelta(days=1)
DAYS = [(START + datetime.timedelta(days=i)).isoformat() for i in range(3)]
YESTERDAY, TODAY_ISO, TOMORROW = DAYS


def _household(role: str, who: str = "") -> dict[str, int]:
    for n in ("Emily", "Vineeth"):
        tools.add_member(n)
        tools.set_member_age_group(n, "adult")
    tools.set_cooking_role(role, who=who)
    tools.add_recipe(
        "Chicken Skewers",
        ingredients=[{"item": "Chicken Thighs", "qty": "1 lb"}],
        prep_time_minutes=10, cook_time_minutes=25, default_servings=2,
    )
    plan = tools.create_weekly_plan(START.isoformat(), day_count=3)["weekly_plan_id"]
    ids = {}
    for d in DAYS:
        ids[d] = tools.plan_meal(d, "Chicken Skewers", slot="dinner", weekly_plan_id=plan)["entry_id"]
    conn = get_conn()
    conn.execute("UPDATE weekly_plans SET status = 'approved' WHERE id = ?", (plan,))
    conn.commit()
    conn.close()
    return {"plan": plan, **ids}


def _cook(day: str) -> dict:
    return next(m for m in _moves.moves_for_day(day) if m["kind"] == "cook")


def _credit(entry_id: int):
    conn = get_conn()
    row = conn.execute(
        "SELECT member_id FROM move_credits WHERE household_id = ? AND meal_plan_entry_id = ?",
        (tools.household_id(), entry_id),
    ).fetchone()
    conn.close()
    return row


def _member_id(name: str) -> int:
    return next(m["id"] for m in tools.list_members() if m["name"] == name)


def test_the_one_cook_is_not_named_on_a_night_they_are_out():
    _household("one_person", who="Vineeth")
    tools.set_member_attendance(TODAY_ISO, "dinner", "Vineeth", present=False)
    tonight = _cook(TODAY_ISO)
    assert (tonight["owner"], tonight["owner_name"]) == (None, None)
    # A night he IS home is still his.
    tomorrow = _cook(TOMORROW)
    assert tomorrow["owner_name"] == "Vineeth"
    assert tomorrow["owner"] == _member_id("Vineeth")


def test_the_tick_on_that_night_is_credited_to_nobody_not_the_absent_cook():
    ids = _household("one_person", who="Vineeth")
    tools.set_member_attendance(TODAY_ISO, "dinner", "Vineeth", present=False)
    _cooker.check_off_meal(ids[TODAY_ISO], "done")
    row = _credit(ids[TODAY_ISO])
    assert row is not None and row["member_id"] is None
    # And a night he was home credits him, as before.
    _cooker.check_off_meal(ids[TOMORROW], "done")
    assert _credit(ids[TOMORROW])["member_id"] == _member_id("Vineeth")


def test_yesterdays_had_it_does_not_credit_the_cook_who_was_away():
    ids = _household("one_person", who="Vineeth")
    tools.set_member_attendance(YESTERDAY, "dinner", "Vineeth", present=False)
    tools.answer_yesterday(ids[YESTERDAY], "had", today=TODAY)
    row = _credit(ids[YESTERDAY])
    assert row is None or row["member_id"] is None


def test_turns_the_turn_holder_who_is_out_owns_nothing_that_night():
    _household("turns")
    first = _cook(YESTERDAY)["owner_name"]
    assert first == "Emily"  # the rotation's anchor, unchanged
    tools.set_member_attendance(YESTERDAY, "dinner", "Emily", present=False)
    assert _cook(YESTERDAY)["owner_name"] is None
    # The rotation itself does not move: today is still Vineeth's.
    assert _cook(TODAY_ISO)["owner_name"] == "Vineeth"


def test_a_said_owner_still_stands_even_on_an_away_night():
    ids = _household("one_person", who="Vineeth")
    tools.set_member_attendance(TODAY_ISO, "dinner", "Vineeth", present=False)
    tools.set_move_owner(f"cook:{ids[TODAY_ISO]}", _member_id("Vineeth"), TODAY_ISO)
    assert _cook(TODAY_ISO)["owner_name"] == "Vineeth"


def test_the_fridge_move_for_his_away_night_is_not_his_either():
    ids = _household("one_person", who="Vineeth")
    tools.set_member_attendance(TOMORROW, "dinner", "Vineeth", present=False)
    conn = get_conn()
    conn.execute(
        "INSERT INTO prep_tasks (household_id, weekly_plan_id, task_date, description, "
        "related_meal, status, task_type, meal_plan_entry_id) "
        "VALUES (?, ?, ?, ?, ?, 'pending', 'defrost', ?)",
        (tools.household_id(), ids["plan"], TODAY_ISO,
         "Move the chicken thighs to the fridge — for the skewers.", "Chicken Skewers", ids[TOMORROW]),
    )
    conn.commit()
    conn.close()
    fridge = [m for m in _moves.moves_for_day(TODAY_ISO) if m["kind"] in ("fridge", "prep")]
    assert fridge, "the thaw should be one of today's moves"
    assert all(m["owner_name"] is None for m in fridge)
    # Tonight's own cook (he is home tonight) is still his.
    assert _cook(TODAY_ISO)["owner_name"] == "Vineeth"
