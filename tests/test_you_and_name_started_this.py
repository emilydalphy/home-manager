"""
The week's questions, two adults: once both have saved answers, the
in-flight line names both — "You and Vic have started this" — rather than
whoever saved last (Emily, 2026-10-31). With one adult it stays as it was.
"""
from __future__ import annotations

import datetime

from app import tools
from app.db import get_conn
from conftest import household_today
from test_replan_opens_this_week import _needs_node, _node
from test_same_as_last_week import _extract


def _week():
    return (household_today() + datetime.timedelta(days=1)).isoformat()


def _prefill(week):
    return tools.get_week_intake_prefill(week, day_count=7)


def test_both_adults_who_saved_are_named_first_first():
    week = _week()
    tools.save_week_intake(week, moods=["comfort"], created_by="Emily")
    tools.save_week_intake(week, cuisines=["Thai"], created_by="Emily")
    tools.save_week_intake(week, freeform="tacos one night", created_by="Vic")
    intake = _prefill(week)["intake"]
    assert intake["created_by"] == "Vic"
    assert intake["answered_by"] == ["Emily", "Vic"]


def test_one_adult_is_one_name():
    week = _week()
    tools.save_week_intake(week, moods=["comfort"], created_by="Emily")
    tools.save_week_intake(week, cuisines=["Thai"], created_by="Emily")
    assert _prefill(week)["intake"]["answered_by"] == ["Emily"]


def test_a_cleared_round_starts_again():
    week = _week()
    tools.save_week_intake(week, moods=["comfort"], created_by="Vic")
    tools.clear_week_intake(week)
    # The clear happened well before the next save.
    with get_conn() as conn:
        conn.execute("UPDATE week_intake SET superseded_at = datetime('now', '-1 hour') WHERE week_start = ?", (week,))
    tools.save_week_intake(week, cuisines=["Thai"], created_by="Emily")
    assert _prefill(week)["intake"]["answered_by"] == ["Emily"]


@_needs_node
def test_the_line_says_you_and_the_other_adult():
    out = _node(_extract("joinedLine") + _extract("andList") + """
console.log(JSON.stringify({
  emily: joinedLine('Vic', 'Emily', '3 hours ago', ['Emily', 'Vic']),
  vic: joinedLine('Vic', 'vic', 'just now', ['Emily', 'Vic']),
  third: joinedLine('Vic', 'Sam', 'just now', ['Emily', 'Vic']),
  one: joinedLine('Vic', 'Emily', '3 hours ago', ['Vic'])
}));""")
    assert out["emily"] == "You and Vic have started this. Your answers are still here."
    assert out["vic"] == "You and Emily have started this. Your answers are still here."
    assert out["third"] == "Emily and Vic have started this. Their answers are still here."
    assert out["one"] == "Vic started this 3 hours ago. Their answers are still here."
