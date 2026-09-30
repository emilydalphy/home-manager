"""
A snack carries no attendance (Emily, 2026-09-30, option 2 on the Loop Board
card "A snack's attendance is written and then silently dropped from the
week payload"). Replaces tests/test_snack_away_characterised.py, which
pinned the old behaviour: a snack row written and never read back, and
marking a snack away destroying BOTH of the day's snacks.

Every writer refuses "snack" with one plain sentence; nothing is written and
both snacks are left alone. The chat assistant gets the sentence as an answer
(not a logged crash). Dinner is the control: it still works.
"""
import json

import pytest
from conftest import household_today

from app import agent, tools
from app.db import get_conn
from app.tools import attendance, slot_needs

REFUSAL = "I don’t track who’s around for snacks — just breakfast, lunch and dinner."


def _seed():
    for name in ("Emily", "Vineeth"):
        tools.add_member(name)
        tools.set_member_age_group(name, "adult")
    day = household_today().isoformat()
    plan = tools.create_weekly_plan(day, day_count=1)["weekly_plan_id"]
    tools.plan_meal(day, "Bean Chili", slot="dinner", weekly_plan_id=plan)
    tools.plan_meal(day, "Apple Slices", slot="snack", weekly_plan_id=plan)
    tools.plan_meal(day, "Greek Yogurt", slot="snack", weekly_plan_id=plan)
    return plan, day


def _snacks(plan):
    conn = get_conn()
    rows = [tuple(r) for r in conn.execute(
        "SELECT id, COALESCE(freeform_meal,''), slot_state FROM meal_plan_entries "
        "WHERE weekly_plan_id = ? AND slot = 'snack' ORDER BY id", (plan,)).fetchall()]
    conn.close()
    return rows


def _count(table, day):
    conn = get_conn()
    n = conn.execute(f"SELECT COUNT(*) FROM {table} WHERE date = ? AND slot = 'snack'", (day,)).fetchone()[0]
    conn.close()
    return n


def test_the_refusal_wording_is_the_pinned_sentence():
    assert tools.SNACK_ATTENDANCE_REFUSAL == REFUSAL


@pytest.mark.parametrize("call", [
    lambda d: attendance.set_slot_attendance(d, "snack", present_member_ids=[]),
    lambda d: attendance.set_slot_attendance(d, "snack", present_member_ids=["Emily"]),
    lambda d: attendance.set_member_attendance(d, "snack", "Vineeth", present=False),
    lambda d: attendance.set_guest_count(d, "snack", 2),
    lambda d: attendance.set_day_attendance(d, {"snack": {"absent": ["Vineeth"]}}),
])
def test_every_writer_refuses_a_snack_and_leaves_both_snacks_alone(call):
    plan, day = _seed()
    before = _snacks(plan)
    assert [r[1] for r in before] == ["Apple Slices", "Greek Yogurt"]
    with pytest.raises(ValueError) as e:
        call(day)
    assert str(e.value) == REFUSAL
    assert _snacks(plan) == before, "both snacks untouched, ids and all"
    assert _count("slot_attendance", day) == 0, "no attendance row written"
    assert _count("slot_needs", day) == 0, "no need row written"


def test_a_day_sheet_with_a_snack_saves_nothing_at_all():
    plan, day = _seed()
    with pytest.raises(ValueError):
        attendance.set_day_attendance(day, {"dinner": {"absent": ["Vineeth"]}, "snack": {"absent": ["Vineeth"]}})
    conn = get_conn()
    n = conn.execute("SELECT COUNT(*) FROM slot_attendance WHERE date = ?", (day,)).fetchone()[0]
    conn.close()
    assert n == 0, "the dinner before the snack was not saved either"


def test_a_snack_can_not_be_marked_away_through_the_need_door_either():
    plan, day = _seed()
    before = _snacks(plan)
    for need in ("away", "quick", "ready_made"):
        with pytest.raises(ValueError) as e:
            slot_needs.set_slot_need(day, "snack", need)
        assert str(e.value) == tools.SNACK_NEED_REFUSAL
    assert _snacks(plan) == before
    assert _count("slot_needs", day) == 0


def test_dinner_is_the_control_and_still_works():
    plan, day = _seed()
    att = attendance.set_slot_attendance(day, "dinner", present_member_ids=["Emily"])
    assert att["absent_member_ids"]
    assert attendance.set_member_attendance(day, "dinner", "Emily", present=False)["away_need"] == "set_away"


def test_a_legacy_snack_row_can_still_be_read_and_cleared():
    plan, day = _seed()
    conn = get_conn()
    conn.execute(
        "INSERT INTO slot_attendance (household_id, date, slot, absent_member_ids_json, guest_count) "
        "SELECT household_id, ?, 'snack', '[]', 1 FROM members LIMIT 1", (day,))
    conn.commit()
    conn.close()
    assert attendance.get_slot_attendance(day, "snack")["guest_count"] == 1
    attendance.clear_slot_attendance(day, "snack")
    assert _count("slot_attendance", day) == 0


@pytest.mark.parametrize("tool,args,wording", [
    ("set_member_attendance", {"date_str": "2026-01-05", "slot": "snack", "member": "Emily", "present": False}, REFUSAL),
    ("set_guest_count", {"date_str": "2026-01-05", "slot": "snack", "guest_count": 2}, REFUSAL),
    ("set_slot_need", {"date_str": "2026-01-05", "slot": "snack", "need": "away"}, None),
])
def test_chat_gets_the_sentence_as_an_answer_not_a_crash(tool, args, wording):
    got = agent._snack_attendance_refusal(tool, args)
    assert got, "the chat loop intercepts it before the tool runs"
    if wording:
        assert got == wording
    assert agent._snack_attendance_refusal(tool, {**args, "slot": "dinner"}) == ""


def test_the_chat_tool_descriptions_no_longer_offer_a_snack():
    for t in agent.TOOL_DEFINITIONS if hasattr(agent, "TOOL_DEFINITIONS") else agent.TOOLS:
        if t["name"] in ("set_member_attendance", "set_guest_count", "set_slot_need"):
            assert "snack" not in json.dumps(t["input_schema"]["properties"]["slot"])


def test_adding_a_member_never_touches_a_legacy_snack_row():
    """A snack row written before the refusal (everyone out, snack away and
    emptied) must not be re-derived when the household changes: that path
    ran clear_plan_slot on the snack slot."""
    plan, day = _seed()
    conn = get_conn()
    conn.execute(
        "INSERT INTO slot_attendance (household_id, date, slot, absent_member_ids_json, guest_count) "
        "SELECT household_id, ?, 'snack', json_group_array(id), 0 FROM members", (day,))
    conn.commit()
    conn.close()
    before = _snacks(plan)
    out = attendance.reconcile_membership()
    assert out["changed"] == []
    assert _snacks(plan) == before
