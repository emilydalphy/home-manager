"""Now's content is one strip down the day (Emily, 2026-09-12, picked from
the "Beyond lists" canvas — artboard "Now · A · The day as a strip"; built
2026-09-13).

Every move is a node on a 44px | 1fr grid: the time, a 28px dot and a
hairline down to the next node on the left; the title and one meta line on
the right. The dot says the state — done (celadon, a tick), now (apricot,
the move's icon), later (surface, a hairline, the icon) — and is the move's
tick. Exactly ONE node is tinted: the next-up move, a celadon-tint tile
with a NOW eyebrow (§2b S3, S6). Its action stays in the dock. Ticking a
node settles with the grocery row's transitions (animation 3).

The builders run under node against a small stub panel, the house standard
(see tests/nodeharness.py); the CSS is checked as source.
"""
from __future__ import annotations

import json
import re
import shutil
from pathlib import Path

import pytest

import nodeharness

REPO = Path(__file__).resolve().parents[1]
SHELL_JS = (REPO / "static" / "shell.js").read_text(encoding="utf-8")
SHELL_CSS = (REPO / "static" / "shell.css").read_text(encoding="utf-8")
CLAUDE_MD = (REPO / "CLAUDE.md").read_text(encoding="utf-8")

_needs_node = pytest.mark.skipif(
    shutil.which("node") is None, reason="node is needed to execute the strip's own builders"
)


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


def _node(script: str):
    res = nodeharness.run_node(script, timeout=30)
    assert res.returncode == 0, f"node failed: {res.stderr}"
    return json.loads(res.stdout.strip().splitlines()[-1])


# The strip's builders, with the two things they lean on from elsewhere in
# Today (the dish link and the tick glyph), and a panel that keeps what is
# written into #today-rest, #today-dock and the band's parts.
def _prelude() -> str:
    return (
        "function escapeHtml(s){return String(s == null ? '' : s).replace(/&/g,'&amp;')"
        ".replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/\"/g,'&quot;');}\n"
        + _region("  var TICK_ICON =", "  var DOTS_ICON =")
        + _region("  // ---------- Now: the day as a strip ----------", "  function tomorrowCardHtml(")
        + _function("moveRecipeTarget")
        + _function("moveDishHtml")
        + _function("tomorrowCardHtml")
        + _function("renderTodayMoves")
        + _function("renderTodayDock")
        + _function("todayIsEmpty")
        + _function("todayNeedsPlan")
        + _function("setRootBand")
        # The band's identity lead (2026-09-13): setRootBand folds the
        # eyebrow into the sub-line under BAND_IDENTITY 'wordmark'; the
        # fake band's eyebrow is empty here, so the line is the day's own.
        + _region("  var BAND_IDENTITY = ", "  function rootBandHtml(")
        + "  var WEEK_STATE_LABELS = { set: 'Week set', draft: 'Draft' };\n"
        + "  function renderTodayEmpty() {}\n"
        + "  function runTodayMoveAction() {}\n"
        + "  function toggleTodayMove() {}\n"
        + "  function openRecipeFor() {}\n"
        + "  function todayLocalStr() { return '2026-09-13'; }\n"
        + """
function el() {
  var e = { innerHTML: '', textContent: '', hidden: false, className: '', _classes: [] };
  e.querySelector = function () { return null; };
  e.querySelectorAll = function () { return []; };
  Object.defineProperty(e, 'offsetHeight', { get: function () { return 1; } });
  return e;
}
function makePanel() {
  var panel = { _els: {} };
  panel.querySelector = function (sel) {
    if (sel.indexOf('.day-node[data-move-id=') === 0) {
      var id = sel.split('"')[1];
      var m = new RegExp('<div class="(day-node is-[a-z]+)" data-move-id="' + id.replace(/[:]/g, '\\\\$&') + '"').exec(panel._els['#today-rest'].innerHTML);
      if (!m) return null;
      var node = el(); node._log = []; node._real = m[1];
      Object.defineProperty(node, 'className', {
        get: function () { return node._log.length ? node._log[node._log.length - 1] : node._real; },
        set: function (v) { node._log.push(v); }
      });
      panel._lastNode = node;
      return node;
    }
    if (!panel._els[sel]) panel._els[sel] = el();
    return panel._els[sel];
  };
  panel.querySelectorAll = function () { return []; };
  return panel;
}
function render(data, opts) {
  var panel = makePanel();
  opts = opts || {};
  if (opts.openDinner) panel._openDinnerCard = true;
  panel._nudge = opts.nudge || null;
  renderTodayMoves(panel, data);
  return {
    strip: panel.querySelector('#today-rest').innerHTML,
    dock: panel.querySelector('#today-dock').innerHTML,
    dockHidden: panel.querySelector('#today-dock').hidden,
    sub: panel.querySelector('#today-band-sub').textContent,
    panel: panel
  };
}
"""
    )


def _move(kind, id_, title, start, end=None, **extra):
    m = {
        "id": id_, "kind": kind, "title": title, "detail": extra.pop("detail", kind + " · line"),
        "reason": "", "date": "2026-09-13", "slot": None,
        "window_start": "2026-09-13T" + start, "window_end": "2026-09-13T" + (end or start),
        "weight": 2, "action": {"label": "Done", "target": {"kind": "check_prep", "taskId": 1}},
        "done": False, "tickable": True, "overdue": False, "entry_id": None, "task_id": None,
        "duration_min": 0, "time_label": "", "chips": [],
    }
    m.update(extra)
    return m


# The day moves.py would hand over, in ITS order (window_start): the two
# all-day moves first, then the meals.
def _day():
    return [
        _move("shop", "shop:2026-09-13", "Shop for tonight", "00:00:00", "18:30:00",
              tickable=False, detail="3 items · by 6:30", weight=3,
              action={"label": "Open the list", "target": {"tab": "grocery"}}),
        _move("fridge", "fridge:4", "Move the chicken to the fridge", "00:00:00", "22:00:00",
              detail="fridge move · by tonight", reason="for Thursday’s skewers",
              action={"label": "Done", "target": {"kind": "check_prep", "taskId": 4}}),
        _move("reheat", "reheat:11", "Egg White Bites", "08:00:00", "10:00:00", weight=1,
              done=True, detail="made ahead Sunday · reheat · 8:00", entry_id=11,
              action={"label": "Mark eaten", "target": {"kind": "check_meal", "entryId": 11}}),
        _move("cook", "cook:12", "Chopped Salad", "12:00:00", "14:00:00", weight=3,
              duration_min=0, detail="lunch · noon", entry_id=12,
              action={"label": "Cook this", "target": {"tab": "kitchen", "cookFocus": {
                  "entryId": 12, "date": "2026-09-13", "slot": "lunch", "title": "Chopped Salad"}}}),
        _move("cook", "cook:13", "Chicken Skewers", "17:55:00", "20:30:00", weight=3,
              duration_min=35, detail="dinner · 35 min · 6:30", entry_id=13,
              action={"label": "Cook this", "target": {"tab": "kitchen", "cookFocus": {
                  "entryId": 13, "date": "2026-09-13", "slot": "dinner", "title": "Chicken Skewers"}}}),
    ]


def _payload(featured, moves=None, **extra):
    moves = moves if moves is not None else _day()
    data = {"date": "2026-09-13", "moves": moves, "featured": featured,
            "done": sum(1 for m in moves if m["done"]), "total": len(moves),
            "week_state": "set", "tomorrow": None, "holiday": None}
    data.update(extra)
    return data


def _nodes(strip: str) -> list[tuple[str, str, str]]:
    """(state, move id, time eyebrow) for every node, top to bottom."""
    return re.findall(
        r'<div class="day-node is-(done|now|later)" data-move-id="([^"]+)">'
        r'<div class="day-node-rail"><span class="day-node-time">([^<]*)</span>',
        strip,
    )


# --------------------------------------------------------------------------
# The nodes and their states
# --------------------------------------------------------------------------

@_needs_node
def test_every_move_is_a_node_and_the_states_read_off_done_and_featured():
    out = _node(_prelude() + "console.log(JSON.stringify(render(" + json.dumps(_payload("cook:13")) + ")));")
    nodes = _nodes(out["strip"])
    assert [n[1] for n in nodes] == ["shop:2026-09-13", "reheat:11", "cook:12", "cook:13", "fridge:4"], (
        "top to bottom down the day: the shop (any time today), breakfast, lunch, dinner, then the fridge move (by tonight)"
    )
    states = dict((n[1], n[0]) for n in nodes)
    assert states == {
        "shop:2026-09-13": "later", "reheat:11": "done", "cook:12": "later",
        "cook:13": "now", "fridge:4": "later",
    }
    assert out["strip"].startswith('<div class="day-strip">')
    assert out["sub"] == "1 of 5 done"


@_needs_node
def test_exactly_one_node_is_tinted_and_it_is_the_next_up_move():
    out = _node(_prelude() + "console.log(JSON.stringify(render(" + json.dumps(_payload("cook:13")) + ")));")
    strip = out["strip"]
    assert strip.count("is-now") == 1, "§2b S3: one tinted node, ever"
    assert strip.count("day-node-tile") == 1
    tile = strip.split('class="day-node-tile"', 1)[1].split("</button>", 1)[0]
    assert 'data-move-action="cook:13"' in strip.split('class="day-node-tile"', 1)[0][-40:] or \
        'class="day-node-tile" data-move-action="cook:13"' in strip
    assert '<span class="day-node-eyebrow">Now</span>' in tile, "S6: the tint carries the word for it"
    assert '<span class="day-node-title">Chicken Skewers</span>' in tile
    assert '<span class="day-node-meta">dinner · 35 min · 6:30</span>' in tile
    # A later node has no tile and no eyebrow — title and meta only.
    later = strip.split('data-move-id="cook:12">', 1)[1].split('<div class="day-node is-', 1)[0]
    assert "day-node-tile" not in later and "day-node-eyebrow" not in later
    assert '<button type="button" class="day-node-text day-node-open" data-move-action="cook:12">' in later


@_needs_node
def test_no_node_is_tinted_while_tonights_dinner_is_an_open_question():
    """The one exception to 'the tint is whatever the server ranked first'
    (Emily, 2026-09-08): an undecided dinner is itself what's next, and its
    needs-you card is already on screen."""
    out = _node(_prelude() + "console.log(JSON.stringify(render(" + json.dumps(_payload("cook:13")) + ", { openDinner: true })));")
    assert "is-now" not in out["strip"]
    assert out["dockHidden"] is True and out["dock"] == ""


@_needs_node
def test_the_dot_is_the_tick_and_says_the_state():
    out = _node(_prelude() + "console.log(JSON.stringify(render(" + json.dumps(_payload("cook:13")) + ")));")
    strip = out["strip"]
    # Done: a pressed tick that undoes.
    done = strip.split('data-move-id="reheat:11">', 1)[1].split("</div>", 1)[0]
    assert '<button type="button" class="day-tick is-done" data-move-tick="reheat:11" aria-pressed="true" aria-label="Put it back on the list">' in done
    # Now and later: an unpressed tick.
    now = strip.split('data-move-id="cook:13">', 1)[1].split("</div>", 1)[0]
    assert 'class="day-tick" data-move-tick="cook:13" aria-pressed="false" aria-label="Tick it off"' in now
    # Both glyphs are always in the dot; the state class picks one, so a
    # tick can settle from the kind's icon to the tick (animation 3).
    for rail in (done, now):
        assert '<span class="day-dot"><span class="day-dot-icon"><svg' in rail
        assert '<span class="day-dot-tick"><svg' in rail
    # The shop has nothing behind its tick (moves.py's `tickable`): a plain
    # mark on the rail, not a button.
    shop = strip.split('data-move-id="shop:2026-09-13">', 1)[1].split("</div>", 1)[0]
    assert '<span class="day-tick" aria-hidden="true"><span class="day-dot">' in shop
    assert "data-move-tick" not in shop and "<button" not in shop


@_needs_node
def test_a_done_node_keeps_its_dish_name_as_a_link_and_nothing_else_tappable():
    moves = _day()
    moves[4]["done"] = True  # dinner cooked
    out = _node(_prelude() + "console.log(JSON.stringify(render(" + json.dumps(_payload(None, moves)) + ")));")
    node = out["strip"].split('data-move-id="cook:13">', 1)[1].split('<div class="day-node is-', 1)[0]
    body = node.split('<div class="day-node-body">', 1)[1]
    assert '<button type="button" class="day-node-title dish-link" data-move-dish="cook:13">Chicken Skewers</button>' in body
    assert "data-move-action" not in body
    # A done reheat has no recipe behind it, so its name is plain text.
    reheat = out["strip"].split('data-move-id="reheat:11">', 1)[1].split('<div class="day-node is-', 1)[0]
    assert '<span class="day-node-title">Egg White Bites</span>' in reheat


# --------------------------------------------------------------------------
# The dock follows the next-up move
# --------------------------------------------------------------------------

@_needs_node
def test_the_dock_carries_the_next_up_moves_action_and_follows_it():
    script = _prelude() + (
        "var a = render(" + json.dumps(_payload("cook:13")) + ");\n"
        "var b = render(" + json.dumps(_payload("fridge:4")) + ");\n"
        "var c = render(" + json.dumps(_payload("shop:2026-09-13")) + ");\n"
        "console.log(JSON.stringify({ a: a.dock, b: b.dock, c: c.dock, bStrip: b.strip }));"
    )
    out = _node(script)
    assert out["a"] == '<button type="button" class="dock-primary" data-move-action="cook:13">Cook this</button>'
    assert out["b"] == '<button type="button" class="dock-primary" data-move-action="fridge:4">Done</button>'
    assert out["c"] == '<button type="button" class="dock-primary" data-move-action="shop:2026-09-13">Open the list</button>'
    # And the tint moved with it.
    assert [n for n in _nodes(out["bStrip"]) if n[0] == "now"][0][1] == "fridge:4"
    # The node's tile runs the same action as the dock (one thing, two
    # places to reach it), and never carries a button of its own.
    tile = out["bStrip"].split('class="day-node-tile"', 1)[1].split("</button>", 1)[0]
    assert "dock-primary" not in tile and "<button" not in tile
    assert '<span class="day-node-meta day-node-why">for Thursday’s skewers</span>' in tile


@_needs_node
def test_nothing_left_today_names_tomorrow_after_the_strip():
    moves = _day()
    for m in moves:
        m["done"] = True
    moves.pop(0)  # a shop stops being a move once the list is empty
    tomorrow = _move("cook", "cook:20", "Pancakes", "08:00:00", "10:00:00", entry_id=20, date="2026-09-14",
                     action={"label": "Cook this", "target": {"tab": "kitchen", "cookFocus": {
                         "entryId": 20, "date": "2026-09-14", "slot": "breakfast", "title": "Pancakes"}}})
    out = _node(_prelude() + "console.log(JSON.stringify(render(" + json.dumps(_payload(None, moves, tomorrow=tomorrow)) + ")));")
    strip = out["strip"]
    assert "is-now" not in strip and strip.count('"day-node is-done"') == 4
    assert strip.index("</div></div>") < strip.index('class="shell-card tomorrow-card"'), "the tomorrow card sits after the strip"
    assert 'data-move-dish="cook:20">Pancakes</button>' in strip
    assert out["dockHidden"] is True


# --------------------------------------------------------------------------
# Times are a person's times
# --------------------------------------------------------------------------

@_needs_node
def test_the_rail_says_the_clock_the_way_a_person_reads_it():
    out = _node(_prelude() + "console.log(JSON.stringify(render(" + json.dumps(_payload("cook:13")) + ")));")
    times = dict((n[1], n[2]) for n in _nodes(out["strip"]))
    assert times == {
        "shop:2026-09-13": "Today",      # any time today
        "reheat:11": "8:00",             # the meal's time — never "08:00"
        "cook:12": "Noon",               # 12:00 is noon, as moves.py says it
        "cook:13": "6:30",               # window_start 5:55 + 35 min of cooking = on the table at 6:30
        "fridge:4": "Tonight",           # by tonight
    }


@_needs_node
def test_the_clock_helper_handles_the_edges():
    script = _prelude() + (
        "console.log(JSON.stringify({\n"
        "  pm: moveStripTime({ kind: 'reheat', window_start: '2026-09-13T18:05:00' }),\n"
        "  midnightish: moveStripTime({ kind: 'reheat', window_start: '2026-09-13T00:30:00' }),\n"
        "  prep: moveStripTime({ kind: 'prep', window_start: '2026-09-13T00:00:00', window_end: '2026-09-13T22:00:00' }),\n"
        "  broken: moveStripTime({ kind: 'cook', window_start: 'nonsense', time_label: 'by tonight' }),\n"
        "  order: dayStripOrder([\n"
        "    { id: 'p', kind: 'prep', window_start: '2026-09-13T00:00:00', window_end: '2026-09-13T22:00:00' },\n"
        "    { id: 'd', kind: 'cook', window_start: '2026-09-13T18:00:00', duration_min: 30 },\n"
        "    { id: 'b', kind: 'reheat', window_start: '2026-09-13T08:00:00' },\n"
        "    { id: 's', kind: 'shop', window_start: '2026-09-13T00:00:00', window_end: '2026-09-13T18:30:00' }\n"
        "  ]).map(function (m) { return m.id; })\n"
        "}));"
    )
    out = _node(script)
    assert out["pm"] == "6:05"
    assert out["midnightish"] == "12:30"
    assert out["prep"] == "Tonight"
    assert out["broken"] == "by tonight", "an unreadable clock falls back to the server's own label"
    assert out["order"] == ["s", "b", "d", "p"]


# --------------------------------------------------------------------------
# A tick settles (animation 3)
# --------------------------------------------------------------------------

@_needs_node
def test_a_ticked_node_settles_from_the_state_it_was_in():
    script = _prelude() + (
        "var r = render(" + json.dumps(_payload("cook:13")) + ");\n"
        "todayAnimateNodeSettle(r.panel, 'cook:13', 'later');\n"
        "var log = r.panel._lastNode._log;\n"
        "todayAnimateNodeSettle(r.panel, 'nope', 'later');\n"
        "console.log(JSON.stringify({ log: log }));"
    )
    out = _node(script)
    # The opposite state first (so there is a frame to transition from),
    # then the real one — the same trick as groAnimateRowSettle.
    assert out["log"] == ["day-node is-later", "day-node is-now"]
    toggle = _function("toggleTodayMove")
    assert "var fromState = was ? 'done' : (panel._featured && panel._featured.id === moveId ? 'now' : 'later');" in toggle
    assert "todayAnimateNodeSettle(panel, moveId, fromState);" in toggle
    assert toggle.index("renderTodayMoves(panel, data);") < toggle.index("todayAnimateNodeSettle(panel, moveId, fromState);")


# --------------------------------------------------------------------------
# The CSS: one section, tokens only, the three dot states, the motion
# --------------------------------------------------------------------------

def _strip_css() -> str:
    start = SHELL_CSS.index("NOW — the day as a strip")
    start = SHELL_CSS.rindex("/* ====", 0, start)
    end = SHELL_CSS.index("/* Nothing left today: name tomorrow's first move", start)
    return SHELL_CSS[start:end]


def test_the_strip_css_is_one_banner_section_built_from_tokens():
    section = _strip_css()
    for rule in (".day-strip {", ".day-node {", ".day-node-rail {", ".day-node-time {", ".day-tick {",
                 ".day-dot {", ".day-node-line {", ".day-node-text {", ".day-node-title {",
                 ".day-node-meta {", ".day-node-tile {", ".day-node-eyebrow {"):
        assert rule in section, f"missing {rule}"
    body = re.sub(r"/\*.*?\*/", "", section, flags=re.S)
    assert not re.search(r"#[0-9a-fA-F]{3,8}\b", body), "a literal colour in the strip CSS (Rule 9)"
    # The grid and the rail, as designed.
    assert "grid-template-columns: 44px 1fr;" in section
    assert "gap: 0 10px;" in section
    dot = section.split(".day-dot {", 1)[1].split("}", 1)[0]
    assert "width: 28px;" in dot and "height: 28px;" in dot
    assert "border: 1.5px solid var(--hairline-strong);" in dot
    assert "background: var(--surface);" in dot and "color: var(--ink-secondary);" in dot
    line = section.split(".day-node-line {", 1)[1].split("}", 1)[0]
    assert "width: 1.5px;" in line and "background: var(--hairline);" in line


def test_the_three_dot_states_and_the_one_tint():
    section = _strip_css()
    now = section.split(".day-node.is-now .day-dot {", 1)[1].split("}", 1)[0]
    assert "background: var(--apricot);" in now and "color: var(--on-accent-ink);" in now
    done = section.split(".day-node.is-done .day-dot {", 1)[1].split("}", 1)[0]
    assert "background: var(--celadon);" in done and "color: var(--on-accent-ink);" in done
    assert ".day-node.is-done .day-dot-icon { opacity: 0; }" in section
    assert ".day-node.is-done .day-dot-tick { opacity: 1; }" in section
    assert ".day-dot-icon svg { display: block; width: 18px; height: 18px; }" in section
    assert ".day-dot-tick svg { display: block; width: 16px; height: 16px; }" in section
    tile = section.split(".day-node-tile {", 1)[1].split("}", 1)[0]
    assert "background: var(--celadon-tint);" in tile
    assert "border: 1.5px solid var(--celadon-edge);" in tile
    assert "border-radius: var(--radius-tile);" in tile
    assert "padding: 12px 14px;" in tile
    assert "color: var(--celadon-label);" in section.split(".day-node-eyebrow {", 1)[1].split("}", 1)[0]
    title = section.split(".day-node-tile .day-node-title {", 1)[1].split("}", 1)[0]
    assert "font-family: var(--font-display);" in title and "font-size: 17px;" in title and "letter-spacing: -0.02em;" in title
    assert ".day-node.is-done .day-node-title { color: var(--ink-done); }" in section
    # Rule 6: the row is the tap target, 52px+; the tick is 44px around the dot.
    text = section.split(".day-node-text {", 1)[1].split("}", 1)[0]
    assert "min-height: 52px;" in text and "padding: 6px 0 14px;" in text
    tick = section.split(".day-tick {", 1)[1].split("}", 1)[0]
    assert "width: 44px;" in tick and "height: 44px;" in tick
    # The eyebrow is the eyebrow: 10px / 800 / uppercase / --ink-muted.
    time = section.split(".day-node-time {", 1)[1].split("}", 1)[0]
    assert "font-size: 10px;" in time and "font-weight: 800;" in time
    assert "text-transform: uppercase;" in time and "color: var(--ink-muted);" in time


def test_the_settle_is_animation_three_not_a_fourth():
    motion = SHELL_CSS[SHELL_CSS.index("   Motion (Emily, 2026-09-11)."):]
    assert "a ticked node on Now's day strip" in motion
    # The dot rides the grocery checkbox's own transition rule.
    assert ".gro-row .gro-box, .day-dot {" in motion
    assert ".day-node.is-done .day-node-title { text-decoration-color: currentColor; }" in motion
    strike = motion.split(".day-node-title {", 1)[1].split("}", 1)[0]
    assert "text-decoration: line-through;" in strike and "text-decoration-color: transparent;" in strike
    assert "text-decoration-color var(--motion-base) var(--motion-ease)" in strike
    # Nothing in the strip section itself animates — the transitions live
    # in Motion, with the other two.
    plain = re.sub(r"/\*.*?\*/", "", _strip_css(), flags=re.S)
    assert "transition" not in plain and "animation" not in plain


def test_the_old_card_and_row_rules_are_retired():
    for dead in (".nextup-card", ".today-area-nextup", ".nextup-foot", ".nextup-dish", ".rest-card",
                 ".rest-title", ".rest-list", ".rest-row", ".rest-row-text", ".rest-row-title",
                 ".rest-row-detail", ".rest-done", ".rest-done-head", ".hero-chips-spacer"):
        assert not re.search(r"(^|[\s,}])%s\s*[,{:\[.]" % re.escape(dead), SHELL_CSS, re.M), (
            f"{dead} still has a CSS rule — the day strip replaced it"
        )
    # .nextup-when went too (2026-09-18): the Meal step's hero, which had
    # printed the day with it, is gone with "The recipe is the recipe".
    assert ".nextup-when" not in SHELL_CSS


def test_the_decision_log_has_the_entry():
    assert "2026-09-13 — Now is one strip down the day" in CLAUDE_MD
