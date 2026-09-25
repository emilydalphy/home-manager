"""
The four Plan cards of 2026-09-18 (Emily's approved boards 11, 11b, 12,
19 and 19b), run against shell.js's own renderers under node:

  1. Check the week — day tabs, a swipeable carousel of day cards, rows
     in Breakfast → Lunch → Dinner order with "Swap" (it read "Swap the meal"
     until 2026-09-25), the "?"
     help button, and the dock's "Approve · Open grocery list".
  2. All set is one thing — the tick, one line, two numbers, ONE button —
     and the freezer step with its chips and "What that means" card; the
     root's freezer row in its three states.
  3. Batch cooking assumed from prep days — no cook-ahead ask anywhere on
     Plan; a covered day reads "from Monday". (The rule's own tests are in
     tests/test_batch_from_prep_days.py.)
  4. The Plan root's rows — Done + Swap, the done state, "+ Add a meal",
     the swap sheet's markup (three picks + the two lines), and the
     approved root's dock with no apricot.
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

import nodeharness
from test_week_seven_tiles import _extract, _extract_var

REPO = Path(__file__).resolve().parent.parent
SHELL_JS = (REPO / "static" / "shell.js").read_text(encoding="utf-8")
SHELL_CSS = (REPO / "static" / "shell.css").read_text(encoding="utf-8")
SHELL_HTML = (REPO / "static" / "shell.html").read_text(encoding="utf-8")

_needs_node = pytest.mark.skipif(shutil.which("node") is None, reason="node is needed to run the renderers")


def _run(harness: str):
    res = nodeharness.run_node(harness, timeout=30)
    assert res.returncode == 0, f"node failed: {res.stderr}"
    return json.loads(res.stdout.strip())


_DAYNAME_STUB = (
    "function dayName(d, opts){ if (opts && opts.day) return d.slice(8).replace(/^0/, '');"
    " var names = ['Sunday','Monday','Tuesday','Wednesday','Thursday','Friday','Saturday'];"
    " var n = names[new Date(d + 'T00:00:00').getDay()];"
    " return opts && opts.weekday === 'short' ? n.slice(0, 3) : n; }\n"
)


def _prelude() -> str:
    return (
        _extract("escapeHtml", SHELL_JS) + "\n"
        + "var WEEK_SLOTS = ['breakfast', 'lunch', 'dinner'];\n"
        + "var SLOT_LABELS = { breakfast: 'Breakfast', lunch: 'Lunch', dinner: 'Dinner' };\n"
        + "var READY_CHECK = '<svg></svg>';\n"
        + "var GRO_ICONS = { chevRight: '<svg></svg>' };\n"
        + _DAYNAME_STUB
        + "var weekState = { days: [], data: null, selectedIndex: 0, step: 'week', draftView: 'menu', draftViewPlanId: null };\n"
        + "var defrostAskState = { planId: null, items: null, selected: {}, forceShow: false };\n"
        + "function periodRangeLabel(start, n) { return start + ' +' + n; }\n"
        + "function countOpenSlots() { return 0; }\n"
        + "function approveWithOpenLabel() { return 'Approve anyway'; }\n"
        + "function weekSuggestedNoteHtml() { return ''; }\n"
        + "function weekReplacesNote() { return ''; }\n"
        + "function dayAttendanceLine() { return ''; }\n"
        + "function planEntryLabel(n, which, planned) { return (planned ? 'Re-plan ' : 'Plan ') + (which === 'current' ? 'this week' : 'next week'); }\n"
        + "function openSlotCardHtml(date, slot, entry) { return '<div class=\"week-open-card\"></div>'; }\n"
        + "function wasRecentlyChanged() { return false; }\n"
        + _extract("weekPlanState", SHELL_JS) + "\n"
        + _extract("mealDisplayName", SHELL_JS) + "\n"
        + _extract("awayLineFor", SHELL_JS) + "\n"
        + _extract("joinList", SHELL_JS) + "\n"
        + _extract("isSnackSlot", SHELL_JS) + "\n"
        + _extract("snackSlotKey", SHELL_JS) + "\n"
        + _extract("slotWord", SHELL_JS) + "\n"
        + _extract("slotEyebrowLabel", SHELL_JS) + "\n"
        + _extract("daySlotEntry", SHELL_JS) + "\n"
        + _extract("daySlotKeys", SHELL_JS) + "\n"
        + _extract("defaultDayIndex", SHELL_JS) + "\n"
        + _extract("dishShortName", SHELL_JS) + "\n"
        + _extract_var("reviewState", SHELL_JS) + "\n"
        + _extract_var("WK_ICONS", SHELL_JS) + "\n"
        + _extract("reviewDayIsClosed", SHELL_JS) + "\n"
        + _extract("reviewClosedLine", SHELL_JS) + "\n"
        + _extract("reviewTileTags", SHELL_JS) + "\n"
        + _extract("wkRowMeta", SHELL_JS) + "\n"
        + _extract("wkRowMetaLine", SHELL_JS) + "\n"
        + _extract("wkRowMetaHtml", SHELL_JS) + "\n"
        + _extract("draftView", SHELL_JS) + "\n"
        + _extract_var("WK_MENU_LABELS", SHELL_JS) + "\n"
        + _extract_var("WK_MENU_NOUNS", SHELL_JS) + "\n"
        + _extract("wkMenuGroups", SHELL_JS) + "\n"
        + _extract("wkDaysPhrase", SHELL_JS) + "\n"
        + _extract("wkMenuFact", SHELL_JS) + "\n"
        + _extract("wkMenuRowHtml", SHELL_JS) + "\n"
        + _extract("wkMenuHtml", SHELL_JS) + "\n"
        + _extract("wkDayMealCount", SHELL_JS) + "\n"
        + _extract("wkMiniHtml", SHELL_JS) + "\n"
        + _extract("wkMealRowHtml", SHELL_JS) + "\n"
        + _extract("wkDayCardHtml", SHELL_JS) + "\n"
        + _extract("wkDayTabsHtml", SHELL_JS) + "\n"
        + _extract("wkDotsHtml", SHELL_JS) + "\n"
        + _extract("wkHelpButtonHtml", SHELL_JS) + "\n"
        + _extract("reviewOpenIndex", SHELL_JS) + "\n"
        + _extract("reviewStepHtml", SHELL_JS) + "\n"
        + _extract("reviewDecideHtml", SHELL_JS) + "\n"
        + _extract("wkDockMoreHtml", SHELL_JS) + "\n"
        + _extract("weekDecideHtml", SHELL_JS) + "\n"
        + _extract("weekSnackTileHtml", SHELL_JS) + "\n"
        + _extract("weekSnacksHtml", SHELL_JS) + "\n"
        + _extract("weekFrozenItems", SHELL_JS) + "\n"
        + _extract("weekFreezerRowHtml", SHELL_JS) + "\n"
        + _extract("weekDayHtml", SHELL_JS) + "\n"
        + _extract("allSetStepHtml", SHELL_JS) + "\n"
        + _extract("defrostAskChipHtml", SHELL_JS) + "\n"
        + _extract("defrostSelectedItems", SHELL_JS) + "\n"
        + _extract("defrostAskItemsAlreadyAnswered", SHELL_JS) + "\n"
        + _extract("defrostMeaningLines", SHELL_JS) + "\n"
        + _extract("defrostMeaningHtml", SHELL_JS) + "\n"
        + _extract("freezerStepHtml", SHELL_JS) + "\n"
        + _extract("swapMoveOptions", SHELL_JS) + "\n"
        + _extract_var("SWAP_WAIT_SECONDS", SHELL_JS) + "\n"
        + _extract_var("SWAP_PLACEHOLDERS", SHELL_JS) + "\n"
        + _extract("swapWaitLine", SHELL_JS) + "\n"
        + _extract("swapWaitHtml", SHELL_JS) + "\n"
        + _extract("swapPickHtml", SHELL_JS) + "\n"
        # The whole-dish line (2026-09-22) — empty for a one-day sheet.
        + _extract("swapDaysLine", SHELL_JS) + "\n"
        + _extract("swapSheetTitle", SHELL_JS) + "\n"
        + _extract("swapSheetBodyHtml", SHELL_JS) + "\n"
    )


def _entry(title, **kw):
    e = {"title": title, "state": "planned", "source": "plan", "meta": "25 min", "entry_id": 1, "cooked": False}
    e.update(kw)
    return e


def _day(date, iso_today=False, past=False, **slots):
    d = {"date": date, "isToday": iso_today, "isPast": past}
    d.update(slots)
    return d


_MON = "2026-09-21"
_TUE = "2026-09-22"
_WED = "2026-09-23"


def _week():
    return [
        _day(_MON, breakfast=_entry("Overnight oats", entry_id=11, meta=None),
             lunch=_entry("Chickpea salad jars", entry_id=12, meta="10 min"),
             dinner=_entry("Lemon chicken & orzo", entry_id=13, meta="35 min")),
        _day(_TUE, iso_today=True,
             breakfast=_entry("Made ahead — Monday's Overnight oats", entry_id=21, meta="reheat", source="leftovers",
                              leftover_from={"date": _MON, "meal": "Overnight oats", "cook_ahead": True}),
             lunch=_entry("Leftover orzo", entry_id=22, meta="reheat", source="leftovers"),
             dinner=_entry("Black bean tacos", entry_id=23, meta="25 min")),
        _day(_WED, breakfast=_entry("Eggs", entry_id=31), lunch=None,
             dinner={"title": "I’d like your call on this one", "state": "open", "source": "open", "entry_id": 33, "options": []}),
    ]


def _draft(days):
    return {"weekly_plan_id": 7, "status": "draft", "days": days, "week_label": "Sept 21 – 27", "day_count": 7}


def _approved(days, **extra):
    d = {"weekly_plan_id": 7, "status": "approved", "days": days, "week_label": "Sept 21 – 27", "day_count": 7,
         "receipt": {"meals": 15, "recipes": 9, "list_count": 53}, "defrost_asked_at": None}
    d.update(extra)
    return d


# ---------------------------------------------------------------------------
# 1. Check the week
# ---------------------------------------------------------------------------

@_needs_node
def test_check_the_week_opens_with_the_crumb_title_line_tabs_cards_and_dots():
    days = _week()
    html = _run(_prelude() + f"weekState.days = {json.dumps(days)};\n"
                f"console.log(JSON.stringify(reviewStepHtml({json.dumps(_approved(days))}, weekState.days, false)));")
    assert 'class="crumb" data-wk-back="week">‹ Plan</button>' in html
    assert '<h1 class="wk-title">Check the week.</h1>' in html
    assert "Sept 21 – 27 · 7 meals" in html, "the real range and the real count"
    assert html.count('class="wk-daytab"') + html.count('class="wk-daytab is-on"') == 3 and html.count("data-wk-daytab=") == 3
    assert html.count('data-wk-card="') == 3, "one card per day, in the carousel"
    assert 'id="wk-carousel"' in html
    assert html.count('class="wk-dot-page') == 3
    assert 'id="wk-help"' in html and 'data-wk-help="Check the week"' in html


@_needs_node
def test_each_card_has_the_day_the_count_and_rows_in_meal_order_with_swap():
    # "Swap the meal" until 2026-09-25: 1A's row line holds "Swap" and
    # "Tweak it" beside the time.
    days = _week()
    html = _run(_prelude() + f"weekState.days = {json.dumps(days)};\n"
                f"console.log(JSON.stringify(wkDayCardHtml({json.dumps(days[0])}, 0, {{ done: false, swapLabel: 'Swap' }})));")
    assert '<span class="wk-card-day">Monday</span>' in html
    assert '<span class="wk-card-count">3 meals</span>' in html
    order = [html.index(x) for x in ("Breakfast", "Lunch", "Dinner")]
    assert order == sorted(order)
    assert html.count(">Swap</button>") == 3
    assert "swapLabel: 'Swap the meal'" not in SHELL_JS
    assert html.count('data-wk-swap-sheet="') == 3
    assert 'data-wk-meal="dinner">Lemon chicken &amp; orzo<svg class="wk-row-chev"' in html, "the dish is the existing recipe link"
    assert '<span class="wk-row-meta">35 min</span>' in html
    assert "data-wk-done" not in html, "Check the week has no Done — that is the root's"


@_needs_node
def test_a_made_ahead_row_says_from_monday_and_an_open_row_offers_pick():
    days = _week()
    html = _run(_prelude() + f"weekState.days = {json.dumps(days)};\n"
                f"console.log(JSON.stringify([wkDayCardHtml({json.dumps(days[1])}, 1, {{}}), wkDayCardHtml({json.dumps(days[2])}, 2, {{}})]));")
    tue, wed = html
    assert 'data-wk-meal="breakfast">Overnight oats<svg class="wk-row-chev"' in tue, "the dish, not the whole made-ahead sentence"
    assert '<span class="wk-row-meta">from Monday</span>' in tue
    assert '<span class="wk-row-meta">leftovers</span>' in tue
    assert 'data-wk-pick="dinner"' in wed and ">Pick</button>" in wed
    assert 'id="wk-open-dinner" hidden' in wed
    assert '<span class="wk-row-name is-quiet">Nothing yet</span>' in wed


@_needs_node
def test_the_tabs_and_dots_mark_the_day_in_view():
    html = _run(_prelude() + "console.log(JSON.stringify([wkDayTabsHtml("
                + json.dumps(_week()) + ", 1), wkDotsHtml(3, 1)]));")
    tabs, dots = html
    assert 'class="wk-daytab is-on" role="tab" aria-selected="true" data-wk-daytab="1"' in tabs
    assert tabs.count("is-on") == 1 and ">Tue</button>" in tabs
    assert dots.count("is-on") == 1 and dots.index("is-on") > dots.index("wk-dot-page")


@_needs_node
def test_the_draft_dock_reads_approve_open_grocery_list_and_the_set_dock_open_grocery_list():
    days = _week()
    html = _run(_prelude() + f"weekState.days = {json.dumps(days)};\n"
                f"console.log(JSON.stringify([reviewDecideHtml({json.dumps(_draft(days))}), reviewDecideHtml({json.dumps(_approved(days))})]));")
    draft, approved = html
    assert 'id="week-approve-btn">Approve · Open grocery list</button>' in draft
    assert draft.count("wk-decide dock") == 1
    assert 'class="dock-primary" id="wk-review-go">Open grocery list</button>' in approved
    assert "Approve" not in approved, "an approved week is never asked to approve again"


@_needs_node
def test_the_draft_root_keeps_the_band_as_its_head_so_it_has_no_crumb_and_no_second_title():
    """Since 2026-09-21 the band carries the dates, the opener and the
    What we're eating | Which days toggle, so the count line and the "?"
    under it went too (tests/test_draft_front_door.py); Which days is the
    carousel as built here, unchanged."""
    days = _week()
    html = _run(_prelude() + f"weekState.days = {json.dumps(days)}; weekState.draftView = 'days'; weekState.draftViewPlanId = 7;\n"
                f"console.log(JSON.stringify(reviewStepHtml({json.dumps(_draft(days))}, weekState.days, true)));")
    assert "crumb" not in html
    assert "<h1" not in html
    assert "7 meals" not in html and 'id="wk-help"' not in html
    assert 'id="wk-carousel"' in html and html.count('data-wk-card="') == 3
    assert 'id="wk-more"' in html, "the draft's rare actions stay behind More"
    assert 'id="week-approve-btn"' in html


def test_the_old_review_is_gone_from_the_source():
    """No list-style review, no ± stepper, no "Which days" toggle, no
    "Change one", no one-time tips overlay."""
    for gone in ("reviewDishRowHtml", "reviewAddPickerHtml", "RV_MINUS_SVG", "Change one", "data-rv-view",
                 "coachCardHtml", "renderCoachCard", "coach-card-slot", "reviewDayTileHtml",
                 "wireReviewTiles"):
        assert gone not in SHELL_JS, gone
    assert "coach-card-slot" not in SHELL_HTML
    assert "#coach-sheet" not in SHELL_CSS
    assert ".rv-step-btn" not in SHELL_CSS and ".rv-tile {" not in SHELL_CSS


def test_the_help_button_opens_the_shared_need_a_hand_sheet():
    """static/help-sheet.js (Week 1) is loaded by shell.html ahead of
    shell.js, so the ? calls it outright — no fallback to the ask sheet."""
    src = _extract("openWeekHelp", SHELL_JS)
    assert src == "function openWeekHelp(screenName) {\n    openHelpSheet({ screenName: screenName });\n  }"
    assert SHELL_HTML.index('<script src="/static/help-sheet.js"></script>') < SHELL_HTML.index('<script src="/static/shell.js"></script>')


def test_a_tab_tap_scrolls_smoothly_unless_motion_is_reduced():
    wire = _extract("wireReviewCarousel", SHELL_JS)
    assert "window.matchMedia('(prefers-reduced-motion: reduce)').matches" in wire
    assert "behavior: smooth && !reduce ? 'smooth' : 'auto'" in wire


def test_the_carousel_and_tabs_are_sized_as_the_board_says():
    tab = SHELL_CSS[SHELL_CSS.index(".wk-daytab {"):SHELL_CSS.index("}", SHELL_CSS.index(".wk-daytab {"))]
    assert "height: 36px" in tab and "background: var(--sand)" in tab
    assert ".wk-daytab.is-on { background: var(--apricot); color: var(--on-accent-ink); }" in SHELL_CSS
    car = SHELL_CSS[SHELL_CSS.index(".wk-carousel {"):SHELL_CSS.index("}", SHELL_CSS.index(".wk-carousel {"))]
    assert "scroll-snap-type: x mandatory" in car and "overflow-x: auto" in car
    card = SHELL_CSS[SHELL_CSS.index(".wk-carousel .wk-day-card {"):]
    card = card[:card.index("}")]
    assert "width: 310px" in card and "scroll-snap-align: start" in card
    help_ = SHELL_CSS[SHELL_CSS.index(".wk-help {"):SHELL_CSS.index("}", SHELL_CSS.index(".wk-help {"))]
    assert "width: 44px" in help_ and "height: 44px" in help_ and "background: var(--sand)" in help_


# ---------------------------------------------------------------------------
# 2. All set and the freezer step
# ---------------------------------------------------------------------------

@_needs_node
def test_all_set_is_the_tick_one_line_two_numbers_and_one_button():
    days = _week()
    html = _run(_prelude() + f"console.log(JSON.stringify(allSetStepHtml({json.dumps(_approved(days))}, {json.dumps(days)})));")
    assert '<h1 class="wk-allset-title">All set.</h1>' in html
    assert '<p class="wk-allset-line">Sept 21 – 27 is planned.</p>' in html
    assert html.count("wk-allset-num\"") == 2
    assert ">15</span>" in html and ">meals</span>" in html and ">9</span>" in html and ">recipes</span>" in html
    assert "ingredients" not in html
    assert 'id="wk-allset-next">Next · Anything in the freezer?</button>' in html
    assert html.count("dock-primary") == 1 and "dock-secondary" not in html
    for gone in ("See the week", "thaw", "wk-allset-asks", "batch cook"):
        assert gone not in html, gone


@_needs_node
def test_the_first_plan_says_week_1_and_a_plan_with_nothing_to_ask_opens_the_list():
    days = _week()
    first = _approved(days, is_first_plan=True)
    html = _run(_prelude() + "defrostAskState = { planId: 7, items: [], selected: {} };\n"
                f"console.log(JSON.stringify([allSetStepHtml({json.dumps(first)}, {json.dumps(days)}), allSetStepHtml({json.dumps(_approved(days, defrost_asked_at='2026-09-20T10:00'))}, {json.dumps(days)})]));")
    first_html, answered = html
    assert "Week 1 is planned." in first_html
    assert 'id="wk-allset-next">Open grocery list</button>' in first_html, "no meat to ask about: straight to the list"
    assert 'id="wk-allset-next">Open grocery list</button>' in answered, "already answered for this plan"


_ITEMS = [
    {"item": "Chicken thighs", "nights": [{"date": _MON, "meal": "Lemon chicken & orzo", "weekday": "Monday",
                                           "slot": "dinner", "move_date": "2026-09-19", "move_weekday": "Saturday"}]},
    {"item": "Ground beef", "nights": [{"date": "2026-09-24", "meal": "Tacos", "weekday": "Thursday",
                                        "slot": "dinner", "move_date": _TUE, "move_weekday": "Tuesday"}]},
    {"item": "Salmon", "nights": [{"date": _WED, "meal": "Salmon", "weekday": "Wednesday",
                                   "slot": "dinner", "move_date": _MON, "move_weekday": "Monday"}]},
]


@_needs_node
def test_the_freezer_step_has_the_chips_the_meaning_card_and_the_two_answers():
    html = _run(_prelude() + f"defrostAskState = {{ planId: 7, items: {json.dumps(_ITEMS)}, selected: {{ 'Chicken thighs': true, 'Ground beef': true }} }};\n"
                f"console.log(JSON.stringify(freezerStepHtml({json.dumps(_approved(_week()))})));")
    assert 'class="crumb" data-wk-back="week">‹ Plan</button>' in html
    # Board F-A (2026-09-21): the title and line say what a tap does.
    assert "<h1 class=\"wk-title\">Anything already in the freezer?</h1>" in html
    assert "Tap what you’ve got frozen. I’ll take it off the shopping list and tell you when to move it to the fridge." in html
    assert html.count('class="defrost-chip') == 3 and html.count("defrost-chip is-selected") == 2
    # A selected chip carries the snowflake; an unselected one does not.
    salmon = html[html.index('data-defrost-chip="Salmon"') - 60:html.index('data-defrost-chip="Salmon"') + 80]
    assert "<svg" not in salmon.split('data-defrost-chip="Salmon"')[1]
    assert "What that means" in html
    assert "Chicken thighs → into the fridge Saturday night, for Monday’s dinner." in html
    assert "Ground beef → into the fridge Tuesday night, for Thursday’s dinner." in html
    assert "Salmon →" not in html, "only what is tapped"
    assert 'id="wk-freezer-go">Add to the schedule · Open grocery list</button>' in html
    assert 'id="wk-freezer-none">Nothing frozen — I’m buying it all</button>' in html
    assert html.count("dock-primary") == 1


@_needs_node
def test_the_freezer_step_with_nothing_to_ask_offers_the_list_and_no_card():
    html = _run(_prelude() + "defrostAskState = { planId: 7, items: [], selected: {} };\n"
                f"console.log(JSON.stringify(freezerStepHtml({json.dumps(_approved(_week()))})));")
    assert "Nothing in this week’s meals needs thawing." in html
    assert 'id="wk-freezer-list">Open grocery list</button>' in html
    assert "What that means" not in html and "defrost-chip" not in html


@_needs_node
def test_the_roots_freezer_row_asks_then_reads_the_answer():
    days = _week()
    days[0]["dinner"]["defrost"] = {"date": "2026-09-19", "note": "Move the Chicken thighs to the fridge — for Monday's Lemon chicken & orzo."}
    days[1]["dinner"]["defrost"] = {"date": _MON, "note": "Move the ground beef to the fridge — for Tuesday's tacos."}
    unanswered = _approved(days)
    answered = _approved(days, defrost_asked_at="2026-09-20T10:00")
    nothing = _approved(_week(), defrost_asked_at="2026-09-20T10:00")
    html = _run(_prelude() + f"console.log(JSON.stringify([weekFreezerRowHtml({json.dumps(unanswered)}, {json.dumps(days)}), "
                f"weekFreezerRowHtml({json.dumps(answered)}, {json.dumps(days)}), weekFreezerRowHtml({json.dumps(nothing)}, {json.dumps(_week())})]));")
    ask, done, none = html
    for row in html:
        assert 'data-wk-freezer="1"' in row and "wk-freezer-tile" in row and "wk-freezer-chev" in row
    assert "Anything in the freezer this week?" in ask and "is-answered" not in ask
    assert "Chicken thighs, ground beef — from the freezer" in done and "is-answered" in done
    assert "Nothing frozen this week" in none


def test_the_freezer_step_is_a_step_of_the_plan_flow_and_ends_when_plan_is_left():
    assert "} else if (weekState.step === 'freezer') {" in SHELL_JS
    assert "(weekState.step === 'allset' || weekState.step === 'freezer')" in SHELL_JS
    assert "search.indexOf('after=approve')" in SHELL_JS, "the reveal's hand-off"
    assert "goMealsStep('freezer', { replace: true })" in SHELL_JS
    tile = SHELL_CSS[SHELL_CSS.index(".wk-freezer-tile {"):SHELL_CSS.index("}", SHELL_CSS.index(".wk-freezer-tile {"))]
    assert "background: var(--celadon-tint)" in tile and "color: var(--celadon-label)" in tile
    means = SHELL_CSS[SHELL_CSS.index(".wk-freezer-means {"):SHELL_CSS.index("}", SHELL_CSS.index(".wk-freezer-means {"))]
    assert "background: var(--celadon-tint)" in means


def test_the_after_approve_hand_off_waits_for_the_freezer_items_and_says_approved_not_draft():
    """Integration follow-ups (2026-09-18): the Week 1 reveal hands in with
    ?after=approve&drafted=<Monday>. That week is past "change anything
    before you approve it", so that toast stays quiet; and the freezer
    step is only the landing when there is something to thaw — the items
    are one request, awaited, rather than landing on an empty step."""
    build = SHELL_JS[SHELL_JS.index("async function buildWeekPanel("):SHELL_JS.index("async function loadPlanningPeriodDefault(")]
    assert "if (drafted && !afterApprove) {" in build
    assert build.index("var afterApprove = ") < build.index("await loadWeekMenu(panel);")
    landing = build[build.index("if (afterApprove && weekState.data && weekPlanState(weekState.data) === 'set') {"):]
    assert "await ensureDefrostAskItems(panel, weekState.data);" in landing
    assert "ask = !!(defrostAskState.items && defrostAskState.items.length);" in landing
    assert "if (ask) goMealsStep('freezer', { replace: true });" in landing
    assert "else { goGroceryList(); showToast('Your week was approved'); }" in landing


# ---------------------------------------------------------------------------
# 3. No cook-ahead ask on Plan
# ---------------------------------------------------------------------------

def test_the_cook_ahead_ask_is_gone_from_all_set_and_the_root():
    for gone in ("cookAheadAskCardHtml", "cookAheadAskState", "ensureCookAheadAskItems", "submitCookAheadAsk",
                 "renderWeekReceipt", "renderAllSetAsks", "weekQuickOpen", "Do you want to batch cook any of these",
                 "cook-ahead-confirm", "cook-ahead-items"):
        assert gone not in SHELL_JS, gone
    # Cook's own links to the two asks went with the recipe screen's
    # clock ("The recipe is the recipe", same day), and their landings
    # with them.
    for gone in ("openCookAheadAskFromCook", "openDefrostAskFromCook", "cookAheadTallyLine"):
        assert gone not in SHELL_JS, gone


# ---------------------------------------------------------------------------
# 4. The Plan root: Done + Swap, Add a meal, the swap sheet, the dock
# ---------------------------------------------------------------------------

@_needs_node
def test_the_roots_rows_carry_done_and_swap_and_the_card_is_followed_by_add_a_meal_and_the_freezer_row():
    days = _week()
    html = _run(_prelude() + f"weekState.days = {json.dumps(days)};\n"
                f"console.log(JSON.stringify(weekDayHtml({json.dumps(days[1])}, 1, {json.dumps(_approved(days))})));")
    assert '<span class="wk-root-day-name">Tuesday</span>' in html and '<span class="wk-root-day-today">Today</span>' in html
    assert html.count('data-wk-done="') == 3 and html.count('data-wk-swap-sheet="') == 3
    assert html.count(">Done</button>") == 3 and html.count(">Swap</button>") == 3
    assert "Swap the meal" not in html
    order = [html.index('data-wk-card="1"'), html.index('data-wk-add-meal="1"'), html.index('data-wk-freezer="1"')]
    assert order == sorted(order), "card, then + Add a meal, then the freezer row"
    assert "Add a meal</button>" in html and "wk-mini wk-mini-add" in html


@_needs_node
def test_a_done_row_strikes_through_and_its_button_turns_celadon_with_put_back():
    days = _week()
    days[1]["dinner"]["cooked"] = True
    html = _run(_prelude() + f"weekState.days = {json.dumps(days)};\n"
                f"console.log(JSON.stringify(wkDayCardHtml({json.dumps(days[1])}, 1, {{ done: true, swapLabel: 'Swap' }})));")
    import re
    assert re.findall(r'<div class="wk-row has-foot( is-done)?" data-wk-row="(\w+)"', html) == [
        ("", "breakfast"), ("", "lunch"), (" is-done", "dinner")]
    lunch = html[html.index('data-wk-row="lunch"'):html.index('<div class="wk-row has-foot is-done"')]
    dinner = html[html.index('<div class="wk-row has-foot is-done"'):]
    assert 'data-wk-done="dinner" aria-pressed="true"' in dinner
    assert "wk-mini-done is-done" in dinner
    # "Put back " was the same euphemism, heard only by a screen reader
    # (copy sweep finding 22): it says what the tap leaves true instead.
    assert 'aria-label="Not cooked yet — Black bean tacos"' in dinner
    assert "is-done" not in lunch and 'aria-pressed="false"' in lunch
    done_css = SHELL_CSS[SHELL_CSS.index(".wk-mini-done.is-done {"):SHELL_CSS.index("}", SHELL_CSS.index(".wk-mini-done.is-done {"))]
    assert "background: var(--celadon)" in done_css and "color: var(--on-accent-ink)" in done_css
    assert ".wk-row.is-done .wk-row-name { color: var(--ink-done); text-decoration: line-through; }" in SHELL_CSS


def test_the_mini_buttons_are_36px_with_a_44px_tap_target():
    mini = SHELL_CSS[SHELL_CSS.index(".wk-mini {"):SHELL_CSS.index("}", SHELL_CSS.index(".wk-mini {"))]
    assert "height: 36px" in mini
    assert '.wk-mini::before { content: ""; position: absolute; inset: -4px 0; }' in SHELL_CSS


@_needs_node
def test_a_past_day_keeps_done_and_loses_swap_and_a_night_nobody_is_home_has_no_buttons():
    days = _week()
    days[0]["isPast"] = True
    away = _day("2026-09-25",
                breakfast={"title": "Out — nothing to cook", "state": "planned_empty", "source": "empty", "need": "away"},
                lunch={"title": "Out — nothing to cook", "state": "planned_empty", "source": "empty", "need": "away"},
                dinner={"title": "Out — nothing to cook", "state": "planned_empty", "source": "empty", "need": "away"})
    html = _run(_prelude() + f"weekState.days = {json.dumps(days + [away])};\n"
                f"console.log(JSON.stringify([wkDayCardHtml({json.dumps(days[0])}, 0, {{ done: true }}), wkDayCardHtml({json.dumps(away)}, 3, {{ done: true }})]));")
    past, closed = html
    assert past.count('data-wk-done="') == 3 and "data-wk-swap-sheet" not in past
    assert "wk-row" not in closed and "Away — nothing planned, nothing bought." in closed


@_needs_node
def test_the_swap_sheet_shows_the_eyebrow_the_title_three_picks_and_the_two_lines():
    days = _week()
    st = {
        "date": _TUE, "slot": "dinner", "name": "Black bean tacos", "view": "picks", "busy": False, "trouble": "",
        # The picks as Week 1's /swap-options hands them out (index, meal,
        # reason, minutes) — the sheet was rewired onto those routes when
        # the two branches were integrated (2026-09-18).
        "options": [
            {"index": 0, "meal": "Sheet-Pan Sausages", "reason": "uses the sausages", "minutes": 30},
            {"index": 1, "meal": "Chicken fajitas", "reason": "same tortillas", "minutes": 25},
            {"index": 2, "meal": "Veggie quesadillas", "reason": "no shopping", "minutes": 15},
        ],
    }
    html = _run(_prelude() + f"weekState.days = {json.dumps(days)};\n"
                f"console.log(JSON.stringify(swapSheetBodyHtml({json.dumps(st)})));")
    assert '<p class="wk-swap-eyebrow">Tuesday · dinner</p>' in html
    assert 'id="wk-swap-title">Instead of Black bean tacos?</h2>' in html
    assert html.count('data-wk-swap-pick="') == 3
    assert 'data-wk-swap-pick="0"' in html and 'data-wk-swap-pick="2"' in html, "the pick's own index, what /swap-choose wants back"
    assert "30 min · uses the sausages" in html and "25 min · same tortillas" in html and "15 min · no shopping" in html
    assert 'id="wk-swap-move">Move the tacos to another day</button>' in html
    assert 'id="wk-swap-tell">Ask for something else</button>' in html
    assert html.index("wk-swap-picks") < html.index("wk-swap-move") < html.index("wk-swap-tell")


@_needs_node
def test_the_move_view_lists_the_other_nights_and_a_breakfast_has_no_move_line():
    days = _week()
    st = {"date": _TUE, "slot": "dinner", "name": "Black bean tacos", "view": "move", "busy": False, "trouble": "", "options": []}
    bf = {"date": _TUE, "slot": "breakfast", "name": "Overnight oats", "view": "picks", "busy": False, "trouble": "", "options": None}
    html = _run(_prelude() + f"weekState.days = {json.dumps(days)};\n"
                f"console.log(JSON.stringify([swapSheetBodyHtml({json.dumps(st)}), swapSheetBodyHtml({json.dumps(bf)})]));")
    move, breakfast = html
    assert "Move the tacos to which night?" in move
    assert f'data-wk-swap-move="{_MON}"' in move and f'data-wk-swap-move="{_WED}"' in move
    assert f'data-wk-swap-move="{_TUE}"' not in move, "never itself"
    assert "Lemon chicken &amp; orzo" in move and "Your call" in move
    assert 'id="wk-swap-back"' in move
    assert "wk-swap-move" not in breakfast, "only a dinner moves between nights"
    assert "Finding three you could have — about ten seconds." in breakfast, "the wait line (board D5, 2026-09-21)"


def test_the_swap_sheet_is_a_sheet_and_the_picks_go_through_week_ones_routes():
    """The three picks are the Week 1 screen's (app/tools/swap_options.py):
    POST /swap-options to ask, POST /swap-choose with the pick's index to
    take one — the Plan sheet and the first-week screen are one feature."""
    sheet = SHELL_CSS[SHELL_CSS.index("#wk-swap-sheet, #wk-tweak-sheet {"):SHELL_CSS.index("}", SHELL_CSS.index("#wk-swap-sheet, #wk-tweak-sheet {"))]
    assert "background: var(--surface)" in sheet
    assert "border-radius: var(--radius-hero) var(--radius-hero) 0 0" in sheet
    assert "box-shadow: var(--shadow-sheet)" in sheet
    opened = _extract("openSwapSheet", SHELL_JS)
    assert "'/swap-options'" in opened and "method: 'POST'" in opened and "avoid: []" in opened
    picked = _extract("runSwapPick", SHELL_JS)
    assert "'/swap-choose'" in picked and "option: picked.index" in picked
    assert "/swap-pick'" not in SHELL_JS and "swap-options?entry_id" not in SHELL_JS, "the duplicate routes are gone"
    assert "toastSaved(savedLine(picked.meal, 'swapped in')," in SHELL_JS
    assert "{ label: 'Undo', onClick: function () { runSwapUndo(panel, wkFreshDay(day), slot); } }, SWAP_UNDO_MS);" in SHELL_JS
    assert "'/api/cooker/check-meal'" in _extract("runMealDone", SHELL_JS), "Done is the same server tick Today's move uses"


@_needs_node
def test_the_approved_root_has_no_apricot_and_plan_next_week_at_the_right():
    days = _week()
    nxt = {"start_date": "2026-09-28", "day_count": 7, "is_current_period": False, "is_planned": False}
    html = _run(_prelude() + f"console.log(JSON.stringify([weekDecideHtml({json.dumps(_approved(days))}, {json.dumps(nxt)}), weekDecideHtml({json.dumps(_draft(days))}, {json.dumps(nxt)})]));")
    approved, draft = html
    assert 'class="dock wk-root-dock"' in approved
    assert 'id="wk-plan-next">Plan next week</button>' in approved
    assert "dock-primary" not in approved and "btn-gold" not in approved
    assert draft == "", "a draft's root is the review, which carries Approve"
    # Since 2026-09-21 (board D4) the row also carries the round More.
    dock = SHELL_CSS[SHELL_CSS.index(".wk-root-dock .wk-dock-row {"):SHELL_CSS.index("}", SHELL_CSS.index(".wk-root-dock .wk-dock-row {"))]
    assert "justify-content: flex-end" in dock
    btn = SHELL_CSS[SHELL_CSS.index(".wk-plan-next {"):SHELL_CSS.index("}", SHELL_CSS.index(".wk-plan-next {"))]
    assert "background: transparent" in btn and "border: 1.5px solid var(--hairline-strong)" in btn and "apricot" not in btn
