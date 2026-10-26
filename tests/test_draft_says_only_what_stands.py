"""
The draft's "as you asked" is only said of a dish that is still on the week.

Defect hunt, 2026-10-24. The model's report of what it did with the typed
requests (honoured_requests) was stored as it came back, and the opener
believed it — but the passes after the model can take a requested dish
away: an out night is emptied whatever is on it, and the allergen gate
swaps a dish someone can't have (carrying the slot's citation onto the
replacement). And a "tomorrow" was pinned onto a night tagged out, BEFORE
the out pass, so the dish was moved there and then cleared. What the
household read, each case measured on origin/main 2f2557a:

  "Lasagna Wednesday, as you asked, Wednesday left free."   (no lasagna)
  "Peanut noodles Tuesday, as you asked."                   (Tuesday: a peanut-free swap)
  "Monday left free." / "I moved Lamb Curry to Monday dinner, as you asked."
                                                            (no curry anywhere)

CATCH = red on that commit; GUARD = green there, pinning what must not move.
The model and every re-pick are stubbed; nothing reaches the API.
"""
from __future__ import annotations

import datetime

import pytest

from app import agent, tools
from app.db import get_conn
from app.tools import allergen_gate, swap_in_place
from conftest import household_date, household_pin

SUNDAY = "2026-09-27"
PERIOD = [(datetime.date(2026, 9, 27) + datetime.timedelta(days=i)).isoformat() for i in range(6)]


def _next_monday() -> str:
    today = datetime.date.fromisoformat(household_date())
    return (today - datetime.timedelta(days=today.weekday()) + datetime.timedelta(days=7)).isoformat()


def _slot(date, slot, name, freeform=None, ingredients=None):
    out = {"date": date, "slot": slot, "meal_name": name, "is_new_recipe": True,
           "ingredients": ingredients or [{"item": f"{name} base", "qty": "1", "category": "pantry"}],
           "reasoning": f"{name} fits", "food_groups": ["protein", "vegetable", "carb"],
           "prep_time_minutes": 10, "cook_time_minutes": 15}
    if freeform:
        out["derived_from"] = {"freeform": freeform}
    return out


def _week(dates, special: dict) -> list[dict]:
    out = []
    for i, d in enumerate(dates):
        out.append(_slot(d, "breakfast", "Oatmeal"))
        out.append(_slot(d, "lunch", ["Lentil Soup", "Turkey Wraps"][i % 2]))
        out.append(special.get(d) or _slot(d, "dinner", f"House Dinner {i}"))
    return out


@pytest.fixture
def household():
    for name in ("Emily", "Vineeth"):
        tools.add_member(name)
        tools.set_member_age_group(name, "adult")


@pytest.fixture
def picker(monkeypatch):
    names = iter([f"Quick Dish {i}" for i in range(1, 40)])

    def pick(context):
        name = next(names)
        return {"meal_name": name, "reason": "quick",
                "ingredients": [{"item": f"{name} base", "qty": "1", "category": "pantry"}],
                "instructions": ["Cook it.", "Serve."], "food_groups": ["protein", "vegetable", "carb"],
                "prep_time_minutes": 5, "cook_time_minutes": 10}
    monkeypatch.setattr(swap_in_place, "_pick_replacement", pick)
    monkeypatch.setattr(allergen_gate, "quick_pick", pick)


def _model(monkeypatch, days, honoured):
    def gen(context):
        out = agent.GeneratedDays(days)
        out.report = {"honoured_requests": honoured, "unmet_requests": []}
        return out
    monkeypatch.setattr(agent, "generate_weekly_plan_llm", gen)


def _dinner(plan_id, date):
    conn = get_conn()
    rows = conn.execute(
        """SELECT mpe.slot_state, COALESCE(r.name, mpe.freeform_meal) AS meal
           FROM meal_plan_entries mpe LEFT JOIN recipes r ON r.id = mpe.recipe_id
           WHERE mpe.weekly_plan_id = ? AND mpe.date = ? AND mpe.slot = 'dinner'""",
        (plan_id, date)).fetchall()
    conn.close()
    assert len(rows) == 1, rows
    return dict(rows[0])


def _all_dinners(plan_id):
    conn = get_conn()
    rows = conn.execute(
        """SELECT COALESCE(r.name, mpe.freeform_meal) AS meal FROM meal_plan_entries mpe
           LEFT JOIN recipes r ON r.id = mpe.recipe_id
           WHERE mpe.weekly_plan_id = ? AND mpe.slot = 'dinner' AND mpe.slot_state = 'planned'""",
        (plan_id,)).fetchall()
    conn.close()
    return {r["meal"] for r in rows}


def test_a_requested_dish_cleared_off_an_out_night_is_not_as_you_asked(household, picker, monkeypatch):
    """CATCH. The model put the lasagna they asked for on the night they
    tagged out; the tag wins and the night is emptied, so the draft says it
    couldn't fit the lasagna rather than crediting it."""
    week = _next_monday()
    dates = tools.period_dates(week, 7)
    words = "Lasagna on Wednesday please"
    tools.save_week_intake(week, night_tags={dates[2]: ["out"]}, freeform=words)
    _model(monkeypatch, _week(dates, {dates[2]: _slot(dates[2], "dinner", "Lasagna", freeform=words)}),
           [{"words": words, "label": "Lasagna Wednesday"}])

    plan_id = agent.generate_weekly_plan(week)["weekly_plan_id"]

    assert _dinner(plan_id, dates[2])["slot_state"] == "planned_empty"
    opener = tools.get_week_menu(plan_id)["draft_opener"]
    assert not any("as you asked" in line and "Lasagna" in line for line in opener), opener
    # Snag rule 8 (2026-10-25): the dish is not dropped — it moves to the
    # nearest home night, and the draft flag says so, not the opener.
    menu = tools.get_week_menu(plan_id)
    assert _dinner(plan_id, dates[3])["meal"] == "Lasagna"
    assert "You're out Wednesday, so Lasagna moved to Thursday." in [f["text"] for f in menu.get("draft_flags") or []]


def test_a_requested_dish_swapped_for_an_allergy_is_not_as_you_asked(household, picker, monkeypatch):
    """CATCH. Peanut noodles, asked for by name, for a household where Emily
    is allergic to peanuts: the gate swaps the dish, and neither the opener
    nor the replacement's row says it was what they asked for."""
    tools.set_member_dietary_restrictions("Emily", ["peanut allergy"])
    week = _next_monday()
    dates = tools.period_dates(week, 7)
    words = "Peanut noodles one night"
    tools.save_week_intake(week, freeform=words)
    noodles = _slot(dates[1], "dinner", "Peanut Noodles", freeform=words,
                    ingredients=[{"item": "peanut butter", "qty": "1 cup", "category": "pantry"}])
    _model(monkeypatch, _week(dates, {dates[1]: noodles}), [{"words": words, "label": "Peanut noodles"}])

    plan_id = agent.generate_weekly_plan(week)["weekly_plan_id"]

    assert _dinner(plan_id, dates[1])["meal"].startswith("Quick Dish")
    menu = tools.get_week_menu(plan_id)
    assert not any("as you asked" in line for line in menu["draft_opener"]), menu["draft_opener"]
    # Nor "couldn't fit": a swap may still answer a request (review,
    # 2026-10-24 — Shrimp Pad Thai → Chicken Pad Thai), so it is not said.
    assert not any("Peanut noodles" in line for line in menu["draft_opener"]), menu["draft_opener"]
    tuesday = next(d for d in menu["days"] if d["date"] == dates[1])
    assert not (tuesday["dinner"].get("asked") or ""), tuesday["dinner"]


def test_a_requested_dish_that_stays_is_still_credited(household, picker, monkeypatch):
    """GUARD. The ordinary case: the dish is on the night asked, and so is
    a request the model honoured without citing any slot (its say-so is
    all there is to go on, and the opener has always taken it)."""
    week = _next_monday()
    dates = tools.period_dates(week, 7)
    words = "Lasagna on Wednesday please"
    tools.save_week_intake(week, freeform=words + ". Something with greens.")
    _model(monkeypatch, _week(dates, {dates[2]: _slot(dates[2], "dinner", "Lasagna", freeform=words)}),
           [{"words": words, "label": "Lasagna Wednesday"},
            {"words": "Something with greens", "label": "greens"}])

    plan_id = agent.generate_weekly_plan(week)["weekly_plan_id"]

    first = tools.get_week_menu(plan_id)["draft_opener"][0]
    assert "Lasagna Wednesday" in first and "greens" in first and first.endswith("as you asked."), first


@pytest.mark.real_time_of_day
def test_tomorrow_is_not_pinned_onto_a_night_tagged_out(household, picker, monkeypatch, frozen_today):
    """CATCH. "Lamb curry tomorrow." with tomorrow tagged out: the curry used
    to be moved onto Monday and then cleared with the night. It stays where
    the model put it, still credited, and no line claims a move."""
    frozen_today(household_pin(9, 0, on=datetime.date(2026, 9, 27)))
    words = "Lamb curry tomorrow."
    tools.save_week_intake(SUNDAY, day_count=6, freeform=words, night_tags={PERIOD[1]: ["out"]})
    _model(monkeypatch, _week(PERIOD, {PERIOD[3]: _slot(PERIOD[3], "dinner", "Lamb Curry", freeform=words)}),
           [{"words": words, "label": "Lamb curry"}])

    plan_id = agent.generate_weekly_plan(SUNDAY, day_count=6, period_start=SUNDAY)["weekly_plan_id"]

    assert _dinner(plan_id, PERIOD[1])["slot_state"] == "planned_empty"
    assert "Lamb Curry" in _all_dinners(plan_id)
    opener = tools.get_week_menu(plan_id)["draft_opener"]
    assert not any("I moved" in line for line in opener), opener
    assert opener[0].startswith("Lamb curry Wednesday, as you asked"), opener


@pytest.mark.real_time_of_day
def test_tomorrow_is_not_pinned_onto_a_day_tapped_off(household, picker, monkeypatch, frozen_today):
    """CATCH, the same rule for a day left out on "Which days?"."""
    frozen_today(household_pin(9, 0, on=datetime.date(2026, 9, 27)))
    words = "Lamb curry tomorrow."
    tools.save_week_intake(SUNDAY, day_count=6, freeform=words, skipped_days=[PERIOD[1]])
    _model(monkeypatch, _week(PERIOD, {PERIOD[3]: _slot(PERIOD[3], "dinner", "Lamb Curry", freeform=words)}),
           [{"words": words, "label": "Lamb curry"}])

    plan_id = agent.generate_weekly_plan(SUNDAY, day_count=6, period_start=SUNDAY)["weekly_plan_id"]

    assert "Lamb Curry" in _all_dinners(plan_id)
    assert not any("I moved" in line for line in tools.get_week_menu(plan_id)["draft_opener"])


@pytest.mark.real_time_of_day
def test_tomorrow_still_moves_onto_a_night_someone_is_home(household, picker, monkeypatch, frozen_today):
    """GUARD. With nobody out, the curry moves to Monday and the line says so."""
    frozen_today(household_pin(9, 0, on=datetime.date(2026, 9, 27)))
    words = "Lamb curry tomorrow."
    tools.save_week_intake(SUNDAY, day_count=6, freeform=words)
    _model(monkeypatch, _week(PERIOD, {PERIOD[3]: _slot(PERIOD[3], "dinner", "Lamb Curry", freeform=words)}),
           [{"words": words, "label": "Lamb curry"}])

    plan_id = agent.generate_weekly_plan(SUNDAY, day_count=6, period_start=SUNDAY)["weekly_plan_id"]

    assert _dinner(plan_id, PERIOD[1])["meal"] == "Lamb Curry"
    assert "I moved Lamb Curry to Monday dinner, as you asked." in tools.get_week_menu(plan_id)["draft_opener"]


def test_two_requests_sharing_a_word_do_not_vouch_for_each_other(household, picker, monkeypatch):
    """CATCH (review, 2026-10-24). "Chicken tacos on Tuesday" and "Chicken
    curry on Friday" share "chicken"; Tuesday is tagged out, so the tacos
    are gone. Word overlap let the curry's slot keep the tacos credited."""
    week = _next_monday()
    dates = tools.period_dates(week, 7)
    tacos, curry = "Chicken tacos on Tuesday", "Chicken curry on Friday"
    tools.save_week_intake(week, night_tags={dates[1]: ["out"]}, freeform=f"{tacos}. {curry}.")
    _model(monkeypatch, _week(dates, {
        dates[1]: _slot(dates[1], "dinner", "Chicken Tacos", freeform=tacos),
        dates[4]: _slot(dates[4], "dinner", "Chicken Curry", freeform=curry),
    }), [{"words": tacos, "label": "Chicken tacos"}, {"words": curry, "label": "Chicken curry"}])

    plan_id = agent.generate_weekly_plan(week)["weekly_plan_id"]

    opener = tools.get_week_menu(plan_id)["draft_opener"]
    assert not any("tacos" in line.lower() and "as you asked" in line for line in opener), opener
    assert "Chicken curry Friday, as you asked" in opener[0], opener
    # Snag rule 8 (2026-10-25): the tacos move to Wednesday, said by the flag.
    menu = tools.get_week_menu(plan_id)
    assert _dinner(plan_id, dates[2])["meal"] == "Chicken Tacos"
    assert "You're out Tuesday, so Chicken Tacos moved to Wednesday." in [f["text"] for f in menu.get("draft_flags") or []]


def test_an_allergen_swap_that_may_still_meet_the_request_is_not_called_unmet(household, picker, monkeypatch):
    """CATCH (review, 2026-10-24). Shellfish allergy, "Pad Thai on Friday",
    the model's Shrimp Pad Thai swapped by the gate: the request is said
    neither as met nor as unmet."""
    tools.set_member_dietary_restrictions("Emily", ["shellfish allergy"])
    week = _next_monday()
    dates = tools.period_dates(week, 7)
    words = "Pad Thai on Friday"
    tools.save_week_intake(week, freeform=words)
    shrimp = _slot(dates[4], "dinner", "Shrimp Pad Thai", freeform=words,
                   ingredients=[{"item": "shrimp", "qty": "1 lb", "category": "seafood"}])
    _model(monkeypatch, _week(dates, {dates[4]: shrimp}), [{"words": words, "label": "Pad Thai Friday"}])

    plan_id = agent.generate_weekly_plan(week)["weekly_plan_id"]

    assert _dinner(plan_id, dates[4])["meal"] != "Shrimp Pad Thai"
    opener = tools.get_week_menu(plan_id)["draft_opener"]
    assert not any("Pad Thai" in line for line in opener), opener


@pytest.mark.real_time_of_day
def test_tomorrow_is_not_pinned_onto_a_meal_they_asked_for_none_of(household, picker, monkeypatch, frozen_today):
    """CATCH (review nit). Lunches 0: every lunch is emptied, so "Soup for
    lunch tomorrow" is not pinned there and no line claims a move."""
    frozen_today(household_pin(9, 0, on=datetime.date(2026, 9, 27)))
    tools.set_household_meal_preferences(lunches_per_week=0)
    words = "Soup for lunch tomorrow."
    tools.save_week_intake(SUNDAY, day_count=6, freeform=words)
    days = _week(PERIOD, {})
    days = [d for d in days if not (d["date"] == PERIOD[3] and d["slot"] == "lunch")]
    days.append(_slot(PERIOD[3], "lunch", "Tomato Soup", freeform=words))
    _model(monkeypatch, days, [{"words": words, "label": "Soup lunch"}])
    told = []
    stub = agent.generate_weekly_plan_llm
    monkeypatch.setattr(agent, "generate_weekly_plan_llm", lambda ctx: told.append(ctx) or stub(ctx))

    plan_id = agent.generate_weekly_plan(SUNDAY, day_count=6, period_start=SUNDAY)["weekly_plan_id"]

    assert not told[0].get("freeform_on_a_day"), "the model is not told to pin soup onto a lunch nobody eats"
    opener = tools.get_week_menu(plan_id)["draft_opener"]
    assert not any("I moved" in line for line in opener), opener
    assert not any("Soup" in line and "as you asked" in line for line in opener), opener
