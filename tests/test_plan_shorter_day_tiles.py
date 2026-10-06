"""Plan strip: the day tiles are only as tall as the day and the date
(Emily, 2026-10-05: "Yes make them shorter", after the dots went).

The 72px floor held the three dots; with them gone it left ~25px empty.
What has to stay true: the tap target never drops under 44px (hard rule 6)
and the padding, selected rim and TODAY tint are untouched.
"""
import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SHELL_CSS = (REPO / "static" / "shell.css").read_text(encoding="utf-8")


def _rule(selector):
    m = re.search(r"(?m)^" + re.escape(selector) + r" \{(.*?)\n\}", SHELL_CSS, re.S)
    assert m, selector
    return m.group(1)


def test_the_tile_has_no_72px_floor_and_keeps_a_44px_tap_target():
    tile = _rule(".wk-tile")
    assert "min-height: 72px" not in tile
    assert re.search(r"(?m)^\s*min-height: 44px;", tile)
    # Same padding as before the change — only the empty space went.
    assert "padding: 10px 2px 9px;" in tile


def test_the_selected_rim_and_today_tint_are_untouched():
    assert ".wk-tile.is-selected { border-color: var(--ink-strong); }" in SHELL_CSS
    assert ".wk-tile.is-today { background: var(--sand); border-color: var(--hairline-deep); }" in SHELL_CSS
