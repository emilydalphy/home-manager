"""
Inventory is a real feature again (Emily, 2026-10-25/26). The "In development"
pill on Cook -> More -> Inventory and the sheet's "Inventory is still being
built..." line (Loop Board card A5) are gone, with the INVENTORY_IN_DEVELOPMENT
constants that gated them. This file keeps its old name so the history is
findable; it now pins the label's absence.

No JavaScript harness exists for the shell, so these read the source text.
"""
from __future__ import annotations

from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SHELL_JS = (REPO / "static" / "shell.js").read_text()
INVENTORY_HTML = (REPO / "static" / "inventory.html").read_text()
SHELL_CSS = (REPO / "static" / "shell.css").read_text()


def _function(src, start, end):
    return src[src.index(start):src.index(end)]


def test_the_constants_are_gone():
    assert "INVENTORY_IN_DEVELOPMENT" not in SHELL_JS
    assert "INVENTORY_IN_DEVELOPMENT" not in INVENTORY_HTML
    assert "INVENTORY_IN_DEVELOPMENT" not in SHELL_CSS


def test_cook_more_inventory_row_has_no_pill():
    fn = _function(SHELL_JS, "function cookMoreRowsHtml()", "function openCookMoreSheet()")
    assert 'data-sheet="inventory"' in fn  # the row itself stays
    inv = fn[fn.index('data-sheet="inventory"'):]
    assert "In development" not in inv
    assert "pill" not in inv


def test_sheet_has_no_in_development_line():
    assert "Inventory is still being built" not in INVENTORY_HTML
    assert "inv-dev-note" not in INVENTORY_HTML
    assert "no need to keep it up to date" not in INVENTORY_HTML


def test_scan_entry_points_are_untouched():
    for kind in ("receipt", "fridge", "pantry"):
        assert f'id="inv-scan-{kind}-btn"' in INVENTORY_HTML
        assert f'id="inv-scan-{kind}-input"' in INVENTORY_HTML
    assert "runScan(kind, file)" in INVENTORY_HTML
    assert "/api/inventory/confirm-scan" in INVENTORY_HTML
