"""
Bright Dusk, stage 2: Shop and Kitchen as the boards draw them (Z-Shop,
Z-AddItem, Z-EmptyShop, Z-Kitchen). The flows are the older tests' job; this
file holds the look: what the boards say, run through the app's own builders.
"""
import re
from pathlib import Path

import nodeharness
from shop_harness import SHELL_CSS, SHELL_JS, needs_node, bright_dusk_parts

REPO = Path(__file__).resolve().parent.parent
KA_JS = (REPO / "static" / "kitchen-add.js").read_text(encoding="utf-8")
INVENTORY = (REPO / "static" / "inventory.html").read_text(encoding="utf-8")

LEGACY = re.compile(
    r"--(?:spruce|celadon|sand|ivory|on-spruce|apricot-(?:label|light|deep|rule)"
    r"|ink-(?:strong|muted|placeholder|inactive|done)|hairline-(?:strong|deep)|urgent|on-accent-ink|good-ink)"
)


def _fn(name):
    start = SHELL_JS.index("function " + name + "(")
    end = SHELL_JS.index("\n  }\n", start)
    return SHELL_JS[start:end]


def _rule(selector):
    start = SHELL_CSS.index("\n" + selector + " {") + 1
    return SHELL_CSS[start:SHELL_CSS.index("}", start)]


def test_shop_and_kitchen_css_say_no_legacy_names():
    """DESIGN_SYSTEM section 1: a screen moves to the canonical names at its
    stage. Dormant dark rules aside, nothing on these two screens is left."""
    bad = []
    for m in re.finditer(r"([^{}]*)\{([^{}]*)\}", SHELL_CSS):
        sel = m.group(1).split("*/")[-1]
        if re.search(r"\.(gro-|home-)|#gro-|#home-", sel) and "data-theme" not in sel:
            if LEGACY.search(m.group(2)):
                bad.append(sel.strip())
    assert bad == []


def test_dusk_on_these_screens_is_only_use_soon():
    """Dusk is for the tab bar, Use soon, timers and the welcome."""
    fills = []
    for m in re.finditer(r"([^{}]*)\{([^{}]*)\}", SHELL_CSS):
        sel = m.group(1).split("*/")[-1]
        if re.search(r"\.(gro-|home-)|#gro-|#home-", sel) and "var(--dusk)" in m.group(2):
            fills.append(sel.strip())
    assert fills == [".home-soon"]
    assert "var(--dusk)" not in KA_JS and "var(--dusk)" not in INVENTORY.split("Dark mode: the leftovers")[0]


def test_the_roots_heads_are_the_boards():
    shop = _fn("buildGroceryPanel")
    assert "title: 'Shop', noGear: true, tools: groAddButtonHtml()" in shop
    home = _fn("buildHomePanel")
    assert "title: 'Kitchen', noGear: true" in home and "label: 'Snap a shelf'" in home


def test_kitchen_opens_switcher_then_use_soon_then_tiles():
    body = _fn("homeBodyHtml")
    tail = body[body.rindex("return homeSegHtml"):]
    assert tail.index("homeSegHtml") < tail.index("homeUseSoonHtml") < tail.index("homeShelfHtml")


def test_a_tile_row_is_a_22px_ring_in_a_44px_box():
    box = _rule(".gro-box")
    assert "width: 44px" in box and "height: 44px" in box and "margin: 0 -11px" in box
    assert "min-height: 44px" in _rule(".gro-row")
    assert "min-height: 44px" in _rule(".gro-line-open")
    assert ".gro-box.checked .tick-ring { background: var(--apricot); box-shadow: none; }" in SHELL_CSS


def test_a_segment_taps_the_whole_track():
    theme = (REPO / "static" / "theme.css").read_text(encoding="utf-8")
    assert '.seg-btn::after { content: ""; position: absolute; inset: -3px 0; }' in theme


def test_a_wrapped_name_is_struck_line_by_line():
    assert ".gro-row.done .gro-name { text-decoration-color: currentColor; }" in SHELL_CSS
    assert ".gro-row .gro-name::after" not in SHELL_CSS


def test_the_kitchen_sheet_keeps_every_way_in_behind_the_plus():
    assert "fromMenu: !!opts.menuBack" in KA_JS
    assert 'data-ka="close"' in KA_JS and "if (what === 'close') { close(); return; }" in KA_JS
    # No italics, no caps, no spruce-era fills in its styles.
    style = KA_JS[KA_JS.index("var STYLE ="):KA_JS.index("var sheetEl = null;")]
    assert "italic" not in style and "uppercase" not in style and not LEGACY.search(style)


def test_inventory_says_no_legacy_names_outside_its_dormant_dark_rules():
    light = INVENTORY[INVENTORY.index("<style>"):INVENTORY.index("Dark mode: the leftovers")]
    assert not LEGACY.search(light)
    assert "text-transform" not in light and "rem;" not in light
    sizes = {int(n) for n in re.findall(r"font-size: (\d+)px", light)}
    assert sizes <= {32, 26, 16, 14, 13, 11}


@needs_node
def test_the_switcher_needs_two_to_choose_between_and_scrolls_past_three():
    script = (
        "function escapeHtml(s){return String(s);}\n" + bright_dusk_parts() +
        "console.log(segHtml([{label:'a',on:true},{label:'b'}],{cls:'gro-seg is-long'}));"
    )
    done = nodeharness.run_node(script)
    assert done.returncode == 0, done.stderr
    assert 'class="seg gro-seg is-long"' in done.stdout and 'seg-btn is-on' in done.stdout
    chips = _fn("groStoreChipsHtml")
    assert "if (items.length < 2) return '';" in chips
    assert "items.length > 3 ? ' is-long'" in chips
