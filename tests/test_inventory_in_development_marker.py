"""
Inventory: mark as still in development (Loop Board ticket). As a beta
tester, I want Pomona to tell me which parts are finished and which are
still being built, so that I don't put effort into keeping something up to
date that Pomona isn't asking me to.

Decision taken: LABEL it, don't hide it — scans feed inventory today, so a
hidden screen would make scanned items vanish somewhere the tester can't
see. This adds a small neutral "In development" pill at its entry point
(the Kitchen tile in kitchenTilesHtml()) plus one plain line at the top of
the inventory sheet, and touches nothing else — receipt/fridge/pantry scans
are untouched.

Originally this pill also lived on a second entry point, the desktop rail's
Inventory row in shell.html — that whole rail (six entries, two emoji, and
a "Share meal plan" button) was removed 2026-09-11 (Emily's "phone in the
room" decision: the app is one centred phone-width column at every width,
with no separate desktop nav). "What we know" and "Inventory" are reached
through Preferences now, exactly as on the phone, so the Kitchen tile is
the only entry point this file still has to check.

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


def test_desktop_rail_is_gone():
    """
    The desktop rail (shell.html's #shell-rail, six entries including
    Inventory, removed 2026-09-11) is not coming back — "Inventory" and
    "What we know" are reached through Preferences at every width, same as
    on the phone. Pins its removal so this file's inventory-pill coverage
    doesn't silently start missing a second entry point again if a rail
    were ever reintroduced without updating this test.
    """
    assert "data-rail-sheet" not in SHELL_HTML
    assert 'id="shell-rail"' not in SHELL_HTML
    assert ".rail-row" not in SHELL_JS


def test_no_emoji_entities_anywhere_in_static():
    """DESIGN_SYSTEM.md rule 7: icons are inline stroke SVG, never emoji.
    The rail's two rows were the app's last emoji — a pencil
    (&#9998; / ✎) on "What we know" and an apple (&#127823; / 🍏) on
    "Inventory" — gone with the rail itself (2026-09-11). Scans every file
    static/ ships (not just shell.html/js) so a future emoji anywhere in
    the app trips this rather than only a rail-shaped one.
    """
    offenders = []
    for path in sorted((REPO / "static").rglob("*")):
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, ValueError):
            continue  # a binary asset (icon, font, image) — not source text
        for entity in ("&#9998;", "&#127823;"):
            if entity in text:
                offenders.append(f"{path.relative_to(REPO)}: {entity}")
    assert not offenders, "emoji entity found: " + ", ".join(offenders)


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
