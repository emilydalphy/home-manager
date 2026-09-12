"""
Meals as three steps — Week -> Day -> Meal (Emily's approved design,
2026-09-08, branch `flows-2-meals-week-day-meal`).

The screen answers one question, "what are we eating this week, and is it
settled?", and it answers it in three steps rather than in one stack. The
root is a single card of seven rows with a state badge; a day is three equal
cards; a meal is one meal in full. Everything rare — re-plan, pick my own
days, try again, change my answers, setup, start over — moved into a "More"
bottom sheet.

Two kinds of test live here, and the split is deliberate:

  * BEHAVIOUR, for the four things get_week_menu now has to hand the screen
    so it can describe one meal without a second round trip: the household's
    real slot clock, an entry's food groups, its defrost task, and — for a
    made-ahead night — the source dish and date kept APART from the headline
    sentence built out of them.
  * SOURCE MARKERS, for the front end, because shell.js has no JS test
    harness in this repo (see tests/test_frontend_restored_2026_09_08.py's
    docstring for the full reasoning, and for what a marker is worth). They
    are cheap tripwires: what the root renders, what it no longer renders,
    and what the two deeper steps carry.
"""
from __future__ import annotations

import datetime
from pathlib import Path

import pytest

from app import tools
from app.db import get_conn


TODAY = datetime.date.today()
# Anchored two days back so today is never the plan's first day — a
# cook-ahead source has to sit on an earlier day of the same plan.
WEEK_START = (TODAY - datetime.timedelta(days=2)).isoformat()
ISO_TODAY = TODAY.isoformat()
ISO_YESTERDAY = (TODAY - datetime.timedelta(days=1)).isoformat()

REPO = Path(__file__).resolve().parent.parent
SHELL_JS = (REPO / "static" / "shell.js").read_text(encoding="utf-8")
SHELL_CSS = (REPO / "static" / "shell.css").read_text(encoding="utf-8")
SHELL_HTML = (REPO / "static" / "shell.html").read_text(encoding="utf-8")


def _plan() -> int:
    return tools.create_weekly_plan(WEEK_START)["weekly_plan_id"]


def _entry_id(day: str, slot: str) -> int:
    conn = get_conn()
    row = conn.execute(
        "SELECT id FROM meal_plan_entries WHERE household_id = ? AND date = ? AND slot = ?",
        (tools.household_id(), day, slot),
    ).fetchone()
    conn.close()
    return row["id"]


def _day(menu: dict, iso: str) -> dict:
    return [d for d in menu["days"] if d["date"] == iso][0]


# ---------- what the Day and Meal steps need from the payload ----------

def test_the_week_carries_the_households_own_slot_clock():
    """
    "Dinner · 6:30" on the Day step is the same hour Today's timeline uses,
    because both read moves.py's mapping off the dinner_window rhythm fact.
    Two copies of that mapping is exactly how the two screens would come to
    put different times on the same meal.
    """
    tools.set_dinner_window("later")
    times = tools.get_week_menu()["slot_times"]
    assert times == {"breakfast": "8:00", "lunch": "12:30", "dinner": "8:00"}


def test_a_household_that_never_answered_gets_the_default_dinner_hour():
    assert tools.get_week_menu()["slot_times"]["dinner"] == "6:30"


def test_a_meal_carries_its_food_groups_and_says_there_is_nothing_to_thaw():
    """The Meal step's "The plate" card is read, never guessed: the groups
    are the ones the entry recorded, and `defrost` is None when the plan has
    no freezer move for it — which is what "Nothing to thaw." is written
    from."""
    tools.add_recipe(
        "Sheet-Pan Chicken", ingredients=[{"item": "Chicken", "qty": "1 lb"}],
        food_groups=["protein", "vegetable", "carb"],
    )
    plan_id = _plan()
    tools.plan_meal(ISO_TODAY, "Sheet-Pan Chicken", slot="dinner", weekly_plan_id=plan_id)

    dinner = _day(tools.get_week_menu(), ISO_TODAY)["dinner"]

    assert dinner["food_groups"] == ["protein", "vegetable", "carb"]
    assert dinner["defrost"] is None


def test_a_meal_with_a_freezer_move_carries_it():
    """The thaw line is the plan's own prep_tasks row — the same one Today's
    fridge move ticks — so the two can't disagree about what is frozen."""
    tools.add_recipe("Sheet-Pan Chicken", ingredients=[{"item": "Chicken", "qty": "1 lb"}])
    plan_id = _plan()
    tools.plan_meal(ISO_TODAY, "Sheet-Pan Chicken", slot="dinner", weekly_plan_id=plan_id)
    entry_id = _entry_id(ISO_TODAY, "dinner")
    conn = get_conn()
    conn.execute(
        "INSERT INTO prep_tasks (household_id, weekly_plan_id, task_date, description, "
        "related_meal, status, task_type, meal_plan_entry_id) "
        "VALUES (?, ?, ?, ?, ?, 'pending', 'defrost', ?)",
        (tools.household_id(), plan_id, ISO_YESTERDAY,
         "Move the chicken to the fridge", "Sheet-Pan Chicken", entry_id),
    )
    conn.commit()
    conn.close()

    dinner = _day(tools.get_week_menu(), ISO_TODAY)["dinner"]

    assert dinner["defrost"] == {
        "date": ISO_YESTERDAY, "note": "Move the chicken to the fridge",
    }


def test_a_made_ahead_night_names_its_source_apart_from_the_headline():
    """
    The card wants two different things out of one fact: the eyebrow says
    "Breakfast · made ahead <weekday>" and the dish name says "Egg White
    Bites". Both come from `leftover_from`, so the screen never has to unpick
    the headline sentence to get at half of it — and `cook_ahead` is the one
    word of difference between "made ahead" and "leftovers".
    """
    tools.add_recipe("Egg White Bites", ingredients=[{"item": "Eggs", "qty": "6"}])
    plan_id = _plan()
    tools.plan_meal(ISO_YESTERDAY, "Egg White Bites", slot="breakfast", weekly_plan_id=plan_id)
    tools.plan_meal(ISO_TODAY, "Egg White Bites", slot="breakfast", weekly_plan_id=plan_id)
    tools.set_cook_ahead(
        _entry_id(ISO_YESTERDAY, "breakfast"), [_entry_id(ISO_TODAY, "breakfast")]
    )

    breakfast = _day(tools.get_week_menu(), ISO_TODAY)["breakfast"]

    assert breakfast["source"] == "leftovers"
    assert breakfast["leftover_from"] == {
        "date": ISO_YESTERDAY, "meal": "Egg White Bites", "cook_ahead": True,
    }
    # The headline is still what it was — this adds a second reading of the
    # same fact, it does not replace the first.
    assert "Made ahead" in breakfast["title"]
    assert "Egg White Bites" in breakfast["title"]


def test_an_ordinary_cook_has_no_leftover_source():
    tools.add_recipe("Sheet-Pan Chicken", ingredients=[{"item": "Chicken", "qty": "1 lb"}])
    plan_id = _plan()
    tools.plan_meal(ISO_TODAY, "Sheet-Pan Chicken", slot="dinner", weekly_plan_id=plan_id)
    assert "leftover_from" not in _day(tools.get_week_menu(), ISO_TODAY)["dinner"]


def test_a_week_with_no_plan_still_says_when_the_meals_land():
    """The Meals tab renders before it has a plan, and the empty state is
    still the same screen — so the payload it gets has the same shape."""
    menu = tools.get_week_menu()
    assert menu["weekly_plan_id"] is None
    assert "slot_times" in menu


# ---------- the front end, by source marker ----------

def _assert_in(needle: str, haystack: str, what: str, where: str) -> None:
    assert needle in haystack, (
        f"{what} is missing from static/{where}.\n"
        f"Expected to find: {needle!r}\n"
        "This is Emily's approved Meals Week/Day/Meal design (2026-09-08). "
        "If the change is deliberate, update this test in the same commit "
        "and say why."
    )


def _assert_not_in(needle: str, haystack: str, what: str) -> None:
    assert needle not in haystack, (
        f"{what} is back on the Meals root: {needle!r}.\n"
        "The approved design replaces the old stack (review band, framing "
        "line, day rail, day card, whole-week row, approve row, reset link, "
        "Plan-a-week card, setup link) with ONE card of day rows plus a "
        "quiet 'Plan next week / More' line. Rare actions belong in the "
        "More sheet, not back on the page."
    )


def test_the_root_is_one_card_of_day_rows():
    """Seven rows (or the plan period's days), three dot-prefixed lines
    each, today's row tinted."""
    _assert_in("function weekStepHtml(", SHELL_JS, "the Week step", "shell.js")
    _assert_in("function weekRowHtml(", SHELL_JS, "the day row", "shell.js")
    _assert_in("function weekRowLineHtml(", SHELL_JS, "the per-slot line", "shell.js")
    _assert_in("wk-week-card", SHELL_JS, "the week card", "shell.js")
    _assert_in("data-wk-day=", SHELL_JS, "the row's day handle", "shell.js")
    _assert_in(".wk-day-row.is-today", SHELL_CSS, "today's tinted row", "shell.css")
    _assert_in("var(--celadon-tint)", SHELL_CSS, "the celadon tint on today's row", "shell.css")


@pytest.mark.parametrize("dot", ["is-cook", "is-ahead", "is-open"])
def test_the_row_dots_carry_the_whole_legend(dot):
    """Apricot cooks, celadon is already made, an outline is still a
    question. The dot IS the legend — there is no key anywhere on screen."""
    _assert_in(".wk-dot." + dot, SHELL_CSS, "the " + dot + " dot", "shell.css")


def test_names_truncate_to_one_line():
    """Emily's call: seven days truncated beats five in full."""
    _assert_in("text-overflow: ellipsis", SHELL_CSS, "the one-line truncation", "shell.css")


def test_an_open_slot_asks_to_be_picked():
    _assert_in("'Pick a ' + slot", SHELL_JS, "the open-slot line", "shell.js")


@pytest.mark.parametrize("badge", ["SET", "DRAFT", "NOTHING YET"])
def test_the_header_says_whether_the_week_is_settled(badge):
    _assert_in(badge, SHELL_JS, "the " + badge + " badge", "shell.js")


def test_the_subtitle_says_the_shape_of_the_week():
    """"Sep 7 – 13 · 4 cooks, 3 made ahead" — takeout is deliberately
    neither, since nobody cooks it and nobody made it ahead."""
    _assert_in("function weekCountsLabel(", SHELL_JS, "the cooks/made-ahead count", "shell.js")
    _assert_in("' made ahead'", SHELL_JS, "the made-ahead count copy", "shell.js")


@pytest.mark.parametrize("gone", [
    "day-rail",
    "whole-week-row",
    "week-reset-row",
    "#week-plan-row'",
    "week-setup-standing'",
])
def test_the_old_stack_is_off_the_root(gone):
    """
    The day rail, the whole-week row and the reset link are gone outright.
    The Plan-a-week card and the standing setup link still EXIST — the
    NOTHING YET state is built from them — so what's asserted here is that
    the root's own markup no longer names them: the only `#week-plan-row`
    left is the one weekStepHtml creates for the empty state, and the only
    `#week-setup-standing` is the one renderPlanWeekEntry puts inside it.
    """
    root_markup = SHELL_JS[SHELL_JS.index("async function buildWeekPanel("):
                           SHELL_JS.index("var planningPeriodDefault = null;")]
    _assert_not_in(gone, root_markup, gone)


def test_the_quiet_row_under_the_card():
    _assert_in("wk-plan-next", SHELL_JS, "the 'Plan next week' link", "shell.js")
    _assert_in("More ···", SHELL_JS, "the More entry point", "shell.js")


@pytest.mark.parametrize("action", [
    "wk-more-replan",     # Re-plan this week
    "week-period-open",   # Pick my own days (the existing picker, moved)
    "wk-more-try-again",  # Try again
    "wk-more-change",     # Change my answers
    "wk-more-setup",      # Adjust your setup
    "wk-more-reset",      # Start over
])
def test_the_more_sheet_holds_every_rare_action(action):
    _assert_in(action, SHELL_JS, "the More sheet's " + action + " row", "shell.js")


def test_the_more_sheet_exists_as_a_sheet():
    """A sheet over Meals, not a page (DESIGN_SYSTEM §6)."""
    _assert_in('id="meals-more-sheet"', SHELL_HTML, "the More sheet", "shell.html")
    _assert_in("#meals-more-sheet[hidden]", SHELL_CSS, "the sheet's hidden guard", "shell.css")
    _assert_in("function openMealsMoreSheet(", SHELL_JS, "the More sheet opener", "shell.js")


def test_the_custom_days_picker_moved_rather_than_being_rebuilt():
    """Same opener id, same picker id, same wirePeriodPicker — it only
    changed address."""
    _assert_in("wirePeriodPicker(rows, start, dayCount)", SHELL_JS,
               "the picker wired inside the More sheet", "shell.js")
    _assert_in("PERIOD_PICKER_COPY.open", SHELL_JS, "the picker's own copy", "shell.js")


def test_the_draft_review_band_is_gone_and_the_decision_moved_under_the_card():
    """
    UPDATED 2026-09-08 (flows-3-review-and-receipt), deliberately, per the
    honesty rule this file's sibling states at the top of
    tests/test_frontend_restored_2026_09_08.py.

    This test used to assert that `renderWeekReviewBand` still rendered
    `#week-review-band` above the week card, and that its button still read
    "Approve the week" — true for flows 2, which said in its own decision-log
    entry that "flows 3 replaces the band". Flows 3 did: review IS the week
    card, so the band, its DRAFT · YOUR TURN eyebrow, its status line and its
    grocery promise are gone, and the decision is one apricot plus one quiet
    link directly under the card (weekDecideHtml). The assertion is inverted
    rather than deleted — the band must not come back — and the two things
    that mattered about it (a draft has an Approve button, and it belongs to
    the ROOT only) are asserted in their new place. Approval itself is
    untouched: see tests/test_flows_3_review_and_receipt.py.
    """
    assert "function renderWeekReviewBand(" not in SHELL_JS, (
        "renderWeekReviewBand is back. Emily's approved 2026-09-08 design "
        "removed the review band: review is the week card itself."
    )
    assert '<div id="week-review-band">' not in SHELL_JS, (
        "#week-review-band is back above the week card."
    )
    # UPDATED 2026-09-11 (Build 3 of the screen-by-screen redesign, Emily's decisions C and E): a draft's root IS the review, so the week card's own decision strip (Check the week / Approve this week / the italic Tweak it with me) is gone; the one apricot is reviewDecideHtml's, in the dock.
    _assert_in("function reviewDecideHtml(", SHELL_JS, "the draft's decision (in the review it opens on)", "shell.js")
    _assert_in("Approve and build my shopping list", SHELL_JS, "the Approve button", "shell.js")
    _assert_in("if (approve) approve.hidden = !onRoot || draft;", SHELL_JS,
               "the receipt row above the card belonging to the root only (a draft's clash sits on its dish instead)", "shell.js")


def test_the_day_step_is_three_equal_cards():
    _assert_in("function dayStepHtml(", SHELL_JS, "the Day step", "shell.js")
    _assert_in("function daySlotCardHtml(", SHELL_JS, "the slot card", "shell.js")
    _assert_in("WEEK_SLOTS.map(function (slot) { return daySlotCardHtml(day, slot); })", SHELL_JS,
               "one card per slot, in slot order", "shell.js")
    _assert_in("‹ This week", SHELL_JS, "the Day step's back link", "shell.js")
    _assert_in("function slotEyebrow(", SHELL_JS, "the slot eyebrow", "shell.js")
    _assert_in("slot_times", SHELL_JS, "the slot clock", "shell.js")
    _assert_in("function dayAttendanceLine(", SHELL_JS, "the attendance subtitle", "shell.js")
    _assert_in("both home tonight", SHELL_JS, "the attendance copy", "shell.js")


def test_the_day_step_offers_cook_this_and_swap():
    _assert_in("function slotActionsHtml(", SHELL_JS, "the two-segment control", "shell.js")
    _assert_in("'Cook this' + (time ? ' · ' + time : '')", SHELL_JS, "'Cook this · 55 min'", "shell.js")
    _assert_in("REHEAT_ACTION_LABEL", SHELL_JS, "'Mark eaten' for a reheat", "shell.js")
    _assert_in("data-wk-swap=", SHELL_JS, "the Swap segment", "shell.js")
    _assert_in("' for something else'", SHELL_JS, "the swap prefill", "shell.js")


def test_cook_this_still_passes_the_exact_meal():
    """The focus target is the mechanism 42d422a built — an entry id, then
    date+slot, then the dish name — so cook mode lands on the meal that was
    tapped and never on a different one.

    UPDATED 2026-09-08 (flows-4-kitchen-and-preferences), deliberately: the
    key is `cookFocus` rather than `mealsFocus`, because cook mode is a step
    of the Kitchen tab now rather than a state of Meals. The resolver it
    feeds (cookResolveFocusIndex) and the four fields it carries are
    unchanged — only the tab it aims at moved.

    UPDATED 2026-09-09 (overnight/tap-a-meal-opens-recipe), also
    deliberately: "Cook this" goes through openRecipeFor now, the one door
    every dish-name tap in the app uses, so that cook mode's back link can
    name the Meals step it came from instead of saying Kitchen. The target
    it hands over is the same four fields, and openRecipeFor's own last
    line is still activateTab('kitchen', ..., { cookFocus }).
    """
    _assert_in("function openRecipeFor(target, origin)", SHELL_JS,
               "the one door every dish-name tap goes through", "shell.js")
    _assert_in("activateTab('kitchen', true, { cookFocus: target });",
               SHELL_JS, "the cook mode entry", "shell.js")
    _assert_in("entryId: entry ? entry.entry_id : null", SHELL_JS, "the focused entry", "shell.js")
    _assert_in("function cookResolveFocusIndex(", SHELL_JS, "the focus resolver", "shell.js")


def test_the_meal_step_carries_the_plate_and_the_two_actions():
    _assert_in("function mealStepHtml(", SHELL_JS, "the Meal step", "shell.js")
    _assert_in("function plateCardHtml(", SHELL_JS, "'The plate' card", "shell.js")
    _assert_in("The plate", SHELL_JS, "'The plate' title", "shell.js")
    _assert_in("Nothing to thaw.", SHELL_JS, "the no-thaw line", "shell.js")
    _assert_in("entry.food_groups", SHELL_JS, "the plate components", "shell.js")
    _assert_in("entry.defrost", SHELL_JS, "the thaw fact", "shell.js")
    _assert_in("Why this night", SHELL_JS, "the reason card", "shell.js")
    _assert_in(".wk-act.is-apricot", SHELL_CSS,
               "the Meal step's one apricot action", "shell.css")


def test_the_meal_step_reuses_the_cook_ahead_picker():
    """Same picker, same POST (cookSetCookAhead) — not a second copy of the
    rules about which days a batch may cover."""
    _assert_in("function cookMealForEntry(", SHELL_JS, "the cook card lookup", "shell.js")
    _assert_in("cookAheadHtml(cookMeal)", SHELL_JS, "the reused picker", "shell.js")
    _assert_in("await cookSetCookAhead(go)", SHELL_JS, "the reused write", "shell.js")


def test_the_steps_are_states_not_routes():
    """Back links and the browser's back gesture step out one level; a
    refresh lands on the week, because nothing restores a deeper step."""
    _assert_in("function goMealsStep(", SHELL_JS, "the step machine", "shell.js")
    _assert_in("function pushMealsStepHistory(", SHELL_JS, "the step's history entry", "shell.js")
    _assert_in("function applyMealsStepFromHistory(", SHELL_JS, "the back gesture", "shell.js")
    _assert_in("if (currentTabKey() === 'week') applyMealsStepFromHistory(e && e.state);", SHELL_JS,
               "the single popstate listener", "shell.js")
    _assert_in("step: 'week', mealSlot: 'dinner'", SHELL_JS, "the default step", "shell.js")


def test_a_changed_day_still_lands_on_that_day():
    """Today's and the tweak sheet's "See your week" name a date and a slot;
    it opens the Day step for that date with the changed meal ringed."""
    _assert_in("function focusChangedWeekDay(", SHELL_JS, "the See-your-week handoff", "shell.js")
    _assert_in("goMealsStep('day', { dayIndex: index });", SHELL_JS,
               "landing on the Day step", "shell.js")
    _assert_in(".wk-slot-card[data-wk-slot=\"' + pending.slot + '\"]", SHELL_JS,
               "the ring on the changed meal", "shell.js")
    _assert_in("just-changed", SHELL_JS, "the ring itself", "shell.js")


def test_the_chat_no_longer_carries_a_meals_hint():
    """The per-tab hint went with the always-open bar on 2026-09-11 (the
    chat is an icon; Build 1 of the screen-by-screen redesign) — one line
    on every tab, because the topic isn't always this week."""
    assert "Tweak this week with me" not in SHELL_JS


# ---------- snacks render too (2026-09-08, "snack-swap-applies") ----------
#
# The backend fix landed first (WEEK_SLOTS stayed the 21-slot guarantee,
# DAY_SLOTS and get_week_menu's `snacks` list arrived beside it) but that
# decision-log entry says plainly: "shell.js still hard-codes its own
# WEEK_SLOTS of three and will not draw them until it reads day.snacks."
# These tests run the screen's own render functions under node — a meal
# dict in, HTML out — rather than reading the source for the right words,
# because the whole bug being fixed was "the write landed and the screen
# had nowhere to draw it," and a source-marker test cannot catch that
# class of bug again.

import json
import re
import shutil

import nodeharness

_needs_node = pytest.mark.skipif(
    shutil.which("node") is None, reason="node is needed to execute the screen's own functions"
)


def _extract(name: str, source: str) -> str:
    """Lift one brace-balanced `function name(...) {...}` out of the file."""
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


def _run_node(harness: str):
    res = nodeharness.run_node(harness, timeout=30)
    assert res.returncode == 0, f"node failed: {res.stderr}"
    return json.loads(res.stdout.strip())


_ESCAPE_STUB = (
    "function escapeHtml(s){return String(s == null ? '' : s)"
    ".replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');}\n"
)
_DAYNAME_STUB = "function dayName(d, opts){ return 'Thursday'; }\n"


def _week_row_html(day: dict) -> str:
    """weekRowHtml(day, 0), the day row on the Week root — three meal lines
    then, per the 2026-09-08 design, one line per snack."""
    harness = (
        _ESCAPE_STUB + _DAYNAME_STUB
        + "var WEEK_SLOTS = ['breakfast', 'lunch', 'dinner'];\n"
        + _extract("mealDisplayName", SHELL_JS) + "\n"
        + _extract("awayLineFor", SHELL_JS) + "\n"
        + _extract("isRealCook", SHELL_JS) + "\n"
        + _extract("weekRowLineHtml", SHELL_JS) + "\n"
        + _extract("weekSnackLineHtml", SHELL_JS) + "\n"
        + _extract("weekRowHtml", SHELL_JS) + "\n"
        + f"console.log(JSON.stringify(weekRowHtml({json.dumps(day)}, 0)));\n"
    )
    return _run_node(harness)


def _plain_day(snacks: list) -> dict:
    return {
        "date": "2026-09-10", "isToday": False, "isPast": False,
        "breakfast": None, "lunch": None, "dinner": None,
        "snacks": snacks,
        "snack": snacks[0] if snacks else None,
    }


_GRAB_AND_GO_SNACK = {
    "state": "planned", "title": "Apple slices", "source": "plan", "meta": None,
}
_REAL_COOK_SNACK = {
    "state": "planned", "title": "Baked Oatmeal Cups", "source": "plan", "meta": "20 min",
}


@_needs_node
def test_a_day_with_two_snacks_renders_two_snack_lines_in_order():
    html = _week_row_html(_plain_day([_GRAB_AND_GO_SNACK, _REAL_COOK_SNACK]))
    names = re.findall(r'wk-line-name">([^<]*)<', html)
    # Three meal lines (all "Nothing yet" on this bare day) then the two
    # snacks, in the order the plan holds them.
    assert names[3:] == ["Apple slices", "Baked Oatmeal Cups"]
    dots = re.findall(r'wk-dot ([\w-]+)"', html)
    # A grab-and-go snack gets the grey/none dot; only the real recipe earns
    # the apricot cook dot — the one thing this design narrows beyond the
    # existing meal-line legend.
    assert dots[3:] == ["is-none", "is-cook"]


@_needs_node
def test_a_day_with_zero_snacks_renders_no_snack_lines():
    html = _week_row_html(_plain_day([]))
    assert len(re.findall(r'wk-line-name">', html)) == 3
    assert len(re.findall(r'wk-dot ', html)) == 3


@_needs_node
def test_the_counts_label_ignores_snacks():
    """WEEK_SLOTS stays the three real meals on purpose (2026-09-08 decision
    log) precisely so weekCountsLabel, which reads WEEK_SLOTS, never counts
    a snack as a cook — checked here by running it on a day whose only
    "cook" is a snack."""
    harness = (
        "var WEEK_SLOTS = ['breakfast', 'lunch', 'dinner'];\n"
        + _extract("weekCountsLabel", SHELL_JS) + "\n"
        + f"console.log(JSON.stringify(weekCountsLabel({json.dumps([_plain_day([_REAL_COOK_SNACK])])})));\n"
    )
    assert _run_node(harness) == ""


def _day_snack_cards_html(day: dict) -> str:
    harness = (
        _ESCAPE_STUB + _DAYNAME_STUB
        + "var weekState = { data: {} };\n"
        + "var swapState = null;\n"
        + "var REHEAT_ACTION_LABEL = 'Mark eaten';\n"
        # Only the one icon daySlotCardHtml actually draws (the chevron on
        # its openable button, added 2026-09-11 for item 11 of the
        # design-tidy pass) — the real GRO_ICONS lives with the Grocery
        # block this harness doesn't otherwise pull in.
        + "var GRO_ICONS = { chevRight: '<svg aria-hidden=\"true\"></svg>' };\n"
        + _extract("isSnackSlot", SHELL_JS) + "\n"
        + _extract("snackSlotKey", SHELL_JS) + "\n"
        + _extract("daySlotEntry", SHELL_JS) + "\n"
        + _extract("slotWord", SHELL_JS) + "\n"
        + _extract("slotEyebrowLabel", SHELL_JS) + "\n"
        + _extract("isRealCook", SHELL_JS) + "\n"
        + _extract("mealDisplayName", SHELL_JS) + "\n"
        + _extract("awayLineFor", SHELL_JS) + "\n"
        + _extract("chipsRowHtml", SHELL_JS) + "\n"
        + _extract("plateChips", SHELL_JS) + "\n"
        + _extract("cookTimeChip", SHELL_JS) + "\n"
        + _extract("swapStateFor", SHELL_JS) + "\n"
        + _extract("swapLineHtml", SHELL_JS) + "\n"
        + _extract("slotEyebrow", SHELL_JS) + "\n"
        + _extract("slotActionsHtml", SHELL_JS) + "\n"
        + _extract("daySlotCardHtml", SHELL_JS) + "\n"
        + _extract("daySnackCardsHtml", SHELL_JS) + "\n"
        + f"console.log(JSON.stringify(daySnackCardsHtml({json.dumps(day)})));\n"
    )
    return _run_node(harness)


@_needs_node
def test_two_snacks_render_two_day_step_cards_in_order():
    html = _day_snack_cards_html(_plain_day([_GRAB_AND_GO_SNACK, _REAL_COOK_SNACK]))
    slots = re.findall(r'data-wk-slot="([^"]+)"', html)
    assert slots == ["snack", "snack2"]
    # More than one snack that day, so both eyebrows are numbered — never
    # the bare "Snack" a solo snack gets.
    eyebrows = re.findall(r'wk-slot-eyebrow">([^<]*)<', html)
    assert eyebrows == ["Snack 1", "Snack 2"]
    names = re.findall(r'wk-slot-name[^>]*>([^<]*)<', html)
    assert names == ["Apple slices", "Baked Oatmeal Cups"]
    # Grab-and-go gets "Mark eaten"; a real recipe gets "Cook this".
    assert "Mark eaten" in html.split("snack2")[0]
    assert "Cook this" in html.split("snack2", 1)[1]


@_needs_node
def test_zero_snacks_renders_no_day_step_cards():
    assert _day_snack_cards_html(_plain_day([])) == ""


def _meal_step_html(day: dict, slot: str) -> str:
    harness = (
        _ESCAPE_STUB + _DAYNAME_STUB
        + "var weekState = { data: {} };\n"
        + "var swapState = null;\n"
        + "var REHEAT_ACTION_LABEL = 'Mark eaten';\n"
        + "var cookState = { data: { meals: [] }, cookAheadPicks: {} };\n"
        + _extract("isSnackSlot", SHELL_JS) + "\n"
        + _extract("daySlotEntry", SHELL_JS) + "\n"
        + _extract("slotWord", SHELL_JS) + "\n"
        + _extract("isRealCook", SHELL_JS) + "\n"
        + _extract("mealDisplayName", SHELL_JS) + "\n"
        + _extract("chipsRowHtml", SHELL_JS) + "\n"
        + _extract("cookTimeChip", SHELL_JS) + "\n"
        + _extract("plateCardIsEmpty", SHELL_JS) + "\n"
        + "function capitalizeFirst(s) { return String(s).charAt(0).toUpperCase() + String(s).slice(1); }\n"
        + "var PLATE_GROUP_LABELS = { protein: 'protein', carb: 'carb', vegetable: 'veg' };\n"
        + _extract("plateChips", SHELL_JS) + "\n"
        + _extract("plateCardHtml", SHELL_JS) + "\n"
        + _extract("cookMealForEntry", SHELL_JS) + "\n"
        # The recipe panel the Meal step borrows from the cook screen
        # (2026-09-09, overnight/tap-a-meal-opens-recipe) — rendered plain,
        # so none of its interactive branches is reached, but it still has
        # to be defined for the step to render at all.
        + _extract("cookStepLi", SHELL_JS) + "\n"
        + _extract("cookInstructionsHtml", SHELL_JS) + "\n"
        + _extract("cookDetailHtml", SHELL_JS) + "\n"
        + _extract("mealRecipeCardHtml", SHELL_JS) + "\n"
        + _extract("swapStateFor", SHELL_JS) + "\n"
        + _extract("swapLineHtml", SHELL_JS) + "\n"
        + _extract("slotActionsHtml", SHELL_JS) + "\n"
        # The hero head (2026-09-11) names the slot and sizes the dish name.
        + "var SLOT_LABELS = { breakfast: 'Breakfast', lunch: 'Lunch', dinner: 'Dinner' };\n"
        + _extract("slotEyebrowLabel", SHELL_JS) + "\n"
        + _extract("slotEyebrow", SHELL_JS) + "\n"
        + _extract("dishSizeClass", SHELL_JS) + "\n"
        + _extract("mealDockHtml", SHELL_JS) + "\n"
        + _extract("mealStepHtml", SHELL_JS) + "\n"
        + f"console.log(JSON.stringify(mealStepHtml({json.dumps(day)}, {json.dumps(slot)})));\n"
    )
    return _run_node(harness)


@_needs_node
def test_a_snack_renders_on_the_meal_step_without_a_plate_card():
    """A grab-and-go snack has no food groups, no added sides and no thaw
    task — "Nothing to thaw." on its own is an empty card wearing a
    caption, so it's hidden rather than shown (Emily's approved design)."""
    entry = dict(_GRAB_AND_GO_SNACK, entry_id=101, sides=[], food_groups=[], defrost=None, plate_note="")
    day = _plain_day([entry])
    html = _meal_step_html(day, "snack")
    assert "The plate" not in html
    assert "Apple slices" in html
    assert "Mark eaten" in html


@_needs_node
def test_a_real_cook_snack_still_shows_its_plate_card():
    """The hide is specific to an actually-empty plate — a snack that
    carries real food groups keeps the card exactly as any other slot
    would."""
    entry = dict(_REAL_COOK_SNACK, entry_id=102, sides=[], food_groups=["carb"], defrost=None, plate_note="")
    day = _plain_day([entry])
    html = _meal_step_html(day, "snack")
    assert "The plate" in html


def _ring_target_slots(pending_slot: str) -> list[str]:
    """applyPendingDayFocus, run with a fake panel of two snack-card
    stand-ins so the test can see exactly which one gets 'just-changed'
    without re-implementing the whole step machine."""
    harness = (
        "var weekState = { pendingDayFocus: " + json.dumps({"date": "2026-09-10", "slot": pending_slot}) + ", "
        "days: [{ date: '2026-09-10' }], selectedIndex: null };\n"
        "function goMealsStep(step, opts) { weekState.step = step; "
        "if (opts && opts.dayIndex != null) weekState.selectedIndex = opts.dayIndex; }\n"
        "function makeEl(slot) {\n"
        "  var el = { slot: slot, classes: [] };\n"
        "  el.classList = {\n"
        "    add: function (c) { el.classes.push(c); },\n"
        "    remove: function (c) { el.classes = el.classes.filter(function (x) { return x !== c; }); }\n"
        "  };\n"
        "  return el;\n"
        "}\n"
        "var els = [makeEl('snack'), makeEl('snack2')];\n"
        "var panel = { querySelector: function (sel) {\n"
        "  var m = /data-wk-slot=\"([^\"]+)\"/.exec(sel);\n"
        "  if (!m) return null;\n"
        "  for (var i = 0; i < els.length; i++) if (els[i].slot === m[1]) return els[i];\n"
        "  return null;\n"
        "} };\n"
        + _extract("applyPendingDayFocus", SHELL_JS) + "\n"
        + "applyPendingDayFocus(panel);\n"
        + "console.log(JSON.stringify(els.map(function (e) { return { slot: e.slot, classes: e.classes }; })));\n"
        + "process.exit(0);\n"
    )
    result = _run_node(harness)
    return [e["slot"] for e in result if "just-changed" in e["classes"]]


@_needs_node
def test_a_pending_snack_focus_rings_the_snack_card():
    """A chat swap on a snack carries bare slot 'snack' (app/main.py
    ChatAction) same as any other slot — no date/slot disambiguates WHICH
    snack, so this lands on the first, exactly as day.snack (the backend's
    own shorthand) already does, and never the second card by accident."""
    assert _ring_target_slots("snack") == ["snack"]
