"""
Route crash sweep, 2026-10-30: every POST/PUT/PATCH/DELETE route on a
seeded household, one field at a time set to the wrong shape, nested
shapes included. What still answered 500 (beyond the week intake's lists,
tests/test_intake_lists_shape_2026_10_30.py):

  * POST /api/usual-week — a variety choice sent as a list (`in` hashed
    it), and prep days sent as a bare number (iterated).
  * POST /api/week/unbatch-undo — a batch's ids sent as a bare number
    (iterated). And the quieter half: ids that filtered down to none
    reached set_cook_ahead as "no days", which CLEARS the batch the Undo
    exists to put back.
"""
from __future__ import annotations

import datetime

import pytest
from fastapi.testclient import TestClient

from conftest import household_today, on_the_weeks_monday
from app import tools
from app.db import get_conn
from app.main import app
from app.tools import cooker

ON_MONDAY = on_the_weeks_monday()


def _server_errors() -> list:
    conn = get_conn()
    try:
        return [r["where_"] for r in conn.execute(
            "SELECT where_ FROM error_events WHERE kind = 'server'").fetchall()]
    finally:
        conn.close()


@pytest.fixture
def lenient():
    with TestClient(app, raise_server_exceptions=False) as c:
        res = c.post("/login", data={"password": "test-password", "next": "/"}, follow_redirects=False)
        assert res.status_code == 303
        yield c


# ---------- POST /api/usual-week ----------

@pytest.mark.parametrize("body", [
    {"variety": {"dinner": []}},
    {"variety": {"lunch": ["few_rotation"]}},
    {"prep": {"days": 5}},
    {"prep": {"days": ["sunday"], "length": ["long"]}},
])
def test_usual_week_answers_in_the_wrong_shape_are_a_400(lenient, body):
    before = lenient.get("/api/usual-week").json()
    res = lenient.post("/api/usual-week", json=body)
    assert res.status_code == 400, res.text
    assert isinstance(res.json()["detail"], str)
    assert lenient.get("/api/usual-week").json() == before
    assert _server_errors() == []


def test_usual_week_still_saves_a_real_prep_day(lenient):
    res = lenient.post("/api/usual-week", json={"prep": {"days": ["sunday"], "length": None}})
    assert res.status_code == 200, res.text


# ---------- POST /api/week/unbatch-undo ----------

def _monday() -> datetime.date:
    today = household_today()
    return today - datetime.timedelta(days=today.weekday())


def _batched_week():
    """Turkey Chili Monday and Thursday — approval batches it on a prep-day household."""
    for name in ("Emily", "Vineeth"):
        tools.add_member(name)
    tools.set_prep_days([{"weekday": "sunday", "minutes": 90}])
    tools.add_recipe(
        "Turkey Chili",
        ingredients=[{"item": "Ground turkey", "qty": "1 lb", "category": "meat"}],
        default_servings=4, prep_time_minutes=15, cook_time_minutes=40,
        instructions=["Brown the turkey.", "Simmer for 40 minutes."],
    )
    mon = _monday()
    plan_id = tools.create_weekly_plan(mon.isoformat())["weekly_plan_id"]
    for n in (0, 3):
        tools.plan_meal((mon + datetime.timedelta(days=n)).isoformat(), "Turkey Chili",
                        slot="dinner", weekly_plan_id=plan_id)
    tools.approve_weekly_plan(plan_id, approved_by="Emily")
    return plan_id


def _cards(plan_id):
    return {(m["date"], m["meal"]): bool(m.get("is_leftovers"))
            for m in cooker.get_cooker_view(plan_id)["meals"]}


@ON_MONDAY
@pytest.mark.parametrize("ids", [5, True, "abc", [None], []])
def test_an_undo_whose_days_are_not_ids_is_refused_and_the_batch_stays(lenient, ids):
    plan_id = _batched_week()
    before = _cards(plan_id)
    assert any(before.values()), "the week should start batched"
    source = next(m["entry_id"] for m in cooker.get_cooker_view(plan_id)["meals"]
                  if m["date"] == _monday().isoformat() and m["meal"] == "Turkey Chili")

    res = lenient.post("/api/week/unbatch-undo", json={
        "weekly_plan_id": plan_id,
        "dishes": [{"source_entry_id": source, "covered_entry_ids": ids}],
    })

    assert res.status_code == 200, res.text
    assert res.json()["status"] == "refused"
    assert _cards(plan_id) == before
    assert _server_errors() == []


def test_a_component_undo_with_a_bare_number_is_refused_not_a_500(lenient):
    plan_id = tools.create_weekly_plan(_monday().isoformat())["weekly_plan_id"]
    res = lenient.post("/api/week/unbatch-undo", json={
        "weekly_plan_id": plan_id, "components": [{"key": "eggs", "entry_ids": 5}],
    })
    assert res.status_code == 200, res.text
    assert res.json()["status"] == "refused"
    assert _server_errors() == []
