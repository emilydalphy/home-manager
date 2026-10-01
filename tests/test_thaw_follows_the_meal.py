"""
A fridge move follows the meal it is for when the household swaps or moves
that meal by hand (Loop Board bug, 2026-10-01).

defrost.sync_defrost_tasks is the booking pass, and its own docstring has
listed "a swapped meal" among the moments it is safe to call since the day
it was written. Nothing called it there. Its only two callers were plan
generation and a manual prep-schedule regenerate (both in agent.py), so a
week's fridge moves were worked out ONCE, when the week was drafted, and
every hand change after that was judged against the plan as it had been.

Measured on origin/main 940df8b, both doors, through the real functions:

  a hand swap (swap_meal_in_plan: tomorrow's chili -> Chicken Skewers,
  whose thighs are in the freezer)
      prep_tasks                  nothing, before and after
      defrost_candidates_for_plan "Move the Chicken thighs to the fridge
                                   — for Friday's Chicken Skewers."
      i.e. the app knew what was owed and booked none of it, so Today, the
      morning text, the evening nudge and Cook's thaw list all said
      nothing about 2 lbs of frozen chicken due to be cooked tomorrow.

  a hand move (swap_dinner_nights: the same skewers pulled from +4 to +1)
      prep_tasks                  the row shifted onto YESTERDAY, which is
                                  the right answer
      get_defrost_today           []
      get_defrost_schedule        []
      Today's fridge moves        []
      the result                  prep_tasks_moved: 1, and no sentence

So the two halves of the fix. The sync is the recomputation; the sentence
(`thaw_now`, defrost.thaw_move_sentence) is the half a count cannot do —
every surface that shows a fridge move reads a calendar day, so a thaw
whose day has gone is a row that exists and that nothing can show, which
is indistinguishable from no reminder at all unless somebody says so.

WHAT WAS ALREADY TRUE, measured rather than assumed, and labelled here so
nobody reads this file as evidence for more than it is:

  * criterion 2 (a reminder for a meal that has left the plan is removed)
    was already met on the swap doors by weekly_plan._release_prep_rows
    (2026-09-22). The tests for it are GUARDs.
  * criterion 1 on the MOVE doors was already met for the row's DATE by
    weekly_plan._shift_defrost_tasks (2026-09-13), and that is not luck:
    defrost._move_date shifts linearly, so carrying a row by the same
    number of days the meal moved IS what recomputing gives. The sync
    added on those doors is defence in depth — it keeps the plan's rows
    equal to what the plan owes whatever else changed — and the shift
    still earns its keep, because it is what carries a DONE row's status
    across a move where a delete-and-reinsert would lose it.

Every test says in its own docstring whether it is a CATCH (red on
origin/main for the reason it is named after), a GUARD (green either way,
with the mutation that pins it named) or a NAME (red there only because a
symbol this card adds does not exist).

RED AGAINST MAIN, measured in a git archive of origin/main with this file
copied into it, and decomposed rather than quoted: 16 failed / 15 passed.
Of the 16,

  * TEN fail on the assertion they are named for — five on the booking
    half (nothing was booked at all), two on the missing `thaw_now` key
    (the nights swap and the night off), the orphaned reheat's own
    sentence, the move sheet's merged line, and the surfaces test on the
    first surface it checks;
  * ONE is a source marker (the shared wording), genuinely red because
    meal_move._thaw_notes kept its own f-strings there;
  * ONE is a NAME (defrost.thaw_move_sentence);
  * FOUR are red for a reason other than the one they are named after and
    say so in their own docstrings — three die at a PREMISE with an
    IndexError, reading [0] of a list of booked rows that is empty there,
    and one because the pass it watches is not there to be watched.

So the honest count is ten behaviour catches plus a source marker. The
fifteen that pass either way are the guards, and each names the mutation
that pins it; those mutations are the rest of the evidence and were run.

That number was 14 of 27 when it was first measured, and four tests landed
after it while the mutations were being re-aimed (see
test_a_swap_that_keeps_the_same_dish and its three neighbours below). It
is re-measured here rather than carried forward, because a red count taken
before the file was finished is a count of a different file.
"""
from __future__ import annotations

import datetime
import json
import os
import sqlite3

import pytest
from freezegun import freeze_time

from app import households, tools
from app.db import get_conn
from app.tools import (
    cooker as ck,
    defrost as df,
    digest,
    freezer_portions as fp,
    leftovers,
    meal_move,
    moves,
    prep_sessions,
    tonight,
    weekly_plan as wp,
)

from conftest import household_today


# ---------- helpers ----------

def _d(offset: int) -> str:
    return (household_today() + datetime.timedelta(days=offset)).isoformat()


def _defrost_rows(plan_id: int | None = None) -> list[dict]:
    conn = get_conn()
    sql = ("SELECT id, task_date, description, quantity, status, meal_plan_entry_id, "
           "inventory_item_id, detail_json, weekly_plan_id FROM prep_tasks "
           "WHERE task_type = 'defrost'")
    args: tuple = ()
    if plan_id is not None:
        sql += " AND weekly_plan_id = ?"
        args = (plan_id,)
    rows = conn.execute(sql + " ORDER BY id", args).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def _said(plan_id: int | None = None) -> list[str]:
    return [r["description"] for r in _defrost_rows(plan_id)]


def _entry(plan_id: int, day: str, slot: str = "dinner") -> dict:
    conn = get_conn()
    row = conn.execute(
        "SELECT * FROM meal_plan_entries WHERE weekly_plan_id = ? AND date = ? AND slot = ? ORDER BY id",
        (plan_id, day, slot),
    ).fetchone()
    conn.close()
    return dict(row)


def _recipe(name: str, item: str, qty: str = "2 lbs", category: str = "meat") -> None:
    tools.add_recipe(name, ingredients=[{"item": item, "qty": qty, "category": category}],
                     prep_time_minutes=10, cook_time_minutes=20, default_servings=2)


@pytest.fixture
def household():
    tools.add_member("Emily")
    tools.set_member_age_group("Emily", "adult")
    tools.add_member("Vineeth")
    tools.set_member_age_group("Vineeth", "adult")
    # A real dinner hour, so _move_date does clock arithmetic rather than
    # whole-day counting — the shape a household that answered the rhythm
    # question has, and the one these dates are computed against.
    tools.set_dinner_window("6_8")
    tools.set_prep_days(["sunday", "monday", "tuesday", "wednesday", "thursday",
                         "friday", "saturday"])


@pytest.fixture
def frozen_kitchen(household):
    """Two frozen meats and three dishes: one for each meat, one for
    neither. Everything a swap between them needs."""
    _recipe("Chicken Skewers", "Chicken thighs")
    _recipe("Beef Stew", "Beef chuck")
    _recipe("Bean Chili", "Black beans", qty="1 can", category="pantry")
    tools.update_inventory("Chicken thighs", "add", quantity="2 lbs",
                           category="meat", location="freezer")
    tools.update_inventory("Beef chuck", "add", quantity="2 lbs",
                           category="meat", location="freezer")


def _plan(*meals: tuple[str, str], start: str | None = None, slot: str = "dinner") -> int:
    """A plan with its booking pass already run, the way a drafted week
    arrives: agent._sync_defrost_tasks_if_needed is what does this for a
    real generation."""
    plan_id = tools.create_weekly_plan(start or _d(0))["weekly_plan_id"]
    for day, meal in meals:
        tools.plan_meal(day, meal, slot=slot, weekly_plan_id=plan_id)
    tools.sync_defrost_tasks(plan_id)
    return plan_id


# ======================================================================
# 1. The hand swap books what the new dish needs (criterion 1)
# ======================================================================

def test_a_swap_onto_a_frozen_dish_books_its_fridge_move(frozen_kitchen):
    """
    CATCH — the reported bug. Swapping a dinner for a dish whose meat is
    in the freezer booked nothing at all on main: the week's fridge moves
    had been worked out when the week was drafted, and the dish that
    arrived afterwards was never asked about.
    """
    plan_id = _plan((_d(5), "Bean Chili"))
    assert _defrost_rows(plan_id) == [], "premise: a chili needs nothing out of the freezer"

    tools.swap_meal_in_plan(plan_id, _d(5), "Chicken Skewers", slot="dinner")

    rows = _defrost_rows(plan_id)
    assert len(rows) == 1, rows
    assert rows[0]["description"].startswith("Move the Chicken thighs to the fridge")
    assert rows[0]["status"] == "pending"
    assert rows[0]["meal_plan_entry_id"] == _entry(plan_id, _d(5))["id"]


def test_the_booked_move_is_the_one_the_plan_now_owes(frozen_kitchen):
    """
    CATCH. The whole point of recomputing rather than patching: after the
    swap the plan's rows are exactly what defrost_candidates_for_plan says
    the plan owes — same day, same amount, same sentence. On main the rows
    were empty while the candidates named the move.
    """
    plan_id = _plan((_d(5), "Bean Chili"))
    tools.swap_meal_in_plan(plan_id, _d(5), "Chicken Skewers", slot="dinner")

    owed = {(c["task_date"], c["quantity"], c["description"])
            for c in df.defrost_candidates_for_plan(plan_id)}
    booked = {(r["task_date"], r["quantity"], r["description"]) for r in _defrost_rows(plan_id)}
    assert owed and booked == owed


def test_it_books_for_the_dish_that_is_there_now_not_the_one_that_left(frozen_kitchen):
    """
    CATCH. Two frozen meats, and the swap trades one dish for the other:
    exactly one move afterwards, and it names the beef. On main the
    chicken's move stayed and the beef never got one — the reminder for a
    dinner nobody was cooking, and none for the one they were.
    """
    plan_id = _plan((_d(5), "Chicken Skewers"))
    assert _said(plan_id) == ["Move the Chicken thighs to the fridge — "
                              f"for {df._weekday_name(_d(5))}'s Chicken Skewers."]

    tools.swap_meal_in_plan(plan_id, _d(5), "Beef Stew", slot="dinner")

    assert _said(plan_id) == ["Move the Beef chuck to the fridge — "
                              f"for {df._weekday_name(_d(5))}'s Beef Stew."]


def test_a_multi_day_swap_books_it_too(frozen_kitchen):
    """
    CATCH — weekly_plan.replace_dish_on_days, the write behind a Swap
    tapped on a multi-day row of "What we're eating". The other write that
    releases a meal's prep rows, so without the same post-commit pass it
    had the same hole.
    """
    plan_id = _plan((_d(4), "Bean Chili"), (_d(5), "Bean Chili"))
    assert _defrost_rows(plan_id) == []

    wp.replace_dish_on_days(plan_id, [
        {"old_entry_id": _entry(plan_id, day)["id"], "date": day, "slot": "dinner",
         "new_meal": "Chicken Skewers"}
        for day in (_d(4), _d(5))
    ])

    rows = _defrost_rows(plan_id)
    assert len(rows) == 2, rows
    assert {r["task_date"] for r in rows} == {
        df._move_date(_d(4), df.lead_hours_for_item("Chicken thighs")[0], "6_8"),
        df._move_date(_d(5), df.lead_hours_for_item("Chicken thighs")[0], "6_8"),
    }


def test_a_night_that_was_reheating_the_swapped_out_cook_gets_its_own_move(frozen_kitchen):
    """
    CATCH. Swapping a chain SOURCE away leaves the nights it fed as
    ordinary cooks of their own dish (_replace_slot_entries clears their
    links_to and _reingest_unlinked_entries buys for them) — so a night
    whose own recipe calls for something frozen is owed a fridge move it
    never had, attached to an entry the swap did not touch. That is why
    the pass is told about the orphaned reheats as well as the new meal.
    """
    plan_id = tools.create_weekly_plan(_d(0))["weekly_plan_id"]
    tools.plan_meal(_d(3), "Bean Chili", slot="dinner", weekly_plan_id=plan_id)
    tools.plan_meal(_d(5), "Chicken Skewers", slot="dinner", weekly_plan_id=plan_id)
    cook, reheat = _entry(plan_id, _d(3)), _entry(plan_id, _d(5))
    conn = get_conn()
    conn.execute("UPDATE meal_plan_entries SET derived_from_json = ? WHERE id = ?",
                 (json.dumps({"make_double_for": [f"{_d(5)}:dinner"]}), cook["id"]))
    conn.execute("UPDATE meal_plan_entries SET derived_from_json = ? WHERE id = ?",
                 (json.dumps({"links_to": f"{_d(3)}:dinner"}), reheat["id"]))
    conn.commit()
    conn.close()
    tools.sync_defrost_tasks(plan_id)
    assert _defrost_rows(plan_id) == [], \
        "premise: a reheat night buys and thaws nothing while the chain holds"

    tools.swap_meal_in_plan(plan_id, _d(3), "Bean Chili", slot="dinner", old_meal="Bean Chili")

    rows = _defrost_rows(plan_id)
    assert len(rows) == 1, rows
    assert rows[0]["meal_plan_entry_id"] == reheat["id"]
    assert rows[0]["description"].startswith("Move the Chicken thighs to the fridge")


def test_a_swap_still_takes_the_old_meals_fridge_move_with_it(frozen_kitchen):
    """
    GUARD — criterion 2, and it was already met: _release_prep_rows
    (2026-09-22) deletes the outgoing meal's prep rows inside the swap's
    own transaction. Here so the recomputation cannot be read as what
    does it.

    PINNED BY NOTHING ON ITS OWN, and that is measured rather than
    assumed: with _release_prep_rows made a no-op, every one of the 27
    tests this file held when that was first run still passed, because
    sync_defrost_tasks' own sweep then removes the same row. The two
    halves are told apart by its sibling below, which uses a row the
    sweep deliberately never touches.
    """
    plan_id = _plan((_d(5), "Chicken Skewers"))
    assert len(_defrost_rows(plan_id)) == 1

    tools.swap_meal_in_plan(plan_id, _d(5), "Bean Chili", slot="dinner")

    assert _defrost_rows(plan_id) == []


def test_a_confirmed_freezer_item_goes_with_the_meal_it_was_confirmed_for(household):
    """
    GUARD, and the one that really does pin _release_prep_rows.
    confirm_frozen_items' rows carry no inventory row at all, which is
    exactly the shape sync_defrost_tasks is scoped never to sweep — so the
    recomputation cannot stand in for the release here, and only the
    release can take this row off a dinner that has left the plan.

    Mutation that pins it: make _release_prep_rows a no-op in
    _replace_slot_entries (`held_thawed = []`), or scope its DELETE to
    `inventory_item_id IS NOT NULL`.
    """
    _recipe("Roast Chicken", "Whole chicken", qty="1")
    _recipe("Bean Chili", "Black beans", qty="1 can", category="pantry")
    plan_id = tools.create_weekly_plan(_d(0))["weekly_plan_id"]
    tools.plan_meal(_d(5), "Roast Chicken", slot="dinner", weekly_plan_id=plan_id)
    tools.approve_weekly_plan(plan_id, approved_by="Emily")
    for item in tools.list_grocery_list():
        tools.mark_grocery_item(item["id"], "purchased")
    offered = [o for o in tools.meat_items_for_plan(plan_id) if "chicken" in o["item"].lower()]
    assert offered, "premise: the freezer step offers the chicken"
    tools.confirm_frozen_items(plan_id, [offered[0]["item"]])
    rows = _defrost_rows(plan_id)
    assert len(rows) == 1 and rows[0]["inventory_item_id"] is None, \
        "premise: a confirmed freezer item is the kind the sweep never touches"

    tools.swap_meal_in_plan(plan_id, _d(5), "Bean Chili", slot="dinner")

    assert _defrost_rows(plan_id) == []


def test_a_household_with_nothing_frozen_is_untouched_by_any_of_this(household):
    """
    GUARD. Most households track no freezer item at all (defrost.py's own
    module docstring), so the overwhelmingly common swap must write
    nothing and say nothing.

    Mutation that pins it: drop the `if said` guards at the two call
    sites, so `thaw_now` is always present.
    """
    _recipe("Bean Chili", "Black beans", qty="1 can", category="pantry")
    _recipe("Lentil Soup", "Lentils", qty="1 cup", category="pantry")
    plan_id = _plan((_d(5), "Bean Chili"))

    result = tools.swap_meal_in_plan(plan_id, _d(5), "Lentil Soup", slot="dinner")

    assert _defrost_rows(plan_id) == []
    assert "thaw_now" not in result


def test_it_says_nothing_about_a_thaw_this_swap_had_nothing_to_do_with(frozen_kitchen):
    """
    GUARD. A thaw already overdue on ANOTHER night is true whether or not
    this swap caused it, and repeating it on every tap turns an urgent
    sentence into furniture — "on the spot" means about the thing just
    done. So the sentences are scoped to the meals the change touched.

    Mutation that pins it: have resync_plan_thaws report every pending row
    on the plan rather than only the named meals'.
    """
    _recipe("Lentil Soup", "Lentils", qty="1 cup", category="pantry")
    plan_id = _plan((_d(1), "Chicken Skewers"), (_d(5), "Bean Chili"))
    overdue = _defrost_rows(plan_id)
    assert len(overdue) == 1 and overdue[0]["task_date"] < household_today().isoformat(), \
        "premise: a thaw on another night is already overdue"

    result = tools.swap_meal_in_plan(plan_id, _d(5), "Lentil Soup", slot="dinner")

    assert "thaw_now" not in result


# ======================================================================
# 2. When the thaw has already started, somebody says so (criterion 3)
# ======================================================================

def test_a_swap_whose_thaw_has_already_gone_says_so(frozen_kitchen):
    """
    CATCH. A 48-hour thaw for a dinner TOMORROW is due the day before
    today, and every surface that shows a fridge move reads a calendar
    day — so the row exists and nothing can draw it. The swap's own result
    is the only place left to say it.

    On main it dies one line EARLIER than its own claim, at the premise:
    there is no row at all to read a date off. Said so rather than
    counted as evidence for the sentence.
    """
    plan_id = _plan((_d(1), "Bean Chili"))

    result = tools.swap_meal_in_plan(plan_id, _d(1), "Chicken Skewers", slot="dinner")

    row = _defrost_rows(plan_id)[0]
    assert row["task_date"] < household_today().isoformat(), \
        "premise: the thaw really is due on a day that has gone"
    assert result["thaw_now"] == ["Move the Chicken thighs to the fridge now."]


def test_a_swap_whose_thaw_is_due_today_says_today(frozen_kitchen):
    """
    CATCH. The other half of the sentence: a move still possible, now.
    Like its sibling above, on main it dies at the premise rather than at
    its own claim — there is no row to read a date off.
    """
    plan_id = _plan((_d(2), "Bean Chili"))

    result = tools.swap_meal_in_plan(plan_id, _d(2), "Chicken Skewers", slot="dinner")

    row = _defrost_rows(plan_id)[0]
    assert row["task_date"] == household_today().isoformat(), "premise: due today"
    assert result["thaw_now"] == ["Move the Chicken thighs to the fridge today."]


def test_a_swap_whose_thaw_is_still_ahead_says_nothing(frozen_kitchen):
    """
    GUARD on the branch's own behaviour, and RED on main for a reason
    that is not its claim: there is no row there to be silent about, so it
    dies at the premise. The claim — a thaw still ahead is not announced —
    is a thing only this branch can get wrong.

    Mutation that pins it: drop the `AND task_date <= ?` clause from
    resync_plan_thaws' query.
    """
    plan_id = _plan((_d(6), "Bean Chili"))

    result = tools.swap_meal_in_plan(plan_id, _d(6), "Chicken Skewers", slot="dinner")

    row = _defrost_rows(plan_id)[0]
    assert row["task_date"] > household_today().isoformat(), "premise: still ahead"
    assert "thaw_now" not in result


def test_a_nights_swap_that_pulls_a_frozen_cook_forward_says_so(frozen_kitchen):
    """
    CATCH — the second half of the reported bug. _shift_defrost_tasks
    carries the row onto the right day and that day has gone, so Today,
    the morning text, the evening nudge and Cook's thaw list all show
    nothing, under a result that said only `prep_tasks_moved: 1`.
    """
    plan_id = _plan((_d(4), "Chicken Skewers"), (_d(1), "Bean Chili"))

    result = tools.swap_dinner_nights(plan_id, _d(4), _d(1))

    assert result["status"] == "swapped" and result["prep_tasks_moved"] == 1
    assert _defrost_rows(plan_id)[0]["task_date"] < household_today().isoformat(), \
        "premise: the shift put the move on a day that has already gone"
    # …so no surface can draw it. The sentence is the only thing left.
    assert [m for m in moves.today_moves(
        day=household_today().isoformat(),
        now=datetime.datetime.combine(household_today(), datetime.time(9, 0)),
    )["moves"] if m["kind"] == "fridge"] == []
    assert df.get_defrost_today() == [] and df.get_defrost_schedule() == []
    assert result["thaw_now"] == ["Move the Chicken thighs to the fridge now."]


def test_a_nights_swap_that_pushes_a_frozen_cook_later_says_nothing(frozen_kitchen):
    """
    GUARD. The mirror of the catch above: moving a frozen dinner further
    out moves its thaw further out too, and that one shows on its own day.

    Mutation that pins it: drop the `AND task_date <= ?` clause from
    resync_plan_thaws' query.
    """
    plan_id = _plan((_d(3), "Chicken Skewers"), (_d(6), "Bean Chili"))

    result = tools.swap_dinner_nights(plan_id, _d(3), _d(6))

    assert result["status"] == "swapped"
    assert "thaw_now" not in result


def test_a_move_already_ticked_done_is_never_said_again(frozen_kitchen):
    """
    GUARD. A fridge move the household has ticked is food already in the
    fridge; "move the chicken now" about it is the app not reading its own
    records.

    Mutation that pins it: drop `status = 'pending'` from
    resync_plan_thaws' query.
    """
    plan_id = _plan((_d(4), "Chicken Skewers"))
    row = _defrost_rows(plan_id)[0]
    tools.check_off_prep_step(row["id"], "done")

    result = tools.swap_dinner_nights(plan_id, _d(4), _d(1))

    assert result["status"] == "swapped"
    assert _defrost_rows(plan_id)[0]["status"] == "done", \
        "premise: the shift kept the tick (that is what _shift_defrost_tasks is for)"
    assert "thaw_now" not in result


def test_the_orphaned_reheats_own_overdue_thaw_is_said_too(frozen_kitchen):
    """
    CATCH. Swapping a chain SOURCE away makes the night it fed an ordinary
    cook of its own dish, so a frozen ingredient of that dish is owed a
    fridge move — on an entry the swap never named. Saying nothing about
    it would be the honest half of the fix stopping one entry short.

    This is also the test that pins the orphan ids being passed to the
    pass at all: with `orphaned_reheats` dropped from
    _replace_slot_entries' call, the ROW is still booked (the sync is
    plan-wide) and the SENTENCE is not.
    """
    plan_id = tools.create_weekly_plan(_d(0))["weekly_plan_id"]
    tools.plan_meal(_d(0), "Bean Chili", slot="dinner", weekly_plan_id=plan_id)
    tools.plan_meal(_d(2), "Chicken Skewers", slot="dinner", weekly_plan_id=plan_id)
    cook, reheat = _entry(plan_id, _d(0)), _entry(plan_id, _d(2))
    conn = get_conn()
    conn.execute("UPDATE meal_plan_entries SET derived_from_json = ? WHERE id = ?",
                 (json.dumps({"make_double_for": [f"{_d(2)}:dinner"]}), cook["id"]))
    conn.execute("UPDATE meal_plan_entries SET derived_from_json = ? WHERE id = ?",
                 (json.dumps({"links_to": f"{_d(0)}:dinner"}), reheat["id"]))
    conn.commit()
    conn.close()
    tools.sync_defrost_tasks(plan_id)
    assert _defrost_rows(plan_id) == [], "premise: a reheat night thaws nothing"

    result = tools.swap_meal_in_plan(plan_id, _d(0), "Beef Stew", slot="dinner")

    rows = _defrost_rows(plan_id)
    assert {r["meal_plan_entry_id"] for r in rows} >= {reheat["id"]}, rows
    assert "Move the Chicken thighs to the fridge today." in result["thaw_now"]


def test_the_move_sheet_says_a_thaw_that_is_still_overdue_after_the_move(frozen_kitchen):
    """
    CATCH on the merged half of meal_move's thaw line. Its own
    before-and-after rule only speaks when a thaw moved EARLIER, so a
    thaw that was already overdue and moved LATER — still overdue — said
    nothing: the diff sees `to > from` and stops. The post-commit pass
    reads the STATE instead, and the two are merged into the one field
    shell.js draws.

    Mutation that pins it: drop the `also` loop from _thaw_notes, or stop
    passing still_owed to it in move_meal.
    """
    _recipe("Lentil Soup", "Lentils", qty="1 cup", category="pantry")
    plan_id = _plan((_d(1), "Chicken Skewers"), (_d(2), "Lentil Soup"))
    row = _defrost_rows(plan_id)[0]
    assert row["task_date"] < household_today().isoformat(), \
        "premise: the thaw is already overdue"

    out = meal_move.move_meal(plan_id, _entry(plan_id, _d(1))["id"], _d(2))

    assert out["status"] == "moved", out
    moved = _defrost_rows(plan_id)[0]
    assert moved["task_date"] > row["task_date"], \
        "premise: the move pushed the thaw LATER, so the diff rule says nothing"
    assert moved["task_date"] <= household_today().isoformat(), \
        "premise: ...and it is still today or earlier"
    assert out["thaw_notes"] == ["Move the Chicken thighs to the fridge today."]


def test_the_sentence_names_the_move_and_never_the_meals_own_weekday():
    """
    NAME — defrost.thaw_move_sentence. A row's description is written
    "<the move> — <what it's for>.", and the tail carries the MEAL's
    weekday. A sentence about when to move something must not also carry
    a different day, or it names two.
    """
    desc = "Move the Chicken thighs to the fridge — for Thursday's Chicken Skewers."
    assert df.thaw_move_sentence(desc, "2026-10-01", "2026-10-01") == \
        "Move the Chicken thighs to the fridge today."
    assert df.thaw_move_sentence(desc, "2026-09-30", "2026-10-01") == \
        "Move the Chicken thighs to the fridge now."
    assert df.thaw_move_sentence(desc, "2026-10-02", "2026-10-01") == \
        "Move the Chicken thighs to the fridge on Friday."
    assert df.thaw_move_sentence("", "2026-10-01", "2026-10-01") is None


def test_the_move_sheet_and_the_swap_doors_share_one_wording():
    """
    GUARD. meal_move._thaw_notes had this wording first and now reads it
    from defrost, so the Move sheet's toast and the swap doors' lines
    cannot come to say different things about the same chicken.

    Mutation that pins it: give _thaw_notes its own f-strings back.
    """
    import inspect
    body = inspect.getsource(meal_move._thaw_notes)
    assert "thaw_move_sentence" in body
    assert 'f"{what} now."' not in body and 'f"{what} today."' not in body


# ======================================================================
# 3. The move doors: recomputed, not only shifted (criterion 1)
# ======================================================================

def test_after_a_nights_swap_the_plans_rows_are_what_the_plan_owes(frozen_kitchen):
    """
    GUARD, and the reason it is a guard rather than a catch is worth
    reading: _shift_defrost_tasks already put the row on the right day,
    and that is not luck — defrost._move_date shifts linearly, so
    carrying a row by the number of days the meal moved IS what
    recomputing gives. What the pass adds here is the invariant, held
    whatever else about the week changed.

    Mutation that pins it: make _shift_defrost_tasks a no-op (return 0
    without updating). The sync then has to do the whole job, and this
    test is what says it did.
    """
    plan_id = _plan((_d(5), "Chicken Skewers"), (_d(3), "Bean Chili"))

    tools.swap_dinner_nights(plan_id, _d(5), _d(3))

    owed = {(c["task_date"], c["description"]) for c in df.defrost_candidates_for_plan(plan_id)}
    booked = {(r["task_date"], r["description"]) for r in _defrost_rows(plan_id)}
    assert owed and booked == owed


def test_the_move_sheet_recomputes_the_weeks_fridge_moves_too(frozen_kitchen):
    """
    GUARD — meal_move.move_meal, through _after_move. Same reasoning as
    the nights swap above: the date was already right, this holds the
    invariant.

    Mutation that pins it: have _after_move return [] without calling
    defrost.resync_plan_thaws, then make _redate_plan_rows' call to
    _shift_defrost_tasks a no-op.
    """
    plan_id = _plan((_d(5), "Chicken Skewers"), (_d(3), "Bean Chili"))
    entry_id = _entry(plan_id, _d(5))["id"]

    out = meal_move.move_meal(plan_id, entry_id, _d(3))

    assert out["status"] == "moved", out
    owed = {(c["task_date"], c["description"]) for c in df.defrost_candidates_for_plan(plan_id)}
    booked = {(r["task_date"], r["description"]) for r in _defrost_rows(plan_id)}
    assert owed and booked == owed


def test_the_move_sheet_still_says_a_thaw_it_pulled_earlier(frozen_kitchen):
    """
    GUARD. meal_move's `thaw_notes` is the Move sheet's own line (shell.js
    reads it and nothing else), built from the before-and-after of the
    shift; the post-commit pass's answer is MERGED into it rather than
    added as a second field. Both halves must survive.

    Mutation that pins it: drop the `also` argument from _thaw_notes, or
    stop passing still_owed to it in move_meal.
    """
    plan_id = _plan((_d(5), "Chicken Skewers"), (_d(2), "Bean Chili"))
    entry_id = _entry(plan_id, _d(5))["id"]

    out = meal_move.move_meal(plan_id, entry_id, _d(2))

    assert out["status"] == "moved", out
    assert out["thaw_notes"] == ["Move the Chicken thighs to the fridge today."]


def test_a_night_off_that_moves_the_dish_recomputes_and_says_so(frozen_kitchen):
    """
    CATCH. "Not tonight — we're going out" moves tonight's dinner through
    the very swap_dinner_nights the Plan tiles use, and
    _apply_dinner_nights_swap handed a connection deliberately leaves
    every read to its caller — so on main nothing recomputed the week's
    thaws and nothing said the chicken's move had already come and gone.
    """
    plan_id = _plan((_d(0), "Chicken Skewers"), (_d(3), "Bean Chili"))
    assert len(_defrost_rows(plan_id)) == 1, "premise: tonight's skewers have a fridge move"

    result = tonight.tonight_night_off(day=_d(0))

    assert result["status"] == "night_off" and result["kind"] == "move"
    owed = {(c["task_date"], c["description"]) for c in df.defrost_candidates_for_plan(plan_id)}
    booked = {(r["task_date"], r["description"]) for r in _defrost_rows(plan_id)}
    assert booked == owed
    assert result["thaw_now"] == ["Move the Chicken thighs to the fridge now."]


# ======================================================================
# 4. The portion kinds — a week's own cook, and a night off's
# ======================================================================

def _own_portion_week():
    """Monday's lunch cooks a portion extra for the freezer and Saturday's
    lunch eats it — what weekly_plan._freeze_instead and the weekday-lunch
    count write (leftovers.FROM_FREEZER_KEY with a "cook" ref). Built the
    way tests/test_own_freezer_portion_thaw.py builds it."""
    for name in ("Soup", "Pasta", "Stew"):
        _recipe(name, f"{name} base", qty="1 lb", category="pantry")
    start = household_today() + datetime.timedelta(days=(7 - household_today().weekday()))
    days = [(start + datetime.timedelta(days=i)).isoformat() for i in range(7)]
    plan_id = tools.create_weekly_plan(days[0])["weekly_plan_id"]
    tools.plan_meal(days[0], "Soup", slot="lunch", weekly_plan_id=plan_id)
    tools.plan_meal(days[5], "Pasta", slot="lunch", weekly_plan_id=plan_id)
    cook, sat = _entry(plan_id, days[0], "lunch"), _entry(plan_id, days[5], "lunch")
    conn = get_conn()
    wp.freeze_a_portion(conn, cook["id"], days[5], "lunch")
    conn.execute(
        "UPDATE meal_plan_entries SET recipe_id = NULL, freeform_meal = ?, derived_from_json = ? "
        "WHERE id = ?",
        (leftovers.freezer_night_name("Soup", days[0]),
         json.dumps({leftovers.FROM_FREEZER_KEY: {"cook": f"entry_id:{cook['id']}", "dish": "Soup"}}),
         sat["id"]),
    )
    conn.commit()
    conn.close()
    tools.sync_defrost_tasks(plan_id)
    return plan_id, days, sat["id"]


def test_swapping_the_meal_that_eats_the_weeks_own_frozen_portion_takes_its_move(household):
    """
    GUARD — criterion 2 for the portion kind the 2026-09-30 leftovers card
    added (defrost.OWN_PORTION_KIND, no inventory row at all). Already met
    by _release_prep_rows, which is keyed by meal_plan_entry_id and so
    covers every kind of defrost row at once.

    Mutation that pins it: scope _release_prep_rows' DELETE to
    `inventory_item_id IS NOT NULL`.
    """
    plan_id, days, _sat_id = _own_portion_week()
    assert len(_defrost_rows(plan_id)) == 1, "premise: the frozen lunch has a fridge move"

    tools.swap_meal_in_plan(plan_id, days[5], "Stew", slot="lunch")

    assert _defrost_rows(plan_id) == []


def test_moving_a_night_off_portions_night_recomputes_its_move(household):
    """
    GUARD — the other portion kind (freezer_portions.KEY, an inventory
    row a night off wrote). A nights swap carries its reminder, and the
    recomputation holds the invariant.

    Mutation that pins it: make _shift_defrost_tasks a no-op.
    """
    for name in ("Soup", "Pasta", "Stew"):
        _recipe(name, f"{name} base", qty="1 lb", category="pantry")
    plan_id = tools.create_weekly_plan(_d(0))["weekly_plan_id"]
    for day, name in ((_d(2), "Pasta"), (_d(5), "Stew")):
        tools.plan_meal(day, name, slot="dinner", weekly_plan_id=plan_id)
    added = tools.update_inventory("Soup (cooked)", "add", quantity="2 servings",
                                   category="frozen", location="freezer")
    row = _entry(plan_id, _d(5))
    conn = get_conn()
    conn.execute(
        "UPDATE meal_plan_entries SET recipe_id = NULL, freeform_meal = ?, derived_from_json = ? "
        "WHERE id = ?",
        (leftovers.frozen_portion_night_name("Soup"),
         json.dumps({fp.KEY: {"inventory_item_id": added["item_id"], "dish": "Soup"}}),
         row["id"]),
    )
    conn.commit()
    conn.close()
    tools.sync_defrost_tasks(plan_id)
    assert len(_defrost_rows(plan_id)) == 1, "premise: the portion's night has a fridge move"

    tools.swap_dinner_nights(plan_id, _d(5), _d(2))

    owed = {(c["task_date"], c["description"]) for c in df.defrost_candidates_for_plan(plan_id)}
    booked = {(r["task_date"], r["description"]) for r in _defrost_rows(plan_id)}
    assert owed and booked == owed


# ======================================================================
# 5. The pass sweeps only its own rows (the trap sync's docstring names)
# ======================================================================

def test_a_household_confirmed_freezer_item_survives_a_swap_elsewhere(household):
    """
    GUARD, and the one worth having most. confirm_frozen_items' rows carry
    no inventory row (inventory is deferred policy) and sync_defrost_tasks
    is scoped to exclude them — the exact bug independent review caught
    twice in that function's own history, now reachable from a new caller.

    Mutation that pins it: drop `AND inventory_item_id IS NOT NULL` from
    sync_defrost_tasks' `existing` query.
    """
    _recipe("Roast Chicken", "Whole chicken", qty="1")
    _recipe("Bean Chili", "Black beans", qty="1 can", category="pantry")
    _recipe("Lentil Soup", "Lentils", qty="1 cup", category="pantry")
    plan_id = tools.create_weekly_plan(_d(0))["weekly_plan_id"]
    tools.plan_meal(_d(5), "Roast Chicken", slot="dinner", weekly_plan_id=plan_id)
    tools.plan_meal(_d(3), "Bean Chili", slot="dinner", weekly_plan_id=plan_id)
    tools.approve_weekly_plan(plan_id, approved_by="Emily")
    for item in tools.list_grocery_list():
        tools.mark_grocery_item(item["id"], "purchased")
    offered = [o for o in tools.meat_items_for_plan(plan_id) if "chicken" in o["item"].lower()]
    assert offered, "premise: the freezer step offers the chicken"
    tools.confirm_frozen_items(plan_id, [offered[0]["item"]])
    assert len(_defrost_rows(plan_id)) == 1

    tools.swap_meal_in_plan(plan_id, _d(3), "Lentil Soup", slot="dinner")

    rows = _defrost_rows(plan_id)
    assert len(rows) == 1, rows
    assert rows[0]["description"].startswith("Move the Whole chicken to the fridge")


def test_a_confirmed_ready_made_defrost_survives_a_swap_elsewhere(frozen_kitchen):
    """
    GUARD — the other producer sync_defrost_tasks is scoped around
    (defrost_task_from_ready_made's rows have no meal_plan_entry_id).

    Mutation that pins it: drop `AND meal_plan_entry_id IS NOT NULL` from
    sync_defrost_tasks' `existing` query.
    """
    plan_id = _plan((_d(4), "Bean Chili"), (_d(2), "Bean Chili"))
    tools.set_slot_need(_d(6), "dinner", "ready_made")
    conn = get_conn()
    conn.execute(
        "UPDATE slot_needs SET recommended_defrost_item = 'Chicken thighs', "
        "recommendation_confirmed = 1 WHERE date = ? AND slot = 'dinner'",
        (_d(6),),
    )
    conn.commit()
    conn.close()
    booked = df.defrost_task_from_ready_made(_d(6), "dinner")
    assert booked, "premise: the ready-made recommendation booked a move"

    tools.swap_meal_in_plan(plan_id, _d(2), "Beef Stew", slot="dinner")

    ids = {r["id"] for r in _defrost_rows()}
    assert booked["prep_task_id"] in ids


def test_another_households_fridge_moves_are_left_alone(frozen_kitchen):
    """
    GUARD. The recomputation is a WRITE reached from a new place, and
    every statement under it is household-scoped (household_id()).

    Mutation that pins it: drop `AND household_id = ?` from
    sync_defrost_tasks' `existing` query.

    Dropping it from resync_plan_thaws' OWN query reddens nothing, and
    that is measured rather than overlooked: that query is already scoped
    by weekly_plan_id, and a plan belongs to exactly one household, so
    the household clause there is defence in depth with nothing
    behavioural behind it. Said rather than claimed as coverage.
    """
    other = households.create_household("Next door", "another-passphrase-entirely")
    with tools.use_household(other):
        tools.add_member("Sam")
        tools.set_member_age_group("Sam", "adult")
        tools.set_dinner_window("6_8")
        _recipe("Chicken Skewers", "Chicken thighs")
        tools.update_inventory("Chicken thighs", "add", quantity="2 lbs",
                               category="meat", location="freezer")
        theirs = _plan((_d(4), "Chicken Skewers"))
        before = [(r["task_date"], r["description"]) for r in _defrost_rows(theirs)]
    assert len(before) == 1, "premise: the other household has a fridge move of its own"

    mine = _plan((_d(4), "Chicken Skewers"))
    tools.swap_meal_in_plan(mine, _d(4), "Beef Stew", slot="dinner")

    with tools.use_household(other):
        after = [(r["task_date"], r["description"]) for r in _defrost_rows(theirs)]
    assert after == before


# ======================================================================
# 6. Criterion 4 — the four surfaces, once the row exists
# ======================================================================

def test_the_booked_move_shows_on_every_surface_a_drafted_weeks_does(frozen_kitchen):
    """
    CATCH. The card's fourth criterion. Nothing here is new code — all
    four surfaces read prep_tasks rows — so this is a verification rather
    than a mechanism, and on main it fails on the first one because the
    swap booked no row at all.
    """
    plan_id = tools.create_weekly_plan(_d(0))["weekly_plan_id"]
    # A distinct dinner on every other night, so whichever day the move
    # lands on has an uncooked cook for the evening nudge to attach its
    # "move the chicken first" line to (a repeated dish would fold into a
    # batch and make that night a reheat).
    for i in range(7):
        if i == 5:
            continue
        _recipe(f"Filler {i}", f"Filler {i} base", qty="1 lb", category="pantry")
        tools.plan_meal(_d(i), f"Filler {i}", slot="dinner", weekly_plan_id=plan_id)
    tools.plan_meal(_d(5), "Bean Chili", slot="dinner", weekly_plan_id=plan_id)
    tools.approve_weekly_plan(plan_id, approved_by="Emily")
    tools.sync_defrost_tasks(plan_id)

    tools.swap_meal_in_plan(plan_id, _d(5), "Chicken Skewers", slot="dinner")

    rows = _defrost_rows(plan_id)
    assert len(rows) == 1, rows
    move_day = rows[0]["task_date"]
    sentence = rows[0]["description"]
    assert move_day > household_today().isoformat(), \
        "premise: the move is still ahead, so every surface can show it"

    # 1 — Today (moves.today_moves, which the Now card and the strip read)
    payload = moves.today_moves(
        day=move_day, now=datetime.datetime.fromisoformat(f"{move_day}T08:00:00"))
    assert [m["title"] for m in payload["moves"] if m["kind"] == "fridge"] == \
        ["Move the Chicken thighs to the fridge"]
    # 2 — the morning text
    text = digest.build_morning_text(
        now_local=datetime.datetime.fromisoformat(f"{move_day}T07:30:00"), link=False) or ""
    assert sentence in text
    # 3 — the evening nudge
    nudge = digest.build_evening_nudge(
        now_local=datetime.datetime.fromisoformat(f"{move_day}T18:05:00"), link=False) or ""
    assert nudge.startswith("Move the Chicken thighs to the fridge first")
    # 4 — Cook's thaw list (the prep schedule, and the tab's own session)
    assert [t["description"] for t in tools.get_prep_schedule(plan_id)
            if t["task_type"] == "defrost"] == [sentence]
    assert sentence in [
        item.get("title") or item.get("description")
        for session in prep_sessions.prep_sessions_for_plan(plan_id)
        for item in session.get("items", [])
    ]
    # …and the two reads chat answers from.
    with freeze_time(datetime.datetime.fromisoformat(f"{move_day}T12:00:00")
                     + datetime.timedelta(hours=4)):
        assert [t["description"] for t in df.get_defrost_today()] == [sentence]
    assert [t["description"] for t in df.get_defrost_schedule()] == [sentence]


# ======================================================================
# 7. The pass can never cost the household their swap
# ======================================================================

def test_a_failing_reminder_pass_never_fails_the_swap(frozen_kitchen, monkeypatch):
    """
    GUARD. A swap that landed must not be reported as one that did not —
    the stance agent._sync_defrost_tasks_if_needed and
    meal_move._after_move both take. The cost of swallowing is the week's
    thaws being judged against the plan as it was, which is the bug this
    card fixes and is survivable; the cost of raising is telling the
    household nothing changed over a change that did.

    Mutation that pins it: remove the try/except around sync_defrost_tasks
    in resync_plan_thaws.
    """
    plan_id = _plan((_d(5), "Bean Chili"))

    def boom(_plan_id):
        raise RuntimeError("the freezer fell over")

    monkeypatch.setattr(df, "sync_defrost_tasks", boom)
    result = tools.swap_meal_in_plan(plan_id, _d(5), "Chicken Skewers", slot="dinner")

    assert result["meal"] == "Chicken Skewers"
    assert _entry(plan_id, _d(5))["id"] == result["entry_id"]
    assert "thaw_now" not in result


def test_the_pass_runs_after_the_commit_and_not_inside_the_transaction(frozen_kitchen, monkeypatch):
    """
    GUARD on the branch's own behaviour, and RED on main only because
    there is no pass there to observe (the probe never runs, so `seen` is
    empty) — not evidence of the ordering. A runtime guard rather than a
    source marker: the failure mode is an intermittent "database is
    locked", not a wrong answer.
    sync_defrost_tasks opens its own connection, so if the swap's write
    transaction were still open when this ran it could not take the write
    lock. The probe asks for exactly that lock, with a short timeout so a
    regression fails fast instead of waiting out sqlite3's default five
    seconds.

    Mutation that pins it: move the _defrost_resync call inside
    _replace_slot_entries' try block, above conn.commit().
    """
    plan_id = _plan((_d(5), "Bean Chili"))
    seen: list[str] = []
    real = df.sync_defrost_tasks

    def probing(pid):
        probe = sqlite3.connect(os.environ["DB_PATH"], timeout=0.2)
        try:
            probe.execute("BEGIN IMMEDIATE")
            probe.rollback()
            seen.append("free")
        except sqlite3.OperationalError as exc:
            seen.append(f"locked: {exc}")
        finally:
            probe.close()
        return real(pid)

    monkeypatch.setattr(df, "sync_defrost_tasks", probing)
    tools.swap_meal_in_plan(plan_id, _d(5), "Chicken Skewers", slot="dinner")

    assert seen == ["free"], seen
