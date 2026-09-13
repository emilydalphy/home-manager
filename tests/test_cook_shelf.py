"""
Cook's root is "Cook · D · The shelf" (Emily picked it from the Beyond-lists
canvas on 2026-09-13). Under the band, in order:

  1. **The shelf** — a sideways strip, one tile per night of the planning
     period, tonight tinted celadon, one word for each dish
     (`dishShortWord`), a dash for a night with nothing planned. A tile
     opens that night's meal screen with the "‹ Cook" crumb (§2b S8).
  2. **Tonight** — ONE spruce card (rule 4): the eyebrow with the day, the
     meal of the day and the cook's name when the household named one;
     the dish; START / ON THE TABLE off the moves engine's own arithmetic;
     the thaw/prep fact for this meal.
  3. At most **two quiet get-ready rows**: the next thaw for a later night,
     the next prep session, the next loose prep task — the two soonest.
  4. The **dock**: "Start cooking" (tonight's cook), "Mark eaten" (a reheat
     night), nothing at all when tonight has no cook. Until this design
     Cook's root had no dock by rule — DESIGN_SYSTEM §6 changed in the same
     commit.
  5. Recipes / Add from a link / Inventory behind one "More ···" link, in a
     sheet (`#cook-more-sheet`).

Most of these run shell.js's own functions under node (tests/nodeharness.py);
the rest are source assertions, the same kind as
tests/test_kitchen_and_preferences.py. The backend half at the bottom pins
the three keys get_cooker_view grew for this: period_start_date, day_count,
cook_name.
"""
from __future__ import annotations

import datetime
import json
import re
import shutil
from pathlib import Path

import pytest

from app import tools
from tests import nodeharness

REPO = Path(__file__).resolve().parent.parent
SHELL_JS = (REPO / "static" / "shell.js").read_text(encoding="utf-8")
SHELL_CSS = (REPO / "static" / "shell.css").read_text(encoding="utf-8")
SHELL_HTML = (REPO / "static" / "shell.html").read_text(encoding="utf-8")
DESIGN = (REPO / "DESIGN_SYSTEM.md").read_text(encoding="utf-8")
CLAUDE_MD = (REPO / "CLAUDE.md").read_text(encoding="utf-8")

_needs_node = pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")


def _function(name: str, source: str = SHELL_JS) -> str:
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


def _between(start_marker: str, end_marker: str) -> str:
    a = SHELL_JS.index(start_marker)
    return SHELL_JS[a : SHELL_JS.index(end_marker, a)]


_PRELUDE = (
    "function escapeHtml(s){return String(s == null ? '' : s)"
    ".replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/\"/g,'&quot;');}\n"
    "const GRO_ICONS = { chevRight: '<svg data-icon=\"chev\"></svg>' };\n"
    "const COOK_ICONS = { check: '<svg data-icon=\"check\"></svg>' };\n"
    "const REHEAT_ACTION_LABEL = 'Mark eaten';\n"
    "const REHEAT_UNDO_LABEL = 'Mark not eaten';\n"
    "var cookState = { tonightIdx: null };\n"
    # Real date helpers: the shelf counts nights and names days.
    "function dayName(dateStr, opts){ return new Date(dateStr + 'T00:00:00').toLocaleDateString('en-US', opts); }\n"
    "function dayNameShort(iso){ return new Date(iso + 'T00:00:00').toLocaleDateString('en-US', { weekday: 'short' }); }\n"
    + _function("addDaysLocal") + "\n"
    + _function("cookMinutesLabel") + "\n"
    + _function("cookCoversLabel") + "\n"
    + _function("cookFocusPrepTasks") + "\n"
    + _function("kitchenLoosePrepTasks") + "\n"
    + _function("kitchenTodayLine") + "\n"
    + _function("kitchenTodayRows") + "\n"
    + _function("kitchenTodayRowHtml") + "\n"
    # The whole root block: dishShortWord through the More sheet's rows.
    + _between("  var COOK_DISH_WORDS = [", "  function renderKitchen()")
)


def _node(script: str):
    res = nodeharness.run_node(_PRELUDE + script, timeout=30)
    assert res.returncode == 0, f"node failed: {res.stderr}"
    return json.loads(res.stdout.strip())


MON = "2026-09-14"  # a Monday
TUE, WED, THU = "2026-09-15", "2026-09-16", "2026-09-17"


def _meal(entry_id, date, meal, slot="dinner", **extra):
    m = {"entry_id": entry_id, "date": date, "slot": slot, "meal": meal,
         "cooked_status": "pending", "is_leftovers": False,
         "prep_time_minutes": 10, "cook_time_minutes": 20, "advance_prep_notes": ""}
    m.update(extra)
    return m


# --------------------------------------------------------------------------
# dishShortWord
# --------------------------------------------------------------------------

@_needs_node
def test_dish_short_word_finds_the_word_that_names_the_dish():
    """Emily's three: "Stir-fry", "Tikka", and "Chicken" — not "Roast",
    which would be too clever."""
    cases = ["Beef and Broccoli Stir-Fry", "Chicken Tikka Masala", "Herb-Roasted Chicken",
             "Sheet-Pan Fajitas", "Spaghetti Bolognese", "Lentil soup with kale",
             "Egg Bites", "Chili", "tacos al pastor", "Mac and Cheese", "",
             "Baked Lemon Herb Salmon with Roasted Asparagus", "Herb-Roasted Chicken with Roasted Root Vegetables"]
    out = _node("console.log(JSON.stringify(" + json.dumps(cases) + ".map(function (c) { return dishShortWord(c); })));")
    assert out == ["Stir-fry", "Tikka", "Chicken", "Fajitas", "Spaghetti", "Soup",
                   "Bites", "Chili", "Tacos", "Mac", "", "Salmon", "Chicken"]


@_needs_node
def test_dish_short_word_prefers_a_short_name_and_never_a_joining_word():
    out = _node(
        "console.log(JSON.stringify(["
        " dishShortWord('Whatever Long Name', 'Tikka'),"
        " dishShortWord('Beef and Broccoli'),"
        " dishShortWord('rice and beans')"
        "]));"
    )
    assert out == ["Tikka", "Broccoli", "Rice"]


# --------------------------------------------------------------------------
# The shelf
# --------------------------------------------------------------------------

def _shelf(meals, data, today):
    return _node(
        "var meals = " + json.dumps(meals) + ";\n"
        "console.log(JSON.stringify({ html: cookShelfHtml(meals, " + json.dumps(data) + ", " + json.dumps(today) + "),"
        " nights: cookShelfNights(meals, " + json.dumps(data) + ", " + json.dumps(today) + ")"
        " }));"
    )


@_needs_node
def test_the_shelf_is_one_tile_per_night_of_the_period():
    meals = [_meal(1, MON, "Chicken Tikka Masala"), _meal(2, WED, "Beef and Broccoli Stir-Fry"),
             _meal(3, THU, "Leftovers", is_leftovers=True, leftovers_headline="Leftovers — Wednesday’s stir-fry")]
    out = _shelf(meals, {"period_start_date": MON, "day_count": 7}, TUE)
    html = out["html"]
    assert html.count('class="shelf-tile') == 7
    assert [n["iso"] for n in out["nights"]][0] == MON and len(out["nights"]) == 7
    # Tonight is Tuesday, which has nothing planned: tinted, a dash, not a button.
    tonight = re.search(r'<div class="shelf-tile is-tonight is-empty"[^>]*>(.*?)</div>', html)
    assert tonight, "tonight's empty tile is a plain tile"
    assert ">TUE<" in tonight.group(1) and ">15<" in tonight.group(1) and ">—<" in tonight.group(1)
    # The planned nights are buttons into their meal (S8), one word each.
    assert '<button type="button" class="shelf-tile" data-cook="focus" data-idx="0" data-at="steps"' in html
    assert ">Tikka<" in html and ">Stir-fry<" in html
    # A reheat night says so and opens the reheat's own card.
    assert ">Leftovers<" in html and 'data-cook="focus" data-idx="2"' in html
    # Every tile is at least 44px tall by CSS, and every colour a token.
    assert "min-height: 44px;" in SHELL_CSS.split(".shelf-tile {", 1)[1][:200]


@_needs_node
def test_the_shelf_falls_back_to_a_week_from_today_and_prefers_dinner():
    meals = [_meal(1, MON, "Egg Bites", slot="breakfast"), _meal(2, MON, "Chili", slot="dinner")]
    out = _shelf(meals, {}, MON)
    assert len(out["nights"]) == 7 and out["nights"][0]["iso"] == MON
    assert out["nights"][0]["meal"]["meal"] == "Chili", "the night is its dinner"
    assert "shelf-tile is-tonight" in out["html"] and ">Chili<" in out["html"]


def test_the_shelf_opens_scrolled_to_tonight_and_never_scrolls_the_page():
    fn = _function("cookShelfScrollToTonight")
    assert "shelf.scrollLeft = Math.max(0, left);" in fn
    assert "scrollIntoView" not in fn
    assert "cookShelfScrollToTonight(body);" in _function("renderKitchen")


# --------------------------------------------------------------------------
# Tonight: the one spruce card
# --------------------------------------------------------------------------

def _tonight(meals, moves, data, today, tonight_idx=None):
    return _node(
        "cookState.tonightIdx = " + json.dumps(tonight_idx) + ";\n"
        "var meals = " + json.dumps(meals) + ";\n"
        "var rows = kitchenTodayRows(meals, " + json.dumps(moves) + ", " + json.dumps(today) + ");\n"
        "var row = cookTonightRow(rows);\n"
        "console.log(JSON.stringify({"
        " html: kitchenCookingTodayHtml(rows, meals, " + json.dumps(today) + ", " + json.dumps(data) + "),"
        " dock: cookRootDockHtml(row)"
        " }));"
    )


_MOVE = {"kind": "cook", "entry_id": 1, "chips": ["30 min", "Start by 6:00"],
         "time_label": "6:30 tonight", "detail": "dinner · 30 min · 6:30"}


@_needs_node
def test_the_card_says_the_day_tonight_and_the_cook_and_when_to_start():
    meals = [_meal(1, MON, "Chicken Tikka Masala")]
    out = _tonight(meals, [_MOVE], {"cook_name": "Emily"}, MON, 0)
    html = out["html"]
    assert html.startswith('<section class="cook-tonight"')
    assert ">MONDAY · TONIGHT · EMILY<" in html
    assert '<h2 class="cook-tonight-dish">Chicken Tikka Masala</h2>' in html
    assert ">Start<" in html and ">6:00<" in html
    assert ">On the table<" in html and ">6:30<" in html
    assert ">Nothing to thaw or prep ahead.<" in html
    assert "<button" not in html, "the card carries no button — the dock does"
    assert 'data-cook="start-tonight" data-idx="0">Start cooking</button>' in out["dock"]


@_needs_node
def test_the_card_names_nobody_when_the_household_did_not_and_says_today_for_a_lunch():
    meals = [_meal(1, MON, "Soup", slot="lunch")]
    out = _tonight(meals, [], {"cook_name": None}, MON, 0)
    assert ">MONDAY · TODAY<" in out["html"]
    # No move and a recipe with timing: the one honest tile is how long it takes.
    assert ">Takes<" in out["html"] and ">30 min<" in out["html"]
    assert ">Start<" not in out["html"]


@_needs_node
def test_the_card_carries_the_thaw_fact_for_this_meal():
    meals = [_meal(1, MON, "Chicken Skewers")]
    pending = {"prep_tasks": [{"id": 5, "task_type": "defrost", "status": "pending", "task_date": "2026-09-13",
                               "description": "Move the chicken thighs to the fridge — for Monday’s skewers.",
                               "meal_plan_entry_id": 1, "related_meal": ""}]}
    out = _tonight(meals, [_MOVE], pending, MON, 0)
    assert ">Move the chicken thighs to the fridge — still to do.<" in out["html"]
    done = {"prep_tasks": [dict(pending["prep_tasks"][0], status="done")]}
    out = _tonight(meals, [_MOVE], done, MON, 0)
    assert ">The chicken thighs are in the fridge already.<" in out["html"]
    # A prep step for this meal still to do; then the recipe's own note.
    prep = {"prep_tasks": [{"id": 6, "task_type": "general", "status": "pending", "task_date": MON,
                            "description": "Marinate the chicken", "meal_plan_entry_id": 1, "related_meal": ""}]}
    out = _tonight(meals, [_MOVE], prep, MON, 0)
    assert ">Still to do: Marinate the chicken.<" in out["html"]
    out = _tonight([_meal(1, MON, "Chicken Skewers", advance_prep_notes="Soak the skewers an hour ahead.")], [_MOVE], {}, MON, 0)
    assert ">Soak the skewers an hour ahead.<" in out["html"]


@_needs_node
def test_a_cooked_dinner_has_no_start_time_and_no_dock():
    meals = [_meal(1, MON, "Chili", cooked_status="done")]
    out = _tonight(meals, [_MOVE], {}, MON, 0)
    assert 'class="cook-tonight is-done"' in out["html"]
    assert ">Cooked.<" in out["html"]
    assert "cook-tonight-times" not in out["html"]
    assert out["dock"] == ""


@_needs_node
def test_a_reheat_night_is_the_card_with_mark_eaten_in_the_dock():
    meals = [_meal(7, THU, "Bulgogi Wraps", is_leftovers=True,
                   leftovers_headline="Leftovers — Tuesday’s Bulgogi Wraps",
                   leftovers_from={"date": TUE, "slot": "dinner", "meal": "Bulgogi Wraps"})]
    move = {"kind": "reheat", "entry_id": 7, "chips": [], "time_label": "6:30 tonight", "detail": "leftovers · reheat · 6:30"}
    out = _tonight(meals, [move], {}, THU, 0)
    assert "cook-tonight is-reheat" in out["html"]
    assert ">Leftovers — Tuesday’s Bulgogi Wraps<" in out["html"]
    assert ">Reheat — cooked on Tuesday.<" in out["html"]
    assert ">On the table<" in out["html"] and ">Start<" not in out["html"]
    assert 'data-cook="check-meal" data-entry-id="7" data-next="done">Mark eaten</button>' in out["dock"]


@_needs_node
def test_a_day_with_more_than_one_meal_keeps_the_others_as_rows():
    """A 21-slot plan's breakfast and lunch are still today: the card is
    the meal the tab calls tonight, the rest keep their tick rows."""
    meals = [_meal(1, MON, "Egg Bites", slot="breakfast"), _meal(2, MON, "Soup", slot="lunch"),
             _meal(3, MON, "Chili", slot="dinner")]
    out = _tonight(meals, [], {}, MON, 2)
    assert '<h2 class="cook-tonight-dish">Chili</h2>' in out["html"]
    assert out["html"].count('class="cook-week-item') == 2
    assert ">Egg Bites<" in out["html"] and ">Soup<" in out["html"]
    assert 'data-cook="check-meal" data-entry-id="1"' in out["html"]


@_needs_node
def test_nothing_to_cook_is_the_empty_moment_and_no_dock():
    out = _node(
        "function emptyMomentHtml(i, s, d){ return '<div class=\"empty-moment\">' + s + (d ? '|' + d : '') + '</div>'; }\n"
        + _function("kitchenNextCookLine") + "\n"
        "var meals = " + json.dumps([_meal(1, WED, "Pancakes")]) + ";\n"
        "var rows = kitchenTodayRows(meals, [], " + json.dumps(MON) + ");\n"
        "console.log(JSON.stringify({ html: kitchenCookingTodayHtml(rows, meals, " + json.dumps(MON) + ", {}),"
        " dock: cookRootDockHtml(cookTonightRow(rows)) }));"
    )
    assert out["html"] == '<div class="empty-moment">Nothing to cook tonight.|Next: Wednesday, Pancakes.</div>'
    assert out["dock"] == ""


# --------------------------------------------------------------------------
# The get-ready rows
# --------------------------------------------------------------------------

def _ready(data, meals, today, tonight_idx):
    return _node(
        "var meals = " + json.dumps(meals) + ";\n"
        "var moves = cookGetReadyMoves(" + json.dumps(data) + ", meals, " + json.dumps(today) + ", " + json.dumps(tonight_idx) + ");\n"
        "console.log(JSON.stringify({ moves: moves.map(function (m) { return [m.kind, m.title, m.line]; }),"
        " html: cookGetReadyRowsHtml(moves) }));"
    )


@_needs_node
def test_the_next_thaw_and_the_next_prep_session_are_the_two_rows():
    meals = [_meal(1, MON, "Chili"), _meal(2, WED, "Chicken Skewers")]
    data = {
        "prep_tasks": [
            {"id": 5, "task_type": "defrost", "status": "pending", "task_date": TUE,
             "description": "Move the chicken thighs to the fridge — for Wednesday’s skewers.",
             "meal_plan_entry_id": 2, "related_meal": ""},
        ],
        "prep_sessions": [
            {"date": "2026-09-20", "weekday": "Sunday", "items_done": 1, "items_total": 4,
             "total_minutes_estimate": 40, "covers": [MON, TUE], "items": []},
        ],
    }
    out = _ready(data, meals, MON, 0)
    assert out["moves"] == [
        ["thaw", "Move the chicken thighs to the fridge", "Tomorrow · for Wednesday’s skewers"],
        ["session", "Sunday prep", "Sunday · about 40 min · covers Mon–Tue · 1 of 4 done"],
    ]
    html = out["html"]
    assert html.startswith('<div class="cook-ready" id="kit-get-ready">')
    assert 'class="cook-ready-row is-thaw" data-cook="focus" data-idx="1"' in html, "the thaw opens the meal it is for"
    assert 'class="cook-ready-row" data-cook="session" data-date="2026-09-20"' in html
    assert html.count("cook-ready-chev") == 2


@_needs_node
def test_tonights_own_thaw_is_the_cards_line_not_a_row_and_two_is_the_cap():
    meals = [_meal(1, MON, "Chicken Skewers"), _meal(2, TUE, "Stew")]
    data = {
        "prep_tasks": [
            {"id": 5, "task_type": "defrost", "status": "pending", "task_date": "2026-09-13",
             "description": "Move the chicken thighs to the fridge — for Monday’s skewers.",
             "meal_plan_entry_id": 1, "related_meal": ""},
            {"id": 6, "task_type": "defrost", "status": "pending", "task_date": MON,
             "description": "Move the beef to the fridge — for Tuesday’s stew.",
             "meal_plan_entry_id": 2, "related_meal": ""},
            {"id": 11, "task_type": "general", "status": "pending", "task_date": THU,
             "description": "Soak the beans", "meal_plan_entry_id": None, "related_meal": ""},
        ],
        "prep_sessions": [
            {"date": WED, "weekday": "Wednesday", "items_done": 0, "items_total": 2,
             "total_minutes_estimate": 20, "covers": [], "items": []},
        ],
    }
    out = _ready(data, meals, MON, 0)
    kinds = [m[0] for m in out["moves"]]
    assert kinds == ["thaw", "session"], "the two soonest of one-per-kind; the loose task waits its turn"
    assert out["moves"][0][1] == "Move the beef to the fridge", "tonight's own thaw is not here"
    assert out["moves"][0][2].startswith("By tonight")


@_needs_node
def test_a_loose_prep_task_is_a_row_with_its_tick():
    data = {"prep_tasks": [{"id": 11, "task_type": "general", "status": "pending", "task_date": THU,
                            "description": "Soak the beans", "meal_plan_entry_id": None, "related_meal": ""}],
            "prep_sessions": []}
    out = _ready(data, [_meal(1, MON, "Chili")], MON, 0)
    assert out["moves"] == [["task", "Soak the beans", "Thursday"]]
    assert '<div class="cook-ready-row">' in out["html"]
    assert 'data-cook="check-prep" data-prep-id="11" data-next="done"' in out["html"]
    assert "cook-ready-chev" not in out["html"]


@_needs_node
def test_no_get_ready_moves_is_nothing_at_all():
    out = _ready({"prep_tasks": [], "prep_sessions": []}, [_meal(1, MON, "Chili")], MON, 0)
    assert out["moves"] == [] and out["html"] == ""


# --------------------------------------------------------------------------
# The dock, the More sheet, the wiring
# --------------------------------------------------------------------------

def test_start_cooking_enters_cook_focus_for_tonight_from_the_root():
    handler = _function("onCookClick")
    assert "if (what === 'start-tonight') {" in handler
    block = handler.split("if (what === 'start-tonight') {", 1)[1][:400]
    assert "cookState.focusOrigin = null;" in block, "opened from Cook, so the crumb says Cook"
    assert "cookEnterFocus(parseInt(el.getAttribute('data-idx'), 10));" in block
    assert "return (origin && origin.label) ? origin.label : 'Cook';" in _function("cookBackLabel")
    # The dock element is last in the root view and pinned to its foot.
    build = _function("buildKitchenPanel")
    assert '<div class="dock cook-root-dock" id="kit-dock" hidden></div>' in build
    assert build.index('id="kit-body"') < build.index('id="kit-dock"')
    assert "#kit-root-view .cook-root-dock { margin-top: auto; }" in SHELL_CSS
    assert ".kitchen-content.has-dock { padding-bottom: 0; }" in SHELL_CSS
    root = _function("renderKitchen")
    assert "setDock(cookRootDockHtml(tonight));" in root
    assert "content.classList.toggle('has-dock', !!html);" in root


def test_recipes_add_from_a_link_and_inventory_are_behind_one_more_link():
    root = _function("renderKitchen")
    assert root.count("cookMoreLinkHtml()") == 3, "on the root, the no-plan state and the error state alike"
    link = _function("cookMoreLinkHtml")
    assert 'data-kit="more">More ···</button>' in link
    assert "cook-empty-link" in link, "the 44px in-prose link (rule 6)"
    rows = _function("cookMoreRowsHtml")
    assert rows.count('class="kit-row"') == 3
    for needle in ('data-kit="recipes"', ">Recipes<", 'data-kit="recipe-link"', ">Add from a link<",
                   'data-sheet="inventory"', 'kit-row-title">Inventory'):
        assert needle in rows, needle
    assert "dock-primary" not in rows and "btn-primary" not in rows
    assert 'id="cook-more-sheet"' in SHELL_HTML and 'id="cook-more-scrim"' in SHELL_HTML
    assert 'id="cook-more-rows"' in SHELL_HTML
    assert "openSheet(cookMoreSheet, cookMoreScrim);" in _function("openCookMoreSheet")
    assert "if (what === 'more') openCookMoreSheet();" in _function("onKitchenClick")
    # Same geometry and the same three motion rules as Meals' More.
    assert "#meals-more-sheet, #cook-more-sheet {" in SHELL_CSS
    assert "#cook-more-sheet.is-open" in SHELL_CSS and "#cook-more-scrim.is-open" in SHELL_CSS
    # The old rows never render on the root.
    assert "function kitchenTilesHtml(" not in SHELL_JS
    assert "kit-rows" not in root


def test_the_old_lists_are_gone():
    for gone in ("function cookRestOfWeekHtml(", "function cookRestDayRowHtml(", "function cookPrepSessionsHtml(",
                 "function kitchenPrepTodoHtml(", "KITCHEN_REST_VISIBLE", 'data-cook="rest-more"',
                 "'#kit-prep-todo'", "'#kit-prep-sessions'"):
        assert gone not in SHELL_JS, f"{gone} is back"
    for dead in (".cook-day-row", ".cook-day-dishes", ".cook-day-dish", ".cook-day-min", ".cook-week-name.is-quiet"):
        assert not re.search(r"(^|[\s,}])%s\s*[,{:\[]" % re.escape(dead), SHELL_CSS, re.M), (
            f"{dead} still has a CSS rule — the shelf replaced that list"
        )
    assert "var prepEl = rootView.querySelector('#kit-get-ready');" in _function("renderCook")


def test_the_new_css_is_tokens_only_and_the_card_wears_the_dark_edge():
    start = SHELL_CSS.index("/* ---------- The shelf ----------")
    end = SHELL_CSS.index('/* "More ···"', start)
    section = SHELL_CSS[start:end]
    body = re.sub(r"/\*.*?\*/", "", section, flags=re.S)
    assert not re.search(r"#[0-9a-fA-F]{3,8}\b", body), "a literal colour in the shelf/card CSS (rule 9)"
    tile = section.split(".shelf-tile {", 1)[1][:600]
    assert "flex: 0 0 74px;" in tile and "border-radius: var(--radius-tile);" in tile
    assert "background: var(--surface);" in tile and "border: 1.5px solid var(--hairline);" in tile
    assert ".shelf-tile.is-tonight { background: var(--celadon-tint); border-color: var(--celadon-edge); }" in section
    assert ".shelf-tile.is-tonight .shelf-day { color: var(--celadon-label); }" in section
    assert ".shelf-tile.is-empty .shelf-dish { color: var(--ink-muted); }" in section
    card = section.split(".cook-tonight {", 1)[1][:500]
    for rule in ("background: var(--spruce);", "border-radius: var(--radius-card);",
                 "border: 1.5px solid var(--spruce-edge);", "padding: 18px;", "box-shadow: var(--shadow-hero);"):
        assert rule in card, rule
    assert "color: var(--apricot-light);" in section.split(".cook-tonight-eyebrow {", 1)[1][:300]
    assert "font-size: 26px;" in section.split(".cook-tonight-dish {", 1)[1][:200]
    assert "background: var(--spruce-raised);" in section.split(".cook-tonight-tile {", 1)[1][:200]
    assert "min-height: 52px;" in section.split(".cook-ready-row {", 1)[1][:300]
    assert ".cook-ready-row.is-thaw .cook-ready-icon { background: var(--celadon-tint); color: var(--celadon-label); }" in section
    assert "background: var(--sand);" in section.split(".cook-ready-icon {", 1)[1][:300]


# --------------------------------------------------------------------------
# The doc moved with the code (Tier 2: the dock on Cook's root)
# --------------------------------------------------------------------------

def test_design_system_and_the_decision_log_say_cooks_root_has_a_dock_now():
    assert "**Cook's root has no primary action.**" not in DESIGN
    assert "Cook's root has one primary action" in DESIGN
    assert "The shelf" in DESIGN and "Start cooking" in DESIGN
    # Nav rule 2 no longer names Cook's root as the screen with no dock.
    rule2 = DESIGN.split("- **One dock**", 1)[1][:1200]
    assert "Cook's root is the live example" not in rule2
    assert "2026-09-13" in rule2
    # The empty-moment row: still no dock on an empty night.
    assert "no dock on a night with nothing to cook" in DESIGN
    assert "2026-09-13 — Cook's root is the shelf" in CLAUDE_MD


# --------------------------------------------------------------------------
# The backend half: three keys the shelf and the card read
# --------------------------------------------------------------------------

def _week_start() -> str:
    today = datetime.date.today()
    return (today - datetime.timedelta(days=today.weekday())).isoformat()


def test_the_cooker_view_carries_the_period_and_the_cook():
    plan_id = tools.create_weekly_plan(_week_start())["weekly_plan_id"]
    tools.add_recipe("Chili", ingredients=[{"item": "beans", "qty": "1 tin"}])
    tools.plan_meal(datetime.date.today().isoformat(), "Chili", slot="dinner", weekly_plan_id=plan_id)
    view = tools.get_cooker_view()
    assert view["period_start_date"] == _week_start()
    assert view["day_count"] == 7
    assert view["cook_name"] is None, "nobody named, nobody guessed"
    tools.set_cooking_role("one_person", who="Emily")
    assert tools.get_cooker_view()["cook_name"] == "Emily"
    tools.set_cooking_role("turns")
    assert tools.get_cooker_view()["cook_name"] is None


def test_no_plan_at_all_still_answers_with_the_keys():
    view = tools.get_cooker_view()
    assert view["weekly_plan_id"] is None
    assert view["period_start_date"] is None and view["day_count"] == 0
    assert view["cook_name"] is None
