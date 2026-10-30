"""
The declutter (Emily, 2026-10-30) — the three approved "Combined" mockups on
the Less clutter canvas, run against static/shell.js's own renderers under
node. One button per screen stands out; everything else is one tap in,
never on every row.

PLAN — one screen for a draft and an approved week
  1. A day bar on top: Week (the default) then each day; a day shows only
     that day, in the same groups.
  2. Breakfast (each breakfast once, with its days), Lunch & dinner (the day
     at the left), Snacks (folded to one line in the week, rows in a day).
  3. Quiet rows: no Swap / Tweak / Move / Done on any row; a leftover meal's
     row says just "Leftovers".
  4. Tapping a row opens ONE sheet: Swap, Tweak, Move (only where allowed),
     See the recipe.
  5. Approving keeps the layout — only the badge and the dock change.
  6. The band carries More ··· only; the More sheet differs by state.

SHOP — one list, store chips as a filter
  7. "All · N" then one chip per store with something left; a chip filters.
  8. No ⋯ on any line: the name opens the row's sheet, with today's options.
  9. "Pick stores" in the Anywhere card's head replaces the "N things to
     sort" row; the dock is "+ Add something" alone (chat floats beside it).

TODAY — same screen, quiet rows
 10. Today's meals: eyebrow, dish, chevron; leftovers say just "Leftovers".
 11. Cook and Prep are one To do list: no who-button, no time-of-day tag.
 12. On shop day the Shop card is one line, "Shop today · Costco, T&T".
"""
from __future__ import annotations

import json
import re
import shutil

import pytest

import nodeharness
import shop_harness
from test_week_seven_tiles import _extract, _extract_var
from test_plan_cards_2026_09_18 import _prelude, _entry, _day, _MON, _TUE, _WED, SHELL_JS

_needs_node = pytest.mark.skipif(shutil.which("node") is None, reason="node is needed to run the renderers")

_THU = "2026-09-24"


def _run(script: str):
    res = nodeharness.run_node(script, timeout=30)
    assert res.returncode == 0, f"node failed: {res.stderr}"
    return json.loads(res.stdout.strip().splitlines()[-1])


# ---------------------------------------------------------------------------
# PLAN
# ---------------------------------------------------------------------------

def _plan_prelude() -> str:
    names = [
        "planDayIndex", "planDayBarHtml", "planRowMeta", "planIsReheat", "planRowHtml",
        "planSlotRowHtml", "planDayLabel", "planDayGroupHtml", "planCardHtml",
        "planBreakfastHtml", "planMainsDayRows", "planMainsHtml", "planSnackRows",
        "planSnacksHtml", "planDayTagsHtml", "planNotesHtml", "planDockHtml",
        "planWeekHtml", "planRowSheetOptions", "planRowSheetHtml", "quietSheetHeadHtml",
        "wkCanMove", "weekBandExtras", "weekBandMoreHtml",
    ]
    return (
        _prelude()
        + "function weekNotesHtml() { return ''; }\n"
        + "function _dayHasPlateSides() { return false; }\n"
        + "function wkCouldntFitHtml() { return '<div class=\"wk-unfit\"></div>'; }\n"
        + "function wkUseUpLineHtml() { return ''; }\n"
        + "function plateCanChange(day, slot, entry) { return slot === 'dinner' && !day.isPast; }\n"
        + _extract_var("MOVE_SLOTS", SHELL_JS) + "\n"
        + _extract_var("PLAN_SHEET_OPTS", SHELL_JS) + "\n"
        + "\n".join(_extract(n, SHELL_JS) for n in names) + "\n"
    )


def _week():
    """Mon (past) – Thu. Oats Mon and Tue; a dinner Tuesday eaten again as
    Wednesday's lunch; two snacks a day; an open Thursday dinner."""
    snack = lambda t, i: _entry(t, entry_id=i, meta="5 min")  # noqa: E731
    return [
        _day(_MON, past=True, breakfast=_entry("Overnight oats", entry_id=11, meta="13 min"),
             lunch=_entry("Chickpea salad", entry_id=12, meta="10 min"),
             dinner=_entry("Lemon chicken", entry_id=13, meta="35 min"),
             snacks=[snack("Apple", 14), snack("Yogurt", 15)]),
        _day(_TUE, iso_today=True, breakfast=_entry("Overnight oats", entry_id=21, meta="13 min"),
             lunch=_entry("Turkey club", entry_id=22, meta="25 min"),
             dinner=_entry("Shrimp paella", entry_id=23, meta="50 min"),
             snacks=[snack("Pear", 24), snack("Edamame", 25)]),
        _day(_WED, breakfast=_entry("Frittata", entry_id=31, meta="30 min"),
             lunch=_entry("Shrimp paella", entry_id=32, meta="reheat", source="leftovers",
                          leftover_from={"date": _TUE, "slot": "dinner", "meal": "Shrimp paella"}),
             dinner=_entry("Beef stew", entry_id=33, meta="1 hr"),
             snacks=[snack("Rice cakes", 34), snack("Grapes", 35)]),
        _day(_THU, breakfast=_entry("Frittata", entry_id=41, meta="30 min"),
             lunch=_entry("Lentil soup", entry_id=42, meta="30 min"),
             dinner={"title": "Your call", "state": "open", "source": "open", "entry_id": 43, "options": []},
             snacks=[snack("Nuts", 44), snack("Cheese", 45)]),
    ]


def _data(status, days):
    return {"weekly_plan_id": 7, "status": status, "days": days, "week_label": "Sep 21–27",
            "day_count": 7, "defrost_asked_at": None}


def _plan(script: str):
    return _run(_plan_prelude() + "var DAYS = " + json.dumps(_week()) + ";\n"
                + "weekState.days = DAYS;\n" + script)


def _row_attrs(html: str, attr: str) -> list[str]:
    return re.findall(attr + r'="([^"]*)"', html)


@_needs_node
def test_the_week_is_the_default_view_with_a_day_bar_of_week_then_each_day():
    out = _plan(
        "weekState.planDay = null;\n"
        "var data = Object.assign(" + json.dumps(_data("draft", [])) + ", { days: DAYS });\n"
        "console.log(JSON.stringify(planWeekHtml(data, DAYS)));"
    )
    tiles = _row_attrs(out, "data-plan-day")
    assert tiles == ["week", "0", "1", "2", "3"], "Week first, then one tile per day"
    assert 'data-plan-day="week" aria-pressed="true"' in out
    assert "The whole week" in out
    # Every day's lunch and dinner is in the week view.
    assert set(_row_attrs(out, "data-wk-day-index")) >= {"0", "1", "2", "3"}


@_needs_node
def test_a_day_on_the_bar_shows_only_that_day_in_the_same_groups():
    out = _plan(
        "weekState.planDay = 2;\n"
        "var data = Object.assign(" + json.dumps(_data("draft", [])) + ", { days: DAYS });\n"
        "console.log(JSON.stringify(planWeekHtml(data, DAYS)));"
    )
    assert 'data-plan-day="2" aria-pressed="true"' in out
    assert 'data-plan-day="week" aria-pressed="false"' in out
    assert set(_row_attrs(out, "data-wk-day-index")) == {"2"}, "only Wednesday's rows"
    assert '<h2 class="wk-pview-title">Wednesday</h2>' in out
    for head in ("Breakfast", "Lunch &amp; dinner", "Snacks"):
        assert head in out
    # A day's snacks are rows straight away — no fold.
    assert "data-plan-snacks" not in out
    assert _row_attrs(out, "data-plan-snack") == ["snack", "snack2"]


@_needs_node
def test_rows_carry_no_swap_tweak_move_or_done_buttons():
    for status in ("draft", "approved"):
        out = _plan(
            "var data = Object.assign(" + json.dumps(_data(status, [])) + ", { days: DAYS });\n"
            "weekState.planDay = null; var a = planWeekHtml(data, DAYS);\n"
            "weekState.planDay = 1; var b = planWeekHtml(data, DAYS);\n"
            "console.log(JSON.stringify(a + b));"
        )
        for attr in ("data-wk-swap-sheet", "data-wk-tweak", "data-wk-move", "data-wk-done", "wk-mini"):
            assert attr not in out, f"{status}: {attr} is on a row"
        assert 'class="wk-qrow' in out
        assert "wk-qrow-chev" in out


@_needs_node
def test_a_leftover_meal_row_says_just_leftovers():
    out = _plan(
        "weekState.planDay = 2;\n"
        "var data = Object.assign(" + json.dumps(_data("draft", [])) + ", { days: DAYS });\n"
        "console.log(JSON.stringify(planWeekHtml(data, DAYS)));"
    )
    lunch = out.split('data-plan-row="lunch"', 1)[1].split("</button>", 1)[0]
    assert '<span class="wk-row-meta">Leftovers</span>' in lunch
    assert "from" not in lunch.lower() and "Tuesday" not in lunch


@_needs_node
def test_breakfast_in_the_week_is_each_dish_once_with_its_days():
    out = _plan(
        "DAYS.forEach(function (d) { d.isPast = false; });\n"
        "console.log(JSON.stringify(planBreakfastHtml(DAYS, null)));"
    )
    assert out.count("data-plan-row=") == 2, "oats and frittata, once each"
    assert "Mon, Tue · 13 min" in out
    assert "Wed, Thu · 30 min" in out
    # Frittata has two days still ahead: its row is the whole dish.
    assert f'data-plan-dates="{_WED},{_THU}"' in out
    assert f'data-plan-dates="{_MON},{_TUE}"' in out


@_needs_node
def test_the_weeks_snacks_fold_to_one_line_and_open_in_place():
    out = _plan(
        "weekState.planSnacksOpen = false; var shut = planSnacksHtml(DAYS, null);\n"
        "weekState.planSnacksOpen = true; var open = planSnacksHtml(DAYS, null);\n"
        "console.log(JSON.stringify({ shut: shut, open: open }));"
    )
    assert "2 a day · 8 in all" in out["shut"]
    assert 'data-plan-snacks="1" aria-expanded="false"' in out["shut"]
    assert "data-plan-snack=" not in out["shut"]
    assert 'aria-expanded="true"' in out["open"]
    assert out["open"].count("data-plan-snack=") == 6, "Monday has gone by: its two are lines, not swaps"
    assert "Apple" in out["open"] and "Yogurt" in out["open"]
    assert len(re.findall(r'class="wk-pgroup[ "]', out["open"])) == 4, "per-day rows, styled like the meal rows"


@_needs_node
def test_approving_changes_only_the_badge_and_the_dock_not_the_layout():
    out = _plan(
        "weekState.planDay = null;\n"
        "var d = planWeekHtml(Object.assign(" + json.dumps(_data("draft", [])) + ", { days: DAYS }), DAYS);\n"
        "var a = planWeekHtml(Object.assign(" + json.dumps(_data("approved", [])) + ", { days: DAYS }), DAYS);\n"
        "console.log(JSON.stringify({ d: d, a: a, xd: weekBandExtras({ weekly_plan_id: 7, status: 'draft', days: DAYS }),"
        " xa: weekBandExtras({ weekly_plan_id: 7, status: 'approved', days: DAYS }) }));"
    )
    rows = lambda h: _row_attrs(h, "data-plan-row")  # noqa: E731
    assert rows(out["d"]) == rows(out["a"]), "the same rows either way"
    assert _row_attrs(out["d"], "data-plan-day") == _row_attrs(out["a"], "data-plan-day")
    assert 'id="week-approve-btn"' in out["d"] and "Approve the week" in out["d"]
    assert 'id="week-approve-btn"' not in out["a"]
    assert "wk-decide" not in out["a"], "an approved week's dock is only the chat button"
    # The band: More ··· and nothing else, either way.
    assert out["xd"] == {"more": True} and out["xa"] == {"more": True}


@_needs_node
def test_a_rows_sheet_offers_swap_tweak_move_only_where_allowed_and_the_recipe():
    out = _plan(
        "var opts = {\n"
        "  dinnerAhead: planRowSheetOptions(DAYS[2], 'dinner'),\n"
        "  breakfast: planRowSheetOptions(DAYS[2], 'breakfast'),\n"
        "  leftovers: planRowSheetOptions(DAYS[2], 'lunch'),\n"
        "  past: planRowSheetOptions(DAYS[0], 'dinner'),\n"
        "  html: planRowSheetHtml(DAYS[2], 'dinner')\n"
        "};\n"
        "console.log(JSON.stringify(opts));"
    )
    assert out["dinnerAhead"] == ["swap", "tweak", "move"]
    assert "move" not in out["breakfast"], "no Move for a breakfast"
    assert "move" not in out["leftovers"], "no Move for a leftovers night"
    assert "swap" not in out["past"], "nothing to swap on a day gone by"
    html = out["html"]
    for key in ("swap", "tweak", "move", "recipe"):
        assert f'data-plan-opt="{key}"' in html
    assert "See the recipe" in html and "Beef stew" in html
    assert "Dinner · Wednesday" in html


def test_the_plan_band_lost_its_pills_and_toggle():
    for gone in ("wk-replan", "wk-draft-seg", "wk-band-plan-next", "weekReplanPillHtml", "weekDraftSegHtml"):
        assert gone not in SHELL_JS, gone
    assert 'id="wk-band-more"' in SHELL_JS


def test_approving_stays_on_plan_with_the_list_one_tap_away():
    start = SHELL_JS.index("  async function submitWeekApproval(")
    body = SHELL_JS[start:SHELL_JS.index("\n  function showApproveConfirm(", start)]
    assert "label: 'See the list'" in body
    assert "goGroceryList();\n      showToast" not in body


def _more_sheet(status):
    stub = (
        "var ROWS = { innerHTML: '', querySelector: function () { return null; } };\n"
        "var document = { getElementById: function (id) { return id === 'meals-more-rows' ? ROWS : null; } };\n"
        "var panels = {};\n"
        "function addDaysLocal(d, n) { var x = new Date(d + 'T00:00:00'); x.setDate(x.getDate() + n); return x.toISOString().slice(0, 10); }\n"
        "function todayLocalStr() { return '2026-09-22'; }\n"
        "var planningPeriodDefault = null;\n"
        "function thisWeekStartLocal() { return '2026-09-21'; }\n"
    )
    names = ["mealsMoreRowHtml", "mealsMoreGroupHtml", "planAfterTitle", "planAfterLine", "nextPeriodFor",
             "renderMealsMoreSheet"]
    return _run(
        _prelude() + stub + "\n".join(_extract(n, SHELL_JS) for n in names) + "\n"
        + "weekState.data = " + json.dumps(_data(status, [_day(_MON)])) + ";\n"
        + "weekState.days = weekState.data.days;\n"
        + "renderMealsMoreSheet();\n"
        + "var labels = (ROWS.innerHTML.match(/wk-more-label\">([^<]*)</g) || [])"
        + ".map(function (m) { return m.replace(/wk-more-label\">/, '').replace(/<$/, ''); });\n"
        + "console.log(JSON.stringify(labels));"
    )


@_needs_node
def test_the_more_sheet_for_a_draft():
    assert _more_sheet("draft") == [
        "Try again", "Change my answers", "See the whole week", "Adjust your setup", "Start over", "Need a hand?",
    ]


@_needs_node
def test_the_more_sheet_for_an_approved_week():
    assert _more_sheet("approved") == [
        "Reopen the week", "Plan the week after", "See the whole week", "Adjust your setup", "Start over",
        "Need a hand?",
    ]


# ---------------------------------------------------------------------------
# SHOP
# ---------------------------------------------------------------------------

_SHOP_SETUP = """
const data = setUp(3, [
  { store: 'Costco', items: [
      { id: 101, item: 'Whole chicken', quantity: '4', store: 'Costco', store_decided: 1, category: 'meat', status: 'needed' },
      { id: 102, item: 'Dish soap', quantity: '', store: 'Costco', store_decided: 1, category: 'other', status: 'needed' } ] },
  { store: 'T&T', items: [
      { id: 201, item: 'Jasmine rice', quantity: '3 cups', store: 'T&T', store_decided: 1, category: 'pantry', status: 'needed' } ] }
], ['Costco', 'T&T']);
"""


def _shop(body: str):
    return shop_harness.run(_SHOP_SETUP + body)


@shop_harness.needs_node
def test_shop_chips_are_all_then_each_store_with_what_is_left():
    out = _shop("console.log(JSON.stringify(groStoreChipsHtml(data)));")
    labels = re.findall(r'data-gro="store-filter"[^>]*>([^<]*)<', out)
    assert labels == ["All · 6", "Costco · 2", "T&amp;T · 1", "Anywhere · 3"]
    assert 'data-filter="" aria-pressed="true"' in out, "All is the default"


@shop_harness.needs_node
def test_a_store_chip_filters_the_list_to_that_store():
    out = _shop(
        "clickIfRendered({ gro: 'store-filter', filter: 'Costco' });\n"
        "const one = groListHtml(data);\n"
        "const chips = groStoreChipsHtml(data);\n"
        "clickIfRendered({ gro: 'store-filter', filter: '' });\n"
        "console.log(JSON.stringify({ one: cards(one), chips: chips, all: cards(groListHtml(data)) }));"
    )
    assert out["one"] == ["Costco"]
    assert 'data-filter="Costco" aria-pressed="true"' in out["chips"]
    assert out["all"] == ["Costco", "T&amp;T", "Anywhere"]


@shop_harness.needs_node
def test_a_line_has_no_dots_and_its_name_opens_the_rows_sheet():
    out = _shop(
        "const before = groListHtml(data);\n"
        "clickIfRendered({ gro: 'row-menu', id: '101' });\n"
        "console.log(JSON.stringify({ before: before, screen: screenHtml() }));"
    )
    assert "gro-rowmore" not in out["before"], "no ⋯ on any line"
    assert out["before"].count('data-gro="row-menu"') == 6, "one per line, on the name"
    assert 'class="gro-line-open"' in out["before"]
    sheet = out["screen"].split('class="gro-rowmenu"', 1)[1]
    # Today's row options, in the sheet.
    assert 'data-gro="row-store" data-id="101" data-store="Costco" aria-pressed="true"' in sheet
    assert 'data-store="" aria-pressed="false"' in sheet and ">Any store<" in sheet
    for action in ("row-qty", "have-it", "row-exclude", "subst-open", "row-staple", "row-remove"):
        assert f'data-gro="{action}"' in sheet, action
    assert "Make it a regular" in sheet
    assert "Whole chicken" in sheet


@shop_harness.needs_node
def test_ticking_is_unchanged_by_the_sheet():
    out = _shop(
        "clickIfRendered({ gro: 'line-tick', id: '101', bought: '0' });\n"
        "settle(function () { console.log(JSON.stringify(purchases())); });"
    )
    assert any("/101" in u for u in out)


@shop_harness.needs_node
def test_pick_stores_replaces_the_things_to_sort_row_and_the_dock_is_add_alone():
    out = _shop("console.log(JSON.stringify({ list: groListHtml(data), dock: groDockHtml(data, 'list'), band: groBandExtrasHtml(data) }));")
    assert "to sort" not in out["list"]
    assert "have a store yet" not in out["list"]
    anywhere = out["list"].split('gro-anywhere', 1)[1].split("gro-card-rows", 1)[0]
    assert 'data-gro="goto-sort"' in anywhere and "Pick stores" in anywhere
    assert 'data-gro="add-open"' in out["dock"]
    for gone in ("dock-primary", "goto-beforeshop", "goto-sort"):
        assert gone not in out["dock"], gone


@shop_harness.needs_node
def test_before_you_go_is_a_link_in_the_band_when_the_pass_has_something_to_ask():
    out = _shop(
        "const none = groBandExtrasHtml(data);\n"
        "groceryState.bsData = { regulars: { choices: [{ id: 1, name: 'Milk' }], on_list: [] } };\n"
        "console.log(JSON.stringify({ none: none, band: groBandExtrasHtml(data), dock: groDockHtml(data, 'list') }));"
    )
    assert "goto-beforeshop" not in out["none"], "nothing to ask, no link"
    assert 'data-gro="goto-beforeshop"' in out["band"] and "Before you go" in out["band"]
    assert "goto-beforeshop" not in out["dock"], "a link in the band, never the dock's button"


# ---------------------------------------------------------------------------
# TODAY
# ---------------------------------------------------------------------------

def _today_prelude() -> str:
    def region(a, b):
        i = SHELL_JS.index(a)
        return SHELL_JS[i:SHELL_JS.index(b, i)]

    return (
        "function escapeHtml(s){return String(s == null ? '' : s).replace(/&/g,'&amp;')"
        ".replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/\"/g,'&quot;');}\n"
        + region("  var TICK_ICON =", "  var DOTS_ICON =")
        + region("  // ---------- Today: Shop and Cook ----------", "  function tomorrowCardHtml(")
        + _extract("moveRecipeTarget", SHELL_JS) + "\n"
        + _extract("moveDishHtml", SHELL_JS) + "\n"
        + _extract("quietSheetHeadHtml", SHELL_JS) + "\n"
        + "function openRecipeFor() {}\n"
        + "var shellWho = { adults: [{ id: 1, name: 'Maya' }, { id: 2, name: 'Theo' }] };\n"
    )


def _move(kind, id_, title, start, **extra):
    m = {
        "id": id_, "kind": kind, "title": title, "detail": "", "reason": "", "date": "2026-10-30",
        "slot": None, "window_start": "2026-10-30T" + start, "window_end": "2026-10-30T" + start,
        "weight": 2, "action": {"label": "Done", "target": {}}, "done": False, "tickable": True,
        "overdue": False, "entry_id": None, "duration_min": 0, "meta": "", "chips": [],
    }
    m.update(extra)
    return m


_MOVES = [
    _move("prep", "prep:1", "Soak the chickpeas", "22:00:00", meta="for Tue", owner_name="Maya", owner=1),
    _move("cook", "cook:2", "Make lunch", "12:00:00", meta="25 min", duration_min=25),
    _move("cook", "cook:3", "Cook dinner", "17:35:00", meta="40 min", chips=["Start by 5:35"],
          action={"label": "Cook this", "target": {}}),
]


def _today(script: str):
    return _run(_today_prelude() + script)


@_needs_node
def test_cook_and_prep_are_one_to_do_list_without_who_or_time_tags():
    html = _today("console.log(JSON.stringify(dayGroupsHtml(%s, null, null, [])));" % json.dumps(_MOVES))
    assert "day-group-todo" in html
    assert "day-group-cook" not in html and "day-group-prep" not in html
    assert "To do" in html
    for gone in ("day-node-who", "day-node-tag", "Morning", "Afternoon", "Evening", "Any time"):
        assert gone not in html, gone
    # One short note where it helps.
    assert '<span class="day-node-meta">start 5:35</span>' in html
    assert '<span class="day-node-meta">for Tue</span>' in html
    assert "Maya" not in html, "who's on it is in the sheet, not on the row"
    # In the day's order, top to bottom.
    order = [t for t in ("Make lunch", "Cook dinner", "Soak the chickpeas") if t in html]
    assert [html.index(t) for t in order] == sorted(html.index(t) for t in order)


@_needs_node
def test_a_to_do_rows_sheet_holds_who_is_on_it_and_the_action():
    out = _today(
        "var data = { moves: %s };\n"
        "console.log(JSON.stringify({ prep: todayMoveSheetHtml(data.moves[0]), cook: todayMoveSheetHtml(data.moves[2]) }));"
        % json.dumps(_MOVES)
    )
    assert "Who’s on it" in out["prep"] and "Maya" in out["prep"]
    assert 'data-move-who="prep:1"' in out["prep"]
    assert "Start by 5:35" in out["cook"] and "Cook this" in out["cook"]
    assert 'data-move-run="cook:3"' in out["cook"]


@_needs_node
def test_todays_meals_rows_are_eyebrow_dish_and_chevron_and_leftovers_say_just_that():
    rows = [
        {"slot": "breakfast", "label": "Breakfast", "dishes": ["Scrambled eggs"], "per_person": False,
         "lines": [{"who": "everyone", "where": "", "dish": "Scrambled eggs"}], "note": "", "entry_id": 1},
        {"slot": "lunch", "label": "Lunch", "dishes": ["Shrimp paella"], "per_person": False, "is_leftovers": True,
         "lines": [{"who": "everyone", "where": "", "dish": "Shrimp paella"}],
         "note": "leftovers from Thursday’s dinner", "entry_id": 2},
        {"slot": "dinner", "label": "Dinner", "dishes": ["Pepper chicken"], "per_person": True,
         "lines": [{"who": "Maya", "where": "at home", "dish": "Pepper chicken", "initials": ["M"]},
                   {"who": "Theo", "where": "out", "dish": "Pepper chicken", "initials": ["T"]}],
         "note": "", "entry_id": 3},
    ]
    out = _today(
        "var rows = %s;\n"
        "console.log(JSON.stringify({ card: todayMealsCardHtml(rows), sheet: todayMealSheetHtml(rows[2]),"
        " left: todayMealSheetHtml(rows[1]) }));" % json.dumps(rows)
    )
    card = out["card"]
    assert card.count("data-meal-sheet=") == 3
    assert card.count("day-meal-chev") == 3
    assert "Thursday" not in card and "from" not in card
    lunch = card.split('data-meal-sheet="1"', 1)[1].split("</button>", 1)[0]
    assert '<span class="day-meal-note">Leftovers</span>' in lunch
    # Per-person lines are in the sheet, not on the row.
    assert "Maya" not in card and "day-meal-who" not in card
    assert "Maya" in out["sheet"] and "at home" in out["sheet"] and "See the recipe" in out["sheet"]
    assert "Thursday" not in out["left"]


@_needs_node
def test_on_shop_day_the_shop_card_is_one_line_naming_the_stores():
    shop = _move("shop", "shop:1", "Costco · 6 things", "09:00:00", tickable=False,
                 stops=[{"store": "Costco", "count": 4, "items": []}, {"store": "T&T", "count": 2, "items": []}])
    html = _today("console.log(JSON.stringify(dayGroupsHtml(%s, null, %s, [])));"
                  % (json.dumps(_MOVES + [shop]), json.dumps({"is_shop_day": True})))
    assert "day-group-shop" not in html
    assert "Shop today" in html and "Costco, T&amp;T" in html
    assert 'data-shop-open="1"' in html


@_needs_node
def test_a_past_days_snack_is_a_line_not_a_swap_and_unplanned_snacks_draw_no_card():
    out = _plan(
        "var past = planSnackRows(DAYS[0], 0);\n"
        "var empty = DAYS.map(function (d) { return Object.assign({}, d, { snacks: [{ state: 'planned_empty', title: 'None' }] }); });\n"
        "console.log(JSON.stringify({ past: past, empty: planSnacksHtml(empty, null) }));"
    )
    assert "data-plan-snack" not in out["past"], "nothing to swap on a day gone by"
    assert "Apple" in out["past"]
    assert out["empty"] == "", "no '0 in all' card"


def test_a_failed_store_pick_or_amount_save_gives_the_button_back():
    for marker in ("case 'row-qty': {", "case 'row-store': {"):
        start = SHELL_JS.index(marker)
        body = SHELL_JS[start:SHELL_JS.index("\n      case '", start + 10)]
        assert "if (!ok) { el.disabled = false; return; }" in body, marker
