"""
The welcome flow (Emily, 2026-09-10 — Loop Board "Onboarding: the welcome
flow — five screens where Pomona introduces herself before the first
question").

Until this branch a new household signed in and landed straight on "Who am
I cooking for, exactly?" — Pomona never said hello. Five screens now sit
between sign-in and that question: hello, what I'm for, what I help with,
how we'll talk, and let's get to know each other. Her brief, in her words:
"Hi I'm Pomona, I'm your home manager… this is my purpose… this is what I
can do… this is how you can interact with me… this is what we're going to
walk through so I can get to know you." The copy below was worked through
with her line by line and is hers.

What this file pins:

- the five screens exist, in that order, ahead of the household step, and
  the questions after them are untouched;
- every line of copy is exactly what she approved — a change is a Loop
  Board decision first (DESIGN_SYSTEM §8), not a drive-by;
- there is no skip ("it's a low time commitment and they should go through
  it");
- each button goes one screen forward and the last lands on the first
  question; the intro hides the questions' progress strip and the strip
  starts where it always did once the intro is over;
- a reload lands on the household step, not back on "Hi" — that one lives
  in tests/test_onboarding_go_back.py beside the other reload rules;
- the new styling goes through tokens only (DESIGN_SYSTEM rule 9).

The navigation half runs the page's own functions under node, through the
harness tests/test_onboarding_go_back.py already lifts; the copy half reads
the markup, because the words are the point.
"""
from __future__ import annotations

import importlib.util
import json
import re
import sys
from pathlib import Path

import pytest

ONBOARDING_PATH = Path(__file__).resolve().parent.parent / "static" / "onboarding.html"
ONBOARDING = ONBOARDING_PATH.read_text()

# tests/ is not a package, so the sibling harness is loaded by path.
_spec = importlib.util.spec_from_file_location(
    "_go_back", Path(__file__).resolve().parent / "test_onboarding_go_back.py"
)
_go_back = importlib.util.module_from_spec(_spec)
sys.modules["_go_back"] = _go_back
_spec.loader.exec_module(_go_back)
_nav_harness, _run, _needs_node = _go_back._nav_harness, _go_back._run, _go_back._needs_node
_step_markup = _go_back._step_markup

INTRO = ["intro-hello", "intro-purpose", "intro-help", "intro-talk", "intro-know"]


def _unescape(markup: str) -> str:
    """The entities the page writes, back into the characters Emily typed."""
    return (markup.replace("&rsquo;", "’").replace("&ldquo;", "“")
            .replace("&rdquo;", "”").replace("&mdash;", "—")
            .replace("&hellip;", "…"))


def _text(step: str) -> str:
    """The step's visible words, tags stripped, whitespace folded."""
    raw = re.sub(r"<!--.*?-->", "", _step_markup(f"step-{step}"), flags=re.S)
    raw = re.sub(r"<svg.*?</svg>", "", raw, flags=re.S)
    return re.sub(r"\s+", " ", _unescape(re.sub(r"<[^>]+>", " ", raw))).strip()


# ---------- the five screens, in order, ahead of the questions ----------


def test_the_five_intro_screens_come_before_the_household_step():
    m = re.search(r"const ALL_STEPS = (\[.*?\]);", ONBOARDING)
    assert m, "ALL_STEPS has moved"
    steps = json.loads(m.group(1).replace("'", '"'))
    assert steps[:6] == INTRO + ["household"], steps[:6]
    # The questions after them, since 2026-09-11 (Build 6, Emily's decision
    # G — setup asks only what changes the plan).
    assert steps[6:] == ["meals", "restrictions", "eating-style", "wont-eat",
                         "excited-about", "leftovers", "prep", "dinner-time", "kit-repeats", "reveal"]


def test_each_intro_screen_is_in_the_markup_in_that_order():
    positions = [ONBOARDING.index(f'<div id="step-{k}"') for k in INTRO + ["household"]]
    assert positions == sorted(positions), "the intro screens are out of order in the markup"


def test_the_page_starts_on_hello_not_on_the_first_question():
    assert "showStep(reloadedMidSetup ? 'household' : 'intro-hello'" in ONBOARDING


# ---------- the words are Emily's ----------


APPROVED = {
    "intro-hello": [
        "Hi, I’m Pomona.",
        "Nice to meet you",
    ],
    "intro-purpose": [
        "Nice to meet you.",
        "I’m your home manager. My whole job is to take the mental load off you "
        "— the planning, the remembering, the what-are-we-eating-tonight "
        "— so it isn’t all on you.",
        "Continue",
    ],
    "intro-help": [
        "Here’s what I help you with.",
        "The plan", "A week of meals around your people and your schedule.",
        "The shopping", "One grocery list, sorted by where you shop.",
        "The cooking", "Prep ahead, then dinner one step at a time.",
        "And I remember. Every answer and every correction makes the next week fit better.",
        "Continue",
    ],
    "intro-talk": [
        "You can just talk to me.",
        "The everyday stuff is a tap",
        "Approving the week, ticking off groceries, starting a recipe — one button each.",
        "Everything else, just tell me",
        "Say it the way you’d say it out loud. “Jamie’s out Thursday.” "
        "“We already have rice.” I’ll take it from there.",
        "If I get it wrong, say so",
        "Correct me right where it happens, and I’ll remember for next time.",
        # The demo chips and ask bar left this screen on 2026-09-11: they
        # pushed Continue off a 375×812 phone, and the chat is an icon now.
        "Continue",
    ],
    "intro-know": [
        "Let’s get to know each other.",
        "A few questions, about five minutes, mostly tapping. Nothing’s locked in "
        "— you can change any answer later.",
        "Who’s eating", "How your week runs", "What you eat", "Your first week",
        "Let’s go",
    ],
}


@pytest.mark.parametrize("step,lines", list(APPROVED.items()), ids=list(APPROVED))
def test_every_line_on_each_screen_is_the_approved_one(step, lines):
    text = _text(step)
    for line in lines:
        assert line in text, f"{step}: {line!r} is not on the screen (or not as approved)"


def test_the_first_screen_says_nothing_but_hello():
    """One line and one button. Nothing narrated, nothing explained yet."""
    assert _text("intro-hello") == "Hi, I’m Pomona. Nice to meet you"


def test_no_screen_offers_a_skip():
    for step in INTRO:
        assert "skip" not in _step_markup(f"step-{step}").lower(), f"{step} offers a skip"


def test_the_intro_carries_no_italic_accent_line():
    """
    DESIGN_SYSTEM §3: the Newsreader italic is for a line that carries a
    real fact, at most one per screen and most screens none. The intro's
    words are all the fact there is; the mockups Emily approved carry no
    italic, so the page must not grow one.
    """
    for step in INTRO:
        assert "font-accent" not in _step_markup(f"step-{step}")
        assert "rhythm-italic" not in _step_markup(f"step-{step}")


# ---------- the buttons walk forward, one screen at a time ----------


@_needs_node
def test_each_button_goes_one_screen_forward_and_the_last_lands_on_the_first_question():
    out = _run(_nav_harness() + """
// The page wires each intro button by id; the harness restates that wiring
// so the buttons run the page's own showStep.
const INTRO = %s;
INTRO.forEach(function (key) {
  const b = makeEl('button'); ELS[key + '-next'] = b;
  b.onclick = function () { const flow = stepFlow(); showStep(flow[flow.indexOf(key) + 1]); };
});
const landed = [];
INTRO.forEach(function (key) { ELS[key + '-next'].click(); landed.push(currentStep); });
console.log(JSON.stringify({ landed: landed, depth: depth() }));
""" % json.dumps(INTRO))
    assert out["landed"] == INTRO[1:] + ["household"]
    # One history entry per screen, so the phone's back gesture walks the
    # intro backwards the same way the crumb does.
    assert out["depth"] == len(INTRO) + 1


@_needs_node
def test_the_progress_strip_is_hidden_during_the_intro_and_starts_at_the_first_question():
    out = _run(_nav_harness() + """
const seen = {};
%s.forEach(function (k) {
  showStep(k);
  seen[k] = { hidden: ELS['progress'].hidden, dots: ELS['progress']._children.length };
});
console.log(JSON.stringify(seen));
""" % json.dumps(INTRO + ["household", "rhythm-1"]))
    for k in INTRO:
        assert out[k]["hidden"] is True, f"the questions' progress strip showed during {k}"
    assert out["household"]["hidden"] is False
    # Ten dots for ten questions — the intro screens don't count as dots.
    assert out["household"]["dots"] == 10
    assert out["rhythm-1"]["dots"] == 10


@_needs_node
def test_the_page_paints_spruce_during_the_intro_and_ivory_from_the_first_question():
    """
    body.intro-active is the whole of the swap, and the sign-in page's own
    spruce is what it swaps to. Toggled from showStep so going BACK into the
    intro from the household step turns the page dark again.
    """
    out = _run(_nav_harness() + """
const body = makeEl('body');
document.body = body;
const seen = [];
['intro-purpose', 'household', 'intro-know', 'rhythm-1'].forEach(function (k) {
  showStep(k); seen.push([k, body.classList.contains('intro-active')]);
});
console.log(JSON.stringify(seen));
""")
    assert out == [["intro-purpose", True], ["household", False], ["intro-know", True], ["rhythm-1", False]]


# ---------- the styling goes through tokens ----------


def _intro_css() -> str:
    start = ONBOARDING.index("The welcome flow (Loop Board")
    return ONBOARDING[start: ONBOARDING.index("</style>", start)]


def test_every_colour_in_the_intro_css_is_a_token():
    css = _intro_css()
    # The one literal allowed is the apricot glow, which static/login.html
    # paints the same way and which has no token (it's an alpha over
    # apricot, not a colour of its own).
    literals = [h for h in re.findall(r"#[0-9a-fA-F]{3,6}\b", css)]
    assert literals == [], f"literal colours in the intro CSS: {literals}"
    rgbas = re.findall(r"rgba\([^)]*\)", css)
    assert all("224, 145, 92" in r for r in rgbas), (
        f"an rgba() that isn't the apricot glow: {rgbas}"
    )
    for token in ("--spruce", "--spruce-raised", "--ivory-ink", "--ivory-ink-muted",
                  "--apricot", "--apricot-light", "--on-accent-ink", "--radius-action",
                  "--shadow-action", "--font-display", "--font-body"):
        assert f"var({token})" in css, f"{token} isn't used"


def test_icons_are_stroke_svg_not_emoji():
    for step in INTRO:
        markup = _step_markup(f"step-{step}")
        assert 'stroke="currentColor"' in markup or step == "intro-purpose"
        assert not re.search(r"[\U0001F300-\U0001FAFF]", markup), f"{step} uses an emoji"


def test_nothing_tappable_is_under_44px():
    css = _intro_css()
    for cls in (".intro-next", ".intro-chip"):
        block = css[css.index(cls + " {"):]
        block = block[: block.index("}")]
        m = re.search(r"min-height:\s*(\d+)px", block)
        assert m and int(m.group(1)) >= 44, f"{cls} is under 44px"
