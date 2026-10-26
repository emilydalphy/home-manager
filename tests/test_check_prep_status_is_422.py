"""
A bad prep status is a bad request, not a missing prep task (overnight 2026-10-22).

/api/cooker/check-prep answered {"status": "finished"} on a real task with
404 "not found": check_off_prep_step refused the word with a plain
ValueError, the same type as its own missing-row error, and the route
mapped every ValueError to 404. check-meal next door answers 422 for the
same mistake (cooker.InvalidMealStatus); this matches it with
cooker.InvalidPrepStatus.
"""
from conftest import household_date

from app import tools
from app.db import get_conn


def _task() -> int:
    plan_id = tools.create_weekly_plan(household_date(0))["weekly_plan_id"]
    conn = get_conn()
    cur = conn.execute(
        "INSERT INTO prep_tasks (household_id, weekly_plan_id, task_date, description, task_type, status) "
        "VALUES (?, ?, ?, 'Chop the onions.', 'general', 'pending')",
        (tools.household_id(), plan_id, household_date(0)),
    )
    conn.commit()
    task_id = cur.lastrowid
    conn.close()
    return task_id


def _status(task_id: int) -> str:
    conn = get_conn()
    row = conn.execute("SELECT status FROM prep_tasks WHERE id = ?", (task_id,)).fetchone()
    conn.close()
    return row["status"]


def test_a_bad_status_is_422_with_a_plain_line(signed_in):
    """CATCH. Was 404 'not found' for a task that exists."""
    task_id = _task()
    res = signed_in.post("/api/cooker/check-prep", json={"prep_task_id": task_id, "status": "finished"})
    assert res.status_code == 422
    detail = res.json()["detail"]
    assert isinstance(detail, str), "a plain line, not pydantic's field-error list"
    assert "finished" in detail and "done" in detail
    assert _status(task_id) == "pending"


def test_a_missing_task_is_still_404(signed_in):
    """GUARD. The new except must not swallow 'no such prep task'."""
    res = signed_in.post("/api/cooker/check-prep", json={"prep_task_id": 999999, "status": "done"})
    assert res.status_code == 404


def test_the_three_real_statuses_still_work(signed_in):
    """GUARD. Tick, skip and untick are the feature."""
    task_id = _task()
    for good in ("done", "pending", "skipped"):
        res = signed_in.post("/api/cooker/check-prep", json={"prep_task_id": task_id, "status": good})
        assert res.status_code == 200
        assert _status(task_id) == good


def test_the_marker_is_still_a_value_error():
    """GUARD. Chat's per-tool except and any ValueError caller see no change."""
    assert issubclass(tools.InvalidPrepStatus, ValueError)
