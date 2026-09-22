"""Weekly intake, Build 4 of the screen-by-screen redesign (Emily, 2026-09-11).

Four short screens, one question each; the days as tiles opening a day
sheet where every meal can carry guests; mood pills that map to fuller
guidance for the planner.
"""
from pathlib import Path

from app.tools import week_intake

REPO = Path(__file__).resolve().parents[1]
PAGE = (REPO / "static" / "plan-week.html").read_text(encoding="utf-8")
AGENT = (REPO / "app" / "agent.py").read_text(encoding="utf-8")


def test_five_screens_one_question_each():
    # Five since 2026-09-21 ("Starting when?" first — Loop Board "The week
    # intake in the onboarding motion"); the questions lost their "this
    # week" now that the first screen settles which days.
    for i in range(1, 6):
        assert 'id="q%d"' % i in PAGE and 'id="bar-%d"' % i in PAGE
    assert "var STEP_COUNT = 5;" in PAGE
    # "Which days?" and the step-5 title are Emily's words (2026-09-21,
    # boards D1 / her pick) — tests/test_intake_design_2026_09_21.py.
    assert "<h1>Which days?</h1>" in PAGE
    assert "<h1>Any days that are different?</h1>" in PAGE
    assert "<h1>Any lunches on the go?</h1>" in PAGE
    assert "<h1>What are you in the mood for?</h1>" in PAGE
    assert "<h1>Anything else you want to share for planning this week?</h1>" in PAGE
    # The "Why I'm asking" panel and the block-body sentences are gone.
    assert "Why I&rsquo;m asking" not in PAGE
    assert "Which lunches leave the house?" not in PAGE


def test_the_skippable_screens_have_a_quiet_line_and_the_last_drafts():
    # Step 1 always has an answer, step 3's skip is the "Nothing on the go"
    # pill (board D2) and step 4's is the Surprise me card, so none of
    # those has a quiet line (2026-09-21).
    assert "var SKIP_LABELS = { 2: 'Nothing different', 5: 'Nothing else' };" in PAGE
    # The quiet line advances — except while "Add a cuisine" is open, when
    # it is that screen's "Never mind" (2026-09-21, board B4a).
    assert "$('skip').addEventListener('click', function () { if (cuisineOpen) closeCuisineScreen(); else advance(); });" in PAGE
    assert "$('cta').textContent = 'Draft my week';" in PAGE


def test_the_days_are_tiles_that_open_a_day_sheet_with_every_meal():
    assert 'id="day-tiles"' in PAGE and 'id="day-sheet"' in PAGE
    assert "function openDaySheet(date)" in PAGE
    # Since the second pass (2026-09-21, board B1b) the sheet is a row per
    # person with a pill per meal, and Done writes the whole day in one
    # call — tests/test_day_sheet_row_per_person.py pins the rest.
    assert "var SHEET_SLOTS = ['breakfast', 'lunch', 'dinner'];" in PAGE
    assert "'/api/week/' + encodeURIComponent(weekStart) + '/day-attendance'" in PAGE
    assert "function attendanceFor(date, slot)" not in PAGE


def test_nothing_still_reaches_for_the_old_day_list():
    """The verifier's find (2026-09-11): saveAwayRange repainted #days, which
    the tiles replaced, so a saved range showed a false error."""
    assert "$('days')" not in PAGE
    # The per-meal "Just this week?" offer went with the per-meal rows
    # (2026-09-21): it never persisted anything, and the row-per-person
    # sheet has no per-meal block for it to sit under.
    assert "offerToRemember" not in PAGE
    assert "Just this week?" not in PAGE


def test_mood_pills_map_to_guidance_the_planner_reads():
    for m in ("Protein-heavy", "Veggie-heavy", "Fibre-focused", "Try a new cuisine"):
        assert m in PAGE, m
        assert m in week_intake.MOOD_GUIDANCE, m
    for label, line in week_intake.MOOD_GUIDANCE.items():
        assert len(line.split()) >= 8, label  # a real line, not the two words again
    assert '"mood_guidance": [' in AGENT
    assert "`intake.mood_guidance` spells out what each of those moods asks for" in AGENT
