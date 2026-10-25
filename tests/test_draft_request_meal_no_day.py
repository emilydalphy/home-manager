"""
Emily's snag rule 2 (2026-09-23, "When your requests and your week don't
line up"): "A request with a meal but no day goes at that meal on a day
with enough time and everyone home. Dishes over 30 minutes stay off
short-on-time nights." Her example: "Tacos for dinner once this week."

No line was decided for this rule, so it is a silent placement repair
(cap_enforce.place_meal_requests).

WHAT MAIN DID BEFORE THIS, measured through generation with the model
stubbed: the prompt tells the model, and nothing checked. A typed dish is
`theirs`, which makes it untouchable to the cap pass, so:
  * tacos the model put on a night Vineeth is out stayed there;
  * a 40-minute dish on a rush night stayed there, and the draft said
    "Tacos takes 40 minutes, and Wednesday is short on time." about a
    Wednesday nobody chose.

Same fixtures as tests/test_draft_request_on_day_out.py: the model and the
re-pick are stubbed (test_rush_cap_enforced's `stub_model` / `run`),
everything else is the real generation.
"""
from __future__ import annotations

import datetime

from app import tools
from app.db import get_conn
from app.tools import cap_enforce, draft_flags

from conftest import household_today
from test_rush_cap_enforced import (  # noqa: E402 — the fixtures this is about
    _dinners, _filler, _recipe, _slot,
    capped, picker, run, stub_model,  # noqa: F401 — pytest fixtures
)


def _monday(offset_weeks: int = 1) -> str:
    today = household_today()
    monday = today - datetime.timedelta(days=today.weekday())
    return (monday + datetime.timedelta(days=7 * offset_weeks)).isoformat()


def _asked(span: str) -> dict:
    return {"derived_from": {"freeform": span}}


_OTHERS = ["Eggs A", "Eggs B", "Eggs C", "Eggs D", "Eggs E", "Eggs F", "Eggs G"]


def _week(dates, dinners, lunches=None, **per_slot):
    """Distinct dishes everywhere (a repeat would be folded into a chain);
    `per_slot` keys are "{date}:{slot}"."""
    lunches = lunches or [f"Wrap {i}" for i in range(len(dates))]
    out = []
    for i, d in enumerate(dates):
        out.append(_slot(d, "breakfast", "Oats"))
        out.append(_slot(d, "lunch", lunches[i], **(per_slot.get(f"{d}:lunch") or {})))
        out.append(_slot(d, "dinner", dinners[i], **(per_slot.get(f"{d}:dinner") or {})))
        out.append(_slot(d, "snack", "Apple"))
    return out


def _seed(*dishes):
    _filler()
    for name, minutes in dishes:
        _recipe(name, minutes)
    for name in _OTHERS:
        _recipe(name, 10)
    for i in range(7):
        _recipe(f"Wrap {i}", 5)


def _where(plan_id, slot, meal):
    conn = get_conn()
    rows = conn.execute(
        """SELECT mpe.date, mpe.slot FROM meal_plan_entries mpe LEFT JOIN recipes r ON r.id = mpe.recipe_id
           WHERE mpe.weekly_plan_id = ? AND COALESCE(r.name, mpe.freeform_meal) = ?
             AND mpe.component_category IS NULL ORDER BY mpe.date""",
        (plan_id, meal)).fetchall()
    conn.close()
    return [(r["date"], r["slot"]) for r in rows]


def _two_adults():
    tools.add_member("Emily")
    tools.add_member("Vineeth")


def test_a_dinner_request_on_a_night_someone_is_out_moves_to_a_night_everyone_is_home(stub_model, run):
    """
    CATCH (main: the tacos stay on Tuesday, with Vineeth out). Monday and
    Wednesday are both one night away and everyone is home for both: a tie
    goes to the later night.
    """
    _two_adults()
    week = _monday()
    dates = tools._week_dates(week)
    _seed(("Tacos", 25))
    said = "Tacos for dinner once this week"
    tools.save_week_intake(week, freeform=said)
    tools.set_member_attendance(dates[1], "dinner", "Vineeth", present=False)
    dinners = list(_OTHERS)
    dinners[1] = "Tacos"
    stub_model(_week(dates, dinners, **{f"{dates[1]}:dinner": _asked(said)}))

    plan_id, _ = run(week, None)

    assert _where(plan_id, "dinner", "Tacos") == [(dates[2], "dinner")]
    # The dish it traded with is on Tuesday instead — moved, never dropped.
    assert _where(plan_id, "dinner", "Eggs C") == [(dates[1], "dinner")]
    assert [f["text"] for f in draft_flags.plan_flags(plan_id)] == [], "no line was decided for rule 2"


def test_a_dish_over_30_minutes_stays_off_a_short_on_time_night(stub_model, run):
    """
    CATCH (main: the 40-minute tacos stay on the rush Wednesday, with
    "Tacos takes 40 minutes, and Wednesday is short on time." said about a
    night nobody chose). Thursday is rush too, so the nearest night with
    the time is Tuesday.
    """
    week = _monday()
    dates = tools._week_dates(week)
    _seed(("Tacos", 40))
    said = "Tacos for dinner once this week"
    tools.save_week_intake(week, night_tags={dates[2]: ["rush"], dates[3]: ["rush"]}, freeform=said)
    dinners = list(_OTHERS)
    dinners[2] = "Tacos"
    stub_model(_week(dates, dinners, **{f"{dates[2]}:dinner": _asked(said)}))

    plan_id, _ = run(week, None)

    assert _where(plan_id, "dinner", "Tacos") == [(dates[1], "dinner")]
    assert [f["text"] for f in draft_flags.plan_flags(plan_id)] == []


def test_a_lunch_request_stays_a_lunch(stub_model, run):
    """
    CATCH (main: the soup stays on the Monday lunch Vineeth is out of).
    It moves to the nearest lunch everyone is at — never to a dinner.
    """
    _two_adults()
    week = _monday()
    dates = tools._week_dates(week)
    _seed(("Lentil Soup", 15))
    said = "Lentil soup for lunch one day"
    tools.save_week_intake(week, freeform=said)
    tools.set_member_attendance(dates[0], "lunch", "Vineeth", present=False)
    lunches = [f"Wrap {i}" for i in range(7)]
    lunches[0] = "Lentil Soup"
    stub_model(_week(dates, list(_OTHERS), lunches, **{f"{dates[0]}:lunch": _asked(said)}))

    plan_id, _ = run(week, None)

    assert _where(plan_id, "lunch", "Lentil Soup") == [(dates[1], "lunch")]


def test_a_request_that_named_a_day_stays_where_they_put_it(stub_model, run):
    """GUARD: a named day outranks a time limit — scenario 3 keeps it and
    says so. And a dish on a night that already suits it does not move."""
    week = _monday()
    dates = tools._week_dates(week)
    _seed(("Tacos", 40), ("Pasta", 20))
    tools.save_week_intake(week, night_tags={dates[2]: ["rush"]},
                           freeform="Tacos for dinner on Wednesday. Pasta for dinner sometime")
    dinners = list(_OTHERS)
    dinners[2], dinners[4] = "Tacos", "Pasta"
    stub_model(_week(dates, dinners, **{f"{dates[2]}:dinner": _asked("Tacos for dinner on Wednesday"),
                                        f"{dates[4]}:dinner": _asked("Pasta for dinner sometime")}))

    plan_id, _ = run(week, None)

    assert _where(plan_id, "dinner", "Tacos") == [(dates[2], "dinner")]
    assert _where(plan_id, "dinner", "Pasta") == [(dates[4], "dinner")]
    assert [f["text"] for f in draft_flags.plan_flags(plan_id)] == [
        "Tacos takes 40 minutes, and Wednesday is short on time."]


def test_never_traded_onto_a_dish_they_asked_for(stub_model, run):
    """GUARD on the priority list: their tacos never displace their pizza,
    even when the pizza's night is the nearest with everyone home."""
    _two_adults()
    week = _monday()
    dates = tools._week_dates(week)
    _seed(("Tacos", 25), ("Pizza", 25))
    said = "Tacos for dinner once this week"
    tools.save_week_intake(week, freeform=f"{said}. Pizza on Wednesday")
    tools.set_member_attendance(dates[1], "dinner", "Vineeth", present=False)
    dinners = list(_OTHERS)
    dinners[1], dinners[2] = "Tacos", "Pizza"
    stub_model(_week(dates, dinners, **{f"{dates[1]}:dinner": _asked(said),
                                        f"{dates[2]}:dinner": _asked("Pizza on Wednesday")}))

    plan_id, _ = run(week, None)

    assert _where(plan_id, "dinner", "Pizza") == [(dates[2], "dinner")]
    assert _where(plan_id, "dinner", "Tacos") == [(dates[0], "dinner")]


# ---------------------------------------------------------------- review, 2026-10-25

def test_a_dinner_request_rescued_off_a_night_nobody_is_home_goes_where_everyone_is(stub_model, run):
    """CATCH (review: rule 8 took the tacos off the Thursday nobody is home
    to Friday — nearest, tie later — where Vineeth is out, and rule 2 then
    left a rule-8 row alone). Wednesday has everyone home."""
    _two_adults()
    week = _monday()
    dates = tools._week_dates(week)
    _seed(("Tacos", 25))
    said = "Tacos for dinner once this week"
    tools.save_week_intake(week, freeform=said)
    for who in ("Emily", "Vineeth"):
        tools.set_member_attendance(dates[3], "dinner", who, present=False)
    tools.set_member_attendance(dates[4], "dinner", "Vineeth", present=False)
    dinners = list(_OTHERS)
    dinners[3] = "Tacos"
    stub_model(_week(dates, dinners, **{f"{dates[3]}:dinner": _asked(said)}))

    plan_id, _ = run(week, None)

    assert _where(plan_id, "dinner", "Tacos") == [(dates[2], "dinner")]
    assert [f["text"] for f in draft_flags.plan_flags(plan_id)] == [], "no day was named, so nothing is said"


def test_another_dishs_meal_word_in_a_shared_span_is_not_this_dishs(stub_model, run):
    """CATCH (review: both dishes cited the whole sentence, so the tacos'
    "for dinner" made the pasta a dinner request and it was moved off
    Vineeth's night out). Pasta named no meal: it stays."""
    _two_adults()
    week = _monday()
    dates = tools._week_dates(week)
    _seed(("Pasta", 20), ("Tacos", 20))
    said = "Pasta sometime, and tacos for dinner"
    tools.save_week_intake(week, freeform=said)
    tools.set_member_attendance(dates[1], "dinner", "Vineeth", present=False)
    dinners = list(_OTHERS)
    dinners[1], dinners[4] = "Pasta", "Tacos"
    stub_model(_week(dates, dinners, **{f"{dates[1]}:dinner": _asked(said), f"{dates[4]}:dinner": _asked(said)}))

    plan_id, _ = run(week, None)

    assert _where(plan_id, "dinner", "Pasta") == [(dates[1], "dinner")]
    assert cap_enforce.meals_named({"freeform": "Lasagna for the dinner party"}, (), "Lasagna") == set()
