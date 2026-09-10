"""
Review a week, part 1: the two views (Emily's approved design, 2026-09-09,
Option A).

A fourth step of the Meals tab — "Check the week" — that reads one week two
ways rather than putting more of it on one screen. Julia's report was that a
week is too much to take in: three meals and two snacks across seven days is
35 things, and a list of all 35 is the problem rather than the answer.

  * WHAT WE'RE EATING  grouped by meal type, each dish ONCE with the number
    of days it covers, a stepper per row and a Change button.
  * WHICH DAYS         one card per day carrying dinner, expanding to all
    five, and a day nobody is home saying so and offering nothing.

Two kinds of test, and the split is the one tests/test_meals_week_day_meal.py
already draws:

  * BEHAVIOUR, for the one new write — taking a day away from a dish, which
    must never leave a slot absent — and for the route in front of it.
  * THE SCREEN'S OWN FUNCTIONS, RUN, under node against plain dicts. Not
    source markers: the whole risk in a "dish appears once with an accurate
    count" screen is the counting, and a test that greps shell.js for the
    right words cannot see a number at all.
"""
from __future__ import annotations

import datetime
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from app import tools
from app.db import get_conn


TODAY = datetime.date.today()
WEEK_START = (TODAY - datetime.timedelta(days=1)).isoformat()
D0 = WEEK_START
D1 = (TODAY + datetime.timedelta(days=0)).isoformat()
D2 = (TODAY + datetime.timedelta(days=1)).isoformat()

REPO = Path(__file__).resolve().parent.parent
SHELL_JS = (REPO / "static" / "shell.js").read_text(encoding="utf-8")
SHELL_CSS = (REPO / "static" / "shell.css").read_text(encoding="utf-8")


# ---------------------------------------------------------------- helpers

def _plan() -> int:
    return tools.create_weekly_plan(WEEK_START)["weekly_plan_id"]


def _entry_id(day: str, slot: str) -> int:
    conn = get_conn()
    row = conn.execute(
        "SELECT id FROM meal_plan_entries WHERE household_id = ? AND date = ? AND slot = ? "
        "ORDER BY id DESC",
        (tools.household_id(), day, slot),
    ).fetchone()
    conn.close()
    return row["id"]


def _snack_ids(day: str) -> list:
    conn = get_conn()
    rows = conn.execute(
        "SELECT id FROM meal_plan_entries WHERE household_id = ? AND date = ? AND slot = 'snack' "
        "ORDER BY id ASC", (tools.household_id(), day)).fetchall()
    conn.close()
    return [r["id"] for r in rows]


def _entry_id_asc(day: str, slot: str) -> int:
    conn = get_conn()
    row = conn.execute(
        "SELECT id FROM meal_plan_entries WHERE household_id = ? AND date = ? AND slot = ? "
        "ORDER BY id ASC", (tools.household_id(), day, slot)).fetchone()
    conn.close()
    return row["id"]


def _slot_state(day: str, slot: str) -> str:
    conn = get_conn()
    row = conn.execute(
        "SELECT slot_state FROM meal_plan_entries WHERE household_id = ? AND date = ? AND slot = ?",
        (tools.household_id(), day, slot),
    ).fetchone()
    conn.close()
    return row["slot_state"] if row else ""


def _rows_on(day: str, slot: str) -> int:
    conn = get_conn()
    n = conn.execute(
        "SELECT COUNT(*) c FROM meal_plan_entries WHERE household_id = ? AND date = ? AND slot = ?",
        (tools.household_id(), day, slot),
    ).fetchone()["c"]
    conn.close()
    return n


# ------------------------------------------- the one new write: one fewer day

def test_taking_a_day_off_a_dish_leaves_the_slot_open_and_never_absent():
    """
    The whole hazard in a stepper that goes down. A slot is one of three
    states, never present-or-missing, and three separate bugs in this repo
    have come from a slot that stopped existing. So the day the household
    takes back is handed to them as a question, not deleted.
    """
    plan = _plan()
    tools.plan_meal(D1, "Chicken Traybake", slot="dinner", weekly_plan_id=plan)
    out = tools.drop_dish_from_day(plan, _entry_id(D1, "dinner"))

    assert out["status"] == "dropped"
    assert out["dish"] == "Chicken Traybake"
    assert _rows_on(D1, "dinner") == 1, "the slot must still hold exactly one row"
    assert _slot_state(D1, "dinner") == "open"


def test_the_open_slot_names_the_household_s_own_instruction_as_the_reason():
    """plan_slot_open refuses a reasonless open slot on purpose — the reason
    is what the Day step shows in place of a meal, and it has to read as the
    constraint that caused this, not as an apology."""
    plan = _plan()
    tools.plan_meal(D1, "Chicken Traybake", slot="dinner", weekly_plan_id=plan)
    out = tools.drop_dish_from_day(plan, _entry_id(D1, "dinner"))

    assert "Chicken Traybake" in out["open_reason"]
    assert "sorry" not in out["open_reason"].lower()
    day = [d for d in tools.get_week_menu(plan)["days"] if d["date"] == D1][0]
    assert day["dinner"]["state"] == "open"
    assert day["dinner"]["open_reason"] == out["open_reason"]


def test_it_hands_back_the_changed_day_in_the_shape_the_screen_already_reads():
    """get_week_menu's own day dict, exactly as the in-place swap answers —
    which is what lets the Review screen splice one day into the week it is
    holding and have BOTH views right with no refetch."""
    plan = _plan()
    tools.plan_meal(D1, "Chicken Traybake", slot="dinner", weekly_plan_id=plan)
    tools.plan_meal(D1, "Oatmeal", slot="breakfast", weekly_plan_id=plan)
    out = tools.drop_dish_from_day(plan, _entry_id(D1, "dinner"))

    assert out["day"]["date"] == D1
    assert out["day"]["dinner"]["state"] == "open"
    # The rest of the day comes back untouched, not just the slot that moved.
    assert out["day"]["breakfast"]["title"] == "Oatmeal"


def test_a_slot_with_nothing_on_it_is_not_something_to_take_a_day_off():
    plan = _plan()
    tools.plan_slot_empty(plan, D1, "dinner", "You're out — nothing planned.")
    with pytest.raises(ValueError):
        tools.drop_dish_from_day(plan, _entry_id(D1, "dinner"))
    # And the deliberate empty row is still there, untouched.
    assert _slot_state(D1, "dinner") == "planned_empty"


def test_an_entry_from_another_week_is_refused_rather_than_quietly_dropped():
    """Household- and plan-scoped both, the same rule the in-place swap
    follows — an id from somewhere else is a refusal, not an edit of
    somebody else's dinner."""
    plan_a = _plan()
    tools.plan_meal(D1, "Chicken Traybake", slot="dinner", weekly_plan_id=plan_a)
    entry = _entry_id(D1, "dinner")
    plan_b = tools.create_weekly_plan(
        (TODAY + datetime.timedelta(days=14)).isoformat()
    )["weekly_plan_id"]
    with pytest.raises(ValueError):
        tools.drop_dish_from_day(plan_b, entry)
    assert _slot_state(D1, "dinner") == "planned"


def test_taking_the_reheat_night_off_a_chain_leaves_the_cook_night_alone():
    """
    A dish cooked once and eaten twice is ONE dish covering two days, so its
    stepper goes 2 -> 1 by taking back the reheat. clear_plan_slot unlinks
    the chain on the way (that is its job, not this function's) — the point
    of the test is that the night somebody actually cooks survives.
    """
    plan = _plan()
    tools.plan_meal(D1, "Beef Chili", slot="dinner", weekly_plan_id=plan)
    tools.plan_meal(D2, "Leftovers from Beef Chili", slot="dinner", weekly_plan_id=plan)
    out = tools.drop_dish_from_day(plan, _entry_id(D2, "dinner"))

    assert out["date"] == D2
    assert _slot_state(D2, "dinner") == "open"
    assert _slot_state(D1, "dinner") == "planned"


def test_stepping_one_snack_down_leaves_the_day_s_OTHER_snack_alone():
    """
    BLOCKER, found in review and reproduced through the real route. A day
    holds TWO rows at slot='snack' by default, and the first version of this
    composed clear_plan_slot, which deletes every row in a slot — so one tap
    on the Apple row's minus destroyed the Greek yogurt beside it, grocery
    reversal and all, and left the day on one open slot when the household
    had asked for two snacks. swap_meal_in_plan's own docstring had already
    written the rule down; removal is by ID now, the way that function does
    it.
    """
    plan = _plan()
    tools.plan_meal(D1, "Apple and peanut butter", slot="snack", weekly_plan_id=plan)
    tools.plan_meal(D1, "Greek yogurt", slot="snack", weekly_plan_id=plan)
    apple = _snack_ids(D1)[0]

    tools.drop_dish_from_day(plan, apple)

    day = [d for d in tools.get_week_menu(plan)["days"] if d["date"] == D1][0]
    titles = [(s["title"], s["state"]) for s in day["snacks"]]
    # The day still has two snack slots: one real snack, one handed back.
    assert ("Greek yogurt", "planned") in titles, titles
    assert sum(1 for _, state in titles if state == "open") == 1, titles
    assert not any(t == "Apple and peanut butter" for t, _ in titles), titles


def test_a_dish_that_feeds_another_night_is_refused_rather_than_dropped():
    """
    Taking away a night that was cooked double leaves the night it fed
    holding a real recipe nobody planned to cook, with the doubled batch's
    groceries just reversed out from under it. _unlink_leftover_target
    covers the reverse direction only, so this refuses and names the night
    that depends on it — nothing written.
    """
    plan = _plan()
    tools.plan_meal(D1, "Beef Chili", slot="dinner", weekly_plan_id=plan)
    tools.plan_meal(D2, "Beef Chili", slot="dinner", weekly_plan_id=plan)
    source, target = _entry_id_asc(D1, "dinner"), _entry_id_asc(D2, "dinner")
    tools.set_cook_ahead(source, [target])

    out = tools.drop_dish_from_day(plan, source)

    assert out["status"] == "refused"
    assert "also feeds" in out["message"]
    assert _slot_state(D1, "dinner") == "planned", "nothing may be written on a refusal"
    assert _slot_state(D2, "dinner") == "planned"


def test_the_route_takes_one_day_off_a_dish(signed_in):
    plan = _plan()
    tools.plan_meal(D1, "Chicken Traybake", slot="dinner", weekly_plan_id=plan)
    res = signed_in.post(
        f"/api/week/{WEEK_START}/drop-dish-day",
        json={"entry_id": _entry_id(D1, "dinner")},
    )
    assert res.status_code == 200
    assert res.json()["status"] == "dropped"
    assert _slot_state(D1, "dinner") == "open"


def test_the_route_refuses_an_entry_that_is_not_on_this_household_s_plan(signed_in):
    plan = _plan()
    # The good request first, so a 404 on the second one is provably a
    # refusal and not simply a route that isn't there.
    tools.plan_meal(D1, "Chicken Traybake", slot="dinner", weekly_plan_id=plan)
    ok = signed_in.post(
        f"/api/week/{WEEK_START}/drop-dish-day",
        json={"entry_id": _entry_id(D1, "dinner")},
    )
    assert ok.status_code == 200
    res = signed_in.post(f"/api/week/{WEEK_START}/drop-dish-day", json={"entry_id": 999999})
    assert res.status_code == 404


# ------------------------------------------------- the screen's own functions

_needs_node = pytest.mark.skipif(
    shutil.which("node") is None, reason="node is needed to execute the screen's own functions"
)


def _extract(name: str, source: str) -> str:
    """Lift one brace-balanced `function name(...) {...}` out of the file."""
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


def _extract_var(name: str, source: str) -> str:
    """
    Lift one `var NAME = ...;` out of the file, brackets and quotes balanced.

    The screen's nouns, its icons and its two view labels are `var`s rather
    than functions, and a harness that retyped them would be testing its own
    copy — the very thing running the real functions exists to avoid.
    """
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


def _extract_async(name: str, source: str) -> str:
    """Same lift, for an `async function` — _extract starts at the word
    `function`, which would drop the `async` and make every `await` inside
    it a syntax error."""
    return "async " + _extract(name, source)


def _run_node(harness: str):
    res = subprocess.run(["node", "-e", harness], capture_output=True, text=True, timeout=30)
    assert res.returncode == 0, f"node failed: {res.stderr}"
    return json.loads(res.stdout.strip())


_ESCAPE = _extract("escapeHtml", SHELL_JS) + "\n"
_DAYNAME_STUB = (
    "function dayName(d, opts){ if (opts && opts.day) return '11';"
    " return 'Thursday'; }\n"
)
_WEEK_SLOTS = "var WEEK_SLOTS = ['breakfast', 'lunch', 'dinner'];\n"
_SLOT_LABELS = "var SLOT_LABELS = { breakfast: 'Breakfast', lunch: 'Lunch', dinner: 'Dinner' };\n"


def _eating_prelude() -> str:
    return (
        _ESCAPE + _WEEK_SLOTS
        + _extract_var("REVIEW_GROUP_LABELS", SHELL_JS) + "\n"
        + _extract_var("REVIEW_SLOT_NOUNS", SHELL_JS) + "\n"
        + _extract_var("RV_MINUS_SVG", SHELL_JS) + "\n"
        + _extract_var("RV_PLUS_SVG", SHELL_JS) + "\n"
        + "var reviewState = { view: 'eating', openDays: {}, busy: null, trouble: '' };\n"
        + _extract("mealDisplayName", SHELL_JS) + "\n"
        + _extract("reviewSlotNoun", SHELL_JS) + "\n"
        + _extract("reviewEatingGroups", SHELL_JS) + "\n"
        + _extract("reviewCookLine", SHELL_JS) + "\n"
        + _extract("reviewDishRowHtml", SHELL_JS) + "\n"
        + _extract("reviewEatingHtml", SHELL_JS) + "\n"
    )


def _days_prelude() -> str:
    return (
        _ESCAPE + _WEEK_SLOTS + _SLOT_LABELS + _DAYNAME_STUB
        + _extract_var("RV_CHEVRON_SVG", SHELL_JS) + "\n"
        + "var reviewState = { view: 'days', openDays: {}, busy: null, trouble: '' };\n"
        + _extract("mealDisplayName", SHELL_JS) + "\n"
        + _extract("awayLineFor", SHELL_JS) + "\n"
        + _extract("isSnackSlot", SHELL_JS) + "\n"
        + _extract("snackSlotKey", SHELL_JS) + "\n"
        + _extract("daySlotEntry", SHELL_JS) + "\n"
        + _extract("slotEyebrowLabel", SHELL_JS) + "\n"
        + _extract("reviewDayIsClosed", SHELL_JS) + "\n"
        + _extract("reviewClosedLine", SHELL_JS) + "\n"
        + _extract("reviewDayHasMore", SHELL_JS) + "\n"
        + _extract("reviewDayFaceLine", SHELL_JS) + "\n"
        + _extract("reviewDayNoteHtml", SHELL_JS) + "\n"
        + _extract("reviewSlotLineHtml", SHELL_JS) + "\n"
        # Was reviewDaySlotKeys, and it is the same function: it moved up
        # beside daySlotEntry and lost the review- prefix when the Approve
        # button's own open-slot count started asking it what a day is
        # actually made of (Emily, 2026-09-10 — an open snack is something
        # left to decide). Nothing about what it returns changed.
        + _extract("daySlotKeys", SHELL_JS) + "\n"
        + _extract("reviewDayTitle", SHELL_JS) + "\n"
        + _extract("reviewDayCardHtml", SHELL_JS) + "\n"
        + _extract("reviewDaysHtml", SHELL_JS) + "\n"
    )


def _groups(days: list) -> list:
    harness = _eating_prelude() + (
        f"console.log(JSON.stringify(reviewEatingGroups({json.dumps(days)})"
        ".map(function (g) { return { slot: g.slot, label: g.label, dishes: g.dishes.map("
        "function (d) { return { name: d.name, days: d.days.length, cooks: d.cooks }; }) }; })));\n"
    )
    return _run_node(harness)


def _eating_html(days: list) -> str:
    harness = _eating_prelude() + (
        f"console.log(JSON.stringify(reviewEatingHtml({json.dumps(days)})));\n"
    )
    return _run_node(harness)


def _day_card(day: dict, open_days: dict | None = None) -> str:
    harness = _days_prelude().replace(
        "openDays: {}", "openDays: " + json.dumps(open_days or {})
    ) + f"console.log(JSON.stringify(reviewDayCardHtml({json.dumps(day)}, 0)));\n"
    return _run_node(harness)


def _cooked(title: str, **kw) -> dict:
    out = {"state": "planned", "title": title, "source": "plan", "meta": "35 min", "entry_id": 1}
    out.update(kw)
    return out


def _day(date: str, **slots) -> dict:
    out = {
        "date": date, "isToday": False, "isPast": False,
        "breakfast": None, "lunch": None, "dinner": None, "snacks": [],
    }
    out.update(slots)
    out["snack"] = (out.get("snacks") or [None])[0]
    return out


# ---------- what we're eating: each dish once, with an honest count ----------

@_needs_node
def test_a_dish_on_three_nights_is_one_row_saying_three():
    days = [
        _day("2026-09-07", dinner=_cooked("Beef Chili", entry_id=11)),
        _day("2026-09-08", dinner=_cooked("Beef Chili", entry_id=12)),
        _day("2026-09-09", dinner=_cooked("Beef Chili", entry_id=13)),
    ]
    groups = _groups(days)
    assert [g["slot"] for g in groups] == ["dinner"]
    assert groups[0]["dishes"] == [{"name": "Beef Chili", "days": 3, "cooks": 3}]


@_needs_node
def test_a_dish_cooked_once_and_eaten_twice_covers_both_days_and_is_one_cook():
    """
    The acceptance criterion this screen turns on. A leftover chain reads as
    ONE dish over two days, and it counts as one cook — the same rule
    weekly_plan._is_cook applies server-side, so the receipt and this screen
    can never put different numbers on the same week.
    """
    reheat = {
        "state": "planned", "title": "Made ahead — Sunday’s Beef Chili",
        "source": "leftovers", "meta": "reheat", "entry_id": 22,
        "leftover_from": {"date": "2026-09-07", "meal": "Beef Chili", "cook_ahead": True},
    }
    days = [
        _day("2026-09-07", dinner=_cooked("Beef Chili", entry_id=21)),
        _day("2026-09-08", dinner=reheat),
    ]
    dishes = _groups(days)[0]["dishes"]
    assert dishes == [{"name": "Beef Chili", "days": 2, "cooks": 1}]


@_needs_node
def test_the_row_says_cooked_once_only_when_that_is_not_already_the_count():
    """§8: a line that restates what is already on screen is not a line. The
    stepper beside it already says how many days — this one exists for the
    fact the count cannot carry."""
    reheat = {
        "state": "planned", "title": "Leftovers", "source": "leftovers",
        "meta": "reheat", "entry_id": 32,
        "leftover_from": {"date": "2026-09-07", "meal": "Beef Chili", "cook_ahead": False},
    }
    chained = _eating_html([
        _day("2026-09-07", dinner=_cooked("Beef Chili", entry_id=31)),
        _day("2026-09-08", dinner=reheat),
    ])
    assert "cooked once" in chained
    assert "2 nights" in chained

    plain = _eating_html([
        _day("2026-09-07", dinner=_cooked("Beef Chili", entry_id=41)),
        _day("2026-09-08", dinner=_cooked("Beef Chili", entry_id=42)),
    ])
    assert "cooked" not in plain
    assert "2 nights" in plain


@_needs_node
def test_an_away_night_and_an_open_night_are_not_things_we_are_eating():
    """A planned_empty slot must never be offered as a decision and an open
    one is a question, not a dish. Counting either is the mistake three
    earlier bugs made."""
    days = [
        _day("2026-09-07", dinner={"state": "planned_empty", "title": "Out — nothing to cook",
                                   "need": "away", "entry_id": 51}),
        _day("2026-09-08", dinner={"state": "open", "title": "I’d like your call on this one",
                                   "open_reason": "Nothing under 20 minutes", "entry_id": 52}),
        _day("2026-09-09", dinner=_cooked("Beef Chili", entry_id=53)),
    ]
    dishes = _groups(days)[0]["dishes"]
    assert dishes == [{"name": "Beef Chili", "days": 1, "cooks": 1}]


@_needs_node
def test_two_different_snacks_a_day_are_two_rows_each_covering_that_day():
    snack_a = {"state": "planned", "title": "Apple slices", "source": "plan",
               "meta": None, "entry_id": 61}
    snack_b = {"state": "planned", "title": "Hummus and carrots", "source": "plan",
               "meta": None, "entry_id": 62}
    days = [
        _day("2026-09-07", snacks=[dict(snack_a), dict(snack_b)]),
        _day("2026-09-08", snacks=[dict(snack_a, entry_id=63), dict(snack_b, entry_id=64)]),
    ]
    groups = _groups(days)
    assert [g["slot"] for g in groups] == ["snack"]
    assert groups[0]["dishes"] == [
        {"name": "Apple slices", "days": 2, "cooks": 2},
        {"name": "Hummus and carrots", "days": 2, "cooks": 2},
    ]


@_needs_node
def test_the_groups_come_back_in_the_order_a_day_is_eaten():
    days = [_day(
        "2026-09-07",
        breakfast=_cooked("Oatmeal", entry_id=71),
        lunch=_cooked("Chicken Wrap", entry_id=72),
        dinner=_cooked("Beef Chili", entry_id=73),
        snacks=[{"state": "planned", "title": "Apple slices", "source": "plan",
                 "meta": None, "entry_id": 74}],
    )]
    assert [g["label"] for g in _groups(days)] == \
        ["Breakfasts", "Lunches", "Dinners", "Snacks"]


@_needs_node
def test_the_stepper_counts_in_the_words_the_household_uses_for_that_meal():
    """"4 mornings", not "4 breakfasts" — Emily's own phrasing, and what a
    person actually says."""
    html = _eating_html([
        _day("2026-09-07", breakfast=_cooked("Oatmeal", entry_id=81)),
        _day("2026-09-08", breakfast=_cooked("Oatmeal", entry_id=82)),
    ])
    assert "2 mornings" in html
    one = _eating_html([_day("2026-09-07", lunch=_cooked("Chicken Wrap", entry_id=83))])
    assert "1 lunch" in one


@_needs_node
def test_a_dish_covering_one_day_cannot_be_stepped_down_any_further():
    """A dish you don't want at all is a change, not a smaller number — and
    Change is the button right beside it."""
    html = _eating_html([_day("2026-09-07", dinner=_cooked("Beef Chili", entry_id=91))])
    minus = re.search(r'data-rv-less="0"[^>]*', html).group(0)
    assert "disabled" in minus
    plus = re.search(r'data-rv-more="0"[^>]*', html).group(0)
    assert "disabled" not in plus


@_needs_node
def test_every_dish_row_carries_a_stepper_and_a_change_button():
    html = _eating_html([
        _day("2026-09-07", dinner=_cooked("Beef Chili", entry_id=101),
             breakfast=_cooked("Oatmeal", entry_id=102)),
    ])
    assert html.count("data-rv-change=") == 2
    assert html.count("data-rv-less=") == 2
    # Indexes are flat across the groups, so the wiring can look a dish up
    # without a dish name ever going into an HTML attribute.
    assert 'data-rv-change="0"' in html and 'data-rv-change="1"' in html


@_needs_node
def test_a_week_with_nothing_planned_says_so_rather_than_rendering_empty_groups():
    assert "Nothing planned yet." in _eating_html([_day("2026-09-07")])


# ---------------------- which days: dinner, then the rest ----------------------

@_needs_node
def test_a_collapsed_day_card_carries_that_days_dinner():
    html = _day_card(_day("2026-09-07",
                          breakfast=_cooked("Oatmeal", entry_id=111),
                          dinner=_cooked("Beef Chili", entry_id=112)))
    assert "Beef Chili" in html
    # Collapsed: the other four are not on screen yet.
    assert "Oatmeal" not in html
    assert 'aria-expanded="false"' in html


@_needs_node
def test_an_opened_day_card_shows_all_five():
    day = _day(
        "2026-09-07",
        breakfast=_cooked("Oatmeal", entry_id=121),
        lunch=_cooked("Chicken Wrap", entry_id=122),
        dinner=_cooked("Beef Chili", entry_id=123),
        snacks=[
            {"state": "planned", "title": "Apple slices", "source": "plan",
             "meta": None, "entry_id": 124},
            {"state": "planned", "title": "Hummus and carrots", "source": "plan",
             "meta": None, "entry_id": 125},
        ],
    )
    html = _day_card(day, {"2026-09-07": True})
    for name in ("Oatmeal", "Chicken Wrap", "Beef Chili", "Apple slices", "Hummus and carrots"):
        assert name in html, name
    # Two snacks are numbered, so a day with more than one can be told apart.
    assert "Snack 1" in html and "Snack 2" in html
    assert 'aria-expanded="true"' in html


@_needs_node
def test_a_day_nobody_is_home_says_so_and_offers_nothing_to_open():
    """
    "Shows nothing to cook" is read off the slot state, never inferred from
    a missing row — and the card is flat, because a deliberately empty slot
    must never be offered as a decision.
    """
    away = {"state": "planned_empty", "title": "Out — nothing to cook",
            "need": "away", "entry_id": 131}
    day = _day("2026-09-07", breakfast=dict(away), lunch=dict(away), dinner=dict(away))
    html = _day_card(day)
    assert "Away — nothing planned, nothing bought." in html
    assert "data-rv-day=" not in html, "an away day has nothing to expand"
    assert "is-closed" in html


@_needs_node
def test_a_day_with_no_rows_at_all_is_not_an_away_day():
    """An unplanned day and a day nobody is home are different facts, and
    only one of them is something the household chose."""
    html = _day_card(_day("2026-09-07"))
    assert "is-closed" not in html
    assert "Nothing yet" in html
    assert "Away" not in html
    # Flat, like a closed day, but for the opposite reason: there is nothing
    # under it rather than nothing to do.
    assert "data-rv-day=" not in html


@_needs_node
def test_a_day_nobody_is_home_is_read_off_its_three_meals_not_its_snacks():
    """
    Found in a browser, not in the code. Both places that mark a day away —
    the `out` night pass and the slot_needs pass in _finish_week_slots —
    write breakfast, lunch and dinner only, so a rule that also demanded
    empty SNACKS would essentially never fire on a real generated week. The
    first version of this rule read every slot, and the Friday nobody was
    home rendered as an ordinary day.
    """
    away = {"state": "planned_empty", "title": "Out — nothing to cook",
            "need": "away", "entry_id": 151}
    day = _day("2026-09-07", breakfast=dict(away), lunch=dict(away), dinner=dict(away),
               snacks=[{"state": "planned", "title": "Apple slices", "source": "plan",
                        "meta": None, "entry_id": 152}])
    html = _day_card(day)
    assert "is-closed" in html
    assert "Away — nothing planned, nothing bought." in html
    # ...and the snack is not hidden behind it: a real row still opens.
    assert "data-rv-day=" in html


@_needs_node
def test_a_made_ahead_night_says_so_on_the_face_of_its_day_card():
    """Without it a chain reads as the same dinner planned twice, which on a
    screen whose whole job is checking the week looks like a mistake to fix
    — the opposite of what it is."""
    reheat = {
        "state": "planned", "title": "Made ahead — Sunday’s Beef Chili",
        "source": "leftovers", "meta": "reheat", "entry_id": 161,
        "leftover_from": {"date": "2026-09-06", "meal": "Beef Chili", "cook_ahead": True},
    }
    html = _day_card(_day("2026-09-07", dinner=reheat))
    assert "made ahead" in html
    # A night somebody actually cooks says nothing extra.
    assert "made ahead" not in _day_card(
        _day("2026-09-07", dinner=_cooked("Beef Chili", entry_id=162)))


@_needs_node
def test_a_made_ahead_night_reads_as_the_dish_not_as_the_whole_sentence():
    reheat = {
        "state": "planned", "title": "Made ahead — Sunday’s Beef Chili",
        "source": "leftovers", "meta": "reheat", "entry_id": 141,
        "leftover_from": {"date": "2026-09-06", "meal": "Beef Chili", "cook_ahead": True},
    }
    html = _day_card(_day("2026-09-07", dinner=reheat))
    assert ">Beef Chili<" in html
    assert "Made ahead —" not in html


# ------------------------------- the step itself -------------------------------

def _step_html(data: dict, days: list) -> str:
    # weekPlanState reads the plan off `data`, days included, so the shape
    # handed to the step is the shape get_week_menu actually returns.
    data["days"] = data.get("days") or days
    harness = (
        _eating_prelude() + _days_prelude().replace(_ESCAPE, "").replace(_WEEK_SLOTS, "")
        + _extract("weekPlanState", SHELL_JS) + "\n"
        + _extract("countOpenSlots", SHELL_JS) + "\n"
        + _extract("approveWithOpenLabel", SHELL_JS) + "\n"
        + _extract("reviewDecideHtml", SHELL_JS) + "\n"
        + _extract("reviewStepHtml", SHELL_JS) + "\n"
        + f"reviewState.view = {json.dumps(data.pop('_view', 'eating'))};\n"
        + f"console.log(JSON.stringify(reviewStepHtml({json.dumps(data)}, {json.dumps(days)})));\n"
    )
    return _run_node(harness)


_DRAFT = {"weekly_plan_id": 3, "status": "draft", "days": []}


@_needs_node
def test_the_screen_is_titled_check_the_week_and_badged_not_approved_yet():
    html = _step_html(dict(_DRAFT), [_day("2026-09-07", dinner=_cooked("Beef Chili"))])
    assert "Check the week" in html
    assert "NOT APPROVED YET" in html


@_needs_node
def test_the_screen_has_exactly_one_segmented_control_and_one_apricot():
    """Rule 5, and §5's one-control rule. The two views are the control; the
    approve button is the apricot, and nothing else on the screen competes
    with it."""
    html = _step_html(dict(_DRAFT), [_day("2026-09-07", dinner=_cooked("Beef Chili"))])
    assert html.count('class="wk-seg"') == 1
    assert html.count("data-rv-view=") == 2
    assert html.count("btn-gold") == 1
    assert "Approve and build my shopping list" in html


@_needs_node
def test_both_views_render_the_same_week_and_only_one_at_a_time():
    days = [_day("2026-09-07", dinner=_cooked("Beef Chili"))]
    eating = _step_html(dict(_DRAFT, _view="eating"), days)
    which = _step_html(dict(_DRAFT, _view="days"), days)
    assert "data-rv-less=" in eating and "data-rv-day=" not in eating
    assert "data-rv-day=" in which and "data-rv-less=" not in which
    assert "Beef Chili" in eating and "Beef Chili" in which


@_needs_node
def test_an_approved_week_still_reads_back_here_without_an_approve_button():
    """This is the Review step for every week, not a first-run screen."""
    html = _step_html(
        {"weekly_plan_id": 3, "status": "approved", "days": []},
        [_day("2026-09-07", dinner=_cooked("Beef Chili"))],
    )
    assert "Check the week" in html
    assert "APPROVED" in html
    assert "btn-gold" not in html


@_needs_node
def test_a_week_with_an_open_slot_names_it_on_the_approve_button():
    """Taking a day off a dish CREATES an open slot, so this screen is the
    one most likely to make a week that approves with one. Approving past an
    open slot is allowed — being quiet about it is not."""
    days = [_day("2026-09-07", dinner={"state": "open", "entry_id": 9,
                                       "title": "I’d like your call on this one"})]
    html = _step_html(dict(_DRAFT, days=days), days)
    assert "Approve — leave Thursday open" in html


# -------------------------------- the way in --------------------------------

def test_a_draft_carries_the_way_into_the_review_step_on_the_page():
    """Reachable from a draft at any time, which means on the draft's own
    decision row rather than behind a sheet."""
    assert 'id="week-check-btn"' in SHELL_JS
    assert ">Check the week</button>" in SHELL_JS
    assert "goMealsStep('review')" in SHELL_JS


def test_an_approved_week_reaches_it_through_the_more_sheet():
    assert "'wk-more-check', 'Check the week'" in SHELL_JS


def test_the_review_step_is_a_step_of_meals_and_not_a_page():
    """Four native screens, period — everything else is a state, a sheet or
    a step (§6). Review renders into #week-steps like the other three and
    its back link goes up a level by name, never history.back()."""
    assert "weekState.step === 'review'" in SHELL_JS
    assert 'data-wk-back="week">‹ This week' in SHELL_JS


def test_the_stepper_and_the_change_button_are_at_least_44px():
    """Rule 6, and the one rule a stepper is most likely to break."""
    step_btn = re.search(r"\.rv-step-btn \{(.*?)\}", SHELL_CSS, re.S).group(1)
    assert "width: 44px" in step_btn and "height: 44px" in step_btn
    change = re.search(r"\.rv-change \{(.*?)\}", SHELL_CSS, re.S).group(1)
    assert "min-height: 44px" in change


def test_no_literal_hex_in_the_review_styles():
    """Canonical tokens only — no literal hex, no legacy aliases."""
    block = SHELL_CSS[SHELL_CSS.index("REVIEW: one control, two ways"):]
    block = block[: block.index("/* ---------- \"One thing to settle\"")] \
        if '/* ---------- "One thing to settle"' in block else block
    assert not re.search(r"#[0-9a-fA-F]{3,8}\b", block), "literal hex in the Review styles"
    for alias in ("--plum", "--gold", "--midnight-violet", "--oat-cream"):
        assert alias not in block


# ------------- the stepper's own call, run rather than read -------------
# runDropDishDay is where the second blocker lived: it spliced the changed
# day into weekState.DAYS and stopped, while the Approve button's count
# reads weekState.DATA, which a splice never touches. The defect was a
# missing call, so these run the real function against stubs and record what
# it actually did.

def _run_drop(response: dict, status: str = "draft") -> dict:
    harness = (
        "var calls = [];\n"
        "var reviewState = { view: 'eating', busy: null, trouble: '', dishes: [\n"
        "  { slot: 'dinner', name: 'Beef Chili', cooks: 2,\n"
        "    days: [{date:'2026-09-07',entryId:1},{date:'2026-09-08',entryId:2}] }] };\n"
        "var weekState = { data: { week_start_date: '2026-09-07', status: "
        + json.dumps(status) + " }, days: [] };\n"
        "var SWAP_TROUBLE = 'That didn’t work just now — nothing changed.';\n"
        "function spliceSwappedDay(d){ calls.push('splice'); }\n"
        "function renderMealsStep(){ calls.push('render'); }\n"
        "async function loadWeekMenu(){ calls.push('loadWeekMenu'); }\n"
        "function showToast(t){ calls.push('toast:' + t); }\n"
        "function refreshGrocerySurfaces(){ calls.push('grocery'); }\n"
        "function dayName(d, o){ return 'Thursday'; }\n"
        "function slotWord(s){ return s; }\n"
        "var fetchBody = null;\n"
        "async function fetch(url, opts){ fetchBody = JSON.parse(opts.body);\n"
        "  calls.push('fetch:' + url);\n"
        "  return { ok: true, json: async () => (" + json.dumps(response) + ") }; }\n"
        + _extract_async("runDropDishDay", SHELL_JS) + "\n"
        "runDropDishDay(null, 0).then(function () {\n"
        "  console.log(JSON.stringify({ calls: calls, body: fetchBody,\n"
        "    trouble: reviewState.trouble, busy: reviewState.busy })); });\n"
    )
    return _run_node(harness)


_DROPPED = {"status": "dropped", "date": "2026-09-08", "slot": "dinner",
            "dish": "Beef Chili", "open_reason": "You cut Beef Chili back, so this "
            "one is yours to fill.", "day": {"date": "2026-09-08"}}


@_needs_node
def test_the_stepper_reloads_the_week_so_the_approve_button_stays_true():
    """
    BLOCKER, found in review and reproduced in a browser. The splice updates
    weekState.days; countOpenSlots — which the Approve button's label is
    built from — reads weekState.data.days, which the splice never touches.
    Without the reload the button went on saying "Approve and build my
    shopping list" on a week that had just been handed an open slot back.
    runSwapInPlace has had this line all along, and says why.
    """
    out = _run_drop(_DROPPED)
    assert "loadWeekMenu" in out["calls"], out["calls"]
    # ...and after the splice, so the reload is what the screen ends on.
    assert out["calls"].index("loadWeekMenu") > out["calls"].index("splice")


@_needs_node
def test_the_stepper_says_out_loud_that_the_slot_is_now_a_question():
    """The tap leaves work behind — a night with nothing on it. Saying so is
    the cue the household otherwise never gets, since Review renders an open
    slot as the bare words "Your call"."""
    toasts = [c for c in _run_drop(_DROPPED)["calls"] if c.startswith("toast:")]
    assert len(toasts) == 1, toasts
    assert "yours to fill" in toasts[0]


@_needs_node
def test_it_targets_the_last_day_the_dish_covers():
    assert _run_drop(_DROPPED)["body"] == {"entry_id": 2}


@_needs_node
def test_a_refusal_writes_nothing_and_shows_the_server_s_own_sentence():
    refusal = {"status": "refused", "dish": "Beef Chili",
               "message": "Beef Chili on Monday also feeds Tuesday’s dinner — "
                          "change that first and I’ll take this one off."}
    out = _run_drop(refusal)
    assert "also feeds" in out["trouble"]
    assert "splice" not in out["calls"], out["calls"]
    assert "loadWeekMenu" not in out["calls"], out["calls"]
    assert not [c for c in out["calls"] if c.startswith("toast:")]


@_needs_node
def test_an_approved_week_s_grocery_surfaces_are_refreshed_and_a_draft_s_are_not():
    assert "grocery" in _run_drop(_DROPPED, status="approved")["calls"]
    assert "grocery" not in _run_drop(_DROPPED, status="draft")["calls"]


@_needs_node
def test_the_busy_state_is_cleared_however_the_call_ends():
    assert _run_drop(_DROPPED)["busy"] is None
    assert _run_drop({"status": "refused", "message": "no"})["busy"] is None
