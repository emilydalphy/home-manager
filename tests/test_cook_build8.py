"""Cook root, Build 8 of the screen-by-screen redesign (Emily, 2026-09-04 and
2026-09-11): empty means empty, the entry points as quiet rows. UPDATED
2026-09-13 for the shelf design (the entry-point rows live in the More
sheet), and 2026-11-01 for Option A, which took the shelf off too — see
tests/test_cook_option_a.py for the root as it is now."""
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SHELL_JS = (REPO / "static" / "shell.js").read_text(encoding="utf-8")
SHELL_CSS = (REPO / "static" / "shell.css").read_text(encoding="utf-8")


def _fn(name):
    start = SHELL_JS.index("  function %s(" % name)
    return SHELL_JS[start:SHELL_JS.index("\n  }\n", start)]


def test_the_week_is_not_on_cooks_root():
    # The day list came out with the shelf (2026-09-13); the shelf came out
    # with Option A (2026-11-01). Cook's root is today; the week is Plan's.
    assert "function cookRestOfWeekHtml(" not in SHELL_JS
    assert "function cookShelfTileHtml(" not in SHELL_JS


def test_empty_means_empty_and_names_the_next_cook():
    # A day with nothing on it is "Night off", then the next cook as a tile.
    off = _fn("cookNightOffHtml")
    assert ">Night off</h2>" in off and "cookNextUpTileHtml(meals, todayIso)" in off
    nxt = _fn("kitchenNextCookLine")
    assert "'Next: '" in nxt


def test_the_two_italic_headings_left_the_root_and_prep_days_is_not_on_it():
    root = _fn("renderKitchen")
    assert "cookDefrostLinkHtml()" not in root and "cookAheadAskLinkHtml()" not in root
    # The "Prep days" row left the root with the root band (2026-09-11):
    # it is a setting, and Preferences already has that row.
    assert "function cookPrepSessionsHtml(" not in SHELL_JS  # the next session is a quiet line now
    assert '<span class="kit-row-title">Prep days</span>' not in SHELL_JS
    assert 'data-cook="prep-days"' not in SHELL_JS
    assert "Prep ahead? " not in SHELL_JS


def test_inventory_and_recipes_are_rows_in_one_card():
    # In the More sheet since 2026-09-13; same rows, same card.
    tiles = _fn("cookMoreRowsHtml")
    assert '<div class="tiles in-sheet">' in tiles
    # Recipes, Add from a link, Add from a cookbook (recipe photo import,
    # 2026-09-13). Inventory was the fourth until 2026-10-26, when it left
    # for its own tab (Kitchen, TABS key 'home' — Loop Board C1).
    assert tiles.count("tileHtml(") == 3
    assert ".kit-rows {" in SHELL_CSS and ".kit-row {" in SHELL_CSS
