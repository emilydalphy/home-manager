"""
An open slot today is a row on Today's timeline — any meal, not just dinner.

Loop Board "A lunch or breakfast the app handed back shows nowhere on Today",
decided by Emily 2026-10-12: "a ROW on the Today timeline (not a needs-you
card), all slots". Before this, moves_for_day built nothing for a slot with no
meal behind it, so an open breakfast, lunch or snack was on no screen of
Today at all; only an open dinner surfaced, through the needs-you band.
"""
from __future__ import annotations

import datetime
from datetime import time
from pathlib import Path

from app import tools
from conftest import household_today


def _setup():
    tools.add_member("Alex")
    tools.add_recipe("Bean Chili", ingredients=[{"item": "Beans", "qty": "1 can"}],
                     prep_time_minutes=10, cook_time_minutes=20, default_servings=2)
    today = household_today()
    plan_id = tools.create_weekly_plan((today - datetime.timedelta(days=2)).isoformat())["weekly_plan_id"]
    return today, plan_id


def _noon(today):
    return datetime.datetime.combine(today, time(12, 0))


def test_every_open_slot_today_is_a_quiet_row_carrying_its_reason():
    today, plan_id = _setup()
    iso = today.isoformat()
    tools.plan_meal(iso, "Bean Chili", slot="dinner", weekly_plan_id=plan_id)
    reasons = {
        "breakfast": "You cut Oats back, so this one is yours to fill.",
        "lunch": "You cut Bean Chili back, so this one is yours to fill.",
        "snack": "You cut Apples back, so this one is yours to fill.",
    }
    for slot, reason in reasons.items():
        tools.plan_slot_open(plan_id, iso, slot, reason)

    payload = tools.today_moves(now=_noon(today))
    rows = {m["slot"]: m for m in payload["moves"] if m["kind"] == "open_slot"}

    assert set(rows) == {"breakfast", "lunch", "snack"}
    lunch = rows["lunch"]
    assert lunch["title"] == "Lunch needs your call"
    assert lunch["meta"] == reasons["lunch"]
    assert lunch["tickable"] is False and lunch["done"] is False
    # Answered where Pick already lives: that day on Plan.
    assert lunch["action"]["target"] == {"tab": "week", "planDay": {"date": iso, "slot": "lunch"}}
    # In its own place in the day: breakfast before lunch before dinner's cook.
    order = [m["slot"] for m in payload["moves"] if m["kind"] in ("open_slot", "cook")]
    assert order.index("breakfast") < order.index("lunch") < order.index("dinner")
    # A choice is never the card, even at the hour the meal would be eaten.
    assert payload["featured"] not in {m["id"] for m in rows.values()}


def test_an_open_lunch_alone_is_not_featured_and_tick_is_a_no_op():
    today, plan_id = _setup()
    tools.plan_slot_open(plan_id, today.isoformat(), "lunch", "You cut Bean Chili back, so this one is yours to fill.")

    payload = tools.today_moves(now=_noon(today))

    assert [m["kind"] for m in payload["moves"]] == ["open_slot"]
    assert payload["featured"] is None
    move_id = payload["moves"][0]["id"]
    assert tools.set_move_done(move_id, True) == {"id": move_id, "done": False, "dispatched_to": None}


def test_planned_empty_and_tomorrows_open_slots_add_no_row_today():
    today, plan_id = _setup()
    iso, tomorrow = today.isoformat(), (today + datetime.timedelta(days=1)).isoformat()
    # Nobody home for dinner tonight: never a question (CLAUDE.md, "A meal
    # slot is never absent").
    _planned_empty(plan_id, iso, "dinner")
    tools.plan_slot_open(plan_id, tomorrow, "lunch", "You cut Bean Chili back, so this one is yours to fill.")

    payload = tools.today_moves(now=_noon(today))

    assert not [m for m in payload["moves"] if m["kind"] == "open_slot"]
    # Tomorrow's open lunch keeps the treatment it had: not tomorrow's card.
    assert payload["tomorrow"] is None


def _planned_empty(plan_id, day, slot):
    from app.db import get_conn
    conn = get_conn()
    conn.execute(
        "INSERT INTO meal_plan_entries (household_id, date, slot, weekly_plan_id, slot_state, open_reason) "
        "VALUES (?, ?, ?, ?, 'planned_empty', 'Everyone is out.')",
        (tools.household_id(), day, slot, plan_id),
    )
    conn.commit()
    conn.close()


def test_today_groups_the_row_with_the_meals_and_opens_plan_on_tap():
    src = Path(__file__).resolve().parents[1].joinpath("static", "shell.js").read_text()
    assert "var COOK_KINDS = { cook: 1, reheat: 1, open_slot: 1 };" in src
    assert "target.planDay" in src and "focusChangedWeekDay(target.planDay.date, target.planDay.slot)" in src
