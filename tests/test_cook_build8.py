"""Cook root, Build 8 of the screen-by-screen redesign (Emily, 2026-09-04 and
2026-09-11): empty means empty, the entry points as quiet rows. UPDATED
2026-09-13 for the shelf design: the week is the shelf (not one line per
day), and the entry-point rows live in the More sheet — see
tests/test_cook_shelf.py for the shelf, the Tonight card and the dock."""
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SHELL_JS = (REPO / "static" / "shell.js").read_text(encoding="utf-8")
SHELL_CSS = (REPO / "static" / "shell.css").read_text(encoding="utf-8")


def _fn(name):
    start = SHELL_JS.index("  function %s(" % name)
    return SHELL_JS[start:SHELL_JS.index("\n  }\n", start)]


def test_the_rest_of_the_week_is_the_shelf_one_tile_per_night():
    assert "function cookRestOfWeekHtml(" not in SHELL_JS, "the day list came out with the shelf"
    tile = _fn("cookShelfTileHtml")
    assert 'data-cook="focus"' in tile  # each night with a meal is still a way into it
    assert "'Leftovers'" in tile and "'—'" in tile  # a reheat night, and an empty one


def test_empty_means_empty_and_names_the_next_cook():
    # Since the root band (2026-09-11) the empty day is the shared empty
    # moment, with the next cook as its second line.
    today = _fn("kitchenCookingTodayHtml")
    assert "emptyMomentHtml('pot', 'Nothing to cook tonight.', kitchenNextCookLine(meals, todayIso))" in today
    nxt = _fn("kitchenNextCookLine")
    assert "'Next: '" in nxt


def test_the_two_italic_headings_left_the_root_and_prep_days_is_not_on_it():
    root = _fn("renderKitchen")
    assert "cookDefrostLinkHtml()" not in root and "cookAheadAskLinkHtml()" not in root
    # The "Prep days" row left the root with the root band (2026-09-11):
    # it is a setting, and Preferences already has that row.
    assert "function cookPrepSessionsHtml(" not in SHELL_JS  # the next session is a get-ready row now
    assert '<span class="kit-row-title">Prep days</span>' not in SHELL_JS
    assert 'data-cook="prep-days"' not in SHELL_JS
    assert "Prep ahead? " not in SHELL_JS


def test_inventory_and_recipes_are_rows_in_one_card():
    # In the More sheet since 2026-09-13; same rows, same card.
    tiles = _fn("cookMoreRowsHtml")
    assert '<div class="kit-rows">' in tiles
    # Recipes, Add from a link, Add from a cookbook (recipe photo import,
    # 2026-09-13), Inventory.
    assert tiles.count('class="kit-row"') == 4
    assert ".kit-rows {" in SHELL_CSS and ".kit-row {" in SHELL_CSS
