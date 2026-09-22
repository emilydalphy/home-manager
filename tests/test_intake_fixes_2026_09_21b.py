"""Two Loop Board fixes on the intake, Emily's phone test of Monday
2026-09-21 (branch intake-fixes-2026-09-21b).

Card A — the range from any start is the household's horizon, never one
day; the "yesterday" line goes. What she saw on "Starting when?": with
Today · Mon 21 selected, "Mon 21 → Mon 21 · 1 day"; with Tomorrow · Tue 22,
"Tue 22 → Sat 26 · 5 days" and no way to change it. Two causes:

1. weekly_plan.next_period_after cut the stretch it offered short to stop
   the day before another live plan — a draft beginning on the Tuesday
   made Monday's "Plan next week ›" a one-day offer.
2. static/plan-week.html took the URL's `?days=` as the household's
   HORIZON, so whichever door opened the page decided what "Today" and
   "Tomorrow" counted by: one day from that link, five from the Re-plan
   pill on a five-day draft.

Now the horizon is the server's (suggest_planning_period's day_count, read
on every visit), the offer is never cut short, and the Friday edge noted
in PRODUCT_FLOWS flow 2 is closed with it: with this week approved, the
suggestion is the period AFTER the approved plan — the same days the
nudge and the link under the plan already named — not a Fri–Thu window.

Card B — select-all in the "Anything else" box. No handler on the textarea
touches the selection, and nothing in the page's or theme's CSS turns it
off; the one code path that could rewrite the box under a typist was a
duplicated prefill fetch (load() started one without waiting, Continue
started another), whose slower answer re-ran the reset after the screen
had moved on. One fetch per period now, and a stale answer never writes.

The node-harness tests run the page's own functions the way
tests/test_intake_motion_2026_09_21.py does.
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
from app.tools import weekly_plan as _weekly_plan

REPO = Path(__file__).resolve().parents[1]
PAGE = (REPO / "static" / "plan-week.html").read_text(encoding="utf-8")
SHELL_JS = (REPO / "static" / "shell.js").read_text(encoding="utf-8")
THEME_CSS = (REPO / "static" / "theme.css").read_text(encoding="utf-8")

_needs_node = pytest.mark.skipif(
    shutil.which("node") is None, reason="node is needed to execute the screen's own functions"
)

MONDAY = "2026-09-21"
TUESDAY = "2026-09-22"
FRIDAY = "2026-09-25"


def _extract(name: str, source: str = PAGE) -> str:
    """The function's source — with its `async`, when it has one."""
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


def _block(start_marker: str, end_marker: str, source: str = PAGE) -> str:
    """A multi-line statement, from its first line through `end_marker`."""
    start = source.index(start_marker)
    end = source.index(end_marker, start) + len(end_marker)
    return source[start:end]


def _section(qid: str) -> str:
    start = PAGE.index(f'<section id="{qid}"')
    return PAGE[start:PAGE.index("</section>", start)]


def _node(script: str, today: str = MONDAY) -> str:
    harness = (
        "const _RealDate = Date;\n"
        f"class _Pinned extends _RealDate {{ constructor(...a) {{ super(...(a.length ? a : ['{today}T09:00:00'])); }} "
        f"static now() {{ return new _RealDate('{today}T09:00:00').getTime(); }} }}\n"
        "Date = _Pinned;\n"
        "function esc(s) { return String(s == null ? '' : s); }\n"
        + _var("START_COPY") + _var("PERIOD_STRIP_DAYS") + _var("PERIOD_MAX_DAYS")
        + "\n".join(_extract(n) for n in ("isoLocal", "todayIso", "addDaysIso", "daysBetween", "weekdayName", "dayLabel"))
        + "\n" + script
    )
    res = nodeharness.run_node(harness, timeout=30)
    assert res.returncode == 0, f"node failed: {res.stderr}"
    return res.stdout.strip()


class _FixedToday(datetime.date):
    _value: "datetime.date | None" = None

    @classmethod
    def today(cls):
        return cls._value


@pytest.fixture
def pin_today(monkeypatch):
    def _pin(iso_date: str):
        _FixedToday._value = datetime.date.fromisoformat(iso_date)
        monkeypatch.setattr(_weekly_plan, "date", _FixedToday)
        from conftest import pin_household_clock
        pin_household_clock(monkeypatch)
    return _pin


def _insert_plan(content_start: str, day_count: int, status: str = "approved", week_start: str | None = None) -> int:
    conn = get_conn()
    cur = conn.execute(
        "INSERT INTO weekly_plans (household_id, week_start_date, status, content_start_date, day_count, planning_mode) "
        "VALUES (1, ?, ?, ?, ?, 'day_based')",
        (week_start or content_start, status, content_start, day_count),
    )
    plan_id = cur.lastrowid
    for day in tools.period_dates(content_start, day_count):
        conn.execute(
            "INSERT INTO meal_plan_entries (household_id, weekly_plan_id, date, slot, freeform_meal) "
            "VALUES (1, ?, ?, 'dinner', 'Chili')",
            (plan_id, day),
        )
    conn.commit()
    conn.close()
    return plan_id


# ==========================================================================
# Card A — the server: the horizon from any start, every door agreeing
# ==========================================================================

class TestTheHorizonFromAnyStart:
    def test_monday_with_an_approved_plan_ending_today_is_seven_days_from_today(self, pin_today):
        # Tue 15 – Mon 21 approved; Monday's suggestion is Mon 21 – Sun 27.
        # Attention hasn't moved on for the anchored week, so it is this
        # week, from today, the whole horizon.
        _insert_plan("2026-09-15", 7)
        pin_today(MONDAY)
        suggestion = tools.suggest_planning_period()
        assert (suggestion["start_date"], suggestion["day_count"]) == (MONDAY, 7)
        assert suggestion["label"] == "Sep 21–27"

    def test_the_link_under_a_plan_ending_today_offers_seven_from_tomorrow(self, pin_today):
        plan = _insert_plan("2026-09-15", 7)
        pin_today(MONDAY)
        menu = tools.get_week_menu()
        assert menu["weekly_plan_id"] == plan
        nxt = menu["next_period"]
        assert (nxt["start_date"], nxt["day_count"]) == (TUESDAY, 7)
        # And Now agrees: the same day, the same length.
        nudge = tools.get_week_planning_nudge()
        assert (nudge["week_start"], nudge["day_count"]) == (TUESDAY, 7)

    def test_emilys_monday_the_offer_is_never_one_day(self, pin_today):
        """FAILED ON MAIN: next_period was ("2026-09-21", 1) with
        shortened_reason "Sep 22–26 is already planned." — the "Mon 21 →
        Mon 21 · 1 day" she saw."""
        _insert_plan(TUESDAY, 5, status="draft", week_start=MONDAY)   # Tue 22 – Sat 26
        ended = _insert_plan("2026-09-14", 7)                          # last week, approved, newest
        pin_today(MONDAY)
        menu = tools.get_week_menu()
        assert menu["weekly_plan_id"] == ended
        nxt = menu["next_period"]
        assert (nxt["start_date"], nxt["day_count"]) == (MONDAY, 7)
        assert "shortened_reason" not in nxt

    def test_the_endpoint_carries_the_horizon_not_a_one_day_period(self, signed_in, pin_today):
        _insert_plan(TUESDAY, 5, status="draft", week_start=MONDAY)
        pin_today(MONDAY)
        body = signed_in.get("/api/week/planning-period").json()
        assert (body["start_date"], body["day_count"]) == (MONDAY, 7)

    def test_the_friday_edge_with_this_week_approved_is_next_week_everywhere(self, pin_today):
        """FAILED ON MAIN: the suggestion was Fri 25 – Thu Oct 1 (the
        current period clamped to today) while the nudge and the Plan tab's
        link both said Sep 28 – Oct 4."""
        approved = _insert_plan(MONDAY, 7)
        pin_today(FRIDAY)
        suggestion = tools.suggest_planning_period()
        assert (suggestion["start_date"], suggestion["day_count"]) == ("2026-09-28", 7)
        assert suggestion["is_current_period"] is False
        menu = tools.get_week_menu()
        assert menu["weekly_plan_id"] == approved
        assert (menu["next_period"]["start_date"], menu["next_period"]["day_count"]) == ("2026-09-28", 7)
        nudge = tools.get_week_planning_nudge()
        assert (nudge["week_start"], nudge["day_count"]) == ("2026-09-28", 7)

    def test_the_friday_edge_still_never_starts_before_today_with_nothing_approved(self, pin_today):
        # Unchanged: nothing approved on a Friday → next week, as before.
        pin_today(FRIDAY)
        suggestion = tools.suggest_planning_period()
        assert (suggestion["start_date"], suggestion["is_current_period"]) == ("2026-09-28", False)

    def test_a_household_planning_as_it_goes_gets_its_own_horizon_from_any_start(self, pin_today):
        # Three days, from today and from the day after a plan — never
        # seven, never one, whatever else is held.
        tools.set_planning_anchor("as_we_go")
        _insert_plan("2026-09-19", 3)                                   # Sat 19 – Mon 21
        _insert_plan("2026-09-23", 2, status="draft", week_start=TUESDAY)  # Wed 23 – Thu 24
        pin_today(MONDAY)
        assert tools.suggest_planning_period()["day_count"] == 3
        nxt = tools.get_week_menu()["next_period"]
        assert (nxt["start_date"], nxt["day_count"]) == (TUESDAY, 3)

    def test_the_servers_next_period_has_no_shortening_left(self):
        src = (REPO / "app" / "tools" / "weekly_plan.py").read_text(encoding="utf-8")
        body = src[src.index("def next_period_after("):src.index("def _week_headline(")]
        code = body.split('"""')[2]   # after the docstring
        assert "shortened_reason" not in code
        assert "day_count = (first_day - start).days" not in code


# ==========================================================================
# Card A — the intake: the horizon is the server's, never the URL's
# ==========================================================================

class TestTheChipsCountByTheHouseholdsHorizon:
    def test_the_horizon_is_read_from_the_server_on_every_visit(self):
        assert "var horizon = 7;" in PAGE
        assert "var horizon = dayCount;" not in PAGE
        load = _extract("load")
        assert "var period = await loadHorizon();" in load
        # The server's start is used only when the URL names none.
        assert "if (!weekStart) {" in load and "weekStart = period.start_date;" in load
        horizon = _extract("loadHorizon")
        assert "fetch('/api/week/planning-period')" in horizon
        assert "horizon = (period.day_count && period.day_count > 0) ? Math.min(period.day_count, PERIOD_MAX_DAYS) : 7;" in horizon
        assert "function resolveWeekStart(" not in PAGE

    @_needs_node
    def test_a_five_day_door_no_longer_locks_today_and_tomorrow_to_five(self):
        """FAILED ON MAIN: horizon was the URL's 5, so both chips made five
        days. Arrived through the Re-plan pill on a Tue 22 – Sat 26 draft,
        the server saying seven: Today is Mon 21 – Sun 27, Tomorrow is
        Tue 22 – Mon 28, and the five days it arrived with sit under "Pick
        my own days"."""
        out = _node(_run_load("?week=2026-09-22&days=5", server_day_count=7))
        got = json.loads(out)
        assert got["horizon"] == 7
        assert got["chosen"] == "pick"
        assert got["today"] == {"start": MONDAY, "days": 7}
        assert got["tomorrow"] == {"start": TUESDAY, "days": 7}
        assert (got["weekStart"], got["dayCount"]) == (TUESDAY, 5)

    @_needs_node
    def test_a_one_day_door_never_makes_today_one_day(self):
        got = json.loads(_node(_run_load("?week=2026-09-21&days=1", server_day_count=7)))
        assert got["today"]["days"] == 7 and got["tomorrow"]["days"] == 7
        assert got["chosen"] == "pick"

    @_needs_node
    def test_a_household_on_a_five_day_horizon_gets_five_from_either_chip(self):
        # The shell's doors name the horizon's own length in the address
        # (startPlanningWeek adds &days= when it isn't seven).
        got = json.loads(_node(_run_load("?week=2026-09-21&days=5", server_day_count=5)))
        assert got["horizon"] == 5
        assert got["chosen"] == "today"
        assert got["today"] == {"start": MONDAY, "days": 5}
        assert got["tomorrow"] == {"start": TUESDAY, "days": 5}

    @_needs_node
    def test_with_no_week_in_the_address_the_servers_period_opens(self):
        got = json.loads(_node(_run_load("", server_day_count=3, server_start=MONDAY)))
        assert (got["weekStart"], got["dayCount"], got["horizon"]) == (MONDAY, 3, 3)
        assert got["chosen"] == "today"

    @_needs_node
    def test_when_the_server_cannot_be_reached_the_horizon_falls_back_to_seven(self):
        got = json.loads(_node(_run_load("?week=2026-09-22&days=5", server_day_count=None)))
        assert got["horizon"] == 7
        assert got["today"]["days"] == 7

    def test_the_yesterday_line_is_gone_and_the_rule_is_not(self):
        q1 = _section("q1")
        assert "already eaten" not in q1
        assert 'id="range-note"' not in PAGE
        # Today, never yesterday, still holds on every door.
        assert "weekStart = clampStart(weekStart, todayIso());" in _extract("load")
        assert "return (!start || start < today) ? today : start;" in _extract("clampStart")

    def test_every_door_still_hands_over_a_start_and_the_shell_carries_no_reason_line(self):
        assert "startPlanningWeek(period.start_date, period.day_count)" in SHELL_JS
        assert "shortened_reason" not in SHELL_JS


def _run_load(search: str, server_day_count, server_start: str = MONDAY) -> str:
    """The page's load() against a stubbed window: URL `search`, a server
    whose planning period says `server_day_count` (None = unreachable).
    Prints the horizon, the chosen chip and what Today / Tomorrow make."""
    server = (
        "Promise.reject(new Error('down'))" if server_day_count is None
        else f"Promise.resolve({{ ok: true, json: function () {{ return Promise.resolve({{ start_date: '{server_start}', day_count: {server_day_count} }}); }} }})"
    )
    return (
        f"var location = {{ search: '{search}', href: '' }};\n"
        "var history = { replaceState: function () {} };\n"
        "var console = { warn: function () {} };\n"
        "function fetch(url) { if (url === '/api/week/planning-period') return " + server + "; "
        "return Promise.resolve({ ok: true, json: function () { return Promise.resolve({ days: [] }); } }); }\n"
        "var els = {}; function $(id) { return els[id] || (els[id] = { hidden: false, textContent: '', innerHTML: '', classList: { toggle: function () {} }, querySelectorAll: function () { return []; } }); }\n"
        + _var("weekStart") + _block("  var dayCount = (function () {", "  })();\n") + _var("horizon") + _var("who")
        + "var picking = false; var pick = { start: '', end: '' }; var step = 1;\n"
        + _extract("clampStart") + "\n" + _extract("choosePeriod") + "\n" + _extract("startOptions") + "\n"
        + _extract("loadHorizon") + "\n" + _extract("load") + "\n"
        + "function renderStartStep() {} function showStep() {} function loadPeriod() { return Promise.resolve(true); }\n"
        + "load().then(function () {\n"
        + "  var opts = startOptions(todayIso(), horizon, weekStart, dayCount);\n"
        + "  var by = {}; opts.options.forEach(function (o) { by[o.key] = { start: o.start, days: o.days }; });\n"
        + "  process.stdout.write(JSON.stringify({ horizon: horizon, chosen: opts.chosen, today: by.today, tomorrow: by.tomorrow, weekStart: weekStart, dayCount: dayCount, href: location.href }));\n"
        + "});\n"
    )


# ==========================================================================
# Card B — select-all in the "Anything else" box
# ==========================================================================

class TestSelectAllInTheAnythingElseBox:
    def test_nothing_on_the_textarea_can_swallow_a_selection_or_a_key(self):
        # The one listener on the box is `input` → paintCta, which only
        # repaints the foot. No keydown, keyup, keypress, select,
        # selectstart, mousedown or touch handler on it at all.
        listeners = re.findall(r"\$\('freeform'\)\.addEventListener\('(\w+)'", PAGE)
        assert listeners == ["input"]
        assert "$('freeform').addEventListener('input', paintCta);" in PAGE
        assert "preventDefault" not in _extract("paintCta")
        # The page's only document-level key handler is Escape for the
        # sheets, and it returns before touching anything else.
        doc_keys = re.findall(r"document\.addEventListener\('keydown', function \(e\) \{\n\s*if \(e\.key !== 'Escape'\) return;", PAGE)
        assert len(doc_keys) == 1
        assert PAGE.count("document.addEventListener('keydown'") == 1
        # A real textarea, not a contenteditable.
        assert '<textarea id="freeform"' in PAGE
        assert "contenteditable" not in PAGE.lower()

    def test_nothing_in_the_styles_turns_selection_off(self):
        for sheet in (PAGE, THEME_CSS):
            assert "user-select: none" not in sheet
            assert "touch-callout: none" not in sheet
        # And the box says selection is on, itself.
        rule = PAGE[PAGE.index("  textarea {"):PAGE.index("  textarea:focus")]
        assert "-webkit-user-select: text; user-select: text; -webkit-touch-callout: default;" in rule

    def test_the_only_writes_to_the_box_are_the_prefill_for_a_period(self):
        # `$('freeform').value =` is written in exactly two places: the
        # reset when a period's prefill lands, and takeAnswers (called
        # only from that same prefill). Nothing writes it on input, on
        # save, or on a timer.
        writes = [m.start() for m in re.finditer(r"\$\('freeform'\)\.value = ", PAGE)]
        assert len(writes) == 2
        assert "$('freeform').value = '';" in _extract("fetchPeriod")
        assert "$('freeform').value = intake.freeform || '';" in _extract("takeAnswers")
        assert PAGE.count("takeAnswers(") == 3   # the definition and its two calls inside fetchPeriod
        assert "takeAnswers(" not in _extract("paintCta") and "takeAnswers(" not in _extract("saveIntake")
        assert "setInterval(" not in PAGE

    def test_one_prefill_per_period_and_a_stale_answer_never_writes(self):
        load = _extract("loadPeriod")
        assert "if (inFlight && inFlight.key === key) return inFlight.promise;" in load
        assert "var promise = fetchPeriod(key)" in load
        fetch = _extract("fetchPeriod")
        assert "if (key !== weekStart + '|' + dayCount) return false;" in fetch
        assert fetch.index("if (key !== weekStart + '|' + dayCount) return false;") < fetch.index("$('freeform').value = '';")

    @_needs_node
    def test_the_box_is_not_rewritten_under_a_typist_by_a_late_prefill(self):
        """FAILED ON MAIN: load() started a prefill without waiting and
        Continue started a second for the same period; the slower one
        landed after the person had typed, and reset the box to ''. Here
        the first answer is held back until after the typing, and the
        second call shares it — one fetch, and the box keeps its text."""
        got = json.loads(_node(_race_harness()))
        assert got["fetches"] == 1
        assert got["freeform"] == "Friday is pizza night"
        assert got["staleWrote"] is False

    def test_back_and_continue_keep_the_text(self):
        # Nothing between steps touches the box: Back only saves and shows
        # the previous step, Continue saves the trimmed text and moves on.
        back = _extract("goBack")
        assert "freeform" not in back
        assert "if (n === 5) return saveIntake({ freeform: $('freeform').value.trim() });" in _extract("saveStep")
        assert "freeform" not in _extract("showStep")


def _race_harness() -> str:
    """Built per call so the file still collects on a page without these
    functions (every test here fails on its own on main)."""
    return (
        "var weekStart = '2026-09-21'; var dayCount = 7; var horizon = 7;\n"
        "var data = null; var loadedFor = ''; var awayRanges = []; var answersAtLoad = ''; var prefilled = {};\n"
        "var answers = { night_tags: {}, guest_counts: {}, packed_lunch_days: [], moods: [], cuisines: [] };\n"
        "var box = { value: '', focused: true };\n"
        "var els = { freeform: box }; function $(id) { return els[id] || (els[id] = { hidden: false, textContent: '' }); }\n"
        "function encodeURIComponent(s) { return s; }\n"
        "var fetches = 0; var release = [];\n"
        "function fetch(url) { fetches++; return new Promise(function (resolve) { release.push(function () {\n"
        "  resolve({ ok: true, json: function () { return Promise.resolve({ days: [], in_flight: false, plan_exists: false }); } }); }); }); }\n"
        "function loadAttendance() { return Promise.resolve(); } function answersSnapshot() { return ''; }\n"
        "function renderDays() {} function renderHolidayBlocks() {} function renderAwaySet() {} function renderLunchDays() {} function renderMoods() {} function renderCuisines() {}\n"
        "function relativeTime() { return ''; } function takeAnswers() {}\n"
        "var inFlight = null;\n"
        + _extract("loadPeriod") + "\n" + _extract("fetchPeriod") + "\n"
        + "var first = loadPeriod();\n"          # load()'s unawaited start
        + "var second = loadPeriod();\n"         # Continue on step 1, awaited
        + "var fetchesForOnePeriod = fetches;\n"
        + "release.pop()();\n"                   # the answer Continue is waiting on lands
        + "Promise.resolve(second).then(function () {\n"
        + "  box.value = 'Friday is pizza night';\n"   # typed on step 5, after the screen moved on
        + "  release.forEach(function (r) { r(); });\n"  # on main: the first, duplicate, answer lands now
        + "  return Promise.resolve(first);\n"
        + "}).then(function () {\n"
        + "  var typed = box.value;\n"
        # A prefill for a period that is no longer on screen: it must not touch the box.
        + "  var p = fetchPeriod('2026-10-05|7'); release.pop()(); return p.then(function (wrote) {\n"
        + "    var staleWrote = wrote !== false || box.value !== typed;\n"
        + "    process.stdout.write(JSON.stringify({ fetches: fetchesForOnePeriod, freeform: typed, staleWrote: staleWrote }));\n"
        + "  });\n"
        + "});\n"
    )
