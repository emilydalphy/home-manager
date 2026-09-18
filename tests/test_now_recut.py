"""Now, re-cut (Emily, 2026-09-11; Build 2 of the screen-by-screen redesign).

One card for next-up (sand, in the gutter, no action inside it), the action
in a dock at the foot, the plan offer as a question with nothing under it,
the coaching card turned into a one-time sheet, and the dinner suggestions
folded behind one Pick.

Since 2026-09-13 the next-up card is the one tinted row of the day's
moves (two cards, Shop and Cook, since 2026-09-17 —
tests/test_today_shop_cook.py); what this file still guards is that the
action stayed in the dock and never came back into the content.
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


def test_next_up_is_not_the_hero_and_not_a_card_above_the_day():
    assert 'class="dinner-hero nextup-hero"' not in SHELL_JS
    assert 'id="today-next-up"' not in SHELL_JS
    assert ".nextup-card {" not in SHELL_CSS and ".today-area-nextup" not in SHELL_CSS
    # The next-up move is the one tinted row of the Shop / Cook cards now.
    assert "dayGroupsHtml(moves, featured)" in _fn("renderTodayMoves")
    assert "return m.done ? 'done' : (featured && m.id === featured.id ? 'now' : 'later');" in _fn("dayGroupsHtml")


def test_the_node_carries_no_apricot_button_the_dock_does():
    html = _fn("dayStripNodeHtml")
    assert "hero-action" not in html and "dock-primary" not in html
    assert "moveTickHtml(move)" in html  # the tick stays on the node
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


def test_the_coaching_card_is_gone_for_good():
    """It was a card on Now, then (2026-09-11) a one-time sheet at body
    level, and since 2026-09-18 neither: the Week 1 "Need a hand?" sheet
    (static/help-sheet.js) says the same things where they are needed."""
    assert "coach-card-slot" not in SHELL_HTML and "coach-card-slot" not in SHELL_JS
    assert "coach-sheet" not in SHELL_CSS and "coachCardHtml" not in SHELL_JS
    assert "plan-nudge-eyebrow\">A QUICK WORD" not in SHELL_JS
    assert '<script src="/static/help-sheet.js"></script>' in SHELL_HTML
    assert "#prefs-sheet,\n#tips-sheet" in SHELL_CSS


def test_the_dinner_suggestions_fold_behind_one_pick():
    assert 'class="shell-card needs-you-card is-folded urgency-' in SHELL_JS
    assert 'data-ny-unfold' in SHELL_JS
    assert ".needs-you-card.is-folded .ny-options { display: none; }" in SHELL_CSS


def test_the_design_system_says_today_has_a_dock_and_no_hero():
    # "Now" until 2026-09-17; the tab is Today now and the rules say so.
    assert "Today (called Now until 2026-09-17) got a dock on 2026-09-11" in DESIGN
    assert "Today has no hero" in DESIGN
