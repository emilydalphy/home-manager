"""
The setup question screens in the welcome flow's look (Loop Board "Setup
question screens in the welcome flow's look: one idea per screen, big type,
lots of air" — Emily, 2026-09-10; built 2026-09-12).

The 2026-09-11 audit found the question screens breaking from the five
welcome screens abruptly: three progress indicators at once (a ten-segment
strip at the top, a "1 of 4 · Who's eating" eyebrow, and the crumb), a
darker apricot on Continue than the welcome screens' button, an × on every
name row including the empty ones, and controls hugging the title. This
file pins what replaced each of those, in the shape a reviewer can check:

- one progress cue per question screen besides the crumb — since
  2026-09-30 the step eyebrow ("2 of 4 · How your week runs"), where it
  was the four-dot pager from 2026-09-12; never a strip as well;
- the one button on every question is the welcome screens' button —
  .btn-primary, --apricot with --on-accent-ink — not a fill of its own;
- a name row only carries its × once it has a name;
- the question screens' CSS uses tokens only, no italics, nothing tappable
  under 44px, and the words the 2026-09-11 cleanse left are still the words.

Style follows tests/test_onboarding_go_back.py: where a behaviour can be run
it is run under node against that file's DOM stub; the rest is read off the
markup and CSS the browser is handed.
"""
from __future__ import annotations

import importlib.util
import json
import re
import sys
from pathlib import Path

import pytest

_HERE = Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location(
    "test_onboarding_go_back", _HERE / "test_onboarding_go_back.py"
)
_go_back = importlib.util.module_from_spec(_spec)
sys.modules["test_onboarding_go_back"] = _go_back
_spec.loader.exec_module(_go_back)
_nav_harness, _run, _needs_node = _go_back._nav_harness, _go_back._run, _go_back._needs_node
_fn, _step_markup = _go_back._fn, _go_back._step_markup

ONBOARDING = (_HERE.parent / "static" / "onboarding.html").read_text()
QUESTION_STEPS = _go_back.QUESTION_STEPS


def _css() -> str:
    return ONBOARDING[ONBOARDING.index("<style>"): ONBOARDING.index("</style>")]


def _question_css() -> str:
    """The question screens' block of the stylesheet, up to the welcome flow's."""
    css = _css()
    return css[: css.index("The welcome flow (Loop Board")]


def _rule(css: str, selector: str) -> str:
    """The body of the first rule whose selector list starts with `selector`."""
    i = css.index(selector + " {")
    return css[i: css.index("}", i)]


# ---------- one progress cue ----------
# UPDATED 2026-09-30: Emily's "How your week runs" mockups put the step
# eyebrow ("2 of 4 · How your week runs") back in place of the four-dot
# pager — still one cue besides the crumb, never two.


@pytest.mark.parametrize("step", QUESTION_STEPS)
def test_each_question_screen_carries_exactly_one_eyebrow_and_no_other_cue(step):
    markup = _step_markup(f"step-{step}")
    assert markup.count('class="q-eyebrow"') == 1, f"{step} has no step eyebrow"
    assert 'class="q-pager"' not in markup, f"{step} still carries the dot pager as well"
    # The crumb is the other way back — and there is exactly one of it.
    assert markup.count(f'data-step-back="{step}"') == 1


def test_the_ten_segment_strip_at_the_top_is_gone():
    assert 'id="progress"' not in ONBOARDING
    assert ".progress {" not in _css() and ".dot {" not in _css()


def test_the_eyebrow_sits_between_the_crumb_and_the_question():
    for step in QUESTION_STEPS:
        markup = _step_markup(f"step-{step}")
        assert markup.index("data-step-back") < markup.index('class="q-eyebrow"') < markup.index("<h1"), (
            f"{step}'s eyebrow is not between its crumb and its question"
        )


def test_the_eyebrow_is_the_systems_eyebrow():
    rule = _rule(_question_css(), ".q-eyebrow")
    assert "font-size: 10px" in rule and "font-weight: 800" in rule
    assert "text-transform: uppercase" in rule
    assert "var(--ink-muted)" in rule


# ---------- one apricot, the welcome screens' button ----------


@pytest.mark.parametrize("step", QUESTION_STEPS)
def test_the_one_button_on_each_question_is_btn_primary(step):
    markup = _step_markup(f"step-{step}")
    buttons = re.findall(r'<button class="([^"]*)"[^>]*id="[\w-]+-next"', markup)
    assert len(buttons) == 1, f"{step} has {len(buttons)} next buttons"
    assert "btn-primary" in buttons[0].split(), f"{step}'s button is not .btn-primary"
    assert "q-next" in buttons[0].split()


def test_the_question_button_takes_its_fill_from_btn_primary_not_a_darker_apricot():
    """
    Continue used to be `.next-btn` with its own fill, and theme.css's
    `.btn-primary` hover swaps to --apricot-deep — so the questions carried
    a darker apricot than the welcome screens. The question CSS now sets no
    fill of its own, so the button is exactly the welcome screens' button.
    """
    css = _question_css()
    assert ".next-btn" not in css
    next_rule = _rule(css, ".q-next")
    assert "background" not in next_rule, ".q-next sets its own fill — it must inherit .btn-primary's"
    assert "background: var(--apricot-deep)" not in css, "a question control paints the darker apricot"
    # theme.css is the source of the fill: apricot with dark ink (hard rule 1).
    theme = (_HERE.parent / "static" / "theme.css").read_text()
    primary = theme[theme.index(".btn-primary, button.btn-primary {"):]
    primary = primary[: primary.index("}")]
    assert "background: var(--apricot);" in primary
    assert "color: var(--on-accent-ink);" in primary


def test_no_question_screen_carries_a_second_fill():
    """One apricot per screen (hard rule 5): the add-person row is an
    outline, the skip lines are text, the chips are spruce when chosen."""
    css = _question_css()
    assert "background: transparent" in _rule(css, ".add-btn")
    assert "background" not in _rule(css, ".skip-link")
    assert "var(--spruce)" in _rule(css, ".chip.active, .rhythm-chip.active")


# ---------- no × on an empty name row ----------


def _member_row_harness() -> str:
    return "\n".join([
        _go_back._DOM_STUB,
        "ELS['members'] = makeEl('div');",
        "const membersDiv = document.getElementById('members');",
        "function escapeHtmlLocal(s) { return String(s == null ? '' : s); }",
        "function pruneMemberKeyedAnswers() {}",
        "function renderMemberAgeChips() {}",
        _fn("addMemberRow"),
    ])


@_needs_node
def test_a_name_row_only_shows_its_remove_control_once_it_has_a_name():
    out = _run(_member_row_harness() + """
addMemberRow('', 'adult');
addMemberRow('', 'adult');
// The stub's innerHTML reads a template's tags back flat (and reads no
// value attribute), so the rows and their inputs are matched side by side
// rather than nested, and a name arrives by typing.
const rows = membersDiv.querySelectorAll('.row');
const inputs = membersDiv.querySelectorAll('.member-name');
function type(i, text) {
  inputs[i].value = text;
  (inputs[i]._listeners.input || []).forEach(function (fn) { fn(); });
}
const before = rows.map(function (r) { return r._classes.has('is-empty'); });
// Type into the first, then blank it again; whitespace is not a name.
type(0, 'Sam');
const named = rows.map(function (r) { return r._classes.has('is-empty'); });
type(0, '   ');
const after = rows.map(function (r) { return r._classes.has('is-empty'); });
console.log(JSON.stringify({ before: before, named: named, after: after }));
""")
    assert out["before"] == [True, True], "a fresh row shows its × before it has a name"
    assert out["named"] == [False, True]
    assert out["after"] == [True, True]


def test_the_empty_row_rule_hides_the_control_and_the_control_is_44px():
    css = _question_css()
    assert "display: none" in _rule(css, ".row.is-empty .remove-btn")
    remove = _rule(css, ".remove-btn")
    assert "width: 44px" in remove and "height: 44px" in remove
    # A stroke icon, not a text glyph (hard rule 7).
    assert 'stroke="currentColor"' in _fn("addMemberRow")
    assert "&times;" not in _fn("addMemberRow")


def test_add_person_is_a_quiet_full_width_outline_row():
    add = _rule(_question_css(), ".add-btn")
    assert "width: 100%" in add
    assert "border: 1.5px solid var(--ink-strong)" in add
    assert "min-height: 50px" in add


# ---------- controls with air ----------


def test_the_question_is_display_face_with_air_above_and_below():
    css = _question_css()
    title = _rule(css, ".q-title")
    assert "var(--font-display)" in title and "font-weight: 700" in title
    size = int(re.search(r"font-size:\s*(\d+)px", title).group(1))
    assert 31 <= size <= 34
    assert "text-wrap: balance" in title
    assert "padding-top: 64px" in _rule(css, ".q-body")
    line = _rule(css, ".q-line")
    assert "font-size: 17px" in line and "var(--ink-secondary)" in line
    gap = int(re.search(r"margin-top:\s*(\d+)px", _rule(css, ".q-controls")).group(1))
    assert gap >= 28


def test_chips_are_at_least_44px_tall_in_wrapping_rows_with_ten_to_twelve_px_gaps():
    css = _question_css()
    chip = _rule(css, ".chip, .rhythm-chip")
    assert int(re.search(r"min-height:\s*(\d+)px", chip).group(1)) >= 44
    row = _rule(css, ".chip-row, .rhythm-chip-row")
    assert "flex-wrap: wrap" in row
    assert 10 <= int(re.search(r"gap:\s*(\d+)px", row).group(1)) <= 12
    # The × inside a typed chip is its own 44px target.
    x = _rule(css, ".chip.removable button")
    assert "width: 44px" in x and "height: 44px" in x


@pytest.mark.parametrize("step, box_id", [
    ("step-eating-style", "eating-style"),
    ("step-wont-eat", "wont-eat-input"),
    ("step-excited-about", "excited-custom"),
])
def test_a_typed_answer_box_carries_its_question_as_the_placeholder(step, box_id):
    """§8.6: no form label over a box. The words stay for a screen reader
    (.sr-only) and stay on the screen as the placeholder."""
    markup = _step_markup(step)
    assert 'class="field-label"' not in markup
    assert f'<label class="sr-only" for="{box_id}">Anything else?</label>' in markup
    assert re.search(rf'id="{box_id}"[^>]*placeholder="Anything else\?"', markup)
    assert ".sr-only {" in _question_css()


def test_the_dinner_time_line_sits_under_the_question_not_under_the_chips():
    markup = _step_markup("step-dinner-time")
    assert markup.index("So I can tell you when to start cooking.") < markup.index('id="rhythm-dinner-window-chips"')


# ---------- the words are untouched ----------


@pytest.mark.parametrize("step, words", [
    # UPDATED 2026-10-05 (the main person): the household step is TWO
    # screens, and the TITLE is the one piece of copy that changed on the
    # second one -- Emily confirmed on 2026-10-04 that it otherwise reuses
    # "Who are we planning for?"'s screen exactly. So the line under it is
    # still verbatim, and "+ Add person" is "+ Add someone", which the card
    # asks for by name. This table's claim -- the 2026-09-11 copy cleanse
    # is still the copy -- is unchanged; the words moved because Emily
    # moved them.
    ("step-your-name", [
        "What&rsquo;s your name?",
        "You&rsquo;ll be the main person for the home. You can add others next.",
    ]),
    ("step-household", ["Who else lives with you?", "Everyone who eats at home.", "+ Add someone"]),
    ("step-helpers", [
        "Does anyone else help run the house?",
        "If someone else shops or cooks too, I&rsquo;ll give them their own way in once your first week is ready.",
    ]),
    ("step-meals-days", [
        "Who&rsquo;s eating, and when?",
        "Everyone starts on every meal. Tap a day to pick who&rsquo;s eating it, or to tap off a meal you don&rsquo;t need planned.",
        "Everyone", "Some of you", "Not planned",
    ]),
    # UPDATED 2026-10-06 (Onboarding regrouped): "Dietary restrictions"
    # again, Emily 2026-10-05; the line stays.
    ("step-restrictions", ["Dietary restrictions", "Allergies, must-avoids, the way someone eats."]),
    ("step-eating-style", ["Is there a certain way you'd like meals to lean?"]),
    ("step-wont-eat", ["Anything I should never recommend?"]),
    ("step-excited-about", ["What are you excited to eat more of lately?"]),
    # 2026-09-30: "Do you like to cook ahead?" replaced the prep-days
    # question, and "How do you feel about leftovers?" left setup.
    ("step-prep", [
        "Do you like to cook ahead?",
        "A prep day is when you make things for later in the week: lunches, a big batch, sauces.",
        "No, I cook as I go", "Every meal is made the day you eat it.",
        # "How long?" left 2026-10-06 (Onboarding regrouped): every prep
        # day is planned for up to 2 hours.
        "Yes, I have a prep day", "Which day?",
    ]),
    ("step-variety-breakfast", [
        "How many different breakfasts in a week?",
        "You&rsquo;ll still have breakfast all the mornings you selected. This is how many different ones.",
    ]),
    ("step-variety-lunch", [
        "How many different lunches in a week?",
        "You&rsquo;ll still have lunch all the days you selected. This is how many different ones.",
    ]),
    ("step-variety-dinner", [
        "How many different dinners in a week?",
        "You&rsquo;ll still have dinner all the nights you selected. This is how many different ones.",
    ]),
    ("step-dinner-time", ["What time do you usually have dinner?", "So I can tell you when to start cooking.", "Skip"]),
    ("step-kit-repeats", [
        "Just two more! Almost there.", "What you&rsquo;ve got to cook with",
        "I&rsquo;ll only suggest recipes your kitchen can make.", "When should your first plan start?",
        "Let&rsquo;s plan your first week", "Skip &mdash; I&rsquo;ll tell you as we go",
    ]),
])
def test_the_cleansed_copy_is_still_the_copy(step, words):
    markup = _step_markup(step)
    for w in words:
        assert w in markup, f"{step} lost {w!r}"


def test_no_italics_no_emoji_no_literal_colours_on_the_question_screens():
    css = _question_css()
    # The reveal's "Nothing planned" italic predates this work and is the
    # only font-style on the page; the question screens add none.
    question_only = css.split("The reveal: Week 1 (rule S5")[0]
    # Card 5 (2026-09-18, mock 07-dinner-time): dinner-time's Skip is a
    # quiet italic line, approved in the mockup — the one other font-style
    # on the question screens, carved out the same way as the reveal's.
    skip_quiet_rule = _rule(css, ".q-skip-quiet") + "}"
    assert "font-style: italic" in skip_quiet_rule, "the carve-out no longer matches .q-skip-quiet"
    question_only_sans_skip_quiet = question_only.replace(skip_quiet_rule, "")
    assert "font-style" not in question_only_sans_skip_quiet
    # Narrowed 2026-10-05 (onboarding-names): this used to scan the raw text,
    # so a hex quoted in an EXPLANATORY COMMENT read as a literal colour in a
    # rule. All four values it exempted one at a time (#7C7161, #FBF6EE,
    # #BFB6A5, #101F19) sat in ONE prose line recording a measured contrast
    # ratio, and the "You" badge's comment added five more -- an allowlist
    # that grows once per documented decision is one that eventually gets
    # switched off. A hex in a comment is not a colour the browser applies,
    # and recording the measured value beside the token is this repo's own
    # convention (theme.css does it throughout).
    #
    # Two things keep it no looser than it was, both mutation-checked. The
    # first cut of this used `question_only.split("/*")[0]` to deal with the
    # banner the slice above cuts in half, which TRUNCATED at the first
    # comment and left nearly every rule unscanned -- caught by neutering the
    # stripper and watching the file stay green. So: complete comments go,
    # one dangling opener goes, nothing else is dropped, and the two
    # assertions below say the stripper ran AND that the rules this file is
    # about are still in what it scanned.
    code_only = re.sub(r"/\*.*?\*/", "", question_only, flags=re.S)
    code_only = re.sub(r"/\*.*\Z", "", code_only, flags=re.S)
    assert "/*" not in code_only and len(code_only) < len(question_only), (
        "the comment stripper did not run -- a literal colour in a comment "
        "would pass over"
    )
    for anchor in (".q-title", ".member-you-badge", ".btn-soft"):
        assert anchor in code_only, (
            f"{anchor} fell out of the stripped CSS -- the stripper is eating "
            "rules, so a literal colour in one would pass over"
        )
    literals = re.findall(r"#[0-9a-fA-F]{3,6}\b", code_only)
    assert literals == [], f"literal colours in the question CSS: {literals}"
    for step in QUESTION_STEPS:
        markup = _step_markup(f"step-{step}")
        assert not re.search(r"[\U0001F300-\U0001FAFF]", markup), f"{step} uses an emoji"
        assert "<i>" not in markup and "<em>" not in markup


# ---------- the moment between screens ----------


def test_every_step_arrives_as_a_crossfade_not_a_cut():
    css = _css()
    assert "@keyframes step-in" in css
    rule = _rule(css, ".step-enter > :not(.q-foot)")
    assert "var(--motion-base)" in rule and "var(--motion-ease)" in rule
    frames = css[css.index("@keyframes step-in"):]
    frames = frames[: frames.index("}\n  }") + 4]
    assert "translateY(8px)" in frames and "opacity: 0" in frames
    assert "@media (prefers-reduced-motion: reduce)" in css
    assert "transition: background-color var(--motion-base)" in _rule(css, "body")
    # showStep is what adds the class, and only on a real arrival.
    body = _fn("showStep")
    assert "classList.add('step-enter')" in body
    assert "wasShowing" in body


@_needs_node
def test_showstep_stamps_the_arrival_class_on_the_step_it_shows():
    out = _run(_nav_harness() + """
showStep('household');
const a = ELS['step-household']._classes.has('step-enter');
showStep('meals-days');
console.log(JSON.stringify([a, ELS['step-meals-days']._classes.has('step-enter')]));
""")
    assert out == [True, True]


# ---------- the reveal is the finish ----------


def test_the_reveal_is_on_spruce_with_the_mark_and_one_button():
    """Since 2026-09-18 (Week 1: the day-card carousel) the head is the mark
    and wordmark over "Here's week 1."; the big number that used to stand
    under the title is gone — it lives on the app's All set screen."""
    markup = _step_markup("step-reveal")
    assert 'class="reveal-mark"' in markup, "the reveal has no mark"
    assert 'id="reveal-number"' not in markup and "reveal-receipt" not in markup
    assert markup.count("btn-primary") == 2, "one apricot per foot state (done / failed)"
    assert 'class="reveal-actions q-foot on-spruce"' in markup
    body = _fn("showStep")
    assert "classList.toggle('reveal-active', key === 'reveal')" in body
    css = _css()
    assert "body.intro-active, body.reveal-active { background-color: var(--spruce); }" in css
    assert ".reveal-number-value" not in css


@_needs_node
def test_the_page_paints_spruce_again_on_the_reveal():
    out = _run(_nav_harness() + """
const body = makeEl('body');
document.body = body;
const seen = [];
['intro-know', 'household', 'kit-repeats', 'reveal'].forEach(function (k) {
  showStep(k); seen.push([k, body.classList.contains('intro-active'), body.classList.contains('reveal-active')]);
});
console.log(JSON.stringify(seen));
""")
    assert out == [
        ["intro-know", True, False], ["household", False, False],
        ["kit-repeats", False, False], ["reveal", False, True],
    ]
