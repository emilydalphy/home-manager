"""
Route crash sweep, 2026-10-30: POST /api/week/{week_start}/intake with the
three plain lists in the wrong shape.

  * packed_lunch_days holding an object 500'd the save itself
    (`d not in skipped` hashes each day).
  * moods holding an object saved with a 200 — and then every generate for
    that week 500'd, because agent._intake_generation_context looks each
    mood up in MOOD_GUIDANCE.

Both are now the 400 their sibling skipped_days already gives, and nothing
is written.
"""
from __future__ import annotations

import datetime

import pytest
from fastapi.testclient import TestClient

from conftest import household_today

from app import agent, tools
from app.db import get_conn
from app.main import app


def _week_start() -> str:
    today = household_today()
    return (today - datetime.timedelta(days=today.weekday())).isoformat()


def _revisions() -> int:
    conn = get_conn()
    try:
        return conn.execute("SELECT COUNT(*) AS c FROM week_intake").fetchone()["c"]
    finally:
        conn.close()


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


@pytest.mark.parametrize("body,said", [
    ({"packed_lunch_days": [{"z": 1}]}, "packed_lunch_days must be a list of ISO dates."),
    ({"packed_lunch_days": [12345]}, "packed_lunch_days must be a list of ISO dates."),
    ({"moods": [{"a": 1}]}, "moods must be a list of words."),
    ({"cuisines": [["Thai"]]}, "cuisines must be a list of words."),
])
def test_a_list_in_the_wrong_shape_is_a_400_and_writes_nothing(lenient, body, said):
    res = lenient.post(f"/api/week/{_week_start()}/intake", json=body)
    assert res.status_code == 400, res.text
    assert res.json()["detail"] == said
    assert _revisions() == 0
    assert _server_errors() == []


def test_a_packed_lunch_day_that_is_not_a_date_is_refused(lenient):
    res = lenient.post(f"/api/week/{_week_start()}/intake", json={"packed_lunch_days": ["someday"]})
    assert res.status_code == 400
    assert _revisions() == 0


def test_the_screens_own_answers_still_save_and_reach_the_generator(lenient):
    week = _week_start()
    days = tools.period_dates(week, 7)
    res = lenient.post(f"/api/week/{week}/intake", json={
        "packed_lunch_days": [days[1]],
        "moods": ["Something warm", "My own words"],
        "cuisines": ["Thai"],
    })
    assert res.status_code == 200, res.text
    intake = tools.get_week_intake(week)
    ctx = agent._intake_generation_context(intake)
    assert ctx["packed_lunch_days"] == [days[1]]
    assert ctx["moods"] == ["Something warm", "My own words"]
    assert ctx["mood_guidance"] == [tools.MOOD_GUIDANCE["Something warm"]]
