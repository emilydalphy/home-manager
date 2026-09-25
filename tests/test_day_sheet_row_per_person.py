"""Different days: the day sheet is a row per person (Emily, 2026-09-21).

Loop Board card, board B1b. Tapping a tile on "Any days that are
different?" opens the day's sheet: "Who's out, and when?", then a row per
person — their initial in a circle and three pills, No breakfast · No
lunch · No dinner — with one line under the rows saying what it adds up
to, a "Guests for dinner" row, the dinner line (Short on time · Got time ·
Leftovers) and Done. The old tag row (Nothing special / Nobody home /
Hosting guests …) and the per-meal guest steppers are gone, and nothing
saves by accident: Done writes the day in one call, × discards.

What the pills write is the SAME per-meal attendance the week already
read (attendance.slot_attendance), so the draft and the shopping list
skip a meal nobody is home for exactly as they did for an away stretch:
"Nobody home" is everyone out for all three, not a tag of its own.

Source checks pin the sheet's bones; the node harness runs the page's
own functions (the summary line, the tile words, the payload, Done and
×) the way tests/test_intake_motion_2026_09_21.py does; the server half
runs set_day_attendance and the route against the throwaway database.
"""
from __future__ import annotations

import datetime
import json
import re
import shutil
from pathlib import Path

import pytest

import nodeharness
from app import agent, tools

REPO = Path(__file__).resolve().parents[1]
PAGE = (REPO / "static" / "plan-week.html").read_text(encoding="utf-8")

_needs_node = pytest.mark.skipif(
    shutil.which("node") is None, reason="node is needed to run the page's own functions"
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


def _var(name: str) -> str:
    m = re.search(rf"  var {name} = [\s\S]*?;\n", PAGE)
    assert m, name
    return m.group(0)


def _sheet_markup() -> str:
    start = PAGE.index('<div class="sheet-scrim" id="day-sheet"')
    return PAGE[start:PAGE.index("</div>\n</div>", start)]


MEMBERS = "[{id:1,name:'Emily',initial:'Em'},{id:2,name:'Ethan',initial:'Et'},{id:3,name:'Vic',initial:'V'}]"


def _prelude() -> str:
    return (
        "function esc(s) { return String(s == null ? '' : s); }\n"
        + _var("SHEET_SLOTS") + _var("SLOT_PILL") + _var("SHEET_TAGS") + _var("GUESTS_MAX")
        + _var("USING_DAY_PHRASES") + _var("TAGS") + _var("SURPRISE_MOOD") + _var("PREP_WEEKDAYS")
        + "\n".join(_extract(n) for n in (
            "joinNames", "joinWords", "joinClauses", "sentence", "humanAttendance", "lowerFirst", "usingDaysSentence", "outGroups", "attendanceWords", "daySummary",
            "draftByDay", "daySheetPayload", "usingLines",
            # Step 3's line in usingLines (weekday lunches, 2026-09-25).
            "lunchUsingLine", "titleDay", "isoWeekday",
        )) + "\n"
    )


def _node(script: str) -> str:
    res = nodeharness.run_node(_prelude() + script, timeout=30)
    assert res.returncode == 0, f"node failed: {res.stderr}"
    return res.stdout.strip().splitlines()[-1]


def _by_day(**slots) -> str:
    """{'lunch': (['Emily'], False, 0)} -> the attendance shape the page reads."""
    out = {}
    for slot, (absent, nobody, guests) in slots.items():
        out[slot] = {"absent_names": absent, "nobody_home": nobody, "guest_count": guests}
    return json.dumps(out)


# ==========================================================================
# The sheet's bones
# ==========================================================================

class TestTheSheetIsARowPerPerson:
    def test_the_question_and_the_three_pills(self):
        assert "var SHEET_QUESTION = 'Who’s out, and when?';" in PAGE
        assert "var SHEET_SLOTS = ['breakfast', 'lunch', 'dinner'];" in PAGE
        assert "var SLOT_PILL = { breakfast: 'No breakfast', lunch: 'No lunch', dinner: 'No dinner' };" in PAGE
        row = _extract("personRowHtml")
        # The initial is drawn for the eye and the name is there for a screen
        # reader; the pills carry the person's name and the meal.
        assert '<span class="who-avatar" aria-hidden="true">' in row
        assert '<span class="sr-only">' in row
        assert "m.initial ||" in row, "the server's display initial, one rule for every surface"
        assert 'aria-pressed="false"' in row
        assert "aria-label=\"' + esc(m.name) + ' — '" in row

    def test_the_guests_row_the_dinner_line_and_done(self):
        assert "var GUESTS_PILL = 'Guests for dinner';" in PAGE
        opener = _extract("openDaySheet")
        assert "guestsRowHtml()" in opener
        assert '<p class="day-section-label">Dinner</p>' in opener
        assert "var SHEET_TAGS = ['rush', 'unrushed', 'left'];" in PAGE
        for label in ("Short on time", "Got time", "Leftovers"):
            assert f"label: '{label}'" in _var("TAGS")
        markup = _sheet_markup()
        assert '<button type="button" class="cta" id="day-done">Done</button>' in markup

    def test_the_old_tag_row_and_the_per_meal_steppers_are_gone(self):
        for gone in (
            "'Nothing special'", "'Nobody home'", "'Hosting guests'", "'Got time tonight'",
            "stepSlotGuests", "guests-slot", "mealBlockHtml", "presenceHtml", "toggleAvatar",
            "PRESENCE_QUESTION", "Is anyone out?", "Who’s eating", "offerToRemember", "Just this week?",
            "'Extra adults'", "'Extra children'",
        ):
            assert gone not in PAGE, gone
        # The tags themselves still exist on the server and still have a
        # word on the tile — only the sheet stopped offering them.
        assert "{ key: 'out',    label: 'nobody home' }" in PAGE

    def test_nothing_saves_by_accident(self):
        assert "$('day-done').addEventListener('click', saveDaySheet);" in PAGE
        assert "$('day-close').addEventListener('click', discardDaySheet);" in PAGE
        assert "if (e.target === $('day-sheet')) discardDaySheet();" in PAGE
        assert "else if (!$('day-sheet').hidden) discardDaySheet();" in PAGE
        # The rows write nothing on tap: no fetch anywhere but Done.
        for name in ("toggleWhoPill", "stepGuests", "toggleDinnerTag", "discardDaySheet"):
            assert "fetch(" not in _extract(name), name
        assert "'/api/week/' + encodeURIComponent(weekStart) + '/day-attendance'" in _extract("saveDaySheet")

    def test_the_pills_are_44px_targets_and_the_circle_grows(self):
        pill = PAGE[PAGE.index("  .pill {"):PAGE.index("  .pill:hover")]
        assert "min-height: 44px" in pill
        avatar = PAGE[PAGE.index("  .who-avatar {"):PAGE.index("  .who-avatar.add")]
        assert "min-width: 44px" in avatar and "width: 44px;" not in avatar.replace("min-width: 44px", "")
        assert "padding: 0 8px" in avatar
        assert ".sr-only { position: absolute; width: 1px; height: 1px;" in PAGE


# ==========================================================================
# What it adds up to — the line under the rows, and the tile's words
# ==========================================================================

@_needs_node
class TestTheSummaryLine:
    def _summary(self, by_day: str) -> str:
        return json.loads(_node(f"console.log(JSON.stringify(daySummary({by_day}, {MEMBERS})));"))

    def test_nothing_marked_says_nothing(self):
        assert self._summary("{}") == ""

    def test_one_person_out_for_two_meals_names_who_is_left(self):
        by_day = _by_day(lunch=(["Emily"], False, 0), dinner=(["Emily"], False, 0))
        assert self._summary(by_day) == "Emily’s out for lunch and dinner — I’ll plan for Ethan and Vic."

    def test_everyone_out_for_everything_is_nobody_home(self):
        everyone = ["Emily", "Ethan", "Vic"]
        by_day = _by_day(breakfast=(everyone, True, 0), lunch=(everyone, True, 0), dinner=(everyone, True, 0))
        assert self._summary(by_day) == "Nobody home — I’ll plan nothing and buy nothing."

    def test_a_meal_nobody_is_home_for_and_people_out_for_others(self):
        everyone = ["Emily", "Ethan", "Vic"]
        by_day = _by_day(breakfast=(everyone, True, 0), lunch=(["Emily", "Ethan"], False, 0), dinner=(["Emily", "Ethan"], False, 0))
        assert self._summary(by_day) == (
            "Nobody home for breakfast — I’ll plan nothing and buy nothing for it. "
            "Emily and Ethan are out for lunch and dinner — I’ll plan for Vic."
        )

    def test_guests_add_a_dinner_count(self):
        by_day = _by_day(lunch=(["Emily"], False, 0), dinner=(["Emily"], False, 2))
        assert self._summary(by_day) == (
            "Emily’s out for lunch and dinner — I’ll plan for Ethan and Vic. Dinner for 4 with 2 guests."
        )

    def test_everyone_out_at_different_meals_plans_each_for_whoever_is_home(self):
        by_day = _by_day(breakfast=(["Emily"], False, 0), lunch=(["Ethan"], False, 0), dinner=(["Vic"], False, 0))
        assert self._summary(by_day) == (
            "Emily’s out for breakfast. Ethan’s out for lunch. Vic’s out for dinner — "
            "I’ll plan each meal for whoever’s home."
        )

    def test_guests_for_a_dinner_nobody_is_home_for_is_said_plainly(self):
        everyone = ["Emily", "Ethan", "Vic"]
        by_day = _by_day(dinner=(everyone, False, 1))
        assert self._summary(by_day) == "Everyone’s out for dinner — 1 guest with nobody home, so I’ll plan nothing."
        by_day = _by_day(breakfast=(everyone, True, 0), lunch=(everyone, True, 0), dinner=(everyone, False, 2))
        assert self._summary(by_day) == (
            "Nobody home for breakfast and lunch — I’ll plan nothing and buy nothing for those. "
            "Everyone’s out for dinner — 2 guests with nobody home, so I’ll plan nothing."
        )
        assert "whoever" not in self._summary(by_day)

    def test_out_all_day_but_not_alone(self):
        by_day = _by_day(breakfast=(["Emily"], False, 0), lunch=(["Emily"], False, 0), dinner=(["Emily"], False, 0))
        assert self._summary(by_day) == "Emily’s out all day — I’ll plan for Ethan and Vic."


@_needs_node
class TestTheTileWords:
    def _words(self, by_day: str, members: str = MEMBERS) -> list:
        return json.loads(_node(f"console.log(JSON.stringify(attendanceWords({by_day}, {members})));"))

    def test_the_mockups_two_notes(self):
        everyone = ["Emily", "Ethan", "Vic"]
        assert self._words(_by_day(breakfast=(everyone, True, 0), lunch=(everyone, True, 0), dinner=(everyone, True, 0))) == ["nobody home"]
        assert self._words(_by_day(breakfast=(["Emily"], False, 0), lunch=(["Emily"], False, 0), dinner=(["Emily"], False, 0))) == ["Emily out"]

    def test_partial_days_and_guests(self):
        assert self._words(_by_day(lunch=(["Emily"], False, 0), dinner=(["Emily"], False, 1))) == ["Emily out for lunch and dinner", "+1 for dinner"]
        everyone = ["Emily", "Ethan", "Vic"]
        assert self._words(_by_day(lunch=(everyone, True, 0), dinner=(["Emily", "Ethan"], False, 0))) == [
            "nobody home for lunch", "Emily and Ethan out for dinner",
        ]

    def test_a_dinner_nobody_is_home_for_carries_no_guest_count(self):
        everyone = ["Emily", "Ethan", "Vic"]
        assert self._words(_by_day(dinner=(everyone, False, 1))) == ["nobody home for dinner"]

    def test_a_household_of_one(self):
        one = "[{id:1,name:'Emily',initial:'E'}]"
        assert self._words(_by_day(lunch=(["Emily"], True, 0)), one) == ["nobody home for lunch"]

    def test_the_building_screen_reads_the_same_words(self):
        # "Got it" (boards C1 → D3) says who's out from attendance, since
        # the sheet no longer writes it to the intake — and a guest count
        # said by attendance is not said twice by the intake's copy of it.
        # One sentence for the days since 2026-09-21 (board D3).
        script = (
            "var words = { '2026-09-25': ['Emily out for lunch and dinner', '+1 for dinner'], '2026-09-24': ['nobody home'] };\n"
            "var intake = { night_tags: { '2026-09-25': ['guests', 'rush'] }, guest_counts: { '2026-09-25': { adults: 1, children: 0 } } };\n"
            "console.log(JSON.stringify(usingLines(intake, function (d) { return d === '2026-09-24' ? 'Thursday' : 'Friday'; }, false, words)));"
        )
        assert json.loads(_node(script)) == [
            {"icon": "home", "text": "Thursday nobody’s home, and Friday is short on time, Emily’s out for lunch and dinner and 1 guest for dinner."},
        ]


# ==========================================================================
# Done and × — what is written, and what is not
# ==========================================================================

@_needs_node
class TestDoneAndDiscard:
    def _harness(self, body: str) -> str:
        return (
            "var weekStart = '2026-09-21';\n"
            f"var attendance = {{ members: {MEMBERS}, byDate: {{}} }};\n"
            "var answers = { night_tags: {}, guest_counts: {}, packed_lunch_days: [], moods: [], cuisines: [] };\n"
            "var sent = [];\n"
            "var painted = { tiles: 0, cta: 0, toast: 0 };\n"
            "function paintTiles() { painted.tiles++; }\n"
            "function paintCta() { painted.cta++; }\n"
            # The pop-up names the day it saved since 2026-09-23 (copy
            # sweep finding 1), so the stub takes the sentence and the two
            # helpers that build it.
            "function toastSaved(said) { painted.toast++; painted.said = said; }\n"
            "function weekdayName(iso) { return 'Friday'; }\n"
            "function savedLine(thing, verbed) { return thing ? thing + ' was ' + verbed : 'Saved'; }\n"
            "function paintSheet() {}\n"
            "var els = { 'day-sheet': { hidden: false }, 'day-done': { disabled: false },\n"
            "  'day-sheet-body': { querySelector: function () { return null; } } };\n"
            "function $(id) { return els[id]; }\n"
            "var console_ = console;\n"
            + "\n".join(_extract(n) for n in ("seedSheet", "applySheetToAnswers", "discardDaySheet"))
            + "\nasync " + _extract("saveDaySheet") + "\n"
            + body
        )

    def test_done_writes_the_day_in_one_call_and_paints_what_came_back(self):
        body = (
            "function fetch(url, opts) { sent.push({ url: url, method: opts.method, body: JSON.parse(opts.body) });\n"
            "  return Promise.resolve({ ok: true, json: function () { return Promise.resolve({ date: '2026-09-25', slots: {\n"
            "    breakfast: { everyone_home: true, absent_names: [], guest_count: 0, nobody_home: false },\n"
            "    lunch: { everyone_home: false, absent_names: ['Emily'], guest_count: 0, nobody_home: false },\n"
            "    dinner: { everyone_home: false, absent_names: ['Emily'], guest_count: 2, nobody_home: false } } }); } }); }\n"
            "var sheet = seedSheet('2026-09-25');\n"
            "sheet.absent.lunch.push('Emily'); sheet.absent.dinner.push('Emily'); sheet.guests = 2; sheet.tags = ['rush'];\n"
            "saveDaySheet().then(function () { console.log(JSON.stringify({ sent: sent, byDate: attendance.byDate, answers: answers, hidden: els['day-sheet'].hidden, sheet: sheet, painted: painted })); });\n"
        )
        got = json.loads(_node(self._harness(body)))
        assert got["sent"] == [{
            "url": "/api/week/2026-09-21/day-attendance", "method": "POST",
            "body": {"date": "2026-09-25", "slots": {
                "breakfast": {"absent": []}, "lunch": {"absent": ["Emily"]},
                "dinner": {"absent": ["Emily"], "guest_count": 2},
            }},
        }]
        # An everyone-home slot is not kept as a row; the others are what the server said.
        assert set(got["byDate"]["2026-09-25"]) == {"lunch", "dinner"}
        assert got["byDate"]["2026-09-25"]["dinner"]["guest_count"] == 2
        # The intake's own record: the dinner tag, and guests with the count.
        assert got["answers"]["night_tags"] == {"2026-09-25": ["rush", "guests"]}
        assert got["answers"]["guest_counts"] == {"2026-09-25": {"adults": 2, "children": 0}}
        assert got["hidden"] is True and got["sheet"] is None
        assert got["painted"] == {"tiles": 1, "cta": 1, "toast": 1, "said": "Friday was saved"}

    def test_discard_writes_nothing_and_changes_nothing(self):
        body = (
            "function fetch() { throw new Error('nothing should be sent'); }\n"
            "var sheet = seedSheet('2026-09-25');\n"
            "sheet.absent.dinner.push('Emily'); sheet.guests = 3; sheet.tags = ['left'];\n"
            "discardDaySheet();\n"
            "console.log(JSON.stringify({ byDate: attendance.byDate, answers: answers, hidden: els['day-sheet'].hidden, sheet: sheet, painted: painted }));\n"
        )
        got = json.loads(_node(self._harness(body)))
        assert got["byDate"] == {} and got["answers"]["night_tags"] == {} and got["answers"]["guest_counts"] == {}
        assert got["hidden"] is True and got["sheet"] is None
        assert got["painted"]["toast"] == 0

    def test_a_failed_save_keeps_the_sheet_open_and_says_so(self):
        body = (
            "var summary = { textContent: '', classList: { add: function (c) { this.on = c; }, remove: function () {} } };\n"
            "els['day-sheet-body'] = { querySelector: function () { return summary; } };\n"
            "function setTimeout() {}\n"
            "function fetch() { return Promise.resolve({ ok: false }); }\n"
            "var sheet = seedSheet('2026-09-25');\n"
            "sheet.absent.dinner.push('Emily');\n"
            "saveDaySheet().then(function () { console.log(JSON.stringify({ hidden: els['day-sheet'].hidden, open: !!sheet, said: summary.textContent, done: els['day-done'].disabled, answers: answers })); });\n"
        )
        got = json.loads(_node(self._harness(body)))
        assert got["hidden"] is False and got["open"] is True and got["done"] is False
        assert got["said"] == "I couldn’t save that just now — try again in a moment."
        assert got["answers"]["night_tags"] == {}

    def test_the_sheet_opens_on_what_is_saved_and_reads_an_old_out_tag(self):
        body = (
            "attendance.byDate['2026-09-25'] = { lunch: { absent_names: ['Vic'], guest_count: 0 }, dinner: { absent_names: [], guest_count: 1 } };\n"
            "answers.night_tags['2026-09-24'] = ['out', 'rush'];\n"
            "console.log(JSON.stringify({ fri: seedSheet('2026-09-25'), thu: seedSheet('2026-09-24') }));\n"
        )
        got = json.loads(_node(self._harness(body)))
        assert got["fri"]["absent"] == {"breakfast": [], "lunch": ["Vic"], "dinner": []}
        assert got["fri"]["guests"] == 1 and got["fri"]["tags"] == []
        # "out" from an older sheet or from chat: everyone out for dinner on
        # the rows, and only the sheet's own tags kept.
        assert got["thu"]["absent"]["dinner"] == ["Emily", "Ethan", "Vic"]
        assert got["thu"]["tags"] == ["rush"]

    def test_guests_for_a_dinner_everyone_is_out_for_are_not_recorded(self):
        body = (
            "applySheetToAnswers({ date: '2026-09-25', absent: { dinner: ['Emily', 'Ethan', 'Vic'] }, guests: 2, tags: [] });\n"
            "console.log(JSON.stringify(answers));\n"
        )
        got = json.loads(_node(self._harness(body)))
        assert got["night_tags"] == {} and got["guest_counts"] == {}

    def test_applying_the_sheet_drops_out_and_guests_when_there_are_none(self):
        body = (
            "answers.night_tags['2026-09-24'] = ['out', 'guests', 'unrushed'];\n"
            "answers.guest_counts['2026-09-24'] = { adults: 2, children: 0 };\n"
            "applySheetToAnswers({ date: '2026-09-24', absent: {}, guests: 0, tags: ['unrushed'] });\n"
            "console.log(JSON.stringify(answers));\n"
        )
        got = json.loads(_node(self._harness(body)))
        assert got["night_tags"] == {"2026-09-24": ["unrushed"]}
        assert got["guest_counts"] == {}


# ==========================================================================
# The server: one write for the day, the same attendance the week reads
# ==========================================================================

def _week() -> str:
    today = datetime.date.today()
    monday = today - datetime.timedelta(days=today.weekday())
    return (monday + datetime.timedelta(days=7)).isoformat()


@pytest.fixture
def three():
    for name in ("Emily", "Ethan", "Vic"):
        tools.add_member(name)


def test_set_day_attendance_writes_each_named_meal(three):
    week = _week()
    friday = tools._week_dates(week)[4]
    out = tools.set_day_attendance(friday, {
        "breakfast": {"absent": []},
        "lunch": {"absent": ["Emily"]},
        "dinner": {"absent": ["Emily"], "guest_count": 2},
    })
    assert out["date"] == friday and set(out["slots"]) == {"breakfast", "lunch", "dinner"}
    assert out["slots"]["lunch"]["absent_names"] == ["Emily"]
    assert out["slots"]["dinner"]["guest_count"] == 2 and out["slots"]["dinner"]["headcount"] == 4
    # Curly apostrophe since 2026-09-25: one writer, one spelling — the page
    # has said "Emily’s" all along. Same claim, one glyph.
    assert out["slots"]["dinner"]["summary"] == "Dinner for 4 — Emily’s out with 2 guests."
    # An untouched meal is not given a row of its own.
    assert out["slots"]["breakfast"]["explicit"] is False
    assert tools.get_slot_attendance(friday, "lunch")["absent_names"] == ["Emily"]
    assert tools.get_slot_attendance(friday, "dinner")["guest_count"] == 2


def test_everyone_out_for_all_three_is_nobody_home_and_the_meals_are_away(three):
    week = _week()
    thursday = tools._week_dates(week)[3]
    everyone = ["Emily", "Ethan", "Vic"]
    out = tools.set_day_attendance(thursday, {s: {"absent": everyone} for s in ("breakfast", "lunch", "dinner")})
    for slot in ("breakfast", "lunch", "dinner"):
        assert out["slots"][slot]["nobody_home"] is True
        assert tools.get_slot_need(thursday, slot)["need"] == "away"
    # Putting one person back for dinner undoes that meal's away and no other.
    tools.set_day_attendance(thursday, {"dinner": {"absent": ["Emily", "Ethan"]}})
    assert tools.get_slot_need(thursday, "dinner")["need"] != "away"
    assert tools.get_slot_need(thursday, "lunch")["need"] == "away"


def test_the_draft_and_the_list_skip_a_meal_nobody_is_home_for(three, monkeypatch):
    week = _week()
    dates = tools._week_dates(week)
    thursday = dates[3]
    tools.add_recipe("Chili", ingredients=[{"item": "beans", "qty": "1 tin"}])
    tools.set_day_attendance(thursday, {s: {"absent": ["Emily", "Ethan", "Vic"]} for s in ("breakfast", "lunch", "dinner")})
    seen = {}

    def _fake(context):
        seen["context"] = context
        return [
            {"date": d, "slot": s, "meal_name": "Chili", "is_new_recipe": False, "reasoning": "fits"}
            for d in dates for s in tools.WEEK_SLOTS
        ]

    monkeypatch.setattr(agent, "generate_weekly_plan_llm", _fake)
    result = agent.generate_weekly_plan(week)
    away = seen["context"]["slot_needs"]["away_slots"]
    assert {(a["date"], a["slot"]) for a in away} >= {(thursday, "breakfast"), (thursday, "lunch"), (thursday, "dinner")}
    from app.db import get_conn
    conn = get_conn()
    rows = conn.execute(
        "SELECT slot, slot_state FROM meal_plan_entries WHERE weekly_plan_id = ? AND date = ? AND component_category IS NULL",
        (result["weekly_plan_id"], thursday),
    ).fetchall()
    links = conn.execute(
        "SELECT COUNT(*) AS n FROM meal_plan_grocery_links mpgl JOIN meal_plan_entries mpe ON mpe.id = mpgl.meal_plan_entry_id "
        "WHERE mpe.weekly_plan_id = ? AND mpe.date = ?",
        (result["weekly_plan_id"], thursday),
    ).fetchone()["n"]
    conn.close()
    assert {r["slot"]: r["slot_state"] for r in rows if r["slot"] in ("breakfast", "lunch", "dinner")} == {
        "breakfast": "planned_empty", "lunch": "planned_empty", "dinner": "planned_empty",
    }
    assert links == 0
    # The day card on "Which days" says so: the need is what the copy reads.
    menu = tools.get_week_menu(result["weekly_plan_id"])
    day = next(d for d in menu["days"] if d["date"] == thursday)
    assert day["dinner"]["need"] == "away"


def test_an_unchanged_meal_is_not_rewritten_and_a_wrong_name_is_refused(three):
    week = _week()
    friday = tools._week_dates(week)[4]
    tools.set_day_attendance(friday, {"lunch": {"absent": ["Vic"]}})
    before = tools.get_slot_attendance(friday, "lunch")
    again = tools.set_day_attendance(friday, {"lunch": {"absent": ["Vic"]}, "breakfast": {"absent": []}})
    assert again["slots"]["lunch"]["absent_names"] == before["absent_names"]
    assert tools.get_week_attendance(week).get(friday, {}).keys() == {"lunch"}
    with pytest.raises(ValueError):
        tools.set_day_attendance(friday, {"dinner": {"absent": ["Nobody"]}})
    with pytest.raises(ValueError):
        tools.set_day_attendance(friday, {"brunch": {"absent": []}})
    with pytest.raises(ValueError):
        tools.set_day_attendance(friday, {"dinner": "Emily"})
    # Guests are clamped to the stepper's range.
    assert tools.set_day_attendance(friday, {"dinner": {"guest_count": 40}})["slots"]["dinner"]["guest_count"] == 10
    # Everyone out means nobody home, guests or not: the count is dropped
    # and the meal is away — there is nobody to host them.
    out = tools.set_day_attendance(friday, {"dinner": {"absent": ["Emily", "Ethan", "Vic"], "guest_count": 2}})
    assert out["slots"]["dinner"]["guest_count"] == 0 and out["slots"]["dinner"]["nobody_home"] is True
    assert tools.get_slot_need(friday, "dinner")["need"] == "away"


def test_the_route_saves_the_sheet_and_refuses_what_it_cannot_read(signed_in, three):
    week = _week()
    friday = tools._week_dates(week)[4]
    res = signed_in.post(f"/api/week/{week}/day-attendance", json={
        "date": friday, "slots": {"lunch": {"absent": ["Emily"]}, "dinner": {"absent": ["Emily"], "guest_count": 1}},
    })
    assert res.status_code == 200, res.text
    body = res.json()
    # Curly apostrophe since 2026-09-25 — see the note above. Same claim.
    assert body["slots"]["dinner"]["summary"] == "Dinner for 3 — Emily’s out with 1 guest."
    assert body["slots"]["lunch"]["everyone_home"] is False
    bad = signed_in.post(f"/api/week/{week}/day-attendance", json={"date": friday, "slots": {"dinner": {"absent": ["Zed"]}}})
    assert bad.status_code == 400
    assert "Zed" in bad.json()["detail"]
    assert signed_in.post(f"/api/week/not-a-date/day-attendance", json={"date": friday, "slots": {}}).status_code == 400
