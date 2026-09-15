"""
The plate note ("Where a meal came out short, I added a small side...")
used to render under WHATEVER day the household had open, because
weekNotesHtml only asked the WEEK-level `data.plates_note` — not whether
the day actually on screen carried a side (Loop Board, LOW/Bug/Phase 1).
A household reading Monday, where nothing was added, would see the
sentence explaining a side sitting on some other day of the same week.

`data.plates_note` still has to be a week-level flag server-side: it is the
one-time telling (see weekly_plan.PLATES_INTRO / mark_plates_intro_shown)
and the route stamps it "shown" the moment the payload carries it at all,
whichever day happens to be selected when the screen is fetched. What
changes here is only the SCREEN's decision to print it — gated to the day
actually on screen, using the `sides` list every planned slot (and every
snack) already carries in the week-menu payload.

Behaviour is run under node against shell.js's own functions, the way
tests/test_allset_week_path.py and friends do — see that file's docstring
for why source markers alone aren't enough for shell.js.
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

import nodeharness
from test_week_seven_tiles import _extract, _extract_var

REPO = Path(__file__).resolve().parent.parent
SHELL_JS = (REPO / "static" / "shell.js").read_text(encoding="utf-8")

_needs_node = pytest.mark.skipif(
    shutil.which("node") is None, reason="node is needed to execute the screen's own functions"
)

_PRELUDE = (
    _extract("escapeHtml", SHELL_JS) + "\n"
    + _extract_var("WEEK_SLOTS", SHELL_JS) + "\n"
    + _extract("weekPlanState", SHELL_JS) + "\n"
    + _extract("_dayHasPlateSides", SHELL_JS) + "\n"
    + _extract("weekNotesHtml", SHELL_JS) + "\n"
)

_NOTE = "Where a meal came out short, I added a small side"
_DATA = {"plates_note": _NOTE + " — I'm thinking about how you're eating.", "next_period": {}}


def _run(day) -> str:
    script = (
        _PRELUDE
        + f"console.log(JSON.stringify(weekNotesHtml({json.dumps(_DATA)}, {json.dumps(day)})));"
    )
    res = nodeharness.run_node(script, timeout=30)
    assert res.returncode == 0, f"node failed: {res.stderr}"
    return json.loads(res.stdout.strip())


@_needs_node
def test_a_dinner_side_puts_the_note_under_that_day():
    day = {"breakfast": None, "lunch": None,
           "dinner": {"sides": [{"item": "Green salad"}]}, "snacks": []}
    assert _NOTE in _run(day)


@_needs_node
def test_a_day_with_nothing_added_gets_no_note():
    """The exact bug: a full plate that needed no help must not carry the
    sentence explaining a side that isn't there."""
    day = {"breakfast": None, "lunch": None,
           "dinner": {"sides": []}, "snacks": []}
    assert _run(day) == ""


@_needs_node
def test_a_side_on_a_snack_still_counts():
    """The server computes plates_note off every ROW, snacks included
    (weekly_plan.get_week_menu's own comment: "a side attached to a snack
    is still a side the household paid for"). The per-day check has to
    agree, or a snack-only side would go unexplained forever."""
    day = {"breakfast": None, "lunch": None, "dinner": None,
           "snacks": [{"sides": [{"item": "Apple"}]}]}
    assert _NOTE in _run(day)


@_needs_node
def test_an_open_or_empty_slot_has_no_sides_key_and_does_not_crash():
    """`build_slot` never sets `sides` on a planned_empty or open slot (see
    weekly_plan.build_slot) — the check must treat that as "no side", not
    throw on a missing key."""
    day = {"breakfast": {"state": "open"},
           "lunch": {"state": "planned_empty"}, "dinner": None, "snacks": []}
    assert _run(day) == ""


@_needs_node
def test_no_day_selected_yet_gets_no_note():
    assert _run(None) == ""


@_needs_node
def test_the_soft_note_is_unaffected_by_the_per_day_gate():
    """The draft's soft-conflict line (coordination._soft_note) is a
    different note entirely and must keep showing regardless of plate
    sides — only plates_note is day-gated."""
    data = {**_DATA, "plates_note": None, "weekly_plan_id": 9, "status": "draft",
            "days": [{}], "soft_note": "Sam isn't keen on the Tuesday fish."}
    day = {"breakfast": None, "lunch": None, "dinner": {"sides": []}, "snacks": []}
    script = (
        _PRELUDE
        + f"console.log(JSON.stringify(weekNotesHtml({json.dumps(data)}, {json.dumps(day)})));"
    )
    res = nodeharness.run_node(script, timeout=30)
    assert res.returncode == 0, f"node failed: {res.stderr}"
    out = json.loads(res.stdout.strip())
    assert "keen on the Tuesday fish" in out


def test_the_call_site_hands_the_selected_day_to_the_notes_row():
    """Source tripwire for the wiring itself: weekStepHtml must pass the
    day actually on screen, not just the week payload."""
    assert "weekNotesHtml(data, days[selected])" in SHELL_JS
