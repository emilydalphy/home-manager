"""
The Review "−" (weekly_plan.drop_dish_from_day) takes the dropped night's
fridge move with it, as every swap door already does (overnight hunt,
2026-10-24).

Found through real doors on main: an approved week with Roast Chicken on
one night, "Something in the freezer?" answered with the whole chicken
(one fridge move booked), then the "−" on that night. The night came back
as an `open` question, and the move stayed:

    chat "what do I need to defrost?" : "Move the Whole chicken to the
        fridge — for <day>'s Roast Chicken."
    the Cook tab's prep session       : the same move, tickable

for a dinner the week no longer has. The swap doors stopped doing this on
2026-09-21 (_release_prep_rows); the "−" deletes its row by hand and was
never given the same line. A thaw already TICKED is held rather than lost,
exactly as the swap does it.
"""
from datetime import timedelta

import pytest

from app import tools
from app.db import get_conn

from conftest import household_today


@pytest.fixture
def week_with_a_thaw():
    """An approved week, one chicken night, its fridge move booked by the
    freezer step — real doors only (the same build as
    test_thaw_survives_a_swap's fixture)."""
    tools.add_member("Emily")
    tools.set_member_age_group("Emily", "adult")
    tools.set_prep_days(["sunday", "monday", "tuesday", "wednesday", "thursday", "friday", "saturday"])
    tools.add_recipe("Roast Chicken",
                     ingredients=[{"item": "Whole chicken", "qty": "1", "category": "meat"}],
                     prep_time_minutes=15, cook_time_minutes=60)
    start = household_today().isoformat()
    night = (household_today() + timedelta(days=3)).isoformat()
    pid = tools.create_weekly_plan(start)["weekly_plan_id"]
    tools.plan_meal(night, "Roast Chicken", slot="dinner", weekly_plan_id=pid)
    tools.approve_weekly_plan(pid, approved_by="Emily")
    for item in tools.list_grocery_list():
        tools.mark_grocery_item(item["id"], "purchased")
    offered = tools.meat_items_for_plan(pid)
    assert offered, "premise: the freezer step has something to offer"
    tools.confirm_frozen_items(pid, [offered[0]["item"]])
    assert len(_prep_rows()) == 1, "premise: confirming wrote exactly one fridge move"
    assert _chat_defrost(), "premise: chat names the move before the drop"
    return pid, night, _entry_id(pid, night)


def test_the_minus_takes_the_dropped_nights_fridge_move(week_with_a_thaw):
    """CATCH — red on main: the move outlived its dinner."""
    pid, night, entry = week_with_a_thaw
    out = tools.drop_dish_from_day(pid, entry)
    assert out["status"] == "dropped"
    assert _prep_rows() == []
    assert _chat_defrost() == []
    assert not any("chicken" in t.lower() for t in _session_items(pid))
    assert "held_thawed" not in out, "a pending move is a reminder, not a fact to hold"


def test_a_ticked_thaw_is_held_when_its_night_is_dropped(week_with_a_thaw):
    """CATCH — the chicken is already out of the freezer, so the fact
    outlives the meal, the same as a swap (test_thaw_survives_a_swap)."""
    pid, night, entry = week_with_a_thaw
    tools.check_off_prep_step(_prep_rows()[0]["id"], status="done")

    out = tools.drop_dish_from_day(pid, entry)

    assert _prep_rows() == []
    held = tools.list_held_things()
    assert len(held) == 1 and "Whole chicken" in held[0]["text"], held
    assert out.get("held_thawed") and out["held_thawed"][0]["item"] == "Whole chicken"


def test_a_refused_drop_leaves_the_move_alone(week_with_a_thaw):
    """GUARD — a night already cooking is refused, and nothing is written:
    the move stays exactly where it was."""
    pid, night, entry = week_with_a_thaw
    conn = get_conn()
    conn.execute("UPDATE meal_plan_entries SET cook_started_at = '2026-01-01T17:00:00' WHERE id = ?", (entry,))
    conn.commit()
    conn.close()
    before = _prep_rows()

    out = tools.drop_dish_from_day(pid, entry)

    assert out["status"] == "refused"
    assert _prep_rows() == before


# ---------- helpers ----------

def _entry_id(plan_id: int, day: str) -> int:
    conn = get_conn()
    row = conn.execute(
        "SELECT id FROM meal_plan_entries WHERE weekly_plan_id = ? AND date = ? AND slot = 'dinner'",
        (plan_id, day),
    ).fetchone()
    conn.close()
    return row["id"]


def _session_items(plan_id: int) -> list[str]:
    return [i.get("title") or i.get("text") or ""
            for s in tools.prep_sessions_for_plan(plan_id) for i in s.get("items", [])]


def _chat_defrost() -> list[str]:
    return [t["description"] for t in tools.get_defrost_schedule(30)]


def _prep_rows() -> list[dict]:
    conn = get_conn()
    rows = conn.execute("SELECT * FROM prep_tasks ORDER BY id").fetchall()
    conn.close()
    return [dict(r) for r in rows]
