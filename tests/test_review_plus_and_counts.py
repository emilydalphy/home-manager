"""
Three small things Emily settled on 2026-09-10, and the tests for them.

  1. THE REVIEW STEPPER'S "+" ASKS WHICH DAY. Every candidate day already
     holds a dish, so "+" always means REPLACING something — and a rule
     that picks the victim silently is exactly the failure this app keeps
     getting caught by. The strip shows what each day is holding and the
     household taps the one they are willing to spend.
  2. THE APPROVE BUTTON COUNTS ALL FOUR MEAL TYPES. It is a promise that
     nothing is left to decide, and an open snack is something left to
     decide.
  3. THE COOK'S SERVING COUNT IS SAVED WITH THE TICKS. They describe one
     cooking session, so a reload that brought back half-ticked amounts
     nobody chose was not an inconsistency — it was a screen that was
     wrong.

The same split every recent file here draws. BEHAVIOUR for the writes,
against the real functions and the real route; and THE SCREEN'S OWN
FUNCTIONS, RUN, under node against plain objects — not source markers,
because every bug in this batch is a value (a count, a target id, a stored
number) and a test that greps shell.js for the right words cannot see one.
"""
from __future__ import annotations

import datetime
import json
import shutil
import subprocess
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

REPO = Path(__file__).resolve().parent.parent
SHELL_JS = (REPO / "static" / "shell.js").read_text(encoding="utf-8")
SHELL_CSS = (REPO / "static" / "shell.css").read_text(encoding="utf-8")


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


# ------------------------------------- CHANGE 1: the write behind the "+"

def test_the_dish_lands_on_the_day_the_household_picked():
    plan = _plan()
    tools.plan_meal(D0, "Chicken Tacos", slot="dinner", weekly_plan_id=plan)
    tools.plan_meal(D1, "Bean Chili", slot="dinner", weekly_plan_id=plan)

    out = tools.add_dish_day(plan, _ids(D0, "dinner")[0], _ids(D1, "dinner")[0])

    assert out["status"] == "added"
    assert out["dish"] == "Chicken Tacos"
    assert _state(D1, "dinner") == [("planned", "Chicken Tacos")]
    # And the night it was copied FROM is untouched — this adds a day, it
    # does not move one.
    assert _state(D0, "dinner") == [("planned", "Chicken Tacos")]


def test_it_names_what_the_day_was_holding_so_the_screen_can_say_what_it_cost():
    """The household is agreeing to lose something. The toast names it, and
    the name has to come from the row that was actually displaced rather
    than from whatever the screen last drew."""
    plan = _plan()
    tools.plan_meal(D0, "Chicken Tacos", slot="dinner", weekly_plan_id=plan)
    tools.plan_meal(D1, "Bean Chili", slot="dinner", weekly_plan_id=plan)

    out = tools.add_dish_day(plan, _ids(D0, "dinner")[0], _ids(D1, "dinner")[0])
    assert out["replaced"] == "Bean Chili"


def test_filling_an_open_slot_displaces_nothing_and_says_so():
    """An open slot is a question, not a dish — nothing is lost by
    answering it, and the sentence must not invent a loss."""
    plan = _plan()
    tools.plan_meal(D0, "Chicken Tacos", slot="dinner", weekly_plan_id=plan)
    tools.plan_slot_open(plan, D1, "dinner", "I'd rather ask than guess on this one.")

    out = tools.add_dish_day(plan, _ids(D0, "dinner")[0], _ids(D1, "dinner")[0])

    assert out["replaced"] is None
    assert _state(D1, "dinner") == [("planned", "Chicken Tacos")]


def test_a_night_nobody_is_home_is_never_a_day_to_plan_into():
    """
    planned_empty means nobody is home, or the household asked for none of
    that meal. Three separate bugs in this app have come from code reading
    that state as a free plate. Nothing on the screen offers one — and the
    rule holds at the WRITE too, because a control is not where a rule like
    this belongs.
    """
    plan = _plan()
    tools.plan_meal(D0, "Chicken Tacos", slot="dinner", weekly_plan_id=plan)
    tools.plan_slot_empty(plan, D1, "dinner", "You’re out — I’ve planned nothing.")

    with pytest.raises(ValueError):
        tools.add_dish_day(plan, _ids(D0, "dinner")[0], _ids(D1, "dinner")[0])
    assert _state(D1, "dinner") == [("planned_empty", None)]


def test_adding_a_snack_leaves_the_days_other_snack_alone():
    """
    The same hazard the stepper going DOWN shipped with and had fixed in
    review: a day holds TWO rows at slot='snack' by default, so anything
    acting on one of them has to say WHICH. This goes through
    swap_meal_in_plan by entry id for exactly that reason — old_meal cannot
    say it for an OPEN target, which has no meal name at all.
    """
    plan = _plan()
    tools.plan_meal(D0, "Apple and peanut butter", slot="snack", weekly_plan_id=plan)
    tools.plan_meal(D1, "Apple and peanut butter", slot="snack", weekly_plan_id=plan)
    tools.plan_meal(D2, "Trail mix", slot="snack", weekly_plan_id=plan)
    tools.plan_meal(D2, "Greek yogurt", slot="snack", weekly_plan_id=plan)
    trail_mix = _ids(D2, "snack")[0]

    tools.add_dish_day(plan, _ids(D0, "snack")[0], trail_mix)

    # The Greek yogurt beside it survives, unmoved. (The new row is
    # appended, so it sorts after — the point is that there are still two
    # snacks and only the tapped one changed.)
    assert _state(D2, "snack") == [
        ("planned", "Greek yogurt"),
        ("planned", "Apple and peanut butter"),
    ]


def test_an_open_snack_is_filled_without_taking_the_real_one_with_it():
    """The case old_meal genuinely cannot express: an open slot has no
    name, so a by-name replacement would have to pass None and take every
    row in the slot — the two-snacks bug reached from the other side."""
    plan = _plan()
    tools.plan_meal(D0, "Apple and peanut butter", slot="snack", weekly_plan_id=plan)
    tools.plan_meal(D2, "Greek yogurt", slot="snack", weekly_plan_id=plan)
    tools.plan_slot_open(plan, D2, "snack", "You cut Trail mix back, so this one is yours.")
    open_row = _ids(D2, "snack")[1]

    tools.add_dish_day(plan, _ids(D0, "snack")[0], open_row)

    assert _state(D2, "snack") == [
        ("planned", "Greek yogurt"),
        ("planned", "Apple and peanut butter"),
    ]


def test_it_hands_back_the_changed_day_in_the_shape_the_screen_already_reads():
    """get_week_menu's own day dict, exactly as the stepper going down and
    the in-place swap both answer — which is what lets the Review screen
    splice one day into the week it holds instead of refetching."""
    plan = _plan()
    tools.plan_meal(D0, "Chicken Tacos", slot="dinner", weekly_plan_id=plan)
    tools.plan_meal(D1, "Bean Chili", slot="dinner", weekly_plan_id=plan)
    tools.plan_meal(D1, "Oatmeal", slot="breakfast", weekly_plan_id=plan)

    out = tools.add_dish_day(plan, _ids(D0, "dinner")[0], _ids(D1, "dinner")[0])

    assert out["day"]["date"] == D1
    assert out["day"]["dinner"]["title"] == "Chicken Tacos"
    # The rest of the day comes back untouched, not just the slot that moved.
    assert out["day"]["breakfast"]["title"] == "Oatmeal"


def test_a_day_of_a_different_meal_is_refused():
    """The stepper lives inside a meal-type group and every day it offers
    is a day of that same meal. A breakfast dish onto a dinner is a
    different decision, and one nobody made on this screen."""
    plan = _plan()
    tools.plan_meal(D0, "Overnight Oats", slot="breakfast", weekly_plan_id=plan)
    tools.plan_meal(D1, "Bean Chili", slot="dinner", weekly_plan_id=plan)

    with pytest.raises(ValueError):
        tools.add_dish_day(plan, _ids(D0, "breakfast")[0], _ids(D1, "dinner")[0])
    assert _state(D1, "dinner") == [("planned", "Bean Chili")]


def test_an_entry_from_another_week_is_refused_rather_than_quietly_moved():
    """Household- and plan-scoped both, the same rule the in-place swap and
    the stepper going down follow — an id from somewhere else is a refusal,
    not an edit of somebody else's dinner."""
    plan_a = _plan()
    tools.plan_meal(D0, "Chicken Tacos", slot="dinner", weekly_plan_id=plan_a)
    tools.plan_meal(D1, "Bean Chili", slot="dinner", weekly_plan_id=plan_a)
    plan_b = tools.create_weekly_plan(
        (TODAY + datetime.timedelta(days=21)).isoformat()
    )["weekly_plan_id"]

    with pytest.raises(ValueError):
        tools.add_dish_day(plan_b, _ids(D0, "dinner")[0], _ids(D1, "dinner")[0])
    assert _state(D1, "dinner") == [("planned", "Bean Chili")]


def test_the_shopping_list_follows_the_swap_rather_than_carrying_both_dishes():
    """
    The whole reason this composes swap_meal_in_plan instead of writing its
    own removal: reversing the displaced dish's contribution, and buying
    for the one going in, are things that function already does correctly
    on an approved week. A second implementation here is how two paths end
    up disagreeing about one household's shopping list.
    """
    plan = _plan()
    tools.add_recipe("Chicken Tacos", ingredients=[{"item": "Tortillas", "qty": "8"}])
    tools.add_recipe("Bean Chili", ingredients=[{"item": "Kidney beans", "qty": "2 tin"}])
    tools.plan_meal(D0, "Chicken Tacos", slot="dinner", weekly_plan_id=plan)
    tools.plan_meal(D1, "Bean Chili", slot="dinner", weekly_plan_id=plan)
    tools.approve_weekly_plan(plan, approved_by="Emily")
    def names():
        return {i["item"] for i in tools.list_grocery_list()}
    assert "Kidney beans" in names()

    tools.add_dish_day(plan, _ids(D0, "dinner")[0], _ids(D1, "dinner")[0])

    assert "Kidney beans" not in names(), names()
    assert "Tortillas" in names(), names()


def test_the_route_answers_with_the_changed_day(signed_in):
    plan = _plan()
    tools.plan_meal(D0, "Chicken Tacos", slot="dinner", weekly_plan_id=plan)
    tools.plan_meal(D1, "Bean Chili", slot="dinner", weekly_plan_id=plan)

    res = signed_in.post(
        f"/api/week/{WEEK_START}/add-dish-day",
        json={"entry_id": _ids(D0, "dinner")[0],
              "target_entry_id": _ids(D1, "dinner")[0]},
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["status"] == "added"
    assert body["replaced"] == "Bean Chili"
    assert body["day"]["dinner"]["title"] == "Chicken Tacos"


def test_the_route_refuses_a_deliberately_empty_target(signed_in):
    """The 404 has to be the RULE refusing, not FastAPI answering about a
    route that isn't there — a status code alone would have passed on the
    commit before this one, when the route did not exist."""
    plan = _plan()
    tools.plan_meal(D0, "Chicken Tacos", slot="dinner", weekly_plan_id=plan)
    tools.plan_slot_empty(plan, D1, "dinner", "You’re out — I’ve planned nothing.")

    res = signed_in.post(
        f"/api/week/{WEEK_START}/add-dish-day",
        json={"entry_id": _ids(D0, "dinner")[0],
              "target_entry_id": _ids(D1, "dinner")[0]},
    )
    assert res.status_code == 404
    assert "a day to plan into" in res.json()["detail"], res.text
    assert _state(D1, "dinner") == [("planned_empty", None)]


# ------------------- CHANGE 2: does generation make an open snack at all?

def test_the_automatic_passes_never_hand_back_an_open_snack():
    """
    THE QUESTION EMILY ASKED, pinned as a test rather than left as a claim
    in a report. It CHARACTERISES behaviour that is not changed here, so it
    is green on both sides of this commit — deliberately, because the
    answer is what Emily needs and a claim nobody can re-run is not one. Widening the Approve button's count to all four meal types
    would start labelling weeks that previously looked settled IF week
    generation produced open snacks on its own.

    It does not. Every automatic pass that writes an open slot is scoped to
    the three real meals: audit_plan_slots (the generation-gap pass) filters
    to WEEK_SLOTS, repair_leftover_chains skips any row whose slot is not in
    WEEK_SLOTS, and slot_needs' away-reopen is only reachable for the three
    it validates. This drives _finish_week_slots over a week with no snacks
    on it at all and asserts nothing hands one back as a question.

    The one path that CAN produce one is the model itself: submit_weekly_plan
    accepts slot='snack' with slot_state='open', and agent's persist loop
    writes it through unchanged. So an open snack is something the assistant
    can decide to hand back, never something the finishing passes invent.
    """
    from app.agent import _finish_week_slots

    plan = _plan()
    dates = [(TODAY + datetime.timedelta(days=i)).isoformat() for i in range(7)]
    for d in dates:
        for slot in ("breakfast", "lunch", "dinner"):
            tools.plan_meal(d, f"Something for {slot}", slot=slot, weekly_plan_id=plan)

    _finish_week_slots(plan, WEEK_START, None, {}, day_count=7)

    conn = get_conn()
    rows = conn.execute(
        "SELECT slot, slot_state FROM meal_plan_entries WHERE weekly_plan_id = ?", (plan,)
    ).fetchall()
    conn.close()
    open_snacks = [r for r in rows if r["slot"] == "snack" and r["slot_state"] == "open"]
    assert open_snacks == [], open_snacks


# ------------------------------------------- the screen's own functions

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


def _extract_async(name: str, source: str = SHELL_JS) -> str:
    return "async " + _extract(name, source)


def _run_node(harness: str):
    res = subprocess.run(["node", "-e", harness], capture_output=True, text=True, timeout=30)
    assert res.returncode == 0, f"node failed: {res.stderr}"
    return json.loads(res.stdout.strip())


_ESCAPE = _extract("escapeHtml") + "\n"
_SLOT_FURNITURE = (
    "var WEEK_SLOTS = ['breakfast', 'lunch', 'dinner'];\n"
    "var SLOT_LABELS = { breakfast: 'Breakfast', lunch: 'Lunch', dinner: 'Dinner' };\n"
    "function dayName(d, opts){ return { 'MON': 'Monday', 'TUE': 'Tuesday',"
    " 'WED': 'Wednesday', 'THU': 'Thursday' }[d] || d; }\n"
    + _extract("isSnackSlot") + "\n"
    + _extract("snackSlotKey") + "\n"
    + _extract("daySlotEntry") + "\n"
    + _extract("daySlotKeys") + "\n"
    + _extract("slotEyebrowLabel") + "\n"
)


def _planned(title, source="plan", entry_id=1):
    return {"title": title, "state": "planned", "source": source, "entry_id": entry_id}


def _open(entry_id=9):
    return {"title": "I’d like your call on this one", "state": "open",
            "source": "open", "entry_id": entry_id}


def _empty(entry_id=8):
    return {"title": "Out — nothing to cook", "state": "planned_empty",
            "source": "empty", "entry_id": entry_id}


def _day(date, **slots):
    day = {"date": date, "before_plan_start": False, "isPast": False,
           "isToday": False, "snacks": slots.pop("snacks", [])}
    for s in ("breakfast", "lunch", "dinner"):
        day[s] = slots.get(s)
    day["snack"] = (day["snacks"] or [None])[0]
    return day


# ------------------------------------- CHANGE 1: which days the "+" offers

def _options(days, dish):
    harness = (
        _ESCAPE + _SLOT_FURNITURE
        + _extract("mealDisplayName") + "\n"
        + _extract("reviewAddDayOptions") + "\n"
        + f"console.log(JSON.stringify(reviewAddDayOptions({json.dumps(days)},"
          f" {json.dumps(dish)})));\n"
    )
    return _run_node(harness)


_TACOS = {"slot": "dinner", "name": "Chicken Tacos", "cooks": 1,
          "days": [{"date": "MON", "entryId": 1}]}


@_needs_node
def test_the_picker_says_what_each_day_is_currently_holding():
    """The whole point of asking rather than guessing: the household is
    agreeing to displace something, so they have to be shown what."""
    days = [_day("MON", dinner=_planned("Chicken Tacos", entry_id=1)),
            _day("TUE", dinner=_planned("Bean Chili", entry_id=2)),
            _day("WED", dinner=_planned("Sheet Pan Salmon", entry_id=3))]
    out = _options(days, _TACOS)
    assert [(o["date"], o["holding"], o["entryId"]) for o in out] == [
        ("TUE", "Bean Chili", 2), ("WED", "Sheet Pan Salmon", 3)]


@_needs_node
def test_a_day_the_dish_already_covers_is_not_offered_again():
    days = [_day("MON", dinner=_planned("Chicken Tacos", entry_id=1)),
            _day("TUE", dinner=_planned("Bean Chili", entry_id=2))]
    out = _options(days, _TACOS)
    assert [o["date"] for o in out] == ["TUE"]


@_needs_node
def test_a_day_nobody_is_home_is_never_offered():
    """The rule that has already caused three bugs when it was got wrong.
    A planned_empty slot is not a free plate."""
    days = [_day("MON", dinner=_planned("Chicken Tacos", entry_id=1)),
            _day("TUE", dinner=_empty(entry_id=2)),
            _day("WED", dinner=_planned("Bean Chili", entry_id=3))]
    out = _options(days, _TACOS)
    assert [o["date"] for o in out] == ["WED"]


@_needs_node
def test_an_open_slot_is_offered_and_reads_as_a_question_not_a_dish():
    days = [_day("MON", dinner=_planned("Chicken Tacos", entry_id=1)),
            _day("TUE", dinner=_open(entry_id=2))]
    out = _options(days, _TACOS)
    assert out[0]["open"] is True
    assert out[0]["holding"] == "Your call"


@_needs_node
def test_a_day_already_eaten_or_outside_the_plan_is_not_a_day_to_plan_into():
    days = [_day("MON", dinner=_planned("Chicken Tacos", entry_id=1))]
    past = _day("TUE", dinner=_planned("Bean Chili", entry_id=2))
    past["isPast"] = True
    before = _day("WED", dinner=_planned("Sheet Pan Salmon", entry_id=3))
    before["before_plan_start"] = True
    out = _options(days + [past, before], _TACOS)
    assert out == []


@_needs_node
def test_each_snack_of_a_day_is_its_own_choice_and_says_which():
    """A day holds two snacks by default and they are two different
    decisions — displacing the apple is not displacing the yogurt beside
    it. Which is also why the write is by entry id all the way down."""
    dish = {"slot": "snack", "name": "Trail mix", "cooks": 0,
            "days": [{"date": "MON", "entryId": 1}]}
    days = [_day("MON", snacks=[_planned("Trail mix", entry_id=1)]),
            _day("TUE", snacks=[_planned("Apple", entry_id=2),
                                _planned("Greek yogurt", entry_id=3)])]
    out = _options(days, dish)
    assert [(o["holding"], o["eyebrow"], o["entryId"]) for o in out] == [
        ("Apple", "Snack 1", 2), ("Greek yogurt", "Snack 2", 3)]


@_needs_node
def test_a_made_ahead_night_is_offered_under_the_dish_it_actually_is():
    """mealDisplayName, the same rule the rest of this screen counts by —
    a made-ahead night reads as the source dish, not as the whole
    "Made ahead — Sunday's Egg White Bites" sentence."""
    days = [_day("MON", dinner=_planned("Chicken Tacos", entry_id=1)),
            _day("TUE", dinner=dict(
                _planned("Made ahead — Sunday’s Beef Chili", source="leftovers", entry_id=2),
                leftover_from={"date": "SUN", "meal": "Beef Chili", "cook_ahead": True}))]
    out = _options(days, _TACOS)
    assert out[0]["holding"] == "Beef Chili"


# ----------------------------------------- CHANGE 1: the strip as rendered

def _picker(days, dish, idx=0):
    harness = (
        _ESCAPE + _SLOT_FURNITURE
        + _extract_var("REVIEW_SLOT_NOUNS") + "\n"
        + _extract("mealDisplayName") + "\n"
        + _extract("reviewSlotNoun") + "\n"
        + _extract("reviewAddDayOptions") + "\n"
        + _extract("reviewAddPickerHtml") + "\n"
        + f"console.log(JSON.stringify(reviewAddPickerHtml({json.dumps(dish)}, {idx},"
          f" {json.dumps(days)})));\n"
    )
    return _run_node(harness)


@_needs_node
def test_every_offered_day_is_a_tappable_row_carrying_its_own_position():
    days = [_day("MON", dinner=_planned("Chicken Tacos", entry_id=1)),
            _day("TUE", dinner=_planned("Bean Chili", entry_id=2)),
            _day("WED", dinner=_planned("Sheet Pan Salmon", entry_id=3))]
    html = _picker(days, _TACOS)
    assert html.count('data-rv-pick="0"') == 2
    assert 'data-rv-pick-at="0"' in html and 'data-rv-pick-at="1"' in html
    assert "Bean Chili" in html and "Sheet Pan Salmon" in html


@_needs_node
def test_the_strip_carries_no_second_apricot():
    """Rule 5. The screen's one accent belongs to Approve at the foot, and
    a strip of five apricot rows above it would be five more primaries."""
    days = [_day("MON", dinner=_planned("Chicken Tacos", entry_id=1)),
            _day("TUE", dinner=_planned("Bean Chili", entry_id=2))]
    html = _picker(days, _TACOS)
    for accent in ("btn-gold", "apricot", "--apricot"):
        assert accent not in html, accent


@_needs_node
def test_a_week_with_nowhere_left_to_put_it_says_so_rather_than_opening_empty():
    days = [_day("MON", dinner=_planned("Chicken Tacos", entry_id=1)),
            _day("TUE", dinner=_empty(entry_id=2))]
    html = _picker(days, _TACOS)
    assert "no other night" in html
    assert "data-rv-pick=" not in html
    assert "data-rv-pick-cancel" in html


@_needs_node
def test_the_ask_names_the_dish_and_says_plainly_that_it_replaces():
    days = [_day("MON", dinner=_planned("Chicken Tacos", entry_id=1)),
            _day("TUE", dinner=_planned("Bean Chili", entry_id=2))]
    html = _picker(days, _TACOS)
    assert "Chicken Tacos takes its place" in html
    # §8: never apologetic, never cute, no exclamation marks.
    assert "!" not in html
    assert "sorry" not in html.lower()


@_needs_node
def test_the_strip_only_opens_on_the_row_being_asked_about():
    """One picker at a time — two open at once is two half-made decisions
    on one screen."""
    harness = (
        _ESCAPE + _SLOT_FURNITURE
        + _extract_var("REVIEW_GROUP_LABELS") + "\n"
        + _extract_var("REVIEW_SLOT_NOUNS") + "\n"
        + _extract_var("RV_MINUS_SVG") + "\n"
        + _extract_var("RV_PLUS_SVG") + "\n"
        + "var reviewState = { view: 'eating', openDays: {}, busy: null, trouble: '',"
          " picking: 1 };\n"
        + _extract("mealDisplayName") + "\n"
        + _extract("reviewSlotNoun") + "\n"
        + _extract("reviewEatingGroups") + "\n"
        + _extract("reviewCookLine") + "\n"
        + _extract("reviewAddDayOptions") + "\n"
        + _extract("reviewAddPickerHtml") + "\n"
        + _extract("reviewDishRowHtml") + "\n"
        + _extract("reviewEatingHtml") + "\n"
        + "var days = " + json.dumps([
            _day("MON", dinner=_planned("Chicken Tacos", entry_id=1)),
            _day("TUE", dinner=_planned("Bean Chili", entry_id=2)),
            _day("WED", dinner=_planned("Sheet Pan Salmon", entry_id=3)),
        ]) + ";\n"
        + "var html = reviewEatingHtml(days);\n"
        + "console.log(JSON.stringify({ strips: (html.match(/rv-pick-days/g) || []).length,"
          " picking: html.indexOf('is-picking') !== -1,"
          " expanded: (html.match(/aria-expanded=\"true\"/g) || []).length }));\n"
    )
    out = _run_node(harness)
    assert out["strips"] == 1
    assert out["picking"] is True
    assert out["expanded"] == 1


# ------------------------------------- CHANGE 1: the call, run not read

def _run_add(response: dict, status: str = "draft") -> dict:
    days = [_day("MON", dinner=_planned("Chicken Tacos", entry_id=1)),
            _day("TUE", dinner=_planned("Bean Chili", entry_id=2)),
            _day("WED", dinner=_planned("Sheet Pan Salmon", entry_id=3))]
    harness = (
        _ESCAPE + _SLOT_FURNITURE
        + "var calls = [];\n"
        + "var reviewState = { view: 'eating', busy: null, trouble: '', picking: 0,\n"
          "  dishes: [{ slot: 'dinner', name: 'Chicken Tacos', cooks: 1,\n"
          "    days: [{date:'MON',entryId:1}] }] };\n"
        + "var weekState = { data: { week_start_date: '2026-09-07', status: "
        + json.dumps(status) + " }, days: " + json.dumps(days) + " };\n"
        + "function spliceSwappedDay(d){ calls.push('splice'); }\n"
          "function renderMealsStep(){ calls.push('render'); }\n"
          "async function loadWeekMenu(){ calls.push('loadWeekMenu'); }\n"
          "function showToast(t){ calls.push('toast:' + t); }\n"
          "function refreshGrocerySurfaces(){ calls.push('grocery'); }\n"
          "function slotWord(s){ return s; }\n"
          "var fetchBody = null;\n"
          "async function fetch(url, opts){ fetchBody = JSON.parse(opts.body);\n"
          "  calls.push('fetch:' + url);\n"
          "  return { ok: true, json: async () => (" + json.dumps(response) + ") }; }\n"
        + _extract("mealDisplayName") + "\n"
        + _extract("reviewAddDayOptions") + "\n"
        + _extract_async("runAddDishDay") + "\n"
        + "runAddDishDay(null, 0, 1).then(function () {\n"
          "  console.log(JSON.stringify({ calls: calls, body: fetchBody,\n"
          "    trouble: reviewState.trouble, busy: reviewState.busy,\n"
          "    picking: reviewState.picking })); });\n"
    )
    return _run_node(harness)


_ADDED = {"status": "added", "date": "WED", "slot": "dinner",
          "dish": "Chicken Tacos", "replaced": "Sheet Pan Salmon",
          "day": {"date": "WED"}}


@_needs_node
def test_the_pick_posts_the_dish_and_the_day_the_household_actually_tapped():
    """Two ids and no dish name. The second option in the strip is
    Wednesday's Sheet Pan Salmon — the row that was tapped, not whatever
    happens to be sitting at a date and slot by the time this lands."""
    assert _run_add(_ADDED)["body"] == {"entry_id": 1, "target_entry_id": 3}


@_needs_node
def test_the_pick_reloads_the_week_so_the_approve_button_stays_true():
    """The splice updates weekState.days; the Approve button's count reads
    weekState.data.days, which a splice never touches — and this tap can
    settle an open slot, which is exactly what that button promises about.
    Same line the stepper going down carries, for the same reason."""
    out = _run_add(_ADDED)
    assert "loadWeekMenu" in out["calls"], out["calls"]
    assert out["calls"].index("loadWeekMenu") > out["calls"].index("splice")


@_needs_node
def test_the_toast_names_the_day_and_what_it_cost():
    toasts = [c for c in _run_add(_ADDED)["calls"] if c.startswith("toast:")]
    assert len(toasts) == 1, toasts
    assert "Wednesday" in toasts[0]
    assert "in place of Sheet Pan Salmon" in toasts[0]


@_needs_node
def test_answering_an_open_slot_does_not_claim_something_was_lost():
    filled = dict(_ADDED, replaced=None)
    toasts = [c for c in _run_add(filled)["calls"] if c.startswith("toast:")]
    assert "in place of" not in toasts[0], toasts


@_needs_node
def test_the_strip_closes_once_the_choice_has_landed():
    out = _run_add(_ADDED)
    assert out["picking"] is None
    assert out["busy"] is None


@_needs_node
def test_a_failed_add_says_nothing_changed_and_leaves_the_week_alone():
    harness_out = _run_add_failing()
    assert "nothing changed" in harness_out["trouble"]
    assert "splice" not in harness_out["calls"], harness_out["calls"]
    assert harness_out["busy"] is None


def _run_add_failing() -> dict:
    days = [_day("MON", dinner=_planned("Chicken Tacos", entry_id=1)),
            _day("TUE", dinner=_planned("Bean Chili", entry_id=2)),
            _day("WED", dinner=_planned("Sheet Pan Salmon", entry_id=3))]
    harness = (
        _ESCAPE + _SLOT_FURNITURE
        + "var calls = [];\n"
          "var console_warn = console.warn; console.warn = function(){};\n"
        + "var reviewState = { view: 'eating', busy: null, trouble: '', picking: 0,\n"
          "  dishes: [{ slot: 'dinner', name: 'Chicken Tacos', cooks: 1,\n"
          "    days: [{date:'MON',entryId:1}] }] };\n"
        + "var weekState = { data: { week_start_date: '2026-09-07', status: 'draft' },"
          " days: " + json.dumps(days) + " };\n"
        + "function spliceSwappedDay(d){ calls.push('splice'); }\n"
          "function renderMealsStep(){ calls.push('render'); }\n"
          "async function loadWeekMenu(){ calls.push('loadWeekMenu'); }\n"
          "function showToast(t){ calls.push('toast:' + t); }\n"
          "function refreshGrocerySurfaces(){ calls.push('grocery'); }\n"
          "function slotWord(s){ return s; }\n"
          "async function fetch(){ return { ok: false }; }\n"
        + _extract("mealDisplayName") + "\n"
        + _extract("reviewAddDayOptions") + "\n"
        + _extract_async("runAddDishDay") + "\n"
        + "runAddDishDay(null, 0, 1).then(function () {\n"
          "  console.log(JSON.stringify({ calls: calls, trouble: reviewState.trouble,\n"
          "    busy: reviewState.busy })); });\n"
    )
    return _run_node(harness)


@_needs_node
def test_an_approved_weeks_grocery_surfaces_are_refreshed_and_a_drafts_are_not():
    assert "grocery" in _run_add(_ADDED, status="approved")["calls"]
    assert "grocery" not in _run_add(_ADDED, status="draft")["calls"]


# ------------------------------ CHANGE 2: the Approve button's own count

def _count(days):
    harness = (
        _SLOT_FURNITURE
        + _extract("countOpenSlots") + "\n"
        + _extract("approveWithOpenLabel") + "\n"
        + "var data = { days: " + json.dumps(days) + " };\n"
        + "var n = countOpenSlots(data);\n"
        + "console.log(JSON.stringify({ n: n, label: n ? approveWithOpenLabel(data, n) : '' }));\n"
    )
    return _run_node(harness)


@_needs_node
def test_an_open_snack_is_something_left_to_decide():
    """
    Emily, 2026-09-10. The button is a promise that nothing is left to
    decide, and before this it counted three meal types out of four — so
    "Which days" said `SNACK 2 · Your call` while the button read "Approve
    and build my shopping list". The screen knew and the button did not.
    """
    days = [_day("MON", dinner=_planned("Chicken Tacos"),
                 snacks=[_planned("Apple", entry_id=5), _open(entry_id=6)])]
    assert _count(days)["n"] == 1


@_needs_node
def test_one_open_snack_is_named_by_its_day():
    days = [_day("MON", dinner=_planned("Chicken Tacos")),
            _day("TUE", snacks=[_open(entry_id=6)])]
    assert _count(days)["label"] == "Approve — leave Tuesday open"


@_needs_node
def test_several_open_slots_across_all_four_meal_types_are_counted_together():
    days = [_day("MON", breakfast=_open(entry_id=1),
                 snacks=[_planned("Apple", entry_id=2), _open(entry_id=3)]),
            _day("TUE", dinner=_open(entry_id=4)),
            _day("WED", lunch=_open(entry_id=5))]
    out = _count(days)
    assert out["n"] == 4
    assert out["label"] == "Approve — leave 4 slots open"


@_needs_node
def test_a_deliberately_empty_snack_is_still_not_a_decision():
    """NO-REGRESSION GUARD — it passes on the commit before this one too,
    and that is the point. Widening the count to four meal types must not
    widen it to four STATES. planned_empty is a night nobody is home, or a meal the
    household asked for none of — nothing to settle either way."""
    days = [_day("MON", snacks=[_empty(entry_id=1)]), _day("TUE", dinner=_empty(entry_id=2))]
    assert _count(days)["n"] == 0


@_needs_node
def test_a_settled_week_still_counts_nothing():
    """The other no-regression guard, and it also passes before the change:
    a week with nothing open must not start naming a slot now that snacks
    are counted."""
    days = [_day("MON", breakfast=_planned("Oats"), lunch=_planned("Soup"),
                 dinner=_planned("Chicken Tacos"),
                 snacks=[_planned("Apple", entry_id=5), _planned("Yogurt", entry_id=6)])]
    assert _count(days)["n"] == 0


def test_week_slots_is_still_exactly_the_three_real_meals():
    """
    NO-REGRESSION GUARD, green on both sides. The scope that did NOT
    change, pinned because the temptation is to fix
    the button by adding 'snack' to WEEK_SLOTS. That constant is the
    21-slot guarantee and the definition of a cook; weekCountsLabel,
    classifyDay, reviewDayIsClosed and reviewClosedLine all read it, and a
    snack counted as a cook is a different bug in four places.
    """
    assert "var WEEK_SLOTS = ['breakfast', 'lunch', 'dinner'];" in SHELL_JS
    assert "'snack'" not in _extract_var("WEEK_SLOTS")
