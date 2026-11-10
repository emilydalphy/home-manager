"""
Emily's snag rule 7 (decided 2026-11-09): "Two requests on the same day:
fit both across that day's meals first (fish lunch, pizza dinner),
flagged. If the same meal was named for both, or a dish doesn't suit
lunch, the more specific request keeps the slot and the other moves to the
nearest fitting meal, flagged." (cap_enforce.place_two_asks_one_day; the
line is draft_flags.TWO_ASKS_ONE_DAY.)

WHAT MAIN DID BEFORE THIS, through generation with the model stubbed:
  * "Fish tacos and pizza on Friday" — the clause split gave Friday to the
    pizza alone, so the fish tacos stayed on the Thursday the model put
    them on, and nothing was said;
  * "Lasagna and pizza for Friday dinner" — the pizza stayed on Wednesday
    (Friday held the lasagna, theirs, so rule 3 could not trade) and the
    draft said "Pizza is on Wednesday, not Friday — I couldn’t free up
    Friday for it.", which is not where the nearest Friday-ish dinner is.

Same fixtures as tests/test_draft_request_meal_no_day.py: the model and the
re-pick are stubbed, everything else is the real generation.
"""
from __future__ import annotations

from app import tools
from app.tools import draft_flags

from test_draft_request_meal_no_day import _OTHERS, _asked, _monday, _seed, _week, _where
from test_rush_cap_enforced import capped, picker, run, stub_model  # noqa: F401 — pytest fixtures


def _lines(plan_id):
    return [f["text"] for f in draft_flags.plan_flags(plan_id)]


def _audit_clean(plan_id):
    audit = tools.audit_plan_slots(plan_id)
    assert not audit.get("duplicated"), audit
    assert not audit.get("missing"), audit


def test_two_dishes_on_one_day_are_fitted_across_its_lunch_and_dinner(stub_model, run):
    """
    CATCH (main: Fish Tacos stay on Thursday dinner, nothing said). Fish
    Tacos take 20 minutes — inside the weekday lunch's 20 — so they are
    Friday lunch; Pizza is Friday dinner. Thursday's dinner is planned
    again, not left a gap.
    """
    week = _monday()
    dates = tools._week_dates(week)
    _seed(("Fish Tacos", 20), ("Pizza", 30))
    said = "Fish tacos and pizza on Friday"
    tools.save_week_intake(week, freeform=said)
    dinners = list(_OTHERS)
    dinners[3], dinners[4] = "Fish Tacos", "Pizza"
    stub_model(_week(dates, dinners, **{f"{dates[3]}:dinner": _asked(said),
                                        f"{dates[4]}:dinner": _asked(said)}))

    plan_id, _ = run(week, None)

    assert _where(plan_id, "lunch", "Fish Tacos") == [(dates[4], "lunch")]
    assert _where(plan_id, "dinner", "Pizza") == [(dates[4], "dinner")]
    assert _lines(plan_id) == [
        "You asked for Fish Tacos and Pizza on Friday, so Fish Tacos is lunch and Pizza is dinner."]
    _audit_clean(plan_id)


def test_the_same_meal_named_for_both_the_first_keeps_it_and_the_other_moves(stub_model, run):
    """
    CATCH (main: Pizza stays on Wednesday, said as "couldn't free up
    Friday"). Equally specific, so the one asked for first (Lasagna)
    keeps Friday dinner; Pizza goes to the nearest dinner — Thursday and
    Saturday are both one night away, and a tie goes to the later night.
    The dinner Saturday held is traded back, never dropped.
    """
    week = _monday()
    dates = tools._week_dates(week)
    _seed(("Lasagna", 30), ("Pizza", 30))
    said = "Lasagna and pizza for Friday dinner"
    tools.save_week_intake(week, freeform=said)
    dinners = list(_OTHERS)
    dinners[2], dinners[4] = "Pizza", "Lasagna"
    stub_model(_week(dates, dinners, **{f"{dates[2]}:dinner": _asked(said),
                                        f"{dates[4]}:dinner": _asked(said)}))

    plan_id, _ = run(week, None)

    assert _where(plan_id, "dinner", "Lasagna") == [(dates[4], "dinner")]
    assert _where(plan_id, "dinner", "Pizza") == [(dates[5], "dinner")]
    assert _where(plan_id, "dinner", _OTHERS[5]) == [(dates[2], "dinner")]
    assert _lines(plan_id) == ["You asked for Lasagna and Pizza for Friday dinner, so Pizza is Saturday dinner."]
    _audit_clean(plan_id)


def test_a_dish_too_slow_for_lunch_gives_way_to_the_more_specific_request(stub_model, run):
    """
    CATCH. Pizza takes 40 minutes, too long for a weekday lunch (20), so
    the two can't share Friday. Lasagna named the meal as well as the day —
    more specific — so it keeps Friday dinner even though the model put
    Pizza there. Pizza goes to the nearest dinner it can: Saturday nobody
    is home, Thursday holds a dish they asked for, so Sunday (two away,
    later than Wednesday).
    """
    week = _monday()
    dates = tools._week_dates(week)
    _seed(("Lasagna", 30), ("Pizza", 40), ("Tacos", 15))
    said = "Pizza on Friday. Lasagna for Friday dinner. Tacos on Thursday"
    tools.save_week_intake(week, freeform=said, night_tags={dates[5]: ["out"]})
    dinners = list(_OTHERS)
    dinners[1], dinners[3], dinners[4] = "Lasagna", "Tacos", "Pizza"
    stub_model(_week(dates, dinners, **{f"{dates[1]}:dinner": _asked("Lasagna for Friday dinner"),
                                        f"{dates[3]}:dinner": _asked("Tacos on Thursday"),
                                        f"{dates[4]}:dinner": _asked("Pizza on Friday")}))

    plan_id, _ = run(week, None)

    assert _where(plan_id, "dinner", "Lasagna") == [(dates[4], "dinner")]
    assert _where(plan_id, "dinner", "Tacos") == [(dates[3], "dinner")]
    assert _where(plan_id, "dinner", "Pizza") == [(dates[6], "dinner")]
    assert _lines(plan_id) == ["You asked for Pizza and Lasagna on Friday, so Pizza is Sunday dinner."]
    _audit_clean(plan_id)


def test_a_lunch_and_a_dinner_they_named_themselves_need_no_line(stub_model, run):
    """GUARD: they chose the meals, so there is nothing to fit and nothing
    to say."""
    week = _monday()
    dates = tools._week_dates(week)
    _seed(("Fish Tacos", 15), ("Pizza", 30))
    tools.save_week_intake(week, freeform="Fish tacos for Friday lunch. Pizza for Friday dinner")
    dinners = list(_OTHERS)
    dinners[4] = "Pizza"
    lunches = [f"Wrap {i}" for i in range(7)]
    lunches[4] = "Fish Tacos"
    stub_model(_week(dates, dinners, lunches, **{f"{dates[4]}:lunch": _asked("Fish tacos for Friday lunch"),
                                                 f"{dates[4]}:dinner": _asked("Pizza for Friday dinner")}))

    plan_id, _ = run(week, None)

    assert _where(plan_id, "lunch", "Fish Tacos") == [(dates[4], "lunch")]
    assert _where(plan_id, "dinner", "Pizza") == [(dates[4], "dinner")]
    assert _lines(plan_id) == []
