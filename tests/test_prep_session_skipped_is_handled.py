"""
A prep task the household skipped reads as handled in its prep-day
session, the way Today's moves already read it (2026-10-10, overnight
defect hunt).

Reproduced over HTTP on a throwaway database: "Move the chicken thighs to
the fridge" skipped through check-prep (the chat's "not this one" lands
here too) — /api/today/moves called it done, while /api/cooker-view's
prep session kept it unticked at "1 / 5" for the rest of the week.
"""
import datetime

from app import tools
from app.tools import prep_sessions
from conftest import household_today


def _session_today():
    today = household_today()
    tools.set_prep_days([{"weekday": today.strftime("%A").lower(), "minutes": 60}])
    plan_id = tools.create_weekly_plan(
        today.isoformat(), content_start_date=today.isoformat(), day_count=7
    )["weekly_plan_id"]
    tools.save_prep_tasks(plan_id, [
        {"task_date": today.isoformat(), "description": "Soak the beans", "related_meal": "Bean Chili"},
        {"task_date": today.isoformat(), "description": "Marinate the chicken", "related_meal": "Skewers"},
    ])
    tasks = {t["description"]: t["id"] for t in tools.get_prep_schedule(plan_id)}
    return plan_id, tasks


def _items(plan_id):
    [session] = prep_sessions.prep_sessions_for_plan(plan_id)
    return session, {i["title"]: i for i in session["items"]}


def test_a_skipped_task_is_handled_in_its_session():
    """CATCH. Red before: done False and items_done 0."""
    plan_id, tasks = _session_today()
    tools.check_off_prep_step(tasks["Soak the beans"], "skipped")

    session, items = _items(plan_id)

    assert items["Soak the beans"]["done"] is True
    assert items["Soak the beans"]["skipped"] is True
    assert session["items_done"] == 1 and session["items_total"] == 2


def test_done_and_pending_read_as_before():
    """GUARD. A ticked task is done and not skipped; an open one is neither."""
    plan_id, tasks = _session_today()
    tools.check_off_prep_step(tasks["Marinate the chicken"], "done")

    session, items = _items(plan_id)

    assert (items["Marinate the chicken"]["done"], items["Marinate the chicken"]["skipped"]) == (True, False)
    assert (items["Soak the beans"]["done"], items["Soak the beans"]["skipped"]) == (False, False)


def test_unskipping_puts_it_back_to_do():
    """GUARD. Back to pending is back on the list."""
    plan_id, tasks = _session_today()
    tools.check_off_prep_step(tasks["Soak the beans"], "skipped")
    tools.check_off_prep_step(tasks["Soak the beans"], "pending")

    session, items = _items(plan_id)
    assert items["Soak the beans"]["done"] is False
    assert session["items_done"] == 0
