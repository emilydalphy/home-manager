"""
Today: the day's meals at the top, then Cook, Prep and a shopping-day line.

Loop Board (High, Phase 1; Gowthami's household, 2026-10-04: "Today screen
should show what the meals are for the day, and have a prep section" /
"Show the Cook for today vs a prep section; Shop: I do grocery shopping on
X day"). Mockup option A, approved and locked in.

MEASURED BEFORE THE CHANGE, on a throwaway DB through the real tools: Today
had TWO cards, Shop first and Cook second, with the day's fridge/prep rows
INSIDE Cook, and no breakfast/lunch/dinner summary anywhere on the screen.
The payload carried no `day_meals` and no `shop` block.

Each test says CATCH (red before the change) or GUARD (green either way,
and pinned by a mutation that was actually run). The branch is stacked on
`overnight/grocery-shop-day`, which is where the shop-day rhythm fact
comes from.

MUTATIONS RUN, with their real red counts over this file plus
tests/test_today_shop_cook.py (the other file that renders these builders):

    1. sections in the wrong order (shop first, as before)        6 red
    2. the meals card omitted entirely                            9 red
    3. per-person lines collapsed to one                          3 red
    4. prep left inside Cook (COOK_KINDS takes fridge+prep)       5 red
    5. the shop line rendered as a task (a tick on it)            1 red
    6. the shop line rendered on shopping day too                 2 red
    7. the snack row before Lunch (SLOT_ORDER)                    3 red
    8. a count of done things put back on Cook                    2 red
    9. a reheat offered as a cook (todayMealTarget ignores it)     2 red
   10. the start time re-derived instead of read off `chips`       1 red
   11. day_meals' own initials instead of display_initials         1 red
   12. provenance_note's two implementations diverge again         3 red

See the hand-back for how each was applied.
"""
from __future__ import annotations

import datetime
import json
import re
import shutil
from pathlib import Path

import pytest

import nodeharness
from conftest import household_today

from app.tools import (
    attendance,
    cooker,
    day_meals,
    meal_plans,
    moves,
    recipes,
    rhythm,
    weekly_plan,
)
from app.tools import household as household_tools

REPO = Path(__file__).resolve().parents[1]
SHELL_JS = (REPO / "static" / "shell.js").read_text(encoding="utf-8")
SHELL_CSS = (REPO / "static" / "shell.css").read_text(encoding="utf-8")

_needs_node = pytest.mark.skipif(
    shutil.which("node") is None, reason="node runs the screen's own builders"
)


# ==========================================================================
# 1. The payload — app/tools/day_meals.py
# ==========================================================================

def _people(*names_and_ages):
    for name, age in names_and_ages:
        household_tools.add_member(name)
        household_tools.set_member_age_group(name, age)


def _recipe(name, minutes=25):
    recipes.add_recipe(
        name,
        ingredients=[{"item": name + " thing", "qty": "1", "category": "pantry"}],
        instructions=["Cook it"],
        prep_time_minutes=5,
        cook_time_minutes=minutes,
        default_servings=4,
    )


def _seed_day(meals, day=None, snacks=1):
    """A household of three with a one-day approved plan holding `meals`
    ({slot: dish}); `snacks` extra snack dishes beyond what `meals` names."""
    day = day or household_today()
    _people(("Gowthami", "adult"), ("Ravi", "adult"), ("Arjun", "child"))
    for slot, dish in meals.items():
        _recipe(dish)
    plan = meal_plans.create_weekly_plan(day.isoformat(), day_count=1)
    pid = plan["weekly_plan_id"]
    for slot, dish in meals.items():
        meal_plans.plan_meal(day.isoformat(), dish, slot.rstrip("0123456789"),
                             weekly_plan_id=pid)
    weekly_plan.approve_weekly_plan(pid)
    return pid, day


def test_the_rows_are_breakfast_lunch_snacks_dinner_in_that_order():
    """CATCH: there was no day_meals payload at all before this.

    SNACKS SIT BETWEEN LUNCH AND DINNER — Emily's own instruction on the
    mockup, 2026-10-04 ("after Lunch and before Dinner"). The card's older
    summary line says "(Snacks last if planned)"; the later, more specific
    sentence wins, and the card's own mutation list assumes it too ("the
    snack row before Lunch" is named there as a wrong state).
    """
    _seed_day({"breakfast": "Idli", "lunch": "Chana Masala",
               "dinner": "Pepper Chicken", "snack": "Apple Slices"})
    rows = moves.today_moves()["day_meals"]
    assert [r["slot"] for r in rows] == ["breakfast", "lunch", "snack", "dinner"]
    assert [r["label"] for r in rows] == ["Breakfast", "Lunch", "Snacks", "Dinner"]
    assert [r["dishes"] for r in rows] == [
        ["Idli"], ["Chana Masala"], ["Apple Slices"], ["Pepper Chicken"],
    ]


def test_everyone_eating_the_same_thing_is_one_row_saying_everyone():
    """CATCH. The card: "When everyone eats the same thing it stays a
    single row with 'everyone'.\""""
    _seed_day({"dinner": "Pepper Chicken"})
    row = moves.today_moves()["day_meals"][0]
    assert row["per_person"] is False
    assert [ln["who"] for ln in row["lines"]] == ["everyone"]
    assert row["lines"][0]["initials"] == [], "nobody needs naming when it is everybody"


def test_a_meal_somebody_is_out_of_names_who_it_is_for():
    """CATCH. The card: a short grey note saying "who it's for when not
    everyone"."""
    _, day = _seed_day({"dinner": "Pepper Chicken"})
    attendance.set_member_attendance(day.isoformat(), "dinner", "Arjun", present=False)
    row = moves.today_moves()["day_meals"][0]
    assert row["per_person"] is False, "an absence is the row's note, not a per-person split"
    assert row["lines"][0]["who"] == "Gowthami + Ravi"


def test_a_lunch_where_people_are_in_different_places_is_one_line_each():
    """CATCH — the per-person lunch day the card names.

    This is THE case today's data can actually produce: the dish is the
    same (meal_plan_entries holds one per slot and has no member column at
    all), and what varies per person is where they are
    (rhythm.lunch_location). People who share both a dish and a place
    share a line, which is what reproduces the card's own example —
    "Gowthami + Ravi" together, Arjun on his own, all three on one dish.
    """
    _seed_day({"lunch": "Chana Masala"})
    rhythm.set_lunch_location("Gowthami", "home")
    rhythm.set_lunch_location("Ravi", "home")
    rhythm.set_lunch_location("Arjun", "out")
    row = moves.today_moves()["day_meals"][0]
    assert row["per_person"] is True
    assert [(ln["who"], ln["where"], ln["dish"]) for ln in row["lines"]] == [
        ("Gowthami + Ravi", "at home", "Chana Masala"),
        ("Arjun", "out", "Chana Masala"),
    ]
    assert [ln["initials"] for ln in row["lines"]] == [["G", "R"], ["A"]]


def test_the_initials_are_the_apps_own_rule_not_a_first_letter_slice():
    """GUARD (pinned by mutation 11: day_meals growing its own `_initial`).

    _shared.display_initials is THE rule for an initial (Loop Board "Two
    people with the same initial", 2026-09-21) — two people whose names
    clash get the shortest prefix that tells them apart, and it is computed
    over the WHOLE household so the letter is the same on every screen.
    """
    household_tools.add_member("Emily")
    household_tools.set_member_age_group("Emily", "adult")
    household_tools.add_member("Ethan")
    household_tools.set_member_age_group("Ethan", "adult")
    people = day_meals.household_people()
    assert people["initials"] == {"Emily": "Em", "Ethan": "Et"}
    assert "def _initial(" not in (REPO / "app" / "tools" / "day_meals.py").read_text()


def test_a_lunch_nobody_has_answered_for_says_nothing_about_where():
    """GUARD (pinned by mutation 3). 'varies' is a real answer that still
    says nothing about today, and never-asked says nothing either — neither
    invents a place (DESIGN_SYSTEM.md §8)."""
    _seed_day({"lunch": "Chana Masala"})
    rows = moves.today_moves()["day_meals"]
    assert rows[0]["per_person"] is False and rows[0]["lines"][0]["where"] == ""
    rhythm.set_lunch_location("Gowthami", "varies")
    rhythm.set_lunch_location("Ravi", "varies")
    rhythm.set_lunch_location("Arjun", "varies")
    rows = moves.today_moves()["day_meals"]
    assert rows[0]["per_person"] is False, "'varies' for everyone is one group, not three"
    assert rows[0]["lines"][0]["where"] == ""


def test_breakfast_and_dinner_say_nothing_about_where_because_nothing_records_it():
    """GUARD. lunch_location is the only per-person place fact the app has
    and it is about lunch by name; saying nothing about the other meals is
    the honest answer rather than an omission."""
    _seed_day({"breakfast": "Idli", "lunch": "Chana Masala", "dinner": "Pepper Chicken"})
    rhythm.set_lunch_location("Arjun", "out")
    rows = {r["slot"]: r for r in moves.today_moves()["day_meals"]}
    assert rows["lunch"]["per_person"] is True
    for slot in ("breakfast", "dinner"):
        assert rows[slot]["per_person"] is False
        assert rows[slot]["lines"][0]["where"] == ""


def test_a_two_snack_day_is_one_row_naming_both_dishes():
    """CATCH. A day can hold two snacks (DAY_SLOTS includes snack; see the
    2026-09-08 "Meals renders snacks" entry) and snacks are household-wide
    today, so the card asks for ONE row."""
    _, day = _seed_day({"lunch": "Chana Masala", "snack": "Apple Slices"})
    _recipe("Roasted Chana")
    pid = cooker.get_cooker_view()["weekly_plan_id"]
    meal_plans.plan_meal(day.isoformat(), "Roasted Chana", "snack", weekly_plan_id=pid)
    rows = [r for r in moves.today_moves()["day_meals"] if r["slot"] == "snack"]
    assert len(rows) == 1, "one snack row, however many snacks the day holds"
    assert sorted(rows[0]["dishes"]) == ["Apple Slices", "Roasted Chana"]
    assert len(rows[0]["entry_ids"]) == 2, "both are addressable when per-person snacks land"


def test_a_day_with_snacks_off_has_no_snack_row():
    """CATCH. "No snack row when snacks are off" — which in the data is
    simply no snack entry on the day."""
    _seed_day({"breakfast": "Idli", "lunch": "Chana Masala", "dinner": "Pepper Chicken"})
    rows = moves.today_moves()["day_meals"]
    assert [r["slot"] for r in rows] == ["breakfast", "lunch", "dinner"]


def test_a_day_with_nothing_planned_has_no_rows_at_all():
    """CATCH. The screen turns [] into "Nothing planned today"; the server
    says nothing rather than inventing an empty meal."""
    _people(("Gowthami", "adult"))
    assert moves.today_moves()["day_meals"] == []


def test_a_meal_nobody_is_home_for_is_not_a_row():
    """GUARD. slot_needs already writes that slot as planned_empty; this is
    the belt for a row that reached here anyway, and it must not say "Dinner
    · everyone" about a meal nobody is eating."""
    _, day = _seed_day({"dinner": "Pepper Chicken"})
    for name in ("Gowthami", "Ravi", "Arjun"):
        attendance.set_member_attendance(day.isoformat(), "dinner", name, present=False)
    assert [r["slot"] for r in moves.today_moves()["day_meals"]] == []


# ---------- the grey note ----------

def test_the_provenance_wording_has_one_implementation():
    """CATCH (mutation 12). moves.py carried its own copy of "leftovers from
    Sunday" / "made ahead Sunday" / "from the freezer" / "prepped Sunday";
    Today now draws the meals row and the Cook row on ONE screen, so that is
    the one place the two would have been seen drifting."""
    src = (REPO / "app" / "tools" / "moves.py").read_text()
    assert "_day_meals.provenance_note(meal)" in src
    assert 'lead = "made ahead" if made_ahead else "leftovers from"' not in src, (
        "the second implementation is gone, not merely unused"
    )
    assert src.count('"from the freezer"') == 0


@pytest.mark.parametrize("meal,expected", [
    ({"is_leftovers": True, "leftovers_headline": "Leftovers — Sunday's Bulgogi",
      "leftovers_from": {"date": "2026-10-04"}}, "leftovers from Sunday"),
    ({"is_leftovers": True, "leftovers_headline": "Made ahead — Sunday's Bites",
      "leftovers_from": {"date": "2026-10-04"}}, "made ahead Sunday"),
    ({"is_leftovers": True, "leftovers_headline": "Made ahead — Bites",
      "leftovers_from": {}}, "made ahead"),
    ({"is_leftovers": True, "leftovers_headline": "Leftovers — Chili",
      "leftovers_from": {}}, "from the freezer"),
    ({"date": "2026-10-05", "prepped_ahead": {"date": "2026-10-04"}}, "prepped Sunday"),
    ({"date": "2026-10-05", "prepped_ahead": {"date": "2026-10-05"}}, ""),
    ({}, ""),
])
def test_provenance_note_says_where_a_meal_came_from(meal, expected):
    """GUARD (mutation 12 reddens the source marker above; these pin the
    behaviour the extraction had to preserve exactly)."""
    assert day_meals.provenance_note(meal) == expected


def test_covers_note_says_what_a_batch_makes():
    """GUARD (pinned by mutation 2 — with no meals card there is nowhere
    for it to be read). The card's "makes tomorrow's lunch", off `covers`
    ({date, slot, eaters}) rather than parsed back out of English."""
    day = "2026-10-05"
    assert day_meals.covers_note(
        {"covers": [{"date": "2026-10-06", "slot": "lunch"}]}, day
    ) == "makes tomorrow's lunch"
    assert day_meals.covers_note(
        {"covers": [{"date": "2026-10-08", "slot": "dinner"}]}, day
    ) == "makes Thursday's dinner"
    assert day_meals.covers_note({"covers": [{"date": day, "slot": "dinner"}]}, day) == ""
    assert day_meals.covers_note({}, day) == ""


# ---------- the shop block ----------

def test_the_shop_line_names_the_day_the_household_said():
    """CATCH — the shop-day line the card names. Reads the rhythm fact
    `overnight/grocery-shop-day` added; this branch is stacked on it."""
    _seed_day({"dinner": "Pepper Chicken"})
    day = household_today()
    other = "saturday" if day.strftime("%A").lower() != "saturday" else "tuesday"
    rhythm.set_shop_days(shop_day=other)
    shop = moves.today_moves()["shop"]
    assert shop["shop_day"] == other and shop["is_shop_day"] is False
    assert shop["line"].startswith("You shop on " + other.capitalize() + ".")
    assert "on the list so far." in shop["line"]


def test_on_shopping_day_the_line_stands_aside_for_the_shop_card():
    """CATCH. "On shopping day it becomes the Shop card that exists today."
    The server says WHICH day it is; the screen decides which of the two to
    draw off that one boolean."""
    _seed_day({"dinner": "Pepper Chicken"})
    rhythm.set_shop_days(shop_day=household_today().strftime("%A").lower())
    assert moves.today_moves()["shop"]["is_shop_day"] is True


def test_a_top_up_shop_day_counts_as_a_shopping_day():
    """GUARD (mutation 6). ASSUMPTION, reversible in one line
    (moves._shop_block): a top-up day is a day the household shops, and
    telling them when they shop on a day they are shopping would be the
    screen arguing with itself."""
    _seed_day({"dinner": "Pepper Chicken"})
    today = household_today().strftime("%A").lower()
    other = "saturday" if today != "saturday" else "tuesday"
    rhythm.set_shop_days(shop_day=other, top_up_day=today)
    shop = moves.today_moves()["shop"]
    assert shop["is_shop_day"] is True and shop["shop_day"] == other


def test_a_household_that_never_answered_gets_no_line_and_no_claim():
    """CATCH. An unanswered question is a real state: '' here, and the
    screen offers "No shopping day set. Pick one" rather than naming a day
    nobody gave (§8)."""
    _seed_day({"dinner": "Pepper Chicken"})
    shop = moves.today_moves()["shop"]
    assert shop["shop_day"] is None and shop["line"] == "" and shop["is_shop_day"] is False


def test_an_empty_list_says_so_rather_than_counting_nothing():
    """GUARD. "Nothing on the list yet" rather than "0 things" — a count of
    nothing is a number where a sentence belongs (§8), the same call
    _standing_list_move's own title makes."""
    _people(("Gowthami", "adult"))
    rhythm.set_shop_days(shop_day="saturday")
    assert moves.today_moves()["shop"]["line"] == "You shop on Saturday. Nothing on the list yet."


def test_one_thing_on_the_list_is_singular():
    """GUARD (pinned by mutation 5 only loosely; this is plain grammar)."""
    assert moves._shop_day_line("saturday", 1).endswith("1 thing on the list so far.")
    assert moves._shop_day_line("saturday", 2).endswith("2 things on the list so far.")


def _connects_for(fn):
    """How many connections `fn` opens, counted at sqlite3.connect rather
    than at any module's get_conn: day_meals imports get_conn BY NAME, so a
    module-level patch would not see its reads at all (the trap the
    2026-09-11 approve-race work records)."""
    import sqlite3

    real = sqlite3.connect
    calls = []

    def counting(*a, **kw):
        calls.append(1)
        return real(*a, **kw)

    sqlite3.connect = counting
    try:
        fn()
    finally:
        sqlite3.connect = real
    return len(calls)


def test_the_whole_day_costs_three_reads_and_never_one_per_person():
    """CATCH, against this branch's own first cut, and the reason that cut
    was wrong: _where_for went through rhythm.effective_lunch_location,
    which reads the WHOLE household rhythm, so a lunch cost one connection
    PER PERSON — measured at 6 for a household of three. The rhythm is read
    once for the day now (and on the path the screen takes, not even that:
    today_moves already holds it and hands it in).

    Three reads: the members, the day's attendance deviations, the rhythm.
    The LOWER bound matters as much as the upper one — `<= 3` alone would be
    green at zero, which is a payload that says nothing."""
    _seed_day({"breakfast": "Idli", "lunch": "Chana Masala",
               "dinner": "Pepper Chicken", "snack": "Apple Slices"})
    rhythm.set_lunch_location("Gowthami", "home")
    rhythm.set_lunch_location("Ravi", "home")
    rhythm.set_lunch_location("Arjun", "out")
    view = cooker.get_cooker_view()
    today = household_today()
    assert _connects_for(lambda: day_meals.for_day(today, view)) == 3
    # And it does not grow with the household. A fourth and fifth person,
    # each with a place of their own, is the same three reads.
    _people(("Meera", "adult"), ("Dev", "child"))
    rhythm.set_lunch_location("Meera", "out")
    rhythm.set_lunch_location("Dev", "home")
    view = cooker.get_cooker_view()
    assert _connects_for(lambda: day_meals.for_day(today, view)) == 3


def test_today_moves_hands_its_own_rhythm_down_rather_than_paying_twice():
    """GUARD (pinned by a mutation that drops the `rhythm=rhythm` argument:
    the count above goes 3 -> 4 on the whole payload). today_moves reads the
    household's rhythm once for everything it builds; day_meals must take
    that reading rather than making its own."""
    src = (REPO / "app" / "tools" / "moves.py").read_text()
    assert "_day_meals.for_day(target, view, people=people, rhythm=rhythm)" in src


# ==========================================================================
# 2. The screen — static/shell.js, run under node
# ==========================================================================

def _region(a: str, b: str) -> str:
    i = SHELL_JS.index(a)
    return SHELL_JS[i:SHELL_JS.index(b, i)]


def _fn(name: str) -> str:
    marker = "  function %s(" % name
    i = SHELL_JS.index(marker)
    return SHELL_JS[i:SHELL_JS.index("\n  }\n", i)] + "\n  }\n"


# The whole "Today: Shop and Cook" region, which is where every builder on
# this card lives — so a new one is picked up here without this list having
# to name it (the fixed-function-list hazard, 2026-10-05).
_PRELUDE = (
    "function escapeHtml(s){return String(s == null ? '' : s).replace(/&/g,'&amp;')"
    ".replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/\"/g,'&quot;');}\n"
    + _region("  var TICK_ICON =", "  var DOTS_ICON =")
    + _region("  // ---------- Today: Shop and Cook ----------", "  function tomorrowCardHtml(")
    + _fn("moveRecipeTarget")
    + _fn("moveDishHtml")
    + "function openRecipeFor() {}\n"
)


def _node(script: str):
    res = nodeharness.run_node(_PRELUDE + script, timeout=30)
    assert res.returncode == 0, f"node failed: {res.stderr}"
    return json.loads(res.stdout.strip().splitlines()[-1])


_SECTION_RE = re.compile(
    r'class="shell-card day-group day-(meals|group-cook|group-prep|group-shop)"'
    r'|class="shell-card (day-shopline)(?: is-unset)?"'
)


def _groups(html: str) -> list[str]:
    """The key of every section card, top to bottom. The meals card is
    `day-meals`; a group is `day-group-<key>`; the shop LINE is
    `day-shopline`."""
    return [(m.group(1) or "shopline") for m in _SECTION_RE.finditer(html)]


def _render(moves_list, featured=None, shop=None, day_meals_rows=None):
    return _node(
        "console.log(JSON.stringify(dayGroupsHtml(%s, %s, %s, %s)));"
        % (json.dumps(moves_list), json.dumps(featured), json.dumps(shop),
           json.dumps(day_meals_rows or []))
    )


def _move(kind, id_, title, start, end=None, **extra):
    m = {
        "id": id_, "kind": kind, "title": title, "detail": kind + " · line",
        "reason": "", "date": "2026-10-05", "slot": None,
        "window_start": "2026-10-05T" + start, "window_end": "2026-10-05T" + (end or start),
        "weight": 2, "action": {"label": "Done", "target": {}},
        "done": False, "tickable": True, "overdue": False, "entry_id": None,
        "task_id": None, "duration_min": 0, "time_label": "", "meta": extra.pop("meta", ""),
        "chips": extra.pop("chips", []),
    }
    m.update(extra)
    return m


def _full_day():
    return [
        _move("shop", "shop:2026-10-05", "Shop for tonight", "00:00:00", "17:55:00",
              tickable=False, timed=True, weight=3, meta="orzo, salmon…",
              stops=[{"store": "Costco", "count": 6, "items": ["orzo", "salmon"]}],
              action={"label": "Go shopping", "target": {"tab": "grocery"}}),
        _move("fridge", "fridge:4", "Move the chicken to the fridge", "00:00:00", "22:00:00",
              reason="for Thursday’s skewers", meta="for Thursday’s skewers"),
        _move("prep", "prep:9", "Soak the beans", "00:00:00", "22:00:00",
              reason="for Wednesday", meta="for Wednesday"),
        _move("reheat", "reheat:11", "Egg White Bites", "08:00:00", "10:00:00",
              weight=1, meta="made ahead Sunday · reheat", entry_id=11),
        _move("cook", "cook:13", "Chicken Skewers", "17:55:00", "20:30:00", weight=3,
              duration_min=35, meta="35 min", entry_id=13, chips=["35 min", "Start by 5:55"],
              action={"label": "Cook this", "target": {"tab": "kitchen", "cookFocus": {
                  "entryId": 13, "date": "2026-10-05", "slot": "dinner",
                  "title": "Chicken Skewers"}}}),
    ]


_MEAL_ROWS = [
    {"slot": "breakfast", "label": "Breakfast", "date": "2026-10-05", "dishes": ["Idli"],
     "entry_id": 1, "entry_ids": [1], "note": "", "per_person": False, "is_leftovers": False,
     "lines": [{"dish": "Idli", "entry_id": 1, "where": "", "names": [], "initials": [],
                "who": "everyone"}]},
    {"slot": "lunch", "label": "Lunch", "date": "2026-10-05", "dishes": ["Chana Masala"],
     "entry_id": 2, "entry_ids": [2], "note": "", "per_person": True, "is_leftovers": False,
     "lines": [
         {"dish": "Chana Masala", "entry_id": 2, "where": "at home",
          "names": ["Gowthami", "Ravi"], "initials": ["G", "R"], "who": "Gowthami + Ravi"},
         {"dish": "Chana Masala", "entry_id": 2, "where": "out",
          "names": ["Arjun"], "initials": ["A"], "who": "Arjun"}]},
    {"slot": "snack", "label": "Snacks", "date": "2026-10-05",
     "dishes": ["Apple Slices", "Roasted Chana"], "entry_id": 4, "entry_ids": [4, 5],
     "note": "", "per_person": False, "is_leftovers": False,
     "lines": [{"dish": "Apple Slices", "entry_id": 4, "where": "", "names": [],
                "initials": [], "who": "everyone"}]},
    {"slot": "dinner", "label": "Dinner", "date": "2026-10-05",
     "dishes": ["Chicken Skewers"], "entry_id": 13, "entry_ids": [13],
     "note": "makes tomorrow's lunch", "per_person": False, "is_leftovers": False,
     "lines": [{"dish": "Chicken Skewers", "entry_id": 13, "where": "", "names": [],
                "initials": [], "who": "everyone"}]},
]

_SHOP_OFF = {"shop_day": "saturday", "top_up_shop_day": None, "is_shop_day": False,
             "count": 14, "line": "You shop on Saturday. 14 things on the list so far.",
             "summary": "Grocery shop: Saturday"}
_SHOP_ON = dict(_SHOP_OFF, is_shop_day=True)
_SHOP_UNSET = {"shop_day": None, "top_up_shop_day": None, "is_shop_day": False,
               "count": 14, "line": "", "summary": ""}


@_needs_node
def test_the_four_sections_are_in_the_cards_order():
    """CATCH (mutation 1) — the section order the card names. It was Shop
    first, Cook second, prep rows inside Cook, and no meals card."""
    html = _render(_full_day(), None, _SHOP_OFF, _MEAL_ROWS)
    assert _groups(html) == ["meals", "group-cook", "group-prep", "shopline"]


@_needs_node
def test_on_shopping_day_the_fourth_section_is_the_shop_card():
    """CATCH (mutation 6). Same four slots; the last one is the card with
    its store stops rather than the line."""
    html = _render(_full_day(), None, _SHOP_ON, _MEAL_ROWS)
    assert _groups(html) == ["meals", "group-cook", "group-prep", "group-shop"]
    assert "day-shopline" not in html
    assert '<span class="day-node-title">Costco · 6 things</span>' in html


@_needs_node
def test_prep_is_its_own_card_and_not_rows_inside_cook():
    """CATCH (mutation 4). Measured before: the fridge move and the soak
    were rows in the Cook card."""
    html = _render(_full_day(), None, _SHOP_OFF, _MEAL_ROWS)
    cook = html.split('day-group-cook"', 1)[1].split('class="shell-card', 1)[0]
    prep = html.split('day-group-prep"', 1)[1].split('class="shell-card', 1)[0]
    assert "cook:13" in cook and "reheat:11" in cook
    assert "fridge:4" not in cook and "prep:9" not in cook
    assert "fridge:4" in prep and "prep:9" in prep
    # What each prep move is FOR, which is the card's own requirement.
    assert "for Thursday’s skewers" in prep and "for Wednesday" in prep
    # Ticking works as today's move ticks do — the same button, unchanged.
    assert 'data-move-tick="fridge:4"' in prep and 'data-move-tick="prep:9"' in prep


@_needs_node
def test_a_day_with_no_prep_draws_no_prep_card():
    """CATCH — "Hidden when there is no prep today.\""""
    day = [m for m in _full_day() if m["kind"] not in ("fridge", "prep")]
    html = _render(day, None, _SHOP_OFF, _MEAL_ROWS)
    assert _groups(html) == ["meals", "group-cook", "shopline"]
    assert "day-group-prep" not in html


@_needs_node
def test_a_reheat_says_reheat_and_is_never_a_way_into_a_recipe():
    """CATCH (mutation 9). Criterion 2's "Reheat rows show here" is a LABEL,
    not permission to treat a reheat as a cook: there is no recipe behind
    one (moves.py's own standing rule), so the meals row is a plain span
    rather than a button that would go nowhere."""
    html = _render(_full_day(), None, _SHOP_OFF, _MEAL_ROWS)
    row = html.split('data-move-id="reheat:11">', 1)[1].split('<div class="day-node is-', 1)[0]
    assert '<span class="day-node-kind">Reheat</span>' in row
    assert "day-node-eyebrow" not in row, "that one is the tinted row's word, and there is one"
    leftovers = [dict(_MEAL_ROWS[3], is_leftovers=True, note="leftovers from Sunday")]
    html = _render([], None, None, leftovers)
    assert "leftovers from Sunday" in html
    assert "data-meal-open" not in html, "nothing to open — a reheat has no recipe screen"


@_needs_node
def test_a_cook_row_says_when_to_start_and_the_batch_line():
    """CATCH (mutation 10) — criterion 2's "start time, total time, and the
    double-batch line". The start is read off the move's own `chips`, never
    re-derived here: the start-by arithmetic is the server's so Today and
    Cook cannot disagree about it."""
    day = _full_day()
    day[-1]["reason"] = "Cooking for 6 — covers Thursday"
    html = _render(day, None, _SHOP_OFF, _MEAL_ROWS)
    row = html.split('data-move-id="cook:13">', 1)[1]
    assert '<span class="day-node-meta">35 min</span>' in row, "the total time"
    assert '<span class="day-node-meta day-node-why">Start by 5:55 · Cooking for 6 — covers Thursday</span>' in row
    # Not a second implementation of the clock.
    assert "setMinutes" not in _fn("dayNodeWhyHtml")


@_needs_node
def test_the_meals_card_reads_breakfast_lunch_snacks_dinner():
    """CATCH (mutations 2 and 7) — the card's first section."""
    html = _render(_full_day(), None, _SHOP_OFF, _MEAL_ROWS)
    card = html.split('day-group day-meals"', 1)[1].split('class="shell-card', 1)[0]
    assert '<span class="day-group-title">Today’s meals</span>' in card
    assert re.findall(r'<span class="day-meal-slot">([^<]+)</span>', card) == [
        "Breakfast", "Lunch", "Snacks", "Dinner",
    ]
    # Snacks, household-wide today, name both dishes on one row.
    assert '<span class="day-meal-dish">Apple Slices, Roasted Chana</span>' in card
    # The grey note is the server's sentence, not one composed here.
    assert "makes tomorrow&#039;s lunch" in card or "makes tomorrow's lunch" in card


@_needs_node
def test_the_per_person_lunch_is_the_label_above_one_line_each():
    """CATCH (mutation 3) — Emily, 2026-10-04: the meal label sits above one
    line per person, a round initial, the name, where they will be in grey,
    and the dish on the line below."""
    html = _render([], None, None, _MEAL_ROWS)
    block = html.split('class="day-meal is-people"', 1)[1].split('</div>', 1)[0]
    assert '<span class="day-meal-slot">Lunch</span>' in block
    assert block.count('class="day-meal-line') == 2, "one line per group, not one collapsed row"
    assert '<span class="day-meal-initial">G</span><span class="day-meal-initial">R</span>' in block
    assert "Gowthami + Ravi · at home" in block
    assert "Arjun · out" in block
    assert block.count('<span class="day-meal-dish">Chana Masala</span>') == 2


@_needs_node
def test_everyone_is_one_row_and_tapping_it_opens_that_meal():
    """CATCH. "Tapping a row opens that meal" — through openRecipeFor, the
    one door to the one screen with a recipe on it."""
    html = _render([], None, None, _MEAL_ROWS)
    first = html.split('class="day-meal day-meal-open"', 1)[1].split("</button>", 1)[0]
    assert 'data-meal-open="1"' in html
    assert '<span class="day-meal-dish">Idli</span>' in first
    assert '<span class="day-meal-note">everyone</span>' in first
    assert "openRecipeFor" in _fn("renderTodayMoves")


@_needs_node
def test_an_empty_day_says_nothing_planned_today_and_hides_the_rest():
    """CATCH — "Empty day: Today's meals reads 'Nothing planned today', the
    rest hides, the existing empty moment applies.\""""
    html = _render([], None, _SHOP_OFF, [])
    assert _groups(html) == ["meals"]
    assert "Nothing planned today" in html
    assert "day-shopline" not in html, "the shop line would talk over the empty moment"
    assert "day-group-cook" not in html and "day-group-prep" not in html


@_needs_node
def test_a_household_with_no_shopping_day_is_offered_the_setting():
    """CATCH — "If no shopping day is known, 'No shopping day set. Pick one'
    opens the Stores/rhythm setting.\""""
    html = _render(_full_day(), None, _SHOP_UNSET, _MEAL_ROWS)
    assert "No shopping day set." in html and "Pick one" in html
    assert 'data-shop-setting="1"' in html
    wiring = _fn("renderTodayMoves")
    assert "data-shop-setting" in wiring and "openKitchenSheet('memory', 'rhythm')" in wiring


@_needs_node
def test_the_shop_line_is_a_line_and_not_a_task():
    """CATCH (mutation 5) — the card's own words, "a celadon line, not a
    task": no tick, no row state, nothing to mark done."""
    html = _render(_full_day(), None, _SHOP_OFF, _MEAL_ROWS)
    line = html.split('class="shell-card day-shopline"', 1)[1]
    assert "You shop on Saturday. 14 things on the list so far." in line
    assert "day-tick" not in line and "data-move-tick" not in line
    assert "day-node" not in line


@_needs_node
def test_nothing_in_the_four_sections_is_an_apricot_fill():
    """GUARD (hard rule 5 — Today's one accent belongs to the dock's one
    action). Source-level: none of these builders reaches for the dock's
    classes, and the CSS for each new block takes celadon or a token ink."""
    for name in ("todayMealsCardHtml", "todayMealRowHtml", "todayShopLineHtml",
                 "dayNodeWhyHtml", "dayGroupsHtml"):
        src = _fn(name)
        assert "dock-primary" not in src and "btn-gold" not in src
    block = SHELL_CSS.split("TODAY'S MEALS", 1)[1].split("Give the last row", 1)[0]
    assert "--apricot" not in block, "rule 5: no second accent on Today"
    assert "#" not in re.sub(r"/\*.*?\*/", "", block, flags=re.S), "rule 9: tokens only"


@_needs_node
def test_no_section_carries_a_count_of_done_things():
    """CATCH (mutation 8) — Emily's rule of 2026-09-24, and the "Now carries
    no score" entry. Cook had no count; Prep and the meals card must not
    introduce one, and neither may say "1 of 3"."""
    day = _full_day()
    day[1]["done"] = True
    day[3]["done"] = True
    html = _render(day, None, _SHOP_OFF, _MEAL_ROWS)
    for key in ("cook", "prep", "meals"):
        marker = 'day-meals"' if key == "meals" else 'day-group-%s"' % key
        card = html.split(marker, 1)[1].split('class="shell-card', 1)[0]
        assert "day-group-count" not in card, f"{key} must carry no count"
    assert not re.search(r"\d+ of \d+", html)
    # Shop's "N stops" is where you are going, not a score — it stays.
    on = _render(day, None, _SHOP_ON, _MEAL_ROWS)
    assert '<span class="day-group-count">1 stop</span>' in on


@_needs_node
def test_the_region_marker_two_harnesses_lift_this_code_by_is_intact():
    """GUARD. tests/test_today_shop_cook.py and tests/test_move_owner.py
    both _region/_slice from this exact line to "function
    tomorrowCardHtml(" — renaming it is a ValueError at module scope that
    takes every test in those files with it. A builder added inside the
    region comes along for free, which is why new ones go there."""
    assert "  // ---------- Today: Shop and Cook ----------" in SHELL_JS
    i = SHELL_JS.index("  // ---------- Today: Shop and Cook ----------")
    region = SHELL_JS[i:SHELL_JS.index("  function tomorrowCardHtml(", i)]
    for name in ("todayMealsCardHtml", "todayMealRowHtml", "todayMealWhoHtml",
                 "todayMealTarget", "todayShopLineHtml", "dayNodeWhyHtml",
                 "COOK_KINDS", "DAY_GROUPS"):
        assert name in region, f"{name} must live inside the lifted region"
