"""The week intake's design pass (Emily, 2026-09-21) — four approved items,
one branch (intake-design-2026-09-21), all in static/plan-week.html:

1. Which days? — pick the start, then tap the days (board D1). The default
   is the household's horizon from the chosen start, never five days
   assumed; the tiles are toggles that drop a day. (Replaced 2026-09-22 by
   Emily's calendar range — tests/test_which_days_calendar_2026_09_22.py;
   what survives of this item is pinned below.)
2. Lunches on the go (board D2): the days, and a "Nothing on the go"
   pill under the day chips instead of the quiet line.
3. Building — "Got it", condensed and human (board D3, with her change:
   keep the icon rows, fewer of them, each a sentence).
4. The step-5 title, in her words.

Source-level checks pin the bones and the words; the node harness runs
the page's own functions the way tests/test_intake_motion_2026_09_21.py
does. The server half of item 1 is tests/test_which_days_2026_09_21.py.
Every test here is red on main.
"""
from __future__ import annotations

import json
import re
import shutil
from pathlib import Path

import pytest

import nodeharness

REPO = Path(__file__).resolve().parents[1]
PAGE = (REPO / "static" / "plan-week.html").read_text(encoding="utf-8")
AGENT = (REPO / "app" / "agent.py").read_text(encoding="utf-8").replace("\\\n", "")

_needs_node = pytest.mark.skipif(
    shutil.which("node") is None, reason="node is needed to execute the screen's own functions"
)


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


def _var(name: str, source: str = PAGE) -> str:
    m = re.search(rf"  var {name} = [\s\S]*?;\n", source)
    assert m, name
    return m.group(0)


def _section(qid: str) -> str:
    start = PAGE.index(f'<section id="{qid}"')
    return PAGE[start:PAGE.index("</section>", start)]


def _date_helpers() -> str:
    return "\n".join(
        _extract(n) for n in ("isoLocal", "todayIso", "addDaysIso", "daysBetween", "weekdayName", "dayLabel", "periodRangeLabel")
    )


def _node(script: str, today: str = "2026-09-21") -> str:
    harness = (
        "const _RealDate = Date;\n"
        f"class _Pinned extends _RealDate {{ constructor(...a) {{ super(...(a.length ? a : ['{today}T09:00:00'])); }} "
        f"static now() {{ return new _RealDate('{today}T09:00:00').getTime(); }} }}\n"
        "Date = _Pinned;\n"
        "function esc(s) { return String(s == null ? '' : s); }\n"
        + _var("RANGE_COPY") + _var("SURPRISE_MOOD") + _var("PERIOD_MAX_DAYS") + _var("PERIOD_STRIP_DAYS")
        + _var("USING_ICONS") + _var("USING_DAY_PHRASES") + _var("PREP_WEEKDAYS")
        + _date_helpers() + "\n"
        # Step 3's line in "Got it" (weekday lunches, 2026-09-25).
        + _extract("lunchUsingLine") + "\n" + _extract("titleDay") + "\n" + _extract("isoWeekday") + "\n"
        + script
    )
    res = nodeharness.run_node(harness, timeout=30)
    assert res.returncode == 0, f"node failed: {res.stderr}"
    return res.stdout.strip()


# ==========================================================================
# 1. Which days? — pick the start, then tap the days (board D1)
# ==========================================================================

class TestWhichDays:
    # The chips, the drop-a-day toggles and "tap a day to drop it" this
    # class pinned went on 2026-09-22 (Emily: "tapping a day DROPS it —
    # the opposite of every calendar"). Their replacements are pinned in
    # tests/test_which_days_calendar_2026_09_22.py; what still holds of
    # board D1 is below.

    def test_the_heading_and_the_line(self):
        q1 = _section("q1")
        assert "<h1>Which days?</h1>" in q1
        assert "Tap the first day, then the last." in q1
        assert "Starting when?" not in q1
        shown = re.sub(r"//[^\n]*|/\*[\s\S]*?\*/|<!--[\s\S]*?-->", "", PAGE)
        assert "drop it" not in shown
        assert ".dt.off" not in PAGE

    def test_the_old_screens_drops_are_cleared_by_the_first_continue(self):
        # The intake still carries skipped_days (the server is untouched);
        # this step sends none, and writes only when an older screen had
        # saved some — the empty set over them.
        assert "function droppedList() { return []; }" in PAGE
        save = _extract("saveStep")
        assert "if (n === 1) {" in save
        assert "if (skipped.join(',') === skippedAtLoad) return null;" in save
        assert "saveIntake({ skipped_days: skipped })" in save
        adv = _extract("advance")
        assert "await saveStep(1);" in adv
        # Leaving carries them too (the empty set), and Back never loses them.
        assert "skipped_days: droppedList()" in _extract("leavePayload")
        assert "droppedList().join(',')" in _extract("answersSnapshot")
        fetch = _extract("fetchPeriod")
        assert "skippedAtLoad = savedSkipped.join(',');" in fetch
        assert "dropped[" not in fetch

    def test_the_later_steps_show_only_the_days_being_planned(self):
        # The strip on step 2, the lunch chips on step 3, the away sheet,
        # the holiday cards, the count behind "Nothing different", and the
        # drafting line all read the kept days.
        # Every day of the period since 2026-09-22 — nothing is dropped.
        assert "return (data && data.days) || [];" in _extract("plannedDays")
        assert "$('day-tiles').innerHTML = plannedDays().map(function (day) {" in _extract("renderDays")
        assert "plannedDays().forEach(function (day) {" in _extract("paintTiles")
        # Step 3 (weekday lunches since 2026-09-25) reads the period's
        # weekdays off plannedDays.
        assert "function currentLunchDates() { return lunchDates(plannedDays(), nobodyHomeFor); }" in PAGE
        assert "var days = plannedDays().map(function (d) { return d.date; });" in _extract("openAwaySheet")
        assert "var days = plannedDays();" in _extract("buildAwaySheet")
        assert "host.innerHTML = ((data && data.holidays) || []).map(holidayBlockHtml).join('');" in _extract("renderHolidayBlocks")
        assert "plannedDays().filter(function (day) { return !!dayNoteFor(day, true); })" in _extract("paintCta")
        show = _extract("showDraftProgress")
        assert "var kept = plannedDays().map(function (d) { return d.date; });" in show
        assert "dayLine = draftDayLine(kept, EXPECTED_DRAFT_MS, paintDraftStatus);" in show
        # Continue on step 1 repaints them for the days just chosen.
        adv = _extract("advance")
        assert adv.index("await saveStep(1);") < adv.index("renderDays();") < adv.index("showStep(2);")
        # Step 3 lays itself out as it opens, from the days as they are then.
        assert "if (to === 3) renderLunchStep();" in adv
        # A lunch ticked on a day since dropped is not saved.
        assert "answers.packed_lunch_days = answers.packed_lunch_days.filter(function (d) { return kept.indexOf(d) !== -1; });" in _extract("saveStep")

    def test_no_yesterday_line_and_no_five_day_assumption_anywhere(self):
        q1 = _section("q1")
        assert "already eaten" not in q1 and "Yesterday" not in q1
        assert "5 days" not in re.sub(r"//[^\n]*|/\*[\s\S]*?\*/|<!--[\s\S]*?-->", "", PAGE)


# ==========================================================================
# 2. Lunches on the go (board D2)
# ==========================================================================

class TestLunchesOnTheGo:
    # Board D2's step ("Any lunches on the go?", a "Nothing on the go"
    # pill) became step 3's "Taking it with you" row on 2026-09-25
    # (Loop Board "Plan a week step 3: weekday lunches", mockup A1). The
    # new step is pinned in tests/test_weekday_lunches.py; what still
    # holds of D2 is below.
    def test_the_on_the_go_days_are_a_row_on_the_lunch_step(self):
        q3 = _section("q3")
        assert '<p class="eyebrow group-eyebrow">Taking it with you</p>' in q3
        assert '<div class="chip-row" id="lunch-days"></div>' in q3
        assert "travels well" not in q3
        assert "packs cold" not in PAGE
        assert "lunch-none" not in PAGE

    def test_a_day_toggles_in_the_packed_days(self):
        render = _extract("renderLunchStep")
        assert "var i = answers.packed_lunch_days.indexOf(d);" in render
        assert "if (i === -1) answers.packed_lunch_days.push(d);" in render

    def test_the_foot_is_continue_only(self):
        assert "var SKIP_LABELS = { 2: 'Nothing different', 5: 'Nothing else' };" in PAGE
        assert "3:" not in _var("SKIP_LABELS")
        assert "step === 3" not in _extract("paintCta")

    def test_the_planners_rule_is_travels_well_fine_cold_or_reheated(self):
        assert "constrained to food that travels well and is fine cold or reheated" in AGENT
        assert "genuinely travels cold" not in AGENT and "holds up till noon" not in AGENT
        assert "packs cold" not in AGENT
        assert '"travels well, good cold or reheated"' in AGENT


# ==========================================================================
# 3. Building — "Got it", condensed and human (board D3, with her change)
# ==========================================================================

def _using(script: str) -> list:
    fns = "\n".join(_extract(n) for n in (
        "joinWords", "joinClauses", "sentence", "humanAttendance", "lowerFirst", "usingDaysSentence", "usingLines",
    ))
    return json.loads(_node(fns + "\n" + script))


def _weekday_fn() -> str:
    return "(d) => weekdayName(d)"


class TestGotIt:
    def test_the_card_is_got_it_with_the_icon_rows_and_no_lead_line(self):
        section = _section("draft-progress")
        assert '<p class="using-eyebrow">Got it</p>' in section
        assert "What I&rsquo;m using" not in section
        assert "Here&rsquo;s what I&rsquo;m building this week from:" not in section
        assert ".using-lead" not in PAGE
        assert 'id="using-rows"' in section
        # Still icon rows — stroke icons at the one width — and the note
        # row clamps to two lines.
        assert 'stroke-width="2.2"' in _extract("usingRowHtml")
        assert "-webkit-line-clamp: 2;" in PAGE
        assert "(line.note ? ' using-note' : '')" in _extract("usingRowHtml")

    @_needs_node
    def test_the_days_are_one_sentence(self):
        intake = {
            "night_tags": {"2026-09-22": ["rush"], "2026-09-24": ["rush"], "2026-09-23": ["left"], "2026-09-25": ["unrushed"]},
        }
        lines = _using(f"console.log(JSON.stringify(usingLines({json.dumps(intake)}, {_weekday_fn()}, false)));")
        assert lines == [{
            "icon": "home",
            "text": "Tuesday and Thursday are short on time, Wednesday’s leftovers, and Friday you’ve got time.",
        }]
        # Nobody home and someone out ride in the same sentence.
        words = {"2026-09-26": ["nobody home"], "2026-09-22": ["Emily out for dinner"]}
        lines = _using(
            f"console.log(JSON.stringify(usingLines({json.dumps(intake)}, {_weekday_fn()}, false, {json.dumps(words)})));"
        )
        # A day is named ONCE, its notes together (verifier, 2026-09-21:
        # "Tuesday is short on time and Tuesday Emily's out for dinner"
        # named it twice); days with the same notes are named together.
        assert lines[0]["text"] == (
            "Tuesday is short on time and Emily’s out for dinner, Wednesday’s leftovers, "
            "Thursday is short on time, Friday you’ve got time, and Saturday nobody’s home."
        )
        # The verifier's own case, two clauses, one with an "and" inside.
        pair = _using(
            f"console.log(JSON.stringify(usingLines({{night_tags: {{'2026-09-22': ['rush'], '2026-09-24': ['unrushed']}}}}, {_weekday_fn()}, false, {{'2026-09-22': ['Emily out for dinner']}})));"
        )
        assert pair[0]["text"] == "Tuesday is short on time and Emily’s out for dinner, and Thursday you’ve got time."
        # Three notes on one day.
        three = _using(
            f"console.log(JSON.stringify(usingLines({{night_tags: {{'2026-09-22': ['rush', 'guests']}}, guest_counts: {{'2026-09-22': {{adults: 2, children: 0}}}}}}, {_weekday_fn()}, false, {{'2026-09-22': ['Emily out for lunch']}})));"
        )
        assert three[0]["text"] == "Tuesday is short on time, Emily’s out for lunch and you’ve got 2 guests."
        # Two of a kind, and one alone.
        one = _using(f"console.log(JSON.stringify(usingLines({{night_tags: {{'2026-09-23': ['left']}}}}, {_weekday_fn()}, false)));")
        assert one == [{"icon": "home", "text": "Wednesday’s leftovers."}]
        two = _using(f"console.log(JSON.stringify(usingLines({{night_tags: {{'2026-09-23': ['left'], '2026-09-25': ['left']}}}}, {_weekday_fn()}, false)));")
        assert two == [{"icon": "home", "text": "Wednesday and Friday are leftovers."}]

    @_needs_node
    def test_guests_and_who_is_out_read_as_people_would_say_them(self):
        intake = {"night_tags": {"2026-09-26": ["guests"]}, "guest_counts": {"2026-09-26": {"adults": 2, "children": 1}}}
        lines = _using(f"console.log(JSON.stringify(usingLines({json.dumps(intake)}, {_weekday_fn()}, false)));")
        assert lines == [{"icon": "home", "text": "Saturday you’ve got 3 guests."}]
        # Said by attendance already: not said twice.
        words = {"2026-09-26": ["+3 for dinner"], "2026-09-24": ["Emily and Vic out for lunch"], "2026-09-25": ["nobody home for lunch"]}
        lines = _using(
            f"console.log(JSON.stringify(usingLines({json.dumps(intake)}, {_weekday_fn()}, false, {json.dumps(words)})));"
        )
        assert lines[0]["text"] == "Thursday Emily and Vic are out for lunch, Friday nobody’s home for lunch, and Saturday 3 guests for dinner."
        # Two days with the same note are named together.
        same = _using(
            f"console.log(JSON.stringify(usingLines({{night_tags: {{'2026-09-22': ['rush'], '2026-09-24': ['rush']}}}}, {_weekday_fn()}, false, {{'2026-09-22': ['Emily out for dinner'], '2026-09-24': ['Emily out for dinner']}})));"
        )
        assert same[0]["text"] == "Tuesday and Thursday are short on time and Emily’s out for dinner."

    @_needs_node
    def test_lunches_mood_note_and_the_no_repeat_line(self):
        intake = {
            "packed_lunch_days": ["2026-09-24", "2026-09-22"],
            "moods": ["Comfort food"], "cuisines": ["Mexican", "Thai"],
            "freeform": "Friday is pizza night",
        }
        lines = _using(f"console.log(JSON.stringify(usingLines({json.dumps(intake)}, {_weekday_fn()}, true)));")
        assert lines == [
            {"icon": "bag", "text": "Lunches on the go Tuesday and Thursday."},
            {"icon": "pot", "text": "Comfort food, Mexican and Thai."},
            {"icon": "note", "text": "“Friday is pizza night”", "note": True},
            {"icon": "star", "text": "No dinner you had last week."},
        ]
        # One lunch; two moods read as a list, the second lowercased.
        lines = _using(
            f"console.log(JSON.stringify(usingLines({{packed_lunch_days: ['2026-09-22'], moods: ['Comfort food', 'On the grill']}}, {_weekday_fn()}, false)));"
        )
        assert lines == [
            {"icon": "bag", "text": "Lunch on the go Tuesday."},
            {"icon": "pot", "text": "Comfort food and on the grill."},
        ]

    @_needs_node
    def test_surprise_me_leans_on_the_cuisines_and_promises_nothing_had_before(self):
        lines = _using(
            f"console.log(JSON.stringify(usingLines({{moods: ['Surprise me'], cuisines: ['Korean', 'Japanese']}}, {_weekday_fn()}, true)));"
        )
        assert lines == [
            {"icon": "pot", "text": "Surprise me, leaning Korean and Japanese."},
            {"icon": "star", "text": "Nothing you’ve had from me before."},
        ]
        # No cuisines, and a first week with nothing on record: no promise.
        lines = _using(f"console.log(JSON.stringify(usingLines({{moods: ['Surprise me']}}, {_weekday_fn()}, false)));")
        assert lines == [{"icon": "pot", "text": "Surprise me."}]

    @_needs_node
    def test_only_answers_given_produce_a_row(self):
        assert _using(f"console.log(JSON.stringify(usingLines({{}}, {_weekday_fn()}, false)));") == []
        assert _using(f"console.log(JSON.stringify(usingLines({{night_tags: {{'2026-09-22': ['normal']}}}}, {_weekday_fn()}, false)));") == []

    def test_the_copy_passes_the_seven_rules(self):
        # Contractions, one breath, nothing a dashboard would say.
        for line in ("is short on time", "you’ve got time", "nobody’s home", "you’ve got ", "Nothing you’ve had from me before.", "No dinner you had last week."):
            assert line in PAGE, line
        assert "USING_TAG_WORDS" not in PAGE
        assert "hosting guests" not in PAGE and "at the table" not in _extract("usingDaysSentence")


# ==========================================================================
# 4. The step-5 title (Emily's pick)
# ==========================================================================

class TestStepFiveTitle:
    def test_the_heading_is_her_words(self):
        q5 = _section("q5")
        assert "<h1>Anything else you want to share for planning this week?</h1>" in q5
        assert "Anything else I should plan around?" not in PAGE
        # The box, the hint and the quiet line are unchanged.
        assert 'placeholder="Friday is pizza night. I want to use the lamb in the freezer."' in q5
        assert "5: 'Nothing else'" in _var("SKIP_LABELS")
