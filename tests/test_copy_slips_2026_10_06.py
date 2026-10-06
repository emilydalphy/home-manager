"""
Copy slips from the 2026-10-06 walkthrough: US spelling, plain grammar,
planner jargon kept out of swap reasons, a chat line that counts options
truthfully, and toasts that name what they confirm.
"""
from __future__ import annotations

from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SHELL_JS = (REPO / "static" / "shell.js").read_text(encoding="utf-8")
ONBOARDING = (REPO / "static" / "onboarding.html").read_text(encoding="utf-8")
PLAN_WEEK = (REPO / "static" / "plan-week.html").read_text(encoding="utf-8")
AGENT = (REPO / "app" / "agent.py").read_text(encoding="utf-8")
SWAP_OPTIONS = (REPO / "app" / "tools" / "swap_options.py").read_text(encoding="utf-8")
SWAP_IN_PLACE = (REPO / "app" / "tools" / "swap_in_place.py").read_text(encoding="utf-8")


def test_us_spelling_in_user_facing_lines():
    assert "tap once for a favorite, twice to skip it" in SHELL_JS
    assert "tap once for a favourite" not in SHELL_JS
    assert "you&rsquo;re traveling or gone" in PLAN_WEEK
    assert "travelling" not in PLAN_WEEK
    assert "a favorite named in intake.freeform" in AGENT


def test_day_picker_line_reads_plainly():
    assert "or to tap off a meal" not in ONBOARDING
    assert "or to turn off a meal you don&rsquo;t need planned" in ONBOARDING


def test_one_child_is_named_not_the_kids():
    assert "use their name (\"Leo's portion\"), never \"the kids' portions\"" in AGENT


def test_before_shop_only_says_ticked_when_something_was():
    # The line is a function of what was ticked when the pass opened.
    i = SHELL_JS.index("title: 'Need any of your regulars?'")
    block = SHELL_JS[i:i + 400]
    assert "line: function ()" in block
    assert "groceryState.bsPreTicked ?" in block
    assert "groceryState.bsPreTicked = bsTickedRegulars().length;" in SHELL_JS


def test_swap_reasons_say_real_minutes_and_plain_protein():
    for src in (SWAP_OPTIONS, SWAP_IN_PLACE):
        assert "Quicker than 45 minutes" in src
        assert "Not chicken or beef again this week" in src
    assert "Quicker than 45 minutes" in AGENT  # the chat card's reason field


def test_card_reports_how_many_options_really_show():
    from app.tools import proposals
    proposal = {
        "proposal_id": "p", "weekly_plan_id": 1, "week_start_date": "2026-10-05",
        "line": "", "status": "open", "applied": [], "created_at": 0,
        "rows": [{
            "date": "2026-10-06", "weekday": "Tuesday", "slot": "dinner", "action": "change",
            "current": None, "candidates": [{"meal_name": "Dal", "reason": "r", "minutes": 30}],
            "chosen": 0, "problem": None,
        }],
    }
    assert proposals.public_view(proposal)["rows"][0]["options_shown"] == 1
    assert "options_shown" in AGENT and "Never write 'three' unless three are there" in AGENT


def test_chat_save_toast_names_what_changed():
    # Chores' two ticks stay bare: Chores is paused (Emily, 2026-09-18).
    assert "toastSaved(data.actions.length === 1 && data.actions[0].change" in SHELL_JS
    assert "savedCount(data.actions.length, 'saved')" in SHELL_JS
    assert "!data.proposal) toastSaved();" not in SHELL_JS
