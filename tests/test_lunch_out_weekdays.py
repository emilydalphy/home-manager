"""
"Sam eats lunch out on weekdays" (Loop Board, Phase 1, Emily's decisions of
2026-10-06):

  1. Setup's Weekday lunches screen asks each person At home / Packed / Out.
     One tap writes that person's Monday-Friday lunch cells on the Who's
     eating grid; Packed is also the Cold packed need.
  2. The day card names the meal ("Sam · lunch out") with "Dinner for 3,
     lunch for 2" under it, never "SAM OUT", when somebody is out for one
     meal and home for another. Out for every meal keeps "Sam out".

The page's own functions run under node, lifted the way the other
onboarding and Plan tests lift them.
"""
from __future__ import annotations

import json

import test_onboarding_regrouped as _regrouped
import test_plan_cards_2026_09_18 as _cards

_needs_node, _fn, _const, _run = _regrouped._needs_node, _regrouped._fn, _regrouped._const, _regrouped._run


# ---------- 1. the lunch step ----------

def _harness(grid_lunch, members=("Dana", "Sam", "Leo")):
    return """
const UW_WEEKDAYS = ['monday', 'tuesday', 'wednesday', 'thursday', 'friday', 'saturday', 'sunday'];
let usualGrid = { lunch: %s };
let lunchNeeds = {};
let lunchHome = {};
let lunchOutDays = {};
function currentMembers() { return %s.map(n => ({ name: n, age_group: 'adult' })); }
""" % (json.dumps(grid_lunch), json.dumps(list(members))) + _const("LUNCH_NEED_OPTIONS") + _const("LUNCH_NEED_DAYS") \
        + _const("LUNCH_WHERE_OPTIONS") + "".join(_fn(n) for n in (
            "uwCleanCell", "uwApplyPick", "uwIsOn", "uwWeekdayLunchOn", "lunchPeople", "lunchEntry",
            "lunchNeedsAnswered", "toggleNeed", "lunchNeedsPayload", "lunchStepPeople", "lunchOutHere",
            "lunchWhere", "lunchSetWhere"))


@_needs_node
def test_the_three_chips_are_emilys_words():
    opts = _const("LUNCH_WHERE_OPTIONS")
    assert [w for w in ("'At home'", "'Packed'", "'Out'") if w in opts] == ["'At home'", "'Packed'", "'Out'"]


@_needs_node
def test_out_takes_one_person_off_weekday_lunches_and_at_home_puts_them_back():
    out = _run(_harness(["all"] * 7) + """
const r = {};
lunchSetWhere('Sam', 'out');
r.out = { grid: usualGrid.lunch.slice(), where: lunchWhere('Sam'), rows: lunchStepPeople(),
  eating: lunchPeople(), stillAsked: uwWeekdayLunchOn(usualGrid) || lunchOutHere() };
lunchSetWhere('Sam', 'home');
r.home = { grid: usualGrid.lunch.slice(), where: lunchWhere('Sam'), here: lunchOutHere() };
console.log(JSON.stringify(r));
""")
    # Monday-Friday without Sam; the weekend is not this question's to touch.
    assert out["out"]["grid"] == [["Dana", "Leo"]] * 5 + ["all", "all"]
    assert out["out"]["where"] == "out"
    # Still a row (so it can be changed back), but no lunch to plan.
    assert out["out"]["rows"] == ["Dana", "Sam", "Leo"]
    assert out["out"]["eating"] == ["Dana", "Leo"]
    assert out["home"]["grid"] == ["all"] * 7
    assert out["home"]["where"] == "home"
    assert out["home"]["here"] is False


@_needs_node
def test_packed_is_the_cold_packed_need_and_at_home_takes_it_back_off():
    out = _run(_harness(["all"] * 7) + """
const r = {};
lunchSetWhere('Leo', 'packed');
r.packed = { where: lunchWhere('Leo'), payload: lunchNeedsPayload() };
lunchEntry('Leo').needs = toggleNeed(lunchEntry('Leo').needs, 'nut_free');
lunchSetWhere('Leo', 'home');
r.home = { where: lunchWhere('Leo'), needs: lunchEntry('Leo').needs };
// Tapping the Cold packed need itself lights Packed: one answer, two doors.
lunchEntry('Dana').needs = toggleNeed([], 'cold_packed');
r.dana = lunchWhere('Dana');
console.log(JSON.stringify(r));
""")
    assert out["packed"]["where"] == "packed"
    assert out["packed"]["payload"] == {"Leo": {"needs": ["cold_packed"], "days": {}}}
    assert out["home"] == {"where": "home", "needs": ["nut_free"]}
    assert out["dana"] == "packed"


@_needs_node
def test_out_payload_drops_the_person_and_their_needs():
    out = _run(_harness(["all"] * 7) + """
lunchSetWhere('Sam', 'packed');
lunchSetWhere('Sam', 'out');
console.log(JSON.stringify({ payload: lunchNeedsPayload() }));
""")
    assert out["payload"] is None


@_needs_node
def test_everyone_out_keeps_the_screen_and_back_never_invents_a_lunch_day():
    # Wednesday was never a lunch day; a household of one goes Out, then
    # back to At home. The screen stays in the flow while it is the reason
    # lunch is off (else Continue would look itself up in a flow it left).
    grid = ["all", "all", "off", "all", "all", "off", "off"]
    out = _run(_harness(grid, members=("Sam",)) + """
lunchSetWhere('Sam', 'out');
const off = { grid: usualGrid.lunch.slice(), on: uwWeekdayLunchOn(usualGrid), here: lunchOutHere() };
lunchSetWhere('Sam', 'home');
console.log(JSON.stringify({ off: off, back: usualGrid.lunch.slice() }));
""")
    assert out["off"]["grid"] == ["off"] * 7
    assert out["off"]["on"] is False and out["off"]["here"] is True
    assert out["back"] == grid


def test_step_flow_keeps_the_lunch_screen_while_out_is_why_lunch_is_off():
    flow = _fn("stepFlow")
    assert "lunchOutHere()" in flow
    # Out people are pruned with every other name-keyed answer.
    assert "lunchHome, lunchOutDays" in _fn("pruneMemberKeyedAnswers")


# ---------- 2. the day card ----------

_TUE = "2026-09-22"


def _e(title, **kw):
    e = {"title": title, "state": "planned", "source": "plan", "meta": "25 min", "entry_id": 1, "cooked": False}
    e.update(kw)
    return e


def _card(day):
    return _cards._run(_cards._prelude() + f"""
var day = {json.dumps(day)};
console.log(JSON.stringify({{ tags: reviewTileTags(day), html: wkDayCardHtml(day, 0, {{}}) }}));""")


@_needs_node
def test_lunch_out_names_the_meal_and_counts_each_meal():
    day = {"date": _TUE, "isToday": False, "isPast": False,
           "breakfast": _e("Oats", entry_id=1),
           "lunch": _e("Wraps", entry_id=2, away_names=["Sam"], present_names=["Dana", "Leo"], serves=2),
           "dinner": _e("Chicken and rice bowls", entry_id=3)}
    out = _card(day)
    assert out["tags"] == ["Sam · lunch out"]
    assert "Sam out" not in out["html"]
    assert '<p class="wk-card-heads">Dinner for 3, lunch for 2</p>' in out["html"]


@_needs_node
def test_out_for_every_meal_still_reads_out_and_has_no_count_line():
    day = {"date": _TUE, "isToday": False, "isPast": False,
           "breakfast": _e("Oats", entry_id=1, away_names=["Sam"], present_names=["Dana", "Leo"]),
           "lunch": _e("Wraps", entry_id=2, away_names=["Sam"], present_names=["Dana", "Leo"]),
           "dinner": _e("Bowls", entry_id=3, away_names=["Sam"], present_names=["Dana", "Leo"])}
    out = _card(day)
    assert out["tags"] == ["Sam out"]
    assert "wk-card-heads" not in out["html"]


@_needs_node
def test_two_people_out_for_the_same_meal_share_one_tag_and_breakfast_off_is_not_a_meal():
    day = {"date": _TUE, "isToday": False, "isPast": False,
           "breakfast": {"title": None, "state": "planned_empty", "meal_off": True, "source": "empty"},
           "lunch": _e("Wraps", entry_id=2, away_names=["Sam", "Leo"], present_names=["Dana"]),
           "dinner": _e("Bowls", entry_id=3)}
    out = _card(day)
    assert out["tags"] == ["Sam and Leo · lunch out"]
    assert "Dinner for 3, lunch for 1" in out["html"]
