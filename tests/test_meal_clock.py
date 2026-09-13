"""
The meal screen as a clock (Emily, 2026-09-12 — "Meal · B · The clock" from
the Beyond-lists canvas).

Under "‹ Monday": a spruce hero (DINNER · MONDAY, "On the table by half
six", the dish, "Start at 6:00" and "Emily's cooking" chips, the thaw as
its one line), then ONE eyebrow ("About thirty minutes, six stops") and the
stops — "Everything out" first, at the start time, then one stop per
instruction with the time it lands at — and the dock's "Start at 6:00" with
"Swap this meal" beside it. The plate card and the recipe card are gone.

The timing rule, as built in shell.js's mealClockStops:

  * start = the slot's table time (get_week_menu's slot_times, off the
    household's dinner_window) minus the recipe's total minutes (prep +
    cook — the arithmetic moves.py's "Start by 5:35" already does).
  * "Everything out" is at the start. Then one stop per instruction.
  * No recipe carries per-step minutes today, so the stops are spread
    evenly from the start to the table time — the last landing ON the
    table time — each rounded to the nearest five minutes and marked
    estimated, which is what makes the eyebrow say "About". Never seconds.
  * A recipe that ever does carry `step_minutes` gets exact, unrounded
    times instead, and no "About".
  * No total minutes at all: the stops with no times, and the eyebrow is
    just the count ("Six stops").

These run shell.js's own functions under node (tests/nodeharness.py), the
house standard for behaviour a source-marker test cannot see.
"""
from __future__ import annotations

import json
import re
import shutil
from pathlib import Path

import pytest

import nodeharness

REPO = Path(__file__).resolve().parent.parent
SHELL_JS = (REPO / "static" / "shell.js").read_text(encoding="utf-8")
SHELL_CSS = (REPO / "static" / "shell.css").read_text(encoding="utf-8")

_needs_node = pytest.mark.skipif(
    shutil.which("node") is None, reason="node is needed to execute shell.js's own functions"
)


def _extract(name: str) -> str:
    """Lift one brace-balanced `function name(...) {...}` out of shell.js."""
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
    + _var("NUMBER_WORDS") + "\n"
    + _var("TENS_WORDS") + "\n"
    + _var("STOP_TITLE_TAIL") + "\n"
    + _extract("isSnackSlot") + "\n"
    + "".join(_extract(n) + "\n" for n in (
        "numberWord", "countInWords", "minutesInWords", "clockLabel", "spokenTime",
        "slotTableMinutes", "mealTotalMinutes", "mealStepMinutes", "stopTitleSplit",
        "ingredientNamesLine", "mealClockStops", "mealClockEyebrow"))
)


def _run(expr: str):
    res = nodeharness.run_node(_PURE + f"console.log(JSON.stringify({expr}));\n", timeout=30)
    assert res.returncode == 0, f"node failed: {res.stderr}"
    return json.loads(res.stdout.strip())


def _stops(meal: dict, table: str | None = "6:30", slot: str = "dinner"):
    household = (
        f"{{ tableMinutes: slotTableMinutes({json.dumps({slot: table})}, {json.dumps(slot)}) }}"
        if table is not None else "null"
    )
    return _run(f"mealClockStops({json.dumps(meal)}, {household})")


STIR_FRY = {
    "meal": "Ginger Beef Stir-Fry",
    "has_full_recipe": True,
    "prep_time_minutes": 10,
    "cook_time_minutes": 20,
    "ingredients": [
        {"item": "Steak", "qty": "1 lb"},
        {"item": "Broccoli, chopped", "qty": "2 cups"},
        {"item": "Carrots", "qty": "2"},
        {"item": "Garlic", "qty": "3 cloves"},
        {"item": "Ginger", "qty": "1 tbsp"},
        {"item": "Rice", "qty": "1.5 cups"},
    ],
    "instructions": [
        "Heat the oil in a large pan over medium-high heat.",
        "Add the steak, sear 2 minutes a side.",
        "Toss in broccoli and carrots, cook until tender.",
        "Stir in garlic and ginger, then the sauce.",
        "Serve over rice.",
    ],
}


# ----------------------------------------------------------- the stops


@_needs_node
def test_a_recipe_with_minutes_starts_at_the_table_time_minus_its_minutes():
    stops = _stops(STIR_FRY)
    # Six stops: "Everything out" plus one per instruction.
    assert len(stops) == 6
    assert [s["kind"] for s in stops] == ["out"] + ["step"] * 5
    # 6:30 on the table, 30 minutes of cooking: everything out at 6:00.
    assert stops[0]["title"] == "Everything out"
    assert stops[0]["time"] == "6:00"
    assert stops[0]["minutes"] == 18 * 60


@_needs_node
def test_ingredients_come_first_as_names_only():
    stops = _stops(STIR_FRY)
    # Names only, the prep note ("chopped") shorn off, joined the way the
    # design draws it — the amounts are one tap in on the screen.
    assert stops[0]["line"] == "Steak · Broccoli · Carrots · Garlic · Ginger · Rice"
    assert "1 lb" not in stops[0]["line"]


@_needs_node
def test_spread_times_are_estimated_rounded_to_five_and_end_on_the_table():
    stops = _stops(STIR_FRY)
    times = [s["time"] for s in stops]
    # Spread evenly over 30 minutes across six stops (six-minute gaps),
    # each rounded to the nearest five: never 6:06, 6:12, 6:18, 6:24.
    assert times == ["6:00", "6:05", "6:10", "6:20", "6:25", "6:30"]
    assert all(s["minutes"] % 5 == 0 for s in stops)
    # The last stop lands exactly on the table time.
    assert stops[-1]["minutes"] == 18 * 60 + 30
    # And every one of them says so — this is the "About" rule's input.
    assert all(s["estimated"] for s in stops)


@_needs_node
def test_the_eyebrow_says_about_when_the_times_are_spread():
    got = _run(
        f"(function () {{ var s = mealClockStops({json.dumps(STIR_FRY)}, "
        "{ tableMinutes: 18 * 60 + 30 }); return mealClockEyebrow(s, mealTotalMinutes("
        f"{json.dumps(STIR_FRY)})); }})()"
    )
    # Words, not digits; "About" because no recipe says how long each step
    # takes, so the per-stop times are the app's spread, not the recipe's.
    assert got == "About thirty minutes, six stops"


@_needs_node
def test_a_recipe_with_per_step_minutes_gets_exact_unrounded_times_and_no_about():
    exact = dict(STIR_FRY, step_minutes=[3, 4, 7, 2, 1])
    stops = _stops(exact)
    # Total is the steps' own sum (17), so everything out is at 6:13; each
    # stop is the start plus the steps before it; nothing is rounded.
    assert [s["time"] for s in stops] == ["6:13", "6:13", "6:16", "6:20", "6:27", "6:29"]
    assert not any(s["estimated"] for s in stops)
    eyebrow = _run(
        f"mealClockEyebrow(mealClockStops({json.dumps(exact)}, {{ tableMinutes: 18 * 60 + 30 }}), 17)"
    )
    assert eyebrow == "Seventeen minutes, six stops"


@_needs_node
def test_a_recipe_with_no_minutes_shows_the_stops_with_no_times():
    untimed = dict(STIR_FRY, prep_time_minutes=None, cook_time_minutes=None)
    stops = _stops(untimed)
    assert len(stops) == 6
    assert all(s["time"] is None and s["minutes"] is None for s in stops)
    assert not any(s["estimated"] for s in stops)
    eyebrow = _run(f"mealClockEyebrow(mealClockStops({json.dumps(untimed)}, {{ tableMinutes: 1110 }}), null)")
    assert eyebrow == "Six stops"


@_needs_node
def test_no_table_time_means_no_times_either():
    # Minutes known, table unknown: nothing to subtract from, so no times
    # — and no "About", since nothing was spread.
    stops = _stops(STIR_FRY, table=None)
    assert all(s["time"] is None for s in stops)
    assert not any(s["estimated"] for s in stops)


@_needs_node
def test_rounding_never_shows_a_time_finer_than_five_minutes():
    # 33 minutes from 6:30 is 5:57 — the spread rounds it to 5:55, and
    # nothing in between lands off the grid either.
    odd = dict(STIR_FRY, prep_time_minutes=13, cook_time_minutes=20)
    stops = _stops(odd)
    assert stops[0]["time"] == "5:55"
    assert all(s["minutes"] % 5 == 0 for s in stops)
    assert stops[-1]["time"] == "6:30"
    # Never seconds, anywhere.
    assert all(re.fullmatch(r"\d{1,2}:\d{2}", s["time"]) for s in stops)


@_needs_node
def test_the_week_entrys_meta_is_enough_for_a_total():
    # The cooker view is what the screen reads, but the rule also accepts
    # the week entry's "35 min" meta and a plain `minutes`, so a caller with
    # only one of those still gets a start.
    assert _run("mealTotalMinutes({ meta: '35 min' })") == 35
    assert _run("mealTotalMinutes({ minutes: 45 })") == 45
    assert _run("mealTotalMinutes({ prep_time_minutes: 10, cook_time_minutes: 25 })") == 35
    assert _run("mealTotalMinutes({ meta: 'reheat' })") is None
    assert _run("mealTotalMinutes(null)") is None


@_needs_node
def test_a_recipe_with_no_ingredients_has_no_everything_out_stop():
    bare = dict(STIR_FRY, ingredients=[])
    stops = _stops(bare)
    assert [s["kind"] for s in stops] == ["step"] * 5
    assert stops[0]["time"] == "6:00" and stops[-1]["time"] == "6:30"


@_needs_node
def test_nothing_at_all_is_an_empty_list():
    assert _stops({"meal": "Takeaway", "has_full_recipe": False}) == []
    assert _stops(None) == []


# -------------------------------------------------------- stop titles


@_needs_node
def test_a_stop_is_titled_by_its_first_verb_phrase():
    got = _run(
        "[stopTitleSplit('Add the steak, sear 2 minutes a side.'),"
        " stopTitleSplit('Serve over rice.'),"
        " stopTitleSplit('Heat the oil in a large pan over medium-high heat.'),"
        " stopTitleSplit('Toss in broccoli and carrots, cook until tender.'),"
        " stopTitleSplit('add 1.5 cups of water and bring to the boil'),"
        " stopTitleSplit('')]"
    )
    # A short opening clause is the title, the rest the line.
    assert got[0] == {"title": "Add the steak", "line": "Sear 2 minutes a side."}
    # A whole short step is a title with no line.
    assert got[1] == {"title": "Serve over rice", "line": ""}
    # A long opening clause: its first three words, fewer if that would end
    # on a joining word ("Stir-fry broccoli and" is not a stop), and the
    # whole step as the line rather than one that starts mid-phrase.
    assert got[2]["title"] == "Heat the oil"
    assert got[2]["line"] == "Heat the oil in a large pan over medium-high heat."
    assert got[3]["title"] == "Toss in broccoli"
    assert _run("stopTitleSplit('Stir-fry broccoli and sliced carrots for 4-5 minutes until crisp-tender.')")["title"] == "Stir-fry broccoli"
    # A full stop inside a number is not the end of a phrase; sentence case.
    assert got[4]["title"] == "Add 1.5 cups"
    assert got[4]["line"].startswith("Add 1.5 cups of water")
    assert got[5] == {"title": "", "line": ""}


# ------------------------------------------------- time, as a person says it


@_needs_node
def test_the_table_time_is_said_the_way_a_person_says_it():
    got = _run(
        "[spokenTime(18 * 60 + 30), spokenTime(19 * 60 + 15), spokenTime(17 * 60 + 45),"
        " spokenTime(19 * 60), spokenTime(12 * 60), spokenTime(17 * 60 + 40),"
        " spokenTime(18 * 60 + 10), spokenTime(18 * 60 + 7), spokenTime(null)]"
    )
    assert got == [
        "half six", "a quarter past seven", "a quarter to six", "seven", "noon",
        "twenty to six", "ten past six",
        # Off the five-minute grid the clock is more honest than "seven past".
        "6:07", "",
    ]


@_needs_node
def test_numbers_are_words_up_to_twelve_and_minutes_are_said_aloud():
    got = _run(
        "[countInWords(6, 'stop'), countInWords(1, 'stop'), countInWords(12, 'stop'), countInWords(13, 'stop'),"
        " minutesInWords(30), minutesInWords(45), minutesInWords(60), minutesInWords(75),"
        " minutesInWords(90), minutesInWords(120), minutesInWords(130), minutesInWords(1)]"
    )
    assert got == [
        "six stops", "one stop", "twelve stops", "13 stops",
        "thirty minutes", "forty-five minutes", "an hour", "an hour and a quarter",
        "an hour and a half", "two hours", "two hours and ten minutes", "one minute",
    ]


@_needs_node
def test_slot_times_read_back_into_a_clock_the_slot_supplies_the_half_of_day():
    # get_week_menu's slot_times carry no am/pm (moves.py's _clock says the
    # time the way a person does), so the slot decides.
    got = _run(
        "[slotTableMinutes({ dinner: '6:30' }, 'dinner'), slotTableMinutes({ dinner: '5:30' }, 'dinner'),"
        " slotTableMinutes({ breakfast: '8:00' }, 'breakfast'), slotTableMinutes({ lunch: 'noon' }, 'lunch'),"
        " slotTableMinutes({ lunch: '12:30' }, 'lunch'), slotTableMinutes({ snack: '3:30' }, 'snack2'),"
        " slotTableMinutes({}, 'dinner'), slotTableMinutes(null, 'dinner'), slotTableMinutes({ dinner: '??' }, 'dinner')]"
    )
    assert got == [18 * 60 + 30, 17 * 60 + 30, 8 * 60, 12 * 60, 12 * 60 + 30, 15 * 60 + 30, None, None, None]


# ------------------------------------------------------------ the screen


_ESCAPE = (
    "function escapeHtml(s){return String(s == null ? '' : s)"
    ".replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/\"/g,'&quot;');}\n"
)


def _screen(day: dict, slot: str, cook_meals: list, rhythm: dict | None = None, ticked: list | None = None) -> str:
    harness = (
        _ESCAPE
        + "function dayName(d, opts){ return 'Monday'; }\n"
        + f"var weekState = {{ data: {{ slot_times: {{ breakfast: '8:00', lunch: '12:30', dinner: '6:30' }} }}, rhythm: {json.dumps(rhythm)} }};\n"
        + "var swapState = null;\n"
        + "var REHEAT_ACTION_LABEL = 'Mark eaten';\n"
        + f"var cookState = {{ data: {{ meals: {json.dumps(cook_meals)} }}, cookAheadPicks: {{}} }};\n"
        + "var GRO_ICONS = { chevRight: '<svg class=\"chev\"></svg>' };\n"
        + "var SLOT_LABELS = { breakfast: 'Breakfast', lunch: 'Lunch', dinner: 'Dinner' };\n"
        + "function cookMealKey(m) { return 'e' + m.entry_id; }\n"
        + f"var TICKED = {json.dumps(ticked or [])};\n"
        + "function cookTicked(kind, key) { return TICKED.indexOf(kind + ':' + key) !== -1; }\n"
        + "function cookAheadHtml() { return ''; }\n"
        + "function cookIngredientLabel(i) { return ((i.qty ? i.qty + ' ' : '') + i.item).trim(); }\n"
        + _PURE
        + "".join(_extract(n) + "\n" for n in (
            "daySlotEntry", "slotWord", "isRealCook", "mealDisplayName", "cookMealForEntry",
            "mealCookName", "mealCookUnderway", "mealClockFor", "mealHeroLine", "mealHeroHtml",
            "mealStopHtml", "mealClockHtml", "swapStateFor", "swapLineHtml", "slotEyebrowLabel",
            "dishSizeClass", "mealDockHtml", "mealStepHtml"))
        + f"console.log(JSON.stringify(mealStepHtml({json.dumps(day)}, {json.dumps(slot)})));\n"
    )
    res = nodeharness.run_node(harness, timeout=30)
    assert res.returncode == 0, f"node failed: {res.stderr}"
    return json.loads(res.stdout.strip())


def _monday(dinner: dict | None) -> dict:
    return {
        "date": "2026-09-14", "isToday": False, "isPast": False,
        "breakfast": None, "lunch": None, "dinner": dinner, "snacks": [], "snack": None,
    }


_DINNER = {
    "state": "planned", "title": "Ginger Beef Stir-Fry", "source": "plan", "meta": "30 min",
    "entry_id": 7, "sides": [], "food_groups": ["protein", "vegetable", "carb"],
    "defrost": None, "plate_note": "",
}
_COOK_CARD = dict(STIR_FRY, entry_id=7, cooked_status="pending", is_leftovers=False,
                  date="2026-09-14", slot="dinner")


@_needs_node
def test_the_screen_is_the_hero_the_eyebrow_the_stops_and_the_dock():
    html = _screen(_monday(_DINNER), "dinner", [_COOK_CARD],
                   rhythm={"cooking_role": {"value": "one_person", "who": "Emily"}})
    # The crumb names its parent.
    assert '<button type="button" class="crumb" data-wk-back="day">‹ Monday</button>' in html
    # The hero: eyebrow, the table time in words, the dish, the two chips.
    assert 'class="hero-eyebrow">Dinner · Monday<' in html
    assert 'class="wk-meal-by">On the table by half six<' in html
    assert "Ginger Beef Stir-Fry" in html
    chips = re.findall(r'hero-chip">([^<]*)<', html)
    assert chips == ["Start at 6:00", "Emily’s cooking"]
    # ONE eyebrow under the hero, then the stops.
    assert html.count('class="wk-clock-eyebrow"') == 1
    assert 'class="wk-clock-eyebrow">About thirty minutes, six stops<' in html
    titles = re.findall(r'wk-stop-title">([^<]*)<', html)
    assert titles == ["Everything out", "Heat the oil", "Add the steak", "Toss in broccoli",
                      "Stir in garlic", "Serve over rice"]
    times = re.findall(r'wk-stop-time">([^<]*)<', html)
    assert times == ["6:00", "6:05", "6:10", "6:20", "6:25", "6:30"]
    # The first stop is the apricot one; only it carries is-first.
    assert html.count("wk-stop is-first") == 1
    # The amounts are one tap in, in a person's units, hidden until then.
    assert 'class="wk-stop-amounts" hidden' in html
    assert "<li>1 lb Steak</li>" in html
    assert 'aria-expanded="false"' in html
    # The dock: the start as the one action, the swap as the quiet link.
    assert '<div class="wk-decide dock wk-meal-dock">' in html
    assert 'class="dock-primary" data-wk-cook="dinner">Start at 6:00<' in html
    assert 'class="dock-link wk-act-swap" data-wk-swap="dinner">Swap this meal<' in html
    # The swap line's idle state stays on the Day step; the dock here is the
    # one action and its one quiet link.
    assert "Tell me what instead" not in html
    # And what is gone.
    for gone in ("The plate", "The recipe", "wk-recipe-card", "cook-detail", "Serves ", "Nothing to thaw"):
        assert gone not in html, gone


@_needs_node
def test_no_known_cook_means_no_cooking_chip():
    for rhythm in (None, {"cooking_role": {"value": "turns", "who": None}},
                   {"cooking_role": {"value": "whoever_free", "who": None}}):
        html = _screen(_monday(_DINNER), "dinner", [_COOK_CARD], rhythm=rhythm)
        assert re.findall(r'hero-chip">([^<]*)<', html) == ["Start at 6:00"]


@_needs_node
def test_the_thaw_note_is_the_heros_one_line():
    entry = dict(_DINNER, defrost={"note": "Steak out of the freezer Sunday night"})
    html = _screen(_monday(entry), "dinner", [_COOK_CARD])
    assert 'class="hero-accent wk-meal-line">Steak out of the freezer Sunday night.<' in html
    # No line at all when there is nothing to thaw — not "Nothing to thaw."
    html = _screen(_monday(_DINNER), "dinner", [_COOK_CARD])
    assert "wk-meal-line" not in html


@_needs_node
def test_before_the_cook_view_loads_the_dock_says_start_cooking_and_the_clock_waits():
    # This harness has no planCookView, so the screen reads as "the view has
    # answered and this entry is not on it" — the no-recipe line. The
    # waiting line ("Getting the recipe…") is covered in
    # tests/test_tap_a_meal_opens_recipe.py.
    html = _screen(_monday(_DINNER), "dinner", [])
    assert "wk-stops" not in html
    assert "No saved recipe for this one" in html
    assert 'data-wk-cook="dinner">Start cooking<' in html
    # The hero still knows the table time — that is the week's own fact.
    assert "On the table by half six" in html


@_needs_node
def test_a_cook_already_under_way_offers_to_keep_cooking():
    html = _screen(_monday(_DINNER), "dinner", [_COOK_CARD], ticked=["steps:e7:0", "steps:e7:1"])
    assert 'data-wk-cook="dinner">Keep cooking<' in html
    # Marked cooked: not under way any more, and the start is offered again.
    done = dict(_COOK_CARD, cooked_status="done")
    html = _screen(_monday(_DINNER), "dinner", [done], ticked=["steps:e7:0"])
    assert 'data-wk-cook="dinner">Start at 6:00<' in html


@_needs_node
def test_a_reheat_night_is_a_hero_with_its_provenance_and_mark_eaten():
    entry = {
        "state": "planned", "title": "Leftovers — Sunday’s Bulgogi", "source": "leftovers", "meta": "reheat",
        "entry_id": 9, "sides": [], "food_groups": [], "defrost": None, "plate_note": "",
        "leftover_from": {"date": "2026-09-13", "meal": "Bulgogi", "cook_ahead": False},
    }
    card = {"entry_id": 9, "meal": "Bulgogi", "is_leftovers": True, "ingredients": [], "instructions": [],
            "has_full_recipe": False, "cooked_status": "pending"}
    html = _screen(_monday(entry), "dinner", [card])
    assert "wk-clock" not in html
    assert re.findall(r'hero-chip">([^<]*)<', html) == []  # no start, nobody cooks
    assert "Leftovers from Monday." in html  # dayName is stubbed to Monday
    assert 'data-wk-cook="dinner">Mark eaten<' in html
    assert "On the table by half six" in html


@_needs_node
def test_a_past_day_has_no_dock():
    day = dict(_monday(_DINNER), isPast=True)
    html = _screen(day, "dinner", [_COOK_CARD])
    assert "wk-meal-dock" not in html
    assert "wk-clock" in html  # the clock is still worth reading


@_needs_node
def test_a_dinner_with_no_recipe_says_so():
    card = {"entry_id": 7, "meal": "Ginger Beef Stir-Fry", "is_leftovers": False, "has_full_recipe": False,
            "ingredients": [], "instructions": [], "cooked_status": "pending"}
    html = _screen(_monday(_DINNER), "dinner", [card])
    assert "No saved recipe for this one" in html
    assert "wk-stops" not in html
    # The week entry's own "30 min" still gives the dock a start.
    assert 'data-wk-cook="dinner">Start at 6:00<' in html


# ----------------------------------------------------------- the CSS


def _rule(selector: str) -> str:
    start = SHELL_CSS.index(selector + " {")
    return SHELL_CSS[start : SHELL_CSS.index("}", start)]


def test_the_stops_are_the_designs_grid_and_every_colour_is_a_token():
    assert "grid-template-columns: 48px 20px minmax(0, 1fr)" in _rule(".wk-stop")
    assert "column-gap: 10px" in _rule(".wk-stop")
    time = _rule(".wk-stop-time")
    assert "font-size: 13px" in time and "font-weight: 800" in time
    assert "font-variant-numeric: tabular-nums" in time
    assert "var(--ink-secondary)" in time
    assert "var(--ink)" in _rule(".wk-stop.is-first .wk-stop-time")
    dot = _rule(".wk-stop-dot")
    assert "width: 12px" in dot and "var(--surface)" in dot and "2px solid var(--hairline-strong)" in dot
    assert "var(--apricot)" in _rule(".wk-stop.is-first .wk-stop-dot")
    assert "width: 1.5px" in _rule(".wk-stop-spine::after") and "var(--hairline)" in _rule(".wk-stop-spine::after")
    title = _rule(".wk-stop-title")
    assert "var(--font-display)" in title and "font-size: 16px" in title and "font-weight: 700" in title
    line = _rule(".wk-stop-line")
    assert "font-size: 14px" in line and "font-weight: 500" in line and "var(--ink-secondary)" in line
    # The hero: 20px gutter, the design's radii, 18px padding, the dish at 28px.
    hero = _rule(".wk-meal-hero")
    assert "margin: 6px 20px 0" in hero and "padding: 18px" in hero
    assert "border-radius: var(--radius-card) var(--radius-card) var(--radius-hero) var(--radius-hero)" in hero
    assert "font-size: 28px" in _rule(".wk-meal-dish") and "-.036em" in _rule(".wk-meal-dish")
    assert "var(--ivory-ink-muted)" in _rule(".wk-meal-by") and "font-size: 13px" in _rule(".wk-meal-by")
    # The whole meal block: no literal colour, no italics, nothing under 44px to tap.
    block = SHELL_CSS[SHELL_CSS.index("/* The Meal step (Emily, 2026-09-12"):SHELL_CSS.index(".wk-act {")]
    assert not re.search(r"#[0-9a-fA-F]{3,8}\b", block), "Rule 9 — every colour goes through a token"
    assert "italic" not in block, "§3 — no new italics"
    assert "min-height: 44px" in _rule(".wk-stop-toggle")
    # The last stop clears the chat icon when there is no dock to do it.
    assert ".wk-meal-body:not(:has(+ .dock)) { padding-bottom: 84px; }" in SHELL_CSS
    # The old cards' rules went with the cards.
    assert ".wk-recipe-card" not in SHELL_CSS


def test_nothing_new_animates():
    """§4: exactly three animations in the app; the chevron on "Everything
    out" simply turns, it does not transition."""
    block = SHELL_CSS[SHELL_CSS.index("/* The Meal step (Emily, 2026-09-12"):SHELL_CSS.index(".wk-act {")]
    assert not re.search(r"^\s*(transition|animation)\b", block, re.M)


def test_the_stops_and_cook_mode_read_one_list():
    """The stops are built off the cooker view's `instructions`; cook mode's
    one-step-at-a-time stage walks the same array. Neither reads the week
    entry for steps, so the two screens can never disagree."""
    assert "(meal && meal.instructions) || []" in _extract("mealClockStops")
    assert "var steps = meal.instructions || [];" in _extract("cookStepStageHtml")
    assert "cookMealForEntry(entry.entry_id)" in _extract("mealStepHtml")
    # The dock's way in is the same door "Cook this" used — openRecipeFor —
    # so it lands on Before you start with this list.
    assert "data-wk-cook" in _extract("mealDockHtml")
    wire = SHELL_JS[SHELL_JS.index("function wireMealsStep("):]
    wire = wire[:wire.index("[data-wk-swap]")]
    assert "openRecipeFor({" in wire
    # The Plan tab fetches the household rhythm once for the cook chip.
    assert "ensureRhythmForMeals(panel);" in SHELL_JS
