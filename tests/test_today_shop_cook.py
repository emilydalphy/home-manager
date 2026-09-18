"""Today: two cards, Shop and Cook, tagged Morning · Afternoon · Evening
(Emily, 2026-09-17, Loop Board "Today: rename Now → Today; group by Shop /
Cook"; mockup 17a-today-tags). Built on the 2026-09-13 day strip.

The first tab is "Today" again. The day's moves sit in two cards in the
gutter: SHOP (bag in a 32px sand tile, "N stops", one row per store stop —
"Costco · 6 things" over the first few things) and COOK (pot, "N of M",
fridge moves, prep, cooks and reheats in day order). A group with nothing
in it is not drawn. Each row is the move's tick (the 28px dot in 44px of
tap), the title, one clock-free meta line, and a MORNING / AFTERNOON /
EVENING tag where the strip printed a clock. Exactly ONE row is tinted
across both groups: the next-up move, celadon-tint with a NOW eyebrow (§2b
S3, S6). Its action stays in the dock — "Go shopping" for the shop, never
"Open the list". Ticking a row settles with the grocery row's transitions
(animation 3).

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
        + _region("  // ---------- Today: Shop and Cook ----------", "  function tomorrowCardHtml(")
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
        "duration_min": 0, "time_label": "", "meta": extra.pop("meta", ""), "chips": [],
    }
    m.update(extra)
    return m


# The day moves.py would hand over, in ITS order (window_start): the two
# all-day moves first, then the meals.
def _day():
    return [
        _move("shop", "shop:2026-09-13", "Shop for tonight", "00:00:00", "17:55:00",
              tickable=False, detail="6 items · by 5:55", weight=3, timed=True,
              meta="orzo, salmon, black beans…",
              stops=[{"store": "Costco", "count": 6, "items": ["orzo", "salmon", "black beans"]}],
              action={"label": "Go shopping", "target": {"tab": "grocery"}}),
        _move("fridge", "fridge:4", "Move the chicken to the fridge", "00:00:00", "22:00:00",
              detail="fridge move · by tonight", reason="for Thursday’s skewers", meta="for Thursday’s skewers",
              action={"label": "Done", "target": {"kind": "check_prep", "taskId": 4}}),
        _move("reheat", "reheat:11", "Egg White Bites", "08:00:00", "10:00:00", weight=1,
              done=True, detail="made ahead Sunday · reheat · 8:00", meta="made ahead Sunday · reheat", entry_id=11,
              action={"label": "Mark eaten", "target": {"kind": "check_meal", "entryId": 11}}),
        _move("cook", "cook:12", "Chopped Salad", "12:00:00", "14:00:00", weight=3,
              duration_min=0, detail="lunch · noon", meta="lunch", entry_id=12,
              action={"label": "Cook this", "target": {"tab": "kitchen", "cookFocus": {
                  "entryId": 12, "date": "2026-09-13", "slot": "lunch", "title": "Chopped Salad"}}}),
        _move("cook", "cook:13", "Chicken Skewers", "17:55:00", "20:30:00", weight=3,
              duration_min=35, detail="dinner · 35 min · 6:30", meta="35 min", entry_id=13,
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
    """(state, move id, tag) for every row, top to bottom."""
    return re.findall(
        r'<div class="day-node is-(done|now|later)" data-move-id="([^"]+)">.*?<span class="day-node-tag">([^<]*)</span></div>',
        strip,
    )


def _groups(strip: str) -> list[tuple[str, str, str]]:
    """(key, title, count) for every group card, top to bottom."""
    return re.findall(
        r'<div class="shell-card day-group day-group-(shop|cook)"><div class="day-group-head">'
        r'<span class="day-group-icon"><svg.*?</svg></span><span class="day-group-title">([^<]*)</span>'
        r'<span class="day-group-count">([^<]*)</span></div>',
        strip,
    )


def _row(strip: str, move_id: str, nth: int = 0) -> str:
    return strip.split('data-move-id="%s">' % move_id)[nth + 1].split('<div class="day-node is-', 1)[0]


# --------------------------------------------------------------------------
# The nodes and their states
# --------------------------------------------------------------------------

@_needs_node
def test_two_groups_shop_then_cook_and_the_states_read_off_done_and_featured():
    out = _node(_prelude() + "console.log(JSON.stringify(render(" + json.dumps(_payload("cook:13")) + ")));")
    strip = out["strip"]
    assert strip.startswith('<div class="day-groups">')
    assert _groups(strip) == [("shop", "Shop", "1 stop"), ("cook", "Cook", "1 of 4")]
    nodes = _nodes(strip)
    assert [n[1] for n in nodes] == ["shop:2026-09-13", "reheat:11", "cook:12", "cook:13", "fridge:4"], (
        "the shop in its card; then Cook top to bottom down the day: breakfast, lunch, dinner, the fridge move (by tonight)"
    )
    states = dict((n[1], n[0]) for n in nodes)
    assert states == {
        "shop:2026-09-13": "later", "reheat:11": "done", "cook:12": "later",
        "cook:13": "now", "fridge:4": "later",
    }
    assert out["sub"] == "1 of 5 done"
    # The head: the bag / the pot in a 32px sand tile (CSS), the title in
    # the display face, the count at the right.
    head = strip.split('<div class="day-group-head">', 1)[1].split("</div>", 1)[0]
    assert head.count("<svg") == 1 and 'stroke-width="2.2"' in head


@_needs_node
def test_the_shop_card_draws_one_row_per_store_stop_and_opens_the_list():
    moves = _day()
    moves[0]["stops"] = [
        {"store": "Costco", "count": 6, "items": ["orzo", "salmon", "black beans"]},
        {"store": "", "count": 2, "items": ["lemons", "dish soap"]},
    ]
    out = _node(_prelude() + "console.log(JSON.stringify(render(" + json.dumps(_payload("cook:13", moves)) + ")));")
    strip = out["strip"]
    assert _groups(strip)[0] == ("shop", "Shop", "2 stops")
    first = _row(strip, "shop:2026-09-13", 0)
    second = _row(strip, "shop:2026-09-13", 1)
    assert '<span class="day-node-title">Costco · 6 things</span><span class="day-node-meta">orzo, salmon, black beans…</span>' in first
    assert '<span class="day-node-title">Any store · 2 things</span><span class="day-node-meta">lemons, dish soap</span>' in second
    # The body opens the list (runTodayMoveAction → activateTab('grocery')),
    # not a button in the row; the shop's tick is a plain mark (not tickable).
    for row in (first, second):
        assert 'class="day-node-text day-node-open" data-move-action="shop:2026-09-13"' in row
        assert '<span class="day-tick" aria-hidden="true">' in row and "data-move-tick" not in row
    assert "Open the list" not in strip


@_needs_node
def test_a_group_with_nothing_in_it_is_not_drawn():
    moves = [m for m in _day() if m["kind"] != "shop"]
    out = _node(_prelude() + "console.log(JSON.stringify(render(" + json.dumps(_payload("cook:13", moves)) + ")));")
    assert _groups(out["strip"]) == [("cook", "Cook", "1 of 4")]
    only_shop = [m for m in _day() if m["kind"] == "shop"]
    out = _node(_prelude() + "console.log(JSON.stringify(render(" + json.dumps(_payload(None, only_shop)) + ")));")
    assert _groups(out["strip"]) == [("shop", "Shop", "1 stop")]


@_needs_node
def test_exactly_one_row_is_tinted_and_it_is_the_next_up_move():
    out = _node(_prelude() + "console.log(JSON.stringify(render(" + json.dumps(_payload("cook:13")) + ")));")
    strip = out["strip"]
    assert strip.count('"day-node is-now"') == 1, "§2b S3: one tinted row, ever, across both groups"
    assert strip.count("day-node-eyebrow") == 1
    row = _row(strip, "cook:13")
    assert '<button type="button" class="day-node-text day-node-open" data-move-action="cook:13">' in row
    assert '<span class="day-node-eyebrow">Now</span>' in row, "S6: the tint carries the word for it"
    assert '<span class="day-node-title">Chicken Skewers</span>' in row
    assert '<span class="day-node-meta">35 min</span>' in row
    # A later row has no eyebrow — title and meta only.
    later = _row(strip, "cook:12")
    assert "day-node-eyebrow" not in later
    assert '<button type="button" class="day-node-text day-node-open" data-move-action="cook:12">' in later
    # A featured shop with two stops tints its first row only.
    moves = _day()
    moves[0]["stops"].append({"store": "Loblaws", "count": 1, "items": ["milk"]})
    out = _node(_prelude() + "console.log(JSON.stringify(render(" + json.dumps(_payload("shop:2026-09-13", moves)) + ")));")
    assert out["strip"].count('"day-node is-now"') == 1
    assert [n[0] for n in _nodes(out["strip"])][:2] == ["now", "later"]


@_needs_node
def test_no_row_is_tinted_while_tonights_dinner_is_an_open_question():
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
    done = _row(strip, "reheat:11")
    assert '<button type="button" class="day-tick is-done" data-move-tick="reheat:11" aria-pressed="true" aria-label="Put it back on the list">' in done
    # Now and later: an unpressed tick.
    now = _row(strip, "cook:13")
    assert 'class="day-tick" data-move-tick="cook:13" aria-pressed="false" aria-label="Tick it off"' in now
    # Both glyphs are always in the dot; the state class picks one, so a
    # tick can settle from the kind's icon to the tick (animation 3).
    for row in (done, now):
        assert '<span class="day-dot"><span class="day-dot-icon"><svg' in row
        assert '<span class="day-dot-tick"><svg' in row
        # The tick comes first in the row, then the body, then the tag.
        assert row.index("day-tick") < row.index("day-node-text") < row.index("day-node-tag")
    # The shop has nothing behind its tick (moves.py's `tickable`): a plain
    # mark on the row, not a button.
    shop = _row(strip, "shop:2026-09-13")
    assert shop.startswith('<span class="day-tick" aria-hidden="true"><span class="day-dot">')
    assert "data-move-tick" not in shop and "<button" not in shop.split("</span></span></span>", 1)[0]


@_needs_node
def test_a_done_node_keeps_its_dish_name_as_a_link_and_nothing_else_tappable():
    moves = _day()
    moves[4]["done"] = True  # dinner cooked
    out = _node(_prelude() + "console.log(JSON.stringify(render(" + json.dumps(_payload(None, moves)) + ")));")
    node = _row(out["strip"], "cook:13")
    assert '<span class="day-node-text"><button type="button" class="day-node-title dish-link" data-move-dish="cook:13">Chicken Skewers</button>' in node
    assert "data-move-action" not in node
    # A done reheat has no recipe behind it, so its name is plain text.
    reheat = _row(out["strip"], "reheat:11")
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
    # Never "Open the list" (Emily, 2026-09-17): the shop's own action word.
    assert out["c"] == '<button type="button" class="dock-primary" data-move-action="shop:2026-09-13">Go shopping</button>'
    # And the tint moved with it.
    assert [n for n in _nodes(out["bStrip"]) if n[0] == "now"][0][1] == "fridge:4"
    # The row's body runs the same action as the dock (one thing, two
    # places to reach it), and never carries a button of its own.
    row = _row(out["bStrip"], "fridge:4")
    body = row.split('<button type="button" class="day-node-text day-node-open" data-move-action="fridge:4">', 1)[1].split("</button>", 1)[0]
    assert "dock-primary" not in body and "<button" not in body
    # A fridge move's reason IS its meta line — said once, not twice.
    assert body.count("for Thursday’s skewers") == 1
    assert '<span class="day-node-meta">for Thursday’s skewers</span>' in body


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
    assert _groups(strip) == [("cook", "Cook", "4 of 4")]
    assert strip.index("</div></div>") < strip.index('class="shell-card tomorrow-card"'), "the tomorrow card sits after the groups"
    assert 'data-move-dish="cook:20">Pancakes</button>' in strip
    assert out["dockHidden"] is True


# --------------------------------------------------------------------------
# Which part of the day, never a clock
# --------------------------------------------------------------------------

@_needs_node
def test_the_tag_says_which_part_of_the_day_and_no_row_prints_a_clock():
    out = _node(_prelude() + "console.log(JSON.stringify(render(" + json.dumps(_payload("cook:13")) + ")));")
    tags = dict((n[1], n[2]) for n in _nodes(out["strip"]))
    assert tags == {
        "shop:2026-09-13": "Evening",    # by the dinner cook's start, 5:55
        "reheat:11": "Morning",          # breakfast at 8:00
        "cook:12": "Afternoon",          # lunch on the table at noon
        "cook:13": "Evening",            # dinner: window_start 5:55 + 35 min = 6:30
        "fridge:4": "Evening",           # by tonight
    }
    text = re.sub(r"<[^>]+>", " ", out["strip"])
    assert not re.search(r"\b\d{1,2}:\d{2}\b", text), "a clock on a row — the tag says the part of the day now"
    assert "Noon" not in text and "Tonight" not in text


@_needs_node
def test_the_time_of_day_mapping_is_one_function_and_dinner_lands_in_evening():
    """moveTimeOfDay is the one mapping: before noon Morning, noon to five
    Afternoon, five on Evening; a shop nothing is waiting on is Any time;
    a fridge or prep move (by tonight) is Evening. Dinner is Evening for
    every dinner_window the household can pick (moves._dinner_clock: 5:30,
    7:00, 8:00; the 6:30 default) — asserted here so the rhythm answer and
    the tag can't drift apart."""
    script = _prelude() + (
        "function tag(o) { return moveTimeOfDay(o); }\n"
        "console.log(JSON.stringify({\n"
        "  early: tag({ kind: 'reheat', window_start: '2026-09-13T00:30:00' }),\n"
        "  lateMorning: tag({ kind: 'reheat', window_start: '2026-09-13T11:59:00' }),\n"
        "  noon: tag({ kind: 'cook', window_start: '2026-09-13T12:00:00', duration_min: 0 }),\n"
        "  four59: tag({ kind: 'reheat', window_start: '2026-09-13T16:59:00' }),\n"
        "  five: tag({ kind: 'reheat', window_start: '2026-09-13T17:00:00' }),\n"
        "  cookCrossesNoon: tag({ kind: 'cook', window_start: '2026-09-13T11:30:00', duration_min: 45 }),\n"
        "  dinners: ['17:30', '19:00', '20:00', '18:30'].map(function (t) {\n"
        "    return tag({ kind: 'cook', window_start: '2026-09-13T' + t + ':00', duration_min: 0 });\n"
        "  }),\n"
        "  prep: tag({ kind: 'prep', window_start: '2026-09-13T00:00:00', window_end: '2026-09-13T22:00:00' }),\n"
        "  fridge: tag({ kind: 'fridge', window_start: '2026-09-13T00:00:00', window_end: '2026-09-13T22:00:00', overdue: true }),\n"
        "  standingList: tag({ kind: 'shop', timed: false, window_start: '2026-09-13T00:00:00', window_end: '2026-09-13T23:59:00' }),\n"
        "  shopByBreakfast: tag({ kind: 'shop', timed: true, window_start: '2026-09-13T00:00:00', window_end: '2026-09-13T07:40:00' }),\n"
        "  shopByDinner: tag({ kind: 'shop', timed: true, window_start: '2026-09-13T00:00:00', window_end: '2026-09-13T17:55:00' }),\n"
        "  broken: tag({ kind: 'cook', window_start: 'nonsense', time_label: 'by tonight' }),\n"
        "  order: dayStripOrder([\n"
        "    { id: 'p', kind: 'prep', window_start: '2026-09-13T00:00:00', window_end: '2026-09-13T22:00:00' },\n"
        "    { id: 'd', kind: 'cook', window_start: '2026-09-13T18:00:00', duration_min: 30 },\n"
        "    { id: 'b', kind: 'reheat', window_start: '2026-09-13T08:00:00' }\n"
        "  ]).map(function (m) { return m.id; })\n"
        "}));"
    )
    out = _node(script)
    assert out["early"] == "Morning" and out["lateMorning"] == "Morning"
    assert out["noon"] == "Afternoon" and out["four59"] == "Afternoon"
    assert out["five"] == "Evening"
    assert out["cookCrossesNoon"] == "Afternoon", "a cook is tagged by when it lands, not when it starts"
    assert out["dinners"] == ["Evening"] * 4
    assert out["prep"] == "Evening" and out["fridge"] == "Evening"
    assert out["standingList"] == "Any time"
    assert out["shopByBreakfast"] == "Morning" and out["shopByDinner"] == "Evening"
    assert out["broken"] == "Any time", "an unreadable clock is no part of the day"
    assert out["order"] == ["b", "d", "p"]
    # The clock formatter is gone with the rail — nothing prints "6:30".
    assert "function moveStripTime(" not in SHELL_JS
    assert SHELL_JS.count("function moveTimeOfDay(") == 1


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
    start = SHELL_CSS.index("TODAY — Shop and Cook")
    start = SHELL_CSS.rindex("/* ====", 0, start)
    end = SHELL_CSS.index("/* Nothing left today: name tomorrow's first move", start)
    return SHELL_CSS[start:end]


def test_the_groups_css_is_one_banner_section_built_from_tokens():
    section = _strip_css()
    for rule in (".day-groups {", ".day-group {", ".day-group-head {", ".day-group-icon {", ".day-group-title {",
                 ".day-group-count {", ".day-node {", ".day-tick {", ".day-dot {", ".day-node-text {",
                 ".day-node-title {", ".day-node-meta {", ".day-node-tag {", ".day-node.is-now {",
                 ".day-node-eyebrow {"):
        assert rule in section, f"missing {rule}"
    body = re.sub(r"/\*.*?\*/", "", section, flags=re.S)
    assert not re.search(r"#[0-9a-fA-F]{3,8}\b", body), "a literal colour in the groups CSS (Rule 9)"
    # The head: a 32px sand tile, the title in the display face at 19px,
    # the count at the right.
    icon = section.split(".day-group-icon {", 1)[1].split("}", 1)[0]
    assert "width: 32px;" in icon and "height: 32px;" in icon and "background: var(--sand);" in icon
    title = section.split(".day-group-title {", 1)[1].split("}", 1)[0]
    assert "font-family: var(--font-display);" in title and "font-size: 19px;" in title
    assert "margin-left: auto;" in section.split(".day-group-count {", 1)[1].split("}", 1)[0]
    # The row: a flex row with a hairline between rows, none under the head.
    node = section.split(".day-node {", 1)[1].split("}", 1)[0]
    assert "display: flex;" in node and "border-top: 1.5px solid var(--hairline);" in node
    assert ".day-group-head + .day-node { border-top: 0; }" in section
    dot = section.split(".day-dot {", 1)[1].split("}", 1)[0]
    assert "width: 28px;" in dot and "height: 28px;" in dot
    assert "border: 1.5px solid var(--hairline-strong);" in dot
    assert "background: var(--surface);" in dot and "color: var(--ink-secondary);" in dot
    # The rail is gone with the clock.
    for dead in (".day-node-rail {", ".day-node-time {", ".day-node-line {", ".day-node-tile {", ".day-strip {"):
        assert dead not in SHELL_CSS, f"{dead} still has a rule — the groups replaced the strip"


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
    # The one tinted row (the mockup's .node.now): celadon-tint, a
    # celadon-edge border, the tile radius.
    tint = section.split(".day-node.is-now {", 1)[1].split("}", 1)[0]
    assert "background: var(--celadon-tint);" in tint
    assert "border: 1.5px solid var(--celadon-edge);" in tint
    assert "border-radius: var(--radius-tile);" in tint
    assert "color: var(--celadon-label);" in section.split(".day-node-eyebrow {", 1)[1].split("}", 1)[0]
    assert ".day-node.is-done .day-node-title { color: var(--ink-done); }" in section
    # Rule 6: the row is the tap target, 52px+; the tick is 44px around the dot.
    text = section.split(".day-node-text {", 1)[1].split("}", 1)[0]
    assert "min-height: 52px;" in text
    tick = section.split(".day-tick {", 1)[1].split("}", 1)[0]
    assert "width: 44px;" in tick and "height: 44px;" in tick
    # The title: 15px / 600. The tag: 10px / 800 / uppercase / --ink-muted.
    title = section.split(".day-node-title {", 1)[1].split("}", 1)[0]
    assert "font-size: 15px;" in title and "font-weight: 600;" in title
    tag = section.split(".day-node-tag {", 1)[1].split("}", 1)[0]
    assert "font-size: 10px;" in tag and "font-weight: 800;" in tag
    assert "text-transform: uppercase;" in tag and "color: var(--ink-muted);" in tag


def test_the_settle_is_animation_three_not_a_fourth():
    motion = SHELL_CSS[SHELL_CSS.index("   Motion (Emily, 2026-09-11)."):]
    assert "a ticked row on Today's Shop / Cook cards" in motion
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
    # .nextup-when stays: Plan's Meal step hero prints the day with it.
    assert ".nextup-when {" in SHELL_CSS and ".wk-meal-hero .nextup-when" in SHELL_CSS


def test_the_decision_log_has_the_entry():
    assert "2026-09-13 — Now is one strip down the day" in CLAUDE_MD
    assert "2026-09-17 — Today: Shop and Cook, tagged by part of the day" in CLAUDE_MD
