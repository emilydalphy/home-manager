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

# UPDATED 2026-09-18 (Loop Board board 01-welcome, Card 1): the separate
# "Nice to meet you" / purpose screen is gone — its line is now the hello
# screen's own intro-lead, so the tour is four screens, not five.
INTRO = ["intro-hello", "intro-help", "intro-talk", "intro-know"]


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


def test_the_four_intro_screens_come_before_the_household_step():
    m = re.search(r"const ALL_STEPS = (\[.*?\]);", ONBOARDING)
    assert m, "ALL_STEPS has moved"
    steps = json.loads(m.group(1).replace("'", '"'))
    # UPDATED 2026-10-05 (the main person): the household step is TWO
    # screens, "What's your name?" then "Who else lives with you?", so the
    # first question after the intro is the name. TRIPWIRE FIRED: this
    # file hard-codes the flow's own order, and the claim it is making --
    # the four intro screens come first, and the questions follow in the
    # storyboard's order -- is unchanged. The flow gained a step.
    assert steps[:5] == INTRO + ["your-name"], steps[:5]
    # The questions after them, since 2026-09-30 (the "How your week runs"
    # storyboard): who helps and never on the plate finish "Who's eating";
    # the week's shape comes before what you eat.
    # UPDATED 2026-10-06 (Onboarding regrouped, Emily's locked flow):
    # household -> schedule -> breakfast -> lunch -> dinner -> snacks -> the rest.
    assert steps[5:] == ["household", "helpers", "restrictions",
                         "meals-days", "shop-day", "prep",
                         "variety-breakfast", "lunch-needs", "variety-lunch",
                         "dinner-time", "variety-dinner", "snacks",
                         "eating-style", "wont-eat", "excited-about", "kit-repeats",
                         # Sharing with Claude, before the first week (2026-09-27),
                         # then setup's last answer, "Anything else I should know?"
                         # (2026-10-05) -- which is where the first week is asked
                         # for, and which needs the consent above it because it
                         # reads the note with a model call.
                         "ai-consent", "anything-else", "reveal"]


def test_each_intro_screen_is_in_the_markup_in_that_order():
    positions = [ONBOARDING.index(f'<div id="step-{k}"') for k in INTRO + ["your-name", "household"]]
    assert positions == sorted(positions), "the intro screens are out of order in the markup"


def test_the_page_starts_on_hello_not_on_the_first_question():
    # UPDATED 2026-10-05: which step a reload lands on is ONE named thing
    # now (firstQuestionStep, read by the popstate handler and the finish
    # too) rather than a literal written down three times -- the household
    # split left a hard-coded 'household' in one of the three. The claim
    # is unchanged: a FIRST visit starts on hello, not on a question.
    assert "showStep(reloadedMidSetup ? firstQuestionStep() : 'intro-hello'" in ONBOARDING
    # And the one thing it resolves to is still the first question of the
    # flow rather than anything else — derived, not written down.
    assert "return ALL_STEPS[INTRO_STEPS.length];" in ONBOARDING


# ---------- the words are Emily's ----------


APPROVED = {
    "intro-hello": [
        "Hi, I’m Pomona.",
        # Card 1 (2026-09-18): the old "Nice to meet you" purpose screen
        # folded into this one — the purpose line now sits right under the
        # title, in the same breath as the hello.
        "I’m your home manager. My job is to make your life easier by taking "
        "on the mental load of planning your weekly meals and helping you "
        "get food on the table.",
        "Nice to meet you",
    ],
    "intro-help": [
        "Here’s what I help you with.",
        "Planning",
        "No more what’s-for-dinner at five o’clock, no more last-minute takeout. "
        "I’ll make the plan, and I’ll make it easy to stick to.",
        "Shopping",
        "One list, built from the plan. Less time in the store, less money, "
        "less food in the bin.",
        "Cooking",
        "Meals you’ll actually want to eat. Shaped to your tastes and your "
        "goals, and they don’t get boring.",
        "Continue",
    ],
    "intro-talk": [
        # Card 3 (2026-09-18): "You can just talk to me" became "Here's how
        # it works" — a five-step walkthrough of the whole loop, replacing
        # the three rows about talking to Pomona.
        "Here’s how it works.",
        "Set up your household",
        "Share your preferences",
        "I draft your week.",
        "You tweak it until it’s right.",
        "Your grocery list builds itself.",
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


def test_the_first_screen_says_hello_and_its_purpose_and_nothing_more():
    """
    UPDATED 2026-09-18 (Card 1): the hello screen used to say nothing but
    hello, with the purpose ("this is my purpose") on its own screen right
    after. That screen is gone — the purpose line now sits under the title
    here, in the same breath, and this is what replaces the old
    one-line-and-a-button assertion.
    """
    assert _text("intro-hello") == (
        "Hi, I’m Pomona. I’m your home manager. My job is to make your life "
        "easier by taking on the mental load of planning your weekly meals "
        "and helping you get food on the table. Nice to meet you"
    )


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
    # UPDATED 2026-10-05: the first question is "What's your name?" since
    # the household step split in two. The claim is unchanged — each
    # button goes exactly one screen forward and the last lands on the
    # first question — and it is asked of the flow rather than named, so
    # a later split can't make this assert something shorter.
    assert out["landed"] == INTRO[1:] + ["your-name"]
    # One history entry per screen, so the phone's back gesture walks the
    # intro backwards the same way the crumb does.
    assert out["depth"] == len(INTRO) + 1


@_needs_node
def test_the_progress_strip_is_hidden_during_the_intro_and_starts_at_the_first_question():
    """
    UPDATED 2026-09-30: the questions' progress cue is the step eyebrow
    ("2 of 4 · How your week runs"), drawn into each question's own
    .q-eyebrow by renderProgress — so an intro screen, which has no eyebrow
    and no section, gets nothing drawn, and a question names its stop.
    """
    out = _run(_nav_harness() + """
const seen = {};
%s.forEach(function (k) {
  const eyebrow = makeEl('p'); eyebrow._classes.add('q-eyebrow');
  ELS['step-' + k].appendChild(eyebrow);
  showStep(k);
  seen[k] = eyebrow.textContent;
});
console.log(JSON.stringify(seen));
""" % json.dumps(INTRO + ["your-name", "household", "meals-days", "eating-style", "ai-consent"]))
    for k in INTRO:
        assert out[k] == "", f"a question eyebrow was drawn during {k}"
    # UPDATED 2026-10-05: both halves of the split household step are in
    # the first stop, so both draw its eyebrow. The claim is unchanged.
    # UPDATED 2026-10-06 (Onboarding regrouped): the eyebrow is the
    # section's name.
    assert out["your-name"] == "Your household"
    assert out["household"] == "Your household"
    assert out["meals-days"] == "Your schedule"
    assert out["eating-style"] == "The rest"
    assert out["ai-consent"] == "The rest"


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
['intro-help', 'household', 'intro-know', 'prep'].forEach(function (k) {
  showStep(k); seen.push([k, body.classList.contains('intro-active')]);
});
console.log(JSON.stringify(seen));
""")
    # 'rhythm-1' used to be the fourth step here; it left the flow on
    # 2026-09-11 and resolveStep sends an unknown key to the first screen.
    # 'intro-purpose' was the third step here until Card 1 (2026-09-18)
    # folded it into intro-hello, leaving it with no step of its own.
    assert out == [["intro-help", True], ["household", False], ["intro-know", True], ["prep", False]]


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
        assert 'stroke="currentColor"' in markup
        assert not re.search(r"[\U0001F300-\U0001FAFF]", markup), f"{step} uses an emoji"


def test_nothing_tappable_is_under_44px():
    css = _intro_css()
    for cls in (".intro-next", ".intro-chip"):
        block = css[css.index(cls + " {"):]
        block = block[: block.index("}")]
        m = re.search(r"min-height:\s*(\d+)px", block)
        assert m and int(m.group(1)) >= 44, f"{cls} is under 44px"
