"""
"Different dishes a week" holds on every draft, and fewer dishes means
leftovers, not more cooking.

Emily, 2026-09-28, phone test. Her settings: Dinners 3 · Breakfasts 1 ·
Lunches 2 · Snacks 2, a six-day week (Mon–Sat), Monday short on time, lunch
prep days Sunday and Tuesday, Friday's lunch "Leftovers from dinner".

  "My dinner settings is 3 but it gave me 4 meal types. And lunch is 2 and
   I have 3 types suggested. Why does this keep happening."
  "It seems like it doesn't do a great job of using the leftover concept."

The third report of the same number being broken (2026-09-13 dinners,
2026-09-27 lunches and snacks). The count pass ran once, and passes AFTER it
could put a new dish on a night — cap_enforce's rush-night re-pick (one
night of a dish that was on other nights too), the typed-ingredient and
cuisine re-picks, a fresh pick for an open dinner — with nothing checking
the number again. These tests are the guard: they run her shape through
every route that makes a draft, and each fails on origin/main 8955da1.

Every test stubs the model (the week) and the swap picker (a re-pick); no
test reaches the API.
"""
from __future__ import annotations

import datetime
import json

import pytest

from app import agent, tools
from app.db import get_conn
from app.tools import dinner_gaps, leftovers, meal_variety, plan_quality, swap_in_place


def _monday(offset_weeks: int = 1) -> str:
    today = datetime.date.today()
    monday = today - datetime.timedelta(days=today.weekday())
    return (monday + datetime.timedelta(days=7 * offset_weeks)).isoformat()


def _slot(date, slot, name, minutes=30, links=None):
    out = {"date": date, "slot": slot, "meal_name": name, "is_new_recipe": True,
           "ingredients": [{"item": f"{name} stuff", "qty": "2 lb", "category": "pantry"}],
           "reasoning": f"{name} because", "food_groups": ["protein", "vegetable", "carb"],
           "prep_time_minutes": 10, "cook_time_minutes": minutes - 10}
    if links:
        out["derived_from"] = {"links_to": links}
    return out


def _week(dates, dinners, lunches, dinner_minutes=45):
    """Emily's shape: one breakfast dish, her lunches, the given dinners, two
    snacks a day. A lunch given as None is Friday's leftovers of Thursday's
    dinner, sent by the model as a link."""
    out = []
    for i, d in enumerate(dates):
        out.append(_slot(d, "breakfast", "Veggie Scramble", 18))
        if lunches[i] is None:
            out.append(_slot(d, "lunch", dinners[i - 1], 20, links=f"{dates[i - 1]}:dinner"))
        else:
            out.append(_slot(d, "lunch", lunches[i], 20))
        out.append(_slot(d, "dinner", dinners[i], dinner_minutes))
        out.append(_slot(d, "snack", ["Apple", "Yogurt", "Nuts"][i % 3], 5))
        out.append(_slot(d, "snack", ["Hummus", "Cheese", "Berries"][i % 3], 5))
    return out


# A-B-C-A-B-C: three dinners, each on two nights more than a day apart —
# the shape a rush-night re-pick used to turn into four.
ABCABC = ["Creamy Chicken Stew", "Beef Tacos", "Green Curry"] * 2
LUNCHES = ["Turkish-Style Lentil Soup", "Turkish-Style Lentil Soup", "Corn and Chicken Pancake",
           "Corn and Chicken Pancake", None, "Turkish-Style Lentil Soup"]


@pytest.fixture
def emily():
    """Her household and her answers for the week."""
    for name in ("Emily", "Vineeth"):
        tools.add_member(name)
        tools.set_member_age_group(name, "adult")
    tools.set_household_meal_preferences(dinners_per_week=3, breakfasts_per_week=1, lunches_per_week=2)
    tools.edit_preference("snack_dishes_per_week", 2)
    mon = _monday()
    dates = tools.period_dates(mon, 6)
    tools.save_week_intake(mon, day_count=6, night_tags={dates[0]: ["rush"]}, weekday_lunches={
        "prep_days": ["sunday", "tuesday"],
        "days": [{"date": dates[0], "kind": "prepped"}, {"date": dates[1], "kind": "prepped"},
                 {"date": dates[2], "kind": "prepped"}, {"date": dates[3], "kind": "prepped"},
                 {"date": dates[4], "kind": "leftovers"}],
    })
    return mon, dates


@pytest.fixture
def picker(monkeypatch):
    """Every re-pick in generation goes through swap_in_place's picker; this
    one hands back quick dishes, never the same one twice, carrying whatever
    the pick was asked to contain."""
    calls = []
    names = [f"Quick Dish {i}" for i in range(1, 40)]

    def _pick(context):
        calls.append(context)
        avoid = {a.strip().lower() for a in (context.get("avoid") or [])}
        must = list(context.get("must_contain") or [])
        while names:
            name = names.pop(0)
            if name.lower() in avoid:
                continue
            if must:
                name = f"{name} with {' and '.join(must)}"
            return {"meal_name": name, "reason": "quick on the night",
                    "ingredients": [{"item": f"{name} stuff", "qty": "1", "category": "pantry"}]
                    + [{"item": m, "qty": "1", "category": "produce"} for m in must],
                    "instructions": [f"Cook {name}.", "Serve."],
                    "food_groups": ["protein", "vegetable", "carb"],
                    "prep_time_minutes": 0, "cook_time_minutes": 15}
        return {}
    monkeypatch.setattr(swap_in_place, "_pick_replacement", _pick)
    return calls


def _stub(monkeypatch, days):
    monkeypatch.setattr(agent, "generate_weekly_plan_llm", lambda context: days)


def _snack_dishes(plan_id: int) -> set[str]:
    conn = get_conn()
    rows = conn.execute(
        """SELECT COALESCE(r.name, mpe.freeform_meal) AS meal FROM meal_plan_entries mpe
           LEFT JOIN recipes r ON r.id = mpe.recipe_id
           WHERE mpe.weekly_plan_id = ? AND mpe.slot = 'snack' AND mpe.slot_state = 'planned'""",
        (plan_id,)).fetchall()
    conn.close()
    return {r["meal"].strip().lower() for r in rows if r["meal"]}


def _assert_her_numbers(plan_id: int):
    counts = {slot: meal_variety.distinct_dishes(plan_id, slot) for slot in ("dinner", "lunch", "breakfast")}
    assert len(counts["dinner"]) <= 3, f"Dinners is 3: {counts['dinner']}"
    assert len(counts["lunch"]) <= 2, f"Lunches is 2: {counts['lunch']}"
    assert len(counts["breakfast"]) <= 1, f"Breakfasts is 1: {counts['breakfast']}"
    assert len(_snack_dishes(plan_id)) <= 2, f"Snacks is 2: {_snack_dishes(plan_id)}"


def _rows(plan_id: int, slot: str) -> dict[str, dict]:
    conn = get_conn()
    rows = conn.execute(
        """SELECT mpe.id, mpe.date, mpe.slot, mpe.slot_state, mpe.derived_from_json,
                  COALESCE(r.name, mpe.freeform_meal) AS meal
           FROM meal_plan_entries mpe LEFT JOIN recipes r ON r.id = mpe.recipe_id
           WHERE mpe.weekly_plan_id = ? AND mpe.slot = ? AND mpe.component_category IS NULL
           ORDER BY mpe.date, mpe.id""", (plan_id, slot)).fetchall()
    conn.close()
    return {r["date"]: dict(r, derived=json.loads(r["derived_from_json"] or "{}")) for r in rows}


def _cooks(plan_id: int, slot: str) -> list[dict]:
    chains = leftovers.plan_leftover_chains(plan_id)
    return [r for r in _rows(plan_id, slot).values()
            if r["slot_state"] == "planned" and r["id"] not in chains["leftovers"]
            and not leftovers.frozen_portion_on(r["derived"])]


# ==========================================================================
# 1. Her numbers hold on every route that makes a draft
# ==========================================================================

def _via_agent(mon, client):
    return agent.generate_weekly_plan(mon, day_count=6)["weekly_plan_id"]


def _via_generate(mon, client):
    res = client.post(f"/api/week/{mon}/generate", json={"day_count": 6})
    assert res.status_code == 200, res.text
    return tools.get_plan_id_for_week(mon)


def _via_stream(mon, client):
    with client.stream("POST", f"/api/week/{mon}/generate/stream", json={"day_count": 6}) as res:
        assert res.status_code == 200
        body = "".join(res.iter_text())
    assert "event: error" not in body, body
    return tools.get_plan_id_for_week(mon)


def _via_replan(mon, client):
    # Re-plan: a second draft over the first.
    _via_generate(mon, client)
    return _via_generate(mon, client)


@pytest.mark.parametrize("route", [_via_agent, _via_generate, _via_stream, _via_replan],
                         ids=["chat-tool", "generate", "stream", "re-plan"])
def test_her_numbers_hold_on_every_route(route, emily, picker, monkeypatch, signed_in):
    """CATCH on main: Monday is short on time and Monday's 45-minute stew is
    also Thursday's dinner, so the rush re-pick put a fourth dinner on the
    week after the count pass had run."""
    mon, dates = emily
    _stub(monkeypatch, _week(dates, ABCABC, LUNCHES))
    plan_id = route(mon, signed_in)
    _assert_her_numbers(plan_id)
    # And the rush night was still made to fit — the count did not win by
    # leaving Monday over its cap.
    monday = _rows(plan_id, "dinner")[dates[0]]
    assert monday["meal"] != "Creamy Chicken Stew"


def test_a_typed_ingredient_re_pick_does_not_add_a_dinner(emily, picker, monkeypatch):
    """CATCH on main (found by the root-cause verifier): "I have some corn"
    re-picked ONE night of a two-night dish into a corn dish — a fourth
    dinner. The corn dish stays (it is what they asked for); another dish
    folds around it."""
    mon, dates = emily
    tools.save_week_intake(mon, night_tags={}, freeform="I have some corn so use it in a dinner")
    _stub(monkeypatch, _week(dates, ABCABC, LUNCHES, dinner_minutes=25))
    plan_id = agent.generate_weekly_plan(mon, day_count=6)["weekly_plan_id"]
    dinners = meal_variety.distinct_dishes(plan_id, "dinner")
    assert len(dinners) <= 3, dinners
    all_meals = [r["meal"] for s in ("lunch", "dinner") for r in _rows(plan_id, s).values()]
    assert any("corn" in (m or "").lower() for m in all_meals), "the corn they typed is still used"


def test_an_open_dinner_is_not_filled_with_a_new_dish_when_the_week_has_its_number(emily, picker):
    """CATCH on main: fill_open_dinners' fresh pick had no count check."""
    mon, dates = emily
    plan_id = tools.create_weekly_plan(mon)["weekly_plan_id"]
    for d, name in zip(dates[:5], ["Stew", "Tacos", "Curry", "Stew", "Tacos"]):
        tools.plan_meal(meal_date=d, meal=name, slot="dinner", weekly_plan_id=plan_id)
    before = len(picker)
    dinner_gaps.fill_open_dinners(plan_id, [dates[5]], targets={"dinner": 3})
    assert len(picker) == before, "no fresh pick when the week already has three dinners"
    assert len(meal_variety.distinct_dishes(plan_id, "dinner")) == 3
    assert _rows(plan_id, "dinner")[dates[5]]["slot_state"] == "planned"


# ==========================================================================
# 2. What "different dishes" counts
# ==========================================================================

def test_a_lunch_eating_last_nights_dinner_is_not_a_lunch_dish(emily, picker, monkeypatch):
    """Her third lunch "type" was Friday's leftovers of Thursday's dinner.
    It is the dinner: the lunch count never counts or folds it — on the
    answered-lunches path and on the ordinary one alike (until 2026-09-28
    the ordinary path counted it under the dinner's name)."""
    mon, dates = emily
    _stub(monkeypatch, _week(dates, ["Stew", "Stew", "Tacos", "Tacos", "Curry", "Curry"], LUNCHES))
    plan_id = agent.generate_weekly_plan(mon, day_count=6)["weekly_plan_id"]
    lunches = meal_variety.distinct_dishes(plan_id, "lunch")
    assert "Tacos" not in lunches and len(lunches) <= 2
    friday = _rows(plan_id, "lunch")[dates[4]]
    assert leftovers.plan_leftover_chains(plan_id)["leftovers"][friday["id"]]["source"]["slot"] == "dinner"


def test_the_ordinary_lunch_pass_leaves_a_dinner_reheat_alone():
    tools.add_member("Emily")
    tools.set_household_meal_preferences(lunches_per_week=1)
    mon = _monday()
    dates = tools.period_dates(mon, 3)
    plan_id = tools.create_weekly_plan(mon)["weekly_plan_id"]
    dinner = tools.plan_meal(meal_date=dates[0], meal="Chili", slot="dinner", weekly_plan_id=plan_id,
                             derived_from={"make_double_for": [f"{dates[1]}:lunch"]})
    tools.plan_meal(meal_date=dates[0], meal="Soup", slot="lunch", weekly_plan_id=plan_id)
    tools.plan_meal(meal_date=dates[1], meal="Chili", slot="lunch", weekly_plan_id=plan_id,
                    derived_from={"links_to": f"entry_id:{dinner['entry_id']}"})
    tools.plan_meal(meal_date=dates[2], meal="Soup", slot="lunch", weekly_plan_id=plan_id)
    assert meal_variety.distinct_dishes(plan_id, "lunch") == ["Soup"]
    out = meal_variety.enforce_distinct_count(plan_id, 1, slot="lunch", fill_up=False)
    assert out["before"] == 1, "the Chili lunch is the dinner, not a second lunch dish"
    assert _rows(plan_id, "lunch")[dates[1]]["meal"] == "Chili"


def test_the_morning_report_says_so_if_a_count_is_ever_broken_again():
    tools.add_member("Emily")
    tools.set_household_meal_preferences(dinners_per_week=2)
    mon = _monday()
    plan_id = tools.create_weekly_plan(mon)["weekly_plan_id"]
    for d, name in zip(tools.period_dates(mon, 3), ["Stew", "Tacos", "Curry"]):
        tools.plan_meal(meal_date=d, meal=name, slot="dinner", weekly_plan_id=plan_id)
    memory = tools.get_household_memory()
    found = plan_quality.dish_counts_respected(plan_id, memory)
    assert [v.rule for v in found] == ["dish_count_respected"] and found[0].slot == "dinner"
    # A count they named in their own words for this week stands it down.
    assert plan_quality.dish_counts_respected(plan_id, memory, ("three different dinners please",)) == []


# ==========================================================================
# 3. Fewer dishes means leftovers, not more cooking
# ==========================================================================

@pytest.mark.parametrize("dinners", [
    ["Stew", "Stew", "Tacos", "Tacos", "Curry", "Curry"],     # the model follows the number
    ["Stew", "Tacos", "Curry", "Pasta", "Chili", "Roast"],   # six different, folded to three
], ids=["follows-the-number", "six-different"])
def test_three_dinners_over_six_nights_is_three_cooks(dinners, emily, picker, monkeypatch):
    """With 3 dinners over 6 nights every dish is cooked once: three cooks,
    the other three nights leftovers of them, none open, and never a dish
    on three meals in a row. CATCH on main for follows-the-number: Tacos was
    cooked Wednesday AND again Thursday, because Thursday fed Friday's lunch.

    Her week preps Sunday and Tuesday, so since Emily's Option B
    (2026-09-30, "cook on Friday") it is three dishes and FOUR cooks: Friday
    is one of the three cooked a second time, so Saturday's lunch eats it
    from the fridge (tests/test_leftovers_weekend_and_even_spread.py)."""
    mon, dates = emily
    tools.save_week_intake(mon, night_tags={})
    _stub(monkeypatch, _week(dates, dinners, LUNCHES, dinner_minutes=25))
    plan_id = agent.generate_weekly_plan(mon, day_count=6)["weekly_plan_id"]
    cooks = _cooks(plan_id, "dinner")
    assert len(cooks) == 4 and cooks[-1]["date"] == dates[4], [(c["date"], c["meal"]) for c in cooks]
    assert len({c["meal"] for c in cooks}) == 3, "three dishes, Friday's cooked a second time"
    rows = _rows(plan_id, "dinner")
    chains = leftovers.plan_leftover_chains(plan_id)
    for d in dates:
        assert rows[d]["slot_state"] == "planned"
        if rows[d]["id"] not in {c["id"] for c in cooks}:
            assert rows[d]["id"] in chains["leftovers"], f"{d} is labelled as leftovers"
    assert leftovers.long_runs(leftovers.run_keys(plan_id)) == []
    # "Leftovers from dinner: 1" — Friday's lunch reheats a dinner.
    friday = _rows(plan_id, "lunch")[dates[4]]
    assert chains["leftovers"][friday["id"]]["source"]["slot"] == "dinner"


def test_a_dinner_that_feeds_a_lunch_is_cooked_once_for_both(emily, picker, monkeypatch):
    """Friday's lunch is last night's dinner, and that dinner is cooked once
    for every meal it feeds — not cooked again the day after the same dish.
    (Until 2026-09-29 this pinned Wednesday's Tacos feeding Thursday dinner
    and Friday lunch; Emily's decision that Saturday's lunch eats Friday's
    dinner moved the week to Stew, Stew, Tacos, Curry, Tacos, Curry — see
    tests/test_leftovers_weekend_and_even_spread.py.)"""
    mon, dates = emily
    tools.save_week_intake(mon, night_tags={})
    _stub(monkeypatch, _week(dates, ["Stew", "Stew", "Tacos", "Tacos", "Curry", "Curry"], LUNCHES,
                             dinner_minutes=25))
    plan_id = agent.generate_weekly_plan(mon, day_count=6)["weekly_plan_id"]
    dinners, lunches = _rows(plan_id, "dinner"), _rows(plan_id, "lunch")
    chains = leftovers.plan_leftover_chains(plan_id)
    thu = dinners[dates[3]]
    friday_lunch = chains["leftovers"][lunches[dates[4]]["id"]]["source"]
    assert friday_lunch["slot"] == "dinner" and friday_lunch["meal"] == thu["meal"]
    cook_id = chains["leftovers"].get(thu["id"], {}).get("source", {}).get("entry_id", thu["id"])
    assert friday_lunch["entry_id"] == cook_id, "Friday's lunch eats the same pot as Thursday's dinner"
    cooks = _cooks(plan_id, "dinner")
    # Option B (2026-09-30): Friday is a fourth cook, of one of the three dishes.
    assert len(cooks) == 4 and len({c["meal"] for c in cooks}) == 3


def test_a_rush_night_whose_dish_they_asked_for_elsewhere_keeps_the_count(emily, picker, monkeypatch):
    """Review, 2026-09-28: when the rush night's dish is also on a night
    they asked for by name, it can't be re-picked whole — and re-picking
    Monday alone would be a fourth dinner. Their number wins; Monday stands
    (plan_quality's cap warning says so)."""
    mon, dates = emily
    days = _week(dates, ABCABC, LUNCHES)
    for d in days:
        if d["date"] == dates[3] and d["slot"] == "dinner":
            d["derived_from"] = {"freeform": "creamy chicken stew on Thursday"}
    _stub(monkeypatch, days)
    plan_id = agent.generate_weekly_plan(mon, day_count=6)["weekly_plan_id"]
    assert len(meal_variety.distinct_dishes(plan_id, "dinner")) <= 3


def test_a_night_carrying_freezer_portions_is_not_folded_into_an_earlier_cook():
    """Review, 2026-09-28: a night that cooks extra portions for the freezer
    stays a cook — a reheat cooks nothing, so the portions would never be made."""
    tools.add_member("Emily")
    mon = _monday()
    dates = tools.period_dates(mon, 7)
    plan_id = tools.create_weekly_plan(mon)["weekly_plan_id"]
    tools.plan_meal(meal_date=dates[0], meal="Stew", slot="dinner", weekly_plan_id=plan_id)
    tue = tools.plan_meal(meal_date=dates[1], meal="Stew", slot="dinner", weekly_plan_id=plan_id,
                          derived_from={"make_double_for": [f"{dates[3]}:dinner"],
                                        leftovers.FREEZER_EXTRA_KEY: {"servings": 1, "for": [f"{dates[6]}:dinner"]}})
    tools.plan_meal(meal_date=dates[2], meal="Tacos", slot="dinner", weekly_plan_id=plan_id)
    tools.plan_meal(meal_date=dates[3], meal="Stew", slot="dinner", weekly_plan_id=plan_id,
                    derived_from={"links_to": f"entry_id:{tue['entry_id']}"})
    tools.plan_meal(meal_date=dates[4], meal="Tacos", slot="dinner", weekly_plan_id=plan_id)
    meal_variety.enforce_distinct_count(plan_id, 2, slot="dinner", fill_up=False)
    tuesday = _rows(plan_id, "dinner")[dates[1]]
    assert not tuesday["derived"].get("links_to"), "Tuesday still cooks its freezer portion"
