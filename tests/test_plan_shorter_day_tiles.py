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


def test_the_day_bar_is_the_shared_switcher_with_no_72px_floor():
    # Bright Dusk: the day bar is .seg-days, 52px (Z-Week), so every segment
    # keeps a tap target of 46px by 44px or more across the bar.
    theme = (REPO / "static" / "theme.css").read_text(encoding="utf-8")
    assert ".seg-days { height: 52px; }" in theme
    assert "min-height: 72px" not in SHELL_CSS[SHELL_CSS.index(".wk-daybar.is-long"):][:200]
    assert ".wk-daybar.is-long .wk-tile { flex: 0 0 44px; }" in SHELL_CSS


def test_the_chosen_segment_and_today_read_off_the_switcher():
    theme = (REPO / "static" / "theme.css").read_text(encoding="utf-8")
    assert ".seg-btn.is-on {" in theme and ".seg-btn.is-today { color: var(--clay); }" in theme
