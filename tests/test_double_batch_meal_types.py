"""
Fewer recipes than meals means batch cooking.

Emily, 2026-09-23 ("Decision E"): "yes - encourage double batch for
leftovers, that's the point of indicating less meal types for number of
meals. If I want 2 types of lunches, but need 4 lunches, you should assume
Im making double of each of the recipes. thats the batch cooking point".
Decided with it:
  * a dish the household asked for once is planned once — never copied as
    a second fresh cook (prod plan 61 got a second Korean Chicken Pancake);
  * leftovers are eaten within three days of the cook, else frozen;
  * Pomona does the planning and says what it did in one plain line.

Every test here is red against main (ef6108b): there the fold wrote a
second, independent cooking of a kept dish (bought per night), a repeated
entry's days were independent copies, a chain could reach any distance,
and the draft said nothing. The model call is stubbed throughout.
"""
from __future__ import annotations

import datetime
import json

import pytest

from app import agent, tools
from app.db import get_conn
from app.tools import cook_ahead, draft_opener, leftovers, meal_variety


def _monday(offset_weeks: int = 1) -> str:
    today = datetime.date.today()
    monday = today - datetime.timedelta(days=today.weekday())
    return (monday + datetime.timedelta(days=7 * offset_weeks)).isoformat()


def _gap(a: str, b: str) -> int:
    return (datetime.date.fromisoformat(b) - datetime.date.fromisoformat(a)).days


def _slot(date, slot, name, **extra):
    return {"date": date, "slot": slot, "meal_name": name, "is_new_recipe": True,
            "ingredients": [{"item": f"{name} stuff", "qty": "2 lb", "category": "pantry"}],
            "reasoning": f"{name} because", "food_groups": ["protein", "vegetable", "carb"],
            "prep_time_minutes": 10, "cook_time_minutes": 20, **extra}


def _days(dates, *, breakfasts, lunches, dinners) -> list[dict]:
    out = []
    for i, date in enumerate(dates):
        out.append(_slot(date, "breakfast", breakfasts[i]))
        out.append(_slot(date, "lunch", lunches[i]))
        out.append(_slot(date, "dinner", dinners[i]))
    return out


@pytest.fixture
def four_adults():
    for name in ("Emily", "Vineeth", "Nana", "Sam"):
        tools.add_member(name)
        tools.set_member_age_group(name, "adult")


@pytest.fixture
def stub_model(monkeypatch):
    def _stub(days):
        monkeypatch.setattr(agent, "generate_weekly_plan_llm", lambda ctx: days)
    return _stub


def _rows(plan_id: int, slot: str) -> list[dict]:
    conn = get_conn()
    rows = conn.execute(
        """
        SELECT mpe.id, mpe.date, mpe.slot, mpe.slot_state, mpe.derived_from_json,
               COALESCE(r.name, mpe.freeform_meal) AS meal
        FROM meal_plan_entries mpe LEFT JOIN recipes r ON r.id = mpe.recipe_id
        WHERE mpe.weekly_plan_id = ? AND mpe.slot = ? AND mpe.component_category IS NULL
        ORDER BY mpe.date, mpe.id
        """,
        (plan_id, slot),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def _cooks(plan_id: int, slot: str) -> list[dict]:
    """The nights of a slot something is actually cooked on."""
    chains = tools.plan_leftover_chains(plan_id)
    return [r for r in _rows(plan_id, slot)
            if r["slot_state"] == "planned" and r["id"] not in chains["leftovers"]
            and not (json.loads(r["derived_from_json"] or "{}").get(leftovers.FROM_FREEZER_KEY))]


def _links(plan_id: int, slot: str) -> dict[str, dict]:
    return {v["date"]: v["source"] for v in tools.plan_leftover_chains(plan_id)["leftovers"].values()
            if v["slot"] == slot}


def _ledger_entries() -> set[int]:
    conn = get_conn()
    rows = conn.execute("SELECT DISTINCT meal_plan_entry_id FROM meal_plan_grocery_links").fetchall()
    conn.close()
    return {r[0] for r in rows}


def _line(item: str) -> str | None:
    for g in tools.list_grocery_list():
        if g["item"] == item:
            return g["quantity"]
    return None


# ---------- 1. two lunch recipes over four lunches ----------

@pytest.mark.parametrize("lunches", [
    ["Wrap", "Soup", "Salad", "Sandwich"],   # four distinct: two folded
    ["Wrap", "Soup", "Wrap", "Soup"],        # already on the count, sent as repeats
])
def test_two_lunch_types_for_four_lunches_are_two_cooks_each_made_double(four_adults, stub_model, lunches):
    tools.set_household_meal_preferences(lunches_per_week=3)  # 3 a week over 4 days = 2
    week = _monday()
    dates = tools._week_dates(week)[1:5]  # Tue–Fri
    stub_model(_days(dates, breakfasts=["Oats", "Eggs", "Toast", "Granola"], lunches=lunches,
                     dinners=["Chili", "Tacos", "Curry", "Pasta"]))

    plan_id = agent.generate_weekly_plan(week, day_count=4, period_start=dates[0])["weekly_plan_id"]

    cooks = _cooks(plan_id, "lunch")
    assert [(c["date"], c["meal"]) for c in cooks] == [(dates[0], "Wrap"), (dates[1], "Soup")]
    links = _links(plan_id, "lunch")
    assert sorted(links) == [dates[2], dates[3]]
    assert {links[d]["meal"] for d in links} == {"Wrap", "Soup"}, "each recipe cooked double, not one tripled"
    assert all(1 <= _gap(s["date"], d) <= 3 for d, s in links.items())
    assert tools.audit_plan_slots(plan_id)["complete"] is True
    assert "Two lunches, each cooked double." in tools.get_week_menu(plan_id)["draft_opener"]

    tools.approve_weekly_plan(plan_id, approved_by="Emily")
    # Bought once per recipe, on the cook, scaled to the batch: four adults
    # on a four-serving recipe is 2 lb a meal, so 4 lb for two meals.
    ledger = _ledger_entries()
    reheat_ids = set(tools.plan_leftover_chains(plan_id)["leftovers"])
    assert not (reheat_ids & ledger), "a leftovers night buys nothing"
    assert {c["id"] for c in cooks} <= ledger
    assert _line("Wrap stuff") == "4 lbs"
    assert _line("Soup stuff") == "4 lbs"


# ---------- 2. two dinner recipes over five nights ----------

def test_two_dinner_types_for_five_nights_never_link_more_than_three_days_apart(four_adults, stub_model):
    tools.set_household_meal_preferences(dinners_per_week=2)  # 2 a week over 5 days = 2
    week = _monday()
    dates = tools._week_dates(week)[:5]
    stub_model(_days(dates, breakfasts=["Oats"] * 5, lunches=["Wrap"] * 5,
                     dinners=["Chili", "Tacos", "Curry", "Pasta", "Stew"]))

    plan_id = agent.generate_weekly_plan(week, day_count=5, period_start=dates[0])["weekly_plan_id"]

    rows = [r for r in _rows(plan_id, "dinner") if r["slot_state"] == "planned"]
    assert len(rows) == 5
    links = _links(plan_id, "dinner")
    assert links, "the freed nights are leftovers"
    assert all(1 <= _gap(s["date"], d) <= 3 for d, s in links.items())
    assert {c["meal"] for c in _cooks(plan_id, "dinner")} == {"Chili", "Tacos"}
    assert len(_cooks(plan_id, "dinner")) == 2, "two recipes, two cooks"


def test_two_dinners_over_seven_nights_relay_the_cooks_rather_than_freeze(four_adults, stub_model):
    """Two cooks can't feed seven nights while alternating inside three
    days, so the cooks are moved: one run of four, one of three."""
    tools.set_household_meal_preferences(dinners_per_week=2)
    week = _monday()
    dates = tools._week_dates(week)
    stub_model(_days(dates, breakfasts=["Oats"] * 7, lunches=["Wrap"] * 7,
                     dinners=["Chili", "Tacos", "Curry", "Pasta", "Stew", "Kofte", "Salmon"]))

    plan_id = agent.generate_weekly_plan(week)["weekly_plan_id"]

    assert [(c["date"], c["meal"]) for c in _cooks(plan_id, "dinner")] == [(dates[0], "Chili"), (dates[4], "Tacos")]
    links = _links(plan_id, "dinner")
    assert len(links) == 5 and all(1 <= _gap(s["date"], d) <= 3 for d, s in links.items())


# ---------- 3. a requested dish is cooked once ----------

def test_a_requested_dish_is_never_copied_as_a_fresh_cook(four_adults, stub_model):
    """Prod plan 61's shape: three dinners a week, one asked for by name.
    The old fold put the requested dish on a freed night as a second
    cook, because it was the kept dish furthest from that night."""
    tools.set_household_meal_preferences(dinners_per_week=3)
    week = _monday()
    dates = tools._week_dates(week)
    days = _days(dates, breakfasts=["Oats"] * 7, lunches=["Wrap"] * 7,
                 dinners=["Chili", "Salmon", "Korean Chicken Pancake", "Kofte", "Tacos", "Burgers", "Shrimp"])
    for d in days:
        if d["slot"] == "dinner" and d["meal_name"] == "Korean Chicken Pancake":
            d["derived_from"] = {"freeform": "korean chicken pancake"}
    stub_model(days)

    plan_id = agent.generate_weekly_plan(week)["weekly_plan_id"]

    pancake_cooks = [c for c in _cooks(plan_id, "dinner") if c["meal"] == "Korean Chicken Pancake"]
    assert [c["date"] for c in pancake_cooks] == [dates[2]], "cooked once, on the night it was asked for"
    assert meal_variety.enforce_distinct_count(plan_id, 3)["after"] == 3
    rows = [r for r in _rows(plan_id, "dinner") if r["slot_state"] == "planned"]
    assert len(rows) == 7, "every night still fed"
    assert all(1 <= _gap(s["date"], d) <= 3 for d, s in _links(plan_id, "dinner").items())


def test_a_requested_dish_repeated_past_three_days_goes_to_the_freezer_not_a_second_cook(four_adults, stub_model):
    """The model repeats the requested dish a week apart and nothing else
    can feed that night: it eats a portion frozen on the first cook."""
    tools.set_household_meal_preferences(dinners_per_week=1)
    week = _monday()
    dates = tools._week_dates(week)
    days = _days(dates, breakfasts=["Oats"] * 7, lunches=["Wrap"] * 7, dinners=["Chili"] * 7)
    for d in days:
        if d["slot"] == "dinner" and d["date"] == dates[0]:
            d["derived_from"] = {"freeform": "chili"}
    stub_model(days)

    plan_id = agent.generate_weekly_plan(week)["weekly_plan_id"]

    assert [c["date"] for c in _cooks(plan_id, "dinner")] == [dates[0]]
    chains = tools.plan_leftover_chains(plan_id)
    monday = _rows(plan_id, "dinner")[0]
    assert chains["freezer"][monday["id"]] == 4 * 3, "three nights' portions for four, frozen on Monday"
    frozen = [r for r in _rows(plan_id, "dinner") if r["meal"].startswith("Leftovers from the freezer")]
    assert [r["meal"] for r in frozen] == [leftovers.freezer_night_name("Chili", dates[0])] * 3


# ---------- 4. a repeated entry is one cook ----------

def test_one_entry_on_several_days_is_one_cook_and_leftovers_within_three_days(stub_model):
    week = _monday()
    dates = tools._week_dates(week)
    days = [_slot(dates[0], "lunch", "Lentil Soup", dates=dates[:5]),
            _slot(dates[5], "lunch", "Wrap", dates=dates[5:]),
            _slot(dates[0], "breakfast", "Oats", dates=dates[:3]),
            _slot(dates[3], "breakfast", "Eggs", dates=dates[3:])]
    days += [_slot(d, "dinner", f"Dinner {i}") for i, d in enumerate(dates)]
    stub_model(days)

    plan_id = agent.generate_weekly_plan(week)["weekly_plan_id"]

    lunch = _links(plan_id, "lunch")
    # Monday cooks for Tue–Thu; Friday is four days on, so it cooks again.
    assert {d: s["date"] for d, s in lunch.items()} == {
        dates[1]: dates[0], dates[2]: dates[0], dates[3]: dates[0], dates[6]: dates[5],
    }
    assert [c["date"] for c in _cooks(plan_id, "lunch")] == [dates[0], dates[4], dates[5]]
    # A breakfast batch is "made ahead" — the one breakfast chain the repair takes.
    breakfast = tools.plan_leftover_chains(plan_id)["leftovers"]
    assert any(v["slot"] == "breakfast" and v["cook_ahead"] for v in breakfast.values())
    assert all(1 <= _gap(s["date"], d) <= 3 for d, s in _links(plan_id, "breakfast").items())


# ---------- 5. the repair's three-day limit ----------

def _chain(days_apart: int):
    tools.add_member("Emily")
    tools.set_member_age_group("Emily", "adult")
    tools.add_member("Vineeth")
    tools.set_member_age_group("Vineeth", "adult")
    tools.add_recipe("Chili", ingredients=[{"item": "beans", "qty": "1 can"}], prep_time_minutes=10, cook_time_minutes=30)
    week = _monday()
    cook = week
    later = (datetime.date.fromisoformat(week) + datetime.timedelta(days=days_apart)).isoformat()
    plan_id = tools.create_weekly_plan(week)["weekly_plan_id"]
    cook_id = tools.plan_meal(cook, "Chili", slot="dinner", weekly_plan_id=plan_id)["entry_id"]
    later_id = tools.plan_meal(later, "Chili", slot="dinner", weekly_plan_id=plan_id,
                               derived_from={"links_to": f"{cook}:dinner"})["entry_id"]
    return plan_id, cook_id, later_id, later


def test_a_chain_three_days_apart_is_confirmed():
    plan_id, cook_id, later_id, later = _chain(3)
    out = tools.repair_leftover_chains(plan_id)
    assert out["frozen"] == [] and len(out["confirmed"]) == 1
    assert later_id in tools.plan_leftover_chains(plan_id)["leftovers"]


def test_a_chain_more_than_three_days_apart_becomes_a_freezer_portion():
    """Not reopened as a question: nothing about the plan is wrong except
    how long the food sits, and Pomona does that planning itself."""
    plan_id, cook_id, later_id, later = _chain(4)

    out = tools.repair_leftover_chains(plan_id)

    assert out["repaired"] == [] and [f["date"] for f in out["frozen"]] == [later]
    chains = tools.plan_leftover_chains(plan_id)
    assert chains["leftovers"] == {} and chains["sources"] == {}
    assert chains["freezer"] == {cook_id: 2}, "the cook makes the two portions extra"
    night = next(r for r in _rows(plan_id, "dinner") if r["date"] == later)
    assert night["slot_state"] == "planned"
    assert night["meal"] == leftovers.freezer_night_name("Chili", _monday())
    derived = json.loads(night["derived_from_json"])
    assert derived[leftovers.FROM_FREEZER_KEY]["dish"] == "Chili"
    assert "links_to" not in derived
    # The cook's batch counts the frozen portions (every batch reader does).
    assert leftovers.batch_for_entry(cook_id, chains)["servings"] == 4


# ---------- 6. prep days at approval: still batched, never chained twice ----------

def test_approval_with_prep_days_batches_the_rest_and_chains_nothing_twice(four_adults, stub_model):
    tools.set_prep_days(["sunday"])
    week = _monday()
    dates = tools._week_dates(week)
    days = [_slot(dates[0], "lunch", "Lentil Soup", dates=dates[:4]),     # chained at generation
            _slot(dates[4], "lunch", "Wrap", dates=dates[4:])]
    # Oats sent one row per morning (not folded): the prep-day rule's to batch.
    days += [_slot(d, "breakfast", "Oats") for d in dates]
    days += [_slot(d, "dinner", f"Dinner {i}") for i, d in enumerate(dates)]
    stub_model(days)
    plan_id = agent.generate_weekly_plan(week)["weekly_plan_id"]
    before = tools.plan_leftover_chains(plan_id)

    tools.approve_weekly_plan(plan_id, approved_by="Emily")

    chains = tools.plan_leftover_chains(plan_id)
    # Nothing the generation chained was re-chained or dropped.
    for entry_id, v in before["leftovers"].items():
        assert chains["leftovers"][entry_id]["source"]["entry_id"] == v["source"]["entry_id"]
    # Every covered night names one cook, which lists it; no cook is a reheat.
    listed = [t["entry_id"] for s in chains["sources"].values() for t in s["targets"]]
    assert len(listed) == len(set(listed)) == len(chains["leftovers"])
    assert not (set(chains["sources"]) & set(chains["leftovers"]))
    # The prep-day rule batched the oats, in runs no longer than three days.
    oats = {v["date"]: v["source"]["date"] for v in chains["leftovers"].values() if v["slot"] == "breakfast"}
    assert oats == {dates[1]: dates[0], dates[2]: dates[0], dates[3]: dates[0],
                    dates[5]: dates[4], dates[6]: dates[4]}


def test_the_prep_day_rule_splits_a_long_run_at_three_days():
    item = {"first": {"entry_id": 1, "date": "2026-10-05"},
            "later": [{"entry_id": n, "date": f"2026-10-{5 + n - 1:02d}"} for n in range(2, 8)]}
    batches = cook_ahead._within_three_days(item)
    assert [(c["date"], [d["date"] for d in later]) for c, later in batches] == [
        ("2026-10-05", ["2026-10-06", "2026-10-07", "2026-10-08"]),
        ("2026-10-09", ["2026-10-10", "2026-10-11"]),
    ]


# ---------- the draft's one line ----------

@pytest.mark.parametrize("cooks,expected", [
    ({"lunch": [2, 2]}, "Two lunches, each cooked double."),
    ({"lunch": [2]}, "One lunch, cooked double."),
    ({"lunch": [2], "dinner": [2, 2]}, "One lunch and two dinners, each cooked double."),
    ({"dinner": [2, 3]}, "Two dinners, each cooked once for several meals."),
    ({}, ""),
])
def test_the_batch_line(cooks, expected):
    entries, next_id = [], 1
    for slot, sizes in cooks.items():
        for size in sizes:
            cook_id = next_id
            entries.append({"id": cook_id, "date": "2026-10-05", "slot": slot, "meal": "X",
                            "slot_state": "planned", "derived_from": "{}"})
            next_id += 1
            for _ in range(size - 1):
                entries.append({"id": next_id, "date": "2026-10-06", "slot": slot, "meal": "X",
                                "slot_state": "planned",
                                "derived_from": json.dumps({"links_to": f"entry_id:{cook_id}",
                                                            leftovers.BATCH_KEY: True})})
                next_id += 1
    assert draft_opener.batch_line(entries) == expected


def test_a_chain_the_model_wrote_is_not_claimed_by_the_line():
    entries = [
        {"id": 1, "date": "2026-10-05", "slot": "dinner", "meal": "Chili", "slot_state": "planned", "derived_from": "{}"},
        {"id": 2, "date": "2026-10-06", "slot": "dinner", "meal": "Chili", "slot_state": "planned",
         "derived_from": json.dumps({"links_to": "2026-10-05:dinner"})},
    ]
    assert draft_opener.batch_line(entries) == ""
