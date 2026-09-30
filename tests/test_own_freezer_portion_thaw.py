"""
A meal eating a portion this week's own cook froze gets its fridge move the
day before, like a night-off portion always did.

Emily, 2026-09-30: "for the freezer portion - will it remind me the day
before to take that out?" Two kinds of freezer meal:

  (a) a portion a night off froze (freezer_portions.KEY, an inventory row):
      defrost_candidates books "Move the Kofte to the fridge — for
      Wednesday's dinner." the day before. Always did.
  (b) "Leftovers from the freezer — Monday's Soup": this week's own cook
      makes a portion extra (leftovers.FREEZER_EXTRA_KEY) and a later meal
      eats it (leftovers.FROM_FREEZER_KEY with a "cook" ref). There is no
      inventory row, so nothing matched it and NO reminder was ever booked
      — on Saturday it just appeared as a reheat, still frozen.

Now (b) is booked by freezer_portions.own_portion_candidates through
defrost.sync_defrost_tasks, on the day before, and shows everywhere a
fridge move shows: Today's moves (the Now card), the Today tile, the
morning text and the evening nudge. Each test here fails on origin/main
7f513a9.
"""
from __future__ import annotations

import datetime
import json

from conftest import household_today
from freezegun import freeze_time

from app import tools
from app.db import get_conn
from app.tools import defrost, digest, leftovers, moves, weekly_plan


def _next_monday() -> datetime.date:
    today = household_today()
    return today - datetime.timedelta(days=today.weekday()) + datetime.timedelta(days=7)


def _entry(plan_id: int, d: str, slot: str) -> dict:
    conn = get_conn()
    row = conn.execute("SELECT * FROM meal_plan_entries WHERE weekly_plan_id = ? AND date = ? AND slot = ? "
                       "ORDER BY id", (plan_id, d, slot)).fetchone()
    conn.close()
    return dict(row)


def _defrost_rows(plan_id: int) -> list[dict]:
    conn = get_conn()
    rows = conn.execute("SELECT * FROM prep_tasks WHERE weekly_plan_id = ? AND task_type = 'defrost' ORDER BY id",
                        (plan_id,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def _week_with_a_frozen_saturday_lunch():
    """Monday's Soup cooks a portion extra for the freezer; Saturday's lunch
    eats it — exactly what weekly_plan._freeze_instead and the lunch count
    write."""
    for name in ("Emily", "Vineeth"):
        tools.add_member(name)
    mon = _next_monday()
    dates = [(mon + datetime.timedelta(days=i)).isoformat() for i in range(7)]
    plan_id = tools.create_weekly_plan(dates[0])["weekly_plan_id"]
    for name in ("Soup", "Stew", "Tacos", "Saturday Placeholder"):
        tools.add_recipe(name, ingredients=[{"item": f"{name} base", "qty": "1 lb", "category": "pantry"}],
                         prep_time_minutes=10, cook_time_minutes=30, default_servings=2)
    tools.plan_meal(dates[0], "Soup", slot="lunch", weekly_plan_id=plan_id)
    tools.plan_meal(dates[5], "Saturday Placeholder", slot="lunch", weekly_plan_id=plan_id)
    for d in dates:
        tools.plan_meal(d, "Tacos" if d == dates[4] else "Stew", slot="dinner", weekly_plan_id=plan_id)
    cook, sat = _entry(plan_id, dates[0], "lunch"), _entry(plan_id, dates[5], "lunch")
    conn = get_conn()
    weekly_plan.freeze_a_portion(conn, cook["id"], dates[5], "lunch")
    night = {leftovers.FROM_FREEZER_KEY: {"cook": f"entry_id:{cook['id']}", "dish": "Soup"}}
    conn.execute("UPDATE meal_plan_entries SET recipe_id = NULL, freeform_meal = ?, derived_from_json = ? WHERE id = ?",
                 (leftovers.freezer_night_name("Soup", dates[0]), json.dumps(night), sat["id"]))
    conn.commit()
    conn.close()
    return plan_id, dates, sat["id"]


def test_the_move_is_booked_the_day_before():
    plan_id, dates, sat_id = _week_with_a_frozen_saturday_lunch()
    tools.sync_defrost_tasks(plan_id)
    rows = _defrost_rows(plan_id)
    assert len(rows) == 1, rows
    row = rows[0]
    assert row["task_date"] == dates[4], "Friday — the day before Saturday's lunch"
    assert row["description"] == "Move the Soup to the fridge — for Saturday's lunch."
    assert row["meal_plan_entry_id"] == sat_id and row["status"] == "pending"
    assert json.loads(row["detail_json"])["kind"] == defrost.OWN_PORTION_KIND


def test_friday_shows_it_on_today_the_morning_text_and_the_evening_nudge():
    plan_id, dates, _ = _week_with_a_frozen_saturday_lunch()
    tools.approve_weekly_plan(plan_id, approved_by="Emily")
    tools.sync_defrost_tasks(plan_id)
    friday = dates[4]

    morning = datetime.datetime.fromisoformat(f"{friday}T07:30:00")
    today = moves.today_moves(day=friday, now=morning)
    assert ("fridge", "Move the Soup to the fridge") in [(m["kind"], m["title"]) for m in today["moves"]]
    with freeze_time(morning + datetime.timedelta(hours=4)):  # Toronto → UTC
        assert [t["description"] for t in defrost.get_defrost_today()] == \
            ["Move the Soup to the fridge — for Saturday's lunch."]
    assert "Move the Soup to the fridge — for Saturday's lunch." in \
        (digest.build_morning_text(now_local=morning, link=False) or "")

    evening = datetime.datetime.fromisoformat(f"{friday}T18:05:00")
    assert (digest.build_evening_nudge(now_local=evening, link=False) or "").startswith(
        "Move the Soup to the fridge first")

    # Nothing on Saturday itself: by then it is too late to thaw.
    saturday = dates[5]
    sat_moves = moves.today_moves(day=saturday, now=datetime.datetime.fromisoformat(f"{saturday}T08:00:00"))
    assert not [m for m in sat_moves["moves"] if m["kind"] == "fridge"]


def test_a_resync_keeps_done_and_sweeps_a_lunch_that_is_no_longer_frozen():
    plan_id, dates, sat_id = _week_with_a_frozen_saturday_lunch()
    tools.sync_defrost_tasks(plan_id)
    conn = get_conn()
    conn.execute("UPDATE prep_tasks SET status = 'done' WHERE weekly_plan_id = ?", (plan_id,))
    conn.commit()
    conn.close()
    tools.sync_defrost_tasks(plan_id)
    rows = _defrost_rows(plan_id)
    assert [(r["status"], r["meal_plan_entry_id"]) for r in rows] == [("done", sat_id)], "not re-booked"

    conn = get_conn()
    conn.execute("UPDATE meal_plan_entries SET derived_from_json = '{}', freeform_meal = 'Sandwiches' WHERE id = ?",
                 (sat_id,))
    conn.commit()
    conn.close()
    tools.sync_defrost_tasks(plan_id)
    assert _defrost_rows(plan_id) == []


def test_it_never_touches_a_move_the_freezer_step_booked():
    """confirm_frozen_items' rows also have no inventory row; the sync keeps
    its hands off them, and un-ticking a freezer chip never cancels the
    Soup's move."""
    plan_id, dates, sat_id = _week_with_a_frozen_saturday_lunch()
    stew = _entry(plan_id, dates[2], "dinner")
    conn = get_conn()
    conn.execute(
        "INSERT INTO prep_tasks (household_id, weekly_plan_id, task_date, description, related_meal, status, "
        "task_type, meal_plan_entry_id) VALUES (1, ?, ?, ?, 'Stew', 'pending', 'defrost', ?)",
        (plan_id, dates[1], "Move the soup to the fridge — for Wednesday's Stew.", stew["id"]),
    )
    conn.commit()
    conn.close()
    tools.sync_defrost_tasks(plan_id)
    tools.sync_defrost_tasks(plan_id)
    descriptions = sorted(r["description"] for r in _defrost_rows(plan_id))
    assert descriptions == ["Move the Soup to the fridge — for Saturday's lunch.",
                            "Move the soup to the fridge — for Wednesday's Stew."]
    assert defrost._release_frozen_item("soup", plan_id) == 1
    assert [r["description"] for r in _defrost_rows(plan_id)] == ["Move the Soup to the fridge — for Saturday's lunch."]


def test_a_night_off_portion_still_reads_as_dinner():
    assert defrost.portion_move_description("Kofte", "2026-10-07") == \
        "Move the Kofte to the fridge — for Wednesday's dinner."
