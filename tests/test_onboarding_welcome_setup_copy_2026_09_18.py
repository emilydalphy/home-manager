"""
The six 2026-09-18 welcome/setup copy cards (branch
onboarding-welcome-setup-copy), each pinned once here beyond the existing
suites those other files already carry:

  Card 1 — the hello screen's purpose line, and the four-dot pager (down
           from five) now that the separate "Nice to meet you" screen and
           its own pager position are gone.
  Card 3 — "Here's how it works": five numbered rows, not the old
           "You can just talk to me" rows.
  Card 5 — dinner-time's Skip sits under Continue, styled as a quiet
           italic line rather than the bold .skip-link.
  Card 6 — the loading screen's "what I'm using" card: one row per fact,
           built from revealSetupFacts, hidden the moment revealShowDays()
           runs.

(Card 2's copy and Card 4's prep-day chips are pinned in
tests/test_onboarding_welcome_flow.py and tests/test_onboarding_prep_chips.py
respectively.)
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import nodeharness

_HERE = Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location(
    "test_onboarding_go_back", _HERE / "test_onboarding_go_back.py"
)
_go_back = importlib.util.module_from_spec(_spec)
sys.modules["test_onboarding_go_back"] = _go_back
_spec.loader.exec_module(_go_back)
_needs_node, _fn, _const = _go_back._needs_node, _go_back._fn, _go_back._const
_step_markup = _go_back._step_markup

ONBOARDING_PATH = _HERE.parent / "static" / "onboarding.html"
ONBOARDING = ONBOARDING_PATH.read_text()


def _run(script: str):
    res = nodeharness.run_node(script, timeout=30)
    assert res.returncode == 0, f"node failed: {res.stderr}"
    return json.loads(res.stdout)


# ---------- Card 1: the hello screen's pager and purpose line ----------


def test_every_intro_pager_now_has_four_dots_not_five():
    for step in ("intro-hello", "intro-help", "intro-talk", "intro-know"):
        markup = _step_markup(f"step-{step}")
        pager = markup[markup.index('class="intro-pager"'):]
        pager = pager[: pager.index("</div>") + len("</div>")]
        assert pager.count("<span") == 4, f"{step}'s pager has {pager.count('<span')} dots, not 4"
        assert pager.count('class="is-on"') == 1, f"{step}'s pager doesn't light exactly one dot"


def test_the_hello_screen_carries_its_purpose_line_and_keeps_the_glyph():
    markup = _step_markup("step-intro-hello")
    assert 'class="intro-glyph"' in markup, "the real Pomona mark is gone from the hello screen"
    assert 'class="intro-lead"' in markup
    assert (
        "I&rsquo;m your home manager. My job is to make your life easier by "
        "taking on the mental load of planning your weekly meals and helping "
        "you get food on the table." in markup
    )
    assert markup.index("is-hello") < markup.index('class="intro-lead"'), (
        "the purpose line renders above the title"
    )


def test_intro_purpose_has_no_step_left_at_all():
    assert 'id="step-intro-purpose"' not in ONBOARDING
    assert "intro-purpose" not in ONBOARDING


# ---------- Card 3: "Here's how it works" ----------


def test_intro_talk_is_now_how_it_works_with_five_numbered_rows():
    markup = _step_markup("step-intro-talk")
    assert "Here&rsquo;s how it works." in markup
    assert "You can just talk to me." not in ONBOARDING
    rows = [
        "Set up your household", "Share your preferences", "I draft your week.",
        "You tweak it until it&rsquo;s right.", "Your grocery list builds itself.",
    ]
    for i, row in enumerate(rows, start=1):
        assert row in markup, f"step {i} ({row!r}) is missing"
    assert markup.count('class="intro-step-num"') == 5
    assert 'id="intro-talk-next"' in markup, "the button keeps its internal id"


def test_the_step_name_map_calls_it_how_it_works():
    assert "'intro-talk': 'How it works'" in ONBOARDING
    assert "'intro-talk': 'How we talk'" not in ONBOARDING


# ---------- Card 5: dinner-time's Skip under Continue ----------


def test_dinner_times_skip_sits_under_continue_and_reads_quiet():
    markup = _step_markup("step-dinner-time")
    foot = markup[markup.index('class="q-foot"'):]
    assert foot.index('id="dinner-time-next"') < foot.index('id="dinner-time-skip"'), (
        "Skip still sits above Continue on the dinner-time screen"
    )
    skip_tag = foot[foot.index('id="dinner-time-skip"') - 60: foot.index('id="dinner-time-skip"')]
    assert "q-skip-quiet" in skip_tag
    assert "skip-link" not in skip_tag


def test_q_skip_quiet_is_italic_and_44px_and_full_width():
    css = ONBOARDING[ONBOARDING.index("<style>"): ONBOARDING.index("</style>")]
    rule = css[css.index(".q-skip-quiet {"): ]
    rule = rule[: rule.index("}")]
    assert "font-style: italic" in rule
    assert "min-height: 44px" in rule
    assert "width: 100%" in rule


# ---------- Card 6: the loading screen's "what I'm using" card ----------


def test_the_reveal_carries_the_using_card_under_the_status_hidden_by_default():
    step = _step_markup("step-reveal")
    assert 'id="reveal-using"' in step and 'id="reveal-using-note"' in step
    assert step.index('id="reveal-status"') < step.index('id="reveal-using"'), (
        "the using card doesn't sit under the status"
    )
    using_tag_start = step.index('<div class="reveal-using"')
    using_tag = step[using_tag_start: step.index(">", using_tag_start) + 1]
    assert "hidden" in using_tag
    note_tag_start = step.index('<p class="reveal-using-note"')
    note_tag = step[note_tag_start: step.index(">", note_tag_start) + 1]
    assert "hidden" in note_tag
    assert "Not quite right? You can change any of it later." in step


@_needs_node
def _run_using_js(body: str):
    harness = "\n".join([
        _const("REVEAL_DINNER_WINDOW_FACT"),
        _fn("revealTitleCase"),
        _fn("revealJoinWords"),
        _fn("escapeHtmlLocal"),
        _const("REVEAL_USING_ICONS"),
        _fn("revealUsingRowHtml"),
        _fn("revealUsingRows"),
        body,
    ])
    return _run(harness)


@_needs_node
def test_using_rows_cover_people_avoid_dinner_and_prep_in_order():
    facts = {
        "people": 2, "avoidItems": [], "dinnerWindow": "6_8",
        "prepDays": ["sunday", "monday"],
    }
    out = _run_using_js(f"console.log(JSON.stringify(revealUsingRows({json.dumps(facts)})))")
    assert out.index(">2 people<") < out.index("No allergies")
    assert out.index("No allergies") < out.index("Dinner between 6 and 8")
    assert out.index("Dinner between 6 and 8") < out.index("Prep on Sunday and Monday")


@_needs_node
def test_a_solo_household_with_avoids_and_no_prep_reads_back_as_itself():
    facts = {
        "people": 1, "avoidItems": ["shrimp", "peanuts"], "dinnerWindow": "all_over",
        "prepDays": [],
    }
    out = _run_using_js(f"console.log(JSON.stringify(revealUsingRows({json.dumps(facts)})))")
    assert ">1 person<" in out
    assert "No shrimp, no peanuts" in out
    # 'all_over' isn't specific enough to read back — no dinner row at all.
    assert "Dinner" not in out
    assert "No prep days" in out


def test_reveal_setup_facts_gathers_avoid_items_from_allergies_and_wont_eat():
    body = _fn("revealSetupFacts")
    assert "avoidItems" in body
    assert "answers.wont_eat" in body


@_needs_node
def test_reveal_show_days_hides_the_using_card_and_its_note():
    harness = "\n".join([
        _go_back._DOM_STUB,
        "el('reveal-days'); el('reveal-title'); el('reveal-using'); el('reveal-using-note');",
        "document.getElementById('reveal-days').hidden = true;",
        "document.getElementById('reveal-using').hidden = false;",
        "document.getElementById('reveal-using-note').hidden = false;",
        _const("REVEAL_TITLE_READY"),
        _fn("revealShowDays"),
        """
revealShowDays();
console.log(JSON.stringify({
  daysHidden: document.getElementById('reveal-days').hidden,
  usingHidden: document.getElementById('reveal-using').hidden,
  noteHidden: document.getElementById('reveal-using-note').hidden,
}));
""",
    ])
    out = _run(harness)
    assert out == {"daysHidden": False, "usingHidden": True, "noteHidden": True}


@_needs_node
def test_a_failed_generation_also_hides_the_using_card_and_its_note():
    """
    Coordinator follow-up (2026-09-18): revealShowDays() was the only place
    that hid the using card — a generation that FAILS never reaches it, so
    the card (and its "not quite right" note) used to sit on screen right
    alongside the failure receipt. renderRevealFailedReceipt is the
    function that owns rendering a failure, so it owns hiding them too, the
    same way revealShowDays() owns hiding them on success.
    """
    harness = "\n".join([
        _go_back._DOM_STUB,
        "el('reveal-receipt'); el('reveal-using'); el('reveal-using-note');",
        "document.getElementById('reveal-using').hidden = false;",
        "document.getElementById('reveal-using-note').hidden = false;",
        _const("REVEAL_RECEIPT_EYEBROW"),
        _const("REVEAL_FAILED_TITLE"),
        _const("REVEAL_FAILED_LINE"),
        _fn("escapeHtmlLocal"),
        _fn("revealReceiptLinesHtml"),
        _fn("renderRevealFailedReceipt"),
        """
renderRevealFailedReceipt();
console.log(JSON.stringify({
  receiptHidden: document.getElementById('reveal-receipt').hidden,
  usingHidden: document.getElementById('reveal-using').hidden,
  noteHidden: document.getElementById('reveal-using-note').hidden,
}));
""",
    ])
    out = _run(harness)
    assert out == {"receiptHidden": False, "usingHidden": True, "noteHidden": True}


def test_generate_first_plan_and_reveal_fills_the_using_card_every_attempt():
    body = _fn("generateFirstPlanAndReveal")
    assert "renderRevealUsingFacts(answers)" in body
    # Filled before the request goes out, same moment renderRevealNumber(null)
    # resets the last attempt's number — not only on success, so a retry
    # after a failure still shows what it's building from.
    assert body.index("renderRevealUsingFacts(answers)") < body.index("await streamFirstPlan")
