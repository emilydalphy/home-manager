"""What's left of the Identity Round (Emily, 2026-09-13), under Bright Dusk.

Bright Dusk (Emily, 2026-11-05) replaced two of its four decisions:

  Q1      The band and its mark and wordmark are gone. A root opens with a
          plain title and one line on cream; the date, or the context, still
          leads that line ("Sunday, Sep 13 · 1 cook tonight").
  Q2      The chat button is Ask, the apricot pill at the tab bar's end, with
          the word on it. The mark and the three-visit label went with it.
  Q3      Plan's tab glyph is still the week as a row, drawn as on the board.
  Q4      One 2.2px stroke for the icon set; the tab bar's four icons are 1.9
          as drawn on the boards, and the mark, a logo, keeps its own weight
          (the sweep is guarded in tests/test_design_hygiene.py, (d)).

The head is pure enough to run under node; the rest is read off the source.
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


# The head, with everything it reads: its helpers, the gear, and a panel
# fake for setRootBand.
_BAND_JS = (
    "function escapeHtml(s){return String(s == null ? '' : s).replace(/&/g,'&amp;').replace(/</g,'&lt;')"
    ".replace(/>/g,'&gt;').replace(/\"/g,'&quot;');}\n"
    "var PREFS_GEAR_ICON = '<svg></svg>';\n"
    + _region("  function bandDateLabel(", "  // ---------- Empty states as designed moments ----------")
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
  // As rootBandHtml renders it: always hidden, it leads the line instead.
  els['#b-eyebrow'].hidden = true;
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
# The head: no band, no mark, the date in the line
# --------------------------------------------------------------------------

@_needs_node
def test_the_head_is_a_title_and_one_line_and_the_date_leads_it():
    out = _node(
        _BAND_JS
        + "console.log(JSON.stringify({"
        + " now: rootBandHtml({ id: 'x', eyebrow: 'Sunday, Sep 13', title: 'Now', sub: '2 of 5 done', badge: 'Draft' }),"
        + " plan: rootBandHtml({ id: 'p', eyebrow: 'Sep 14–20', title: 'This week', sub: 'your turn', badge: 'Draft' }),"
        + " bare: rootBandHtml({ id: 'y', title: 'Shop' }),"
        + " day: rootBandHtml({ id: 'd', title: 'Friday', noGear: true })"
        + "}));"
    )
    now = out["now"]
    assert 'class="root-band screen-head"' in now
    assert "root-band-brand" not in now and "root-band-wordmark" not in now and "Pomona" not in now
    assert MARK_FIRST_PATH not in now, "no mark in the head"
    assert 'id="x-eyebrow" hidden>Sunday, Sep 13<' in now
    assert 'id="x-sub" data-sub="2 of 5 done">Sunday, Sep 13 · 2 of 5 done<' in now
    assert 'id="x-badge">Draft<' in now
    assert 'id="p-sub" data-sub="your turn">Sep 14–20 · your turn<' in out["plan"]
    # Nothing to fold in and nothing to say: no line at all.
    assert 'id="y-sub" data-sub="" hidden' in out["bare"]
    assert "prefs-gear" in out["bare"] and "prefs-gear" not in out["day"], "noGear leaves the gear out"


@_needs_node
def test_set_root_band_keeps_the_date_in_the_line_whichever_half_changes():
    out = _node(
        _BAND_JS
        + "var r = {};\n"
        # A load: the line arrives, the eyebrow was rendered.
        + "r.load = after(bandPanel('Sunday, Sep 13', ''), { sub: '2 of 5 done', badge: 'Draft' });\n"
        # Only the line changes: the date stays in front of it.
        + "r.tick = after(bandPanel('Sunday, Sep 13', '2 of 5 done'), { sub: '3 of 5 done' });\n"
        # Only the eyebrow changes (Cook re-rendering the day): the line keeps its words.
        + "r.day = after(bandPanel('Saturday, Sep 12', '1 cook tonight'), { eyebrow: 'Sunday, Sep 13' });\n"
        # The line empties: the date alone, still shown.
        + "r.empty = after(bandPanel('Sunday, Sep 13', '2 of 5 done'), { sub: '' });\n"
        # A title-only update touches neither.
        + "r.title = after(bandPanel('Sunday, Sep 13', '2 of 5 done'), { title: 'Now' });\n"
        + "console.log(JSON.stringify(r));"
    )
    assert out["load"]["sub"] == "Sunday, Sep 13 · 2 of 5 done" and not out["load"]["subHidden"]
    assert out["load"]["eyebrowHidden"], "the eyebrow stays hidden after an update too"
    assert out["tick"]["sub"] == "Sunday, Sep 13 · 3 of 5 done"
    assert out["day"]["sub"] == "Sunday, Sep 13 · 1 cook tonight"
    assert out["empty"]["sub"] == "Sunday, Sep 13" and not out["empty"]["subHidden"]
    assert out["title"]["sub"] == "2 of 5 done", "an untouched line is left alone (data-sub is the source, not re-derived)"


def test_now_hands_the_band_todays_date_as_its_eyebrow():
    build = _function("buildTodayPanel")
    assert "eyebrow: bandDateLabel()," in build
    assert "toLocaleDateString" not in build, "the one date format lives in bandDateLabel"
    fn = _function("bandDateLabel")
    assert "{ weekday: 'long', month: 'short', day: 'numeric' }" in fn
    assert "'T00:00:00'" in fn, "local, not UTC"


@_needs_node
def test_shop_and_cook_lead_their_lines_with_their_eyebrow():
    """Cook's eyebrow is the date; Shop's is "This week"."""
    out = _node(
        _BAND_JS
        + "var r = {};\n"
        + "r.shop = groBandEyebrow({ all: 60, stops: ['A'] });\n"
        + "r.shopNone = groBandEyebrow({ all: 0, stops: [] });\n"
        + "r.cook = cookBandEyebrow('2026-09-13');\n"
        + "r.cookLine = after(bandPanel(cookBandEyebrow('2026-09-13'), ''), { sub: '1 cook tonight' }).sub;\n"
        + "r.shopLine = after(bandPanel(r.shop, ''), { sub: groBandLine({ all: 14, stops: ['A', 'B'] }) }).sub;\n"
        + "r.shopLineNone = after(bandPanel(r.shopNone, ''), { sub: groBandLine({ all: 0, stops: [] }) }).sub;\n"
        + "console.log(JSON.stringify(r));"
    )
    assert out["shop"] == "This week" and out["shopNone"] == "This week"
    assert out["shopLine"] == "This week · 14 things, two stores."
    assert out["shopLineNone"] == "This week", "nothing to buy: the eyebrow alone — the empty moment says the rest"
    assert out["cook"] == "Sunday, Sep 13"
    assert out["cookLine"] == "Sunday, Sep 13 · 1 cook tonight"


def test_plan_and_cook_pass_through_the_same_band_builder():
    step = _function("renderMealsStep")
    assert "bandSlot.innerHTML = rootBandHtml(parts);" in step
    parts = _function("weekBandParts")
    assert "sub: sub.join(' · ')," in parts and "eyebrow: eyebrow," in parts
    kitchen = _function("renderKitchen")
    assert "setRootBand(panel, 'kit-band', { eyebrow: cookBandEyebrow(todayIso) });" in kitchen
    assert "setRootBand(panel, 'kit-band', { sub: kitchenSubtitle(rows, meals, todayIso) });" in kitchen
    shop = _function("renderGrocery")
    assert "setRootBand(panel, 'gro-band', { eyebrow: '', sub: '' });" in shop


# --------------------------------------------------------------------------
# Ask: the apricot pill at the end of the tab bar
# --------------------------------------------------------------------------

def test_ask_is_the_word_on_an_apricot_pill_inside_the_tab_bar():
    bar = SHELL_HTML[SHELL_HTML.index('<div id="tab-bar">'):]
    fab = bar[:bar.index("</button>")]
    assert 'id="chat-fab"' in fab and fab.rstrip().endswith(">Ask")
    assert 'class="pomona-mark"' not in fab, "the word is the button"
    assert 'id="ask-bar-dock"' not in SHELL_HTML, "nothing floats over the content"
    assert "chat-fab-label" not in SHELL_HTML + SHELL_JS + SHELL_CSS
    assert "FAB_LABEL_VISITS" not in SHELL_JS
    css = SHELL_CSS[SHELL_CSS.index(".chat-fab {"):]
    css = css[:css.index("}")]
    for rule in ("width: 58px;", "height: 44px;", "background: var(--apricot);", "color: var(--night-ink);",
                 "border-radius: var(--radius-pill);", "font-size: 14px;", "font-weight: 700;"):
        assert rule in css, rule
    assert "position:" not in css, "it sits in the bar's grid"


def test_ask_goes_to_the_end_of_the_bar_and_opens_the_sheet():
    assert "tabBarEl.appendChild(askTabBtn);" in SHELL_JS
    assert SHELL_JS.index("tabBarEl.appendChild(btn);") < SHELL_JS.index("tabBarEl.appendChild(askTabBtn);")
    wiring = SHELL_JS[SHELL_JS.index("var askBar = document.getElementById('chat-fab');"):]
    wiring = wiring[:wiring.index("\n  }\n")]
    assert "openAskSheet('', weekAskContext())" in wiring


# --------------------------------------------------------------------------
# Q3 — Plan's glyph: the week as a row
# --------------------------------------------------------------------------

def test_plan_wears_the_week_as_a_row_and_the_bullseye_is_gone():
    tabs = _region("  var TAB_ICONS = {", "  var TABS = [")
    week = tabs[tabs.index("    week:"):tabs.index("    bag:")]
    assert '<path d="M4 7h16M4 17h16"/>' in week, "two rules"
    assert 'd="M5 12h.01M9.5 12h.01M14 12h.01M18.5 12h.01"' in week, "the days between them"
    assert 'viewBox="0 0 24 24"' in week and 'stroke-width="1.9"' in week
    assert "{ key: 'week', path: '/week', label: 'Plan', icon: TAB_ICONS.week, week: true }" in SHELL_JS
    assert "ICONS.plate" not in SHELL_JS
    assert '<circle cx="12" cy="12" r="8.2"/><circle cx="12" cy="12" r="3"/>' not in SHELL_JS


# --------------------------------------------------------------------------
# Q4 — one stroke width
# --------------------------------------------------------------------------

SWEPT_FILES = [
    "shell.js", "shell.html", "login.html", "onboarding.html", "plan-week.html",
    "chores-setup.html", "share.html", "member-share.html", "inventory.html",
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
    # The tab bar's four are 1.9, as drawn on the boards (TAB_ICONS).
    tab_icons = [(n, w) for n, w in icons if w == "1.9"]
    assert tab_icons == [("shell.js", "1.9")] * 4, tab_icons
    assert {w for _, w in icons} == {"2.2", "1.9"}, sorted({(n, w) for n, w in icons if w not in ("2.2", "1.9")})
    # The mark: each drawing at the weight it was drawn, the same paths,
    # never redrawn. The chat button's went with Bright Dusk (2026-11-05).
    assert len(marks) == 6, marks
    assert {w for _, w in marks} == {"1.6", "1.7", "1.8"}
    assert sorted(n for n, w in marks if w == "1.8") == ["onboarding.html", "shell.js"]
    assert sorted(n for n, w in marks if w == "1.6") == ["onboarding.html", "plan-week.html"]


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

def test_design_system_carries_the_decisions_that_stand():
    assert "the tab bar's four icons are 1.9" in DESIGN
    assert "the Pomona mark, a logo, keeps the weight it was drawn at" in DESIGN
    assert "Ask, the apricot pill with the word Ask at its end" in DESIGN
    assert "BAND_IDENTITY" not in DESIGN
