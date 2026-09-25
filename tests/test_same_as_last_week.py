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

def _same_section() -> str:
    start = PAGE.index('<section id="same"')
    return PAGE[start:PAGE.index("</section>", start)]


class TestThePage:
    def test_the_page_says_it_in_the_mockups_words_with_the_hook_above_the_rows(self):
        same = _same_section()
        assert "<h1>Same as last week?</h1>" in same
        assert '<p class="step-sub">Tap Change on anything that&rsquo;s different.</p>' in same
        order = ['id="same-joined"', 'id="bring-over"', 'id="same-rows"']
        assert [same.index(x) for x in order] == sorted(same.index(x) for x in order)
        assert "BRING OVER FROM LAST WEEK" in same
        # It sits before the first question, so it's the first screen.
        assert PAGE.index('<section id="same"') < PAGE.index('<section id="q1"')
        rows = _var("SAME_ROWS")
        for key, step, label in (("days", 1, "Days"), ("lunches", 3, "Weekday lunches"),
                                 ("packed", 3, "Taking lunch with you"), ("different", 2, "Different days"),
                                 ("mood", 4, "Mood"), ("freeform", 5, "Anything else")):
            assert f"key: '{key}'" in rows and f"step: {step}, label: '{label}'" in rows, key
        copy = _var("SAME_COPY")
        for line in ("go: 'Plan this week'", "done: 'Done'", "change: 'Change'", "changed: 'Changed'",
                     "nothingMarked: 'Nothing marked'", "nothingAdded: 'Nothing added'"):
            assert line in copy, line

    def test_rows_are_44px_taps_and_colours_go_through_tokens(self):
        css = PAGE[PAGE.index("/* ---------- Same as last week?"):PAGE.index("/* The typed answer (step 5)")]
        assert "min-height: 56px" in css
        assert not re.search(r"#[0-9a-fA-F]{3,6}\b", css)
        assert "var(--celadon-tint)" in css and "var(--apricot-label)" in css
        assert '<button type="button" class="same-row' in _extract("renderSame")

    def test_a_first_week_opens_on_the_questions_and_a_later_one_on_the_page(self):
        load = _extract("load")
        assert "var same = await sameAsLastWeek();" in load
        assert load.index("sameAsLastWeek()") < load.index("$('loading').hidden = true;")
        assert "if (same) { showSame(); return; }" in load
        assert "if (!last) return false;" in _extract("sameAsLastWeek")

    def test_plan_this_week_drafts_from_every_answer_in_one_save(self):
        advance = _extract("advance")
        assert "if (editing) { await finishChange(); return; }" in advance
        assert "if (step >= 1 && step < STEP_COUNT) {" in advance
        assert "var saved = await saveIntake(step === 0 ? samePayload() : {" in advance
        # A draft that fell over comes back to the page it started from.
        assert "if (fromSame) showSame();" in advance
        assert "Tap Plan this week to try again." in advance
        payload = _extract("samePayload")
        for field in ("night_tags", "guest_counts", "packed_lunch_days", "skipped_days", "moods",
                      "cuisines", "freeform", "payload.weekday_lunches = lunchPayload();"):
            assert field in payload, field

    def test_change_opens_one_question_and_done_comes_back(self):
        assert "row.addEventListener('click', function () { openChange(Number(row.dataset.step)); });" in \
            _extract("renderSame")
        cta = _extract("paintCta")
        assert "$('cta').textContent = SAME_COPY.go;" in cta
        assert "$('cta').textContent = SAME_COPY.done;" in cta
        show = _extract("showStep")
        assert "$('same').hidden = true;" in show and "$('progress').hidden = !!editing;" in show
        finish = _extract("finishChange")
        assert "await saveStep(n);" in finish and finish.rstrip().endswith("showSame();\n  }")
        # The crumb on a question opened from the page goes back to it.
        assert "else if (editing && !drafting) advance();" in _extract("crumbTap")
        assert "else if (step === 0) leaveFlow();" in _extract("crumbTap")
        # Leaving the page untouched saves nothing.
        assert "if (first) answersAtLoad = answersSnapshot();" in _extract("showSame")
        assert "$('same').hidden = true;" in _extract("showDraftProgress")


# ---------- the page's own functions, under node ----------

_ROW_FUNCS = ("isoLocal", "addDaysIso", "weekdayName", "dayLabel", "isoWeekday", "titleDay",
              "joinWords", "lunchSummary", "sameRowValues")


def _rows(state: dict) -> dict:
    prelude = _var("SAME_COPY") + _var("PREP_WEEKDAYS") + "\n".join(_extract(f) for f in _ROW_FUNCS) + "\n"
    return _node(prelude + f"console.log(JSON.stringify(sameRowValues({json.dumps(state)})));")


# Mon 28 Sep – Sun 4 Oct 2026.
WEEK = ["2026-09-28", "2026-09-29", "2026-09-30", "2026-10-01", "2026-10-02"]


class TestTheRows:
    @_needs_node
    def test_the_mockups_week(self):
        got = _rows({
            "start": "2026-09-28", "count": 7,
            "lunchDays": [
                {"date": WEEK[0], "kind": "prepped", "prep_day": "sunday"},
                {"date": WEEK[1], "kind": "prepped", "prep_day": "sunday"},
                {"date": WEEK[2], "kind": "leftovers", "prep_day": ""},
                {"date": WEEK[3], "kind": "prepped", "prep_day": "sunday"},
                {"date": WEEK[4], "kind": "cooked", "prep_day": ""},
            ],
            "packed": ["Mon", "Tue", "Thu"], "dayNotes": [],
            "moods": ["Something warm", "Protein-heavy"], "cuisines": [], "freeform": "",
        })
        assert got == {
            "days": "Mon 28 → Sun 4 · 7 days",
            "lunches": "3 prepped Sunday, 1 leftovers, 1 cooked Friday",
            "packed": "Mon, Tue, Thu",
            "different": "Nothing marked",
            "mood": "Something warm, Protein-heavy",
            "freeform": "Nothing added",
        }

    @_needs_node
    def test_what_was_said_this_week_and_the_empty_answers(self):
        got = _rows({
            "start": "2026-10-02", "count": 1, "lunchDays": None, "packed": None,
            "dayNotes": [{"day": "Wednesday", "note": "short on time"},
                         {"day": "Friday", "note": "+2 for dinner"}],
            "moods": ["Surprise me"], "cuisines": ["Thai"], "freeform": "  Use the lamb  ",
        })
        assert got["days"] == "Fri 2 · 1 day"
        assert got["lunches"] is None and got["packed"] is None     # no weekday lunch: no rows
        assert got["different"] == "Wednesday: short on time\nFriday: +2 for dinner"
        assert got["mood"] == "Surprise me, Thai"
        assert got["freeform"] == "Use the lamb"
        empty = _rows({"start": "2026-09-28", "count": 5, "lunchDays": [], "packed": [], "dayNotes": [],
                       "moods": [], "cuisines": [], "freeform": ""})
        assert empty["packed"] == "None" and empty["mood"] == "Nothing picked"
        assert empty["days"] == "Mon 28 → Fri 2 · 5 days"

    @_needs_node
    def test_many_cooked_days_read_on_the_day_and_two_prep_days_are_both_named(self):
        days = [{"date": d, "kind": "cooked", "prep_day": ""} for d in WEEK[:3]] + [
            {"date": WEEK[3], "kind": "prepped", "prep_day": "sunday"},
            {"date": WEEK[4], "kind": "prepped", "prep_day": "wednesday"},
        ]
        got = _rows({"start": "2026-09-28", "count": 7, "lunchDays": days, "packed": [], "dayNotes": [],
                     "moods": [], "cuisines": [], "freeform": ""})
        assert got["lunches"] == "2 prepped Sunday and Wednesday, 3 cooked on the day"

    @_needs_node
    def test_a_row_that_says_something_new_is_marked_changed(self):
        """renderSame against a stub screen: the first render is the
        baseline; a later one marks only the rows whose words moved."""
        values = [
            {"days": "Mon 28 → Sun 4 · 7 days", "lunches": None, "packed": None,
             "different": "Nothing marked", "mood": "Comfort food", "freeform": "Nothing added"},
            {"days": "Mon 28 → Sun 4 · 7 days", "lunches": None, "packed": None,
             "different": "Wednesday: short on time", "mood": "Comfort food", "freeform": "Nothing added"},
        ]
        script = (
            "var els = {}; function $(id) { return els[id] || (els[id] = { innerHTML: '', querySelectorAll: function () { return []; } }); }\n"
            "function esc(s) { return String(s); }\n"
            "var sameBaseline = null; var seq = " + json.dumps(values) + "; var n = 0;\n"
            "function sameValues() { return seq[n++]; }\n"
            + _var("SAME_ROWS") + _var("SAME_COPY") + _extract("renderSame") + "\n"
            "renderSame(); var first = $('same-rows').innerHTML; renderSame();\n"
            "console.log(JSON.stringify({ first: first, second: $('same-rows').innerHTML }));\n"
        )
        got = _node(script)
        assert "changed" not in got["first"].replace("same-change", "")
        assert got["first"].count('class="same-row"') == 4          # the two lunch rows aren't drawn
        assert 'class="same-row changed" data-step="2"' in got["second"]
        assert "Different days · Changed" in got["second"]
        assert got["second"].count("same-row changed") == 1


class TestCarryingTheLength:
    """sameAsLastWeek against a stubbed prefill: last week's day count
    travels only when the door named no length and this period has
    nothing of its own."""

    def _run(self, last, days_in_address=False, plan_exists=False, day_count=7):
        script = (
            "var weekStart = '2026-10-05'; var dayCount = " + str(day_count) + "; var range = {};\n"
            "var DAYS_IN_ADDRESS = " + json.dumps(days_in_address) + ";\n"
            "var loads = 0;\n"
            "var data = null;\n"
            "function loadPeriod() { loads++; data = { last_intake: " + json.dumps(last)
            + ", plan_exists: " + json.dumps(plan_exists) + ", intake: null }; return Promise.resolve(true); }\n"
            "function choosePeriod(s, c) { weekStart = s; dayCount = c; }\n"
            "function openRange() { range = { start: weekStart, count: dayCount }; }\n"
            + "async " + _extract("sameAsLastWeek") + "\n"
            "sameAsLastWeek().then(function (same) { console.log(JSON.stringify({ same: same, dayCount: dayCount, weekStart: weekStart, loads: loads })); });\n"
        )
        return _node(script)

    @_needs_node
    def test_a_first_week_is_not_the_page(self):
        assert self._run(None) == {"same": False, "dayCount": 7, "weekStart": "2026-10-05", "loads": 1}

    @_needs_node
    def test_last_weeks_five_days_carry_and_the_dates_stay_the_doors(self):
        got = self._run({"day_count": 5})
        assert got == {"same": True, "dayCount": 5, "weekStart": "2026-10-05", "loads": 2}

    @_needs_node
    def test_a_door_that_named_its_length_or_a_week_already_planned_keeps_its_own(self):
        assert self._run({"day_count": 5}, days_in_address=True)["dayCount"] == 7
        assert self._run({"day_count": 5}, plan_exists=True)["dayCount"] == 7
        assert self._run({"day_count": None})["dayCount"] == 7
