"""
The consent screen's words and where it shows (Loop Board "App Store: ask
permission before household details go to the AI", 2026-09-27).

Two copies of the words exist — the shell's screen (existing households,
and Preferences) and onboarding's step (new households) — because
onboarding.html doesn't load shell.js. These tests keep them identical and
check each says the four things the card requires: what's sent, who gets
it, what for, and the training line.
"""
from __future__ import annotations

import json
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parent.parent
SHELL = (ROOT / "static" / "shell.js").read_text()
ONBOARDING = (ROOT / "static" / "onboarding.html").read_text()

SHARED_KEYS = ["askTitle", "lead", "moreLabel", "more", "linkLabel", "linkUrl", "notNow"]


def _copy_block(source: str, decl: str) -> str:
    start = source.index(decl)
    i = source.index("{", start)
    depth = 0
    for j in range(i, len(source)):
        if source[j] == "{":
            depth += 1
        elif source[j] == "}":
            depth -= 1
            if depth == 0:
                return source[i : j + 1]
    raise AssertionError("unbalanced AI_CONSENT_COPY")


def _field(block: str, key: str) -> str:
    m = re.search(rf"\b{key}: '((?:[^'\\]|\\.)*)'", block)
    assert m, f"{key} missing from AI_CONSENT_COPY"
    return m.group(1)


def _rows(block: str) -> list[tuple[str, str]]:
    return re.findall(r"\{ label: '([^']*)', body: '([^']*)' \}", block)


SHELL_COPY = _copy_block(SHELL, "var AI_CONSENT_COPY = ")
ONB_COPY = _copy_block(ONBOARDING, "const AI_CONSENT_COPY = ")


def test_the_two_copies_say_the_same_thing():
    """CATCH. A household sees the same words whether it meets them in setup or on its next visit."""
    for key in SHARED_KEYS:
        assert _field(SHELL_COPY, key) == _field(ONB_COPY, key), key
    assert _rows(SHELL_COPY) == _rows(ONB_COPY)
    assert len(_rows(SHELL_COPY)) == 3


def test_it_says_what_is_sent_who_gets_it_what_for_and_the_training_line():
    """CATCH. The four things guideline 5.1.2(i) and the card ask for, in plain words."""
    rows = dict(_rows(SHELL_COPY))
    assert set(rows) == {"What I send", "Who gets it", "What for"}
    assert "allergies" in rows["What I send"]
    assert "who’s home" in rows["What I send"]
    assert "Anthropic" in rows["Who gets it"]
    assert "Claude" in rows["Who gets it"]
    assert "plan your meals" in rows["What for"]
    assert "doesn’t use it to train" in rows["What for"]
    assert "Claude" in _field(SHELL_COPY, "askTitle")


def test_copy_follows_the_house_rules():
    """GUARD. Passphrase-free, no dashboard words, no exclamation marks, contractions used."""
    text = " ".join(
        [_field(SHELL_COPY, k) for k in SHARED_KEYS if k != "linkUrl"]
        + [b for _, b in _rows(SHELL_COPY)]
    )
    assert "!" not in text
    for banned in ("AI-powered", "seamless", "Get started", "Welcome to", "simply", "journey"):
        assert banned.lower() not in text.lower(), banned
    assert "’" in text, "contractions, always"


def test_the_shell_asks_before_the_tabs_when_never_asked():
    """CATCH. An existing household is asked once, on its next visit, before anything renders."""
    check = SHELL[SHELL.index("(async function checkOnboarding()"):]
    check = check[: check.index("})();")]
    assert "shellWho.ai_consent === ''" in check
    assert check.index("openAiConsentScreen()") < check.index("activateTab(")


def test_preferences_shows_the_choice_and_opens_the_screen():
    """CATCH. Preferences reads the choice back and is the way to change it."""
    render = SHELL[SHELL.index("function renderPrefsRows()"):]
    render = render[: render.index("\n  }\n")]
    assert "aiConsentPrefsRowHtml()" in render
    row = SHELL[SHELL.index("function aiConsentPrefsRowHtml()"):]
    row = row[: row.index("\n  }\n")]
    assert "Sharing with Claude" in row
    assert 'data-aic-prefs="open"' in row


def test_onboarding_asks_after_the_last_question_and_before_the_first_week():
    """
    CATCH. The step sits after the last question and before any AI call.

    UPDATED 2026-10-05 ("Anything else I should know?"): the reveal is no
    longer the very next step -- setup's last answer sits between them, and
    it is the step that reads the note with a model call. So the claim is
    exactly as before (consent comes before anything goes to Anthropic) and
    this now pins all four positions rather than three, which is more than
    it asserted before, not less.
    """
    steps = json.loads(re.search(r"const ALL_STEPS = (\[.*?\]);", ONBOARDING).group(1).replace("'", '"'))
    assert steps.index("ai-consent") == steps.index("kit-repeats") + 1
    assert steps.index("anything-else") == steps.index("ai-consent") + 1
    assert steps.index("reveal") == steps.index("anything-else") + 1
    assert '<div id="step-ai-consent"' in ONBOARDING
    after = ONBOARDING[ONBOARDING.index("function afterLastQuestion()"):]
    after = after[: after.index("\n}\n")]
    assert "showStep('ai-consent')" in after


def test_not_now_in_onboarding_saves_the_answers_without_asking_for_a_week():
    """CATCH. "Not now" keeps everything they answered and asks for no plan."""
    later = ONBOARDING[ONBOARDING.index("document.getElementById('ai-consent-later').onclick"):]
    later = later[: later.index("\n};\n")]
    assert "saveAiConsentAnswer(false)" in later
    assert "finishSetupAndReveal({ plan: false })" in later
    finish = ONBOARDING[ONBOARDING.index("async function finishSetupAndReveal("):]
    finish = finish[: finish.index("\n}\n")]
    assert finish.index("if (!plan) return true;") < finish.index("generateFirstPlanAndReveal(")


def test_allow_in_onboarding_saves_the_yes_before_the_week_is_asked_for():
    """
    CATCH. The yes is stored first; only then is the week asked for.

    UPDATED 2026-10-05 ("Anything else I should know?"): Allow hands on to
    setup's last answer rather than finishing setup itself, so the ordering
    to pin is "the yes is stored before this handler goes anywhere" plus
    "the week is still asked for downstream of it, never inside it". Both
    halves are asserted; the claim is unchanged.
    """
    allow = ONBOARDING[ONBOARDING.index("document.getElementById('ai-consent-allow').onclick"):]
    allow = allow[: allow.index("\n};\n")]
    assert allow.index("saveAiConsentAnswer(true)") < allow.index("showStep('anything-else')")
    # Nothing in this handler asks for a week -- it cannot run before the
    # yes above it, because it does not run here at all.
    assert "finishSetupAndReveal(" not in allow
    # And the week really is asked for from the step it hands on to.
    note_step = ONBOARDING[ONBOARDING.index("async function runAnythingElseNext()"):]
    note_step = note_step[: note_step.index("\n}\n")]
    assert "finishSetupAndReveal()" in note_step
