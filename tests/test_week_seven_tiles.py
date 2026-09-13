"""
Plan › Which days as seven tiles (Emily, 2026-09-12 — picked from the
"Beyond lists" canvas, artboard "Week · A · Seven tiles").

One tile a night: the date, the dinner, a bar for how long it takes, and a
handle to drag a night onto another — which trades the two DINNERS through
POST /api/week/{week}/swap-nights (tests/test_swap_dinner_nights.py covers
the write). These tests run the screen's own functions under node, the
house standard (see tests/test_review_two_views.py for why): the bar's
arithmetic, which nights get a handle, what the eyebrow says, and the
optimistic move — trade on the drop, tell the server, put back on a no,
offer Undo on a yes.
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
    shutil.which("node") is None, reason="node is needed to execute the screen's own functions"
)


def _extract(name: str, source: str) -> str:
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


def _extract_async(name: str, source: str) -> str:
    return "async " + _extract(name, source)


def _extract_var(name: str, source: str) -> str:
    start = source.index(f"var {name} = ")
    depth, quote, j = 0, "", source.index("=", start) + 1
    while True:
        c = source[j]
        if quote:
            if c == "\\":
                j += 2
                continue
            if c == quote:
                quote = ""
        elif c in "\"'":
            quote = c
        elif c in "([{":
            depth += 1
        elif c in ")]}":
            depth -= 1
        elif c == ";" and depth == 0:
            break
        j += 1
    return source[start : j + 1]


def _run_node(harness: str):
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
        + _DAYNAME_STUB
        + "var reviewState = { view: 'days', nightMove: null, focusHandle: null, busy: null,"
        " trouble: '', troubleFor: null, picking: null };\n"
        + "var weekState = { days: [], data: null };\n"
        + _extract("mealDisplayName", SHELL_JS) + "\n"
        + _extract("awayLineFor", SHELL_JS) + "\n"
        + _extract("joinList", SHELL_JS) + "\n"
        + _extract("isSnackSlot", SHELL_JS) + "\n"
        + _extract("snackSlotKey", SHELL_JS) + "\n"
        + _extract("daySlotEntry", SHELL_JS) + "\n"
        + _extract("daySlotKeys", SHELL_JS) + "\n"
        + _extract("reviewDayIsClosed", SHELL_JS) + "\n"
        + _extract("reviewClosedLine", SHELL_JS) + "\n"
        + _extract("reviewDayFaceLine", SHELL_JS) + "\n"
        + _extract_var("RV_BAR_FULL_MIN", SHELL_JS) + "\n"
        + _extract_var("RV_BAR_LONG_MIN", SHELL_JS) + "\n"
        + _extract_var("RV_GRIP_SVG", SHELL_JS) + "\n"
        + _extract("reviewDinnerMinutes", SHELL_JS) + "\n"
        + _extract("reviewTileTimeHtml", SHELL_JS) + "\n"
        + _extract("reviewTileTags", SHELL_JS) + "\n"
        + _extract("reviewTileIsMovable", SHELL_JS) + "\n"
        + _extract("reviewDayTileHtml", SHELL_JS) + "\n"
        + _extract("reviewDaysHtml", SHELL_JS) + "\n"
    )


def _tile(day: dict) -> str:
    return _run_node(_prelude() + f"console.log(JSON.stringify(reviewDayTileHtml({json.dumps(day)}, 0)));\n")


def _days_html(days: list) -> str:
    return _run_node(_prelude() + f"console.log(JSON.stringify(reviewDaysHtml({json.dumps(days)})));\n")


def _cooked(title: str, minutes=35, **kw) -> dict:
    out = {"state": "planned", "title": title, "source": "plan",
           "meta": f"{minutes} min" if minutes else None, "entry_id": 1}
    out.update(kw)
    return out


def _day(date: str, **slots) -> dict:
    out = {"date": date, "isToday": False, "isPast": False,
           "breakfast": None, "lunch": None, "dinner": None, "snacks": []}
    out.update(slots)
    out["snack"] = (out.get("snacks") or [None])[0]
    return out


MON, TUE, WED = "2026-09-14", "2026-09-15", "2026-09-16"


# ------------------------------------------------------------- the tiles

@_needs_node
def test_seven_days_are_seven_tiles_under_one_head():
    days = [_day(f"2026-09-{d}", dinner=_cooked("Beef Chili")) for d in range(14, 21)]
    html = _days_html(days)
    assert html.count('role="listitem"') == 7
    assert html.count("data-rv-handle=") == 7
    assert "Dinners · drag to move a night" in html
    assert "Bar = how long" in html
    # The live region the moves announce into sits in shell.html, outside
    # the panel that re-renders on every move.
    shell_html = (REPO / "static" / "shell.html").read_text(encoding="utf-8")
    assert 'id="rv-tiles-live" class="rv-tiles-live" aria-live="polite"' in shell_html


@_needs_node
def test_the_tile_reads_date_then_dinner_then_how_long():
    html = _tile(_day(MON, dinner=_cooked("Beef Chili", minutes=30)))
    assert '<span class="rv-tile-dow">Mon</span>' in html
    assert '<span class="rv-tile-num">14</span>' in html
    assert '<span class="rv-tile-name">Beef Chili</span>' in html
    assert "30 min" in html


@_needs_node
def test_the_bar_is_minutes_over_ninety_five_floored_and_capped():
    """width = minutes/95, min 18%, max 150px — the artboard's numbers."""
    html = _tile(_day(MON, dinner=_cooked("Quick Eggs", minutes=19)))
    assert "width:min(150px, max(18%, 20%))" in html
    html = _tile(_day(MON, dinner=_cooked("Braise", minutes=95)))
    assert "width:min(150px, max(18%, 100%))" in html


@_needs_node
def test_the_bar_is_apricot_from_fifty_minutes_and_celadon_under():
    assert 'class="rv-tile-bar is-long"' in _tile(_day(MON, dinner=_cooked("Braise", minutes=50)))
    assert 'class="rv-tile-bar"' in _tile(_day(MON, dinner=_cooked("Stir Fry", minutes=49)))


@_needs_node
def test_a_night_with_nothing_to_cook_gets_the_word_not_a_bar():
    reheat = {"state": "planned", "title": "Made ahead — Sunday’s Beef Chili",
              "source": "leftovers", "meta": "reheat", "entry_id": 2,
              "leftover_from": {"date": "2026-09-13", "meal": "Beef Chili", "cook_ahead": True}}
    html = _tile(_day(MON, dinner=reheat))
    assert "rv-tile-bar" not in html
    assert '<span class="rv-tile-min">made ahead</span>' in html
    assert ">Beef Chili<" in html


@_needs_node
def test_today_is_marked_and_says_so():
    """§2b S6: the tint carries its word."""
    html = _tile(_day(MON, isToday=True, dinner=_cooked("Beef Chili")))
    assert 'class="rv-tile is-today"' in html
    assert '<span class="rv-tile-today">Today</span>' in html
    assert "rv-tile-today" not in _tile(_day(MON, dinner=_cooked("Beef Chili")))


@_needs_node
def test_a_real_life_tag_says_why_the_night_is_different():
    out = _tile(_day(MON, dinner=_cooked("Beef Chili", away_names=["Emily"], present_names=["Sam"])))
    assert '<span class="rv-tile-tag">Emily out</span>' in out
    hosting = _tile(_day(MON, dinner=_cooked("Beef Chili", guest_count=2, serves=5, present_names=["Emily", "Sam", "Jo"])))
    assert '<span class="rv-tile-tag">Hosting · 5</span>' in hosting
    holiday = _tile(_day(MON, dinner=_cooked("Beef Chili"), holiday={"name": "Thanksgiving", "label": "Thanksgiving"}))
    assert '<span class="rv-tile-tag">Thanksgiving</span>' in holiday
    plain = _tile(_day(MON, dinner=_cooked("Beef Chili", present_names=["Emily", "Sam"])))
    assert "rv-tile-tag" not in plain


@_needs_node
def test_a_night_nobody_is_home_and_a_cooked_night_have_no_handle():
    away = {"state": "planned_empty", "title": "Out — nothing to cook", "need": "away", "entry_id": 3}
    closed = _tile(_day(MON, breakfast=dict(away), lunch=dict(away), dinner=dict(away)))
    assert "is-closed" in closed and "data-rv-handle=" not in closed
    assert "Away — nothing planned, nothing bought." in closed
    cooked = _tile(_day(MON, dinner=_cooked("Beef Chili", cooked=True)))
    assert "data-rv-handle=" not in cooked
    # ...and the space is kept so the grid does not jump.
    assert 'class="rv-tile-handle is-off"' in cooked


@_needs_node
def test_the_handle_is_a_focusable_button_that_says_what_the_arrows_do():
    html = _tile(_day(WED, dinner=_cooked("Beef Chili")))
    assert '<button type="button" class="rv-tile-handle" data-rv-handle="2026-09-16"' in html
    assert "Move Wednesday’s dinner — arrow up or down" in html
    assert "<svg" in html and "stroke=" in html


@_needs_node
def test_the_clash_is_one_red_word_on_its_night():
    harness = _prelude().replace(
        "var weekState = { days: [], data: null };",
        "var weekState = { days: [], data: { settle: { note: 'x', date: '2026-09-14', meal: 'Beef Chili', member: 'Emily' } } };",
    ) + f"console.log(JSON.stringify(reviewDayTileHtml({json.dumps(_day(MON, dinner=_cooked('Beef Chili')))}, 0)));\n"
    html = _run_node(harness)
    assert '<span class="rv-day-clash">not for Emily</span>' in html


# --------------------------------------------- the move, run rather than read

def _run_move(response, ok: bool = True, undo_response=None, tap_undo: bool = False) -> dict:
    """runSwapNights against stubs: what it fetched, what it rendered, what
    it said, and where the two dinners ended up."""
    days = [
        _day(MON, dinner=_cooked("Beef Chili", entry_id=1)),
        _day(TUE, dinner=_cooked("Chicken Traybake", entry_id=2)),
        _day(WED, dinner=None),
    ]
    harness = (
        "var calls = [];\n"
        "var toasts = [];\n"
        "var said = [];\n"
        "var undoAction = null;\n"
        "var reviewState = { view: 'days', nightMove: null, focusHandle: null };\n"
        "var weekState = { data: { week_start_date: '2026-09-14', status: 'draft' }, days: "
        + json.dumps(days) + " };\n"
        "var SWAP_TROUBLE = 'That didn’t work just now — nothing changed.';\n"
        "var SWAP_UNDO_MS = 8000;\n"
        "var WEEK_SLOTS = ['breakfast', 'lunch', 'dinner'];\n"
        "function todayLocalStr(){ return '2026-09-10'; }\n"
        "function classifyDay(day, t){ return { isToday: day.date === t, isPast: day.date < t }; }\n"
        "function spliceSwappedDay(d){ calls.push('splice:' + d.date); }\n"
        "function renderMealsStep(){ calls.push('render:' + JSON.stringify(weekState.days.map(function (d) {"
        " return d.dinner ? d.dinner.title : null; })) + ':' + (reviewState.nightMove ? 'busy' : 'idle')); }\n"
        "async function loadWeekMenu(){ calls.push('loadWeekMenu'); }\n"
        "function refreshTodayMoves(){ calls.push('today'); }\n"
        "function showToast(t, action, hold){ toasts.push(t); if (action) undoAction = action; }\n"
        "function reviewTileAnnounce(t){ said.push(t); }\n"
        "function weekStartForSwap(){ return weekState.data.week_start_date; }\n"
        + _DAYNAME_STUB
        + "var bodies = [];\n"
        "async function fetch(url, opts){ bodies.push(JSON.parse(opts.body)); calls.push('fetch:' + url);\n"
        "  var isUndo = url.indexOf('undo') !== -1;\n"
        "  return { ok: " + ("true" if ok else "false") + ", status: " + ("200" if ok else "500") + ","
        " json: async () => (isUndo ? " + json.dumps(undo_response or {}) + " : " + json.dumps(response) + ") }; }\n"
        + _extract("reviewDayByDate", SHELL_JS) + "\n"
        + _extract("reviewSwapDaysInState", SHELL_JS) + "\n"
        + _extract("reviewNightMoveSentence", SHELL_JS) + "\n"
        + _extract_async("runSwapNights", SHELL_JS) + "\n"
        + _extract_async("runSwapNightsUndo", SHELL_JS) + "\n"
        "runSwapNights(null, '2026-09-14', '2026-09-15').then(async function () {\n"
        + ("  if (undoAction) await undoAction.onClick();\n" if tap_undo else "")
        + "  console.log(JSON.stringify({ calls: calls, bodies: bodies, toasts: toasts, said: said,\n"
        "    undo: !!undoAction, dinners: weekState.days.map(function (d) { return d.dinner ? d.dinner.title : null; }),\n"
        "    busy: reviewState.nightMove })); });\n"
    )
    return _run_node(harness)


_SWAPPED = {
    "status": "swapped", "date_a": MON, "date_b": TUE,
    "moved": [{"entry_id": 1, "meal": "Beef Chili", "from": MON, "to": TUE},
              {"entry_id": 2, "meal": "Chicken Traybake", "from": TUE, "to": MON}],
    "days": [{"date": MON}, {"date": TUE}], "can_undo": True,
}


@_needs_node
def test_the_move_trades_the_dinners_before_the_server_answers():
    """§6: you change something → the screen updates on tap. The first
    render already shows the trade, with the list marked busy."""
    out = _run_move(_SWAPPED)
    assert out["calls"][0] == 'render:["Chicken Traybake","Beef Chili",null]:busy'
    assert out["calls"][1] == "fetch:/api/week/2026-09-14/swap-nights"
    assert out["bodies"][0] == {"date_a": MON, "date_b": TUE}


@_needs_node
def test_a_success_splices_both_days_reloads_the_week_and_offers_undo():
    out = _run_move(_SWAPPED)
    assert "splice:2026-09-14" in out["calls"] and "splice:2026-09-15" in out["calls"]
    assert "loadWeekMenu" in out["calls"] and "today" in out["calls"]
    assert out["undo"] is True
    assert out["toasts"] == ["Beef Chili is on Tuesday now, and Chicken Traybake on Monday."]
    assert out["said"] == out["toasts"]
    assert out["busy"] is None
    assert out["dinners"] == ["Chicken Traybake", "Beef Chili", None]


@_needs_node
def test_a_refusal_puts_the_dinners_back_and_says_the_servers_sentence():
    refused = {"status": "refused", "date_a": MON, "date_b": TUE,
               "message": "Beef Chili on Monday has already been cooked — I’ll leave that one where it is."}
    out = _run_move(refused)
    assert out["dinners"] == ["Beef Chili", "Chicken Traybake", None]
    assert out["toasts"] == [refused["message"]]
    assert out["said"] == [refused["message"]]
    assert out["undo"] is False
    assert "loadWeekMenu" not in out["calls"]
    assert out["busy"] is None


@_needs_node
def test_a_failed_call_puts_the_dinners_back_calmly():
    out = _run_move({}, ok=False)
    assert out["dinners"] == ["Beef Chili", "Chicken Traybake", None]
    assert out["toasts"] == ["That didn’t work just now — nothing changed."]
    assert out["busy"] is None


@_needs_node
def test_undo_trades_them_back_through_its_own_route():
    restored = dict(_SWAPPED, status="restored", can_undo=False)
    out = _run_move(_SWAPPED, undo_response=restored, tap_undo=True)
    assert out["calls"].count("fetch:/api/week/2026-09-14/swap-nights-undo") == 1
    assert out["bodies"][1] == {"date_a": MON, "date_b": TUE}
    assert out["dinners"] == ["Beef Chili", "Chicken Traybake", None]
    assert out["toasts"][-1] == "Put back."


@_needs_node
def test_a_move_onto_an_unplanned_night_names_only_the_dish_that_moved():
    one = {"status": "swapped", "date_a": MON, "date_b": WED,
           "moved": [{"entry_id": 1, "meal": "Beef Chili", "from": MON, "to": WED}],
           "days": [{"date": MON}, {"date": WED}], "can_undo": True}
    harness = _DAYNAME_STUB + _extract("reviewNightMoveSentence", SHELL_JS) + \
        f"\nconsole.log(JSON.stringify(reviewNightMoveSentence({json.dumps(one)})));\n"
    assert _run_node(harness) == "Beef Chili is on Wednesday now."


# ------------------------------------------------ the wiring and the styles

def test_the_keyboard_moves_a_night_and_the_focus_follows_the_dish():
    keys = _extract("reviewTileKeydown", SHELL_JS)
    assert "e.key !== 'ArrowUp' && e.key !== 'ArrowDown'" in keys
    assert "reviewState.focusHandle = to;" in keys
    assert "runSwapNights(panel, from, to, from);" in keys
    wire = _extract("wireReviewTiles", SHELL_JS)
    assert "if (next && document.activeElement !== next) next.focus();" in wire
    # ...and it comes back to the night it left when the move is refused.
    move = _extract_async("runSwapNights", SHELL_JS)
    assert "if (focusBack) reviewState.focusHandle = focusBack;" in move
    assert "'pointerdown'" in wire and "'keydown'" in wire


def test_touch_lifts_after_a_hold_and_a_mouse_lifts_at_once():
    down = _extract("reviewTilePointerDown", SHELL_JS)
    assert "RV_LIFT_MS" in down and "var RV_LIFT_MS = 250;" in SHELL_JS
    assert "if (e.pointerType === 'touch')" in down
    assert "setPointerCapture" in down and "'pointercancel'" in down
    # A swap previewed as a swap: the night under the finger slides into
    # the lifted night's home; nothing in between moves.
    assert "over.el.style.transform = 'translateY(' + (drag.home.top - over.top) + 'px)';" in down


def test_the_tiles_use_the_crossfades_own_motion_tokens_and_the_hero_shadow():
    tile = re.search(r"\.rv-tile \{(.*?)\}", SHELL_CSS, re.S).group(1)
    assert "transition: transform var(--motion-fast) var(--motion-ease);" in tile
    assert "grid-template-columns: 52px 1fr 44px;" in tile
    assert "min-height: 56px;" in tile
    assert "border-radius: var(--radius-tile);" in tile
    lifted = re.search(r"\.rv-tile\.is-lifted \{(.*?)\}", SHELL_CSS, re.S).group(1)
    assert "box-shadow: var(--shadow-hero);" in lifted
    today = re.search(r"\.rv-tile\.is-today \{(.*?)\}", SHELL_CSS, re.S).group(1)
    assert "background: var(--celadon-tint);" in today and "border-color: var(--celadon-edge);" in today


def test_the_handle_is_44px_and_every_colour_is_a_token():
    handle = re.search(r"\n\.rv-tile-handle \{(.*?)\}", SHELL_CSS, re.S).group(1)
    assert "width: 44px;" in handle and "height: 44px;" in handle
    assert "cursor: grab;" in handle and "touch-action: none;" in handle
    body = re.search(r"\.rv-tile-body \{(.*?)\}", SHELL_CSS, re.S).group(1)
    assert "min-height: 44px;" in body
    block = SHELL_CSS[SHELL_CSS.index("REVIEW: which days — seven tiles"):]
    block = block[: block.index('/* ---------- "One thing to settle"')]
    assert not re.search(r"#[0-9a-fA-F]{3,8}\b", block), "literal hex in the tile styles"
    assert "var(--apricot)" in block and "var(--celadon)" in block


def test_a_chat_move_refreshes_the_plan_panel():
    """The gotcha in CLAUDE.md: a panel built once and never told to
    refresh goes stale. swap_dinner_nights is tagged `week` on the backend
    (app/main.py _WEEK_TOOLS), and the `week` branch reloads the menu."""
    main_py = (REPO / "app" / "main.py").read_text(encoding="utf-8")
    assert '"swap_dinner_nights"' in main_py.split("_WEEK_TOOLS = ", 1)[1].split("\n", 1)[0]
    refresh = _extract("refreshStaleTabsFromActions", SHELL_JS)
    assert "if (action.tab === 'week' && panels.week && panels.week.dataset.built)" in refresh
    assert "loadWeekMenu(panels.week);" in refresh
