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


def test_four_screens_one_question_each():
    for i in range(1, 5):
        assert 'id="q%d"' % i in PAGE and 'id="dot-%d"' % i in PAGE
    assert "var STEP_COUNT = 4;" in PAGE
    assert "Any days that are different this week?" in PAGE
    assert "Any lunches on the go this week?" in PAGE
    assert "What are you in the mood for?" in PAGE
    assert "Anything else I should plan around?" in PAGE
    # The "Why I'm asking" panel and the block-body sentences are gone.
    assert "Why I&rsquo;m asking" not in PAGE
    assert "Which lunches leave the house?" not in PAGE


def test_every_screen_has_a_quiet_skip_and_the_last_drafts():
    assert "var SKIP_LABELS = { 1: 'Nothing different', 2: 'Nothing on the go', 3: 'Surprise me', 4: 'Nothing else' };" in PAGE
    assert "$('skip').addEventListener('click', advance);" in PAGE
    assert "$('cta').textContent = 'Draft my week';" in PAGE


def test_the_days_are_tiles_that_open_a_day_sheet_with_every_meal():
    assert 'id="day-tiles"' in PAGE and 'id="day-sheet"' in PAGE
    assert "function openDaySheet(date)" in PAGE
    # Dinner keeps its tags, who's eating and the guest steppers; lunch and
    # breakfast get who's eating and a guests count of their own.
    assert "mealBlockHtml('lunch', 'Lunch')" in PAGE and "mealBlockHtml('breakfast', 'Breakfast')" in PAGE
    assert "async function stepSlotGuests(dayEl, slot, delta)" in PAGE
    assert "body: JSON.stringify({ date: d, slot: slot, guest_count: next })" in PAGE
    # Presence is per slot now, not dinner-only.
    assert "body: JSON.stringify({ date: d, slot: slot, member: name, present: wasOut })" in PAGE
    assert "function attendanceFor(date, slot)" in PAGE


def test_mood_pills_map_to_guidance_the_planner_reads():
    for m in ("Protein-heavy", "Veggie-heavy", "Fibre-focused", "Try a new cuisine"):
        assert m in PAGE, m
        assert m in week_intake.MOOD_GUIDANCE, m
    for label, line in week_intake.MOOD_GUIDANCE.items():
        assert len(line.split()) >= 8, label  # a real line, not the two words again
    assert '"mood_guidance": [' in AGENT
    assert "`intake.mood_guidance` spells out what each of those moods asks for" in AGENT
