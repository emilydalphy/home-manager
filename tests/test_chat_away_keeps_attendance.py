"""
The chat door to a meal's need keeps attendance in step (overnight
2026-10-24, branch overnight/hunt-attendance-1024).

"Away" IS an empty attendance (attendance.py's docstring) and every other
door writes both halves. The assistant's set_slot_need wrote only the
need, and the next ordinary gesture read the disagreement as news:

- away in chat, then one adult tapping himself out of the same night,
  reopened it as "…but Emily will be here after all";
- away in chat, then "normal", left the dinner planned_empty saying
  "You're away — I've planned nothing and bought nothing" for ever.

These drive agent.TOOL_FUNCTIONS["set_slot_need"] — the function chat
actually calls — rather than the primitive, which attendance's own sync
uses and which must keep writing only the need.
"""
from __future__ import annotations

import datetime

from conftest import household_today

from app import agent, tools
from app.db import get_conn

DAY = (household_today() + datetime.timedelta(days=3)).isoformat()


def _chat_need(need: str, slot: str = "dinner", day: str = DAY) -> dict:
    return agent.TOOL_FUNCTIONS["set_slot_need"](date_str=day, slot=slot, need=need)


def _approved_dinner() -> int:
    for name in ("Emily", "Vineeth"):
        tools.add_member(name)
    tools.add_recipe(
        "Pasta Bake",
        ingredients=[{"item": "Penne", "qty": "1 lb"}, {"item": "Mozzarella", "qty": "8 oz"}],
        default_servings=2,
    )
    plan_id = tools.create_weekly_plan(household_today().isoformat())["weekly_plan_id"]
    tools.plan_meal(DAY, "Pasta Bake", slot="dinner", weekly_plan_id=plan_id)
    tools.approve_weekly_plan(plan_id, "Emily")
    return plan_id


def _dinner_rows() -> list[dict]:
    conn = get_conn()
    rows = [dict(r) for r in conn.execute(
        "SELECT slot_state, recipe_id, reasoning, open_reason FROM meal_plan_entries "
        "WHERE household_id = ? AND date = ? AND slot = 'dinner' AND component_category IS NULL",
        (tools.household_id(), DAY),
    )]
    conn.close()
    return rows


def test_away_from_chat_records_nobody_home():
    _approved_dinner()
    _chat_need("away")
    att = tools.get_slot_attendance(DAY, "dinner")
    assert att["nobody_home"] and att["explicit"]
    assert tools.get_slot_need(DAY, "dinner")["need"] == "away"
    assert [r["slot_state"] for r in _dinner_rows()] == ["planned_empty"]


def test_one_adult_tapping_out_of_a_chat_away_night_does_not_reopen_it():
    """CATCH — the night came back open: "…but Emily will be here after all"."""
    _approved_dinner()
    _chat_need("away")
    att = tools.set_member_attendance(DAY, "dinner", "Vineeth", present=False)
    assert att["away_need"] == "already_away"
    (row,) = _dinner_rows()
    assert row["slot_state"] == "planned_empty"
    assert "after all" not in (row["open_reason"] or "")


def test_normal_from_chat_over_an_away_hands_the_night_back_as_a_question():
    """CATCH — the dinner stayed planned_empty, still saying "You're away"."""
    _approved_dinner()
    _chat_need("away")
    _chat_need("normal")
    (row,) = _dinner_rows()
    assert row["slot_state"] == "open"
    assert "Emily and Vineeth will be here after all" in row["open_reason"]
    assert tools.get_slot_need(DAY, "dinner")["need"] == "normal"
    assert tools.get_slot_attendance(DAY, "dinner")["everyone_home"]


def test_normal_from_chat_over_a_day_sheet_away_brings_everyone_home():
    """The away came from the day sheet, not chat: "normal" is still everyone home."""
    _approved_dinner()
    tools.set_day_attendance(DAY, {"dinner": {"absent": ["Emily", "Vineeth"]}})
    _chat_need("normal")
    assert tools.get_slot_attendance(DAY, "dinner")["everyone_home"]
    assert tools.get_slot_need(DAY, "dinner")["need"] == "normal"
    assert [r["slot_state"] for r in _dinner_rows()] == ["open"]


def test_quick_from_chat_over_an_away_is_a_quick_dinner_for_everyone():
    _approved_dinner()
    _chat_need("away")
    _chat_need("quick")
    assert tools.get_slot_need(DAY, "dinner")["need"] == "quick"
    assert tools.get_slot_attendance(DAY, "dinner")["everyone_home"]
    assert [r["slot_state"] for r in _dinner_rows()] == ["open"]


def test_away_from_chat_drops_guests_with_nobody_to_host_them():
    _approved_dinner()
    tools.set_guest_count(DAY, "dinner", 2)
    _chat_need("away")
    att = tools.get_slot_attendance(DAY, "dinner")
    assert att["nobody_home"] and att["guest_count"] == 0
    assert tools.get_slot_need(DAY, "dinner")["need"] == "away"


def test_a_household_with_nobody_on_record_is_never_told_zero_guests():
    """No members: attendance cannot be written, the need and the reopen still are."""
    tools.add_recipe("Pasta Bake", ingredients=[{"item": "Penne", "qty": "1 lb"}], default_servings=2)
    plan_id = tools.create_weekly_plan(household_today().isoformat())["weekly_plan_id"]
    tools.plan_meal(DAY, "Pasta Bake", slot="dinner", weekly_plan_id=plan_id)
    _chat_need("away")
    assert [r["slot_state"] for r in _dinner_rows()] == ["planned_empty"]
    _chat_need("normal")
    (row,) = _dinner_rows()
    assert row["slot_state"] == "open"
    assert "0 guests" not in row["open_reason"]
    assert row["open_reason"].startswith("This was down as nobody home, but you will be here")


def test_the_primitive_still_writes_only_the_need():
    """GUARD — attendance's own sync calls set_slot_need; it must not write attendance back."""
    _approved_dinner()
    tools.set_slot_need(DAY, "dinner", "away")
    assert not tools.get_slot_attendance(DAY, "dinner")["explicit"]
