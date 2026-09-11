"""Now, re-cut (Emily, 2026-09-11; Build 2 of the screen-by-screen redesign).

One card for next-up (sand, in the gutter, no action inside it), the action
in a dock at the foot, the plan offer as a question with nothing under it,
the coaching card turned into a one-time sheet, and the dinner suggestions
folded behind one Pick.
"""
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SHELL_HTML = (REPO / "static" / "shell.html").read_text(encoding="utf-8")
SHELL_JS = (REPO / "static" / "shell.js").read_text(encoding="utf-8")
SHELL_CSS = (REPO / "static" / "shell.css").read_text(encoding="utf-8")
DESIGN = (REPO / "DESIGN_SYSTEM.md").read_text(encoding="utf-8")


def _fn(name):
    start = SHELL_JS.index("  function %s(" % name)
    end = SHELL_JS.index("\n  }\n", start)
    return SHELL_JS[start:end]


def test_next_up_is_a_card_in_the_gutter_not_the_hero():
    assert '<div id="today-next-up" class="today-area-nextup shell-card nextup-card" hidden></div>' in SHELL_JS
    assert 'class="dinner-hero nextup-hero"' not in SHELL_JS
    card = SHELL_CSS[SHELL_CSS.index(".nextup-card {"):]
    card = card[:card.index("}")]
    assert "background: var(--sand);" in card
    assert ".today-area-nextup { margin: 0 20px 14px; }" in SHELL_CSS


def test_the_card_carries_no_action_the_dock_does():
    html = _fn("nextUpCardHtml")
    assert "hero-action" not in html
    assert "data-move-action" not in html
    assert "moveTickHtml(move)" in html  # the tick stays on the card
    dock = _fn("renderTodayDock")
    assert 'data-move-action="' in dock
    assert "Let’s plan the week" in dock and ">Not now<" in dock
    assert '<div class="dock today-dock" id="today-dock" hidden></div>' in SHELL_JS
    # Rendered whenever the moves or the offer change.
    assert SHELL_JS.count("renderTodayDock(panel)") >= 4


def test_the_offer_is_a_question_with_nothing_under_it():
    offer = _fn("renderPlanWeekNudge")
    assert "together?" in offer
    assert "Two rounds of questions" not in offer
    assert "plan-nudge-cta" not in offer and "plan-nudge-dismiss" not in offer
    assert "WHENEVER SUITS YOU" not in offer


def test_the_coaching_card_is_a_one_time_sheet_at_body_level():
    assert '<div id="coach-card-slot"></div>' in SHELL_HTML
    assert 'id="coach-card-slot" class="today-area-nudge"' not in SHELL_JS
    assert "#coach-card-slot:empty { display: none; }" in SHELL_CSS
    assert "#coach-sheet,\n#prefs-sheet,\n#tips-sheet" in SHELL_CSS
    assert "plan-nudge-eyebrow\">A QUICK WORD" not in SHELL_JS


def test_the_dinner_suggestions_fold_behind_one_pick():
    assert 'class="shell-card needs-you-card is-folded urgency-' in SHELL_JS
    assert 'data-ny-unfold' in SHELL_JS
    assert ".needs-you-card.is-folded .ny-options { display: none; }" in SHELL_CSS


def test_the_design_system_says_now_has_a_dock_and_no_hero():
    assert "Now got a dock on 2026-09-11" in DESIGN
    assert "Now has no hero" in DESIGN
