"""The week intake in the onboarding motion (Emily, 2026-09-20/21).

Four Loop Board cards, one branch (intake-motion-2026-09-21):

1. The week intake in the onboarding motion — one question a screen.
2. Plan a week starts with "Starting when?" — today, never yesterday.
3. Surprise me is a card at the top of the mood screen.
4. Building your week is the onboarding screen — "What I'm using", and a
   status line that moves.

Source-level checks pin the screens' bones and words; the node harness
runs the page's own functions (the start options, the day line, the
"What I'm using" lines) the way tests/test_planning_exit.py runs
shell.js's. Every test here is red on main.
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
from app.tools import week_intake

REPO = Path(__file__).resolve().parents[1]
PAGE = (REPO / "static" / "plan-week.html").read_text(encoding="utf-8")
SHELL_HTML = (REPO / "static" / "shell.html").read_text(encoding="utf-8")
ONBOARDING = (REPO / "static" / "onboarding.html").read_text(encoding="utf-8")

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
    """The `var NAME = ...;` statement, for the constants the harness needs."""
    m = re.search(rf"  var {name} = [\s\S]*?;\n", source)
    assert m, name
    return m.group(0)


def _flow() -> str:
    return PAGE[PAGE.index('<main id="flow"'):PAGE.index("</main>")]


def _section(qid: str) -> str:
    start = PAGE.index(f'<section id="{qid}"')
    return PAGE[start:PAGE.index("</section>", start)]


# A node prelude with the page's date helpers and copy, and a pinned "now".
# Built per call rather than at import so the file still collects (and
# every test fails on its own) on a page without these functions.
def _date_helpers() -> str:
    return "\n".join(
        _extract(n) for n in ("isoLocal", "todayIso", "addDaysIso", "daysBetween", "weekdayName", "dayLabel", "periodRangeLabel")
    )


def _node(script: str, today: str = "2026-09-20") -> str:
    harness = (
        "const _RealDate = Date;\n"
        f"class _Pinned extends _RealDate {{ constructor(...a) {{ super(...(a.length ? a : ['{today}T09:00:00'])); }} "
        f"static now() {{ return new _RealDate('{today}T09:00:00').getTime(); }} }}\n"
        "Date = _Pinned;\n"
        "function esc(s) { return String(s == null ? '' : s); }\n"
        + _var("RANGE_COPY") + _var("SURPRISE_MOOD") + _var("PERIOD_MAX_DAYS") + _var("USING_ICONS") + _var("USING_DAY_PHRASES")
        + _date_helpers() + "\n"
        + script
    )
    res = nodeharness.run_node(harness, timeout=30)
    assert res.returncode == 0, f"node failed: {res.stderr}"
    return res.stdout.strip()


# ==========================================================================
# Card 1 — one question a screen, the onboarding bones
# ==========================================================================

class TestOneQuestionAScreen:
    def test_five_steps_in_order_each_with_the_question_as_the_heading(self):
        flow = _flow()
        # "Which days?" and the last title are the 2026-09-21 design pass
        # (boards D1, Emily's pick) — tests/test_intake_design_2026_09_21.py.
        order = [
            "<h1>Which days?</h1>",
            "<h1>Any days that are different?</h1>",
            "<h1>Any lunches on the go?</h1>",
            "<h1>What are you in the mood for?</h1>",
            "<h1>Anything else you want to share for planning this week?</h1>",
        ]
        positions = [flow.index(h) for h in order]
        assert positions == sorted(positions)
        assert "var STEP_COUNT = 5;" in PAGE
        # Nothing restates which week: the first screen settles that. The
        # last title is Emily's exact wording (2026-09-21) and keeps hers.
        for h in order[:-1]:
            assert "this week" not in h

    def test_the_bones_are_onboardings(self):
        # One crumb above the steps, the "Plan a week · n of 5" eyebrow, five
        # bars, a full-width Continue, one quiet italic line under it.
        flow = _flow()
        assert flow.count('class="crumb"') == 1
        assert "$('eyebrow').textContent = 'Plan a week · ' + n + ' of ' + STEP_COUNT;" in PAGE
        assert flow.count('class="bar"') == 5
        assert "$('cta').textContent = 'Continue';" in PAGE
        foot = PAGE[PAGE.index('<div class="footer"'):PAGE.index("</div>", PAGE.index('<div class="footer"')) + 6]
        assert 'class="cta" id="cta"' in foot and 'class="skip" id="skip"' in foot
        assert foot.index('id="cta"') < foot.index('id="skip"'), "the quiet line sits UNDER Continue"
        skip_css = PAGE[PAGE.index("  .skip {"):PAGE.index("  .skip:hover")]
        assert "font-family: var(--font-accent); font-style: italic;" in skip_css
        assert "min-height: 44px" in skip_css
        # The crumb reads "‹ Plan" only on the first step.
        assert "$('leave').innerHTML = n === 1 ? '&lsaquo; Plan' : '&lsaquo; Back';" in PAGE

    def test_ivory_everywhere_and_the_ivory_chip(self):
        body = PAGE[PAGE.index("  body {"):PAGE.index("  body.drafting")]
        assert "background: var(--ground);" in body
        chip = PAGE[PAGE.index("  .chip {"):PAGE.index("  .chip:hover")]
        assert "background: var(--surface);" in chip and "border: 1.5px solid var(--hairline-strong);" in chip
        assert "min-height: 44px" in chip
        # The only spruce ground is the drafting screen.
        assert PAGE.count("background: var(--spruce); padding-bottom: 40px; }") == 1
        # The old dark tags-on-a-card look is gone from the steps.
        assert ".day-tile" not in PAGE and "--plum-panel" not in PAGE

    def test_the_quiet_lines_are_the_two_and_surprise_me_left_the_foot(self):
        # Two since board D2 (2026-09-21): "Nothing on the go" is a pill on
        # the lunch screen, not a quiet line under Continue.
        assert "var SKIP_LABELS = { 2: 'Nothing different', 5: 'Nothing else' };" in PAGE
        assert "'Surprise me'" not in _var("SKIP_LABELS")

    def test_the_away_card_carries_emilys_words_and_opens_the_existing_sheet(self):
        q2 = _section("q2")
        assert '<span class="card-btn-title">Away for a few nights?</span>' in q2
        assert (
            "Tell me when you&rsquo;re travelling or gone for a stretch &mdash; "
            "I&rsquo;ll skip those meals altogether." in q2
        )
        assert 'id="away-gate"' in q2
        assert "$('away-gate').addEventListener('click', openAwaySheet);" in PAGE
        assert "Anyone away this week?" not in PAGE

    def test_the_days_step_has_the_strip_and_notes_under_it_ready_for_the_day_sheet_pass(self):
        q2 = _section("q2")
        assert 'class="stripe" id="day-tiles"' in q2 and 'id="day-notes"' in q2
        assert 'id="holiday-blocks"' in q2 and 'id="away-set"' in q2
        # The day sheet opens from a tile; its insides are the second
        # pass's (tests/test_day_sheet_row_per_person.py).
        assert "function openDaySheet(date)" in PAGE
        assert "tile.addEventListener('click', function () { openDaySheet(tile.dataset.date); });" in PAGE

    def test_back_never_loses_an_answer_and_leaving_keeps_them(self):
        back = _extract("goBack")
        assert "saveStep(step)" in back and "showStep(step - 1)" in back
        assert "if (n === 5) return saveIntake({ freeform: $('freeform').value.trim() });" in PAGE
        leave = _extract("leavePayload")
        assert "moods: answers.moods" in leave and "night_tags: answers.night_tags" in leave

    def test_what_pomona_already_knows_opens_already_chosen(self):
        load = _extract("fetchPeriod")   # the body of loadPeriod, one fetch per period (2026-09-21)
        # Lunches from the rhythm — the suggestion that existed but never
        # skipped the step (PRODUCT_FLOWS flow 2).
        assert ".filter(function (s) { return s.suggested_packed; })" in load
        assert "answers.packed_lunch_days = suggested; prefilled.lunches = true;" in load
        # Last week's moods and cuisines.
        assert "answers.moods = (data.last_intake.moods || []).slice();" in load
        assert "prefilled.moods = true;" in load
        assert "prefilled.lunches ? 'I’ve ticked your usual days.' : ''" in PAGE
        assert "'Last week’s picks, unless you change them.'" in PAGE
        # Continue on a pre-answered lunch step is one tap: the pill under
        # the days is off while days are ticked (board D2).
        assert "$('lunch-none').classList.toggle('on', none);" in _extract("paintLunchDays")

    def test_the_server_tells_the_screen_last_weeks_answers(self):
        this_week = "2026-10-05"
        tools.save_week_intake("2026-09-28", moods=["Comfort food"], cuisines=["Thai"])
        tools.save_week_intake("2026-09-21", moods=["On the grill"], cuisines=["Greek"])
        prefill = tools.get_week_intake_prefill(this_week)
        assert prefill["last_intake"] == {"week_start": "2026-09-28", "moods": ["Comfort food"], "cuisines": ["Thai"]}
        # A first week has nothing to carry.
        assert tools.get_week_intake_prefill("2026-09-14")["last_intake"] is None
        assert prefill["recent_dinners_on_record"] is False

    def test_copy_passes_the_seven_rules(self):
        # Contractions, always; no dashboard labels; nothing restating the chips.
        for line in (
            "Tap the first day, then the last.",
            "I&rsquo;ll pick from what you like and keep the week varied.",
            "No need to wait &mdash; the draft lands on Plan when it&rsquo;s done.",
        ):
            assert line in PAGE, line
        # No dashboard labels anywhere the household reads (comments aside).
        shown = re.sub(r"//[^\n]*|/\*[\s\S]*?\*/|<!--[\s\S]*?-->", "", PAGE)
        for dashboard in ("Get started", "Action required", "Set up", "Step 1"):
            assert dashboard not in shown
        assert "As many as you like. I won&rsquo;t make every night the same." not in PAGE


# ==========================================================================
# Card 2 — Starting when?
# ==========================================================================

class TestStartingWhen:
    def test_the_first_screen_is_which_days_with_one_strip_and_no_chips(self):
        # The Today / Tomorrow / Pick a date chips went on 2026-09-22
        # (Emily: a second control on top of the strip); the range card is
        # the whole step. tests/test_which_days_calendar_2026_09_22.py has
        # the rest.
        q1 = _section("q1")
        assert q1.index("<h1>Which days?</h1>") < q1.index('id="range-card"')
        assert 'id="start-chips"' not in q1
        assert 'id="range-words"' in q1 and 'id="range-strip"' in q1
        # The "Yesterday's already eaten" line under the strip went on
        # 2026-09-21 (Emily's call); the rule behind it (clampStart) stays.
        assert "already eaten" not in q1
        shown = re.sub(r"//[^\n]*|/\*[\s\S]*?\*/|<!--[\s\S]*?-->", "", PAGE)
        assert "Pick a date" not in shown
        # Today is outlined in apricot, and says so in a word (S6).
        assert ".dt.today { outline: 2px solid var(--apricot);" in PAGE
        assert "var dow = d === today ? 'Today' : weekdayName(d, 'short');" in PAGE

    @_needs_node
    def test_a_start_before_today_becomes_today_whichever_door_it_came_through(self):
        # The Re-plan door hands over the draft's own start — Sep 19 on Sep 20.
        fn = _extract("clampStart")
        assert _node(fn + "\nconsole.log(clampStart('2026-09-19', '2026-09-20'));") == "2026-09-20"
        assert _node(fn + "\nconsole.log(clampStart('2026-09-26', '2026-09-20'));") == "2026-09-26"
        assert _node(fn + "\nconsole.log(clampStart('', '2026-09-20'));") == "2026-09-20"
        assert "weekStart = clampStart(weekStart, todayIso());" in _extract("load")

    @_needs_node
    def test_the_range_reads_in_words_with_its_day_count(self):
        assert _node("console.log(dayLabel('2026-09-20') + ' → ' + dayLabel('2026-09-26'));") == "Sun 20 → Sat 26"
        assert _node("console.log(periodRangeLabel('2026-09-20', 7));") == "Sep 20–26"
        # Just the count since 2026-09-22 — "· tap a day to drop it" went
        # with the dropping; half a range says what's missing instead.
        assert "$('range-count').textContent = range.end ? count + (count === 1 ? ' day' : ' days') : RANGE_COPY.lastDay;" in PAGE

    def test_the_strip_is_the_picker(self):
        # No picker to open since 2026-09-22: the step's one strip runs to
        # the 28-day ceiling and a finished range is the period at once.
        # (The scroll behaviour this class used to pin for the old picker
        # is pinned for the strip in test_which_days_calendar_2026_09_22.)
        assert "var PERIOD_STRIP_DAYS = 28;" in PAGE and "var PERIOD_MAX_DAYS = 28;" in PAGE
        assert "if (step === 1) $('cta').disabled = !range.end;" in _extract("paintCta")
        assert "if (range.end) choosePeriod(range.start, daysBetween(range.start, range.end) + 1);" in _extract("tapRangeDay")

    def test_the_period_chosen_is_the_one_every_later_step_asks_about(self):
        # Leaving step 1 fetches the prefill for the chosen period (once).
        assert "if (!(await ensureLoaded())) return;" in _extract("advance")
        load = _extract("loadPeriod")
        assert "var key = weekStart + '|' + dayCount;" in load and "if (data && loadedFor === key) return true;" in load
        assert "history.replaceState(null, '', url);" in _extract("choosePeriod")

    def test_the_servers_suggestion_never_starts_before_today(self, monkeypatch):
        """Card 2's root cause: suggest_planning_period returned the anchor's
        start day even once it had passed — Sat 19 on Sun 20 for a
        "ready by Friday" household. tests/test_planning_periods.py pins the
        rule on that function; this is the endpoint the shell reads."""
        from app.tools import weekly_plan as _weekly_plan

        class _Sunday(datetime.date):
            @classmethod
            def today(cls):
                return cls(2026, 9, 20)

        tools.set_planning_anchor("friday")
        monkeypatch.setattr(_weekly_plan, "date", _Sunday)
        from conftest import pin_household_clock
        pin_household_clock(monkeypatch)
        suggestion = tools.suggest_planning_period()
        assert suggestion["start_date"] == "2026-09-20"
        assert suggestion["label"] == "Sep 20–26"
        assert suggestion["day_count"] == 7


# ==========================================================================
# Card 3 — Surprise me is a card at the top of the mood screen
# ==========================================================================

class TestSurpriseMeCard:
    def test_the_card_is_first_under_the_question_then_the_eyebrow_then_the_chips(self):
        q4 = _section("q4")
        card = q4.index('id="surprise"')
        assert q4.index("<h1>What are you in the mood for?</h1>") < card
        assert card < q4.index("Or steer me &middot; as many as you like") < q4.index('id="moods"')
        assert q4.index('id="moods"') < q4.index("Cuisines you like") < q4.index('id="cuisines"')
        assert '<span class="card-btn-title">Surprise me</span>' in q4
        assert "I&rsquo;ll pick from what you like and keep the week varied." in q4
        assert 'class="card card-btn surprise"' in q4
        assert ".surprise { border-color: var(--apricot); }" in PAGE
        # A star, drawn as a stroke icon.
        assert '<svg class="surprise-star"' in q4

    def test_tapping_it_records_the_mood_and_goes_straight_on(self):
        fn = _extract("chooseSurprise")
        assert "answers.moods.push(SURPRISE_MOOD);" in fn and "advance();" in fn
        assert "$('surprise').addEventListener('click', chooseSurprise);" in PAGE
        assert "var SURPRISE_MOOD = 'Surprise me';" in PAGE
        assert week_intake.SURPRISE_MOOD == "Surprise me"
        assert "Surprise me" in week_intake.MOOD_GUIDANCE
        assert len(week_intake.MOOD_GUIDANCE["Surprise me"].split()) >= 8

    def test_the_moods_win_over_the_card_never_both(self):
        moods = _extract("renderMoods")
        assert "var i = answers.moods.indexOf(SURPRISE_MOOD);" in moods
        assert "if (i !== -1 && answers.moods.length > 1) answers.moods.splice(i, 1);" in moods
        assert "$('surprise').classList.toggle('on', on);" in _extract("paintMoodScreen")

    def test_the_foot_is_continue_only(self):
        assert "4:" not in _var("SKIP_LABELS")
        assert "'Surprise me'" not in _var("SKIP_LABELS")

    def test_a_surprise_intake_saves_and_the_planner_reads_it(self):
        saved = tools.save_week_intake("2026-09-21", moods=[week_intake.SURPRISE_MOOD], cuisines=["Mexican"])
        assert saved["moods"] == ["Surprise me"]
        from app import agent
        ctx = agent._intake_generation_context(saved)
        assert ctx["moods"] == ["Surprise me"]
        assert ctx["mood_guidance"] == [week_intake.MOOD_GUIDANCE["Surprise me"]]


# ==========================================================================
# Card 4 — Building your week is the onboarding screen
# ==========================================================================

class TestBuildingYourWeek:
    def test_the_screen_is_onboardings_cooking_up_screen(self):
        section = _section("draft-progress")
        assert "Cooking up your week&hellip;" in section
        assert 'id="draft-eyebrow"' in section
        # "Got it", with no lead line, since board D3 (2026-09-21).
        assert '<p class="using-eyebrow">Got it</p>' in section
        assert "What I&rsquo;m using" not in section and "Here&rsquo;s what I&rsquo;m building this week from:" not in section
        assert "No need to wait &mdash; the draft lands on Plan when it&rsquo;s done." in section
        assert "document.body.classList.add('drafting');" in _extract("showDraftProgress")
        assert "body.drafting { background: var(--spruce);" in PAGE
        # The old ivory list of ticked days is gone.
        assert "Building your week" not in PAGE and "draft-day-check" not in PAGE
        assert "Nothing is approved until you say so." not in PAGE

    def test_the_pomona_mark_is_the_real_one_never_redrawn(self):
        mark = re.search(r'<div class="draft-mark"><svg[^>]*>(.*?)</svg>', PAGE, re.S).group(1)
        paths = re.findall(r'<path d="([^"]+)"', mark)
        assert len(paths) == 4
        for d in paths:
            assert d in SHELL_HTML, d
        assert 'stroke-width="1.6"' in re.search(r'<div class="draft-mark"><svg[^>]*>', PAGE).group(0)

    def test_the_status_line_moves_by_day_from_the_streams_day_frames(self):
        stream = _extract("streamGenerate")
        assert "if (dayLine && body && body.date) dayLine.sawDate(body.date);" in stream
        assert "PomonaWaiting" not in PAGE

    @_needs_node
    def test_the_day_line_advances_on_frames_and_on_its_own_clock_and_stops_on_the_last_day(self):
        script = (
            "const q = []; let now = 0;\n"
            "global.setTimeout = (fn, ms) => { const t = { at: now + ms, fn, dead: false }; q.push(t); return t; };\n"
            "global.clearTimeout = (t) => { if (t) t.dead = true; };\n"
            "function tick(ms) { now += ms; for (const t of q.slice()) { if (!t.dead && t.at <= now) { t.dead = true; t.fn(); } } }\n"
            + _extract("draftDayLine") + "\n"
            "const seen = [];\n"
            "const line = draftDayLine(['2026-09-20','2026-09-21','2026-09-22','2026-09-23'], 20000, (t) => seen.push(t));\n"
            "line.start();\n"
            "tick(5000);            // its own clock: one day every 20000/4 ms\n"
            "line.sawDate('2026-09-23');  // a real frame jumps forward\n"
            "line.sawDate('2026-09-21');  // an earlier frame moves nothing\n"
            "tick(60000);           // and it never runs past the last day\n"
            "console.log(JSON.stringify(seen));\n"
        )
        seen = json.loads(_node(script))
        assert seen == ["Drafting Sunday…", "Drafting Monday…", "Drafting Wednesday…"]

    @_needs_node
    def test_the_day_line_alone_still_moves_when_the_stream_is_silent(self):
        script = (
            "const q = []; let now = 0;\n"
            "global.setTimeout = (fn, ms) => { const t = { at: now + ms, fn, dead: false }; q.push(t); return t; };\n"
            "global.clearTimeout = (t) => { if (t) t.dead = true; };\n"
            "function tick(ms) { now += ms; for (const t of q.slice()) { if (!t.dead && t.at <= now) { t.dead = true; t.fn(); } } }\n"
            + _extract("draftDayLine") + "\n"
            "const seen = [];\n"
            "const line = draftDayLine(['2026-09-20','2026-09-21','2026-09-22'], 9000, (t) => seen.push(t));\n"
            "line.start(); for (let i = 0; i < 10; i++) tick(3000);\n"
            "console.log(JSON.stringify(seen));\n"
        )
        assert json.loads(_node(script)) == ["Drafting Sunday…", "Drafting Monday…", "Drafting Tuesday…"]

    @_needs_node
    def test_what_im_using_lists_only_the_answers_given(self):
        intake = {
            "night_tags": {"2026-09-25": ["out"], "2026-09-26": ["guests"]},
            "guest_counts": {"2026-09-26": {"adults": 2, "children": 1}},
            "packed_lunch_days": ["2026-09-24", "2026-09-22"],
            "moods": ["Comfort food"], "cuisines": ["Mexican"],
            "freeform": "Chicken, potatoes and veg for dinners",
        }
        # Condensed and human since board D3 (2026-09-21) — the days as one
        # sentence, each row an icon and a sentence; the full shape is in
        # tests/test_intake_design_2026_09_21.py.
        using = "\n".join(_extract(n) for n in (
            "joinWords", "joinClauses", "sentence", "humanAttendance", "lowerFirst", "usingDaysSentence", "usingLines",
        )) + "\n"
        script = (
            using +
            f"const lines = usingLines({json.dumps(intake)}, (d) => weekdayName(d), true);\n"
            "console.log(JSON.stringify(lines));\n"
        )
        lines = json.loads(_node(script))
        assert lines == [
            {"icon": "home", "text": "Friday nobody’s home and Saturday you’ve got 3 guests."},
            {"icon": "bag", "text": "Lunches on the go Tuesday and Thursday."},
            {"icon": "pot", "text": "Comfort food and Mexican."},
            {"icon": "note", "text": "“Chicken, potatoes and veg for dinners”", "note": True},
            {"icon": "star", "text": "No dinner you had last week."},
        ]
        # Untouched steps add no line; a first week has no "last week".
        empty = json.loads(_node(using + "console.log(JSON.stringify(usingLines({}, (d) => d, false)));"))
        assert empty == []
        surprise = json.loads(_node(
            using + "console.log(JSON.stringify(usingLines({moods: ['Surprise me'], cuisines: ['Thai']}, (d) => d, false)));"
        ))
        assert surprise == [{"icon": "pot", "text": "Surprise me, leaning Thai."}]

    def test_the_card_reads_the_saved_intake_the_planner_uses(self):
        adv = _extract("advance")
        # The lines are built from what the save RETURNED — the revision the
        # generate call is then pointed at — not from the screen's state.
        assert "showDraftProgress(saved);" in adv
        # run_token rides along since 2026-09-22 (a dropped stream asks
        # /generate/status about this tap by name — test_draft_stream_drop.py).
        assert "streamGenerate({ intake_id: saved.intake_id, day_count: dayCount, run_token: runToken })" in adv
        assert "renderUsing(saved || {});" in _extract("showDraftProgress")
        assert "$('using').hidden = !lines.length;" in _extract("renderUsing")

    def test_nothing_is_tappable_while_drafting_and_the_page_cannot_resubmit(self):
        adv = _extract("advance")
        assert adv.startswith("function advance() {\n    if (drafting) return;") or "if (drafting) return;" in adv
        assert "drafting = true;" in adv
        show = _extract("showDraftProgress")
        assert "$('footer').hidden = true;" in show and "$('progress').hidden = true;" in show
        # A failed draft comes back to the last step, tappable again.
        assert "drafting = false;" in adv and "hideDraftProgress();" in adv

    def test_leaving_while_drafting_is_the_crumb_and_the_draft_still_lands(self):
        assert "if (step === 1 || drafting) leaveFlow();" in _extract("crumbTap")
        assert "location.href = '/week';" in _extract("leaveFlow")

    def test_the_server_says_whether_there_was_a_last_week(self):
        later = (datetime.date.today() + datetime.timedelta(days=1)).isoformat()
        # Nothing planned yet: no line.
        assert tools.get_week_intake_prefill(later)["recent_dinners_on_record"] is False
        # A week of dinners on record, inside the three-week window the
        # planner's own no-repeat rule reads (get_recent_meal_history).
        week = (datetime.date.today() - datetime.timedelta(days=7)).isoformat()
        conn = get_conn()
        plan_id = conn.execute(
            "INSERT INTO weekly_plans (household_id, week_start_date, status, content_start_date, day_count) "
            "VALUES (1, ?, 'approved', ?, 7)", (week, week),
        ).lastrowid
        for day in tools.period_dates(week, 7):
            conn.execute(
                "INSERT INTO meal_plan_entries (household_id, weekly_plan_id, date, slot, freeform_meal) "
                "VALUES (1, ?, ?, 'dinner', 'Chili')", (plan_id, day),
            )
        conn.commit()
        conn.close()
        assert tools.get_week_intake_prefill(later)["recent_dinners_on_record"] is True
        # Only dinners BEFORE the period count — a week planned after this
        # one is not "last week".
        assert tools.get_week_intake_prefill(week)["recent_dinners_on_record"] is False
