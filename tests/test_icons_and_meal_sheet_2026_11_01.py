"""The meal sheet (M2) and icons in soft tiles (I1 + I2) — Emily, 2026-11-01.

M2: the sheet a Plan meal opens is one row of three equal outline pills
(Swap, Tweak, Move — the long lines under them came off) and a full-width
apricot "See the recipe" pill with a book and an arrow.

I1: every meal row on Plan's day view and on Cook's list leads with a 44px
celadon tile carrying the slot's icon (breakfast sunrise, lunch bowl, snack
apple, dinner pot); a Plan row's status word carries a 15px check, loop or
clock. I2: each prep-list section gets a 44px tile (celadon for the cool
ones, sand for the heat-and-hands ones), and each Shop aisle a 16px
apricot-label icon before its name.
"""
from __future__ import annotations

import json
import re
import shutil
from pathlib import Path

import pytest

import nodeharness
from app.tools import prep_list
from test_week_seven_tiles import _extract, _extract_var
from test_plan_cards_2026_09_18 import SHELL_JS, SHELL_CSS
from test_declutter_2026_10_30 import _plan

REPO = Path(__file__).resolve().parent.parent
THEME_CSS = (REPO / "static" / "theme.css").read_text(encoding="utf-8")

_needs_node = pytest.mark.skipif(shutil.which("node") is None, reason="node is needed to run the renderers")


def _run(script: str):
    res = nodeharness.run_node(script, timeout=30)
    assert res.returncode == 0, f"node failed: {res.stderr}"
    return json.loads(res.stdout.strip().splitlines()[-1])


def _icons() -> dict:
    return _run(_extract_var("ICONS", SHELL_JS) + "\nconsole.log(JSON.stringify(ICONS));")


def _icon_prelude() -> str:
    return (
        _extract("escapeHtml", SHELL_JS) + "\n"
        + _extract_var("ICONS", SHELL_JS) + "\n"
        + _extract_var("SLOT_ICONS", SHELL_JS) + "\n"
        + _extract_var("PREP_SECTION_ICONS", SHELL_JS) + "\n"
        + _extract_var("AISLE_ICONS", SHELL_JS) + "\n"
        + "\n".join(_extract(n, SHELL_JS) for n in (
            "iconTileHtml", "slotIconTileHtml", "prepSectionTileHtml", "aisleIconHtml", "statusIconName", "statusIconHtml"))
        + "\n"
    )


def _css_rule(selector: str) -> str:
    start = SHELL_CSS.index(selector + " {")
    return SHELL_CSS[start:SHELL_CSS.index("}", start)]


# ---------------------------------------------------------------------------
# The icon set
# ---------------------------------------------------------------------------

NEW_ICONS = ("bowl", "apple", "check", "loop", "book", "snowflake", "drop", "oven", "slowCooker",
             "knife", "bottle", "whisk", "freezerBox", "star", "container", "moon",
             "leaf", "fish", "bread", "jar", "can")


@_needs_node
def test_every_new_icon_is_in_icons_drawn_in_the_one_style():
    icons = _icons()
    for name in NEW_ICONS + ("sunrise", "pot", "flame", "clock", "arrow", "bag"):
        svg = icons.get(name)
        assert svg, f"ICONS.{name} is missing"
        assert 'stroke="currentColor"' in svg and 'stroke-width="2.2"' in svg, name
        assert 'stroke-linecap="round"' in svg and 'stroke-linejoin="round"' in svg, name
        assert 'fill="none"' in svg, name


def test_the_tile_ink_pairs_have_dark_values():
    for token in ("--celadon-tint", "--celadon-label", "--sand", "--sand-deep", "--apricot-label"):
        assert THEME_CSS.count(f"{token}:") >= 3, f"{token} needs light, dark (media) and forced-dark values"


def test_the_tile_is_44px_at_the_control_radius_in_tokens_only():
    tile = _css_rule(".icon-tile")
    assert "width: 44px" in tile and "height: 44px" in tile
    assert "border-radius: var(--radius-control)" in tile
    assert "background: var(--celadon-tint)" in tile and "color: var(--celadon-label)" in tile
    assert ".icon-tile.is-sand { background: var(--sand); color: var(--apricot-label); }" in SHELL_CSS
    assert ".icon-tile svg { width: 22px; height: 22px; display: block; }" in SHELL_CSS
    assert ".status-icon svg { width: 15px; height: 15px; display: block; }" in SHELL_CSS
    assert ".gro-aisle-icon svg { width: 16px; height: 16px; display: block; }" in SHELL_CSS


def test_no_tile_on_the_spruce_cards():
    # The prep day card and the night-before checklist are spruce: no
    # celadon tile there (DESIGN_SYSTEM §1).
    for name in ("cookPrepDayCardHtml", "cookBedCardHtml"):
        body = _extract(name, SHELL_JS)
        assert "IconTileHtml" not in body and "icon-tile" not in body and "prepSectionTileHtml" not in body, name


# ---------------------------------------------------------------------------
# M2: the meal sheet
# ---------------------------------------------------------------------------

@_needs_node
def test_the_meal_sheet_is_three_pills_and_the_recipe_pill():
    out = _plan("console.log(JSON.stringify({ html: planRowSheetHtml(DAYS[2], 'dinner'), icons: ICONS }));")
    html = out["html"]
    acts = re.findall(r'<button type="button" class="plan-act" data-plan-opt="(\w+)">', html)
    assert acts == ["swap", "tweak", "move"]
    for word in ("Swap", "Tweak", "Move"):
        assert f'<span class="plan-act-label">{word}</span>' in html
    assert html.count('class="plan-acts"') == 1
    # The long lines came off.
    for gone in ("Pick something else", "Change a part, or tell me what to change", "Put it on another day",
                 "quiet-opt", "quiet-link"):
        assert gone not in html, gone
    # The recipe: the apricot pill with the book on the left, the arrow on the right.
    m = re.search(r'<button type="button" class="plan-recipe" data-plan-opt="recipe">(.*?)</button>', html)
    assert m, html
    inner = m.group(1)
    assert inner.index('plan-recipe-icon') < inner.index('See the recipe') < inner.index('plan-recipe-arrow')
    assert out["icons"]["book"] in inner and out["icons"]["arrow"] in inner
    # The pills sit above the recipe.
    assert html.index("plan-acts") < html.index("plan-recipe")


def test_the_meal_sheet_css_matches_m2():
    act = _css_rule(".plan-act")
    for want in ("flex: 1 1 0", "min-height: 48px", "border: 1.5px solid transparent",
                 "border-radius: var(--radius-pill)", "background: var(--blush)",
                 "font-size: 14px", "font-weight: 700"):
        assert want in act, want
    assert ".plan-act-icon { display: flex; flex: 0 0 auto; color: var(--celadon-label); }" in SHELL_CSS
    assert ".plan-act-icon svg { width: 18px; height: 18px; display: block; }" in SHELL_CSS
    rec = _css_rule(".plan-recipe")
    for want in ("width: 100%", "min-height: 58px", "border-radius: var(--radius-pill)",
                 "background: var(--apricot)", "color: var(--on-accent-ink)",
                 "font-family: var(--font-display)", "font-size: 16px", "font-weight: 700"):
        assert want in rec, want
    assert "#" not in act and "#" not in rec, "tokens only"


# ---------------------------------------------------------------------------
# I1: the meal slots and the status words
# ---------------------------------------------------------------------------

@_needs_node
def test_a_plan_tile_is_its_plate_and_no_slot_icon():
    """Bright Dusk (Emily, 2026-11-05) took I1 off Plan: every dish shows its
    plate, a tile with no dish is words only, and the line under the name
    carries no icon. No filler pictures. Cook's rows keep theirs until its
    stage."""
    out = _plan(
        "console.log(JSON.stringify({\n"
        "  lunch: planSlotRowHtml(DAYS[2], 2, 'lunch', 'Lunch'),\n"
        "  dinner: planSlotRowHtml(DAYS[2], 2, 'dinner', 'Dinner'),\n"
        "  open: planSlotRowHtml(DAYS[3], 3, 'dinner', 'Dinner') }));"
    )
    for key in ("lunch", "dinner", "open"):
        assert "slot-tile" not in out[key] and "status-icon" not in out[key], key
    assert ">Dinner · 1 hr<" in out["dinner"]
    assert 'class="tile tile-text wk-qrow is-quiet"' in out["open"], "no plate, words only"


@_needs_node
def test_every_cook_row_carries_its_slot_tile_and_stays_the_same_size():
    script = (
        _icon_prelude()
        + "var COOK_ROW_CHECK = '<svg></svg>';\n"
        + "function cookSlotLabel(s) { return s.charAt(0).toUpperCase() + s.slice(1); }\n"
        + "function cookRowCookName() { return ''; }\n"
        + _extract("cookTodayRowHtml", SHELL_JS) + "\n"
        + "var rows = [['breakfast', true], ['lunch', false], ['snack', false], ['dinner', false], ['snack2', false]]"
        + ".map(function (r, i) { return cookTodayRowHtml({ slot: r[0], done: r[1], idx: i, title: 'X', right: '20 min' }, i === 1); });\n"
        + "console.log(JSON.stringify({ rows: rows, icons: ICONS }));"
    )
    out = _run(script)
    want = ["sunrise", "bowl", "apple", "pot", "apple"]
    for html, icon in zip(out["rows"], want):
        assert '<span class="icon-tile slot-tile" aria-hidden="true">' + out["icons"][icon] + "</span>" in html
        assert html.count("icon-tile") == 1, "the slot tile is the one addition"
        # No status icon on Cook's rows: the minutes stay plain text (Option A).
        assert "status-icon" not in html
    row = _css_rule(".cook-today-row")
    assert "min-height: 64px" in row and "padding: 10px 0" in row, "44px tile + 10px + 10px = the same 64px row"


# ---------------------------------------------------------------------------
# I2: the prep list's sections and Shop's aisles
# ---------------------------------------------------------------------------

PREP_WANT = {
    "thaw": ("snowflake", "celadon"), "soak": ("drop", "celadon"), "oven": ("oven", "sand"),
    "stove": ("flame", "sand"), "slow_cooker": ("slowCooker", "sand"), "chop": ("knife", "celadon"),
    "sauces": ("bottle", "celadon"), "chill": ("whisk", "celadon"), "freezer": ("freezerBox", "celadon"),
    "special": ("star", "sand"), "pack": ("container", "sand"),
}


def test_every_prep_section_the_server_knows_has_an_icon():
    assert set(prep_list.SECTION_ORDER) == set(PREP_WANT)


@_needs_node
def test_each_prep_section_and_made_on_the_night_has_its_tile():
    keys = prep_list.SECTION_ORDER
    script = (
        _icon_prelude()
        + _extract_var("PREP_LIST_ICONS", SHELL_JS) + "\n"
        + _extract("prepListItem", SHELL_JS) + "\n"
        + "function cookSessionItemHtml() { return ''; }\n"
        + _extract("cookPrepSectionHtml", SHELL_JS) + "\n"
        + _extract("cookPrepFreshHtml", SHELL_JS) + "\n"
        + "var session = { date: '2026-11-01', weekly_plan_id: 1, items: [], made_fresh_line: 'x' };\n"
        + "var keys = " + json.dumps(keys) + ";\n"
        + "console.log(JSON.stringify({ icons: ICONS,\n"
        + "  secs: keys.map(function (k) { return cookPrepSectionHtml(session, { key: k, title: k, line: 'l', step_keys: [] }, false, []); }),\n"
        + "  fresh: cookPrepFreshHtml(session, [{ key: 'a', title: 'Rice', state: 'off' }]) }));"
    )
    out = _run(script)
    for key, html in zip(keys, out["secs"]):
        icon, tone = PREP_WANT[key]
        cls = "icon-tile" + (" is-sand" if tone == "sand" else "") + " prep-sec-tile"
        assert f'<span class="{cls}" aria-hidden="true">' + out["icons"][icon] + "</span>" in html, key
        assert html.index("prep-sec-tile") < html.index("prep-sec-text")
    assert '<span class="icon-tile is-sand prep-sec-tile" aria-hidden="true">' + out["icons"]["moon"] in out["fresh"]
    assert ".prep-fresh .icon-tile.is-sand { background: var(--sand-deep); }" in SHELL_CSS


@_needs_node
def test_each_shop_aisle_header_has_its_icon():
    script = (
        _icon_prelude()
        + _extract_var("GRO_SECTION_ORDER", SHELL_JS) + "\n"
        + _extract_var("GRO_AISLE_LABELS", SHELL_JS) + "\n"
        + _extract("groAisleOf", SHELL_JS) + "\n"
        + "function groLineHtml(it) { return '<div class=\"gro-line\">' + it.name + '</div>'; }\n"
        + _extract("groAisleGroupsHtml", SHELL_JS) + "\n"
        + "var items = GRO_SECTION_ORDER.map(function (c) { return { name: c, category: c === 'meat/seafood' ? 'meat' : c }; });\n"
        + "console.log(JSON.stringify({ icons: ICONS, order: GRO_SECTION_ORDER, html: groAisleGroupsHtml(items, {}) }));"
    )
    out = _run(script)
    want = {"produce": "leaf", "dairy": "bottle", "meat/seafood": "fish", "pantry": "jar",
            "canned": "can", "frozen": "snowflake", "other": "bag"}
    assert set(out["order"]) == set(want), "a new aisle needs an icon"
    for aisle, icon in want.items():
        head = re.search(r'<div class="gro-aisle" data-aisle="' + re.escape(aisle) + r'">(.*?)</div>', out["html"])
        assert head, aisle
        assert head.group(1).startswith('<span class="gro-aisle-icon" aria-hidden="true">' + out["icons"][icon]), aisle
        assert '<span class="gro-eyebrow">' in head.group(1)
