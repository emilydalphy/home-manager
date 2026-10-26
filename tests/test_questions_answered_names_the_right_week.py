"""
"Since you last looked" names THIS week's questions by the household's own
week, not the calendar's (defect hunt 2026-10-26, week anchors).

since_last_looked told the other adult "answered next week's questions"
whenever the intake's week started after this calendar week's MONDAY. Every
period the app offers starts no earlier than today (suggest_planning_period),
so a household whose week starts on a Sunday ("ready by Saturday") answering
for the week that starts today was told it was next week's — and so was any
household answering mid-week for the rest of this week. "Next" now means the
period starts after today.
"""
import datetime

import pytest

from app import tools
from app.db import get_conn
from app.tools import _shared


def _adults():
    for n in ("Emily", "Vineeth"):
        tools.add_member(n)
        tools.set_member_age_group(n, "adult")
    conn = get_conn()
    rows = conn.execute("SELECT id, name FROM members WHERE household_id = ?", (tools.household_id(),)).fetchall()
    conn.execute("UPDATE members SET last_looked_at = '2000-01-01 00:00:00' WHERE household_id = ?",
                 (tools.household_id(),))
    conn.commit()
    conn.close()
    return {r["name"]: r["id"] for r in rows}


def _told_emily_after_vineeth_answers(week_start):
    ids = _adults()
    with _shared.use_member(ids["Vineeth"]):
        tools.save_week_intake(week_start, night_tags={})
    with _shared.use_member(ids["Emily"]):
        return [l["text"] for l in tools.look_since_last()]


@pytest.mark.today("2026-10-25T12:00")
def test_a_sunday_start_week_answered_on_its_sunday_is_this_weeks():
    """CATCH: red on main — said "next week’s" (Oct 25 is after Monday Oct 19)."""
    assert "Vineeth answered this week’s questions." in _told_emily_after_vineeth_answers("2026-10-25")


@pytest.mark.today("2026-10-28T12:00")
def test_the_rest_of_this_week_answered_midweek_is_this_weeks():
    """CATCH: red on main — a Wednesday-start period read as next week."""
    assert "Vineeth answered this week’s questions." in _told_emily_after_vineeth_answers("2026-10-28")


@pytest.mark.today("2026-10-25T12:00")
def test_tomorrows_week_answered_on_sunday_is_still_next_weeks():
    """GUARD: the default Monday week, answered the Sunday before."""
    assert "Vineeth answered next week’s questions." in _told_emily_after_vineeth_answers("2026-10-26")
