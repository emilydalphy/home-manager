"""Plan a week, step 2: one tap for "Nobody home all day".

Loop Board improvement (Emily's standing rule the night of 2026-09-22: as
easy as possible on the user). A day everybody is away used to be reached
by touching every person's initial under each of breakfast, lunch and
dinner — nine taps for a household of three, measured. One pill above the
rows now writes the same absences in one, and tapping it again puts back
what it replaced.

The control is a SHORTCUT THROUGH THE ROWS, never a second way of saying
the day is off: it writes `sheet.absent` and nothing else, its own on/off
is DERIVED from those rows rather than kept as a flag, and so the summary
line, the tile's words and Done's payload all say what they already said
for an all-away day. That is what most of this file is about — a flag of
its own is how two implementations of one rule come to disagree.

Most of it runs the page's own functions under node the way
tests/test_day_sheet_row_per_person.py does; the source checks pin the
control's bones and that nothing about it writes on tap.
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

_needs_node = pytest.mark.skipif(
    shutil.which("node") is None, reason="node is needed to run the page's own functions"
)


def _extract(name: str) -> str:
    start = PAGE.index(f"function {name}(")
    i = PAGE.index("{", start)
    depth, j = 0, i
    while True:
        if PAGE[j] == "{":
            depth += 1
        elif PAGE[j] == "}":
            depth -= 1
            if depth == 0:
                break
        j += 1
    return PAGE[start:j + 1]


def _var(name: str) -> str:
    m = re.search(rf"  var {name} = [\s\S]*?;\n", PAGE)
    assert m, name
    return m.group(0)


THREE = "[{id:1,name:'Emily',initial:'Em'},{id:2,name:'Ethan',initial:'Et'},{id:3,name:'Vic',initial:'V'}]"
ONE = "[{id:1,name:'Emily',initial:'E'}]"


def _prelude(members: str = THREE) -> str:
    return (
        "function esc(s) { return String(s == null ? '' : s); }\n"
        + _var("SHEET_SLOTS") + _var("SLOT_PILL") + _var("GUESTS_MAX") + _var("NOBODY_HOME_PILL")
        + _var("SHEET_TAGS")
        + "\n".join(_extract(n) for n in (
            "joinNames", "joinWords", "outGroups", "attendanceWords", "daySummary",
            "draftByDay", "daySheetPayload", "allDayAway", "dropHomeBefore",
            "holdSheetChange", "toggleNobodyHome", "toggleWhoPill", "stepGuests",
            "seedSheet", "reseedSheet",
        ))
        + f"\nvar attendance = {{ members: {members}, byDate: {{}} }};\n"
        + "var answers = { night_tags: {}, guest_counts: {} };\n"
        + "function paintSheet() {}\n"
        + "function blank(d) { return { date: d || '2026-09-25', absent: { breakfast: [], lunch: [], dinner: [] },"
          " guests: 0, tags: [], homeBefore: null, saving: false }; }\n"
    )


def _node(script: str, members: str = THREE) -> str:
    res = nodeharness.run_node(_prelude(members) + script, timeout=30)
    assert res.returncode == 0, f"node failed: {res.stderr}"
    return res.stdout.strip().splitlines()[-1]


def _json(script: str, members: str = THREE):
    return json.loads(_node(script, members))


ALL_AWAY = "Nobody home — I’ll plan nothing and buy nothing."


# ==========================================================================
# The cost, measured — nine taps, then one
# ==========================================================================

@_needs_node
class TestOneTapInsteadOfNine:
    def test_by_hand_a_household_of_three_takes_nine_taps(self):
        """The reproduction: what the sheet asked for before this control.

        Not a claim about the new code — it drives the per-person pills,
        which are unchanged — but the number this ticket exists to remove,
        measured rather than reasoned about.
        """
        got = self._by_hand()
        assert got["taps"] == 9
        assert got["taps"] / 3 == 3, "three taps per person"
        assert got["summary"] == ALL_AWAY

    def test_the_whole_day_is_one_tap(self):
        got = self._json_one_tap()
        assert got["summary"] == ALL_AWAY

    def test_one_tap_writes_exactly_what_the_nine_wrote(self):
        """The property that matters: one rule, one implementation.

        Not "it also reaches nobody-home" — the absences, the summary, the
        tile's words and the payload Done sends are the same object, so
        nothing downstream can tell which way the household got there.
        """
        # toggleWhoPill and toggleNobodyHome both read the page's own
        # `sheet`, so each is driven with `sheet` pointing at its own draft.
        script = (
            "var sheet = blank();\n"
            "attendance.members.forEach(function (m) { SHEET_SLOTS.forEach(function (s) { toggleWhoPill(m.name, s); }); });\n"
            "var hand = sheet;\n"
            "sheet = blank();\n"
            "toggleNobodyHome();\n"
            "var quick = sheet;\n"
            "function shape(d) { return { absent: d.absent, summary: daySummary(draftByDay(d), attendance.members),"
            "  tile: attendanceWords(draftByDay(d), attendance.members), payload: daySheetPayload(d) }; }\n"
            "console.log(JSON.stringify({ hand: shape(hand), quick: shape(quick) }));\n"
        )
        got = _json(script)
        assert got["quick"] == got["hand"]
        assert got["quick"]["summary"] == ALL_AWAY

    def _by_hand(self):
        return _json(
            "var sheet = blank(); var taps = 0;\n"
            "attendance.members.forEach(function (m) { SHEET_SLOTS.forEach(function (s) {\n"
            "  if (daySummary(draftByDay(sheet), attendance.members) === " + json.dumps(ALL_AWAY) + ") return;\n"
            "  toggleWhoPill(m.name, s); taps++; }); });\n"
            "console.log(JSON.stringify({ taps: taps, summary: daySummary(draftByDay(sheet), attendance.members) }));\n"
        )

    def _json_one_tap(self):
        return _json(
            "var sheet = blank(); toggleNobodyHome();\n"
            "console.log(JSON.stringify({ absent: sheet.absent,"
            " summary: daySummary(draftByDay(sheet), attendance.members) }));\n"
        )


# ==========================================================================
# What the day then reads as — the existing all-away words, not new ones
# ==========================================================================

@_needs_node
class TestItReadsTheWayAnAllAwayDayAlreadyDid:
    def test_the_summary_line_is_the_sentence_the_sheet_already_had(self):
        got = _json(
            "var sheet = blank(); toggleNobodyHome();\n"
            "console.log(JSON.stringify(daySummary(draftByDay(sheet), attendance.members)));\n"
        )
        assert got == ALL_AWAY
        # And it is the page's own string, not a copy written here.
        assert "return 'Nobody home — I’ll plan nothing and buy nothing.';" in PAGE

    def test_the_tile_says_nobody_home(self):
        got = _json(
            "var sheet = blank(); toggleNobodyHome();\n"
            "console.log(JSON.stringify(attendanceWords(draftByDay(sheet), attendance.members)));\n"
        )
        assert got == ["nobody home"]

    def test_done_sends_every_meal_with_everyone_out(self):
        got = _json(
            "var sheet = blank(); toggleNobodyHome();\n"
            "console.log(JSON.stringify(daySheetPayload(sheet)));\n"
        )
        assert got == {"date": "2026-09-25", "slots": {
            "breakfast": {"absent": ["Emily", "Ethan", "Vic"]},
            "lunch": {"absent": ["Emily", "Ethan", "Vic"]},
            "dinner": {"absent": ["Emily", "Ethan", "Vic"], "guest_count": 0},
        }}

    def test_a_household_of_one(self):
        got = _json(
            "var sheet = blank(); toggleNobodyHome();\n"
            "console.log(JSON.stringify({ on: allDayAway(sheet),"
            " summary: daySummary(draftByDay(sheet), attendance.members),"
            " tile: attendanceWords(draftByDay(sheet), attendance.members) }));\n",
            members=ONE,
        )
        assert got == {"on": True, "summary": ALL_AWAY, "tile": ["nobody home"]}


# ==========================================================================
# One tap undoes it
# ==========================================================================

@_needs_node
class TestOneTapUndoesIt:
    def test_tapping_it_again_puts_everyone_back(self):
        got = _json(
            "var sheet = blank(); toggleNobodyHome(); toggleNobodyHome();\n"
            "console.log(JSON.stringify({ absent: sheet.absent, on: allDayAway(sheet),"
            " summary: daySummary(draftByDay(sheet), attendance.members) }));\n"
        )
        assert got["absent"] == {"breakfast": [], "lunch": [], "dinner": []}
        assert got["on"] is False
        assert got["summary"] == ""

    def test_the_undo_gives_back_an_answer_that_was_already_there(self):
        """Without the copy this takes, changing your mind would quietly
        lose "Emily's out for lunch" — the whole-day control would have
        eaten it on the way past."""
        got = _json(
            # Tapped, not pushed: the copy is taken at the turn-on, so a
            # hand edit BEFORE it is captured and one after it is not.
            "var sheet = blank(); toggleWhoPill('Emily', 'lunch');\n"
            "toggleNobodyHome();\n"
            "var away = daySummary(draftByDay(sheet), attendance.members);\n"
            "toggleNobodyHome();\n"
            "console.log(JSON.stringify({ away: away, absent: sheet.absent,"
            " back: daySummary(draftByDay(sheet), attendance.members) }));\n"
        )
        assert got["away"] == ALL_AWAY
        assert got["absent"] == {"breakfast": [], "lunch": ["Emily"], "dinner": []}
        assert got["back"] == "Emily’s out for lunch — I’ll plan for Ethan and Vic."

    def test_the_guests_go_out_with_the_day_and_come_back_with_the_undo(self):
        """A dinner nobody is home for has nobody to host them, and the
        day's save drops the count anyway (attendance.set_day_attendance)
        — so the sheet drops it too rather than showing a stepper reading
        2 that Done would throw away."""
        got = _json(
            "var sheet = blank(); sheet.guests = 2;\n"
            "toggleNobodyHome();\n"
            "var away = { guests: sheet.guests, summary: daySummary(draftByDay(sheet), attendance.members) };\n"
            "toggleNobodyHome();\n"
            "console.log(JSON.stringify({ away: away, back: sheet.guests }));\n"
        )
        # The count is gone, so the day reads as the plain all-away sentence
        # rather than the "guests with nobody home" one.
        assert got["away"] == {"guests": 0, "summary": ALL_AWAY}
        assert got["back"] == 2

    def test_the_copy_is_dropped_once_it_has_been_used(self):
        got = _json(
            "var sheet = blank(); sheet.absent.dinner.push('Vic');\n"
            "toggleNobodyHome();\n"
            "var held = JSON.parse(JSON.stringify(sheet.homeBefore));\n"
            "toggleNobodyHome();\n"
            "console.log(JSON.stringify({ held: held, after: sheet.homeBefore }));\n"
        )
        assert got["held"]["absent"]["dinner"] == ["Vic"]
        assert got["after"] is None


# ==========================================================================
# The copy is kept in step with everything else that writes the sheet
#
# Both of these were shipped and both were reproduced end to end against a
# real server before they were fixed. Nothing in the first cut of this file
# mutated the sheet AFTER a turn-on and BEFORE the undo — the one
# arrangement in which either can appear.
# ==========================================================================

@_needs_node
class TestTheCopyCannotGoStale:
    def test_the_undo_does_not_reverse_a_holiday_answered_while_it_was_on(self):
        """BLOCKER: "Going to someone's" makes the server mark everyone out
        of that dinner, and reseedSheet pulls it into the open sheet. The
        copy still said the row was empty, so the undo won — the household
        was going out for Thanksgiving, the answer was still on record, and
        the week planned and shopped a dinner for it.

        Driven through reseedSheet itself, off attendance.byDate, which is
        exactly what answerHoliday leaves behind for it."""
        script = (
            "var sheet = blank('2026-10-12');\n"
            "toggleNobodyHome();\n"
            # The server answers: everyone out of that dinner, nothing else.
            "attendance.byDate['2026-10-12'] = { dinner: { absent_names: ['Emily', 'Ethan', 'Vic'],"
            " nobody_home: true, guest_count: 0 } };\n"
            "reseedSheet('2026-10-12');\n"
            "toggleNobodyHome();\n"
            "console.log(JSON.stringify({ absent: sheet.absent,"
            " payload: daySheetPayload(sheet),"
            " summary: daySummary(draftByDay(sheet), attendance.members) }));\n"
        )
        got = _json(script)
        # The day is no longer all-away — but the dinner they answered for
        # survives the undo, and Done still sends it.
        assert got["absent"]["dinner"] == ["Emily", "Ethan", "Vic"]
        assert got["absent"]["breakfast"] == [] and got["absent"]["lunch"] == []
        assert got["payload"]["slots"]["dinner"]["absent"] == ["Emily", "Ethan", "Vic"]
        assert got["summary"] == "Nobody home for dinner — I’ll plan nothing and buy nothing for it."

    def test_the_undo_does_not_destroy_a_guest_count_typed_while_it_was_on(self):
        """BLOCKER, four taps: control on (which zeroes the guests), then
        "Guests for dinner" — the row is still live — then +, then the
        control off. The household meant "everybody's home, two friends for
        dinner" and got "everybody's home, nobody coming"."""
        got = _json(
            "var sheet = blank();\n"
            "toggleNobodyHome();\n"
            "stepGuests(1); stepGuests(1);\n"
            "var typed = sheet.guests;\n"
            "toggleNobodyHome();\n"
            "console.log(JSON.stringify({ typed: typed, after: sheet.guests,"
            " summary: daySummary(draftByDay(sheet), attendance.members) }));\n"
        )
        assert got["typed"] == 2
        assert got["after"] == 2
        assert got["summary"] == "Dinner for 5 with 2 guests."

    def test_the_same_thing_by_the_longer_path_that_leaves_the_control_dark(self):
        """On, tap one person back in (dark), add guests, tap them out
        again (it lights, guests still there), undo.

        A GUARD, not a catch, and its first docstring said "the copy going
        stale while the control reads OFF" — which it cannot show, because
        toggleWhoPill NULLS the copy, so there is nothing left to go stale
        by the time the guests are added. Measured on review: the
        `stepGuests stops telling the copy` mutation reddens two tests and
        this is not one of them. What it does pin is that the longer path
        ends where the short one does."""
        got = _json(
            "var sheet = blank();\n"
            "toggleNobodyHome();\n"
            "toggleWhoPill('Vic', 'lunch');\n"
            "var dark = allDayAway(sheet);\n"
            "stepGuests(1); stepGuests(1);\n"
            "toggleWhoPill('Vic', 'lunch');\n"
            "var relit = allDayAway(sheet);\n"
            "toggleNobodyHome();\n"
            "console.log(JSON.stringify({ dark: dark, relit: relit, guests: sheet.guests }));\n"
        )
        assert got["dark"] is False and got["relit"] is True
        assert got["guests"] == 2

    def test_a_guest_count_the_server_wrote_survives_the_undo_too(self):
        """answerHoliday's hosting branch writes the headcount straight onto
        the open sheet; the same rule covers it."""
        got = _json(
            "var sheet = blank();\n"
            "toggleNobodyHome();\n"
            # what answerHoliday does for a hosting answer of 6
            "sheet.guests = Math.min(GUESTS_MAX, 6); holdSheetChange({ guests: true });\n"
            "toggleNobodyHome();\n"
            "console.log(JSON.stringify(sheet.guests));\n"
        )
        assert got == 6

    def test_a_row_tapped_by_hand_drops_the_copy_rather_than_refreshing_it(self):
        """Editing a pill is the household answering for one person, so
        "put back what the control replaced" stops naming anything — the
        undo from there is plainly everyone home."""
        got = _json(
            "var sheet = blank();\n"
            "toggleWhoPill('Emily', 'lunch');\n"
            "toggleNobodyHome();\n"
            "toggleWhoPill('Vic', 'dinner');\n"
            "var held = sheet.homeBefore;\n"
            "SHEET_SLOTS.forEach(function (s) { sheet.absent[s] = ['Emily', 'Ethan', 'Vic']; });\n"
            "toggleNobodyHome();\n"
            "console.log(JSON.stringify({ held: held, absent: sheet.absent }));\n"
        )
        assert got["held"] is None
        assert got["absent"] == {"breakfast": [], "lunch": [], "dinner": []}

    def test_every_other_writer_of_the_open_sheet_says_so(self):
        """The rule is only true if nothing writes sheet.absent or
        sheet.guests without telling the copy. Four do, and each is named
        here — a fifth wants adding to this list and to one of the two
        helpers."""
        writers = {
            "toggleWhoPill": "dropHomeBefore()",
            "stepGuests": "holdSheetChange({ guests: true })",
            "reseedSheet": "holdSheetChange({ slots: ['dinner'] })",
            "answerHoliday": "holdSheetChange({ guests: true })",
        }
        for name, call in writers.items():
            assert call in _extract(name), f"{name} does not keep the copy in step"
        # And nothing else touches them outside the toggle and the painters.
        #
        # WIDENED after review, 2026-09-24, because the first cut of this
        # sweep reached four of the writes in the file and NEITHER of the
        # two shapes that matter. It was
        # `sheet\.(absent|guests)\s*(=|\.push|\.splice)`, which does not
        # match an INDEXED write — `sheet.absent[slot] = []`, the shape
        # both real absent-writers use and the shape that caused blocker 1
        # — and attribution was `rfind("  function ")`, which cannot see
        # `async function`. Measured: a fifth writer doing an indexed write,
        # and a fifth writer inside a new async function, each passed the
        # whole file. It is also why `paintHoliday`, which writes nothing
        # at all, was in the allow-list: `answerHoliday` is async, so its
        # own write was attributed to the plain function above it.
        allowed = {"toggleWhoPill", "toggleNobodyHome", "stepGuests", "reseedSheet",
                   "answerHoliday"}
        body = PAGE[PAGE.index("  function seedSheet(date)"):]
        # An ASSIGNMENT, in any of the shapes this file writes one:
        # `sheet.guests =`, `sheet.absent.dinner =`, `sheet.absent[slot] =`,
        # and the mutating array calls. `=(?!=)` so a comparison is not a
        # write, and the optional index/property is the whole point — see
        # the note above.
        hits = list(re.finditer(
            r"sheet\.(?:absent|guests)(?:\s*\[[^\]]*\]|\.\w+)?\s*(?:=(?!=)|\+=|-=|\.push\b|\.splice\b)",
            body))
        assert len(hits) == 8, (
            f"the sweep found {len(hits)} writes, not the 8 that are there — "
            "it has drifted off the file rather than found something"
        )
        for m in hits:
            fn = max(body.rfind("  function ", 0, m.start()),
                     body.rfind("  async function ", 0, m.start()))
            named = re.match(r"  (?:async )?function (\w+)", body[fn:]).group(1)
            assert named in allowed, f"{named} writes the sheet without telling the copy"


# ==========================================================================
# On/off is derived from the rows, not kept beside them
# ==========================================================================

@_needs_node
class TestTheControlAndThePillsCannotDisagree:
    def test_tapping_one_person_back_in_turns_it_off(self):
        got = _json(
            "var sheet = blank(); toggleNobodyHome();\n"
            "var on = allDayAway(sheet);\n"
            "toggleWhoPill('Vic', 'lunch');\n"
            "console.log(JSON.stringify({ on: on, after: allDayAway(sheet),"
            " summary: daySummary(draftByDay(sheet), attendance.members) }));\n"
        )
        assert got["on"] is True
        assert got["after"] is False
        assert got["summary"] != ALL_AWAY

    def test_reaching_it_by_hand_reads_as_on(self):
        """No flag to forget to set: nine taps light the control just as
        one does."""
        got = _json(
            "var sheet = blank();\n"
            "attendance.members.forEach(function (m) { SHEET_SLOTS.forEach(function (s) { toggleWhoPill(m.name, s); }); });\n"
            "console.log(JSON.stringify({ on: allDayAway(sheet), copy: sheet.homeBefore }));\n"
        )
        assert got["on"] is True
        # Nothing was replaced, so there is nothing to give back: the undo
        # from here is plainly "everyone home".
        assert got["copy"] is None

    def test_a_day_that_is_only_partly_away_is_off(self):
        for script in (
            "sheet.absent.dinner = ['Emily', 'Ethan', 'Vic'];",
            "sheet.absent.breakfast = ['Emily']; sheet.absent.lunch = ['Emily']; sheet.absent.dinner = ['Emily'];",
            "SHEET_SLOTS.forEach(function (s) { sheet.absent[s] = ['Emily', 'Ethan']; });",
        ):
            got = _json(
                "var sheet = blank();\n" + script + "\n"
                "console.log(JSON.stringify(allDayAway(sheet)));\n"
            )
            assert got is False, script

    def test_with_nobody_on_record_the_state_is_unreachable(self):
        """A household with no members has no rows to shortcut past, and
        draftByDay needs a size before it will call a meal away — so the
        control is not offered, and would answer off if it were."""
        got = _json(
            "var sheet = blank();\n"
            "console.log(JSON.stringify({ on: allDayAway(sheet), summary: daySummary(draftByDay(sheet), []) }));\n",
            members="[]",
        )
        assert got == {"on": False, "summary": ""}


# ==========================================================================
# The control's bones
# ==========================================================================

class TestTheControlItself:
    def test_the_label_is_one_constant(self):
        assert "var NOBODY_HOME_PILL = 'Nobody home all day';" in PAGE
        row = _extract("nobodyHomeRowHtml")
        assert "NOBODY_HOME_PILL" in row
        assert "'Nobody home all day'" not in row, "the label is said once"

    def test_it_is_a_pill_with_a_pressed_state_and_its_own_id(self):
        row = _extract("nobodyHomeRowHtml")
        assert 'class="pill"' in row
        assert 'id="nobody-home"' in row
        assert 'aria-pressed="false"' in row

    def test_it_sits_under_the_question_and_above_the_rows(self):
        opener = _extract("openDaySheet")
        lede = opener.index("SHEET_QUESTION")
        control = opener.index("nobodyHomeRowHtml()")
        rows = opener.index('<div class="who-rows">')
        assert lede < control < rows

    def test_it_is_offered_only_when_there_is_a_household_to_be_out(self):
        opener = _extract("openDaySheet")
        assert "(members.length ? nobodyHomeRowHtml() : '')" in opener

    def test_it_is_wired_and_painted_by_id(self):
        # A class lookup across the sheet body answers with whichever came
        # first in the DOM — the 2026-09-17 bug class this sheet already
        # carries a warning about.
        assert "body.querySelector('#nobody-home')" in _extract("openDaySheet")
        assert "body.querySelector('#nobody-home')" in _extract("paintSheet")
        assert "allDayAway(sheet)" in _extract("paintSheet")

    @_needs_node
    def test_the_pill_is_painted_from_that_state_and_not_from_a_flag(self):
        """paintSheet itself, against a stub of the one node it touches —
        the pill's lit state is what the household sees, and a source
        marker cannot tell whether it moves."""
        script = (
            "var pill = { cls: {}, attrs: {},\n"
            "  classList: { toggle: function (c, on) { pill.cls[c] = on; } },\n"
            "  setAttribute: function (k, v) { pill.attrs[k] = v; } };\n"
            "var els = { 'day-sheet-body': { querySelectorAll: function () { return []; },\n"
            "  querySelector: function (sel) { return sel === '#nobody-home' ? pill : null; } } };\n"
            "function $(id) { return els[id]; }\n"
            + _extract("paintSheet") + "\n"
            "var sheet = blank();\n"
            "paintSheet();\n"
            "var off = { on: pill.cls.on, pressed: pill.attrs['aria-pressed'] };\n"
            "toggleNobodyHome();\n"
            "var on = { on: pill.cls.on, pressed: pill.attrs['aria-pressed'] };\n"
            "toggleWhoPill('Vic', 'lunch');\n"
            "console.log(JSON.stringify({ off: off, on: on,"
            " back: { on: pill.cls.on, pressed: pill.attrs['aria-pressed'] } }));\n"
        )
        got = _json(script)
        assert got["off"] == {"on": False, "pressed": "false"}
        assert got["on"] == {"on": True, "pressed": "true"}
        # And it goes dark again the moment one person is back in.
        assert got["back"] == {"on": False, "pressed": "false"}

    def test_nothing_is_saved_by_the_tap(self):
        # The sheet's own rule: Done writes the day, × throws it away.
        for name in ("toggleNobodyHome", "allDayAway", "nobodyHomeRowHtml"):
            assert "fetch(" not in _extract(name), name

    def test_the_toggle_writes_the_rows_and_nothing_of_its_own(self):
        body = _extract("toggleNobodyHome")
        assert "sheet.absent[slot]" in body
        assert "sheet.guests" in body
        # No parallel "this day is off" tag: the rows say it.
        for invented in ("night_tags", "'out'", "nobody_home"):
            assert invented not in body, invented

    def test_seed_sheet_starts_with_nothing_held(self):
        assert "homeBefore: null," in _extract("seedSheet")

    def test_the_row_is_tokens_only_and_borrows_the_pills_44px(self):
        css = PAGE[PAGE.index("  .allday-row {"):PAGE.index("  .who-rows {")]
        assert not re.search(r"#[0-9a-fA-F]{3,8}\b", css), "hard rule 9: every colour goes through a token"
        # Hard rule 6 comes from .pill, which the control is one of.
        pill = PAGE[PAGE.index("  .pill {"):PAGE.index("  .pill:hover")]
        assert "min-height: 44px" in pill

    def test_it_is_not_a_second_apricot(self):
        """Hard rule 5: the sheet's one primary is Done. The control takes
        the pills' sand-to-spruce, which is what every other toggle in here
        already wears."""
        css = PAGE[PAGE.index("  .allday-row {"):PAGE.index("  .who-rows {")]
        assert "apricot" not in css
        assert "--apricot" not in _extract("nobodyHomeRowHtml")
        assert ".pill.on { background: var(--spruce);" in PAGE
