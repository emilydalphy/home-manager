"""
Plan a week starts from last week's answers: "Same as last week?"

Loop Board card (Emily, 2026-09-25, option A of the "Start from last week,
not from a blank form" mockup): from the second planned week on, Plan a
week opens on one page — "Same as last week?", "Tap Change on anything
that's different." — listing last week's answers as rows (Days, Weekday
lunches, Taking lunch with you, Different days, Mood, Anything else), each
with Change. "Plan this week" drafts from those answers; Change opens only
that question and comes back; a changed row says "· Changed". Carried over:
how many days, the weekday lunches and prep day, the lunches taken out, the
mood and cuisines. Starting empty: the different days, guests, holidays,
"anything else". The dates move forward. A first week asks all five
questions as before. A slot above the rows waits for the next card, "Bring
over from last week".

Fifteen of these fail on weekday-lunches-step (3f627cd): there is no
#same page, no last_intake.day_count, no sameAsLastWeek / sameRowValues /
openChange / finishChange / samePayload. Two pass there on purpose — a
first week still has no last week, and the route already took a whole
answer set in one save — and pin that neither moved.
"""
from __future__ import annotations

import datetime
import json
import re
import shutil
from pathlib import Path

import pytest

import nodeharness
from app import tools
from app.db import get_conn

REPO = Path(__file__).resolve().parents[1]
PAGE = (REPO / "static" / "plan-week.html").read_text(encoding="utf-8")

_needs_node = pytest.mark.skipif(
    shutil.which("node") is None, reason="node is needed to run the page's own functions"
)


def _monday(offset_weeks: int = 1) -> str:
    today = datetime.date.today()
    monday = today - datetime.timedelta(days=today.weekday())
    return (monday + datetime.timedelta(days=7 * offset_weeks)).isoformat()


def _plus(iso: str, n: int) -> str:
    return (datetime.date.fromisoformat(iso) + datetime.timedelta(days=n)).isoformat()


def _extract(name: str, source: str = PAGE) -> str:
    start = source.index(f"function {name}(")
    i = source.index("{", start)
    depth, j = 0, i
    while True:
        if source[j] == "{":
            depth += 1
        elif source[j] == "}":
            depth -= 1
            if depth == 0:
                break
        j += 1
    return source[start:j + 1]


def _var(name: str) -> str:
    m = re.search(rf"  var {name} = [\s\S]*?;\n", PAGE)
    assert m, name
    return m.group(0)


def _node(script: str) -> object:
    res = nodeharness.run_node(script, timeout=30)
    assert res.returncode == 0, f"node failed: {res.stderr}"
    return json.loads(res.stdout.strip().splitlines()[-1])


def _attach_plan(week: str, intake_id: int, day_count: int, status: str = "draft") -> int:
    conn = get_conn()
    plan_id = conn.execute(
        "INSERT INTO weekly_plans (household_id, week_start_date, status, content_start_date, day_count, intake_id) "
        "VALUES (1, ?, ?, ?, ?, ?)", (week, status, week, day_count, intake_id),
    ).lastrowid
    conn.commit()
    conn.close()
    return plan_id


# ==========================================================================
# 1. The server: what the page needs to know about last week
# ==========================================================================

class TestLastWeek:
    def test_last_weeks_length_comes_from_the_plan_drafted_from_it(self):
        last, this = _monday(1), _monday(2)
        saved = tools.save_week_intake(last, moods=["Comfort food"], day_count=5)
        _attach_plan(last, saved["intake_id"], 5)
        prefill = tools.get_week_intake_prefill(this)
        assert prefill["last_intake"]["day_count"] == 5
        assert prefill["last_intake"]["moods"] == ["Comfort food"]

    def test_any_revision_of_that_week_counts_and_a_retired_plan_does_not(self):
        last, this = _monday(1), _monday(2)
        first = tools.save_week_intake(last, moods=["Comfort food"])
        _attach_plan(last, first["intake_id"], 4, status="retired")
        assert tools.get_week_intake_prefill(this)["last_intake"]["day_count"] is None
        _attach_plan(last, first["intake_id"], 6)
        tools.save_week_intake(last, moods=["On the grill"])      # a later revision, never drafted
        assert tools.get_week_intake_prefill(this)["last_intake"]["day_count"] == 6

    def test_a_first_week_has_no_last_week_so_it_asks_everything(self):
        assert tools.get_week_intake_prefill(_monday(2))["last_intake"] is None

    def test_what_is_about_one_week_does_not_travel(self):
        last, this = _monday(1), _monday(2)
        saved = tools.save_week_intake(last, night_tags={_plus(last, 2): ["rush"]},
                                       guest_counts={_plus(last, 4): {"adults": 2, "children": 0}},
                                       freeform="Use the lamb", moods=["Something warm"])
        # Drafted, so it isn't an in-flight intake this period would join.
        _attach_plan(last, saved["intake_id"], 7)
        prefill = tools.get_week_intake_prefill(this)
        assert prefill["intake"] is None                  # nothing for this week yet
        assert set(prefill["last_intake"]) == {"week_start", "day_count", "moods", "cuisines", "weekday_lunches"}

    def test_anything_else_is_said_to_be_this_weeks_and_the_page_starts_it_blank(self):
        """Emily, 2026-09-28: the note is for this week only and must read
        that way. The server never sends last week's (above); the page
        clears the box whenever a new period's prefill lands, and says so."""
        fetch = _extract("fetchPeriod")
        assert "$('freeform').value = '';" in fetch
        q5 = PAGE[PAGE.index('<section id="q5"'):PAGE.index('</section>', PAGE.index('<section id="q5"'))]
        assert "Just for this week &mdash; it starts blank next time." in q5

    def test_plan_this_week_saves_every_answer_in_one_revision(self, signed_in):
        """What samePayload sends: the whole set, as leaving does."""
        mon = _monday(2)
        res = signed_in.post(f"/api/week/{mon}/intake", json={
            "night_tags": {}, "guest_counts": {}, "packed_lunch_days": [_plus(mon, 1)],
            "skipped_days": [], "moods": ["Something warm"], "cuisines": ["Thai"], "freeform": "",
            "weekday_lunches": {"days": [{"date": _plus(mon, i), "kind": "cooked"} for i in range(5)],
                                "prep_days": []},
            "created_by": "", "day_count": 7,
        })
        assert res.status_code == 200
        body = res.json()
        assert body["revision"] == 1
        assert body["moods"] == ["Something warm"] and body["packed_lunch_days"] == [_plus(mon, 1)]
        assert body["weekday_lunches"]["counts"]["cooked"] == 5


# ==========================================================================
# 2. The page
# ==========================================================================
# UPDATED 2026-10-19 (re-plan Option B, Emily's live session 2026-10-18):
# option A's page these classes pinned — six rows (Days, Weekday lunches,
# Taking lunch with you, Different days, Mood, Anything else this week),
# "Tap Change on anything that's different.", "Plan this week", "This week
# so far", a first week asking all five steps, and last week's day count
# carried over — was rebuilt in onboarding's look: eight rows in
# onboarding's order and words, each opening onboarding's own screen, on
# every visit, for the household's usual length. The page is pinned in
# tests/test_replan_option_b.py; what still holds of option A's look is
# below.

def test_rows_are_44px_taps_and_colours_go_through_tokens():
    css = PAGE[PAGE.index("/* ---------- Same as last week?"):PAGE.index("/* The typed answer (step 5)")]
    assert "min-height: 56px" in css
    assert not re.search(r"#[0-9a-fA-F]{3,6}\b", css)
    assert "var(--celadon-tint)" in css and "var(--apricot-label)" in css
    assert '<button type="button" class="same-row' in _extract("renderSame")
