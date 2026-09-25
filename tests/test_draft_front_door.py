"""
The draft's front door (Emily, 2026-09-21 — boards A2, C2, C3), run
against shell.js's own renderers under node:

  1. Re-plan is one tap from the draft — an apricot pill in the band, on a
     draft and on an approved week, opening the intake for the week on
     screen; "Re-plan this week" and "Pick my own days" leave More ···.
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
        + _extract_var("DRAFT_VIEWS", SHELL_JS) + "\n"
        + _extract("draftView", SHELL_JS) + "\n"
        + _extract("weekBandExtras", SHELL_JS) + "\n"
        + _extract("weekReplanPillHtml", SHELL_JS) + "\n"
        + _extract("weekDraftSegHtml", SHELL_JS) + "\n"
        + _extract("weekBandTailHtml", SHELL_JS) + "\n"
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
  var band = el('root-band');
  var pill = el('wk-replan');
  var menuBtn = el('seg'); menuBtn.attrs['data-wk-view'] = 'menu';
  var daysBtn = el('seg'); daysBtn.attrs['data-wk-view'] = 'days';
  band.querySelector = function (sel) {
    if (sel === '.root-band-tools') return tools;
    if (sel === '#wk-replan') return band.html.indexOf('id="wk-replan"') !== -1 ? pill : null;
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
  return { slot: slot, band: band, tools: tools, pill: pill, menuBtn: menuBtn, daysBtn: daysBtn };
}
"""


# ---------------------------------------------------------------------------
# 1. Re-plan is one tap from the draft
# ---------------------------------------------------------------------------

@_needs_node
def test_the_pill_sits_in_the_band_on_a_draft_and_on_an_approved_week_but_not_on_an_empty_plan():
    days = _week()
    out = _run(_band_prelude() + f"""
console.log(JSON.stringify({{
  draft: weekBandExtras({json.dumps(_draft(days))}),
  set: weekBandExtras({json.dumps(_approved(days))}),
  none: weekBandExtras({{}}),
  pill: weekReplanPillHtml()
}}));""")
    assert out["draft"]["pill"] is True and out["set"]["pill"] is True
    assert out["none"] is None, "no plan, nothing to re-plan — the entry card is the way in"
    pill = out["pill"]
    assert pill.startswith('<button type="button" class="wk-replan" id="wk-replan"')
    assert "<svg" in pill and pill.endswith("Re-plan</button>"), "the swap icon and the word"


@_needs_node
def test_the_pill_goes_first_in_the_bands_tools_and_opens_the_intake_for_the_week_on_screen():
    days = _week()
    data = dict(_draft(days), period_start_date="2026-09-21", week_start_date="2026-09-21", day_count=5)
    out = _run(_band_prelude() + _BAND_DOM + f"""
weekState.data = {json.dumps(data)};
var b = makeBand();
fillWeekBandExtras({{}}, b.slot, weekBandExtras(weekState.data));
b.pill.handlers.click();
console.log(JSON.stringify({{ tools: b.tools.html, calls: CALLS }}));""")
    assert out["tools"].startswith('<button type="button" class="wk-replan"'), "top right, before the gear"
    assert out["calls"] == [["2026-09-21", 5]], "the plan's own start and length"


def test_the_pill_is_the_one_road_and_re_plan_left_the_more_sheet():
    """Since 2026-09-21 (board D4) the dock's quiet "Plan it differently"
    is gone too — it was the pill under a second name — so the pill is the
    only caller of replanWeek."""
    fill = _extract("fillWeekBandExtras", SHELL_JS)
    assert "tools.insertAdjacentHTML('afterbegin', weekReplanPillHtml())" in fill
    assert "pill.addEventListener('click', function () { replanWeek(); })" in fill
    wiring = _extract("wireMealsStep", SHELL_JS)
    assert "'#wk-plan-differently'" not in wiring and ">Plan it differently<" not in SHELL_JS
    assert SHELL_JS.count("{ replanWeek(); }") == 1, "one door: the band's pill"
    replan = _extract("replanWeek", SHELL_JS)
    assert "startPlanningWeek(start, dayCount)" in replan
    sheet = _extract("renderMealsMoreSheet", SHELL_JS)
    for gone in ("wk-more-replan", "week-period-open", "PERIOD_PICKER_COPY", "wirePeriodPicker", "planEntryLabel"):
        assert gone not in sheet, gone
    # Mockup "10C" (2026-09-25): "Drop this draft" was renamed "Keep my
    # approved week" and every row now carries an icon and a
    # what-happens-next line — see test_the_more_sheet_is_icon_rows_
    # under_two_eyebrows below for the fuller check.
    for kept in ("'wk-more-try-again', WK_ICONS.redo, 'Try again'",
                 "'wk-more-change', WK_ICONS.pencil, 'Change my answers'",
                 "'wk-more-discard', WK_ICONS.bin, 'Keep my approved week'",
                 "'wk-more-whole-week', WK_ICONS.calendar, 'See the whole week'",
                 "'wk-more-setup', WK_ICONS.tweak, 'Adjust your setup'",
                 "'wk-more-reset', WK_ICONS.resetArrow, 'Start over'",
                 "'wk-more-help', WK_ICONS.help, 'Need a hand?'"):
        assert kept in sheet, kept


def test_the_pill_is_a_real_44px_button_in_the_bands_accent():
    rule = SHELL_CSS[SHELL_CSS.index(".wk-replan {"):SHELL_CSS.index("}", SHELL_CSS.index(".wk-replan {"))]
    assert "height: 36px" in rule and "background: var(--apricot-light)" in rule and "color: var(--on-accent-ink)" in rule
    assert '.wk-replan::before { content: ""; position: absolute; inset: -4px 0; }' in SHELL_CSS
    assert re.search(r"#[0-9a-fA-F]{3,6}\b", rule) is None, "every colour goes through a token"


# ---------------------------------------------------------------------------
# 2. What we're eating is the front door; Which days behind the toggle
# ---------------------------------------------------------------------------

@_needs_node
def test_the_band_carries_the_opener_and_the_toggle_with_what_were_eating_selected():
    days = _week()
    data = dict(_draft(days), draft_opener=["Mexican for lunch Mon–Thu, as you asked.", "Nine new dishes — nothing from the last two weeks."])
    out = _run(_band_prelude() + f"""
var extras = weekBandExtras({json.dumps(data)});
console.log(JSON.stringify({{ extras: extras, tail: weekBandTailHtml(extras), set: weekBandTailHtml(weekBandExtras({json.dumps(_approved(days))})) }}));""")
    # draft_opener is still computed and still on extras.lead (declutter,
    # 2026-09-25: Emily asked the summary paragraph off the draft's band —
    # only the display goes; the underlying field still feeds anything
    # else that reads it), but weekBandTailHtml no longer draws it.
    assert out["extras"]["view"] == "menu" and out["extras"]["lead"] == data["draft_opener"]
    tail = out["tail"]
    assert "wk-draft-lead" not in tail, "the opener paragraph is no longer shown"
    assert re.search(r'class="wk-draft-seg-btn is-on" role="tab" aria-selected="true" data-wk-view="menu">What we’re eating<', tail)
    assert re.search(r'class="wk-draft-seg-btn" role="tab" aria-selected="false" data-wk-view="days">Which days<', tail)
    assert tail.count("wk-draft-seg-btn") == 2
    assert out["set"] == "", "an approved week has no toggle and no opener"


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


@_needs_node
def test_a_tap_on_the_toggle_switches_the_view_and_redraws_the_root():
    days = _week()
    out = _run(_band_prelude() + _BAND_DOM + f"""
weekState.data = {json.dumps(_draft(days))};
var b = makeBand();
fillWeekBandExtras({{}}, b.slot, weekBandExtras(weekState.data));
b.menuBtn.handlers.click();
var same = CALLS.slice();
b.daysBtn.handlers.click();
console.log(JSON.stringify({{ same: same, view: weekState.draftView, calls: CALLS }}));""")
    assert out["same"] == [], "tapping the side already selected does nothing"
    assert out["view"] == "days" and out["calls"] == [["render"]]


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
    # The days it covers and the one fact.
    assert ">3 mornings · 12 min<" in html
    assert ">Mon, Wed · Mexican, as asked<" in html
    assert ">Tuesday · packs cold<" in html
    assert ">Tuesday · 30 min<" in html
    assert ">Monday · chicken" not in html and ">Monday · as asked<" in html
    # Swap on every row, through the same swap sheet, aimed at the dish's first day ahead.
    assert html.count("Swap</button>") == 6
    assert re.search(r'data-wk-day-index="0" data-wk-row="lunch".*?data-wk-meal="lunch">Chicken al pastor tacos<svg class="wk-row-chev".*?</button>.*?data-wk-swap-sheet="lunch"', html, re.S)
    assert 'data-wk-swap-sheet="snack"' in html
    # No day notes, no steppers, no head line, no carousel.
    for gone in ("Out — nothing to cook", "wk-card-tags", "rv-step", "data-rv-", "wk-carousel", "wk-daytab", "7 meals", 'id="wk-help"'):
        assert gone not in html, gone
    # Under it: Approve and the round More, one row in the dock.
    assert 'id="week-approve-btn">Approve · Open grocery list</button>' in html
    assert "Plan it differently" not in html
    assert 'id="wk-more"' in html and html.index("wk-decide dock") < html.index('id="wk-more"')


@_needs_node
def test_which_days_is_the_carousel_as_built_with_the_same_dock_under_it():
    days = _menu_week()
    html = _run(_prelude() + f"weekState.days = {json.dumps(days)}; weekState.draftView = 'days'; weekState.draftViewPlanId = 7;\n"
                f"console.log(JSON.stringify(reviewStepHtml({json.dumps(_draft(days))}, weekState.days, true)));")
    assert 'id="wk-carousel"' in html and html.count('data-wk-card="') == 3 and html.count("data-wk-daytab=") == 3
    assert "wk-menu" not in html
    assert 'id="week-approve-btn">Approve · Open grocery list</button>' in html
    assert "Plan it differently" not in html and 'id="wk-more"' in html
    # A row's fact rides beside its minutes here too.
    assert ">33 min · Mexican, as asked<" in html


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


def test_the_toggle_and_the_dock_more_follow_the_system():
    seg = SHELL_CSS[SHELL_CSS.index(".wk-draft-seg-btn {"):SHELL_CSS.index("}", SHELL_CSS.index(".wk-draft-seg-btn {"))]
    assert "min-height: 44px" in seg, "rule 6 — the board's 38px is not a tap target"
    assert ".wk-draft-seg-btn.is-on { background: var(--surface); color: var(--ink-strong); }" in SHELL_CSS
    track = SHELL_CSS[SHELL_CSS.index(".wk-draft-seg {"):SHELL_CSS.index("}", SHELL_CSS.index(".wk-draft-seg {"))]
    assert "background: var(--spruce-raised)" in track, "chips on spruce only"
    more = SHELL_CSS[SHELL_CSS.index(".wk-dock-more {"):SHELL_CSS.index("}", SHELL_CSS.index(".wk-dock-more {"))]
    assert "width: 48px" in more and "height: 48px" in more, "rule 6, with room to spare"
    assert "border-radius: var(--radius-pill)" in more
    section = SHELL_CSS[SHELL_CSS.index("The draft's front door"):SHELL_CSS.index(".wk-dock-more:hover")]
    assert re.search(r":\s*#[0-9a-fA-F]{3,6}\b", section) is None, "every colour goes through a token"


# ---------------------------------------------------------------------------
# 3. The draft says what it did
# ---------------------------------------------------------------------------

@_needs_node
def test_the_reason_is_plain_text_after_the_time_and_nothing_shows_without_one():
    """Emily, 2026-09-25 (option 1A): the reason used to be a tap on the
    meta line that popped a note; it is said on the line now, as text."""
    out = _run(_prelude() + """
console.log(JSON.stringify({
  withReason: wkRowMetaHtml({ state: 'planned', reason: 'you said you love salmon.' }, '35 min'),
  noReason: wkRowMetaHtml({ state: 'planned', reason: null }, '35 min'),
  noMeta: wkRowMetaHtml({ state: 'planned', reason: 'a quick one' }, ''),
  asked: wkRowMetaHtml({ state: 'planned', asked: 'Mexican, as asked', reason: 'Mexican, as you asked' }, '35 min · Mexican, as asked'),
  nothing: wkRowMetaHtml({ state: 'planned' }, '')
}));""")
    assert out["withReason"] == '<span class="wk-row-meta">35 min · You said you love salmon</span>'
    assert out["noReason"] == '<span class="wk-row-meta">35 min</span>'
    assert out["noMeta"] == '<span class="wk-row-meta">A quick one</span>'
    assert out["asked"] == '<span class="wk-row-meta">35 min · Mexican, as asked</span>', "the asked fact already says why"
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
    assert out == ["Vic out", "4 for breakfast"]


def test_the_words_pass_the_seven_rules():
    """Contractions, the thing not the feature, no dashboard labels."""
    for literal in ("Here’s your week.", "What we’re eating", "Which days", "Re-plan",
                    "Need a hand?", "your turn"):
        assert literal in SHELL_JS, literal
    assert "'a draft, your turn'" not in SHELL_JS, "the chip already says Draft"
    assert "Re-plan this week" not in _extract("renderMealsMoreSheet", SHELL_JS)
