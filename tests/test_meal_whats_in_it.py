"""
The meal screen's ingredient overview and its "Add something" (Emily,
2026-09-13 — Loop Board "Meal screen: ingredients at a glance, for review
before I commit to the swap" and "Meal screen: add what's missing ('add
potatoes') from the dish itself, not via chat").

Between the hero and the clock: one eyebrow ("WHAT'S IN IT · FOR TWO"),
one ingredient per row with the amount for tonight's table on the right,
and under the rows a plain "Add something" — an outline control, never the
screen's apricot (the dock has that, Rule 5). "Everything out" on the
clock stays as the mise-en-place cue. The Add sheet (#wk-add-sheet) asks
"What should go with it?" and offers the server's short list for this
dish, each row the tap that adds, plus one line to type.

The clock learns to time a side off its own minutes (mealClockStops): the
side's last step at table − minutes so it lands with the rest, each
earlier step five minutes before, never before the start; a side that
needs longer than the dish moves the start earlier; a side with no
minutes sits at the start and says so. These run shell.js's own functions
under node (tests/nodeharness.py), the house standard.
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

import nodeharness

REPO = Path(__file__).resolve().parent.parent
SHELL_JS = (REPO / "static" / "shell.js").read_text(encoding="utf-8")
SHELL_CSS = (REPO / "static" / "shell.css").read_text(encoding="utf-8")
SHELL_HTML = (REPO / "static" / "shell.html").read_text(encoding="utf-8")

_needs_node = pytest.mark.skipif(
    shutil.which("node") is None, reason="node is needed to execute shell.js's own functions"
)


def _extract(name: str) -> str:
    start = SHELL_JS.index(f"function {name}(")
    i = SHELL_JS.index("{", start)
    depth, j = 0, i
    while True:
        if SHELL_JS[j] == "{":
            depth += 1
        elif SHELL_JS[j] == "}":
            depth -= 1
            if depth == 0:
                break
        j += 1
    return SHELL_JS[start : j + 1]


def _var(name: str) -> str:
    start = SHELL_JS.index(f"var {name} = ")
    end = SHELL_JS.index(";\n", start) + 1
    return SHELL_JS[start:end]


_PURE = (
    "function capitalizeFirst(s) { return String(s).charAt(0).toUpperCase() + String(s).slice(1); }\n"
    "function escapeHtml(s) { return String(s == null ? '' : s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/\"/g,'&quot;'); }\n"
    + _var("NUMBER_WORDS") + "\n"
    + _var("TENS_WORDS") + "\n"
    + _var("STOP_TITLE_TAIL") + "\n"
    + _var("WK_ADD_ICON") + "\n"
    + _var("HUMAN_QTY_FRACTIONS") + "\n"
    + "var GRO_ICONS = { chevRight: '<svg/>' };\n"
    + _extract("isSnackSlot") + "\n"
    + "".join(_extract(n) + "\n" for n in (
        "numberWord", "countInWords", "minutesInWords", "clockLabel", "spokenTime",
        "slotTableMinutes", "mealTotalMinutes", "mealStepMinutes", "stopTitleSplit",
        "ingredientNamesLine", "mealClockSides", "mealClockTotal", "finishSideStop",
        "mealClockStops", "mealClockEyebrow", "humanQtyAmount", "humanQtyText",
        "cookIngredientLabel", "mealStopHtml", "mealWhatsInEyebrow", "mealWhatsInHtml"))
)


def _run(expr: str):
    res = nodeharness.run_node(_PURE + f"console.log(JSON.stringify({expr}));\n", timeout=30)
    assert res.returncode == 0, f"node failed: {res.stderr}"
    return json.loads(res.stdout.strip())


def _stops(meal: dict, table: str | None = "6:30"):
    household = (
        f"{{ tableMinutes: slotTableMinutes({json.dumps({'dinner': table})}, 'dinner') }}"
        if table is not None else "null"
    )
    return _run(f"mealClockStops({json.dumps(meal)}, {household})")


POTATOES = {
    "name": "Roasted potatoes", "minutes": 25, "added_by": "household",
    "instructions": [
        "Alongside — Roasted potatoes: Halve the potatoes, toss with oil and salt on a sheet pan.",
        "Alongside: Roast at 425° until golden, about 25 minutes.",
    ],
}

SHRIMP = {
    "meal": "Garlic-Herb Shrimp with Roasted Broccolini",
    "has_full_recipe": True,
    "prep_time_minutes": 10,
    "cook_time_minutes": 20,
    "default_servings": 2,
    "attendance": {"headcount": 2},
    "ingredients": [
        {"item": "Shrimp", "qty": "0.5 lb"},
        {"item": "Broccolini", "qty": "1 bunch"},
        {"item": "Lemon", "qty": "0.5"},
    ],
    "instructions": [
        "Toss the broccolini with oil and roast.",
        "Sear the shrimp with the garlic.",
        "Finish with lemon.",
    ],
    "sides": [],
}


def _with_side(meal: dict, side: dict, extra_ingredients: list | None = None) -> dict:
    out = json.loads(json.dumps(meal))
    out["sides"] = out.get("sides", []) + [side]
    out["instructions"] = out["instructions"] + side["instructions"]
    out["ingredients"] = out["ingredients"] + (extra_ingredients or [])
    return out


# ---------- the clock times a side off its own minutes ----------

@_needs_node
def test_a_side_lands_with_the_rest_and_its_stops_read_in_time_order():
    meal = _with_side(SHRIMP, POTATOES, [{"item": "Yukon Gold potatoes", "qty": "1 lb", "added": True}])
    stops = _stops(meal)

    assert stops[0]["kind"] == "out" and stops[0]["time"] == "6:00"
    sides = [s for s in stops if s["kind"] == "side"]
    assert [s["title"] for s in sides] == ["Halve the potatoes", "Roast at 425°"]
    # Last step at table − 25 = 6:05; the one before it five minutes earlier.
    assert [s["time"] for s in sides] == ["6:00", "6:05"]
    assert all(s["side"] == "Roasted potatoes" for s in sides)
    assert all(not s["untimed"] for s in sides)
    # The dish's own stops keep their spread and the whole list is in time order.
    times = [s["minutes"] for s in stops]
    assert times == sorted(times)
    assert stops[-1]["title"] == "Finish with lemon" and stops[-1]["time"] == "6:30"
    # The "Alongside — Roasted potatoes:" prefix is the tag, not the title.
    assert not any("Alongside" in s["title"] for s in stops)


@_needs_node
def test_a_side_that_needs_longer_than_the_dish_moves_the_start_earlier():
    slow = dict(POTATOES, minutes=45)
    meal = _with_side(SHRIMP, slow)
    stops = _stops(meal)

    assert stops[0]["kind"] == "out" and stops[0]["time"] == "5:45", "everything out at the new start"
    sides = [s for s in stops if s["kind"] == "side"]
    assert [s["time"] for s in sides] == ["5:40", "5:45"] or [s["time"] for s in sides] == ["5:45", "5:45"]
    # The dish's own first step still starts where the dish starts.
    main = [s for s in stops if s["kind"] == "step"]
    assert main[0]["time"] == "6:10" and main[-1]["time"] == "6:30", "the dish keeps its own spread from its own 6:00 start"
    assert _run(f"mealClockTotal({json.dumps(meal)})") == 45
    assert _run(f"mealClockEyebrow(mealClockStops({json.dumps(meal)}, {{ tableMinutes: 18 * 60 + 30 }}), 45)") \
        == "About forty-five minutes, six stops"


@_needs_node
def test_a_side_with_no_minutes_sits_at_the_start_and_says_so():
    untimed = dict(POTATOES, minutes=None)
    meal = _with_side(SHRIMP, untimed)
    stops = _stops(meal)

    sides = [s for s in stops if s["kind"] == "side"]
    assert all(s["untimed"] for s in sides)
    assert all(s["time"] == "6:00" for s in sides)
    html = _run(f"mealStopHtml({json.dumps(sides[0])}, 1, {json.dumps(meal)})")
    assert "No time on this one — start it with everything else." in html
    assert 'class="wk-stop-tag">Roasted potatoes</span>' in html
    assert "is-side" in html


@_needs_node
def test_a_dish_with_no_side_times_exactly_as_before():
    stops = _stops(SHRIMP)
    assert [s["time"] for s in stops] == ["6:00", "6:10", "6:20", "6:30"]
    assert all(s["kind"] != "side" for s in stops)
    assert _run(f"mealClockTotal({json.dumps(SHRIMP)})") == 30


@_needs_node
def test_no_table_time_means_no_times_on_the_side_either():
    meal = _with_side(SHRIMP, POTATOES)
    stops = _stops(meal, table=None)
    assert all(s["time"] is None for s in stops)
    assert sum(1 for s in stops if s["kind"] == "side") == 2


# ---------- the overview ----------

def _overview(meal: dict, is_past: bool = False, state: str = "planned") -> str:
    clock = {"isCook": True, "cookMeal": meal}
    return _run(
        f"mealWhatsInHtml({{ isPast: {json.dumps(is_past)} }}, 'dinner', "
        f"{{ state: {json.dumps(state)}, entry_id: 7 }}, {json.dumps(clock)})"
    )


@_needs_node
def test_the_overview_is_one_ingredient_per_row_with_the_amount_for_tonight():
    html = _overview(SHRIMP)
    assert 'class="wk-clock-eyebrow wk-whatsin-eyebrow">What’s in it · for two</div>' in html
    assert html.count('<li class="wk-ing') == 3
    assert '<span class="wk-ing-name">Shrimp</span><span class="wk-ing-qty">½ lb</span>' in html
    assert '<span class="wk-ing-name">Lemon</span><span class="wk-ing-qty">½</span>' in html
    # Not a dot-separated sentence.
    assert " · Broccolini · " not in html
    # The one way to change the plate from here, plain, under the rows.
    assert 'class="wk-ing-add" data-wk-add="dinner"' in html
    assert "<span>Add something</span>" in html
    assert "dock-primary" not in html and "btn-primary" not in html


ROAST_CHICKEN_BATCHED = {
    "meal": "Roast Chicken",
    "has_full_recipe": True,
    "default_servings": 4,
    "servings": 4,
    "attendance": {"headcount": 2},
    "covers": [{"date": "2027-10-14", "slot": "dinner", "eaters": 2}],
    "covers_note": "Cooking for 4 — enough for Tuesday and Thursday.",
    "ingredients": [
        {"item": "Whole chicken", "qty": "2"},
        {"item": "Rice", "qty": "2 cups"},
    ],
    "instructions": ["Roast the chicken.", "Cook the rice."],
    "sides": [],
}


@_needs_node
def test_a_batched_source_night_says_nights_and_plates_not_tonights_headcount():
    # Loop Board bug: entry 40 (Roast Chicken, make_double_for pointing at
    # Thursday) showed "WHAT'S IN IT · FOR TWO" over the doubled amounts
    # (two whole chickens) — the eyebrow read tonight's headcount while the
    # numbers on the right were the whole batch. The eyebrow must describe
    # what the amounts are actually for.
    html = _overview(ROAST_CHICKEN_BATCHED)
    assert 'class="wk-clock-eyebrow wk-whatsin-eyebrow">What’s in it · two nights, 4 plates</div>' in html
    assert 'for two' not in html
    assert '<span class="wk-ing-name">Whole chicken</span><span class="wk-ing-qty">2</span>' in html
    assert '<span class="wk-ing-name">Rice</span><span class="wk-ing-qty">2 cups</span>' in html


@_needs_node
def test_an_ordinary_night_still_says_for_the_table_it_is_cooking_for():
    # Not batched (no covers_note/servings): unchanged behaviour — the
    # eyebrow names tonight's own table, and the amounts already match it.
    assert _run(f"mealWhatsInEyebrow({json.dumps(SHRIMP)})") == "What’s in it · for two"


@_needs_node
def test_a_batch_of_one_plate_still_reads_naturally():
    # Belt and braces on the pluralization — not reachable from a real
    # batch (a batch always covers at least one later night), but the
    # helper itself should never say "1 plates".
    one_plate = dict(ROAST_CHICKEN_BATCHED, servings=1)
    assert _run(f"mealWhatsInEyebrow({json.dumps(one_plate)})") == "What’s in it · two nights, 1 plate"


@_needs_node
def test_an_added_thing_and_a_thing_already_at_home_are_marked():
    meal = _with_side(SHRIMP, POTATOES, [{"item": "Yukon Gold potatoes", "qty": "1 lb", "added": True}])
    meal["ingredients"][2]["at_home"] = True
    html = _overview(meal)
    assert 'Yukon Gold potatoes <span class="wk-ing-tag">added</span>' in html
    assert 'Lemon <span class="wk-ing-tag">at home</span>' in html
    assert html.count("wk-ing-tag") == 2


@_needs_node
def test_a_past_day_shows_what_was_in_it_but_offers_nothing_to_add():
    html = _overview(SHRIMP, is_past=True)
    assert "What’s in it" in html
    assert "data-wk-add" not in html


@_needs_node
def test_a_reheat_night_or_a_missing_card_gets_no_overview():
    assert _run("mealWhatsInHtml({ isPast: false }, 'dinner', { state: 'planned' }, { isCook: false, cookMeal: null })") == ""
    assert _run("mealWhatsInHtml({ isPast: false }, 'dinner', { state: 'planned' }, { isCook: true, cookMeal: null })") == ""


# ---------- the screen, the sheet, the styles (source markers) ----------

def test_the_overview_sits_between_the_hero_and_the_clock():
    step = _extract("mealStepHtml")
    assert step.index("mealHeroHtml(") < step.index("mealWhatsInHtml(") < step.index("mealClockHtml(")


def test_everything_out_stays_as_the_mise_en_place_cue():
    assert "title: 'Everything out'" in _extract("mealClockStops")


def test_the_sheet_asks_the_plain_question_and_has_the_line_to_type():
    assert 'id="wk-add-sheet"' in SHELL_HTML
    assert "What should go with it?" in SHELL_HTML
    rows = _extract("mealAddRowsHtml")
    assert 'placeholder="Something else…"' in rows
    # Since 2026-09-13 (S10) a row is chosen, then saved: the "Add" on
    # every row became one Save under them — see test_plate_parts.py.
    assert 'id="wk-add-save"' in rows and ">Save</button>" in rows
    assert "coming soon" not in SHELL_JS.lower()


def test_the_add_flow_reaches_the_servers_three_calls_and_offers_undo():
    assert "/additions?entry_id=" in _extract("openMealAddSheet")
    add = _extract("runMealAdd")
    assert "/add-component" in add
    assert "label: 'Undo'" in add
    assert "is already on it." in add
    assert "/remove-component" in _extract("runMealAddUndo")
    # Wired from the overview's button.
    assert "[data-wk-add]" in _extract("wireMealsStep")


def test_the_copy_uses_contractions_and_no_dashboard_labels():
    for line in ("What should go with it?", "Add something", "What’s in it", "Taken back off.",
                 "That didn’t go on — try again."):
        assert line in SHELL_JS or line in SHELL_HTML, line
    for banned in ("Action required", "Add component", "Add ingredient", "Ingredients:"):
        assert banned not in _extract("mealWhatsInHtml")


def test_add_something_is_an_outline_control_not_a_second_apricot():
    i = SHELL_CSS.index(".wk-ing-add {")
    block = SHELL_CSS[i:SHELL_CSS.index("}", i)]
    assert "var(--apricot)" not in block
    assert "var(--hairline-strong)" in block
    assert "min-height: 44px" in block, "tappable at 44px (Rule 6)"
    for cls in (".wk-whatsin", ".wk-ing-list", ".wk-ing-qty", ".wk-ing-tag", ".wk-stop-tag",
                "#wk-add-sheet", ".wk-add-option", ".wk-add-input"):
        assert cls in SHELL_CSS, cls
    assert "#wk-add-scrim[hidden], #wk-add-sheet[hidden] { display: none; }" in SHELL_CSS
