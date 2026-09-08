"""
Cook-mode hands-free voice is hidden, not deleted (Emily, 2026-09-08):
"Let's just drop the cook mode voice for now. Just hide it, and we can
rebuild it later."

The feature — a mic button on the Cook screen (both the prep-schedule
section and a recipe's detail panel), a status log that shows spoken
commands, and SpeechRecognition/speechSynthesis wiring in cookToggleVoice —
stays in static/shell.js verbatim, gated behind one constant,
COOK_VOICE_ENABLED, declared next to the other shell-wide constants
(REHEAT_ACTION_LABEL, TABS). This file has no JavaScript execution
harness (see tests/test_cooker_today.py's node workaround for the one
place this repo does that), so — like tests/test_chores_setup_split.py's
onboarding.html checks — these pin the source text directly:

1. The constant exists and is false.
2. Every place that used to render a mic button or the voice-status div
   unconditionally now does so only inside a COOK_VOICE_ENABLED ternary.
3. cookToggleVoice — the function that would create a voice session and
   trigger a microphone permission prompt — bails out first thing when
   the constant is false, so nothing can reach window.createVoiceSession
   even if some other path ever calls it.
4. stopCookVoice stays exactly the safe no-op it always was: since
   cookToggleVoice's guard means cookState.voiceSession can never be set
   while the flag is false, stopCookVoice's existing
   "only stop if there's an active session" check already covers it —
   nothing there needed to change, which this test pins.
5. static/cooker.html (the legacy standalone page, unreachable from the
   shell per CLAUDE.md/theme.css) is left alone but for a one-line
   pointer comment at its own hands-free section, naming the constant
   above.
"""
from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SHELL_JS = (REPO / "static" / "shell.js").read_text()
COOKER_HTML = (REPO / "static" / "cooker.html").read_text()


def _extract_function(name: str, source: str) -> str:
    """Lift one brace-balanced `function name(...) {...}` out of source."""
    start = source.index(f"function {name}(")
    i = source.index("{", start)
    depth, j = 0, i
    while True:
        if source[j] == "{":
            depth += 1
        elif source[j] == "}":
            depth -= 1
            if depth == 0:
                break
        j += 1
    return source[start : j + 1]


def test_cook_voice_enabled_constant_is_false():
    """The one switch controlling the whole feature is off."""
    m = re.search(r"var\s+COOK_VOICE_ENABLED\s*=\s*(true|false)\s*;", SHELL_JS)
    assert m, "COOK_VOICE_ENABLED constant not found in static/shell.js"
    assert m.group(1) == "false"


def test_cook_voice_enabled_declared_beside_other_shell_constants():
    """
    Lives near TABS/REHEAT_ACTION_LABEL rather than buried inside the
    cook-specific section, per the instruction to put it "beside the
    other shell constants."
    """
    reheat_idx = SHELL_JS.index("var REHEAT_ACTION_LABEL")
    flag_idx = SHELL_JS.index("var COOK_VOICE_ENABLED")
    tabs_idx = SHELL_JS.index("var TABS = [")
    assert reheat_idx < flag_idx < tabs_idx


def test_both_cook_mic_buttons_are_gated():
    """
    Two mic buttons exist in the source (prep schedule, recipe detail) —
    both must render only when COOK_VOICE_ENABLED is true, not
    unconditionally.
    """
    occurrences = [m.start() for m in re.finditer(r'data-cook="voice"', SHELL_JS)]
    assert len(occurrences) == 2, (
        f"expected exactly 2 cook-mic buttons in the source, found {len(occurrences)}"
    )
    for pos in occurrences:
        window = SHELL_JS[max(0, pos - 250) : pos]
        assert "COOK_VOICE_ENABLED" in window, (
            "a data-cook=\"voice\" button is not preceded by a "
            "COOK_VOICE_ENABLED guard within 250 chars"
        )
        assert "cook-mic" in SHELL_JS[max(0, pos - 250) : pos + 50]


def test_cook_voice_status_div_is_gated():
    """The #cook-voice status/log panel only appears when the flag is on."""
    pos = SHELL_JS.index('id="cook-voice"')
    window = SHELL_JS[max(0, pos - 250) : pos]
    assert "COOK_VOICE_ENABLED" in window


def test_cook_toggle_voice_bails_out_first_when_disabled():
    """
    cookToggleVoice is the only place that calls window.createVoiceSession
    (which is what would spin up SpeechRecognition/speechSynthesis and
    trigger a mic permission prompt). It must check the flag before doing
    anything else, so no session is ever created while voice is hidden —
    defense in depth beyond just not rendering the button.
    """
    fn = _extract_function("cookToggleVoice", SHELL_JS)
    guard_idx = fn.index("if (!COOK_VOICE_ENABLED) return;")
    session_idx = fn.index("createVoiceSession")
    assert guard_idx < session_idx, (
        "cookToggleVoice must check COOK_VOICE_ENABLED before reaching "
        "createVoiceSession"
    )
    # The guard must be at (or immediately after) the top of the function,
    # not somewhere in the middle behind other logic.
    body_start = fn.index("{") + 1
    prefix = fn[body_start:guard_idx]
    assert prefix.strip().startswith("//") or prefix.strip() == "", (
        "COOK_VOICE_ENABLED guard should be the first real statement in "
        "cookToggleVoice"
    )


def test_stop_cook_voice_remains_a_safe_noop():
    """
    stopCookVoice is unconditional-call-safe: it only acts on an already
    active session. Because cookToggleVoice's guard means
    cookState.voiceSession can never be assigned while the flag is off,
    this existing shape is already the required no-op and needed no
    change — pinned here so a future edit doesn't quietly remove the
    active-session check.
    """
    fn = _extract_function("stopCookVoice", SHELL_JS)
    assert "cookState.voiceSession" in fn
    assert ".isActive()" in fn


def test_cooker_html_points_at_the_shell_constant():
    """
    static/cooker.html is the legacy standalone page and still has its
    own copy of the hands-free feature, but it is unreachable from the
    live shell (confirmed via CLAUDE.md / theme.css: the shell no longer
    routes to it). Rather than duplicate the gating there, it carries a
    one-line pointer back to COOK_VOICE_ENABLED.
    """
    assert "COOK_VOICE_ENABLED" in COOKER_HTML
