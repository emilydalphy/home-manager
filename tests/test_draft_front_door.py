"""
The draft's front door (Emily, 2026-09-21 — boards A2, C2, C3), run
against shell.js's own renderers under node:

  1. (Since 2026-10-30, the declutter) the band carries More ··· only —
     the Re-plan pill and the Meals | Schedule toggle went; re-planning is
     More's "Change my answers" and "Try again".
  2. "What we're eating" is the front door again, "Which days" behind the
     toggle: the band carries the opener and the two-way toggle; the menu
     is three cards (Snacks when there are any), one row per dish with the
     days it covers and one fact, Swap on every row, no day notes and no
     steppers; Which days is the 2026-09-18 carousel unchanged; under both,
     Approve · Open grocery list with the round More beside it in the
     dock's one row (board D4, 2026-09-21 — the quiet "Plan it differently"
     line went: it was the band's Re-plan pill twice). The toggle remembers
     its side for the session and a new draft opens on What we're eating.
  3. The draft says what it did: the opener's two lines in the band, the
     stored reason one tap away on both views (never pushing rows around),
     the one fact beside the days, and the quiet mark on a day that's
     different.

The server half — the opener's words, the scope of a typed request, the
variety window — is tests/test_draft_says_what_it_did.py.
"""
from __future__ import annotations

import json
import re
import shutil
from pathlib import Path

import pytest

import nodeharness
from test_plan_cards_2026_09_18 import _prelude, _run, _week, _draft, _approved, _entry, _day, _MON, _TUE, _WED
from test_week_seven_tiles import _extract, _extract_var

REPO = Path(__file__).resolve().parent.parent
SHELL_JS = (REPO / "static" / "shell.js").read_text(encoding="utf-8")
SHELL_CSS = (REPO / "static" / "shell.css").read_text(encoding="utf-8")

_needs_node = pytest.mark.skipif(shutil.which("node") is None, reason="node is needed to run the renderers")


def _band_prelude() -> str:
    return (
        _extract("escapeHtml", SHELL_JS) + "\n"
        + "var WEEK_SLOTS = ['breakfast', 'lunch', 'dinner'];\n"
        + "var weekState = { days: [], data: null, selectedIndex: 0, step: 'week', draftView: 'menu', draftViewPlanId: null };\n"
        + "var planningPeriodDefault = null;\n"
        + "var CALLS = [];\n"
        + "function startPlanningWeek(start, n) { CALLS.push([start, n]); }\n"
        + "function thisWeekStartLocal() { return '2026-09-21'; }\n"
        + "function renderMealsStep() { CALLS.push(['render']); }\n"
        + _extract("weekPlanState", SHELL_JS) + "\n"
        + _extract_var("WK_ICONS", SHELL_JS) + "\n"
        + _extract("draftView", SHELL_JS) + "\n"
        + _extract("weekBandExtras", SHELL_JS) + "\n"
        + _extract("weekBandMoreHtml", SHELL_JS) + "\n"
        + "function openMealsMoreSheet() { CALLS.push(['more']); }\n"
        + _extract("planEntryLabel", SHELL_JS) + "\n"
        + _extract("addDaysLocal", SHELL_JS) + "\n"
        + _extract("nextPeriodFor", SHELL_JS) + "\n"
        + _extract("planNextLabel", SHELL_JS) + "\n"
        + _extract("planNextWeek", SHELL_JS) + "\n"
        + _extract("fillWeekBandExtras", SHELL_JS) + "\n"
        + _extract("replanWeek", SHELL_JS) + "\n"
    )


# A band as rootBandHtml leaves it, with just enough DOM for
# fillWeekBandExtras: querySelector, insertAdjacentHTML, click handlers.
_BAND_DOM = """
function el(cls) {
  var e = { cls: cls, children: [], html: '', handlers: {}, attrs: {} };
  e.insertAdjacentHTML = function (where, html) { e.html += '|' + where + ':' + html; };
  e.addEventListener = function (t, fn) { e.handlers[t] = fn; };
  e.getAttribute = function (n) { return e.attrs[n]; };
  return e;
}
function makeBand() {
  var tools = el('root-band-tools');
  var top = el('root-band-top');
  var band = el('root-band');
  var pill = el('wk-replan');
  var more = el('wk-band-more');
  var planNext = el('wk-band-plan-next');
  var menuBtn = el('seg'); menuBtn.attrs['data-wk-view'] = 'menu';
  var daysBtn = el('seg'); daysBtn.attrs['data-wk-view'] = 'days';
  band.querySelector = function (sel) {
    if (sel === '.root-band-tools') return tools;
    if (sel === '.root-band-top') return top;
    if (sel === '#wk-replan') return band.html.indexOf('id="wk-replan"') !== -1 ? pill : null;
    if (sel === '#wk-band-more') return band.html.indexOf('id="wk-band-more"') !== -1 ? more : null;
    if (sel === '#wk-band-plan-next') return band.html.indexOf('id="wk-band-plan-next"') !== -1 ? planNext : null;
    return null;
  };
  band.querySelectorAll = function (sel) {
    if (sel === '[data-wk-view]' && band.html.indexOf('data-wk-view') !== -1) return [menuBtn, daysBtn];
    return [];
  };
  var slot = el('slot');
  slot.querySelector = function (sel) { return sel === '.root-band' ? band : null; };
  // The tools' inserts count as the band's for the pill lookup.
  tools.insertAdjacentHTML = function (where, html) { tools.html += html; band.html += '|tools:' + html; };
  top.insertAdjacentHTML = function (where, html) { top.html += '|' + where + ':' + html; band.html += '|top-' + where + ':' + html; };
  return { slot: slot, band: band, tools: tools, top: top, pill: pill, more: more, planNext: planNext, menuBtn: menuBtn, daysBtn: daysBtn };
}
"""


# ---------------------------------------------------------------------------
# 1. The band (since the declutter, Emily 2026-10-30): More ··· and nothing
#    else — the Re-plan pill, the draft's "Plan the week" and the Meals |
#    Schedule toggle went. Re-planning is More's "Change my answers" /
#    "Try again"; the Plan root (planWeekHtml) is tests/test_declutter_2026_10_30.py.
# ---------------------------------------------------------------------------

@_needs_node
def test_the_band_carries_more_on_a_draft_and_an_approved_week_and_nothing_on_an_empty_plan():
    days = _week()
    out = _run(_band_prelude() + _BAND_DOM + f"""
var res = {{ draft: weekBandExtras({json.dumps(_draft(days))}),
  set: weekBandExtras({json.dumps(_approved(days))}), none: weekBandExtras({{}}) }};
weekState.data = {json.dumps(_draft(days))};
var b = makeBand();
fillWeekBandExtras({{}}, b.slot, weekBandExtras(weekState.data));
b.more.handlers.click();
res.tools = b.tools.html; res.top = b.top.html; res.calls = CALLS;
console.log(JSON.stringify(res));""")
    assert out["draft"] == {"more": True} and out["set"] == {"more": True}
    assert out["none"] is None
    assert out["tools"].startswith('<button type="button" class="root-band-more" id="wk-band-more"'), "top right, before the gear"
    assert "wk-replan" not in out["tools"] and out["top"] == "", "no pill, no second row under the brand"
    assert out["calls"] == [["more"]]


def test_re_planning_is_in_more_and_the_pills_are_gone():
    fill = _extract("fillWeekBandExtras", SHELL_JS)
    for gone in ("weekReplanPillHtml", "weekBandActionsHtml", "data-wk-view", "replanWeek"):
        assert gone not in fill, gone
    sheet = _extract("renderMealsMoreSheet", SHELL_JS)
    for kept in ("'wk-more-try-again', WK_ICONS.redo, 'Try again'",
                 "'wk-more-change', WK_ICONS.pencil, 'Change my answers'",
                 "'wk-more-discard', WK_ICONS.bin, 'Keep my approved week'",
                 "'wk-more-whole-week', WK_ICONS.calendar, 'See the whole week'",
                 "'wk-more-setup', WK_ICONS.tweak, 'Adjust your setup'",
                 "'wk-more-reset', WK_ICONS.resetArrow, 'Start over'",
                 "'wk-more-help', WK_ICONS.help, 'Need a hand?'"):
        assert kept in sheet, kept
    assert "wk-more-check" not in sheet, "Check the week folded into See the whole week"
    assert "'wk-more-plan-next'" in sheet



# ---------------------------------------------------------------------------
# 2. What we're eating is the front door; Which days behind the toggle
# ---------------------------------------------------------------------------

@_needs_node
def test_the_toggle_remembers_its_side_for_the_session_and_a_new_draft_opens_on_the_menu():
    out = _run(_band_prelude() + """
var a = { weekly_plan_id: 7, status: 'draft', days: [{}] };
var first = draftView(a);
weekState.draftView = 'days';
var again = draftView(a);
var b = { weekly_plan_id: 8, status: 'draft', days: [{}] };
var fresh = draftView(b);
console.log(JSON.stringify([first, again, fresh]));""")
    assert out == ["menu", "days", "menu"]


def _menu_week():
    mexican = _entry("Chicken al pastor tacos", entry_id=12, meta="33 min", asked="Mexican, as asked",
                     reason="Mexican for lunch, as you asked — tacos travel well")
    return [
        _day(_MON, breakfast=_entry("Boiled eggs and avocado toast", entry_id=11, meta="12 min"),
             lunch=dict(mexican),
             dinner=_entry("Chicken thighs, smashed potatoes, broccoli", entry_id=13, meta="35 min", asked="as asked",
                           reason="chicken and potatoes, as you asked"),
             snacks=[_entry("Apple slices", entry_id=14, meta=None)]),
        _day(_TUE, iso_today=True,
             breakfast=_entry("Boiled eggs and avocado toast", entry_id=21, meta="12 min"),
             lunch=_entry("Black bean and corn salad", entry_id=22, meta="10 min", asked="packs cold"),
             dinner=_entry("Sheet-pan chicken and sweet potato", entry_id=23, meta="30 min")),
        _day(_WED, breakfast=_entry("Boiled eggs and avocado toast", entry_id=31, meta="12 min"),
             lunch=dict(mexican, entry_id=32),
             dinner={"title": "Out — nothing to cook", "state": "planned_empty", "source": "empty", "entry_id": 33}),
    ]


@_needs_node
def test_what_were_eating_is_one_card_per_meal_type_with_the_days_and_one_fact_and_swap_on_every_row():
    days = _menu_week()
    html = _run(_prelude() + f"weekState.days = {json.dumps(days)};\n"
                f"console.log(JSON.stringify(reviewStepHtml({json.dumps(_draft(days))}, weekState.days, true)));")
    heads = re.findall(r'<span class="wk-menu-head">([^<]+)</span>', html)
    assert heads == ["Breakfasts", "Lunches", "Dinners", "Snacks"]
    assert html.count('class="wk-row wk-menu-row has-foot"') == 6, "each dish once: 1 breakfast, 2 lunches, 2 dinners, 1 snack"
    # The days it covers and its time — only (Emily, 2026-09-26): the
    # asked fact and the reason stay on Which days' rows.
    assert ">3 mornings · 12 min<" in html
    assert ">Mon, Wed · 33 min<" in html
    assert ">Tuesday · 10 min<" in html
    assert ">Tuesday · 30 min<" in html
    assert ">Monday · 35 min<" in html
    for said in ("as asked", "packs cold", "chicken and potatoes", "travel well"):
        assert said not in html, said
    # Swap on every row, through the same swap sheet, aimed at the dish's first day ahead.
    assert html.count("Swap</button>") == 6
    assert re.search(r'data-wk-day-index="0" data-wk-row="lunch".*?data-wk-meal="lunch">Chicken al pastor tacos<svg class="wk-row-chev".*?</button>.*?data-wk-swap-sheet="lunch"', html, re.S)
    assert 'data-wk-swap-sheet="snack"' in html
    # No day notes, no steppers, no head line, no carousel.
    for gone in ("Out — nothing to cook", "wk-card-tags", "rv-step", "data-rv-", "wk-carousel", "wk-daytab", "7 meals", 'id="wk-help"'):
        assert gone not in html, gone
    # Under it: Approve and the round More, one row in the dock.
    assert 'id="week-approve-btn">Approve the week</button>' in html
    assert "Plan it differently" not in html
    assert 'id="wk-more"' in html and html.index("wk-decide dock") < html.index('id="wk-more"')


@_needs_node
def test_which_days_is_the_carousel_as_built_with_the_same_dock_under_it():
    days = _menu_week()
    html = _run(_prelude() + f"weekState.days = {json.dumps(days)}; weekState.draftView = 'days'; weekState.draftViewPlanId = 7;\n"
                f"console.log(JSON.stringify(reviewStepHtml({json.dumps(_draft(days))}, weekState.days, true)));")
    assert 'id="wk-carousel"' in html and html.count('data-wk-card="') == 3 and html.count("data-wk-daytab=") == 3
    assert "wk-menu" not in html
    assert 'id="week-approve-btn">Approve the week</button>' in html
    assert "Plan it differently" not in html and 'id="wk-more"' in html
    # The row says its minutes and no reason (Emily, 2026-09-27, decision C).
    assert ">33 min<" in html and "as asked" not in html


@_needs_node
def test_a_tap_inside_a_menu_row_is_about_the_dishs_first_day_ahead():
    days = _menu_week()
    out = _run(_prelude() + _extract("wkDayForTap", SHELL_JS) + "\n"
               + _extract("mealsCurrentDay", SHELL_JS) + "\n"
               + f"weekState.days = {json.dumps(days)}; weekState.selectedIndex = 1;\n"
               + "var row = { getAttribute: function (n) { return n === 'data-wk-day-index' ? '2' : null; } };\n"
               + "var btn = { closest: function (sel) { return sel === '[data-wk-day-index]' ? row : null; } };\n"
               + "var day = wkDayForTap(btn);\n"
               + "console.log(JSON.stringify([day.date, weekState.selectedIndex]));")
    assert out == [_WED, 2]


# ---------------------------------------------------------------------------
# 3. The draft says what it did
# ---------------------------------------------------------------------------

@_needs_node
def test_the_meta_line_is_plain_text_and_says_no_reason():
    """Emily, 2026-09-25 (option 1A): the line is plain text, no tap.
    Emily, 2026-09-27 (decision C, "cut it everything"): the stored reason
    and the "as asked" fact are gone from it; only the time is said."""
    out = _run(_prelude() + """
console.log(JSON.stringify({
  withReason: wkRowMetaHtml({ state: 'planned', reason: 'you said you love salmon.' }, wkRowMetaLine({ state: 'planned', reason: 'you said you love salmon.' }, '35 min')),
  noMeta: wkRowMetaHtml({ state: 'planned', reason: 'a quick one' }, wkRowMetaLine({ state: 'planned', reason: 'a quick one' }, '')),
  asked: wkRowMetaLine({ state: 'planned', asked: 'Mexican, as asked', reason: 'Mexican, as you asked' }, '35 min'),
  nothing: wkRowMetaHtml({ state: 'planned' }, '')
}));""")
    assert out["withReason"] == '<span class="wk-row-meta">35 min</span>'
    assert out["noMeta"] == ""
    assert out["asked"] == "35 min"
    assert out["nothing"] == ""


def test_the_reason_pop_and_its_wiring_are_gone():
    for gone in ("wk-why-pop", "wk-row-why", "data-wk-why"):
        assert gone not in SHELL_JS and gone not in SHELL_CSS, gone


@_needs_node
def test_a_day_thats_different_carries_a_quiet_mark_on_which_days():
    day = _day(_MON,
               breakfast=_entry("Eggs", entry_id=1, guest_count=2, serves=4),
               lunch=_entry("Salad", entry_id=2, away_names=["Vic"], present_names=["Emily"]),
               dinner=_entry("Chili", entry_id=3, away_names=["Vic"], present_names=["Emily"]))
    out = _run(_prelude() + f"console.log(JSON.stringify(reviewTileTags({json.dumps(day)})));")
    # Vic is home for breakfast (no away_names on it), so the tag names the
    # meals he's out for rather than "Vic out" (Emily, 2026-10-06: a
    # part-day absence must not read as gone all day).
    assert out == ["Vic · lunch and dinner out", "4 for breakfast"]


def test_the_words_pass_the_seven_rules():
    """Contractions, the thing not the feature, no dashboard labels."""
    for literal in ("Here’s your week.", "Meals", "Schedule", "Re-plan",
                    "Need a hand?", "your turn"):
        assert literal in SHELL_JS, literal
    assert "'a draft, your turn'" not in SHELL_JS, "the chip already says Draft"
    assert "Re-plan this week" not in _extract("renderMealsMoreSheet", SHELL_JS)
