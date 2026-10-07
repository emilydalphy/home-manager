"""
Every move has an owner (slice 3) — ticking a move credits whoever owned it.

Loop Board, Phase 1.5: *as the adult who did tonight's cook, I want the tick
to count for me, so that when the household later looks at whether the work
is split fairly, meals are in the picture.* See app/tools/move_credits.py.

What each group is evidence of:

1. THE TICK — cook, reheat, fridge, prep and a holiday's shop row each leave
   one credit naming the move's owner as Today drew it; empty stays empty.
2. THE UN-TICK — clears it, and so does 'skipped'.
3. THE OWNER AT TICK TIME — an answer changed after planning credits the
   new owner; changed after the tick, the credit does not move.
4. THE READ — per household, per person, per kind, per week, and only
   while the row it is for still reads done.
"""
from __future__ import annotations

import datetime

from app import households, tools
from app.db import get_conn
from app.tools import cooker as _cooker
from app.tools import move_credits as _credits
from app.tools import moves as _moves
from conftest import household_today

TODAY = household_today()
WEEK_START = TODAY - datetime.timedelta(days=2)
DAYS = [(WEEK_START + datetime.timedelta(days=i)).isoformat() for i in range(5)]


# ---------- seeding (the same shapes test_move_owner.py seeds) ----------

def _adults(*names) -> dict[str, int]:
    for n in names:
        tools.add_member(n)
        tools.set_member_age_group(n, "adult")
    conn = get_conn()
    rows = conn.execute(
        "SELECT id, name FROM members WHERE household_id = ?", (tools.household_id(),)
    ).fetchall()
    conn.close()
    return {r["name"]: r["id"] for r in rows}


def _recipe(name="Chicken Skewers"):
    tools.add_recipe(
        name,
        ingredients=[{"item": "Chicken Thighs", "qty": "1 lb"}],
        prep_time_minutes=10,
        cook_time_minutes=25,
        default_servings=3,
    )


def _plan(days=DAYS, dish="Chicken Skewers") -> int:
    plan_id = tools.create_weekly_plan(WEEK_START.isoformat())["weekly_plan_id"]
    for d in days:
        tools.plan_meal(d, dish, slot="dinner", weekly_plan_id=plan_id)
    return plan_id


def _entry(day: str, slot: str = "dinner") -> int:
    conn = get_conn()
    row = conn.execute(
        "SELECT id FROM meal_plan_entries WHERE household_id = ? AND date = ? AND slot = ?",
        (tools.household_id(), day, slot),
    ).fetchone()
    conn.close()
    return row["id"]


def _task(plan_id: int, day: str, entry_id: int | None, task_type: str = "defrost",
          related_meal: str = "Chicken Skewers",
          description: str = "Move the chicken thighs to the fridge — for the skewers.") -> int:
    conn = get_conn()
    cur = conn.execute(
        "INSERT INTO prep_tasks (household_id, weekly_plan_id, task_date, description, "
        "related_meal, status, task_type, meal_plan_entry_id) "
        "VALUES (?, ?, ?, ?, ?, 'pending', ?, ?)",
        (tools.household_id(), plan_id, day, description, related_meal, task_type, entry_id),
    )
    conn.commit()
    conn.close()
    return cur.lastrowid


def _rows() -> list[dict]:
    conn = get_conn()
    rows = conn.execute(
        "SELECT household_id, meal_plan_entry_id, prep_task_id, kind, move_date, member_id "
        "FROM move_credits ORDER BY id"
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def _week():
    return _credits.credits(WEEK_START.isoformat())


# ---------- 1. the tick ----------

def test_ticking_a_cook_on_today_credits_its_owner():
    ids = _adults("Emily", "Vineeth")
    _recipe()
    _plan()
    tools.set_cooking_role("one_person", who="Vineeth")
    entry = _entry(DAYS[2])

    _moves.set_move_done(f"cook:{entry}", True)

    assert _week() == [{"id": f"cook:{entry}", "kind": "cook", "date": DAYS[2],
                        "member_id": ids["Vineeth"]}]


def test_turns_credits_the_nights_own_cook_not_the_first_adult():
    """The credit is the owner Today drew — day 1 is the second adult's turn."""
    ids = _adults("Emily", "Vineeth")
    _recipe()
    _plan()
    tools.set_cooking_role("turns")

    _cooker.check_off_meal(_entry(DAYS[0]), "done")
    _cooker.check_off_meal(_entry(DAYS[1]), "done")

    assert [c["member_id"] for c in _week()] == [ids["Emily"], ids["Vineeth"]]


def test_a_move_nobody_owns_is_still_recorded_and_recorded_empty():
    """whoever_free: the tick is in the record, with nobody on it — never a
    guess at who."""
    _adults("Emily", "Vineeth")
    _recipe()
    _plan()
    tools.set_cooking_role("whoever_free")
    entry = _entry(DAYS[1])

    _cooker.check_off_meal(entry, "done")

    assert _week() == [{"id": f"cook:{entry}", "kind": "cook", "date": DAYS[1], "member_id": None}]
    assert _credits.credits(WEEK_START, member_id=None) == _week()


def test_a_one_person_name_nobody_on_record_has_credits_nobody():
    """The answer is free text; with no member behind it there is no id to
    credit, and the name is not turned into one."""
    _adults("Emily")
    _recipe()
    _plan()
    tools.set_cooking_role("one_person", who="Grandma")
    _cooker.check_off_meal(_entry(DAYS[0]), "done")
    assert [c["member_id"] for c in _week()] == [None]


def test_a_reheat_is_credited_as_a_reheat_with_nobody_on_it():
    ids = _adults("Emily")
    _recipe("Egg White Bites")
    plan_id = tools.create_weekly_plan(WEEK_START.isoformat())["weekly_plan_id"]
    tools.plan_meal(DAYS[0], "Egg White Bites", slot="breakfast", weekly_plan_id=plan_id)
    tools.plan_meal(DAYS[1], "Egg White Bites", slot="breakfast", weekly_plan_id=plan_id)
    cook, reheat = _entry(DAYS[0], "breakfast"), _entry(DAYS[1], "breakfast")
    tools.set_cook_ahead(cook, [reheat])
    tools.set_cooking_role("one_person", who="Emily")

    _moves.set_move_done(f"cook:{cook}", True)
    _moves.set_move_done(f"reheat:{reheat}", True)

    assert _week() == [
        {"id": f"cook:{cook}", "kind": "cook", "date": DAYS[0], "member_id": ids["Emily"]},
        {"id": f"reheat:{reheat}", "kind": "reheat", "date": DAYS[1], "member_id": None},
    ]


def test_a_fridge_move_credits_whoever_cooks_the_meal_it_is_for():
    """Under turns the chicken moved on day 0 is for day 1's cook — Vineeth's."""
    ids = _adults("Emily", "Vineeth")
    _recipe()
    plan_id = _plan()
    tools.set_cooking_role("turns")
    task = _task(plan_id, DAYS[0], _entry(DAYS[1]))

    _moves.set_move_done(f"fridge:{task}", True)

    assert _week() == [{"id": f"fridge:{task}", "kind": "fridge", "date": DAYS[0],
                        "member_id": ids["Vineeth"]}]


def test_a_prep_task_for_no_meal_is_credited_to_nobody():
    _adults("Emily")
    _recipe()
    plan_id = _plan()
    tools.set_cooking_role("one_person", who="Emily")
    task = _task(plan_id, DAYS[0], None, task_type="general", related_meal="",
                 description="Soak the beans")

    _cooker.check_off_prep_step(task, "done")

    assert _week() == [{"id": f"prep:{task}", "kind": "prep", "date": DAYS[0], "member_id": None}]


def test_a_holidays_shop_row_is_credited_as_a_shop_with_nobody_on_it():
    """It carries the big meal's entry id, and the shop still has no owner:
    there is no "who shops" answer in this app (move_owner.py)."""
    _adults("Emily")
    _recipe()
    plan_id = _plan()
    tools.set_cooking_role("one_person", who="Emily")
    task = _task(plan_id, DAYS[0], _entry(DAYS[1]), task_type="holiday", related_meal="Shop",
                 description="Shop for the roast")

    _cooker.check_off_prep_step(task, "done")

    assert _week() == [{"id": f"prep:{task}", "kind": "shop", "date": DAYS[0], "member_id": None}]


def test_the_weeks_ordinary_shop_has_no_tick_and_so_no_credit():
    _adults("Emily")
    _recipe()
    _plan()
    tools.add_grocery_item("Milk", "1")
    result = _moves.set_move_done(f"shop:{TODAY.isoformat()}", True)
    assert result["dispatched_to"] is None
    assert _rows() == []


def test_a_component_batch_is_one_cook_credited_once():
    """
    Ticking a batch reaches every sibling; it is still ONE cook, drawn on
    its cook day — whichever sibling's box was tapped, and un-ticking
    through any sibling clears it.
    """
    ids = _adults("Emily")
    _recipe()
    plan_id = _plan(days=DAYS[:3])
    conn = get_conn()
    conn.execute("UPDATE weekly_plans SET planning_mode = 'component_based' WHERE id = ?", (plan_id,))
    conn.commit()
    conn.close()
    tools.set_cooking_role("one_person", who="Emily")

    first = _entry(DAYS[0])

    _cooker.check_off_meal(_entry(DAYS[1]), "done")
    assert _week() == [{"id": f"cook:{first}", "kind": "cook", "date": DAYS[0],
                        "member_id": ids["Emily"]}]
    _cooker.check_off_meal(_entry(DAYS[2]), "pending")
    assert _rows() == []


# ---------- 2. the un-tick ----------

def test_unticking_a_cook_clears_its_credit():
    _adults("Emily")
    _recipe()
    _plan()
    tools.set_cooking_role("one_person", who="Emily")
    entry = _entry(DAYS[2])

    _moves.set_move_done(f"cook:{entry}", True)
    assert len(_rows()) == 1
    _moves.set_move_done(f"cook:{entry}", False)
    assert _rows() == []
    assert _week() == []


def test_unticking_or_skipping_a_fridge_move_clears_its_credit():
    _adults("Emily")
    _recipe()
    plan_id = _plan()
    tools.set_cooking_role("one_person", who="Emily")
    task = _task(plan_id, DAYS[0], _entry(DAYS[1]))

    _cooker.check_off_prep_step(task, "done")
    _cooker.check_off_prep_step(task, "pending")
    assert _rows() == []

    _cooker.check_off_prep_step(task, "done")
    _cooker.check_off_prep_step(task, "skipped")
    assert _rows() == [], "a declined fridge move is not work done"


# ---------- 3. the owner at the moment of the tick ----------

def test_an_owner_changed_after_planning_credits_the_owner_at_tick_time():
    ids = _adults("Emily", "Vineeth")
    _recipe()
    _plan()
    tools.set_cooking_role("one_person", who="Emily")
    entry = _entry(DAYS[2])
    drawn = {m["id"]: m for m in _moves.moves_for_day(DAYS[2])}[f"cook:{entry}"]
    assert drawn["owner"] == ids["Emily"]

    tools.set_cooking_role("one_person", who="Vineeth")
    _moves.set_move_done(f"cook:{entry}", True)

    assert [c["member_id"] for c in _week()] == [ids["Vineeth"]]


def test_a_credit_does_not_move_when_the_answer_changes_after_the_tick():
    ids = _adults("Emily", "Vineeth")
    _recipe()
    _plan()
    tools.set_cooking_role("one_person", who="Emily")
    entry = _entry(DAYS[2])
    _cooker.check_off_meal(entry, "done")

    tools.set_cooking_role("one_person", who="Vineeth")
    # A second 'done' on a done night (two panels, a double tap) is not a
    # second cook, and does not re-credit it to whoever owns it now.
    _cooker.check_off_meal(entry, "done")

    assert [c["member_id"] for c in _week()] == [ids["Emily"]]


def test_a_repeat_tick_on_a_done_fridge_move_keeps_the_first_credit():
    ids = _adults("Emily", "Vineeth")
    _recipe()
    plan_id = _plan()
    tools.set_cooking_role("one_person", who="Emily")
    task = _task(plan_id, DAYS[0], _entry(DAYS[1]))
    _cooker.check_off_prep_step(task, "done")
    tools.set_cooking_role("one_person", who="Vineeth")
    _cooker.check_off_prep_step(task, "done")
    assert [c["member_id"] for c in _week()] == [ids["Emily"]]


def test_a_credit_that_cannot_be_worked_out_never_costs_the_tick(monkeypatch):
    _adults("Emily")
    _recipe()
    _plan()
    tools.set_cooking_role("one_person", who="Emily")
    entry = _entry(DAYS[2])

    def boom(*a, **k):
        raise RuntimeError("view unreadable")

    monkeypatch.setattr(_moves, "moves_for_day", boom)
    _cooker.check_off_meal(entry, "done")

    conn = get_conn()
    status = conn.execute("SELECT cooked_status FROM meal_plan_entries WHERE id = ?", (entry,)).fetchone()[0]
    conn.close()
    assert status == "done"
    assert _rows() == []


# ---------- 4. the read ----------

def test_the_read_is_per_person_per_kind_and_per_week():
    ids = _adults("Emily", "Vineeth")
    _recipe()
    plan_id = _plan()
    tools.set_cooking_role("turns")
    for d in DAYS[:4]:
        _cooker.check_off_meal(_entry(d), "done")
    task = _task(plan_id, DAYS[0], _entry(DAYS[1]))
    _cooker.check_off_prep_step(task, "done")

    emily = _credits.credits(WEEK_START, member_id=ids["Emily"])
    vineeth = _credits.credits(WEEK_START, member_id=ids["Vineeth"])
    assert [c["date"] for c in emily] == [DAYS[0], DAYS[2]]
    # Oldest first by the move's own date: the fridge move is dated the day
    # the chicken moves, not the night it is for.
    assert [(c["kind"], c["date"]) for c in vineeth] == [("fridge", DAYS[0]), ("cook", DAYS[1]), ("cook", DAYS[3])]
    assert [c["id"] for c in _credits.credits(WEEK_START, kind="fridge")] == [f"fridge:{task}"]
    # A one-day "week" that starts on day 3 sees only day 3's cook.
    assert [c["date"] for c in _credits.credits(DAYS[3], days=1)] == [DAYS[3]]
    # The week after this one has none of them.
    assert _credits.credits(WEEK_START + datetime.timedelta(days=7)) == []


def test_a_credit_for_a_night_that_no_longer_exists_is_not_counted():
    _adults("Emily")
    _recipe()
    _plan()
    tools.set_cooking_role("one_person", who="Emily")
    entry = _entry(DAYS[2])
    _cooker.check_off_meal(entry, "done")
    conn = get_conn()
    conn.execute("DELETE FROM meal_plan_entries WHERE id = ?", (entry,))
    conn.commit()
    conn.close()
    assert _week() == []


def test_the_read_is_per_household():
    _adults("Emily")
    _recipe()
    _plan()
    tools.set_cooking_role("one_person", who="Emily")
    _cooker.check_off_meal(_entry(DAYS[2]), "done")

    other = households.create_household("The Others", "a long enough passphrase for others")
    with tools.use_household(other):
        assert _week() == []
        _adults("Sam")
        _recipe()
        _plan()
        tools.set_cooking_role("one_person", who="Sam")
        _cooker.check_off_meal(_entry(DAYS[2]), "done")
        assert len(_week()) == 1
    assert len(_week()) == 1
    assert {r["household_id"] for r in _rows()} == {tools.household_id(), other}
