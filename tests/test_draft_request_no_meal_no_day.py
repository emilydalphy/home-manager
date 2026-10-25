"""
Emily's snag rule 1 (2026-09-23, "When your requests and your week don't
line up", Decision A): "A request with no meal and no day goes at lunch or
dinner, whichever the dish suits, on a day with enough time. Pomona says
where it went." Her line: "Korean chicken pancake is Saturday dinner. It
takes about 30 minutes."

WHAT MAIN DID BEFORE THIS, measured through generation with the model
stubbed: the model picked, and nothing checked. A typed dish is `theirs`,
untouchable to the cap pass, so a 30-minute pancake the model put on a
20-minute Wednesday stayed there — and the draft said "…Wednesday is short
on time." about a Wednesday nobody chose. Where it landed was never said.

The app has no record of which meal a dish suits, so dinner is the default
and a dish stays a lunch only when the model already put it at lunch
(cap_enforce.place_meal_requests, the rule-2 pass, widened).

Same fixtures as tests/test_draft_request_meal_no_day.py.
"""
from __future__ import annotations

from app import tools
from app.tools import cap_enforce, draft_flags

from test_draft_request_meal_no_day import _OTHERS, _asked, _monday, _seed, _week, _where
from test_rush_cap_enforced import capped, picker, run, stub_model  # noqa: F401 — pytest fixtures

SAID = "I want to make a Korean chicken pancake for a meal"
DISH = "Korean Chicken Pancake"


def _lines(plan_id):
    return [f["text"] for f in draft_flags.plan_flags(plan_id)]


def test_a_dish_with_no_meal_and_no_day_moves_to_a_night_with_the_time_and_says_where(
        capped, stub_model, run):
    """
    CATCH (main: the pancake stays on the 20-minute Wednesday with
    "…Wednesday is short on time." said about a night nobody chose).
    Saturday is the nearest night with 30 minutes; the dish there is
    Pomona's, and it trades back onto Wednesday.
    """
    week = _monday()
    dates = tools._week_dates(week)
    _seed((DISH, 30))
    tools.save_week_intake(week, freeform=SAID)
    dinners = list(_OTHERS)
    dinners[2] = DISH
    stub_model(_week(dates, dinners, **{f"{dates[2]}:dinner": _asked(SAID)}))

    plan_id, _ = run(week, None)

    assert _where(plan_id, "dinner", DISH) == [(dates[5], "dinner")]
    assert _where(plan_id, "dinner", "Eggs F") == [(dates[2], "dinner")]
    assert _lines(plan_id) == ["Korean Chicken Pancake is Saturday dinner. It takes about 30 minutes."]


def test_a_dish_already_where_it_suits_stays_and_is_still_said(capped, stub_model, run):
    """CATCH (main: nothing said). Decision A: Pomona says where it went,
    even when where the model put it was already right."""
    week = _monday()
    dates = tools._week_dates(week)
    _seed((DISH, 30))
    tools.save_week_intake(week, freeform=SAID)
    dinners = list(_OTHERS)
    dinners[6] = DISH
    stub_model(_week(dates, dinners, **{f"{dates[6]}:dinner": _asked(SAID)}))

    plan_id, _ = run(week, None)

    assert _where(plan_id, "dinner", DISH) == [(dates[6], "dinner")]
    assert _lines(plan_id) == ["Korean Chicken Pancake is Sunday dinner. It takes about 30 minutes."]


def test_a_dish_the_model_put_at_lunch_stays_a_lunch_with_the_time_for_it(stub_model, run):
    """CATCH: a 35-minute soup on a 20-minute Tuesday lunch goes to the
    nearest lunch with no limit — Saturday — never to a dinner."""
    week = _monday()
    dates = tools._week_dates(week)
    _seed(("Lentil Soup", 35))
    said = "Lentil soup would be nice"
    tools.save_week_intake(week, freeform=said)
    lunches = [f"Wrap {i}" for i in range(7)]
    lunches[1] = "Lentil Soup"
    stub_model(_week(dates, list(_OTHERS), lunches, **{f"{dates[1]}:lunch": _asked(said)}))

    plan_id, _ = run(week, None)

    assert _where(plan_id, "lunch", "Lentil Soup") == [(dates[5], "lunch")]
    assert _lines(plan_id) == ["Lentil Soup is Saturday lunch. It takes about 35 minutes."]


def test_a_dish_that_fits_no_night_gets_rule_5s_line_and_only_that(capped, stub_model, run):
    """GUARD: no double line. Over every night, it is rule 5's — the
    longest night (Saturday and Sunday tie at 30; the earlier), and its
    line — not rule 1's as well."""
    week = _monday()
    dates = tools._week_dates(week)
    _seed(("Beef Braise", 90))
    said = "A beef braise would be lovely"
    tools.save_week_intake(week, night_tags={dates[5]: ["rush"], dates[6]: ["rush"]}, freeform=said)
    dinners = list(_OTHERS)
    dinners[1] = "Beef Braise"
    stub_model(_week(dates, dinners, **{f"{dates[1]}:dinner": _asked(said)}))

    plan_id, _ = run(week, None)

    assert _where(plan_id, "dinner", "Beef Braise") == [(dates[5], "dinner")]
    assert _lines(plan_id) == ["Beef Braise takes 90 minutes, and your longest night is Saturday, so it's there."]


def test_only_positive_evidence_of_no_meal_and_no_day_counts():
    """GUARD on the detector: a meal word, a day, "tonight" or "one night"
    in their words for the dish is not rule 1; a dish nobody's words name
    is not either, and nor is a mood the model cited on a dish."""
    asked = cap_enforce.asked_no_meal_no_day
    assert asked({"freeform": SAID}, (SAID,), DISH)
    assert not asked({"freeform": "Korean chicken pancake for dinner"}, (), DISH)
    assert not asked({"freeform": "Korean chicken pancake on Saturday"}, (), DISH)
    assert not asked({"freeform": "Korean chicken pancake one night"}, (), DISH)
    assert not asked({"freeform": "Korean chicken pancake tonight"}, (), DISH)
    assert not asked({"freeform": "Something Asian, and tacos"}, (), DISH)
    assert not asked({"freeform": "Something Mexican"}, (), "Mexican Rice Bowl")
