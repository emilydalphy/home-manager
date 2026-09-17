""""Just this week?" acts on the meal it was tapped under, not always dinner.

Loop Board bug, filed off the `overnight/is-anyone-out` walk. `presenceHtml`
has rendered the offer under all three meals since Build 4 (2026-09-11);
`offerToRemember` only ever acted on dinner — it asked `attendanceFor(d,
'dinner')` and then read `dayEl.querySelector('.presence-summary')` and
`dayEl.querySelector('.remember')`, which are THE FIRST IN THE DOM, i.e.
dinner's, because openDaySheet writes the dinner block first.

MEASURED IN CHROMIUM ON THE UNMODIFIED PAGE, before anything was changed —
the real page over a plain static server with canned /api answers, no app
and no database. Two failure modes, not one:

  * Only that meal touched (the ordinary case): tapping the offer under
    lunch or breakfast did NOTHING AT ALL. `attendanceFor(d, 'dinner')` is
    null on a day nobody has taken out of dinner, so the function returned
    at its first guard. The caption did not change, the button stayed where
    it was, nothing was saved, and the person was given no sign of it.
  * Dinner touched as well: the guard passed on dinner's row, and the tap
    under lunch wrote DINNER's caption and hid DINNER's offer. Reopening
    the sheet showed dinner remembered by a tap made under lunch, and lunch
    still asking. The card said "nothing happens"; this half is worse and
    is pinned below.

The front-end half runs the page's OWN functions and its OWN click wiring
under node, against a small DOM whose querySelector is a genuine
depth-first search — so "dinner wins" falls out of document order the way
it does in a browser rather than being asserted into the harness. A
source-marker test cannot see any of this: the bug was a selector's scope.
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from tests import nodeharness

REPO = Path(__file__).resolve().parents[1]
PAGE = (REPO / "static" / "plan-week.html").read_text(encoding="utf-8")

_needs_node = pytest.mark.skipif(
    shutil.which("node") is None, reason="node is needed to run the page's own functions"
)

SLOTS = ["dinner", "lunch", "breakfast"]
DATE = "2026-09-14"


def _extract(name: str) -> str:
    """One function, by brace matching — the slicer test_is_anyone_out.py and
    test_planning_exit.py already use on this page."""
    start = PAGE.index(f"function {name}(")
    i = PAGE.index("{", start)
    depth, j = 0, i
    while True:
        if PAGE[j] == "{":
            depth += 1
        elif PAGE[j] == "}":
            depth -= 1
            if depth == 0:
                break
        j += 1
    return PAGE[start:j + 1]


def _offer_wiring() -> str:
    """The REAL click wiring for the offer, lifted whole out of openDaySheet.

    Taken from the page rather than re-typed here: the defect was half in the
    wiring (it told offerToRemember nothing about which meal was tapped), so a
    harness that wires the button itself would be testing its own code.
    """
    sheet = _extract("openDaySheet")
    start = sheet.index("dayEl.querySelectorAll('.remember')")
    i = sheet.index(".forEach(", start) + len(".forEach(") - 1
    depth, j = 0, i
    while True:
        if sheet[j] == "(":
            depth += 1
        elif sheet[j] == ")":
            depth -= 1
            if depth == 0:
                break
        j += 1
    return sheet[start:j + 1] + ";"


# --------------------------------------------------------------------------
# The DOM the day sheet actually is
# --------------------------------------------------------------------------
# Hand-built rather than parsed, because node has no DOM and this repo has no
# jsdom. Two things keep it honest: querySelector is a real depth-first search
# returning the first match (so dinner wins on document order, not because the
# stub says so), and the structure it builds is pinned against the page by
# test_the_day_sheet_really_does_hold_three_of_each below.
_DOM = """
function matches(node, sel) {
  var m = /^\\.([A-Za-z0-9_-]+)(?:\\[data-([A-Za-z0-9-]+)="([^"]*)"\\])?$/.exec(sel);
  if (!m) throw new Error('the harness cannot read the selector ' + sel);
  if (!node.classList.contains(m[1])) return false;
  if (m[2] && node.dataset[m[2]] !== m[3]) return false;
  return true;
}
function descend(root, sel) {
  var parts = sel.trim().split(/\\s+/);
  var pool = [root];
  parts.forEach(function (part) {
    var next = [];
    pool.forEach(function (base) {
      (function walk(n) {
        n.children.forEach(function (kid) { if (matches(kid, part)) next.push(kid); walk(kid); });
      })(base);
    });
    pool = next;
  });
  return pool;
}
function makeEl(tag, cls, dataset) {
  var node = { tagName: tag, _cls: (cls || '').split(' ').filter(Boolean),
               dataset: dataset || {}, children: [], parent: null,
               hidden: false, textContent: '', title: '', attrs: {}, _click: [] };
  node.classList = {
    contains: function (c) { return node._cls.indexOf(c) !== -1; },
    toggle: function (c, on) {
      var i = node._cls.indexOf(c);
      if (on && i === -1) node._cls.push(c);
      if (!on && i !== -1) node._cls.splice(i, 1);
    }
  };
  node.setAttribute = function (k, v) { node.attrs[k] = v; };
  node.appendChild = function (kid) { kid.parent = node; node.children.push(kid); return kid; };
  node.closest = function (sel) {
    var n = node;
    while (n) { if (matches(n, sel)) return n; n = n.parent; }
    return null;
  };
  node.querySelector = function (sel) { return descend(node, sel)[0] || null; };
  node.querySelectorAll = function (sel) { return descend(node, sel); };
  node.addEventListener = function (type, fn) { if (type === 'click') node._click.push(fn); };
  node.click = function () { node._click.forEach(function (fn) { fn(); }); };
  return node;
}
// openDaySheet's own order: the dinner block is written first, then
// mealBlockHtml('lunch'), then mealBlockHtml('breakfast').
function buildDaySheet(slots) {
  var day = makeEl('div', 'day', { date: DATE });
  (slots || SLOTS).forEach(function (slot) {
    var block = day.appendChild(makeEl('div', 'meal-block', { slot: slot }));
    block.appendChild(makeEl('div', 'day-section-label presence-section-label'));
    var presence = block.appendChild(makeEl('div', 'presence', { slot: slot }));
    attendance.members.forEach(function (m) {
      presence.appendChild(makeEl('button', 'avatar', { member: m.name, slot: slot }));
    });
    block.appendChild(makeEl('div', 'presence-summary'));
    block.appendChild(makeEl('button', 'remember')).hidden = true;
    if (slot === 'dinner') block.appendChild(makeEl('div', 'ack'));
  });
  return day;
}
function readBlock(day, slot) {
  var b = day.querySelector('.meal-block[data-slot="' + slot + '"]');
  if (!b) return null;
  return { caption: b.querySelector('.presence-summary').textContent,
           offerHidden: b.querySelector('.remember').hidden };
}
function readAll(day) {
  var out = {};
  SLOTS.forEach(function (s) { out[s] = readBlock(day, s); });
  return out;
}
"""

_PAGE_FNS = (
    _extract("capitalize") + "\n"
    + _extract("joinNames") + "\n"
    + _extract("localSummary") + "\n"
    + _extract("attendanceFor") + "\n"
    + _extract("ensureLocalAttendance") + "\n"
    + _extract("applyLocalToggle") + "\n"
    + _extract("paintPresence") + "\n"
    + _extract("offerToRemember") + "\n"
)


def _harness(body: str, slots: list[str] | None = None) -> str:
    return (
        "var DATE = %s;\n" % json.dumps(DATE)
        + "var SLOTS = %s;\n" % json.dumps(SLOTS)
        + "var attendance = { members: [{id:1,name:'Emily'},{id:2,name:'Vineeth'},{id:3,name:'Reid'}],"
          " byDate: {} };\n"
        + _DOM
        + _PAGE_FNS
        + "var dayEl = buildDaySheet(%s);\n" % json.dumps(slots or SLOTS)
        + _offer_wiring() + "\n"
        + body
    )


def _node(script: str):
    res = nodeharness.run_node(script, timeout=30)
    assert res.returncode == 0, f"node failed: {res.stderr}"
    return json.loads(res.stdout.strip().splitlines()[-1])


def _tap(slot: str, also_out_of: list[str] | None = None) -> dict:
    """Take Vineeth out of `slot` (and out of anything in `also_out_of` first),
    then click that meal's own "Just this week?" through the page's own wiring.

    Returns what every block read before and after, plus what the page's own
    attendance store holds and what a REOPENED sheet would show — the sheet is
    rebuilt from scratch on open, so what survives is whatever was written.
    """
    before = ["applyLocalToggle(DATE, %s, 'Vineeth', false); paintPresence(dayEl, %s);"
              % (json.dumps(s), json.dumps(s)) for s in (also_out_of or [])]
    return _node(_harness(
        "\n".join(before) + "\n"
        + "applyLocalToggle(DATE, %s, 'Vineeth', false);\n" % json.dumps(slot)
        + "paintPresence(dayEl, %s);\n" % json.dumps(slot)
        + "var before = readAll(dayEl);\n"
        + "dayEl.querySelector('.meal-block[data-slot=\"%s\"]').querySelector('.remember').click();\n" % slot
        + "var after = readAll(dayEl);\n"
        # The round trip, twice over: the flag in the store, and what the
        # page's own painter makes of it on a freshly built sheet.
        + "var remembered = {}; SLOTS.forEach(function (s) {\n"
        + "  var a = attendanceFor(DATE, s); remembered[s] = a ? !!a.remembered : null; });\n"
        + "var reopened = buildDaySheet(SLOTS);\n"
        + "SLOTS.forEach(function (s) { paintPresence(reopened, s); });\n"
        + "console.log(JSON.stringify({ before: before, after: after,\n"
        + "  remembered: remembered, reopened: readAll(reopened) }));\n"
    ))


SENTENCE = "Just this week for now — ask me in chat to make it every week."


# --------------------------------------------------------------------------
# 1. The offer acts on the meal it was tapped under
# --------------------------------------------------------------------------

@_needs_node
@pytest.mark.parametrize("slot", SLOTS)
def test_the_offer_answers_the_meal_it_was_tapped_under(slot):
    """CATCH for lunch and breakfast, GUARD for dinner — a test per meal, so
    "works on dinner" can never again pass for "works".

    On the unmodified page the lunch and breakfast cases fail here: the
    caption never gains the sentence and the button never goes away, because
    `attendanceFor(d, 'dinner')` is null on a day only lunch was touched and
    the function returns at its first guard.
    """
    r = _tap(slot)
    assert SENTENCE in r["after"][slot]["caption"], f"{slot}'s own caption never said it"
    assert r["after"][slot]["caption"].startswith(r["before"][slot]["caption"]), \
        "the sentence is added to what was already there, not written over it"
    assert r["after"][slot]["offerHidden"] is True, f"{slot}'s offer is still asking"


@_needs_node
@pytest.mark.parametrize("slot", SLOTS)
def test_the_other_two_meals_do_not_move(slot):
    """CATCH — the half the card's report missed. With dinner also holding an
    attendance row, the unmodified page passed its guard on DINNER's row and
    wrote dinner's caption and hid dinner's offer from a tap made under lunch.
    Nothing but the meal that was tapped may change."""
    others = [s for s in SLOTS if s != slot]
    r = _tap(slot, also_out_of=others)
    for other in others:
        assert r["after"][other] == r["before"][other], \
            f"tapping {slot}'s offer moved {other}"


@_needs_node
@pytest.mark.parametrize("slot", SLOTS)
def test_what_is_saved_is_saved_against_that_meal(slot):
    """CATCH — the round trip, not the DOM. The standing rule this writes is
    `remembered` on that date's attendance row for that SLOT, and it is what
    paintPresence reads to decide whether to keep asking. Read back off the
    store, and off a freshly built sheet the page has repainted, which is what
    reopening the day sheet does."""
    others = [s for s in SLOTS if s != slot]
    r = _tap(slot, also_out_of=others)
    assert r["remembered"][slot] is True, f"nothing was recorded against {slot}"
    for other in others:
        assert r["remembered"][other] is False, f"{other} was remembered instead"
    # And the reopened sheet agrees: only that meal has stopped asking.
    assert r["reopened"][slot]["offerHidden"] is True
    for other in others:
        assert r["reopened"][other]["offerHidden"] is False, \
            f"{other} stopped asking after a tap under {slot}"


@_needs_node
def test_a_day_nobody_has_touched_for_dinner_is_the_ordinary_case():
    """CATCH, and the exact shape the bug was reported in: only lunch has an
    absence on it, so there is no dinner attendance row at all. On the
    unmodified page this is the early return — the tap did nothing whatsoever
    and said nothing about it."""
    r = _tap("lunch")
    assert r["remembered"] == {"dinner": None, "lunch": True, "breakfast": None}
    assert SENTENCE in r["after"]["lunch"]["caption"]
    assert r["after"]["dinner"] == r["before"]["dinner"]


# --------------------------------------------------------------------------
# 2. Both reads are scoped to the block, and there is no fallback to the day
# --------------------------------------------------------------------------

@_needs_node
def test_a_missing_block_is_a_tap_we_do_nothing_about():
    """CATCH, and the reason offerToRemember takes no `|| dayEl` fallback.

    paintPresence and toggleAvatar both write
    `dayEl.querySelector('.meal-block[data-slot="…"]') || dayEl`. That branch
    is dead for them — a block is missing only when the household has no
    members on record, and then the day carries no presence markup at all for
    the fallback to find — but here it would land on the dinner block and be
    this bug again. Pinned by building a sheet that has no lunch block and
    asking for lunch anyway: dinner must not move.
    """
    out = _node(_harness(
        "applyLocalToggle(DATE, 'dinner', 'Vineeth', false); paintPresence(dayEl, 'dinner');\n"
        "applyLocalToggle(DATE, 'lunch', 'Vineeth', false);\n"
        "var before = readBlock(dayEl, 'dinner');\n"
        "offerToRemember(dayEl, 'lunch');\n"
        "console.log(JSON.stringify({ before: before, after: readBlock(dayEl, 'dinner'),\n"
        "  dinnerRemembered: !!attendanceFor(DATE, 'dinner').remembered }));\n",
        slots=["dinner", "breakfast"],
    ))
    assert out["after"] == out["before"], "dinner took a tap meant for a block that isn't there"
    assert out["dinnerRemembered"] is False


@_needs_node
def test_no_slot_named_is_not_quietly_dinner():
    """CATCH — the old signature was `offerToRemember(dayEl)` and dinner was
    the answer. A caller that names no meal now changes nothing, rather than
    silently acting on the first block in the sheet."""
    out = _node(_harness(
        "applyLocalToggle(DATE, 'dinner', 'Vineeth', false); paintPresence(dayEl, 'dinner');\n"
        "var before = readAll(dayEl);\n"
        "offerToRemember(dayEl);\n"
        "console.log(JSON.stringify({ moved: JSON.stringify(readAll(dayEl)) !== JSON.stringify(before),\n"
        "  dinnerRemembered: !!attendanceFor(DATE, 'dinner').remembered }));\n"
    ))
    assert out["moved"] is False
    assert out["dinnerRemembered"] is False


def test_neither_read_is_made_on_the_day():
    """CATCH, and a SOURCE MARKER on top of the behaviour above, because this
    is the one-line shape that caused it: a `.presence-summary` or a
    `.remember` read off dayEl returns dinner's, three times out of three."""
    fn = _extract("offerToRemember")
    assert "dayEl.querySelector('.presence-summary')" not in fn
    assert "dayEl.querySelector('.remember')" not in fn
    assert "attendanceFor(d, 'dinner')" not in fn
    assert "block.querySelector('.presence-summary')" in fn
    assert "block.querySelector('.remember')" in fn
    # And nowhere else on the page either.
    assert "dayEl.querySelector('.presence-summary')" not in PAGE
    assert "dayEl.querySelector('.remember')" not in PAGE


def test_the_wiring_names_the_meal_that_was_tapped():
    """CATCH, and a SOURCE MARKER because the behaviour tests above run this
    wiring rather than describe it — this pins that it reads the block the way
    the guests steppers four lines below already do, rather than by a second
    route that could answer differently."""
    wiring = _offer_wiring()
    assert "btn.closest('.meal-block')" in wiring
    assert "dataset.slot" in wiring
    assert "offerToRemember(dayEl);" not in wiring
    # The neighbour it copies, unchanged.
    assert "var block = btn.closest('.meal-block');" in _extract("openDaySheet")


# --------------------------------------------------------------------------
# 3. The premise, and the sweep
# --------------------------------------------------------------------------

@_needs_node
def test_the_day_sheet_really_does_hold_three_of_each():
    """GUARD — the premise the whole bug rests on, and the thing that keeps
    the harness's hand-built tree honest: one day sheet renders three
    `.presence-summary` nodes and three `.remember` buttons, dinner's first.
    Measured in Chromium besides — three of each, plus three `.presence` rows
    and six initials for a household of two."""
    markup = _node(
        "var attendance = { members: [{id:1,name:'Emily'},{id:2,name:'Vineeth'}] };\n"
        "var PRESENCE_QUESTION = 'Is anyone out?';\n"
        "function stepperHtml(f, l) { return '<div class=\"stepper\" data-field=\"' + f + '\"></div>'; }\n"
        + _extract("esc") + "\n" + _extract("initialFor") + "\n"
        + _extract("presenceHtml") + "\n" + _extract("mealBlockHtml") + "\n"
        + "console.log(JSON.stringify(presenceHtml('dinner')"
          " + mealBlockHtml('lunch', 'Lunch') + mealBlockHtml('breakfast', 'Breakfast')));\n"
    )
    assert markup.count('class="presence-summary"') == 3
    assert markup.count('class="remember"') == 3
    sheet = _extract("openDaySheet")
    assert sheet.index("presence") < sheet.index("mealBlockHtml('lunch'") \
        < sheet.index("mealBlockHtml('breakfast'"), "dinner is written first, so it wins a day-wide read"


def test_nothing_else_in_the_day_sheet_reads_a_class_that_now_exists_three_times():
    """GUARD — the sweep the card asked for, as a rule rather than a list.

    Every `dayEl.querySelector`/`querySelectorAll` in the page either names a
    class that exists once in a day sheet, carries its own `[data-slot]`, or
    hands the node to a handler that reads the slot off it. Measured in a real
    day sheet with a holiday block open: `.tag` 10, `.ack` 3, `.avatar` 6,
    `.presence` 3, `.presence-summary` 3, `.remember` 3, `.guests` 1,
    `.guests-slot` 2.

    The two that are NOT scoped by a slot are named here with why:
      * `.tag:not(.holiday-answer)` — a night tag is a fact about the DAY
        (`answers.night_tags` is keyed by date alone), and the exclusion is
        what keeps the holiday block's own answers out of it.
      * `.avatar` and `.guests-slot .stepper button` — collected across all
        three blocks on purpose, and each handler reads the slot off the
        button it was given (`btn.dataset.slot`, `btn.closest('.meal-block')`).
      * `.guests` — one node, the dinner guest follow-up; `.guests-slot` is a
        different class and does not match it.
    """
    lines = [ln.strip() for ln in PAGE.splitlines()
             if "dayEl.querySelector" in ln]
    allowed_unscoped = {
        "dayEl.querySelectorAll('.tag:not(.holiday-answer)')",
        "dayEl.querySelectorAll('.avatar')",
        "dayEl.querySelectorAll('.remember')",
        "dayEl.querySelectorAll('.guests-slot .stepper button')",
        "dayEl.querySelector('.guests')",
    }
    for line in lines:
        if "[data-slot=" in line:
            continue
        assert any(ok in line for ok in allowed_unscoped), \
            f"a new day-wide read nobody has reasoned about: {line}"


def test_the_holiday_block_still_scopes_its_own_ack_and_tags():
    """GUARD — `.tag` and `.ack` each exist in the dinner block AND in the
    holiday block above it, so both are read off their own container. This is
    the same defect one screen over, already fixed; it must stay fixed."""
    paint_day = _extract("paintDay")
    assert "dayEl.querySelector('.meal-block[data-slot=\"dinner\"]')" in paint_day
    assert "dinner.querySelector('.ack')" in paint_day
    assert "dayEl.querySelector('.ack')" not in PAGE
    paint_holiday = _extract("paintHolidayBlock")
    assert "block.querySelector('.ack:not(.holiday-said)')" in paint_holiday
    assert "block.querySelector('.holiday-said')" in paint_holiday


def test_the_offer_is_still_a_44px_target_that_really_goes_away():
    """GUARD — hard rule 6, and the one CSS rule this fix leans on. Hiding the
    offer is `remember.hidden = true`, and `.remember` is `display:
    inline-flex`, which BEATS the user agent's own `[hidden]` rule — so
    without `.remember[hidden] { display: none; }` the button would stay on
    screen after a tap that had in fact worked. Nothing here moved; this is
    the promise that a behavioural fix stayed behavioural."""
    css = PAGE[PAGE.index("  .remember {"):PAGE.index("  .remember[hidden]") + 60]
    assert "min-height: 44px" in css
    assert "--apricot" not in css, "the day sheet's one apricot is the CTA (hard rule 5)"
    assert ".remember[hidden] { display: none; }" in PAGE
