"""Which days? — a calendar's range (Emily, 2026-09-22, option 1).

What she hit, on Tue 22 from her phone: she wanted Wed 23 through next
Wed 30 and couldn't get there. Tapping a day DROPPED it (the opposite of
every calendar); the strip showed only the period's seven days, so Wed 30
wasn't on it; and the Today / Tomorrow / Pick a date chips were a second
control on top of the strip, with the range picker she never found behind
the third. Her words: "this view is still not intuitive. it's confusing
what to click… we need to fix this flow".

What she chose, all in static/plan-week.html:
- The screen opens with Pomona's suggested range already chosen (the
  range the door named — unchanged default logic), so most weeks are one
  tap on Continue.
- To change it: tap the first day, then the last; the days between fill
  in. A third tap starts over from that day. A tap on a day before the
  first makes it the first (rangeAfterTap has the rules).
- One strip, today through the 28-day ceiling, scrolling sideways. The
  first and last day celadon, the days between the lighter tint. "Wed 23
  → Wed 30" with "8 days" beside it.
- No chips, no second picker, no dropping a day on this step. Skipping a
  day is step 2's day sheet (everyone out for every meal = nobody home);
  the server's skipped_days stays, this step sends none.

The node harness runs the page's own functions the way
tests/test_intake_design_2026_09_21.py does. Every screen test here is
red on main (a2e3129); the three in TestTheServerTakesEightDays pass
there too — they pin the server half her case depends on (an 8-day
intake, an 8-day generate request, skipped_days still read and
clearable), which this branch leaves untouched.
"""
from __future__ import annotations

import datetime
import json
import re
import shutil
from pathlib import Path

import pytest

import nodeharness
from app import main as app_main
from app import tools

REPO = Path(__file__).resolve().parents[1]
PAGE = (REPO / "static" / "plan-week.html").read_text(encoding="utf-8")
SHELL_JS = (REPO / "static" / "shell.js").read_text(encoding="utf-8")

_needs_node = pytest.mark.skipif(
    shutil.which("node") is None, reason="node is needed to execute the screen's own functions"
)

# Emily's day: Tuesday 22 September 2026.
TUESDAY = "2026-09-22"


def _extract(name: str, source: str = PAGE) -> str:
    start = source.index(f"function {name}(")
    if source[start - len("async "):start] == "async ":
        start -= len("async ")
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


def _shown(source: str = PAGE) -> str:
    """The page without its comments — what a household can read."""
    return re.sub(r"//[^\n]*|/\*[\s\S]*?\*/|<!--[\s\S]*?-->", "", source)


def _node(script: str, today: str = TUESDAY) -> str:
    harness = (
        "const _RealDate = Date;\n"
        f"class _Pinned extends _RealDate {{ constructor(...a) {{ super(...(a.length ? a : ['{today}T09:00:00'])); }} "
        f"static now() {{ return new _RealDate('{today}T09:00:00').getTime(); }} }}\n"
        "Date = _Pinned;\n"
        "function esc(s) { return String(s == null ? '' : s); }\n"
        + _var("RANGE_COPY") + _var("PERIOD_STRIP_DAYS") + _var("PERIOD_MAX_DAYS")
        + "\n".join(_extract(n) for n in ("isoLocal", "todayIso", "addDaysIso", "daysBetween", "weekdayName", "dayLabel"))
        + "\n" + script
    )
    res = nodeharness.run_node(harness, timeout=30)
    assert res.returncode == 0, f"node failed: {res.stderr}"
    return res.stdout.strip()


def _tap_rule(current: dict | None, day: str, max_days: int = 28) -> dict:
    cur = json.dumps(current) if current is not None else "null"
    return json.loads(_node(
        _extract("rangeAfterTap") + "\n"
        f"console.log(JSON.stringify(rangeAfterTap({cur}, '{day}', {max_days})));"
    ))


# The step, driven: the page's own openRange / renderRangeCard / paintRange
# / tapRangeDay / choosePeriod against a stand-in strip whose tiles keep
# their classes, a stand-in Continue, and a fetch that records what
# saveIntake sends.
def _step_harness(start: str, count: int, taps: list[str], save: bool = False) -> str:
    return (
        f"var weekStart = '{start}'; var dayCount = {count}; var who = ''; var step = 1;\n"
        "var history = { replaceState: function () {} };\n"
        "function encodeURIComponent(s) { return s; }\n"
        "var cta = { disabled: false };\n"
        "function paintCta() { if (step === 1) cta.disabled = !range.end; }\n"
        "function Tile(day) { this.dataset = { day: day }; this.cls = {}; this.attrs = {};\n"
        "  this.offsetLeft = 0; this.offsetWidth = 46; this.classList = { toggle: (c, on) => { this.cls[c] = !!on; } }; }\n"
        "Tile.prototype.setAttribute = function (k, v) { this.attrs[k] = v; };\n"
        "Tile.prototype.addEventListener = function () {};\n"
        "var tiles = []; var builds = 0;\n"
        "var strip = { scrollLeft: 0, offsetLeft: 0, clientWidth: 330, _html: '',\n"
        "  set innerHTML(h) { builds++; this._html = h; tiles = (h.match(/data-day=\"[^\"]+\"/g) || []).map(function (m, i) {\n"
        "    var t = new Tile(m.slice(10, -1)); t.offsetLeft = i * 50;\n"
        "    t.today = new RegExp('class=\"dt today\" data-day=\"' + t.dataset.day + '\"').test(h); return t; }); },\n"
        "  get innerHTML() { return this._html; },\n"
        "  querySelectorAll: function () { return tiles; },\n"
        "  querySelector: function (sel) { var d = /data-day=\"([^\"]+)\"/.exec(sel)[1];\n"
        "    return tiles.filter(function (t) { return t.dataset.day === d; })[0] || null; } };\n"
        "var els = { 'range-strip': strip, 'range-words': { textContent: '' }, 'range-count': { textContent: '' } };\n"
        "function $(id) { return els[id]; }\n"
        "var sent = null;\n"
        "function fetch(url, opts) { sent = { url: url, body: JSON.parse(opts.body) };\n"
        "  return Promise.resolve({ ok: true, json: function () { return Promise.resolve({ intake_id: 1 }); } }); }\n"
        + "\n".join(_extract(n) for n in (
            "openRange", "rangeAfterTap", "rangeStripDays", "droppedList", "tapRangeDay",
            "choosePeriod", "renderRangeCard", "paintRange", "saveIntake"))
        + "\nvar range = { start: '', end: '' };\n"
        + "openRange(); renderRangeCard();\n"
        + "var opened = { words: els['range-words'].textContent, count: els['range-count'].textContent, scroll: strip.scrollLeft };\n"
        + "var trail = [];\n"
        + "".join(
            f"tapRangeDay('{d}'); trail.push({{ words: els['range-words'].textContent, count: els['range-count'].textContent, cta: cta.disabled }});\n"
            for d in taps
        )
        + "function report() { return { opened: opened, trail: trail, builds: builds,\n"
        + "  words: els['range-words'].textContent, count: els['range-count'].textContent, cta: cta.disabled,\n"
        + "  weekStart: weekStart, dayCount: dayCount, range: range, sent: sent,\n"
        + "  days: tiles.map(function (t) { return t.dataset.day; }),\n"
        + "  on: tiles.filter(function (t) { return t.cls.on; }).map(function (t) { return t.dataset.day; }),\n"
        + "  inside: tiles.filter(function (t) { return t.cls.in; }).map(function (t) { return t.dataset.day; }),\n"
        + "  pressed: tiles.filter(function (t) { return t.attrs['aria-pressed'] === 'true'; }).map(function (t) { return t.dataset.day; }),\n"
        + "  today: tiles.filter(function (t) { return t.today; }).map(function (t) { return t.dataset.day; }) }; }\n"
        + (
            "saveIntake({ skipped_days: droppedList() }).then(function () { console.log(JSON.stringify(report())); });\n"
            if save else "console.log(JSON.stringify(report()));\n"
        )
    )


# ==========================================================================
# The screen: one strip, no chips
# ==========================================================================

class TestTheScreen:
    def test_no_chips_and_no_second_picker(self):
        """RED ON MAIN: q1 carried #start-chips and the Today / Tomorrow /
        Pick a date words."""
        q1 = _section("q1")
        assert 'id="start-chips"' not in q1
        assert 'id="range-hint"' not in q1
        shown = _shown()
        for gone in ("Pick a date", "tap a day to drop it", "Tap where to start"):
            assert gone not in shown, gone
        for fn in ("startOptions", "chooseStartKey", "renderStartChips", "toggleDay", "pickDay", "keptDays"):
            assert f"function {fn}(" not in PAGE, fn
        assert "START_COPY" not in PAGE

    def test_the_heading_the_line_and_the_card(self):
        q1 = _section("q1")
        assert "<h1>Which days?</h1>" in q1
        assert '<p class="step-sub" id="start-sub">Tap the first day, then the last.</p>' in q1
        assert q1.index("<h1>Which days?</h1>") < q1.index('id="range-card"')
        assert '<div class="stripe" id="range-strip" role="group" aria-label="Days to plan"></div>' in q1
        assert "var RANGE_COPY = { lastDay: 'Tap the last day' };" in PAGE

    def test_the_strip_runs_to_the_ceiling_in_step_with_the_shell(self):
        assert "var PERIOD_STRIP_DAYS = 28;" in PAGE and "var PERIOD_MAX_DAYS = 28;" in PAGE
        assert "var PERIOD_STRIP_DAYS = 28;" in SHELL_JS and "var PERIOD_MAX_DAYS = 28;" in SHELL_JS
        # Twenty-eight tiles scroll; none squeezes under 44px (hard rule 6).
        assert "#range-strip .dt { flex: 0 0 auto; min-width: 46px; }" in PAGE

    def test_the_ends_are_celadon_and_the_days_between_the_tint(self):
        assert ".dt.on { background: var(--celadon); border-color: var(--celadon); color: var(--on-accent-ink); }" in PAGE
        assert ".dt.in { background: var(--celadon-tint); border-color: var(--celadon-edge); color: var(--ink-on-celadon); }" in PAGE
        # Spruce ink on the light accent, never ivory (hard rule 1).
        on_rules = re.findall(r"\.dt\.on[^{]*\{[^}]*\}", PAGE)
        assert on_rules and not any("ivory" in r or "#fff" in r for r in on_rules)

    def test_the_screen_opens_on_the_suggested_range(self):
        load = _extract("load")
        assert load.index("weekStart = clampStart(weekStart, todayIso());") < load.index("openRange();") < load.index("renderStartStep();")


# ==========================================================================
# The tap rules
# ==========================================================================

@_needs_node
class TestTheTapRules:
    def test_a_tap_on_a_whole_range_starts_a_new_one(self):
        # What the screen opens with, and what a third tap meets.
        assert _tap_rule({"start": "2026-09-22", "end": "2026-09-28"}, "2026-09-23") == {"start": "2026-09-23", "end": ""}

    def test_the_second_tap_is_the_last_day(self):
        assert _tap_rule({"start": "2026-09-23", "end": ""}, "2026-09-30") == {"start": "2026-09-23", "end": "2026-09-30"}

    def test_the_first_day_again_is_a_one_day_range(self):
        assert _tap_rule({"start": "2026-09-23", "end": ""}, "2026-09-23") == {"start": "2026-09-23", "end": "2026-09-23"}

    def test_an_earlier_day_becomes_the_first(self):
        assert _tap_rule({"start": "2026-09-25", "end": ""}, "2026-09-23") == {"start": "2026-09-23", "end": ""}

    def test_never_longer_than_the_ceiling(self):
        assert _tap_rule({"start": "2026-09-22", "end": ""}, "2026-11-01") == {"start": "2026-09-22", "end": "2026-10-19"}

    def test_nothing_chosen_yet_is_a_first_day(self):
        assert _tap_rule(None, "2026-09-24") == {"start": "2026-09-24", "end": ""}


# ==========================================================================
# Emily's case, end to end on the step
# ==========================================================================

@_needs_node
class TestEmilysWeek:
    def test_it_opens_with_the_suggestion_chosen_and_continue_ready(self):
        got = json.loads(_node(_step_harness(TUESDAY, 7, [])))
        assert got["opened"]["words"] == "Tue 22 → Mon 28" and got["opened"]["count"] == "7 days"
        assert got["cta"] is False
        assert got["on"] == ["2026-09-22", "2026-09-28"]
        assert got["inside"] == [f"2026-09-{d}" for d in range(23, 28)]
        assert got["pressed"] == [f"2026-09-{d}" for d in range(22, 29)]
        # The strip: today, then the whole ceiling — Wed 30 is on it.
        assert got["days"][0] == TUESDAY and len(got["days"]) == 28
        assert got["days"][-1] == "2026-10-19"
        assert "2026-09-30" in got["days"]
        assert got["today"] == [TUESDAY]

    def test_wed_23_then_wed_30_is_eight_days(self):
        """RED ON MAIN: there was no Wed 30 to tap, and a tap dropped a day."""
        got = json.loads(_node(_step_harness(TUESDAY, 7, ["2026-09-23", "2026-09-30"])))
        first, second = got["trail"]
        # After the first tap: half a range, and Continue waits for the last day.
        assert first == {"words": "Wed 23 → …", "count": "Tap the last day", "cta": True}
        # After the second: the range, the count, Continue ready.
        assert second == {"words": "Wed 23 → Wed 30", "count": "8 days", "cta": False}
        assert got["on"] == ["2026-09-23", "2026-09-30"]
        assert got["inside"] == [f"2026-09-{d}" for d in range(24, 30)]
        assert (got["weekStart"], got["dayCount"]) == ("2026-09-23", 8)
        # A tap repaints; it never rebuilds the strip under the thumb.
        assert got["builds"] == 1

    def test_the_intake_save_carries_day_count_eight_and_no_dropped_days(self):
        got = json.loads(_node(_step_harness(TUESDAY, 7, ["2026-09-23", "2026-09-30"], save=True)))
        assert got["sent"]["url"] == "/api/week/2026-09-23/intake"
        assert got["sent"]["body"]["day_count"] == 8
        assert got["sent"]["body"]["skipped_days"] == []

    def test_a_third_tap_starts_over(self):
        got = json.loads(_node(_step_harness(TUESDAY, 7, ["2026-09-23", "2026-09-30", "2026-09-25"])))
        assert got["words"] == "Fri 25 → …" and got["cta"] is True
        assert got["on"] == ["2026-09-25"] and got["inside"] == []
        # The period stays the last whole range until the new one closes.
        assert (got["weekStart"], got["dayCount"]) == ("2026-09-23", 8)

    def test_one_day_reads_as_one_day(self):
        got = json.loads(_node(_step_harness(TUESDAY, 7, ["2026-09-24", "2026-09-24"])))
        assert (got["words"], got["count"]) == ("Thu 24", "1 day")
        assert got["dayCount"] == 1

    def test_a_range_starting_later_opens_scrolled_to_its_first_day(self):
        # Tiles every 50px on a 330px strip: Oct 5 is the 14th, off-screen.
        got = json.loads(_node(_step_harness("2026-10-05", 7, [])))
        assert got["opened"]["scroll"] == 13 * 50 - 8
        # From today or tomorrow the strip opens at its start, today in view.
        assert json.loads(_node(_step_harness(TUESDAY, 7, [])))["opened"]["scroll"] == 0
        assert json.loads(_node(_step_harness("2026-09-23", 8, [])))["opened"]["scroll"] == 0

    def test_a_period_running_past_the_ceiling_is_all_on_the_strip(self):
        got = json.loads(_node(_step_harness("2026-10-15", 7, [])))
        assert got["days"][-1] == "2026-10-21"


# ==========================================================================
# The server takes her eight days, and generation is asked for eight
# ==========================================================================

class TestTheServerTakesEightDays:
    def test_an_eight_day_intake_saves_an_answer_about_the_eighth_day(self, signed_in):
        start = datetime.date.today() + datetime.timedelta(days=1)
        eighth = (start + datetime.timedelta(days=7)).isoformat()
        week = start.isoformat()
        res = signed_in.post(f"/api/week/{week}/intake", json={
            "skipped_days": [], "day_count": 8, "night_tags": {eighth: ["rush"]},
        })
        assert res.status_code == 200, res.text
        assert res.json()["night_tags"] == {eighth: ["rush"]}
        assert res.json()["skipped_days"] == []
        # Seven days from the same start doesn't reach it: the count is
        # what makes the eighth day part of the period.
        seven = signed_in.post(f"/api/week/{week}/intake", json={"day_count": 7, "night_tags": {eighth: ["rush"]}})
        assert seven.status_code == 400

    def test_generation_is_asked_for_the_days_chosen(self):
        # The page hands generation the step's own count (the drafting
        # code is not run here: a real generation is a model call).
        # Pinned on the day count alone, not the whole call: the dropped-
        # stream fix (branch fix-draft-stream-drop, same night) adds a
        # run_token to the same object, and this test is about the days.
        assert re.search(r"await streamGenerate\(\{ intake_id: saved\.intake_id, day_count: dayCount[,\s}]",
                         _extract("advance"))
        week = (datetime.date.today() + datetime.timedelta(days=1)).isoformat()
        req = app_main.WeekGenerateRequest(intake_id=1, day_count=8)
        assert app_main._validated_period(week, req) == (week, 8)

    def test_saved_dropped_days_still_read_and_are_cleared_by_the_new_step(self):
        # The server's skipped_days is untouched (an intake saved by the
        # old screen still reads)...
        week = (datetime.date.today() + datetime.timedelta(days=1)).isoformat()
        days = tools.period_dates(week, 7)
        tools.save_week_intake(week, skipped_days=[days[3]], day_count=7)
        assert tools.get_week_intake_prefill(week, 7)["intake"]["skipped_days"] == [days[3]]
        # ...and the empty set this step sends clears them.
        assert tools.save_week_intake(week, skipped_days=[], day_count=7)["skipped_days"] == []
