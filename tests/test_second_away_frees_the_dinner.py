"""
The second person marked away on the same meal crashed (Bug, Phase 1 — Beta,
found by the independent review of the snacks branch, 2026-10-02).

Alex away for a dinner leaves a 'ready_made' first-meal-back edge, and
_recommend_ready_made suggests a double batch of the most recent planned
dinner before it — which is that same dinner, still on the plan for Sam.
slot_needs.recommended_batch_from_entry_id is a real foreign key into
meal_plan_entries with no ON DELETE action, so when Sam is marked away too
and the dinner has to go, the DELETE raised
`sqlite3.IntegrityError: FOREIGN KEY constraint failed`. The first away
never trips it: the dinner stays.

Measured on main 926bab4, one test per process: every CATCH is red there
with that IntegrityError. The setup GUARD is green on both sides; the
defrost GUARD is red on main only because the helper it calls is new. (Run
as a whole file on main, the run hangs after the first crash: the failed
write leaves its connection holding the lock.)
"""
from __future__ import annotations

import datetime

from conftest import household_today

from app import tools
from app.db import get_conn
from app.tools import attendance, slot_needs, weekly_plan

START = household_today()
DINNER_DAY = (START + datetime.timedelta(days=1)).isoformat()


def _approved_week() -> tuple[int, int]:
    """Two adults, one approved chili on DINNER_DAY. Returns (plan, entry)."""
    tools.add_member("Alex")
    tools.add_member("Sam")
    tools.add_recipe(
        "Bean Chili",
        ingredients=[{"item": "Black beans", "qty": "1 can"}, {"item": "Onion", "qty": "1"}],
        default_servings=2,
    )
    plan_id = tools.create_weekly_plan(START.isoformat())["weekly_plan_id"]
    entry_id = tools.plan_meal(DINNER_DAY, "Bean Chili", slot="dinner", weekly_plan_id=plan_id)["entry_id"]
    tools.approve_weekly_plan(plan_id, "Emily")
    return plan_id, entry_id


def _add_thaw(plan_id: int, entry_id: int) -> int:
    conn = get_conn()
    cur = conn.execute(
        "INSERT INTO prep_tasks (household_id, weekly_plan_id, task_date, description, related_meal, "
        "task_type, meal_plan_entry_id) VALUES (?, ?, ?, ?, ?, 'defrost', ?)",
        (tools.household_id(), plan_id, START.isoformat(), "Move the beef to the fridge", "Bean Chili", entry_id),
    )
    conn.commit()
    conn.close()
    return cur.lastrowid


def _alex_away() -> None:
    slot_needs.set_away_stretch(DINNER_DAY, "dinner", DINNER_DAY, "dinner", member_names=["Alex"])


def _recommended_from(entry_id: int) -> list[dict]:
    conn = get_conn()
    rows = [dict(r) for r in conn.execute(
        "SELECT date, slot, need, recommended_batch_from_entry_id FROM slot_needs "
        "WHERE household_id = ? AND recommended_batch_from_entry_id = ?",
        (tools.household_id(), entry_id),
    )]
    conn.close()
    return rows


def _dinner_states() -> list[str]:
    conn = get_conn()
    rows = [r["slot_state"] for r in conn.execute(
        "SELECT slot_state FROM meal_plan_entries WHERE household_id = ? AND date = ? AND slot = 'dinner' "
        "AND component_category IS NULL ORDER BY id",
        (tools.household_id(), DINNER_DAY),
    )]
    conn.close()
    return rows


def _needed_items() -> set[str]:
    return {i["item"] for i in tools.list_grocery_list(status="needed")}


def _prep_rows_for(entry_id: int) -> int:
    conn = get_conn()
    n = conn.execute(
        "SELECT COUNT(*) FROM prep_tasks WHERE household_id = ? AND meal_plan_entry_id = ?",
        (tools.household_id(), entry_id),
    ).fetchone()[0]
    conn.close()
    return n


def test_the_setup_really_points_a_recommendation_at_the_dinner():
    """GUARD — green on both sides. Without this the tests below prove nothing."""
    _plan_id, entry_id = _approved_week()
    _alex_away()
    assert _recommended_from(entry_id), "the first away must leave a ready-made edge naming this dinner"
    assert _dinner_states() == ["planned"]


def test_second_person_away_as_a_trip_empties_the_dinner_and_reverses_it():
    """CATCH. The reviewer's reproduction: two single-meal trips, one each."""
    plan_id, entry_id = _approved_week()
    thaw_id = _add_thaw(plan_id, entry_id)
    _alex_away()
    assert {"Black beans", "Onion"} <= _needed_items()

    slot_needs.set_away_stretch(DINNER_DAY, "dinner", DINNER_DAY, "dinner", member_names=["Sam"])

    assert _dinner_states() == ["planned_empty"]
    assert not ({"Black beans", "Onion"} & _needed_items()), "the chili's groceries come off the list"
    assert _prep_rows_for(entry_id) == 0, "the thaw for a meal nobody is home for goes with it"
    conn = get_conn()
    assert conn.execute("SELECT 1 FROM prep_tasks WHERE id = ?", (thaw_id,)).fetchone() is None
    conn.close()


def test_second_person_away_as_a_tap_empties_the_dinner():
    """CATCH. The same thing from the presence avatar: Alex on a trip, Sam taps out."""
    _plan_id, entry_id = _approved_week()
    _alex_away()

    att = attendance.set_member_attendance(DINNER_DAY, "dinner", "Sam", present=False)

    assert att["nobody_home"]
    assert _dinner_states() == ["planned_empty"]
    assert not ({"Black beans", "Onion"} & _needed_items())


def test_the_edge_keeps_its_need_and_loses_only_the_gone_suggestion():
    """CATCH. The first-meal-back fact stays; the suggestion naming a deleted dinner does not."""
    _plan_id, entry_id = _approved_week()
    _alex_away()
    edge = _recommended_from(entry_id)[0]
    slot_needs.confirm_slot_recommendation(edge["date"], edge["slot"])

    slot_needs.set_away_stretch(DINNER_DAY, "dinner", DINNER_DAY, "dinner", member_names=["Sam"])

    need = slot_needs.get_slot_need(edge["date"], edge["slot"])
    assert need["need"] == "ready_made"
    assert need["recommended_batch_from_entry_id"] is None
    assert not need["recommendation_confirmed"]
    assert slot_needs.describe_ready_made(edge["date"], edge["slot"]) is None


def test_a_confirmed_defrost_on_the_same_edge_is_left_alone():
    """GUARD — pins the CASE in _release_ready_made_recommendations (red on main only as AttributeError)."""
    _plan_id, entry_id = _approved_week()
    _alex_away()
    edge = _recommended_from(entry_id)[0]
    conn = get_conn()
    conn.execute(
        "UPDATE slot_needs SET recommended_defrost_item = 'Beef', recommendation_confirmed = 1 "
        "WHERE household_id = ? AND date = ? AND slot = ?",
        (tools.household_id(), edge["date"], edge["slot"]),
    )
    released = weekly_plan._release_ready_made_recommendations(conn, [entry_id])
    conn.commit()
    conn.close()

    need = slot_needs.get_slot_need(edge["date"], edge["slot"])
    assert released == 1
    assert need["recommended_batch_from_entry_id"] is None
    assert need["recommended_defrost_item"] == "Beef"
    assert need["recommendation_confirmed"]


def test_taking_the_recommended_dinner_off_the_day_does_not_crash():
    """CATCH. Same key, the Review stepper's "−" (drop_dish_from_day)."""
    plan_id, entry_id = _approved_week()
    _alex_away()

    weekly_plan.drop_dish_from_day(plan_id, entry_id)

    assert _recommended_from(entry_id) == []
    assert not ({"Black beans", "Onion"} & _needed_items())


def test_resetting_the_week_does_not_crash():
    """CATCH. Same key, the Reset button (clear_weekly_plan)."""
    plan_id, entry_id = _approved_week()
    _alex_away()

    tools.clear_weekly_plan(plan_id)

    assert _dinner_states() == []
    assert _recommended_from(entry_id) == []


# --------------------------------------- the other doors that delete a row
#
# Found by the adversarial verifier on the first commit: the same key blocks
# every delete of a dinner a ready-made edge names. One test per door, each
# red on main with the same IntegrityError.

def _tacos():
    tools.add_recipe("Tacos", ingredients=[{"item": "Tortillas", "qty": "8"}], default_servings=2)


def test_swapping_the_recommended_dinner_does_not_crash():
    """CATCH. The everyday swap (_replace_slot_entries). Since Emily's 2026-10-03
    call the suggestion follows the new dish (tests/test_batch_suggestion_follows_swap.py)."""
    plan_id, entry_id = _approved_week()
    _tacos()
    _alex_away()
    edge = _recommended_from(entry_id)[0]

    weekly_plan.swap_meal_in_plan(plan_id, DINNER_DAY, "Tacos", slot="dinner")

    assert _recommended_from(entry_id) == []
    need = slot_needs.get_slot_need(edge["date"], edge["slot"])
    assert need["need"] == "ready_made"
    assert need["recommended_batch_from_entry_id"] not in (None, entry_id)
    assert "Black beans" not in _needed_items()
    assert "Tortillas" in _needed_items()


def test_swapping_a_confirmed_recommended_dinner_does_not_crash():
    """CATCH. The same swap after the household said yes to the double batch."""
    plan_id, entry_id = _approved_week()
    _tacos()
    _alex_away()
    edge = _recommended_from(entry_id)[0]
    slot_needs.confirm_slot_recommendation(edge["date"], edge["slot"])

    weekly_plan.swap_meal_in_plan(plan_id, DINNER_DAY, "Tacos", slot="dinner")

    need = slot_needs.get_slot_need(edge["date"], edge["slot"])
    assert need["recommended_batch_from_entry_id"] not in (None, entry_id)
    assert need["recommendation_confirmed"], "followed to the new dish, still confirmed (Emily, 2026-10-03)"


def test_the_multi_day_swap_does_not_crash():
    """CATCH. replace_dish_on_days, the Swap on a multi-day row."""
    plan_id, entry_id = _approved_week()
    _tacos()
    _alex_away()

    weekly_plan.replace_dish_on_days(plan_id, [
        {"old_entry_id": entry_id, "date": DINNER_DAY, "slot": "dinner", "new_meal": "Tacos"},
    ])

    assert _recommended_from(entry_id) == []
    assert _dinner_states() == ["planned"]


def test_a_new_plan_taking_over_the_day_does_not_crash():
    """CATCH. _release_plan_days, reached through retire_overlapping_plans."""
    plan_id, entry_id = _approved_week()
    _alex_away()
    new_plan_id = tools.create_weekly_plan(START.isoformat())["weekly_plan_id"]
    assert new_plan_id != plan_id

    weekly_plan.retire_overlapping_plans(new_plan_id, START.isoformat(), 7)

    assert _recommended_from(entry_id) == []
    conn = get_conn()
    assert conn.execute("SELECT 1 FROM meal_plan_entries WHERE id = ?", (entry_id,)).fetchone() is None
    conn.close()


def test_dedupe_of_a_doubled_slot_does_not_crash():
    """CATCH. _dedupe_duplicate_slots removing the second row, which the edge names."""
    plan_id, first_id = _approved_week()
    conn = get_conn()
    cur = conn.execute(
        "INSERT INTO meal_plan_entries (household_id, date, slot, weekly_plan_id, slot_state, freeform_meal) "
        "VALUES (?, ?, 'dinner', ?, 'planned', 'Soup')",
        (tools.household_id(), DINNER_DAY, plan_id),
    )
    dup_id = cur.lastrowid
    conn.commit()
    conn.close()
    _alex_away()
    # _recommend_ready_made picks the newest row on the latest date: the duplicate.
    assert _recommended_from(dup_id), "setup: the edge must name the row dedupe removes"

    weekly_plan._dedupe_duplicate_slots(plan_id, [{"date": DINNER_DAY, "slot": "dinner", "count": 2}])

    assert _recommended_from(dup_id) == []
    assert _dinner_states() == ["planned"]


def test_discarding_a_failed_plan_removes_its_meals():
    """CATCH. discard_failed_plan swallows the error, so main reports 0 meals removed and leaves them."""
    from app.tools import meal_plans

    plan_id, entry_id = _approved_week()
    _alex_away()

    result = meal_plans.discard_failed_plan(plan_id)

    assert result["meals_removed"] >= 1
    assert _recommended_from(entry_id) == []
    conn = get_conn()
    assert conn.execute("SELECT 1 FROM meal_plan_entries WHERE id = ?", (entry_id,)).fetchone() is None
    conn.close()
