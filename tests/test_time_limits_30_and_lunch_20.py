"""
Time limits per meal, not per date (Emily, 2026-09-23).

1. "Short on time" (the `rush` tag) is dinner on the table in 30 minutes
   or less, prep included. It was 20. On a weeknight where the household's
   own weeknight cap is lower, the lower one holds — before this a rush tag
   LOOSENED a weeknight cap of 15 to the rush number.
2. Every Monday-Friday lunch that is cooked that day is 20 minutes or
   less. Her words: "if the meal is on a prep day, it doesn't need to be a
   20min meal… if Im prepping chili for lunches, that's a great meal to
   just reheat, but if Im cooking on the day, then it needs to be 20 mins
   or less". So a lunch in a leftovers chain, either end, has no cap, and
   neither does a lunch on a prep day.
3. The rush tag is dinner only. Breakfast has no cap; weekend lunches have
   no fixed cap.

Before this the caps were keyed by DATE, so a lunch inherited that
evening's dinner cap everywhere a cap was looked up. One helper now
(time_caps.minutes_cap) for the generator's plate and variety passes, the
swap sheet and its gate, and the quality check. No model is called.
"""
from __future__ import annotations

import datetime

import pytest

from conftest import prompt_literals

from app import agent, tools
from app.tools import meal_variety, plan_quality, time_caps
from app.tools import swap_in_place as sip


def _next_monday() -> datetime.date:
    today = datetime.date.today()
    return today - datetime.timedelta(days=today.weekday()) + datetime.timedelta(days=7)


MON = _next_monday()
TUE = (MON + datetime.timedelta(days=1)).isoformat()
WED = (MON + datetime.timedelta(days=2)).isoformat()
SAT = (MON + datetime.timedelta(days=5)).isoformat()
MON = MON.isoformat()

cap = time_caps.minutes_cap


# ---------- the numbers ----------


def test_short_on_time_is_thirty_minutes():
    assert tools.RUSH_MAX_MINUTES == 30
    assert cap(TUE, "dinner", ["rush"], {}) == 30


def test_a_weekday_lunch_cooked_that_day_is_twenty_minutes():
    assert tools.WEEKDAY_LUNCH_MAX_MINUTES == 20
    for day in (MON, TUE, WED):
        assert cap(day, "lunch", [], {}) == 20


# ---------- dinner ----------


def test_a_rush_night_keeps_a_stricter_weeknight_cap():
    """Rush only ever tightens a night: a household whose weeknights are 15
    minutes doesn't get 30 because the night was also tagged rush."""
    assert cap(TUE, "dinner", ["rush"], {"weeknight_max_minutes": 15}) == 15
    assert cap(TUE, "dinner", ["rush"], {"weeknight_max_minutes": 45}) == 30
    # The weeknight cap is Monday-Friday only, so a rush Saturday is 30.
    assert cap(SAT, "dinner", ["rush"], {"weeknight_max_minutes": 15}) == 30


def test_the_dinner_rules_otherwise_stand():
    memory = {"weeknight_max_minutes": 40}
    assert cap(TUE, "dinner", [], memory) == 40
    assert cap(TUE, "dinner", ["unrushed"], memory) is None
    assert cap(SAT, "dinner", [], memory) is None
    assert cap(TUE, "dinner", [], {}) is None
    assert cap(TUE, "dinner", [], {"weeknight_max_minutes": 0}) is None


# ---------- lunch ----------


def test_a_weekday_lunch_that_is_leftovers_has_no_cap():
    assert cap(TUE, "lunch", [], {}, is_leftovers=True) is None


def test_a_lunch_on_a_prep_day_has_no_cap():
    memory = {"rhythm": {"prep_days": [{"weekday": "wednesday"}]}}
    assert cap(WED, "lunch", [], memory) is None
    assert cap(TUE, "lunch", [], memory) == 20


def test_a_saturday_lunch_has_no_cap():
    assert cap(SAT, "lunch", [], {"weeknight_max_minutes": 15}) is None
    assert cap(SAT, "lunch", ["rush"], {}) is None


def test_lunch_ignores_the_dinner_tags_and_the_weeknight_cap():
    """Short on time was asked about dinner: a rush Tuesday's lunch is the
    lunch rule's 20, and a 15-minute weeknight cap doesn't reach lunch."""
    assert cap(TUE, "lunch", ["rush"], {}) == 20
    assert cap(TUE, "lunch", ["unrushed"], {}) == 20
    assert cap(TUE, "lunch", [], {"weeknight_max_minutes": 15}) == 20


# ---------- breakfast and snack ----------


def test_breakfast_and_snacks_have_no_cap():
    for slot in ("breakfast", "snack"):
        assert cap(TUE, slot, ["rush"], {"weeknight_max_minutes": 15}) is None
        assert cap(SAT, slot, [], {}) is None


# ---------- the variety pass ----------


def test_the_slot_view_accepts_both_shapes():
    per_slot = {(TUE, "dinner"): 15, (TUE, "lunch"): 20, (SAT, "lunch"): None}
    assert time_caps.caps_for_slot(per_slot, "lunch") == {TUE: 20, SAT: None}
    assert time_caps.caps_for_slot(per_slot, "dinner") == {TUE: 15}
    # The older date-keyed dict still reads as it always did.
    assert time_caps.caps_for_slot({TUE: 15}, "lunch") == {TUE: 15}
    assert time_caps.caps_for_slot(None, "lunch") == {}


def _lunch_week():
    """Two lunch dishes against a target of one: an 18-minute salad on
    Monday and a 10-minute wrap on Tuesday."""
    tools.add_recipe("Quick Salad", ingredients=[{"item": "lettuce", "qty": "1 head"}],
                     prep_time_minutes=8, cook_time_minutes=10, food_groups=["protein", "vegetable", "carb"])
    tools.add_recipe("Wrap", ingredients=[{"item": "tortillas", "qty": "1 pack"}],
                     prep_time_minutes=5, cook_time_minutes=5, food_groups=["protein", "vegetable", "carb"])
    plan_id = tools.create_weekly_plan(MON)["weekly_plan_id"]
    tools.plan_meal(MON, "Quick Salad", slot="lunch", weekly_plan_id=plan_id, reasoning="fits")
    tools.plan_meal(TUE, "Wrap", slot="lunch", weekly_plan_id=plan_id, reasoning="fits")
    return plan_id


def _lunches(plan_id):
    return {m["date"]: m["meal"] for m in tools.get_weekly_plan(plan_id)["meals"] if m["slot"] == "lunch"}


def test_the_variety_pass_no_longer_holds_lunch_to_the_dinner_cap():
    """
    A weeknight dinner cap of 15. Keyed by date, the salad (18 minutes)
    "didn't fit" Monday's lunch and was the dish folded away. Per slot,
    Monday's lunch is 20 and the salad fits, so both dishes hold a capped
    night they fit and the later one folds — the wrap, onto the salad.
    """
    plan_id = _lunch_week()
    caps = {
        (MON, "dinner"): 15, (TUE, "dinner"): 15,
        (MON, "lunch"): 20, (TUE, "lunch"): 20,
    }
    out = meal_variety.enforce_distinct_count(plan_id, 1, slot="lunch", caps=caps)
    assert out["after"] == 1
    assert _lunches(plan_id) == {MON: "Quick Salad", TUE: "Quick Salad"}


def test_the_variety_pass_still_takes_the_old_date_keyed_caps():
    """The old shape keeps working, with the old meaning (the date's
    dinner cap, for any slot) — 15 on Monday, so the salad goes."""
    plan_id = _lunch_week()
    meal_variety.enforce_distinct_count(plan_id, 1, slot="lunch", caps={MON: 15, TUE: 15})
    assert _lunches(plan_id) == {MON: "Wrap", TUE: "Wrap"}


def _week_start():
    today = datetime.date.today()
    return (today - datetime.timedelta(days=today.weekday()) + datetime.timedelta(days=7)).isoformat()


def test_generation_hands_the_variety_pass_per_slot_caps(monkeypatch):
    tools.add_recipe("Chili", ingredients=[{"item": "beans", "qty": "1 tin"}],
                     prep_time_minutes=10, cook_time_minutes=20)
    tools.edit_preference("weeknight_max_minutes", 15)
    week = _week_start()
    dates = tools._week_dates(week)
    tuesday, saturday = dates[1], dates[5]
    tools.save_week_intake(week, night_tags={saturday: ["rush"]})
    monkeypatch.setattr(agent, "generate_weekly_plan_llm", lambda context: [
        {"date": d, "slot": s, "meal_name": "Chili", "is_new_recipe": False, "reasoning": "fits the week"}
        for d in dates for s in tools.WEEK_SLOTS
    ])
    seen = {}
    real = tools.enforce_distinct_meal_count

    def spy(plan_id, target, slot="dinner", **kw):
        seen[slot] = kw.get("caps")
        return real(plan_id, target, slot=slot, **kw)

    monkeypatch.setattr(tools, "enforce_distinct_meal_count", spy)
    agent.generate_weekly_plan(week)

    lunch = time_caps.caps_for_slot(seen["lunch"], "lunch")
    dinner = time_caps.caps_for_slot(seen["dinner"], "dinner")
    breakfast = time_caps.caps_for_slot(seen["breakfast"], "breakfast")
    assert dinner[tuesday] == 15 and dinner[saturday] == 30
    assert lunch[tuesday] == 20, "a weekday lunch is 20, not the 15-minute dinner cap"
    assert lunch[saturday] is None, "a rush Saturday's lunch is not held to the rush dinner cap"
    assert breakfast[tuesday] is None


# ---------- the swap sheet ----------


def _swap_week():
    tools.add_member("Emily")
    for name in ("Chili", "Soup"):
        tools.add_recipe(name, ingredients=[{"item": name, "qty": "1 lb", "category": "meat/seafood"}],
                         food_groups=["protein", "vegetable", "carb"], prep_time_minutes=15, cook_time_minutes=40)
    plan_id = tools.create_weekly_plan(MON)["weekly_plan_id"]
    tools.plan_meal(MON, "Soup", slot="lunch", weekly_plan_id=plan_id, reasoning="fits")
    tools.plan_meal(TUE, "Chili", slot="lunch", weekly_plan_id=plan_id, reasoning="batch")
    tools.plan_meal(WED, "Chili", slot="lunch", weekly_plan_id=plan_id, reasoning="leftovers",
                    derived_from={"links_to": f"{TUE}:lunch"})
    tools.plan_meal(SAT, "Soup", slot="lunch", weekly_plan_id=plan_id, reasoning="fits")
    tools.plan_meal(TUE, "Soup", slot="dinner", weekly_plan_id=plan_id, reasoning="fits")
    tools.repair_leftover_chains(plan_id)
    return plan_id


def _entry(plan_id, day, slot):
    row = next(m for m in tools.get_weekly_plan(plan_id)["meals"] if m["date"] == day and m["slot"] == slot)
    return sip._entry(plan_id, row["entry_id"])


def test_a_weekday_lunch_swap_is_held_to_twenty():
    plan_id = _swap_week()
    tools.edit_preference("weeknight_max_minutes", 45)
    tools.save_week_intake(MON, night_tags={MON: ["rush"]})
    context = sip.build_swap_context(plan_id, _entry(plan_id, MON, "lunch"))
    assert context["slot"] == "lunch" and context["max_minutes"] == 20


def test_a_leftovers_lunch_swap_and_its_batch_cook_have_no_cap():
    plan_id = _swap_week()
    assert sip.build_swap_context(plan_id, _entry(plan_id, WED, "lunch"))["max_minutes"] is None
    assert sip.build_swap_context(plan_id, _entry(plan_id, TUE, "lunch"))["max_minutes"] is None


def test_a_saturday_lunch_swap_has_no_cap():
    plan_id = _swap_week()
    assert sip.build_swap_context(plan_id, _entry(plan_id, SAT, "lunch"))["max_minutes"] is None


def test_the_swap_gate_holds_each_entry_to_its_own_slot():
    """cap_gate's per-day caps are per entry now: a 30-minute pick is fine
    for a rush dinner and too long for a weekday lunch on the same day."""
    plan_id = _swap_week()
    tools.save_week_intake(MON, night_tags={TUE: ["rush"]})
    pick = {"meal_name": "Stir Fry", "prep_time_minutes": 10, "cook_time_minutes": 20}
    assert sip.cap_gate(plan_id, pick, [_entry(plan_id, TUE, "dinner")]) is None
    why = sip.cap_gate(plan_id, pick, [_entry(plan_id, MON, "lunch")])
    assert why and "only has 20" in why


# ---------- the quality check ----------


def _q(day, slot="lunch", minutes=30, **extra):
    return {"date": day, "slot": slot, "slot_state": "planned", "meal_name": "Stew",
            "prep_time_minutes": 0, "cook_time_minutes": minutes, **extra}


def _lunch_flags(entries, context=None):
    return {v.date for v in plan_quality._weekday_lunch_cap_respected(entries, context or {})}


def test_the_quality_check_warns_on_a_long_weekday_lunch_cooked_that_day():
    assert _lunch_flags([_q(TUE, minutes=30)]) == {TUE}
    assert _lunch_flags([_q(TUE, minutes=20)]) == set(), "20 or less"
    violations = plan_quality.check_week([_q(TUE, minutes=30)], {})
    assert any(v.rule == "weekday_lunch_cap_respected" and v.severity == "warn" for v in violations)


def test_the_quality_check_lets_leftovers_prep_days_and_weekends_run_long():
    entries = [
        _q(MON, minutes=60, make_double_for=[f"{TUE}:lunch"]),   # the batch cook
        _q(TUE, minutes=60, links_to=f"{MON}:lunch"),             # its reheat
        _q(WED, minutes=60),                                      # a prep day
        _q(SAT, minutes=60),                                      # the weekend
        _q(TUE, slot="breakfast", minutes=60),
    ]
    assert _lunch_flags(entries, {"prep_days": [{"weekday": "wednesday"}]}) == set()


def test_the_rush_check_keeps_a_stricter_weeknight_cap():
    entries = [_q(TUE, slot="dinner", minutes=25)]
    loose = {"rush_dates": {TUE}, "rush_max_minutes": 30, "weeknight_max_minutes": 45}
    strict = dict(loose, weeknight_max_minutes=15)
    assert not plan_quality._rush_cap_respected(entries, loose)
    assert [v.rule for v in plan_quality._rush_cap_respected(entries, strict)] == ["rush_cap_respected"]


# ---------- what the model is told ----------


def test_the_prompt_says_the_lunch_rule_and_the_rush_number():
    shipped = prompt_literals(agent.generate_weekly_plan_llm)
    assert "Every Monday-Friday lunch that is cooked that day is capped at " in shipped
    assert "reheats well, like chili, a stew or a curry" in shipped
    assert "Dinner only: it does not change" in shipped
    assert "30 minutes or less" in tools.NIGHT_TAGS["rush"]


@pytest.mark.parametrize("text", [
    "I'll keep it to 30 minutes or less.",
    "everything I have that takes 30 minutes or less repeats",
    "'max_minutes:30'",
])
def test_the_prompt_examples_say_thirty(text):
    source = open(agent.__file__, encoding="utf-8").read()
    assert text in source
    assert "under 20 minutes" not in source
