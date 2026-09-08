"""
Kitchen is the cook's tab, and "what Pomona knows about us" is a sheet.

Emily's approved design, 2026-09-08 (branch
`flows-4-kitchen-and-preferences`). Two moves, and this file guards both:

1. **Kitchen answers "what's cooking, and what's in the house?"** Its root
   is Cooking today / Prep sessions / The rest of the week, plus two quiet
   tiles (Inventory, Recipes). Cook mode — the focused single-meal screen,
   its steps, its cook-ahead picker and its "Mark it cooked" — is a STEP of
   this tab now rather than a state of the Meals tab, so Meals lost its
   Plan | Cook segmented control and every entry point into cooking aims at
   Kitchen with a `cookFocus` target.
2. **Preferences is a sheet behind a gear** in the header of every root
   screen. The "What we know" hero and the "Something not working?" tile
   left Kitchen for it.

These are SOURCE assertions, the same kind and for the same reason as
tests/test_frontend_restored_2026_09_08.py: shell.js has no JS test harness
in this repo, and a marker that is present but mis-wired is still a far
better failure mode than a marker that is gone. Where a string is
user-facing copy it is asserted verbatim — if the copy is deliberately
reworded, update the constant here in the same commit and say so; do not
delete the test.

The one behavioural half is at the bottom: the moves payload really does
name the Kitchen tab, which is what makes Today's "Cook this" land in cook
mode rather than on a tab that no longer has a Cook state.
"""

from __future__ import annotations

import re
from datetime import date, datetime, time, timedelta
from pathlib import Path

import pytest

from app.tools import moves as moves_mod

REPO = Path(__file__).resolve().parent.parent
SHELL_JS = (REPO / "static" / "shell.js").read_text(encoding="utf-8")
SHELL_CSS = (REPO / "static" / "shell.css").read_text(encoding="utf-8")


def _assert_in(needle: str, haystack: str, what: str, where: str) -> None:
    assert needle in haystack, (
        f"{what} is missing from static/{where}.\nExpected to find: {needle!r}"
    )


def _function(name: str, source: str = SHELL_JS) -> str:
    """One brace-balanced `function name(...) {...}`, so an assertion can be
    scoped to the renderer it is about rather than to the whole file."""
    start = source.index(f"function {name}(")
    i = source.index("{", start)
    depth, j = 0, i
    while True:
        if source[j] == "{":
            depth += 1
        elif source[j] == "}":
            depth -= 1
            if depth == 0:
                break
        j += 1
    return source[start : j + 1]


# --- 1. the Kitchen root --------------------------------------------------

def test_the_kitchen_root_is_the_cooks_tab():
    """Three sections, in this order, and the day above them."""
    root = _function("renderKitchen")
    for call in ("kitchenCookingTodayHtml(rows)", "cookPrepSessionsHtml(data)",
                 "cookRestOfWeekHtml(", "kitchenTilesHtml()"):
        assert call in root, f"the Kitchen root no longer renders {call}"
    _assert_in("Cooking today", SHELL_JS, "the Cooking today eyebrow", "shell.js")
    _assert_in("Prep sessions", SHELL_JS, "the Prep sessions eyebrow", "shell.js")
    _assert_in("The rest of the week", SHELL_JS, "the rest-of-week eyebrow", "shell.js")


def test_the_kitchen_root_says_the_day_and_the_count():
    """"Monday · 1 cook tonight" — and "today" rather than "tonight" the
    moment a cook left today is not a dinner."""
    fn = _function("kitchenSubtitle")
    assert "' tonight'" in fn and "' today'" in fn
    assert "slot === 'dinner'" in fn, "the tonight/today test no longer reads the slot"
    _assert_in("nothing to cook today", SHELL_JS, "the empty-day subtitle", "shell.js")


def test_a_cooking_today_line_carries_the_start_by_and_the_badge():
    line = _function("kitchenTodayLine")
    assert "'start by '" in line, "the start-by chip is no longer read off the move"
    rows = _function("kitchenTodayRows")
    assert "'cooked'" in rows and "'Reheat'" in rows and "'Cook'" in rows, (
        "the Cook / cooked / Reheat badge is gone"
    )


def test_the_rest_of_the_week_collapses_after_three():
    fn = _function("cookRestOfWeekHtml")
    _assert_in("var KITCHEN_REST_VISIBLE = 3;", SHELL_JS, "the collapse point", "shell.js")
    assert "' more '" in fn and "'cooks'" in fn, "the '+ 3 more cooks' link is gone"
    _assert_in('data-cook="rest-more"', SHELL_JS, "the expand control", "shell.js")


def test_the_two_quiet_tiles_are_inventory_and_recipes():
    tiles = _function("kitchenTilesHtml")
    assert ">Inventory<" in tiles and ">Recipes<" in tiles
    assert "kit-tile-quiet" in tiles, "Kitchen's tiles must stay quiet (no badge, muted stroke)"
    assert "btn-primary" not in tiles and "apricot" not in tiles, (
        "Kitchen's root has no primary action and no apricot (DESIGN_SYSTEM Rule 5)"
    )


def test_what_we_know_and_the_snw_tile_left_the_kitchen_tab():
    """Both moved into Preferences. The Kitchen renderer must not render
    either one, or the tab is the settings drawer again."""
    root = _function("renderKitchen")
    assert "snwTile()" not in root, "the Something-not-working tile is back on Kitchen"
    assert "What we know" not in root, "the What-we-know hero is back on Kitchen"
    assert "kit-hero" not in root and "kitChip" not in SHELL_JS, (
        "the household hero and its count chips are back"
    )


def test_the_kitchen_root_keeps_the_cook_views_two_quiet_links():
    root = _function("renderKitchen")
    assert "cookDefrostLinkHtml()" in root and "cookAheadAskLinkHtml()" in root
    _assert_in("Something in the freezer?", SHELL_JS, "the freezer re-ask", "shell.js")
    _assert_in("Cooking ahead?", SHELL_JS, "the cook-ahead re-ask", "shell.js")


# --- 2. cook mode is a step of Kitchen ------------------------------------

def test_cook_mode_lives_in_the_kitchen_panel():
    _assert_in("function cookPanel() { return panels['kitchen']; }", SHELL_JS,
               "cook mode's panel", "shell.js")
    _assert_in('id="kit-cook-view"', SHELL_JS, "the cook step's container", "shell.js")
    assert "week-cook-view" not in SHELL_JS, (
        "the Meals tab still has a cook view — cooking is Kitchen's step now"
    )
    assert "week-cook-view" not in SHELL_CSS


def test_the_back_link_says_kitchen():
    """Back links go UP a level by name and never call history.back()."""
    _assert_in('data-cook="exit-focus">&lsaquo; Kitchen</button>', SHELL_JS,
               "the focused screen's back link", "shell.js")
    _assert_in('data-cook="exit-session">&lsaquo; Kitchen</button>', SHELL_JS,
               "the prep session's back link", "shell.js")
    assert "Back to the week" not in SHELL_JS, "a cook screen still points back at Meals"


def test_cook_mode_keeps_its_apricot_and_its_end_state():
    """Kitchen's root has no apricot; cook mode's "Mark it cooked" IS the
    tab's one apricot (the rule that changed with this slice)."""
    _assert_in("Mark it cooked", SHELL_JS, "the end-of-cook primary", "shell.js")
    _assert_in("function cookFocusEndHtml(", SHELL_JS, "the end-of-cook panel", "shell.js")
    _assert_in("function cookFocusHtml(", SHELL_JS, "the focused cook screen", "shell.js")
    _assert_in("cookAheadHtml(meal)", SHELL_JS, "the cook-ahead picker", "shell.js")
    _assert_in("cookPrepCutHtml(data, meal)", SHELL_JS, "the prep-cut offer", "shell.js")


def test_no_call_site_still_asks_for_the_meals_cook_state():
    """`mealsView`/`mealsFocus` were the Meals tab's cook plumbing. Every one
    of them is a bug now: the Meals panel has no cook state to switch to, so
    the tap would land on the plan and do nothing."""
    for dead in ("mealsView:", "mealsFocus:", "setMealsView("):
        assert dead not in SHELL_JS, f"{dead!r} is still a live call in static/shell.js"
    assert "mealsView" not in (REPO / "app" / "tools" / "moves.py").read_text().replace(
        '# a state of Meals ({tab: "week", mealsView: "cook"}) until', ""
    ), "moves.py still emits the old Meals cook target"


@pytest.mark.parametrize(
    "entry_point",
    [
        # Today's Next up card and its move lines (runTodayMoveAction).
        "activateTab('kitchen', true, { cookFocus: target.cookFocus })",
        # Meals' Day/Meal "Cook this" (wireMealsStep).
        "activateTab('kitchen', true, {\n          cookFocus: {",
        # Grocery's shop-done handoff.
        "activateTab('kitchen', true, { cookFocus: true })",
    ],
)
def test_every_former_cook_entry_point_passes_cookfocus(entry_point):
    _assert_in(entry_point, SHELL_JS, "a cook-mode entry point", "shell.js")


def test_the_focus_target_is_still_resolved_the_same_way():
    """id -> date+slot -> title -> nothing. Never a different meal than the
    one that was tapped (42d422a)."""
    fn = _function("cookResolveFocusIndex")
    assert "target.entryId" in fn and "target.date && target.slot" in fn and "target.title" in fn
    assert "return null;" in fn, "an unresolvable target must land on the root, not on a guess"
    _assert_in("if (tab.kitchen && opts && opts.cookFocus) kitchenEnterCook(opts.cookFocus);",
               SHELL_JS, "the cookFocus entry in activateTab", "shell.js")


def test_the_meals_tab_has_no_plan_cook_control_any_more():
    for dead in ("meals-seg", "data-meals-view", "ICONS.flame + '<span>Cook</span>'"):
        assert dead not in SHELL_JS, f"the Plan | Cook control is back ({dead!r})"
    assert ".meals-seg-btn" not in SHELL_CSS, "the Plan | Cook control's style is back"


def test_cook_voice_is_still_gated_after_the_move():
    """The hands-free code moved tabs with the screen; it must still be
    behind COOK_VOICE_ENABLED, and its status panel must render with the
    mics rather than on a screen that no longer exists."""
    _assert_in("var COOK_VOICE_ENABLED = false;", SHELL_JS, "the voice flag", "shell.js")
    panel = _function("cookVoicePanelHtml")
    assert "COOK_VOICE_ENABLED ?" in panel
    assert SHELL_JS.count('data-cook="voice"') == 2, (
        "expected exactly two cook mics (the recipe's and the prep section's)"
    )
    for pos in [m.start() for m in re.finditer(r'data-cook="voice"', SHELL_JS)]:
        assert "COOK_VOICE_ENABLED" in SHELL_JS[max(0, pos - 250) : pos]


# --- 3. the gear, and the Preferences sheet -------------------------------

def test_the_gear_is_in_the_header_of_every_root_screen():
    """Today, Meals, Grocery, Kitchen — and nowhere deeper."""
    assert "prefsGearHtml()" in _function("buildTodayPanel"), "Today has no gear"
    assert "prefsGearHtml()" in _function("buildKitchenPanel"), "Kitchen has no gear"
    assert "prefsGearHtml()" in _function("buildGroceryPanel"), "Grocery has no gear"
    assert "prefsGearRowHtml('meals-gear-row')" in _function("buildWeekPanel"), "Meals has no gear"
    # ...and it is hidden on the deeper steps of the two tabs that have any.
    assert "gearRow.hidden = !onRoot;" in _function("renderMealsStep"), (
        "the gear still shows on Meals' Day and Meal steps"
    )
    assert "groGear.hidden = screen === 'shop';" in _function("renderGrocery"), (
        "the gear still shows while shopping a store"
    )


def test_the_preferences_sheet_says_what_it_is():
    _assert_in(">Preferences<", SHELL_JS, "the sheet title", "shell.js")
    _assert_in("What Pomona knows about your household", SHELL_JS, "the sheet subtitle", "shell.js")
    assert "apricot" not in _function("renderPrefsRows"), "Preferences has no apricot"


@pytest.mark.parametrize(
    "row",
    ["Who’s here", "Your rhythm", "Prep days", "How you eat", "Stores"],
)
def test_the_five_knowledge_rows(row):
    _assert_in(row, SHELL_JS, "a Preferences row", "shell.js")


def test_the_second_group_is_a_way_out_of_trouble_and_a_way_out_of_the_app():
    rows = _function("renderPrefsRows")
    assert "snwTile()" in rows, "Preferences no longer offers 'Something not working?'"
    assert ">Sign out<" in rows, "Preferences no longer offers Sign out"
    signout = SHELL_JS[SHELL_JS.index("if (what === 'signout')") :][:400]
    assert "window.confirm(" in signout, "signing out asks first"
    assert "'/logout'" in signout, "sign out no longer hits GET /logout"
    _assert_in("passphrase", SHELL_JS, "the sign-out sub-line ('passphrase', never 'password')",
               "shell.js")
    assert "your password" not in SHELL_JS.lower()


def test_each_row_opens_the_tab_of_what_we_know_that_owns_the_answer():
    rows = SHELL_JS[SHELL_JS.index("var PREFS_ROWS = ["):]
    rows = rows[: rows.index("];")]
    for tab in ("'people'", "'rhythm'", "'rhythm/prep-days'", "'taste'", "'stores'"):
        assert f"tab: {tab}" in rows, f"no Preferences row opens What we know's {tab}"
    _assert_in("openKitchenSheet('memory', target.getAttribute('data-tab'))", SHELL_JS,
               "the row -> What we know handoff", "shell.js")


def test_the_row_subtitles_are_one_cached_read():
    """One /api/memory per open, dropped when a chat turn writes to it —
    not a fetch per row and not a stale sheet."""
    load = _function("loadPrefs")
    assert "if (prefsState.memory) { renderPrefsRows(); return; }" in load
    assert load.count("fetch(") == 1, "Preferences should cost one read, not several"
    assert "'/api/memory'" in load
    assert "prefsInvalidate();" in _function("refreshStaleTabsFromActions")


def test_the_subtitles_read_the_household_back_plainly():
    """Not "dinner_window: 6_8" — the answer, the way a person says it."""
    _assert_in("'dinner around 7'", SHELL_JS, "the dinner-window wording", "shell.js")
    _assert_in("'plan ready '", SHELL_JS, "the planning-anchor wording", "shell.js")
    _assert_in("'leftovers welcome'", SHELL_JS, "the leftovers wording", "shell.js")
    _assert_in("' snack'", SHELL_JS, "the snacks-a-week wording", "shell.js")
    _assert_in("'Not set yet'", SHELL_JS, "the unanswered-question wording", "shell.js")


def test_the_ask_bar_hint_on_kitchen():
    _assert_in("kitchen: 'What\\u2019s in the fridge that needs using?'", SHELL_JS,
               "Kitchen's ask-bar hint", "shell.js")


# --- 4. the backend half --------------------------------------------------

def test_a_cook_move_targets_the_kitchen_tab(monkeypatch):
    """The payload Today's "Cook this" acts on. It named the Meals tab's
    Cook state until 2026-09-08; a stale target here is a tap that lands on
    the plan and does nothing."""
    today = date.today()
    view = {
        "weekly_plan_id": 1,
        "status": "approved",
        "meals": [{
            "entry_id": 42, "date": today.isoformat(), "slot": "dinner",
            "meal": "Sesame Salmon Bowls", "cooked_status": "pending",
            "is_leftovers": False, "prep_time_minutes": 10, "cook_time_minutes": 15,
        }],
        "prep_tasks": [],
    }
    monkeypatch.setattr(moves_mod._grocery, "list_grocery_list", lambda **kw: [])
    now = datetime.combine(today, time(9, 0))
    day_moves = moves_mod.moves_for_day(today, now=now, view=view)
    cook = [m for m in day_moves if m["kind"] == "cook"][0]
    target = cook["action"]["target"]
    assert target["tab"] == "kitchen"
    assert "mealsView" not in target and "mealsFocus" not in target
    assert target["cookFocus"] == {
        "entryId": 42,
        "date": today.isoformat(),
        "slot": "dinner",
        "title": "Sesame Salmon Bowls",
    }


def test_a_cook_move_still_carries_the_start_by_the_kitchen_line_shows(monkeypatch):
    """"start by 5:35 · 55 min" on the Kitchen root is these two chips,
    lowercased — so the chips have to keep their shape."""
    today = date.today()
    view = {
        "weekly_plan_id": 1, "status": "approved", "prep_tasks": [],
        "meals": [{
            "entry_id": 7, "date": today.isoformat(), "slot": "dinner",
            "meal": "Bulgogi", "cooked_status": "pending", "is_leftovers": False,
            "prep_time_minutes": 20, "cook_time_minutes": 35,
        }],
    }
    monkeypatch.setattr(moves_mod._grocery, "list_grocery_list", lambda **kw: [])
    cook = [m for m in moves_mod.moves_for_day(
        today, now=datetime.combine(today, time(9, 0)), view=view) if m["kind"] == "cook"][0]
    assert cook["chips"][0] == "55 min"
    assert cook["chips"][1].startswith("Start by ")
