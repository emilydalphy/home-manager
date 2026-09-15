"""
QA card, 2026-09-15: "Check the week: '+ nights' says there's no other
dinner when five nights are empty."

reviewAddDayOptions (static/shell.js) only ever offered a night that
already held a `planned` or `open` entry — a night with NO row at all (the
day card's own "Nothing yet") was skipped outright, same as a day the dish
already covers. A household with one cook and six empty dinners got "There
's no other dinner this week to put it on," which was false: six nights
were free, the picker just never looked at them.

Two halves, same as every other file in this batch:
  1. THE WRITE. tools.add_dish_day gets a `target_date` path alongside its
     existing `target_entry_id` one, for a target with no row to name by
     id. It resolves the (date, slot) fresh and applies exactly the same
     refusals (planned_empty, cooked, already-this-dish) a target_entry_id
     would get, so a night that filled in between the screen drawing and
     the tap is never silently overwritten.
  2. THE SCREEN'S OWN FUNCTIONS. reviewAddDayOptions now offers a slot with
     no entry, labelled "Nothing yet", and puts every such night ahead of
     the nights that already hold a meal (the card's own acceptance
     criterion) — proven under node against plain objects, not by grepping
     shell.js for the right words.
"""
from __future__ import annotations

import datetime
import json
import shutil

import nodeharness
from pathlib import Path

import pytest

from app import tools
from app.db import get_conn


TODAY = datetime.date.today()
WEEK_START = TODAY.isoformat()
D0 = WEEK_START
D1 = (TODAY + datetime.timedelta(days=1)).isoformat()
D2 = (TODAY + datetime.timedelta(days=2)).isoformat()
D3 = (TODAY + datetime.timedelta(days=3)).isoformat()
D4 = (TODAY + datetime.timedelta(days=4)).isoformat()
D5 = (TODAY + datetime.timedelta(days=5)).isoformat()
D6 = (TODAY + datetime.timedelta(days=6)).isoformat()

REPO = Path(__file__).resolve().parent.parent
SHELL_JS = (REPO / "static" / "shell.js").read_text(encoding="utf-8")


# ---------------------------------------------------------------- helpers

def _plan() -> int:
    return tools.create_weekly_plan(WEEK_START)["weekly_plan_id"]


def _ids(day: str, slot: str) -> list[int]:
    conn = get_conn()
    rows = conn.execute(
        "SELECT id FROM meal_plan_entries WHERE household_id = ? AND date = ? AND slot = ? "
        "ORDER BY id ASC", (tools.household_id(), day, slot),
    ).fetchall()
    conn.close()
    return [r["id"] for r in rows]


def _state(day: str, slot: str) -> list[tuple]:
    conn = get_conn()
    rows = conn.execute(
        "SELECT mpe.slot_state, COALESCE(r.name, mpe.freeform_meal) AS meal "
        "FROM meal_plan_entries mpe LEFT JOIN recipes r ON r.id = mpe.recipe_id "
        "WHERE mpe.household_id = ? AND mpe.date = ? AND mpe.slot = ? ORDER BY mpe.id ASC",
        (tools.household_id(), day, slot),
    ).fetchall()
    conn.close()
    return [(r["slot_state"], r["meal"]) for r in rows]


# --------------------------------------------------- THE WRITE (server side)

def test_a_genuinely_empty_night_has_no_row_to_begin_with():
    """The premise the rest of this file leans on: create_weekly_plan makes
    no meal_plan_entries rows at all, so a day nobody has planned into is
    not a stand-in row in some 'open' or 'empty' state — it is nothing."""
    _plan()
    assert _ids(D1, "dinner") == []


def test_linking_an_empty_night_creates_the_entry():
    """The heart of the card: stretching a roast over an empty Friday is
    one tap, and that tap has to actually write something."""
    plan = _plan()
    tools.plan_meal(D0, "Sunday Roast", slot="dinner", weekly_plan_id=plan)

    out = tools.add_dish_day(plan, _ids(D0, "dinner")[0], target_date=D3)

    assert out["status"] == "added"
    assert out["dish"] == "Sunday Roast"
    assert _state(D3, "dinner") == [("planned", "Sunday Roast")]
    # The night it was stretched FROM is untouched.
    assert _state(D0, "dinner") == [("planned", "Sunday Roast")]


def test_nothing_was_displaced_by_filling_a_blank():
    """An empty night is not a dish being lost — the toast must not invent
    one, the same guarantee an open slot already gets."""
    plan = _plan()
    tools.plan_meal(D0, "Sunday Roast", slot="dinner", weekly_plan_id=plan)
    out = tools.add_dish_day(plan, _ids(D0, "dinner")[0], target_date=D3)
    assert out["replaced"] is None
    assert out["unchained"] == []


def test_the_shape_is_the_same_the_id_path_already_hands_back():
    """get_week_menu's own day dict, so the Review screen can splice this
    one in exactly as it does the target_entry_id path."""
    plan = _plan()
    tools.plan_meal(D0, "Sunday Roast", slot="dinner", weekly_plan_id=plan)
    out = tools.add_dish_day(plan, _ids(D0, "dinner")[0], target_date=D3)
    assert out["day"]["date"] == D3
    assert out["day"]["dinner"]["title"] == "Sunday Roast"


def test_the_ingredients_follow_the_same_rule_an_approved_week_already_has():
    """No second implementation of "buy for the new meal" — this still
    composes swap_meal_in_plan/plan_meal, so an approved week's grocery
    list picks up the extra night exactly as it would for any other add."""
    plan = _plan()
    tools.add_recipe("Sunday Roast", ingredients=[{"item": "Chicken", "qty": "1"}])
    tools.plan_meal(D0, "Sunday Roast", slot="dinner", weekly_plan_id=plan)
    tools.approve_weekly_plan(plan, approved_by="Emily")

    tools.add_dish_day(plan, _ids(D0, "dinner")[0], target_date=D3)

    items = {i["item"] for i in tools.list_grocery_list()}
    assert "Chicken" in items


def test_a_night_nobody_is_home_is_never_a_day_to_plan_into_even_by_date():
    """The rule that has already caused three bugs when it was got wrong,
    checked from the OTHER side: a caller naming the night by date must be
    refused exactly like one naming it by id."""
    plan = _plan()
    tools.plan_meal(D0, "Sunday Roast", slot="dinner", weekly_plan_id=plan)
    tools.plan_slot_empty(plan, D3, "dinner", "You're out — I've planned nothing.")

    with pytest.raises(tools.SlotRefused):
        tools.add_dish_day(plan, _ids(D0, "dinner")[0], target_date=D3)
    assert _state(D3, "dinner") == [("planned_empty", None)]


def test_a_night_that_filled_in_since_the_screen_drew_it_is_not_overwritten():
    """The picker was drawn from a snapshot. If a chat swap or another tab
    planted a real dish on that night in the gap, tapping "Nothing yet"
    must not silently clobber it — same "already has it" refusal a stale
    target_entry_id tap already gets."""
    plan = _plan()
    tools.plan_meal(D0, "Sunday Roast", slot="dinner", weekly_plan_id=plan)
    tools.plan_meal(D3, "Sunday Roast", slot="dinner", weekly_plan_id=plan)

    with pytest.raises(tools.SlotRefused) as exc:
        tools.add_dish_day(plan, _ids(D0, "dinner")[0], target_date=D3)
    assert "already has it" in str(exc.value)
    assert _state(D3, "dinner") == [("planned", "Sunday Roast")]


def test_passing_neither_or_both_targets_is_refused():
    plan = _plan()
    tools.plan_meal(D0, "Sunday Roast", slot="dinner", weekly_plan_id=plan)
    entry = _ids(D0, "dinner")[0]
    with pytest.raises(ValueError):
        tools.add_dish_day(plan, entry)
    with pytest.raises(ValueError):
        tools.add_dish_day(plan, entry, target_entry_id=entry, target_date=D3)


def test_the_route_creates_the_entry_from_a_date_alone(signed_in):
    plan = _plan()
    tools.plan_meal(D0, "Sunday Roast", slot="dinner", weekly_plan_id=plan)

    res = signed_in.post(
        f"/api/week/{WEEK_START}/add-dish-day",
        json={"entry_id": _ids(D0, "dinner")[0], "target_date": D3},
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["status"] == "added"
    assert body["replaced"] is None
    assert body["day"]["dinner"]["title"] == "Sunday Roast"
    assert _state(D3, "dinner") == [("planned", "Sunday Roast")]


# ------------------------------------------- THE SCREEN'S OWN FUNCTIONS (JS)

_needs_node = pytest.mark.skipif(
    shutil.which("node") is None, reason="node is needed to execute the screen's own functions"
)


def _extract(name: str, source: str = SHELL_JS) -> str:
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


def _extract_var(name: str, source: str = SHELL_JS) -> str:
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


_SLOT_FURNITURE = (
    "var WEEK_SLOTS = ['breakfast', 'lunch', 'dinner'];\n"
    "var SLOT_LABELS = { breakfast: 'Breakfast', lunch: 'Lunch', dinner: 'Dinner' };\n"
    "function dayName(d, opts){ return { 'MON': 'Monday', 'TUE': 'Tuesday', 'WED':"
    " 'Wednesday', 'THU': 'Thursday', 'FRI': 'Friday', 'SAT': 'Saturday',"
    " 'SUN': 'Sunday' }[d] || d; }\n"
    + _extract("isSnackSlot") + "\n"
    + _extract("snackSlotKey") + "\n"
    + _extract("daySlotEntry") + "\n"
    + _extract("daySlotKeys") + "\n"
    + _extract("slotEyebrowLabel") + "\n"
)
_ESCAPE = _extract("escapeHtml") + "\n"


def _day(date, **slots):
    """Same shape get_week_menu hands the screen: a slot with nothing
    planned is None, not a stand-in object — that's the whole bug."""
    day = {"date": date, "before_plan_start": False, "isPast": False,
           "isToday": False, "snacks": slots.pop("snacks", [])}
    for s in ("breakfast", "lunch", "dinner"):
        day[s] = slots.get(s)
    day["snack"] = (day["snacks"] or [None])[0]
    return day


def _planned(title, entry_id=1):
    return {"title": title, "state": "planned", "source": "plan", "entry_id": entry_id}


_ROAST = {"slot": "dinner", "name": "Sunday Roast", "cooks": 1,
          "days": [{"date": "MON", "entryId": 1}]}


def _options(days, dish=_ROAST):
    harness = (
        _ESCAPE + _SLOT_FURNITURE
        + _extract("mealDisplayName") + "\n"
        + _extract("reviewAddDayOptions") + "\n"
        + f"console.log(JSON.stringify(reviewAddDayOptions({json.dumps(days)},"
          f" {json.dumps(dish)})));\n"
    )
    return _run_node(harness)


@_needs_node
def test_six_empty_dinners_are_six_options_not_zero():
    """The scenario the card names outright: one cook, six empty nights."""
    days = [_day("MON", dinner=_planned("Sunday Roast", entry_id=1))]
    days += [_day(d) for d in ["TUE", "WED", "THU", "FRI", "SAT", "SUN"]]
    out = _options(days)
    assert len(out) == 6, out
    assert all(o["holding"] == "Nothing yet" for o in out), out
    assert all(o["entryId"] is None for o in out), out


@_needs_node
def test_an_empty_night_is_labelled_nothing_yet_and_flagged_empty():
    days = [_day("MON", dinner=_planned("Sunday Roast", entry_id=1)), _day("TUE")]
    out = _options(days)
    assert out[0]["date"] == "TUE"
    assert out[0]["holding"] == "Nothing yet"
    assert out[0]["empty"] is True
    assert out[0]["entryId"] is None


@_needs_node
def test_empty_nights_are_offered_ahead_of_nights_that_already_hold_a_meal():
    """The card's own acceptance criterion, word for word: empty nights
    first, occupied ones after — a household stretching a roast should not
    have to scroll past Tuesday's chili to find Friday's blank."""
    days = [
        _day("MON", dinner=_planned("Sunday Roast", entry_id=1)),
        _day("TUE", dinner=_planned("Bean Chili", entry_id=2)),
        _day("WED"),
        _day("THU", dinner=_planned("Fish Tacos", entry_id=3)),
        _day("FRI"),
    ]
    out = _options(days)
    assert [o["date"] for o in out] == ["WED", "FRI", "TUE", "THU"], out
    assert [o["empty"] for o in out] == [True, True, False, False], out


@_needs_node
def test_a_planned_empty_night_is_still_never_offered_as_a_free_plate():
    """NO-REGRESSION GUARD: 'nobody's home' must not be confused with
    'nothing planned'. Only a slot with NO row at all is the new inclusion;
    a real planned_empty row stays excluded exactly as before."""
    days = [
        _day("MON", dinner=_planned("Sunday Roast", entry_id=1)),
        _day("TUE", dinner={"title": "Out — nothing to cook", "state": "planned_empty",
                             "source": "empty", "entry_id": 9}),
        _day("WED"),
    ]
    out = _options(days)
    assert [o["date"] for o in out] == ["WED"], out


@_needs_node
def test_the_no_other_night_line_is_gone_once_any_night_is_empty():
    """The line this card is named for: it must say so only when every
    other day is genuinely unavailable, and an empty night makes that
    false."""
    harness = (
        _ESCAPE + _SLOT_FURNITURE
        + _extract_var("REVIEW_SLOT_NOUNS") + "\n"
        + _extract("mealDisplayName") + "\n"
        + _extract("reviewSlotNoun") + "\n"
        + _extract("reviewAddDayOptions") + "\n"
        + _extract("reviewAddPickerHtml") + "\n"
        + "var days = " + json.dumps([
            _day("MON", dinner=_planned("Sunday Roast", entry_id=1)),
            _day("TUE"),
        ]) + ";\n"
        + f"console.log(JSON.stringify(reviewAddPickerHtml({json.dumps(_ROAST)}, 0, days)));\n"
    )
    html = _run_node(harness)
    assert "no other" not in html
    assert "Nothing yet" in html
    assert "data-rv-pick=" in html


@_needs_node
def test_the_no_other_night_line_still_appears_with_no_empty_nights_either():
    """The other half of the same guard: the line must still show up when
    it's actually true — a week where every other night is planned_empty,
    cooked, or already this dish has nowhere left to offer."""
    harness = (
        _ESCAPE + _SLOT_FURNITURE
        + _extract_var("REVIEW_SLOT_NOUNS") + "\n"
        + _extract("mealDisplayName") + "\n"
        + _extract("reviewSlotNoun") + "\n"
        + _extract("reviewAddDayOptions") + "\n"
        + _extract("reviewAddPickerHtml") + "\n"
        + "var days = " + json.dumps([
            _day("MON", dinner=_planned("Sunday Roast", entry_id=1)),
            _day("TUE", dinner={"title": "Out — nothing to cook", "state": "planned_empty",
                                 "source": "empty", "entry_id": 9}),
        ]) + ";\n"
        + f"console.log(JSON.stringify(reviewAddPickerHtml({json.dumps(_ROAST)}, 0, days)));\n"
    )
    html = _run_node(harness)
    assert "no other night" in html
    assert "data-rv-pick=" not in html


@_needs_node
def test_tapping_an_empty_option_posts_a_target_date_not_an_entry_id():
    """The client side of the write: an empty option has no entryId, so the
    POST has to carry the date instead — see tools.add_dish_day's
    target_date path."""
    days = [_day("MON", dinner=_planned("Sunday Roast", entry_id=1)), _day("TUE")]
    harness = (
        _ESCAPE + _SLOT_FURNITURE
        + "var calls = [];\n"
        + "var SWAP_TROUBLE = 'That didn’t work just now — nothing changed.';\n"
        + "var reviewState = { view: 'eating', busy: null, trouble: '', picking: 0,\n"
          "  dishes: [{ slot: 'dinner', name: 'Sunday Roast', cooks: 1,\n"
          "    days: [{date:'MON',entryId:1}] }] };\n"
        + "var weekState = { data: { week_start_date: '" + WEEK_START + "', status: 'draft' },"
          " days: " + json.dumps(days) + " };\n"
        + "function spliceSwappedDay(d){ calls.push('splice'); }\n"
          "function renderMealsStep(){ calls.push('render'); }\n"
          "async function loadWeekMenu(){ calls.push('loadWeekMenu'); }\n"
          "function showToast(t){ calls.push('toast:' + t); }\n"
          "function refreshGrocerySurfaces(){ calls.push('grocery'); }\n"
          "function slotWord(s){ return s; }\n"
          "var fetchBody = null;\n"
          "async function fetch(url, opts){ fetchBody = JSON.parse(opts.body);\n"
          "  calls.push('fetch:' + url);\n"
          "  return { ok: true, json: async () => ({ status: 'added', date: 'TUE',"
          "    slot: 'dinner', dish: 'Sunday Roast', replaced: null, unchained: [],"
          "    day: { date: 'TUE' } }) }; }\n"
        + _extract("mealDisplayName") + "\n"
        + _extract("reviewAddDayOptions") + "\n"
        + _extract("addDishToastText") + "\n"
        + ("async " + _extract("runAddDishDay")) + "\n"
        + "runAddDishDay(null, 0, 0).then(function () {\n"
          "  console.log(JSON.stringify({ body: fetchBody })); });\n"
    )
    out = _run_node(harness)
    assert out["body"] == {"entry_id": 1, "target_date": "TUE"}
