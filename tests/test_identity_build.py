"""The Identity Round, built (Emily, 2026-09-13).

Four decisions from the Identity Round canvas, each a checkable claim:

  Q1 = C  The root band opens with the apricot mark and "Pomona" top-left;
          the date leaves the eyebrow for the sub-line ("Sunday, Sep 13 ·
          2 of 5 done"). B (the mark before the date eyebrow) is kept in
          reserve behind the same constant: BAND_IDENTITY in shell.js,
          'wordmark' | 'mark' | 'none', one builder for all three.
  Q2 = B  The chat button's icon is the mark, apricot on spruce, with a
          small "ASK" under it for a device's first three visits
          (FAB_LABEL_VISITS) so the fruit is findable as the chat.
  Q3 = B  Plan's tab glyph is the week as a row — five dots between two
          rules; the bullseye is retired.
  Q4 = B  One 2.2px stroke for the whole icon set; the mark, a logo, keeps
          its own weight (the sweep itself is guarded in
          tests/test_design_hygiene.py, (d)).

The band and the counter are pure enough to run under node; the rest is
read off the source.
"""

import json
import re
import shutil
from pathlib import Path

import pytest

import nodeharness

REPO = Path(__file__).resolve().parent.parent
STATIC = REPO / "static"
SHELL_JS = (STATIC / "shell.js").read_text(encoding="utf-8")
SHELL_HTML = (STATIC / "shell.html").read_text(encoding="utf-8")
SHELL_CSS = (STATIC / "shell.css").read_text(encoding="utf-8")
DESIGN = (REPO / "DESIGN_SYSTEM.md").read_text(encoding="utf-8")

MARK_FIRST_PATH = "M12 20.4c-3.1"
BUBBLE_PATH = 'M4 5h16v10H9l-5 4z'
DATE_RE = r"[A-Z][a-z]+day, [A-Z][a-z]{2} \d{1,2}"


def _function(name: str) -> str:
    marker = "  async function %s(" % name
    if marker not in SHELL_JS:
        marker = "  function %s(" % name
    start = SHELL_JS.index(marker)
    end = SHELL_JS.index("\n  }\n", start)
    return SHELL_JS[start:end] + "\n  }\n"


def _region(start_marker: str, end_marker: str) -> str:
    start = SHELL_JS.index(start_marker)
    end = SHELL_JS.index(end_marker, start)
    return SHELL_JS[start:end]


_needs_node = pytest.mark.skipif(
    shutil.which("node") is None, reason="node is needed to execute the shell's own builders"
)


def _node(script: str):
    res = nodeharness.run_node(script, timeout=30)
    assert res.returncode == 0, f"node failed: {res.stderr}"
    return json.loads(res.stdout.strip().splitlines()[-1])


# The band, with everything it reads: the mark, the identity constant and
# its helpers, the gear, and a panel fake for setRootBand.
_BAND_JS = (
    "function escapeHtml(s){return String(s == null ? '' : s).replace(/&/g,'&amp;').replace(/</g,'&lt;')"
    ".replace(/>/g,'&gt;').replace(/\"/g,'&quot;');}\n"
    "var PREFS_GEAR_ICON = '<svg></svg>';\n"
    + _region("  var MARK_PATHS =", "  var ICONS = {")
    + _region("  var BAND_IDENTITY = ", "  // ---------- Empty states as designed moments ----------")
    + _function("prefsGearHtml")
    + _function("dayName")
    + _function("cookBandEyebrow")
    + "function groPlural(n, one, many){ return n + ' ' + (n === 1 ? one : many); }\n"
    "function groTotals(d){ return { all: d.all }; }\n"
    "function groStoresOnList(d){ return d.stops; }\n"
    # The band's line drops the stores clause for the one-list stand-in, so
    # it asks groIsStandIn. That predicate runs for real here — only its
    # groPillStores is stubbed, where the stops ARE the shops. The stops
    # these tests pass are ordinary shop names, so nothing they assert moves.
    "var GRO_ONE_LIST_STOP = 'Your list';\n"
    "function groPillStores(d){ return d.stops || []; }\n"
    + _function("groIsStandIn")
    # Shop's band: the eyebrow constant, the number words and the two
    # builders, as one region (2026-09-18: "This week · 14 things, two
    # stores.").
    + _region("  var GRO_BAND_EYEBROW = ", "  // ---------- LIST ----------")
    + """
function el() { return { textContent: '', hidden: false, dataset: {} }; }
function bandPanel(eyebrow, sub) {
  var els = { '#b-eyebrow': el(), '#b-title': el(), '#b-sub': el(), '#b-badge': el() };
  els['#b-eyebrow'].textContent = eyebrow || '';
  // As rootBandHtml renders it: hidden under 'wordmark', shown otherwise.
  els['#b-eyebrow'].hidden = BAND_IDENTITY === 'wordmark';
  els['#b-sub'].dataset.sub = sub || '';
  els['#b-sub'].textContent = sub || '';
  return { els: els, querySelector: function (sel) { return els[sel] || null; } };
}
function after(panel, parts) {
  setRootBand(panel, 'b', parts);
  return { sub: panel.els['#b-sub'].textContent, subHidden: panel.els['#b-sub'].hidden,
           eyebrow: panel.els['#b-eyebrow'].textContent, eyebrowHidden: panel.els['#b-eyebrow'].hidden };
}
"""
)


# --------------------------------------------------------------------------
# Q1 — the mark in the band: one builder, three identities
# --------------------------------------------------------------------------

def test_emily_chose_c_and_kept_b_in_reserve():
    band = _region("  var BAND_IDENTITY = ", "  function rootBandHtml(")
    assert "var BAND_IDENTITY = 'wordmark';" in band, "C is the default"
    comment = SHELL_JS[SHELL_JS.index("Identity Round\n  // Q1)"):SHELL_JS.index("var BAND_IDENTITY = ")]
    assert "She chose C" in comment and "kept B in\n  // reserve" in comment
    for value in ("'wordmark'", "'mark'", "'none'"):
        assert value in comment, f"the comment should name {value}"
    assert "var BAND_WORDMARK = 'Pomona';" in band


@_needs_node
def test_wordmark_puts_the_mark_and_pomona_top_left_and_the_date_in_the_line():
    out = _node(
        _BAND_JS
        + "BAND_IDENTITY = 'wordmark';\n"
        + "console.log(JSON.stringify({"
        + " now: rootBandHtml({ id: 'x', eyebrow: 'Sunday, Sep 13', title: 'Now', sub: '2 of 5 done', badge: 'Week set' }),"
        + " plan: rootBandHtml({ id: 'p', eyebrow: 'Sep 14–20', title: 'This week', sub: 'a draft, your turn', badge: 'Draft' }),"
        + " bare: rootBandHtml({ id: 'y', title: 'Shop' })"
        + "}));"
    )
    now = out["now"]
    assert 'data-identity="wordmark"' in now
    brand = now[now.index('<span class="root-band-brand is-wordmark">'):now.index('<span class="root-band-eyebrow"')]
    assert 'class="pomona-mark"' in brand and 'stroke-width="1.8"' in brand and MARK_FIRST_PATH in brand
    assert '<span class="root-band-wordmark">Pomona</span>' in brand
    # The brand leads: it is the first thing inside the band's lead slot.
    assert now.index('root-band-brand') < now.index('root-band-eyebrow') < now.index('root-band-badge')
    # The eyebrow keeps the date but is hidden; the line carries it.
    assert 'id="x-eyebrow" hidden>Sunday, Sep 13<' in now
    assert 'id="x-sub" data-sub="2 of 5 done">Sunday, Sep 13 · 2 of 5 done<' in now
    assert 'id="x-badge">Week set<' in now, "the status chip stays where it is"
    assert 'id="p-sub" data-sub="a draft, your turn">Sep 14–20 · a draft, your turn<' in out["plan"]
    # Nothing to fold in and nothing to say: no line at all.
    assert 'id="y-sub" data-sub="" hidden' in out["bare"]
    assert '<span class="root-band-wordmark">Pomona</span>' in out["bare"]


@_needs_node
def test_mark_keeps_the_date_eyebrow_with_the_mark_before_it():
    out = _node(
        _BAND_JS
        + "BAND_IDENTITY = 'mark';\n"
        + "console.log(JSON.stringify({"
        + " now: rootBandHtml({ id: 'x', eyebrow: 'Sunday, Sep 13', title: 'Now', sub: '2 of 5 done' })"
        + "}));"
    )
    now = out["now"]
    assert 'data-identity="mark"' in now
    assert '<span class="root-band-brand">' in now and MARK_FIRST_PATH in now
    assert "root-band-wordmark" not in now and "Pomona" not in now
    assert now.index('root-band-brand') < now.index('root-band-eyebrow')
    assert 'id="x-eyebrow">Sunday, Sep 13<' in now, "the eyebrow keeps the date, shown"
    assert 'id="x-sub" data-sub="2 of 5 done">2 of 5 done<' in now, "the line is the root's own"


@_needs_node
def test_none_is_the_band_as_it_was():
    out = _node(
        _BAND_JS
        + "BAND_IDENTITY = 'none';\n"
        + "console.log(JSON.stringify({"
        + " now: rootBandHtml({ id: 'x', eyebrow: 'Sunday, Sep 13', title: 'Now', sub: '2 of 5 done' })"
        + "}));"
    )
    now = out["now"]
    assert 'data-identity="none"' in now
    assert "root-band-brand" not in now and MARK_FIRST_PATH not in now and "Pomona" not in now
    assert 'id="x-eyebrow">Sunday, Sep 13<' in now
    assert 'id="x-sub" data-sub="2 of 5 done">2 of 5 done<' in now


@_needs_node
def test_set_root_band_keeps_the_date_in_the_line_whichever_half_changes():
    out = _node(
        _BAND_JS
        + "BAND_IDENTITY = 'wordmark';\n"
        + "var r = {};\n"
        # Now after a load: the line arrives, the eyebrow was rendered.
        + "r.load = after(bandPanel('Sunday, Sep 13', ''), { sub: '2 of 5 done', badge: 'Week set' });\n"
        # Only the line changes (a tick): the date stays in front of it.
        + "r.tick = after(bandPanel('Sunday, Sep 13', '2 of 5 done'), { sub: '3 of 5 done' });\n"
        # Only the eyebrow changes (Cook re-rendering the day): the line keeps its words.
        + "r.day = after(bandPanel('Saturday, Sep 12', '1 cook tonight'), { eyebrow: 'Sunday, Sep 13' });\n"
        # The line empties: the date alone, still shown.
        + "r.empty = after(bandPanel('Sunday, Sep 13', '2 of 5 done'), { sub: '' });\n"
        # A title-only update touches neither.
        + "r.title = after(bandPanel('Sunday, Sep 13', '2 of 5 done'), { title: 'Now' });\n"
        + "BAND_IDENTITY = 'none';\n"
        + "r.noneEmpty = after(bandPanel('Sunday, Sep 13', '2 of 5 done'), { sub: '' });\n"
        + "r.noneTick = after(bandPanel('Sunday, Sep 13', ''), { sub: '3 of 5 done' });\n"
        + "console.log(JSON.stringify(r));"
    )
    assert out["load"]["sub"] == "Sunday, Sep 13 · 2 of 5 done" and not out["load"]["subHidden"]
    assert out["load"]["eyebrowHidden"], "under wordmark the eyebrow stays hidden after an update too"
    assert out["tick"]["sub"] == "Sunday, Sep 13 · 3 of 5 done"
    assert out["day"]["sub"] == "Sunday, Sep 13 · 1 cook tonight"
    assert out["empty"]["sub"] == "Sunday, Sep 13" and not out["empty"]["subHidden"]
    assert out["title"]["sub"] == "2 of 5 done", "an untouched line is left alone (data-sub is the source, not re-derived)"
    # Under 'none' an empty line hides, as before 2026-09-13.
    assert out["noneEmpty"]["sub"] == "" and out["noneEmpty"]["subHidden"]
    assert out["noneTick"]["sub"] == "3 of 5 done" and not out["noneTick"]["eyebrowHidden"]


# --------------------------------------------------------------------------
# Q1 — the date reaches each root's line
# --------------------------------------------------------------------------

def test_now_hands_the_band_todays_date_as_its_eyebrow():
    build = _function("buildTodayPanel")
    assert "eyebrow: bandDateLabel()," in build
    assert "toLocaleDateString" not in build, "the one date format lives in bandDateLabel"
    fn = _function("bandDateLabel")
    assert "{ weekday: 'long', month: 'short', day: 'numeric' }" in fn
    assert "'T00:00:00'" in fn, "local, not UTC"


@_needs_node
def test_shop_and_cook_lead_their_lines_with_their_eyebrow_under_wordmark_only():
    """Cook's eyebrow is the date; Shop's is "This week" (Emily's
    shopping-list mockup, 2026-09-18 — it was the date with the list's
    count until then). Under 'wordmark' each leads its own sub-line."""
    out = _node(
        _BAND_JS
        + "var r = {};\n"
        + "BAND_IDENTITY = 'wordmark';\n"
        + "r.shop = groBandEyebrow({ all: 60, stops: ['A'] });\n"
        + "r.shopNone = groBandEyebrow({ all: 0, stops: [] });\n"
        + "r.cook = cookBandEyebrow('2026-09-13');\n"
        + "r.cookLine = after(bandPanel(cookBandEyebrow('2026-09-13'), ''), { sub: '1 cook tonight' }).sub;\n"
        + "r.shopLine = after(bandPanel(r.shop, ''), { sub: groBandLine({ all: 14, stops: ['A', 'B'] }) }).sub;\n"
        + "r.shopLineNone = after(bandPanel(r.shopNone, ''), { sub: groBandLine({ all: 0, stops: [] }) }).sub;\n"
        + "BAND_IDENTITY = 'mark';\n"
        + "r.shopMark = groBandEyebrow({ all: 60, stops: ['A'] });\n"
        + "r.cookMark = cookBandEyebrow('2026-09-13');\n"
        + "BAND_IDENTITY = 'none';\n"
        + "r.shopPlain = groBandEyebrow({ all: 60, stops: ['A'] });\n"
        + "r.cookPlain = cookBandEyebrow('2026-09-13');\n"
        + "console.log(JSON.stringify(r));"
    )
    assert out["shop"] == "This week" and out["shopNone"] == "This week"
    assert out["shopLine"] == "This week · 14 things, two stores."
    assert out["shopLineNone"] == "This week", "nothing to buy: the eyebrow alone — the empty moment says the rest"
    assert out["cook"] == "Sunday, Sep 13"
    assert out["cookLine"] == "Sunday, Sep 13 · 1 cook tonight"
    # B and the old band keep the eyebrows they had.
    assert out["shopMark"] == "This week" and out["shopPlain"] == "This week"
    assert out["cookMark"] == "Sunday" and out["cookPlain"] == "Sunday"


def test_plan_and_cook_pass_through_the_same_band_builder():
    step = _function("renderMealsStep")
    assert "bandSlot.innerHTML = rootBandHtml(parts);" in step
    parts = _function("weekBandParts")
    assert "sub: sub.join(' · ')," in parts and "eyebrow: eyebrow," in parts
    kitchen = _function("renderKitchen")
    assert "setRootBand(panel, 'kit-band', { eyebrow: cookBandEyebrow(todayIso) });" in kitchen
    assert "setRootBand(panel, 'kit-band', { sub: kitchenSubtitle(rows, meals, todayIso) });" in kitchen
    shop = _function("renderGrocery")
    assert "setRootBand(panel, 'gro-band', { eyebrow: groBandEyebrow(data), sub: groBandLine(data) });" in shop


def test_the_band_brand_css_is_the_canvas_sizes_from_tokens():
    start = SHELL_CSS.index(".root-band-brand {")
    section = SHELL_CSS[start:SHELL_CSS.index(".root-band-eyebrow {", start)]
    assert "color: var(--apricot-light);" in section
    assert ".root-band-brand .pomona-mark { width: 20px; height: 20px;" in section
    wordmark = section[section.index(".root-band-wordmark {"):]
    for rule in ("font-family: var(--font-display);", "font-weight: 700;", "font-size: 15px;",
                 "letter-spacing: -0.02em;", "color: var(--ivory-ink-muted);"):
        assert rule in wordmark, rule
    assert not re.search(r"#[0-9a-fA-F]{3,8}\b", re.sub(r"/\*.*?\*/", "", section, flags=re.S))
    assert ".root-band-eyebrow[hidden] { display: none; }" in SHELL_CSS


# --------------------------------------------------------------------------
# Q2 — the chat button: the mark, and "ASK" for three visits
# --------------------------------------------------------------------------

def test_the_chat_button_is_the_mark_and_still_says_chat_with_pomona():
    fab = SHELL_HTML[SHELL_HTML.index('id="chat-fab"'):SHELL_HTML.index("</button>", SHELL_HTML.index('id="chat-fab"'))]
    assert 'aria-label="Chat with Pomona" title="Chat with Pomona"' in fab
    assert BUBBLE_PATH not in fab, "the speech bubble is gone"
    assert 'class="pomona-mark"' in fab and MARK_FIRST_PATH in fab
    assert 'stroke-width="1.8"' in fab, "the mark is a logo: its own weight, not the set's 2.2"
    assert '<span class="chat-fab-label" id="chat-fab-label" aria-hidden="true" hidden>Ask</span>' in fab
    assert BUBBLE_PATH not in SHELL_JS and BUBBLE_PATH not in SHELL_CSS


def test_the_fab_css_stacks_the_mark_and_label_inside_the_same_54px():
    fab = SHELL_CSS[SHELL_CSS.index(".chat-fab {"):]
    fab = fab[:fab.index("}")]
    assert "width: 54px;" in fab and "height: 54px;" in fab
    assert "color: var(--apricot-light);" in fab, "the mark is apricot on the spruce"
    assert "flex-direction: column;" in fab
    assert ".chat-fab .pomona-mark { width: 26px; height: 26px;" in SHELL_CSS
    assert ".chat-fab.has-label .pomona-mark { width: 22px; height: 22px; }" in SHELL_CSS
    label = SHELL_CSS[SHELL_CSS.index(".chat-fab-label {"):]
    label = label[:label.index("}")]
    for rule in ("font-size: 9px;", "font-weight: 800;", "letter-spacing: .14em;",
                 "text-transform: uppercase;", "color: var(--apricot-light);"):
        assert rule in label, rule
    assert ".chat-fab-label[hidden] { display: none; }" in SHELL_CSS
    assert ".chat-fab svg { width: 24px; height: 24px; }" not in SHELL_CSS


def test_the_label_constant_and_key_are_named_and_the_button_is_wired():
    assert "var FAB_LABEL_VISITS = 3;" in SHELL_JS
    assert "var FAB_LABEL_KEY = 'pomona.fabLabelVisits';" in SHELL_JS
    assert "pomona.coaching.visits" not in _function("fabLabelCountVisit"), "its own key, not the coaching one"
    wiring = SHELL_JS[SHELL_JS.index("var askBar = document.getElementById('chat-fab');"):]
    wiring = wiring[:wiring.index("\n  }\n")]
    assert "placeFabLabel(askBar);" in wiring
    place = _function("placeFabLabel")
    assert "label.hidden = !show;" in place and "fab.classList.toggle('has-label', show);" in place


_FAB_JS = (
    _region("  var FAB_LABEL_VISITS = 3;", "  var askBar = document.getElementById('chat-fab');")
    + """
function store(initial) {
  var s = { v: initial };
  return { getItem: function () { return s.v; }, setItem: function (k, v) { s.v = v; } };
}
function run(limit, initial) {
  FAB_LABEL_VISITS = limit;
  window.localStorage = store(initial);
  var visits = [];
  for (var i = 0; i < 5; i++) {
    var n = fabLabelCountVisit();
    visits.push({ n: n, show: fabLabelShouldShow(n), stored: window.localStorage.getItem() });
  }
  return visits;
}
var window = {};
var r = { three: run(3, null), zero: run(0, null), always: run(Infinity, null), resumed: run(3, '2') };
window.localStorage = { getItem: function () { throw new Error('private mode'); }, setItem: function () { throw new Error('private mode'); } };
FAB_LABEL_VISITS = 3;
var n = fabLabelCountVisit();
r.throwing = { n: n, show: fabLabelShouldShow(n) };
console.log(JSON.stringify(r));
"""
)


@_needs_node
def test_the_label_shows_for_three_visits_and_not_the_fourth():
    out = _node(_FAB_JS)
    three = out["three"]
    # The counter stops writing once the three are spent (the fourth
    # visit reads 4, and so does every visit after — the only question it
    # answers is "have the three been spent", same as coachCountVisit).
    assert [v["n"] for v in three] == [1, 2, 3, 4, 4]
    assert [v["show"] for v in three] == [True, True, True, False, False]
    assert [v["stored"] for v in three] == ["1", "2", "3", "3", "3"]
    # A device already on its third visit shows once more, then stops.
    assert [v["show"] for v in out["resumed"]] == [True, False, False, False, False]
    # 0 = never; Infinity = always.
    assert not any(v["show"] for v in out["zero"])
    assert all(v["show"] for v in out["always"])
    # Storage that throws (Safari private mode): visit 1, label shown, no crash.
    assert out["throwing"] == {"n": 1, "show": True}


# --------------------------------------------------------------------------
# Q3 — Plan's glyph: the week as a row
# --------------------------------------------------------------------------

WEEK_DOTS = ['<circle cx="%s" cy="12" r="1.4" fill="currentColor" stroke="none"/>' % cx for cx in (4, 8, 12, 16, 20)]


def test_plan_wears_the_week_as_a_row_and_the_bullseye_is_gone():
    icons = _region("  var ICONS = {", "  var REHEAT_ACTION_LABEL")
    week = icons[icons.index("    week:"):icons.index("    pot:")]
    for dot in WEEK_DOTS:
        assert dot in week, dot
    assert '<path d="M3 6.5h18"/><path d="M3 17.5h18"/>' in week
    assert 'viewBox="0 0 24 24"' in week and 'stroke-width="2.2"' in week
    assert "{ key: 'week', path: '/week', label: 'Plan', icon: ICONS.week, week: true }" in SHELL_JS
    assert "ICONS.plate" not in SHELL_JS and "    plate:" not in icons
    assert '<circle cx="12" cy="12" r="8.2"/><circle cx="12" cy="12" r="3"/>' not in SHELL_JS


# --------------------------------------------------------------------------
# Q4 — one stroke width
# --------------------------------------------------------------------------

SWEPT_FILES = [
    "shell.js", "shell.html", "login.html", "onboarding.html", "plan-week.html",
    "meal-setup.html", "chores-setup.html", "share.html", "member-share.html", "inventory.html",
]


def _stroke_tags():
    for name in SWEPT_FILES:
        raw = (STATIC / name).read_text(encoding="utf-8")
        for m in re.finditer(r"<svg\b[^>]*>", raw):
            tag = m.group(0)
            if 'stroke="currentColor"' not in tag:
                continue
            end = raw.find("</svg>", m.end())
            inner = raw[m.end():end]
            width = re.search(r'stroke-width="([^"]*)"', tag)
            yield name, tag, inner, width.group(1) if width else None


def test_the_sweep_left_one_width_for_the_set_and_the_marks_alone():
    icons, marks = [], []
    for name, tag, inner, width in _stroke_tags():
        (marks if (MARK_FIRST_PATH in inner or "MARK_PATHS" in inner) else icons).append((name, width))
    # 71 icons at the time of the sweep (77 stroke SVGs less the six marks);
    # the floor rather than the exact number, so a new icon at 2.2 passes.
    assert len(icons) >= 70, len(icons)
    assert {w for _, w in icons} == {"2.2"}, sorted({(n, w) for n, w in icons if w != "2.2"})
    # The mark: six drawings, each at the weight it was drawn. Since
    # 2026-09-18 the Week 1 screen's head carries the root band's 20px mark
    # (1.8) where the welcome flow's 64px glyph (1.6) used to stand.
    assert len(marks) == 6, marks
    assert {w for _, w in marks} == {"1.6", "1.7", "1.8"}
    assert sorted(n for n, w in marks if w == "1.8") == ["onboarding.html", "shell.html", "shell.js"]


def test_the_week_glyphs_dots_are_filled_not_stroked():
    """fill="currentColor" shapes keep stroke="none": the sweep is about
    strokes, and a stroked dot would grow by the stroke's width."""
    for name, tag, inner, width in _stroke_tags():
        for shape in re.findall(r"<(?:circle|rect|path)\b[^>]*>", inner):
            if 'fill="currentColor"' in shape:
                assert 'stroke="none"' in shape, f"{name}: {shape}"


# --------------------------------------------------------------------------
# The doc moved with the code (Tier 2)
# --------------------------------------------------------------------------

def test_design_system_carries_the_four_decisions():
    assert "~2.2px stroke (one width for the whole set since 2026-09-13; the mark is 1.8)" in DESIGN
    band_row = DESIGN[DESIGN.index("| Root band |"):]
    band_row = band_row[:band_row.index("\n")]
    assert "BAND_IDENTITY" in band_row and "wordmark" in band_row
    chat = DESIGN[DESIGN.index("**The chat is one icon on every screen"):]
    chat = chat[:chat.index("\n- **")]
    assert "the mark" in chat and "Ask" in chat and "three" in chat
