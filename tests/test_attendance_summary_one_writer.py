"""One writer for the sentence under a meal (Loop Board, 2026-09-25).

`attendance.summary_line` writes the line a day card shows under its
presence avatars — "Dinner for 3 — Vineeth's out with 2 guests." Both
write routes answered with it and the week READ did not, so a screen
could be told the sentence after a tap and had nothing to render on open.
`static/plan-week.html` closed that gap with a second implementation
(`localSummary`), which drifted: it joined an absence and a guest with
"and" where the server says "with", so the caption reworded itself the
moment it was re-saved. That copy was deleted on 2026-09-21 by the
row-per-person sheet, but the hole it was written for was still open —
and one difference outlived it, the server's STRAIGHT apostrophe in
"'s out" against the curly one every other sentence in the app writes.

What this file pins:

  * the sentence rides on the attendance dict itself, so the GET carries
    it per (date, slot) exactly as both POSTs do;
  * the sentence on open and the sentence after a tap are BYTE-IDENTICAL,
    which is the property a second implementation cannot promise;
  * there is one writer — `_attendance_dict` — so no route stamps it;
  * the apostrophes are curly, like the rest of the app's copy.

Read the red-against-main counts carefully. Most of these die on a
MISSING KEY against main, which is the only kind of red a new field can
have and is not a behaviour catch; each docstring says which it is.
"""
from __future__ import annotations

import datetime
import re
from pathlib import Path

import pytest

from app import tools
from app.tools import attendance as _attendance

REPO = Path(__file__).resolve().parents[1]
MAIN = (REPO / "app" / "main.py").read_text(encoding="utf-8")
ATT = (REPO / "app" / "tools" / "attendance.py").read_text(encoding="utf-8")
PAGE = (REPO / "static" / "plan-week.html").read_text(encoding="utf-8")

CURLY = "’"


def _week_start(offset_weeks: int = 1) -> str:
    today = datetime.date.today()
    monday = today - datetime.timedelta(days=today.weekday())
    return (monday + datetime.timedelta(days=7 * offset_weeks)).isoformat()


@pytest.fixture
def couple():
    """Emily and Vineeth — the two-adult household the model was designed against."""
    tools.add_member("Emily")
    tools.add_member("Vineeth")
    return {m["name"]: m["id"] for m in tools.list_members()}


def _thursday() -> str:
    return tools._week_dates(_week_start())[3]


# ==========================================================================
# The read carries the sentence
# ==========================================================================

def test_the_week_read_carries_the_sentence_per_date_and_slot(couple):
    """CATCH, and the only test in this file that fails on main at the
    assertion it is named for: `assert "summary" in att`. Every other red
    one below dies earlier, on a KeyError from the same missing field."""
    week, day = _week_start(), _thursday()
    tools.set_member_attendance(day, "dinner", "Vineeth", present=False)
    tools.set_guest_count(day, "dinner", 2)

    att = tools.get_week_attendance(week)[day]["dinner"]
    assert "summary" in att, "the week read has to carry the sentence, not only the writes"
    assert att["summary"] == f"Dinner for 3 — Vineeth{CURLY}s out with 2 guests."


def test_one_slot_read_carries_it_too(couple):
    """Red on main, but on `KeyError: 'summary'` rather than on its own
    claim — the only kind of red a new field can have. The claim it is
    here for is that the single-slot read is the other door into the same
    dict, so a screen asking about one meal is not answered differently."""
    day = _thursday()
    tools.set_member_attendance(day, "lunch", "Emily", present=False)
    assert tools.get_slot_attendance(day, "lunch")["summary"] == f"Lunch for 1 — Emily{CURLY}s out."


def test_an_ordinary_meal_says_nothing_at_all(couple):
    """GUARD. Red on main only on the missing key, which is NOT what it is
    named for: a day where everyone is home has no news, and an empty
    string is what renders as an empty line rather than a sentence about
    nothing. Pinned by the mutation that makes _attendance_dict stamp a
    placeholder instead of summary_line's own answer."""
    assert tools.get_slot_attendance(_thursday(), "dinner")["summary"] == ""


def test_a_meal_nobody_is_home_for_says_so(couple):
    """CATCH on the apostrophe, though against main it dies earlier on the
    missing key and never reaches that assertion. The branch the card
    never mentions, and the one carrying the SECOND straight apostrophe —
    no test anywhere asserted on this sentence before this file."""
    day = _thursday()
    tools.set_slot_attendance(day, "dinner", present_member_ids=[])
    att = tools.get_slot_attendance(day, "dinner")
    assert att["summary"] == f"Dinner skipped — nobody{CURLY}s home. Nothing planned, nothing bought."


# ==========================================================================
# The property the card asks for: open and after-a-tap are byte-identical
# ==========================================================================

class TestTheSentenceOnOpenAndAfterATapAreTheSame:
    """The whole point. A second implementation can agree today and drift
    tomorrow; these compare the two answers byte for byte, so a drift is
    loud rather than something a person notices weeks later."""

    def test_over_http_a_tap_and_a_reopen_say_the_same_words(self, signed_in, couple):
        """Red on main, on `KeyError: 'summary'` — the GET has nothing to
        compare, so the equality this is named for is never reached. Say
        that plainly: the red is evidence the field is missing, and the
        mutation below is what shows the equality itself bites."""
        week, day = _week_start(), _thursday()
        tapped = signed_in.post(
            f"/api/week/{week}/attendance",
            json={"date": day, "slot": "dinner", "member": "Vineeth", "present": False},
        )
        assert tapped.status_code == 200, tapped.text
        after_tap = tapped.json()["summary"]

        reopened = signed_in.get(f"/api/week/{week}/attendance")
        assert reopened.status_code == 200, reopened.text
        on_open = reopened.json()["attendance"][day]["dinner"]["summary"]

        assert on_open == after_tap, "re-opening the day must not reword what the tap said"
        assert on_open == f"Dinner for 1 — Vineeth{CURLY}s out."

    def test_the_case_that_drifted_before_an_absence_AND_a_guest(self, signed_in, couple):
        """Red on main on the missing key, not on its own claim. What it
        is for: the exact shape `localSummary` got wrong — it joined the
        two clauses with "and" where the server says "with", so a re-save
        silently reworded the caption."""
        week, day = _week_start(), _thursday()
        done = signed_in.post(
            f"/api/week/{week}/day-attendance",
            json={"date": day, "slots": {"dinner": {"absent": ["Vineeth"], "guest_count": 2}}},
        )
        assert done.status_code == 200, done.text
        after_done = done.json()["slots"]["dinner"]["summary"]

        on_open = signed_in.get(
            f"/api/week/{week}/attendance"
        ).json()["attendance"][day]["dinner"]["summary"]

        assert on_open == after_done
        assert " with 2 guests." in on_open and " and 2 guests" not in on_open

    def test_all_three_doors_answer_with_one_string(self, signed_in, couple):
        """Red on main on the missing key, not on its own claim. The two
        writes and the read are the three places this sentence reaches a
        browser; naming them together is what stops a future field being
        added to two of them."""
        week, day = _week_start(), _thursday()
        signed_in.post(
            f"/api/week/{week}/day-attendance",
            json={"date": day, "slots": {"dinner": {"absent": ["Emily"], "guest_count": 1}}},
        )
        one_tap = signed_in.post(
            f"/api/week/{week}/attendance",
            json={"date": day, "slot": "dinner", "guest_count": 1},
        ).json()["summary"]
        whole_day = signed_in.post(
            f"/api/week/{week}/day-attendance",
            json={"date": day, "slots": {"dinner": {"absent": ["Emily"], "guest_count": 1}}},
        ).json()["slots"]["dinner"]["summary"]
        on_open = signed_in.get(
            f"/api/week/{week}/attendance"
        ).json()["attendance"][day]["dinner"]["summary"]

        assert one_tap == whole_day == on_open == f"Dinner for 2 — Emily{CURLY}s out with 1 guest."

    def test_every_deviating_slot_of_a_week_carries_its_own(self, signed_in, couple):
        """Red on main on the missing key, not on its own claim. A week is
        read in one round trip, so "the GET carries it" has to mean every
        slot in it rather than the first one somebody checked."""
        week = _week_start()
        days = tools._week_dates(week)
        for day in days[:3]:
            for slot in ("breakfast", "lunch", "dinner"):
                tools.set_member_attendance(day, slot, "Vineeth", present=False)

        got = signed_in.get(f"/api/week/{week}/attendance").json()["attendance"]
        seen = 0
        for day in days[:3]:
            for slot in ("breakfast", "lunch", "dinner"):
                att = got[day][slot]
                assert att["summary"] == tools.attendance_summary_line(att), (day, slot)
                assert att["summary"], "a deviating slot always has something to say"
                seen += 1
        assert seen == 9


# ==========================================================================
# One writer
# ==========================================================================

class TestThereIsExactlyOneWriter:
    def test_the_dict_builder_is_the_only_caller_in_the_attendance_module(self):
        """CATCH — and the COUNT half of it would have been worthless on its
        own, which is worth writing down. Main also has exactly ONE
        assignment from summary_line; it is simply in the wrong place
        (set_day_attendance, a WRITE, which is why the read had none), so
        `len(calls) == 1` passes on main. What fails there is the second
        assertion, that the one writer is the dict builder. A source check
        because the defect is a call site EXISTING in the wrong function,
        which no behavioural test can see — every stamping produces the
        same words."""
        body = re.sub(r"#[^\n]*", "", ATT)
        calls = [m.start() for m in re.finditer(
            r"^\s*\w[\w\[\]\"']*\s*=\s*summary_line\(", body, flags=re.M)]
        assert len(calls) == 1, (
            "the sentence is written in _attendance_dict and nowhere else; "
            f"found {len(calls)} assignments from summary_line"
        )
        # ...and in _attendance_dict specifically, which is what makes it a
        # property of every attendance dict rather than of whoever happened
        # to build this one.
        start = body.index("def _attendance_dict(")
        end = body.index("\ndef ", start + 1)
        assert start < calls[0] < end, "the one writer has to be the dict builder"

    def test_no_route_stamps_the_sentence_onto_its_answer(self):
        """CATCH — fails on main at its own assertion. The single-slot POST
        route wrote `result["summary"] = tools.attendance_summary_line(
        result)`. A route that has to remember is a route the next route
        forgets to copy, which is how the GET came to be the odd one out."""
        assert "attendance_summary_line" not in re.sub(r"#[^\n]*", "", MAIN)

    def test_the_page_keeps_no_copy_of_the_rule(self):
        """GUARD, green on main since 2026-09-21 — `localSummary` went with
        the row-per-person sheet. Pinned so it cannot come back: the page's
        own line is a WHOLE-DAY sentence (daySummary) and is a different
        sentence, not a second spelling of this one."""
        assert "localSummary" not in PAGE
        assert "function daySummary(" in PAGE, "the page's own whole-day line is a different sentence"

    def test_the_week_menu_still_reads_the_same_function(self):
        """GUARD: get_week_menu decorates a slot with `attendance_summary`
        under a name of its own. It is a legitimate second CALL SITE of one
        function, which is the opposite of a second implementation — pinned
        so nobody 'tidies' it into a hand-written copy."""
        wp = (REPO / "app" / "tools" / "weekly_plan.py").read_text(encoding="utf-8")
        assert '_attendance.summary_line(att)' in wp


# ==========================================================================
# One spelling
# ==========================================================================

class TestOneSpelling:
    def test_the_sentence_is_written_with_curly_apostrophes(self, couple):
        """Red on main on the missing key rather than on the apostrophe —
        the behavioural half of the spelling claim, which the source check
        two tests below covers directly and does fail on main for its own
        reason."""
        day = _thursday()
        tools.set_member_attendance(day, "dinner", "Vineeth", present=False)
        said = tools.get_slot_attendance(day, "dinner")["summary"]
        assert CURLY in said
        assert "'" not in said, f"straight apostrophe in {said!r}"

    def test_the_nobody_home_sentence_too(self, couple):
        """Red on main on the missing key. The claim: the second straight
        apostrophe, in a branch no test asserted on at all before this
        file."""
        day = _thursday()
        tools.set_slot_attendance(day, "dinner", present_member_ids=[])
        said = tools.get_slot_attendance(day, "dinner")["summary"]
        assert CURLY in said and "'" not in said, said

    def test_the_source_holds_no_straight_apostrophe_in_the_sentence(self):
        """CATCH — fails on main at its own assertion, on the docstring's
        own example line and then on the code. Behaviour covers the two
        shapes a household can reach; this covers the strings themselves,
        so a third branch added later cannot quietly reintroduce the other
        glyph."""
        start = ATT.index("def summary_line(")
        end = ATT.index("\ndef ", start + 1)
        body = ATT[start:end]
        for line in body.splitlines():
            code = line.split("#", 1)[0]
            assert "'s out" not in code and "nobody's" not in code, line

    def test_the_page_and_the_server_now_agree(self, couple):
        """Red on main on the missing key. The claim, and the one the whole
        card is about: the sentence the server sends and the apostrophe the
        page has always written are the same character, so the two
        spellings can no longer sit seconds apart on one screen."""
        day = _thursday()
        tools.set_member_attendance(day, "dinner", "Emily", present=False)
        said = tools.get_slot_attendance(day, "dinner")["summary"]
        assert f"Emily{CURLY}s out" in said
        # The page's own contraction, as it is actually written in the file.
        assert f"{CURLY}s" in PAGE


# ==========================================================================
# It costs nothing
# ==========================================================================

def test_carrying_the_sentence_opens_no_extra_connection(couple, monkeypatch):
    """GUARD on the cost, and the reason it is safe to do on every read:
    summary_line reads the dict it is handed and opens no connection, so a
    whole week of deviating slots still costs ONE. Counts sqlite3.connect
    globally, because a module-level patch cannot see a function-local
    `from ..db import get_conn`. Measured on main: 1, and 1 after.

    Pinned by the mutation that gives summary_line a clock or a lookup of
    its own — anything per-slot here would be 21 connections on a screen
    that loads every time the week is opened."""
    import sqlite3

    week = _week_start()
    days = tools._week_dates(week)
    for day in days:
        for slot in ("breakfast", "lunch", "dinner"):
            tools.set_member_attendance(day, slot, "Vineeth", present=False)

    seen = {"n": 0}
    real = sqlite3.connect

    def counting(*a, **k):
        seen["n"] += 1
        return real(*a, **k)

    monkeypatch.setattr(sqlite3, "connect", counting)
    got = tools.get_week_attendance(week)
    monkeypatch.setattr(sqlite3, "connect", real)

    assert sum(len(v) for v in got.values()) == 21, "the shape this is measuring"
    assert seen["n"] == 1, f"a week's sentences must cost no connection of their own, saw {seen['n']}"
