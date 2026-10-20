"""A guest count saved as a bare number can't break the week.

Overnight hunt, 2026-10-20: POST /api/week/{week}/intake took
guest_counts {"2026-10-27": 2} (a number, not {"adults", "children"}) and
saved it as a revision. Every generate for that week then 500'd, because
agent._intake_generation_context calls .get on each entry. The save now
refuses the malformed shape with a 400, and the generator reads any entry
already saved that way as no guests instead of crashing.
"""
from __future__ import annotations

import datetime

import pytest

from app import agent, tools
from app.db import get_conn


def _next_monday() -> str:
    from conftest import household_today
    today = household_today()
    return (today + datetime.timedelta(days=7 - today.weekday())).isoformat()


@pytest.mark.parametrize("bad", [
    {"DAY": 2},
    {"DAY": {"adults": -1}},
    {"DAY": {"adults": "2"}},
    {"DAY": {"adults": True}},
    {"DAY": {"grown_ups": 2}},
    {"not-a-date": {"adults": 2}},
])
def test_a_malformed_guest_count_is_refused_at_the_save(signed_in, bad):
    week = _next_monday()
    tuesday = tools.period_dates(week, 7)[1]
    if isinstance(bad, dict):
        bad = {(tuesday if k == "DAY" else k): v for k, v in bad.items()}
    res = signed_in.post(f"/api/week/{week}/intake", json={"guest_counts": bad})
    assert res.status_code == 400, res.text
    assert "guest" in res.json()["detail"].lower()
    conn = get_conn()
    n = conn.execute("SELECT COUNT(*) FROM week_intake WHERE week_start = ?", (week,)).fetchone()[0]
    conn.close()
    assert n == 0  # nothing landed as a revision


def test_a_well_formed_guest_count_still_saves(signed_in):
    week = _next_monday()
    tuesday = tools.period_dates(week, 7)[1]
    res = signed_in.post(f"/api/week/{week}/intake", json={
        "night_tags": {tuesday: ["guests"]},
        "guest_counts": {tuesday: {"adults": 2, "children": 1}},
    })
    assert res.status_code == 200, res.text
    assert res.json()["guest_counts"] == {tuesday: {"adults": 2, "children": 1}}


def test_an_entry_already_saved_as_a_number_reads_as_no_guests():
    # A revision written before the save checked the shape.
    intake = {
        "household_snapshot": {"adults": 2, "children": 0},
        "night_tags": {},
        "guest_counts": {"2026-10-27": 2, "2026-10-28": {"adults": 1, "children": 0}},
    }
    ctx = agent._intake_generation_context(intake)
    assert ctx["guest_totals"] == {"2026-10-28": {"adults": 3, "children": 0}}
    assert ctx["guest_extras"] == {"2026-10-28": {"adults": 1, "children": 0}}


def test_an_old_bad_entry_does_not_block_the_weeks_next_save(signed_in):
    """Plan the Week posts the stored guest map back on every save; an entry
    saved before the check must read clean, not refuse every later save."""
    import json
    from app.db import get_conn
    week, tuesday = "2026-10-26", "2026-10-27"
    tools.save_week_intake(week, guest_counts={"2026-10-28": {"adults": 1, "children": 0}})
    conn = get_conn()
    conn.execute(
        "UPDATE week_intake SET guest_counts_json = ? WHERE week_start = ? AND superseded_at IS NULL",
        (json.dumps({tuesday: 2, "2026-10-28": {"adults": "1", "children": 0}}), week),
    )
    conn.commit()
    conn.close()

    stored = signed_in.get(f"/api/week/{week}/intake").json()
    stored = stored.get("intake", stored)
    assert stored["guest_counts"] == {"2026-10-28": {"adults": 1, "children": 0}}
    res = signed_in.post(f"/api/week/{week}/intake", json={"guest_counts": stored["guest_counts"], "moods": ["quick"]})
    assert res.status_code == 200, res.text
