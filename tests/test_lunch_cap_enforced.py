"""
A weekday lunch the household said is cooked that day is held to its own
cap by CODE, not only warned about (Loop Board, 2026-10-02: "A weekday lunch
you said you'd cook in 20 minutes is drafted at 35").

Root cause on main: app/tools/cap_enforce.py's loader read
`WHERE mpe.slot = 'dinner'`, so enforce_minutes_caps never saw a lunch —
{moved 0, repicked 0, left 0} — and plan_quality's
`weekday_lunch_cap_respected` warned into the morning report only.

The fix is STAGE 2 ONLY for lunch (and breakfast, which has no cap today):
the same re-pick, against the same shared budget, after the dinners and
leaving what the later dinner fill and allergen sweep need. There is no
lunch door like swap_dinner_nights, so no free trade. Every carve-out is
time_caps.minutes_cap's, asked exactly as plan_quality asks it. Only a lunch
the household SAID is cooked that day (step 3) is re-picked — the
assumption is pinned by test_an_unanswered_weekday_lunch_is_only_recorded.
"""
from __future__ import annotations

import json

import pytest

from app import tools
from app.db import get_conn
from app.tools import cap_enforce, plan_quality

# The rush-cap file's helpers and fixtures: same stub model, same picker.
from test_rush_cap_enforced import (  # noqa: F401  (fixtures are used by name)
    _monday, _recipe, _slot, picker, stub_model, generate_only, run,
)


# Captured at import: `generate_only` monkeypatches the module attribute to a
# no-op for the length of a test, so a direct call must hold the real one.
_enforce = cap_enforce.enforce_minutes_caps


def _day(d: str, lunch: str, *, dinner: str = "Quick Eggs", breakfast: str = "Oats", **lunch_extra):
    return [_slot(d, "breakfast", breakfast), _slot(d, "lunch", lunch, **lunch_extra),
            _slot(d, "dinner", dinner), _slot(d, "snack", "Apple")]


def _filler():
    _recipe("Oats", 5)
    _recipe("Apple", 0)
    _recipe("Quick Eggs", 10)
    _recipe("Quick Wrap", 10)


def _lunches(plan_id: int) -> dict[str, dict]:
    conn = get_conn()
    rows = conn.execute(
        """SELECT mpe.id, mpe.date, mpe.slot_state, mpe.cooked_status, mpe.derived_from_json,
                  COALESCE(r.name, mpe.freeform_meal) AS meal,
                  r.prep_time_minutes AS p, r.cook_time_minutes AS c
           FROM meal_plan_entries mpe LEFT JOIN recipes r ON r.id = mpe.recipe_id
           WHERE mpe.weekly_plan_id = ? AND mpe.slot = 'lunch' AND mpe.component_category IS NULL
           ORDER BY mpe.date, mpe.id""", (plan_id,)).fetchall()
    conn.close()
    return {r["date"]: dict(r, minutes=(r["p"] or 0) + (r["c"] or 0)) for r in rows}


def _lunch_warnings(plan_id: int, intake: dict | None, memory: dict | None = None) -> list:
    from app.tools import weekday_lunches
    context = {
        "prep_days": ((memory or {}).get("rhythm") or {}).get("prep_days") or [],
        "lunch_kinds": weekday_lunches.kinds_by_date(intake),
    }
    return [v for v in plan_quality.check_week(plan_quality._load_plan_entries(plan_id), context)
            if v.rule == "weekday_lunch_cap_respected"]


def _answer(*pairs) -> dict:
    """The week's own step-3 answer: {(date, kind), ...}."""
    return {"weekday_lunches": {"days": [{"date": d, "kind": k} for d, k in pairs]}}


def _cooked(dates) -> dict:
    """Every lunch of the week answered "cooked that day" (a weekend answer
    is harmless: time_caps has no weekend lunch cap)."""
    return _answer(*[(d, "cooked") for d in dates])


# ---------- 1. the reported bug, end to end ----------

def test_a_cooked_that_day_lunch_drafted_at_35_is_brought_inside_20(stub_model, run, picker):
    """
    CATCH. The card's own reproduction: a 35-minute lunch on the Tuesday
    the household said is cooked that day. On main the pass never loads it
    and plan_quality warns; here it is re-picked inside 20.
    """
    week = _monday()
    dates = tools._week_dates(week)
    _filler()
    tools.add_recipe("Slow Braised Lentil Bowl",
                     ingredients=[{"item": "lentils", "qty": "1", "category": "pantry"}],
                     prep_time_minutes=10, cook_time_minutes=25,
                     food_groups=["protein", "vegetable", "carb"],
                     instructions=["Braise.", "Serve."])
    tools.save_week_intake(week, weekday_lunches={"days": [{"date": dates[1], "kind": "cooked"}]})
    days = []
    for i, d in enumerate(dates):
        days += _day(d, "Slow Braised Lentil Bowl" if i == 1 else "Quick Wrap")
    stub_model(days)

    plan_id, seen = run(week, None)

    tuesday = _lunches(plan_id)[dates[1]]
    assert tuesday["slot_state"] == "planned"
    assert tuesday["meal"] != "Slow Braised Lentil Bowl"
    assert tuesday["minutes"] <= 20
    assert [(x["slot"], x["date"]) for x in seen["result"]["repicked"]] == [("lunch", dates[1])]
    assert _lunch_warnings(plan_id, tools.get_week_intake(week)) == []


def test_the_tripwire_goes_from_four_to_zero(generate_only, picker):
    """
    CATCH, acceptance 3 measured before and after on one seeded week: four
    weekday lunches over 20, every one answered "cooked that day". Four is
    what one budget of six can fix while leaving the allergen sweep its two.
    """
    week = _monday()
    dates = tools._week_dates(week)
    _filler()
    _recipe("Long Lunch A", 60)
    _recipe("Long Lunch B", 45)
    _recipe("Long Lunch C", 40)
    _recipe("Long Lunch D", 35)
    tools.save_week_intake(week)
    longs = ["Long Lunch A", "Long Lunch B", "Long Lunch C", "Long Lunch D", "Quick Wrap",
             "Quick Wrap", "Quick Wrap"]
    days = []
    for d, name in zip(dates, longs):
        days += _day(d, name)
    plan_id = _generate(generate_only, week, days)
    intake = _cooked(dates)

    assert len(_lunch_warnings(plan_id, intake)) == 4, "before: four warnings"
    out = _enforce(plan_id, intake, tools.get_household_memory(), picker=picker)
    assert len(out["repicked"]) == 4
    assert _lunch_warnings(plan_id, intake) == [], "after: none"


def _generate(generate_only, week, days):
    import app.agent as agent
    import pytest as _pytest
    mp = _pytest.MonkeyPatch()
    mp.setattr(agent, "generate_weekly_plan_llm", lambda context: days)
    try:
        return generate_only(week)
    finally:
        mp.undo()


# ---------- 2. never handed back; the reason is recorded ----------

def test_nothing_quicker_leaves_the_lunch_as_drafted_with_its_reason(generate_only):
    week = _monday()
    dates = tools._week_dates(week)
    _filler()
    _recipe("Long Lunch", 45)
    tools.save_week_intake(week)
    days = []
    for i, d in enumerate(dates):
        days += _day(d, "Long Lunch" if i == 2 else "Quick Wrap")
    plan_id = _generate(generate_only, week, days)

    out = _enforce(plan_id, _cooked(dates),
                                           tools.get_household_memory(), picker=lambda c: {})

    wed = _lunches(plan_id)[dates[2]]
    assert (wed["slot_state"], wed["meal"]) == ("planned", "Long Lunch")
    assert out["left"] == [{"date": dates[2], "slot": "lunch", "meal": "Long Lunch",
                            "minutes": 45, "cap": 20, "why": "nothing quicker came back"}]


def test_a_pick_over_the_lunch_cap_is_refused(generate_only, picker):
    """The cap is the pick's own refusal: a 25-minute pick is not a fix."""
    week = _monday()
    dates = tools._week_dates(week)
    _filler()
    _recipe("Long Lunch", 45)
    tools.save_week_intake(week)
    days = []
    for i, d in enumerate(dates):
        days += _day(d, "Long Lunch" if i == 0 else "Quick Wrap")
    plan_id = _generate(generate_only, week, days)
    picker.minutes = 25

    out = _enforce(plan_id, _cooked(dates),
                                           tools.get_household_memory(), picker=picker)

    assert out["repicked"] == []
    assert _lunches(plan_id)[dates[0]]["meal"] == "Long Lunch"
    assert picker.calls, "it did ask"


# ---------- 3. every carve-out holds ----------

def _one_long_lunch(generate_only, at: int, **lunch_extra):
    week = _monday()
    dates = tools._week_dates(week)
    _filler()
    _recipe("Long Lunch", 45)
    tools.save_week_intake(week)
    days = []
    for i, d in enumerate(dates):
        days += _day(d, "Long Lunch" if i == at else "Quick Wrap", **(lunch_extra if i == at else {}))
    return week, dates, _generate(generate_only, week, days)


def _never(context):
    raise AssertionError("this lunch has no cap to fit; nothing may be picked for it")


def test_a_weekend_lunch_has_no_cap(generate_only):
    week, dates, plan_id = _one_long_lunch(generate_only, 5)
    out = _enforce(plan_id, _cooked(dates),
                                           tools.get_household_memory(), picker=_never)
    assert out["repicked"] == [] and out["left"] == []
    assert _lunches(plan_id)[dates[5]]["meal"] == "Long Lunch"


def test_a_lunch_on_a_standing_prep_day_has_no_cap(generate_only):
    week, dates, plan_id = _one_long_lunch(generate_only, 2)
    memory = dict(tools.get_household_memory(), rhythm={"prep_days": [{"weekday": "wednesday"}]})
    out = _enforce(plan_id, {}, memory, picker=_never)
    assert out["repicked"] == [] and out["left"] == []


def test_a_prep_day_lunch_they_said_is_cooked_IS_capped(generate_only, picker):
    """2026-09-25: the week's answer "cooked" keeps the cap even on a prep day."""
    week, dates, plan_id = _one_long_lunch(generate_only, 2)
    memory = dict(tools.get_household_memory(), rhythm={"prep_days": [{"weekday": "wednesday"}]})
    out = _enforce(plan_id, _answer((dates[2], "cooked")), memory, picker=picker)
    assert [x["date"] for x in out["repicked"]] == [dates[2]]


@pytest.mark.parametrize("kind", ["prepped", "leftovers"])
def test_a_lunch_they_said_is_prepped_or_leftovers_has_no_cap(generate_only, kind):
    week, dates, plan_id = _one_long_lunch(generate_only, 1)
    out = _enforce(plan_id, _answer((dates[1], kind)),
                                           tools.get_household_memory(), picker=_never)
    assert out["repicked"] == [] and out["left"] == []


def test_either_end_of_a_leftovers_chain_has_no_cap(generate_only):
    """A Monday lunch batch reheated on Tuesday: neither is a cook on the day."""
    week = _monday()
    dates = tools._week_dates(week)
    _filler()
    _recipe("Long Lunch", 45)
    tools.save_week_intake(week)
    days = []
    for i, d in enumerate(dates):
        if i == 0:
            days += _day(d, "Long Lunch", derived_from={"make_double_for": [f"{dates[1]}:lunch"]})
        elif i == 1:
            days += _day(d, "Long Lunch", derived_from={"links_to": f"{dates[0]}:lunch"})
        else:
            days += _day(d, "Quick Wrap")
    plan_id = _generate(generate_only, week, days)

    out = _enforce(plan_id, _cooked(dates),
                                           tools.get_household_memory(), picker=_never)
    assert out["repicked"] == []
    lunches = _lunches(plan_id)
    assert lunches[dates[0]]["meal"] == lunches[dates[1]]["meal"] == "Long Lunch"


def test_a_lunch_they_asked_for_by_name_is_left_alone_and_said_why(generate_only):
    week, dates, plan_id = _one_long_lunch(generate_only, 3, derived_from={"freeform": "long lunch thursday"})
    out = _enforce(plan_id, _cooked(dates),
                                           tools.get_household_memory(), picker=_never)
    assert out["repicked"] == []
    assert [(x["slot"], x["why"]) for x in out["left"]] == \
        [("lunch", "the household asked for this one by name")]


def test_a_lunch_already_cooked_is_left_alone(generate_only):
    week, dates, plan_id = _one_long_lunch(generate_only, 3)
    conn = get_conn()
    conn.execute("UPDATE meal_plan_entries SET cooked_status = 'done' WHERE id = ?",
                 (_lunches(plan_id)[dates[3]]["id"],))
    conn.commit()
    conn.close()
    out = _enforce(plan_id, _cooked(dates),
                                           tools.get_household_memory(), picker=_never)
    assert out["repicked"] == []
    assert [x["why"] for x in out["left"]] == ["it is already cooked"]


def test_breakfast_has_no_cap_and_is_never_touched(generate_only):
    """time_caps has no breakfast cap; the pass runs for breakfast and finds nothing."""
    week = _monday()
    dates = tools._week_dates(week)
    _filler()
    _recipe("Slow Porridge", 90)
    tools.save_week_intake(week)
    days = []
    for d in dates:
        days += _day(d, "Quick Wrap", breakfast="Slow Porridge")
    plan_id = _generate(generate_only, week, days)
    out = _enforce(plan_id, _cooked(dates),
                                           tools.get_household_memory(), picker=_never)
    assert out["repicked"] == [] and out["left"] == []


# ---------- 4. budget: one for the week, and the later passes keep theirs ----------

def test_lunch_leaves_the_allergen_sweep_its_reserve(generate_only):
    """Five cooked-that-day lunches over cap and nothing quicker ever comes
    back: the lunch stage spends down to the sweep's two calls and stops."""
    from app.tools import allergen_gate, swap_in_place
    week = _monday()
    dates = tools._week_dates(week)
    _filler()
    _recipe("Long Lunch", 45)
    tools.save_week_intake(week)
    days = []
    for i, d in enumerate(dates):
        days += _day(d, "Long Lunch" if i < 5 else "Quick Wrap")
    plan_id = _generate(generate_only, week, days)
    seen = []

    def _slow(context):
        seen.append(context)
        return {}

    budget = allergen_gate.CallBudget()
    _enforce(plan_id, _cooked(dates), tools.get_household_memory(), picker=_slow, budget=budget)
    assert budget.left == swap_in_place.MAX_PICK_ATTEMPTS
    assert len(seen) == allergen_gate.MAX_REPICK_CALLS - swap_in_place.MAX_PICK_ATTEMPTS


def test_lunch_shares_the_dinners_budget_rather_than_a_fresh_six(generate_only):
    """Dinners over their cap spend the shared budget first; lunch gets what
    is left, never six of its own."""
    from app.tools import allergen_gate, swap_in_place
    tools.edit_preference("weeknight_max_minutes", 20)
    week = _monday()
    dates = tools._week_dates(week)
    _filler()
    _recipe("Long Braise", 90)
    _recipe("Long Lunch", 45)
    tools.save_week_intake(week)
    days = []
    for i, d in enumerate(dates):
        days += _day(d, "Long Lunch" if i < 5 else "Quick Wrap",
                     dinner="Long Braise" if i < 5 else "Quick Eggs")
    plan_id = _generate(generate_only, week, days)
    seen = []

    def _slow(context):
        seen.append(context)
        return {}

    budget = allergen_gate.CallBudget()
    _enforce(plan_id, _cooked(dates), tools.get_household_memory(), picker=_slow, budget=budget)
    slots = [c.get("slot") for c in seen]
    assert slots[0] == "dinner" and "lunch" in slots
    assert slots == sorted(slots, key=lambda x: x != "dinner"), "every dinner call before any lunch call"
    assert budget.left == swap_in_place.MAX_PICK_ATTEMPTS, "one budget, down to the sweep's reserve"


def test_an_open_dinner_is_still_planned_after_the_lunch_stage(stub_model, monkeypatch):
    """
    The adversarial review's case (2026-10-02): every lunch over its cap and
    one dinner the model handed back open. The lunch stage must leave the
    dinner fill its pick — an open dinner is Emily's decision A. Every other
    night is Chili, so the fill has to spend a model call.
    """
    from test_week_generation import _full_week, _slots_for, _week_start  # noqa: F401
    from app import agent
    from app.tools import swap_in_place as sip
    monkeypatch.setattr(sip, "_pick_replacement", lambda ctx: {
        "meal_name": "Quick Frittata", "reason": "quick", "prep_time_minutes": 5, "cook_time_minutes": 10,
        "ingredients": [{"item": "eggs", "qty": "6", "category": "dairy"}], "instructions": ["Cook."],
    })
    tools.add_recipe("Chili", ingredients=[{"item": "beans", "qty": "1 tin"}],
                     prep_time_minutes=10, cook_time_minutes=20)  # 30: over a lunch's 20
    week = _week_start()
    dates = tools._week_dates(week)
    wednesday = dates[2]
    tools.save_week_intake(week, weekday_lunches={"days": [{"date": d, "kind": "cooked"} for d in dates[:5]]})
    days = [d for d in _full_week(week) if not (d["date"] == wednesday and d["slot"] == "dinner")]
    days.append({
        "date": wednesday, "slot": "dinner", "meal_name": "", "is_new_recipe": False,
        "reasoning": "", "slot_state": "open", "open_reason": "I'd rather ask.",
    })
    stub_model(days)
    from app.tools import dinner_gaps
    real_fill = dinner_gaps.fill_open_dinners
    fills = []

    def _spy(*a, **k):
        fills.append(real_fill(*a, **k))
        return fills[-1]

    monkeypatch.setattr(agent._dinner_gaps, "fill_open_dinners", _spy)

    plan = agent.generate_weekly_plan(week)

    assert _slots_for(plan["weekly_plan_id"])[(wednesday, "dinner")]["slot_state"] == "planned"
    # And planned by the FIRST fill, with its fresh pick — not rescued by
    # the fill after the sweep spending the sweep's own reserve (what the
    # first version of the hold let happen; the review measured it).
    assert fills and fills[0]["left"] == [] and [r["date"] for r in fills[0]["repicked"]] == [wednesday]


def test_the_hold_counts_a_dinner_generation_never_wrote(generate_only):
    """The model's open dinner is left UNWRITTEN by generation, so the gap
    is a date with no dinner row; the hold counts it as fill_open_dinners
    does, plus the sweep's reserve. An allergen-kept question is not a gap."""
    from app.tools import swap_in_place
    week, dates, plan_id = _one_long_lunch(generate_only, 0)
    conn = get_conn()
    conn.execute("DELETE FROM meal_plan_entries WHERE weekly_plan_id = ? AND slot = 'dinner' AND date = ?",
                 (plan_id, dates[2]))
    conn.commit()
    conn.close()
    assert cap_enforce._held_for_later_passes(plan_id, dates) == 2 * swap_in_place.MAX_PICK_ATTEMPTS
    assert cap_enforce._held_for_later_passes(plan_id, [d for d in dates if d != dates[2]]) == \
        swap_in_place.MAX_PICK_ATTEMPTS, "a day the generation isn't planning is not a gap"


# ---------- 5. the cap told to the model ----------

def test_the_model_is_told_it_is_a_lunch_cooked_that_day(generate_only, picker):
    week, dates, plan_id = _one_long_lunch(generate_only, 0)
    _enforce(plan_id, _cooked(dates),
                                     tools.get_household_memory(), picker=picker)
    assert picker.calls
    because = picker.calls[0].get("replacing_because") or ""
    assert "lunch" in because and "20 minutes" in because


# ---------- 6. the assumption: only a lunch they SAID is cooked ----------

def test_an_unanswered_weekday_lunch_is_only_recorded(generate_only):
    """
    ASSUMPTION, Emily's to widen. time_caps caps an unanswered weekday lunch
    too, and plan_quality warns about it; this pass re-picks only a lunch the
    household answered "cooked that day", and records the rest in `left`.
    """
    week, dates, plan_id = _one_long_lunch(generate_only, 1)
    out = _enforce(plan_id, tools.get_week_intake(week), tools.get_household_memory(), picker=_never)
    assert out["repicked"] == []
    assert out["left"] == [{"date": dates[1], "slot": "lunch", "meal": "Long Lunch", "minutes": 45,
                            "cap": 20, "why": "the household hasn't said this lunch is cooked that day"}]
    assert len(_lunch_warnings(plan_id, tools.get_week_intake(week))) == 1, "and it is still warned about"
