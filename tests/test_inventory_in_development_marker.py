"""
Inventory: mark as still in development (Loop Board ticket). As a beta
tester, I want Pomona to tell me which parts are finished and which are
still being built, so that I don't put effort into keeping something up to
date that Pomona isn't asking me to.

Decision taken: LABEL it, don't hide it — scans feed inventory today, so a
hidden screen would make scanned items vanish somewhere the tester can't
see. This adds a small neutral "In development" pill at both entry points
(the Kitchen tile in kitchenTilesHtml(), and the desktop rail row in
shell.html) plus one plain line at the top of the inventory sheet, and
touches nothing else — receipt/fridge/pantry scans are untouched.

Two front-end constants gate all of it, same pattern as
static/shell.js's existing SHOW_CHORES_ON_TODAY:
  - INVENTORY_IN_DEVELOPMENT in static/shell.js (the two pills)
  - INVENTORY_IN_DEVELOPMENT in static/inventory.html (the sheet's note
    line) — a separate copy because inventory.html is a separate document
    (loaded standalone at /inventory, or embedded in the Kitchen sheet's
    iframe) and can't share shell.js's variable.

This repo has no JavaScript execution harness for the shell (see
tests/test_cook_voice_hidden.py's docstring), so — like that file — these
pin the source text directly rather than executing it.
"""
from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SHELL_JS = (REPO / "static" / "shell.js").read_text()
SHELL_HTML = (REPO / "static" / "shell.html").read_text()
INVENTORY_HTML = (REPO / "static" / "inventory.html").read_text()

MARKER_TEXT = "In development"
SHEET_NOTE_TEXT = (
    "Inventory is still being built. Nothing else in Pomona depends on "
    "it, so there’s no need to keep it up to date."
)


def test_shell_js_constant_exists_and_is_true():
    m = re.search(r"var\s+INVENTORY_IN_DEVELOPMENT\s*=\s*(true|false)\s*;", SHELL_JS)
    assert m, "INVENTORY_IN_DEVELOPMENT constant not found in static/shell.js"
    assert m.group(1) == "true"


def test_shell_js_constant_declared_beside_other_shell_constants():
    """Lives next to SHOW_CHORES_ON_TODAY, the flag it mirrors."""
    chores_idx = SHELL_JS.index("var SHOW_CHORES_ON_TODAY")
    flag_idx = SHELL_JS.index("var INVENTORY_IN_DEVELOPMENT")
    notif_idx = SHELL_JS.index("var SHOW_NOTIF_BELL")
    assert chores_idx < flag_idx < notif_idx


def test_kitchen_tile_pill_is_gated_and_uses_the_neutral_pill_classes():
    """
    kitchenTilesHtml()'s Inventory row prints the pill only inside an
    INVENTORY_IN_DEVELOPMENT conditional, using the design system's
    neutral pill (theme.css .pill / .pill-neutral) rather than a new class.
    """
    fn_start = SHELL_JS.index("function kitchenTilesHtml()")
    fn_end = SHELL_JS.index("function renderKitchen()")
    fn = SHELL_JS[fn_start:fn_end]
    assert "data-sheet=\"inventory\"" in fn
    assert "INVENTORY_IN_DEVELOPMENT" in fn
    assert "pill pill-neutral" in fn
    assert MARKER_TEXT in fn
    # Not apricot: the pill classes used must not be an accent/apricot pill.
    assert "pill-attention" not in fn


def test_rail_row_pill_is_appended_by_js_when_flag_is_true():
    """
    The rail's Inventory row (shell.html) carries no pill markup by
    default — a `.pill`'s own display:inline-flex would beat a plain
    [hidden] attribute (the same lesson already documented for
    .notif-bell/SHOW_NOTIF_BELL), so shell.js appends the element itself,
    gated by the same INVENTORY_IN_DEVELOPMENT check used for the tile.
    """
    assert 'data-rail-sheet="inventory"' in SHELL_HTML
    # Static markup: no pill baked in (it's appended, not hidden/shown).
    rail_row_start = SHELL_HTML.index('data-rail-sheet="inventory"')
    rail_row_end = SHELL_HTML.index("</button>", rail_row_start)
    assert "pill" not in SHELL_HTML[rail_row_start:rail_row_end]

    wiring_idx = SHELL_JS.index('.rail-row[data-rail-sheet="inventory"]')
    window = SHELL_JS[max(0, wiring_idx - 400) : wiring_idx + 400]
    assert "INVENTORY_IN_DEVELOPMENT" in window
    assert "pill pill-neutral" in window
    assert MARKER_TEXT in window
    assert "pill-attention" not in window


def test_inventory_sheet_has_the_top_of_sheet_note_gated_by_its_own_constant():
    """
    inventory.html carries its own copy of the constant (it's a separate
    document from shell.js) and the plain top-of-sheet line it gates.
    """
    m = re.search(
        r"const\s+INVENTORY_IN_DEVELOPMENT\s*=\s*(true|false)\s*;", INVENTORY_HTML
    )
    assert m, "INVENTORY_IN_DEVELOPMENT constant not found in static/inventory.html"
    assert m.group(1) == "true"

    assert 'id="inv-dev-note"' in INVENTORY_HTML
    assert SHEET_NOTE_TEXT in INVENTORY_HTML

    # Hidden by default in markup, unhidden by the constant check — not
    # left out of the document (so flipping the constant is the whole
    # reversal), and not carrying a conflicting display override that
    # would defeat [hidden] the way a `.pill` would (see the rail test).
    note_idx = INVENTORY_HTML.index('id="inv-dev-note"')
    tag_start = INVENTORY_HTML.rindex("<p", 0, note_idx)
    tag_end = INVENTORY_HTML.index(">", note_idx)
    assert "hidden" in INVENTORY_HTML[tag_start:tag_end]

    guard_idx = INVENTORY_HTML.index("devNote.hidden = false")
    const_idx = INVENTORY_HTML.index("const INVENTORY_IN_DEVELOPMENT")
    assert const_idx < guard_idx


def test_sheet_note_has_no_exclamation_marks_or_apology_or_beta_word():
    """DESIGN_SYSTEM.md §8: calm and plain, no apology, no beta jargon."""
    assert "!" not in SHEET_NOTE_TEXT
    lowered = SHEET_NOTE_TEXT.lower()
    for word in ("sorry", "apologi", "beta"):
        assert word not in lowered


def test_scan_entry_points_are_untouched():
    """
    Receipt, fridge and pantry scans must keep working exactly as today —
    this ticket only ever adds a label. Pin the three scan buttons, their
    file inputs, and confirm-scan's endpoint are all still present and
    wired exactly as before.
    """
    for kind in ("receipt", "fridge", "pantry"):
        assert f'id="inv-scan-{kind}-btn"' in INVENTORY_HTML
        assert f'id="inv-scan-{kind}-input"' in INVENTORY_HTML
    assert "runScan(kind, file)" in INVENTORY_HTML
    assert "/api/inventory/confirm-scan" in INVENTORY_HTML
