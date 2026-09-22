"""The week intake's design pass (Emily, 2026-09-21) — four approved items,
one branch (intake-design-2026-09-21), all in static/plan-week.html:

1. Which days? — pick the start, then tap the days (board D1). The default
   is the household's horizon from the chosen start, never five days
   assumed; the tiles are toggles that drop a day.
2. Lunches on the go (board D2): "travels well", and a "Nothing on the go"
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
        + _var("START_COPY") + _var("SURPRISE_MOOD") + _var("PERIOD_MAX_DAYS") + _var("PERIOD_STRIP_DAYS")
        + _var("USING_ICONS") + _var("USING_DAY_PHRASES")
        + _date_helpers() + "\n"
        + script
    )
    res = nodeharness.run_node(harness, timeout=30)
    assert res.returncode == 0, f"node failed: {res.stderr}"
    return res.stdout.strip()


# The range card, driven: a Monday, a seven-day horizon, no picker open.
# The strip is a stand-in that keeps the tiles' classes and aria-pressed.
def _card_harness(taps: list[str], start: str = "2026-09-21", count: int = 7) -> str:
    return (
        f"var weekStart = '{start}'; var dayCount = {count}; var horizon = 7; var who = '';\n"
        "var pick = { start: '', end: '' }; var picking = false; var pickTapped = false;\n"
        "function paintCta() {} function renderStartChips() {}\n"
        "function choosePeriod(s, n) { weekStart = s; dayCount = n; }\n"
        "var tiles = [];\n"
        "var strip = { classList: { toggle: function () {} }, scrollLeft: 0, clientWidth: 300, _html: '',\n"
        "  set innerHTML(h) { this._html = h; tiles = (h.match(/<button[^>]*>/g) || []).map(function (tag) {\n"
        "    return { tag: tag, dataset: { day: /data-day=\"([^\"]+)\"/.exec(tag)[1] }, offsetLeft: 0, offsetWidth: 48, addEventListener: function () {} }; }); },\n"
        "  get innerHTML() { return this._html; },\n"
        "  querySelectorAll: function () { return tiles; } };\n"
        "var els = { 'range-strip': strip, 'range-hint': { hidden: true, textContent: '' },\n"
        "  'range-words': { textContent: '' }, 'range-count': { textContent: '' } };\n"
        "function $(id) { return els[id]; }\n"
        + "\n".join(_extract(n) for n in ("droppedList", "periodDays", "keptDays", "toggleDay", "keepDroppedInRange", "pickDay", "renderRangeCard")) + "\n"
        + "var dropped = {}; var droppedTouched = false;\n"
        + "renderRangeCard();\n"
        + "".join(f"toggleDay('{d}');\n" for d in taps)
        + "console.log(JSON.stringify({ words: els['range-words'].textContent, count: els['range-count'].textContent,\n"
        + "  tiles: tiles.map(function (t) { return { day: t.dataset.day, on: /\\bon\\b/.test(t.tag), off: /\\boff\\b/.test(t.tag), pressed: /aria-pressed=\"true\"/.test(t.tag), today: /\\btoday\\b/.test(t.tag) }; }),\n"
        + "  dropped: droppedList(), touched: droppedTouched }));\n"
    )


# ==========================================================================
# 1. Which days? — pick the start, then tap the days (board D1)
# ==========================================================================

class TestWhichDays:
    def test_the_heading_the_line_and_the_chips(self):
        q1 = _section("q1")
        assert "<h1>Which days?</h1>" in q1
        assert "Tap where to start, then the days you want me to plan." in q1
        assert "Starting when?" not in q1
        assert "today: 'Today', tomorrow: 'Tomorrow', pick: 'Pick a date'" in PAGE
        assert "drop: 'tap a day to drop it'" in PAGE
        # The tiles are buttons with a pressed state; a dropped one is
        # dashed and faded.
        assert ".dt.off { opacity: .45; border-style: dashed;" in PAGE
        assert "pressed = ' aria-pressed=\"' + (dropped[d] ? 'false' : 'true') + '\"';" in _extract("renderRangeCard")

    @_needs_node
    def test_the_default_is_the_whole_horizon_from_the_start_never_five_days(self):
        """Emily's one condition: "confirming it's not going to assume 5
        days off the bat?" Seven tiles, all on, from today."""
        got = json.loads(_node(_card_harness([])))
        assert got["words"] == "Mon 21 → Sun 27"
        assert got["count"] == "7 days · tap a day to drop it"
        assert [t["day"] for t in got["tiles"]] == [f"2026-09-{d}" for d in range(21, 28)]
        assert all(t["on"] and t["pressed"] and not t["off"] for t in got["tiles"])
        assert got["tiles"][0]["today"] and not got["tiles"][1]["today"]
        assert got["dropped"] == [] and got["touched"] is False
        # An as-we-go household: three, from the same start.
        three = json.loads(_node(_card_harness([], count=3)))
        assert three["words"] == "Mon 21 → Wed 23" and three["count"] == "3 days · tap a day to drop it"

    @_needs_node
    def test_tapping_a_day_drops_it_and_the_words_follow(self):
        got = json.loads(_node(_card_harness(["2026-09-26", "2026-09-27"])))
        assert got["words"] == "Mon 21 → Fri 25"
        assert got["count"] == "5 days · tap a day to drop it"
        by = {t["day"]: t for t in got["tiles"]}
        assert by["2026-09-26"]["off"] and not by["2026-09-26"]["pressed"] and not by["2026-09-26"]["on"]
        assert by["2026-09-27"]["off"]
        assert by["2026-09-25"]["on"] and by["2026-09-25"]["pressed"]
        assert got["dropped"] == ["2026-09-26", "2026-09-27"] and got["touched"] is True
        # A day in the middle: the ends stay, the count drops.
        mid = json.loads(_node(_card_harness(["2026-09-23"])))
        assert mid["words"] == "Mon 21 → Sun 27" and mid["count"] == "6 days · tap a day to drop it"
        # Dropping the first day moves where the range starts.
        first = json.loads(_node(_card_harness(["2026-09-21"])))
        assert first["words"] == "Tue 22 → Sun 27"

    @_needs_node
    def test_tapping_again_brings_it_back_and_one_day_always_stays(self):
        back = json.loads(_node(_card_harness(["2026-09-26", "2026-09-26"])))
        assert back["dropped"] == [] and back["count"] == "7 days · tap a day to drop it"
        every = json.loads(_node(_card_harness([f"2026-09-{d}" for d in range(21, 28)])))
        assert every["dropped"] == [f"2026-09-{d}" for d in range(21, 27)]
        assert every["words"] == "Sun 27 → Sun 27" and every["count"] == "1 day · tap a day to drop it"

    def test_a_new_start_clears_the_drops_and_a_finished_pick_shows_the_range_as_toggles(self):
        chooser = _extract("chooseStartKey")
        assert "dropped = {};" in chooser
        pick_day = _extract("pickDay")
        assert "picking = false;" in pick_day and "dropped = {};" in pick_day and "renderStartChips();" in pick_day
        strip = _extract("renderRangeCard")
        assert "if (picking) pickDay(b.dataset.day); else toggleDay(b.dataset.day);" in strip

    def test_the_drops_are_saved_with_the_intake_only_when_they_changed(self):
        save = _extract("saveStep")
        assert "if (n === 1) {" in save
        assert "if (skipped.join(',') === skippedAtLoad) return null;" in save
        assert "saveIntake({ skipped_days: skipped })" in save
        adv = _extract("advance")
        assert "await saveStep(1);" in adv
        # Leaving carries them too, and Back never loses them.
        assert "skipped_days: droppedList()" in _extract("leavePayload")
        assert "droppedList().join(',')" in _extract("answersSnapshot")
        # A Re-plan opens with the saved drops, unless the tiles were tapped first.
        fetch = _extract("fetchPeriod")
        assert "if (!droppedTouched) {" in fetch
        assert "savedSkipped.forEach(function (d) { dropped[d] = true; });" in fetch
        assert "skippedAtLoad = savedSkipped.join(',');" in fetch

    def test_the_later_steps_show_only_the_days_being_planned(self):
        # The strip on step 2, the lunch chips on step 3, the away sheet,
        # the holiday cards, the count behind "Nothing different", and the
        # drafting line all read the kept days.
        assert "return ((data && data.days) || []).filter(function (day) { return !dropped[day.date]; });" in _extract("plannedDays")
        assert "$('day-tiles').innerHTML = plannedDays().map(function (day) {" in _extract("renderDays")
        assert "plannedDays().forEach(function (day) {" in _extract("paintTiles")
        assert "$('lunch-days').innerHTML = plannedDays().map(function (day) {" in _extract("renderLunchDays")
        assert "var days = plannedDays().map(function (d) { return d.date; });" in _extract("openAwaySheet")
        assert "var days = plannedDays();" in _extract("buildAwaySheet")
        assert "filter(function (h) { return !dropped[h.date]; })" in _extract("renderHolidayBlocks")
        assert "plannedDays().filter(function (day) { return !!dayNoteFor(day, true); })" in _extract("paintCta")
        show = _extract("showDraftProgress")
        assert "var kept = plannedDays().map(function (d) { return d.date; });" in show
        assert "dayLine = draftDayLine(kept, EXPECTED_DRAFT_MS, paintDraftStatus);" in show
        # Continue on step 1 repaints them for the days just chosen.
        adv = _extract("advance")
        assert adv.index("await saveStep(1);") < adv.index("renderDays();") < adv.index("renderLunchDays();") < adv.index("showStep(2);")
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
    def test_the_line_and_the_pill(self):
        q3 = _section("q3")
        assert "I&rsquo;ll keep those to food that travels well." in q3
        assert "packs cold" not in PAGE
        assert q3.index('id="lunch-days"') < q3.index('id="lunch-none"')
        assert '<button type="button" class="chip" id="lunch-none" aria-pressed="false">Nothing on the go</button>' in q3
        assert "'I’ve ticked your usual days — I’ll keep those to food that travels well.'" in PAGE

    def test_the_pill_clears_the_days_and_a_day_unselects_the_pill(self):
        assert "answers.packed_lunch_days.length = 0;" in _extract("chooseNoLunches")
        paint = _extract("paintLunchDays")
        assert "var none = !answers.packed_lunch_days.length;" in paint
        assert "$('lunch-none').classList.toggle('on', none);" in paint
        assert "$('lunch-none').setAttribute('aria-pressed', none ? 'true' : 'false');" in paint
        assert "$('lunch-none').addEventListener('click', chooseNoLunches);" in PAGE

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
        assert lines[0]["text"] == (
            "Tuesday and Thursday are short on time, Tuesday Emily’s out for dinner, "
            "Wednesday’s leftovers, Friday you’ve got time, and Saturday nobody’s home."
        )
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
