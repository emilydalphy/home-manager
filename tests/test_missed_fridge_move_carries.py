"""
A fridge move nobody ticked on its own day stays on Today until its cook
(overnight hunt, 2026-10-21).

defrost dates a fridge move one or two days before the cook. moves.
_prep_moves read only rows dated today, so a move missed on Monday vanished
on Tuesday from Now, the morning text and the evening nudge while the meat
stayed frozen — only Cook's Tonight card, on the cook day, still said
"— still to do". moves._missed_fridge_moves carries such a row onto today,
"still to do", while its meal is on a later day and not ticked cooked. Never onto the cook day itself (too late to thaw).
"""
from __future__ import annotations

import datetime as dt
from datetime import datetime

from app import tools
from app.db import get_conn
from app.tools import digest
from app.tools import moves as _moves
from conftest import household_today

TODAY = household_today()
ISO = lambda n: (TODAY + dt.timedelta(days=n)).isoformat()  # noqa: E731
DESC = "Move the ground beef to the fridge — for Wednesday's Weeknight Chili."


def _at(hour: int, day: int = 0) -> datetime:
    return datetime.combine(TODAY + dt.timedelta(days=day), dt.time(hour, 0))


def _seed(chili_day: int) -> tuple[int, int]:
    """A plan with Weeknight Chili `chili_day` days from today, its fridge
    move dated two days before it (so already gone by), never ticked."""
    for n in ("Emily", "Vineeth"):
        tools.add_member(n)
        tools.set_member_age_group(n, "adult")
    tools.add_recipe("Weeknight Chili", ingredients=[{"item": "ground beef", "qty": "1 lb"}],
                     prep_time_minutes=10, cook_time_minutes=25, default_servings=2)
    plan_id = tools.create_weekly_plan(ISO(-3))["weekly_plan_id"]
    tools.plan_meal(ISO(chili_day), "Weeknight Chili", slot="dinner", weekly_plan_id=plan_id,
                    add_ingredients_to_grocery_list=False)
    conn = get_conn()
    entry = conn.execute("SELECT id FROM meal_plan_entries WHERE household_id = ? AND date = ?",
                         (tools.household_id(), ISO(chili_day))).fetchone()["id"]
    task = conn.execute(
        "INSERT INTO prep_tasks (household_id, weekly_plan_id, task_date, description, related_meal, "
        "status, task_type, meal_plan_entry_id) VALUES (?, ?, ?, ?, 'Weeknight Chili', 'pending', 'defrost', ?)",
        (tools.household_id(), plan_id, ISO(chili_day - 2), DESC, entry),
    ).lastrowid
    conn.commit()
    conn.close()
    return entry, task


def _fridge(now: datetime) -> list[dict]:
    return [m for m in _moves.today_moves(day=now.date(), now=now)["moves"] if m["kind"] == "fridge"]


def test_a_move_missed_yesterday_is_still_on_today_and_in_the_morning_text():
    _seed(chili_day=1)  # moved due yesterday, chili tomorrow
    [move] = _fridge(_at(7))
    assert move["title"] == "Move the ground beef to the fridge"
    assert move["overdue"] and move["time_label"] == "still to do" and not move["done"]
    text = digest.build_morning_text(_at(7), link=False, parts=["meals", "freezer"])
    assert "Move the ground beef to the fridge — for Wednesday's Weeknight Chili." in text


def test_not_carried_onto_the_cook_day_or_once_ticked_or_cooked():
    entry, task = _seed(chili_day=2)  # move due today; carried tomorrow only
    tomorrow = _at(7, day=1)
    # The look-ahead at tomorrow never repeats today's own row.
    assert len(_fridge(_at(7))) == 1
    assert not [m for m in _moves.moves_for_day(TODAY + dt.timedelta(days=1), now=_at(7))
                if m["kind"] == "fridge"]
    assert len(_fridge(tomorrow)) == 1
    # The cook day itself: too late to thaw, and Now stays quiet (as before).
    assert _fridge(_at(7, day=2)) == []
    tools.check_off_meal(entry, "done")
    assert _fridge(tomorrow) == []
    tools.check_off_meal(entry, "pending")
    assert len(_fridge(tomorrow)) == 1
    tools.check_off_prep_step(task, "skipped")
    assert _fridge(tomorrow) == []
