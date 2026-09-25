"""
Approving a draft that takes over an already-approved week used to say so
BEFORE the tap — a note above the Approve button built from
preview_approved_takeover's `replaces` (app/tools/weekly_plan.py). Emily
asked for it gone (2026-09-25) and, in its place, a pop-up AFTER approving
that names what was replaced (option B): no extra tap before the decision,
the house toast pattern after it — see DESIGN_SYSTEM.md §2b S10.

weekTakeoverToastNote (static/shell.js) builds that clause from the same
`replaces` data get_week_menu already sends (still read, just no longer
rendered as a warning), off `submitWeekApproval`'s own `data` argument —
nothing new is fetched. These tests run it under node, straight off
shell.js's source, so a change to the wording or the data shape it reads
is caught here rather than only by eye on the phone.

No Undo on this toast: reopening the draft this approval just became
(tools.reopen_weekly_plan) only un-approves THAT plan — it never restores
the OTHER, approved plan that retire_overlapping_plans shortened or
retired to make room for it (confirmed by reading both; no code path flips
a 'retired' plan back or extends a shortened one). There is no server
path that gives the replaced days back, so this toast carries none.
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

import nodeharness
from test_week_seven_tiles import _extract

REPO = Path(__file__).resolve().parent.parent
SHELL_JS = (REPO / "static" / "shell.js").read_text(encoding="utf-8")

_needs_node = pytest.mark.skipif(
    shutil.which("node") is None, reason="node is needed to execute the screen's own functions"
)

_PRELUDE = (
    _extract("weekdaySpan", SHELL_JS) + "\n"
    + _extract("weekTakeoverToastNote", SHELL_JS) + "\n"
)


def _note(replaces):
    script = _PRELUDE + f"console.log(JSON.stringify(weekTakeoverToastNote({json.dumps(replaces)})));"
    res = nodeharness.run_node(script, timeout=30)
    assert res.returncode == 0, f"node failed: {res.stderr}"
    return json.loads(res.stdout.strip())


def _days(*entries):
    """entries: (weekday, [(slot, meal_name), ...]) pairs."""
    return [
        {"date": "2026-09-1%d" % i, "weekday": weekday,
         "meals": [{"slot": slot, "meal_name": name} for slot, name in meals]}
        for i, (weekday, meals) in enumerate(entries, start=1)
    ]


# ---------- nothing to say ----------

def test_no_replaces_is_no_toast_text():
    """The ordinary approval — nothing overlapped an approved week."""
    assert _note(None) is None


def test_takeover_with_no_meals_is_no_toast_text():
    """A plan that claimed days but held nothing on them (an away stretch,
    or a plan that never covered these dates) loses nothing a person would
    recognise as a meal — the toast must not invent one."""
    replaces = {"days": [{"date": "2026-09-10", "weekday": "Wednesday", "meals": []}], "meal_count": 0}
    assert _note(replaces) is None


# ---------- the two shapes Emily asked for ----------

def test_multi_day_all_dinners():
    replaces = {
        "days": _days(("Wednesday", [("dinner", "Bean Chili")]),
                       ("Thursday", [("dinner", "Salmon")]),
                       ("Friday", [("dinner", "Tacos")])),
        "meal_count": 3,
    }
    assert _note(replaces) == "Wednesday to Friday’s dinners were replaced"


def test_multi_day_mixed_slots_says_meals_not_dinners():
    replaces = {
        "days": _days(("Wednesday", [("dinner", "Bean Chili"), ("lunch", "Leftover soup")]),
                       ("Thursday", [("dinner", "Salmon")])),
        "meal_count": 3,
    }
    assert _note(replaces) == "Wednesday and Thursday’s meals were replaced"


# ---------- singular day ----------

def test_single_day_single_dinner_is_singular():
    replaces = {"days": _days(("Wednesday", [("dinner", "Bean Chili")])), "meal_count": 1}
    assert _note(replaces) == "Wednesday’s dinner was replaced"


def test_single_day_two_meals_is_plural_meals():
    replaces = {
        "days": _days(("Wednesday", [("dinner", "Bean Chili"), ("breakfast", "Eggs")])),
        "meal_count": 2,
    }
    assert _note(replaces) == "Wednesday’s meals were replaced"


# ---------- two-day span uses "and" ----------

def test_two_day_span_uses_and():
    replaces = {
        "days": _days(("Wednesday", [("dinner", "Bean Chili")]),
                       ("Friday", [("dinner", "Tacos")])),
        "meal_count": 2,
    }
    assert _note(replaces) == "Wednesday and Friday’s dinners were replaced"
