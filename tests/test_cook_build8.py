"""Cook root, Build 8 of the screen-by-screen redesign (Emily, 2026-09-04 and
2026-09-11): the week one line per day, empty means empty, the entry points
as rows above the fold."""
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SHELL_JS = (REPO / "static" / "shell.js").read_text(encoding="utf-8")
SHELL_CSS = (REPO / "static" / "shell.css").read_text(encoding="utf-8")


def _fn(name):
    start = SHELL_JS.index("  function %s(" % name)
    return SHELL_JS[start:SHELL_JS.index("\n  }\n", start)]


def test_the_rest_of_the_week_is_one_line_per_day_three_days_then_the_rest_by_name():
    rest = _fn("cookRestOfWeekHtml")
    assert "shown.map(cookRestDayRowHtml)" in rest
    assert "groups.slice(0, KITCHEN_REST_VISIBLE)" in rest
    assert "'Show ' + dayNameShort(first.date)" in rest
    row = _fn("cookRestDayRowHtml")
    assert 'data-cook="focus"' in row  # each cook is still a way into its recipe
    assert "reheat" in row and "Nothing to cook" in row


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
    offer = _fn("cookPrepSessionsHtml")
    assert '<span class="kit-row-title">Prep days</span>' not in offer
    assert 'data-cook="prep-days"' not in SHELL_JS
    assert "Prep ahead? " not in SHELL_JS


def test_inventory_and_recipes_are_rows_in_one_card():
    tiles = _fn("kitchenTilesHtml")
    assert '<div class="kit-rows">' in tiles
    assert tiles.count('class="kit-row"') == 3
    assert ".kit-rows {" in SHELL_CSS and ".kit-row {" in SHELL_CSS
