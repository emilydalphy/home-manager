"""
Two adults marking themselves out of the same meal at the same instant
(Loop Board, rebuilt on main 2026-10-13).

set_member_attendance read the slot's present list, took one person out in
Python, and wrote the WHOLE list back on another connection. Two phones
tapping "I'm out" together both read the same list, so the second write
erased the first: 10 of 10 trials kept only one name, and a dinner the
last two people left stayed planned (and bought) for one.

Every test here fires the two writes from real threads released by one
barrier — no gap between them.
"""
import datetime
import threading

import pytest

from app import tools
from app.db import get_conn
from conftest import household_today


def _next_week() -> list[str]:
    today = household_today()
    monday = today - datetime.timedelta(days=today.weekday()) + datetime.timedelta(days=7)
    return [(monday + datetime.timedelta(days=i)).isoformat() for i in range(7)]


@pytest.fixture
def family():
    for name in ("Sam", "Riley", "Kid"):
        tools.add_member(name)


def _together(*calls):
    """Run each call on its own thread, released at the same moment."""
    barrier = threading.Barrier(len(calls))
    results, errors = [None] * len(calls), []

    def run(i, fn):
        try:
            barrier.wait()
            results[i] = fn()
        except Exception as exc:  # surfaced below, never swallowed
            errors.append(exc)

    threads = [threading.Thread(target=run, args=(i, fn)) for i, fn in enumerate(calls)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors, errors
    return results


def test_two_outs_at_the_same_instant_both_stick(family):
    meals = [(day, slot) for day in _next_week()[:5] for slot in ("lunch", "dinner")]
    for day, slot in meals:
        sam, riley = _together(
            lambda: tools.set_member_attendance(day, slot, "Sam", present=False),
            lambda: tools.set_member_attendance(day, slot, "Riley", present=False),
        )
        stored = tools.get_slot_attendance(day, slot)
        assert sorted(stored["absent_names"]) == ["Riley", "Sam"], (day, slot)
        # Neither reply describes a state without its own absence.
        assert "Sam" in sam["absent_names"] and "Riley" in riley["absent_names"]


def test_an_out_and_a_back_at_the_same_instant_both_land(family):
    day = _next_week()[2]
    tools.set_member_attendance(day, "dinner", "Riley", present=False)
    _together(
        lambda: tools.set_member_attendance(day, "dinner", "Sam", present=False),
        lambda: tools.set_member_attendance(day, "dinner", "Riley", present=True),
    )
    assert tools.get_slot_attendance(day, "dinner")["absent_names"] == ["Sam"]


def test_the_last_two_out_together_empty_the_dinner_and_its_groceries(family):
    days = _next_week()
    day = days[3]
    tools.add_recipe("Chili", ingredients=[{"item": "Black beans", "qty": "2 cans"}],
                     prep_time_minutes=10, cook_time_minutes=20)
    plan = tools.create_weekly_plan(days[0])
    tools.plan_meal(day, "Chili", slot="dinner", weekly_plan_id=plan["weekly_plan_id"])
    tools.approve_weekly_plan(plan["weekly_plan_id"], approved_by="Sam")
    tools.set_member_attendance(day, "dinner", "Kid", present=False)
    assert any("beans" in (i["item"] or "").lower() for i in tools.list_grocery_list())

    _together(
        lambda: tools.set_member_attendance(day, "dinner", "Sam", present=False),
        lambda: tools.set_member_attendance(day, "dinner", "Riley", present=False),
    )

    assert tools.get_slot_attendance(day, "dinner")["nobody_home"]
    assert tools.get_slot_need(day, "dinner")["need"] == "away"
    conn = get_conn()
    states = [r["slot_state"] for r in conn.execute(
        "SELECT slot_state FROM meal_plan_entries WHERE weekly_plan_id = ? AND date = ? AND slot = 'dinner'",
        (plan["weekly_plan_id"], day),
    )]
    conn.close()
    assert states == ["planned_empty"], states
    assert not any("beans" in (i["item"] or "").lower() for i in tools.list_grocery_list())


def test_a_toggle_keeps_the_guest_count(family):
    day = _next_week()[1]
    tools.set_guest_count(day, "dinner", 2)
    tools.set_member_attendance(day, "dinner", "Sam", present=False)
    att = tools.get_slot_attendance(day, "dinner")
    assert att["guest_count"] == 2 and att["absent_names"] == ["Sam"]
