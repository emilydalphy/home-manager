"""Bright Dusk's foundation (Emily, 2026-11-05; design_handoff_bright_dusk/).

What every later stage stands on, as things a test can check: one font at six
sizes, four corner sizes, no outlines, the tab bar and the toast off the
boards, and the shared components in the one place later screens reuse them.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
STATIC = REPO / "static"
THEME = (STATIC / "theme.css").read_text(encoding="utf-8")
SHELL_CSS = (STATIC / "shell.css").read_text(encoding="utf-8")
SHELL_JS = (STATIC / "shell.js").read_text(encoding="utf-8")
SHELL_HTML = (STATIC / "shell.html").read_text(encoding="utf-8")


def _no_comments(css: str) -> str:
    return re.sub(r"/\*.*?\*/", "", css, flags=re.S)


def _rules(css: str):
    """(selector, body) for every innermost rule."""
    for m in re.finditer(r"([^{}]*)\{([^{}]*)\}", _no_comments(css)):
        yield m.group(1).strip(), m.group(2)


def _live_rules(css: str):
    """Rules that can paint: not the dormant dark ones, and not Chores, which
    is paused and keeps what it had."""
    for sel, body in _rules(css):
        if "data-theme" in sel or "chore" in sel.lower():
            continue
        yield sel, body


@pytest.mark.parametrize("token, value", [
    ("--cream", "#FFFAF3"), ("--dusk", "#18356E"), ("--night-ink", "#14264A"), ("--apricot", "#FFA45C"),
    ("--soft-apricot", "#FFC08F"), ("--blush", "#FBE9D9"), ("--slate", "#55607A"), ("--clay", "#A8461A"),
])
def test_the_approved_palette(token, value):
    assert re.search(rf"^\s*{token}:\s*{value};", THEME, re.M), token


def test_one_font_and_it_is_self_hosted():
    fonts = (STATIC / "fonts" / "fonts.css").read_text(encoding="utf-8")
    assert set(re.findall(r"font-family: '([^']+)'", fonts)) == {"Figtree"}
    assert not list((STATIC / "fonts").glob("bricolage*")) and not list((STATIC / "fonts").glob("newsreader*"))
    worker = (STATIC / "service-worker.js").read_text(encoding="utf-8")
    assert "/static/fonts/figtree-normal-latin.woff2" in worker
    assert "bricolage" not in worker.split("SHELL_ASSETS", 1)[1] and "newsreader" not in worker.split("SHELL_ASSETS", 1)[1]
    assert "--font-display: var(--font-body);" in THEME and "--font-accent:  var(--font-body);" in THEME


def test_six_sizes_no_more():
    sizes = set()
    for _, body in _live_rules(SHELL_CSS):
        sizes.update(re.findall(r"font-size:\s*([0-9.]+)px", body))
    # 5px is a spacer glyph, not text.
    assert sizes - {"5"} == {"11", "13", "14", "16", "26", "32"}


def test_no_eyebrow_caps_italics_or_heavy_weights():
    for sel, body in _live_rules(SHELL_CSS):
        assert "text-transform: uppercase" not in body, sel
        assert "font-style: italic" not in body, sel
        assert not re.search(r"font-weight:\s*800", body), sel


def test_four_corner_sizes():
    # 1-3px are thin tracks and bars; 6px is the tail on an Ask bubble (Z-Ask).
    # 999px is fully round.
    allowed = {"0", "1", "2", "3", "6", "13", "16", "22", "26", "999"}
    for sel, body in _live_rules(SHELL_CSS):
        for value in re.findall(r"border-radius:\s*([^;]+);", body):
            for px in re.findall(r"(\d+)px", value):
                assert px in allowed, f"{sel}: {value}"


def test_no_beige_outlines():
    pat = re.compile(r"border:\s*[\d.]+px solid var\(--(hairline|hairline-strong|celadon-edge|sand-deep|sand)\)")
    for sel, body in _live_rules(SHELL_CSS):
        assert not pat.search(body), f"{sel} still has an outline"


def test_one_card_shadow():
    assert "--shadow-card:   0 1px 2px rgba(20, 38, 74, .05), 0 10px 28px rgba(20, 38, 74, .07);" in THEME


def test_the_shared_components_live_in_theme_css_for_every_page():
    for rule in (".screen-head {", ".screen-title {", ".screen-sub {", ".round-btn {", ".seg {", ".seg-days {",
                 ".section-head {", ".tiles {", ".tile {", ".tile-row {", ".tile-text {", ".note-card {",
                 ".btn-main, .btn-quiet {", ".field {", ".tick-ring {"):
        assert rule in THEME, rule


@pytest.mark.parametrize("fn", ["roundBtnHtml", "sectionHeadHtml", "segHtml", "tileHtml", "rootBandHtml", "quietSheetHeadHtml"])
def test_the_builders_are_there_for_later_screens(fn):
    assert f"  function {fn}(" in SHELL_JS


def test_the_tile_measures_the_board():
    tile = THEME[THEME.index(".tile {"):]
    tile = tile[:tile.index("}")]
    for rule in ("background: var(--white);", "border-radius: var(--radius-card);", "box-shadow: var(--shadow-card);",
                 "padding: 14px 12px 12px;", "gap: 8px;", "text-align: center;"):
        assert rule in tile, rule
    grid = THEME[THEME.index(".tiles {"):]
    assert "grid-template-columns: repeat(2, minmax(0, 1fr));" in grid[:300] and "gap: 10px;" in grid[:300]


def test_the_tab_bar_is_solid_dusk_in_the_pages_flow():
    bar = SHELL_CSS[SHELL_CSS.index("#tab-bar {"):]
    bar = bar[:bar.index("}")]
    assert "background: var(--dusk);" in bar
    assert "grid-template-columns: repeat(4, minmax(0, 1fr)) 72px;" in bar
    assert "position:" not in bar, "it never floats"
    assert ".tab-btn.active .tab-chip { background: rgba(255, 255, 255, .16); }" in SHELL_CSS
    assert ".tab-btn.active { color: var(--white); }" in SHELL_CSS
    assert 'id="ask-bar-dock"' not in SHELL_HTML


def test_the_toast_is_one_dark_bar_above_the_tab_bar():
    toast = SHELL_CSS[SHELL_CSS.index(".toast {"):]
    toast = toast[:toast.index("}")]
    for rule in ("bottom: calc(var(--tab-bar-h) + 12px);", "min-height: 52px;", "border-radius: var(--radius-button);",
                 "background: var(--toast-bg);", "justify-content: space-between;", "font-weight: 500;"):
        assert rule in toast, rule
    assert "--toast-bg:  var(--night-ink);" in THEME


def test_a_sheet_is_cream_with_the_grab_and_a_round_close():
    sheets = SHELL_CSS[SHELL_CSS.index("SHEETS, ONE LOOK"):]
    assert "background: var(--ground);" in sheets and "border-radius: var(--radius-sheet) var(--radius-sheet) 0 0;" in sheets
    assert "background: var(--grab);" in sheets
    close = sheets[sheets.index(".kit-sheet-close {"):]
    assert "width: 44px;" in close[:200] and "border-radius: var(--radius-pill);" in close[:200]


def test_motion_is_off_for_people_who_turn_it_off():
    assert "transform: scale(.98);" in THEME
    reduced = THEME[THEME.index("@media (prefers-reduced-motion: reduce) {\n  *,"):]
    assert "transform: none;" in reduced[:900]
    assert "--motion-press: 0ms;" in THEME


def test_the_page_keeps_nothing_of_the_band():
    assert "root-band-brand" not in SHELL_JS + SHELL_CSS
    assert "root-band-wordmark" not in SHELL_JS + SHELL_CSS
    assert "BAND_IDENTITY" not in SHELL_JS
