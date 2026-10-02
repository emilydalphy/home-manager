"""
Snacks reach the grocery list.

Loop Board "Snacks never reach the grocery list — the plan has two a day,
the list buys nothing for them" (QA walk as a new household, 2026-10-02).
The menu pass marked the week's snacks is_new_recipe=false — as if
"Apple Slices with Almond Butter" were not a recipe — and the generation
save loop (agent._generate_weekly_plan's _ensure_recipe_saved) trusted the
flag. With no saved recipe by that name, plan_meal wrote each snack
FREEFORM, and a freeform entry is never written up and never shopped for:
the walk's server log shows 4 recipes written for a 12-slot week and its
list had no apples, almond butter, rice cakes or turkey.

Both model calls are stubbed (the week generator and the recipe writer);
what is under test is everything around them.
"""
import datetime

import pytest

from app import agent, tools
from app.db import get_conn
from app.tools import meal_variety

APPLE = "Apple Slices with Almond Butter"
RICE_CAKES = "Rice Cakes with Turkey Slices"

DETAILS = {
    APPLE: [{"item": "apples", "qty": "2"}, {"item": "almond butter", "qty": "4 tbsp"}],
    RICE_CAKES: [{"item": "rice cakes", "qty": "4"}, {"item": "sliced turkey", "qty": "4 oz"}],
    "Cheese and Crackers": [{"item": "crackers", "qty": "1 box"}, {"item": "cheddar", "qty": "4 oz"}],
}


def _week_start() -> str:
    today = datetime.date.today()
    monday = today - datetime.timedelta(days=today.weekday())
    return (monday + datetime.timedelta(days=7)).isoformat()


@pytest.fixture
def chili():
    tools.add_recipe(
        "Chili", ingredients=[{"item": "black beans", "qty": "1 can"}],
        food_groups=["protein", "vegetable", "carb"], default_servings=4,
    )


@pytest.fixture
def stub_models(monkeypatch):
    written = []

    def _details(spec):
        written.append(spec["name"])
        return {"ingredients": DETAILS.get(spec["name"], [{"item": "carrots", "qty": "1 lb"}]),
                "instructions": ["Put it on a plate."]}

    monkeypatch.setattr(agent, "generate_recipe_details_llm", _details)
    monkeypatch.setattr(agent, "generate_sides_llm", lambda context: [])

    def _week(days):
        monkeypatch.setattr(agent, "generate_weekly_plan_llm", lambda context: days)

    return _week, written


def _days(week, snacks_for, *, flag=False, dinner_overrides=None):
    """Every day: Chili for each real meal, plus `snacks_for(date)`, each
    snack marked is_new_recipe=`flag` (False is what the walk's model sent)."""
    dinner_overrides = dinner_overrides or {}
    out = []
    for day in tools._week_dates(week):
        for slot in tools.WEEK_SLOTS:
            override = dinner_overrides.get((day, slot))
            out.append({"date": day, "slot": slot, "meal_name": "Chili", "is_new_recipe": False,
                        **(override or {})})
        for name in snacks_for(day):
            out.append({"date": day, "slot": "snack", "meal_name": name, "is_new_recipe": flag,
                        "food_groups": ["protein", "carb"]})
    return out


def _snack_rows(plan_id):
    conn = get_conn()
    rows = conn.execute(
        "SELECT id, date, recipe_id, freeform_meal FROM meal_plan_entries "
        "WHERE weekly_plan_id = ? AND slot = 'snack' ORDER BY date, id",
        (plan_id,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def _needed():
    return [i for i in tools.list_grocery_list() if i.get("status", "needed") == "needed"]


def _names():
    return [i["item"].lower() for i in _needed()]


def test_snacks_the_model_called_not_new_are_still_saved_as_recipes(chili, stub_models):
    stub_week, _ = stub_models
    week = _week_start()
    stub_week(_days(week, lambda d: [APPLE, RICE_CAKES]))

    plan = agent.generate_weekly_plan(week)

    rows = _snack_rows(plan["weekly_plan_id"])
    assert len(rows) == 14
    assert all(r["recipe_id"] for r in rows), (
        "a snack whose name matches no saved recipe must not land freeform: "
        f"{[r for r in rows if not r['recipe_id']][:2]}"
    )
    assert all(r["freeform_meal"] is None for r in rows)
    # One recipe per snack, not one per day.
    assert len({r["recipe_id"] for r in rows}) == 2


def test_approving_the_week_puts_every_snacks_ingredients_on_the_list(chili, stub_models):
    stub_week, written = stub_models
    week = _week_start()
    stub_week(_days(week, lambda d: [APPLE, RICE_CAKES]))
    plan = agent.generate_weekly_plan(week)
    assert _needed() == [], "a draft buys nothing"

    tools.approve_weekly_plan(plan["weekly_plan_id"])

    names = _names()
    for item in ("apples", "almond butter", "rice cakes", "sliced turkey"):
        assert item in names, f"{item!r} missing from the list: {names}"
    # The recipe pass wrote each snack up once, not once per day.
    assert sorted(n for n in written if n in DETAILS) == sorted([APPLE, RICE_CAKES])


def test_a_snack_on_every_day_is_one_line_not_seven(chili, stub_models):
    stub_week, _ = stub_models
    week = _week_start()
    stub_week(_days(week, lambda d: [APPLE, RICE_CAKES]))
    plan = agent.generate_weekly_plan(week)

    tools.approve_weekly_plan(plan["weekly_plan_id"])

    names = _names()
    assert names.count("apples") == 1, names
    assert names.count("almond butter") == 1, names


def test_swapping_a_snack_takes_its_items_back_off(chili, stub_models):
    stub_week, _ = stub_models
    tools.add_recipe("Cheese and Crackers", ingredients=DETAILS["Cheese and Crackers"],
                     food_groups=["protein", "carb"], default_servings=4)
    week = _week_start()
    monday = tools._week_dates(week)[0]
    # Rice cakes on Monday only, so swapping Monday's takes the whole of it.
    stub_week(_days(week, lambda d: [APPLE, RICE_CAKES] if d == monday else [APPLE]))
    plan = agent.generate_weekly_plan(week)
    tools.approve_weekly_plan(plan["weekly_plan_id"])
    assert "rice cakes" in _names()

    tools.swap_meal_in_plan(plan["weekly_plan_id"], monday, "Cheese and Crackers",
                            slot="snack", old_meal=RICE_CAKES)

    names = _names()
    assert "rice cakes" not in names and "sliced turkey" not in names, names
    assert "crackers" in names
    assert "apples" in names, "the day's other snack keeps its shopping"


def test_a_non_snack_slot_still_trusts_the_flag(chili, stub_models):
    """Scope pin: a leftovers night is sent as 'a leftovers entry naming
    what it's eating' with is_new_recipe false, and must buy nothing of its
    own — so only SNACKS are saved regardless of the flag."""
    stub_week, _ = stub_models
    week = _week_start()
    tuesday = tools._week_dates(week)[1]
    stub_week(_days(week, lambda d: [APPLE], dinner_overrides={
        (tuesday, "dinner"): {"meal_name": "Leftover Chili"},
    }))
    plan = agent.generate_weekly_plan(week)

    assert not tools.existing_recipe_named("Leftover Chili")
    conn = get_conn()
    row = conn.execute(
        "SELECT recipe_id, freeform_meal FROM meal_plan_entries WHERE weekly_plan_id = ? "
        "AND date = ? AND slot = 'dinner'", (plan["weekly_plan_id"], tuesday),
    ).fetchone()
    conn.close()
    assert row is not None
    assert row["recipe_id"] is None and row["freeform_meal"] == "Leftover Chili"


def test_a_snack_picked_without_a_list_is_saved_pending(chili):
    """enforce_snacks_per_day's own pick: a picker answer with no
    ingredients used to be saved as nothing at all, so the snack it filled
    a short day with landed freeform."""
    week = _week_start()
    plan = tools.create_weekly_plan(week)
    plan_id = plan["weekly_plan_id"]
    monday = tools._week_dates(week)[0]
    tools.plan_meal(meal_date=monday, meal="Chili", slot="dinner", weekly_plan_id=plan_id)

    out = meal_variety.enforce_snacks_per_day(
        plan_id, 1, [monday],
        picker=lambda context: {"meal_name": "Cheese and Crackers", "reason": "", "food_groups": ["protein", "carb"]},
    )

    assert out["added"] == [{"date": monday, "meal": "Cheese and Crackers"}]
    rows = _snack_rows(plan_id)
    assert len(rows) == 1 and rows[0]["recipe_id"], rows
    pending = {r["name"] for r in tools.pending_recipes_for_plan(plan_id)}
    assert "Cheese and Crackers" in pending
