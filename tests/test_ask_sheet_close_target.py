"""
Source-marker guard for the ask sheet's close affordance (Julia, first beta
tester, 2026-09-08): "the back click to close the chat to go back to the
main screen is hard to click to go back." The only way to dismiss the ask
sheet used to be its 44x44-WIDE-but-5px-TALL drag handle — technically a
44px target in one dimension only, and visually so thin it read as
decorative rather than tappable.

These are SOURCE assertions, not behaviour tests: shell.js has no JS test
harness in this repo (no package.json/jest/mocha), so there is no way to
drive a real DOM/click/keydown/popstate sequence from pytest. A marker that
is present but mis-wired is still a far better failure mode than a marker
that silently disappears in a future edit or merge.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SHELL_JS = (REPO / "static" / "shell.js").read_text(encoding="utf-8")
SHELL_CSS = (REPO / "static" / "shell.css").read_text(encoding="utf-8")
SHELL_HTML = (REPO / "static" / "shell.html").read_text(encoding="utf-8")


def test_back_button_exists_in_the_sheet_markup():
    """A real, always-visible control — not just the thin handle."""
    assert 'id="ask-sheet-back"' in SHELL_HTML, (
        "static/shell.html no longer has the #ask-sheet-back button. Julia's "
        "feedback (2026-09-08) was that the handle alone was too hard to hit "
        "as a way back to the main screen; this button is the fix."
    )
    # It still sits inside #ask-sheet, alongside (not instead of) the handle.
    ask_sheet_start = SHELL_HTML.index('id="ask-sheet"')
    handle_pos = SHELL_HTML.index('id="ask-sheet-handle"', ask_sheet_start)
    back_pos = SHELL_HTML.index('id="ask-sheet-back"', ask_sheet_start)
    assert ask_sheet_start < handle_pos < back_pos, (
        "#ask-sheet-back should be inside #ask-sheet, after the handle "
        "(the handle stays as a secondary affordance, not the only one)."
    )


def test_back_button_copy_is_back_no_exclamation():
    """She thinks of it as going back to the main screen, not 'closing' a
    dialog — and Pomona's voice never uses exclamation marks."""
    ask_sheet_back = re.search(
        r'id="ask-sheet-back"[^>]*>(.*?)</button>', SHELL_HTML, re.S
    )
    assert ask_sheet_back, "Could not find the #ask-sheet-back button body in shell.html."
    label = ask_sheet_back.group(1)
    assert "Back" in label, "The button's label should say 'Back'."
    assert "!" not in label, "Pomona's voice never uses exclamation marks."


def test_back_button_is_a_real_44px_target():
    """The whole point: unlike the 44x5px handle, this control is a genuine
    44x44 (or larger) tap target in both dimensions."""
    rule = re.search(r"\.ask-sheet-back\s*\{([^}]*)\}", SHELL_CSS, re.S)
    assert rule, "static/shell.css has no .ask-sheet-back rule."
    body = rule.group(1)
    assert re.search(r"min-height:\s*44px", body), (
        ".ask-sheet-back must guarantee at least a 44px-tall tap target."
    )
    assert re.search(r"min-width:\s*44px", body), (
        ".ask-sheet-back must guarantee at least a 44px-wide tap target."
    )


def test_close_ask_sheet_is_wired_to_back_button_and_scrim():
    _assert_wired = (
        "document.getElementById('ask-sheet-back').addEventListener('click', closeAskSheet)"
    )
    assert _assert_wired in SHELL_JS, (
        "The #ask-sheet-back button is not wired to closeAskSheet in shell.js."
    )
    assert "askScrim.addEventListener('click', closeAskSheet)" in SHELL_JS, (
        "The scrim tap must call closeAskSheet directly and reliably — this "
        "was one of Julia's three complaints (the handle being the only "
        "reliable dismiss)."
    )
    # The handle stays as a secondary affordance, not removed.
    assert (
        "document.getElementById('ask-sheet-handle').addEventListener('click', closeAskSheet)"
        in SHELL_JS
    )


def test_escape_closes_the_sheet():
    # shell.js already has other document-level keydown/Escape handlers
    # (e.g. the dinner-confirm dialog), so find every one and require that
    # at least one of them is the ask sheet's.
    escape_blocks = re.findall(
        r"document\.addEventListener\('keydown',\s*function\s*\(e\)\s*\{([^}]*)\}\);",
        SHELL_JS,
    )
    assert escape_blocks, "No document-level keydown listener found in shell.js."
    matches = [
        body for body in escape_blocks
        if "Escape" in body and "closeAskSheet" in body
    ]
    assert matches, (
        "No keydown listener closes the ask sheet on Escape. Expected one "
        "guarded on e.key === 'Escape' that calls closeAskSheet()."
    )
    # Must not fire while the sheet is already hidden.
    assert any("askSheet.hidden" in body for body in matches), (
        "The Escape handler should be gated on the sheet actually being open."
    )


def test_popstate_cooperates_with_the_ask_sheet_without_a_second_listener():
    """The back gesture (Android/browser) should close the sheet before
    leaving the tab — a history entry pushed on open, and the shell's ONE
    existing popstate listener (shared with Meals'/Grocery's own step
    history) should be the thing that notices, not a second listener."""
    popstate_listeners = re.findall(r"addEventListener\('popstate'", SHELL_JS)
    assert len(popstate_listeners) == 1, (
        f"Expected exactly one 'popstate' listener in shell.js, found "
        f"{len(popstate_listeners)}. The ask sheet must cooperate with the "
        "existing listener (Meals'/Grocery's step history), not add its own."
    )

    assert "askSheetHistoryPushed" in SHELL_JS, (
        "No history-tracking flag for the ask sheet found — expected some "
        "state (e.g. askSheetHistoryPushed) so the shared popstate listener "
        "can tell the sheet's own back-navigation apart from a tab/step change."
    )
    assert re.search(r"askSheet:\s*true", SHELL_JS), (
        "openAskSheet should push a history entry marked askSheet:true so "
        "the popstate listener can recognize it."
    )

    popstate_fn = re.search(
        r"window\.addEventListener\('popstate', function \(e\) \{(.*?)\n  \}\);",
        SHELL_JS,
        re.S,
    )
    assert popstate_fn, "Could not locate the shell's popstate listener body."
    body = popstate_fn.group(1)
    assert "askSheetHistoryPushed" in body and "closeAskSheet" in body, (
        "The shared popstate listener must check askSheetHistoryPushed and "
        "call closeAskSheet when the back gesture is leaving the sheet's own "
        "pushed entry."
    )


def test_no_desktop_ask_column_short_circuit_remains():
    """openAskSheet used to check isDesktopAsk() and return early, before any
    of the sheet/history plumbing ran, so a permanently-open desktop Ask
    column was never affected by this ticket's change. That column (and the
    isDesktopAsk() gate) is gone — Emily's "phone in the room" decision,
    2026-09-11: the ask sheet behaves identically at every width now, so
    openAskSheet always runs the history/pushState plumbing this file
    exists to pin."""
    assert "isDesktopAsk" not in SHELL_JS, (
        "isDesktopAsk() should not exist any more — the ask sheet is the "
        "only ask surface at every width."
    )
    open_fn = re.search(
        r"function openAskSheet\(prefill\) \{(.*?)\n  \}\n",
        SHELL_JS,
        re.S,
    )
    assert open_fn, "Could not locate openAskSheet in shell.js."
    body = open_fn.group(1)
    assert "pushState" in body, (
        "openAskSheet must still push its history entry unconditionally."
    )
