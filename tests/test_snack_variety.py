"""
A week's snacks have some variety (Loop Board "my week's snacks have some
variety: not the same orange snack 7 days running", 2026-10-19).

Tester Home, plan 72 (2 adults, 2 snacks a day): all fourteen snack slots
were "Sliced Orange with Pumpkin Seeds" or "Edamame with Sea Salt", seven
days each, and the list bought 14 oranges. The model sent it, and
meal_variety.enforce_snack_dishes then folded any variety it did send down
to snack_dishes_per_week's column DEFAULT of 2 — a number nobody chose.

Now: an unanswered count reads as enough dishes that no snack is on more
than three days (snack_variety.default_snack_dishes), and
snack_variety.enforce_snack_variety re-picks each over-cap snack day from
the week's own snacks first, then a code-side pool (no model call) held to
the allergen/taste gate and leaning toward what the week already buys.
A count the household set, words for the week like "same snack every day
is fine", or a snack they asked for by name stand it down.

Every test stubs the model at agent.generate_weekly_plan_llm.
"""
from __future__ import annotations

import datetime

import pytest

from app import agent, tools
from app.tools import snack_variety
from conftest import household_today

ORANGE = "Sliced Orange with Pumpkin Seeds"
EDAMAME = "Edamame with Sea Salt"


def _monday() -> str:
    today = household_today()
    return (today - datetime.timedelta(days=today.weekday()) + datetime.timedelta(days=7)).isoformat()


def _slot(date, slot, name, ingredients=None, **extra):
    d = {"date": date, "slot": slot, "meal_name": name, "is_new_recipe": True,
         "ingredients": ingredients or [{"item": f"{name} stuff", "qty": "1"}],
         "reasoning": "", "food_groups": ["protein", "vegetable"]}
    d.update(extra)
    return d


def _tester_home_week(week, extra=None):
    """Plan 72's shape: the same two snacks on every one of seven days."""
    out = []
    for date in tools._week_dates(week):
        out.append(_slot(date, "breakfast", "Overnight oats"))
        out.append(_slot(date, "lunch", "Chickpea salad"))
        out.append(_slot(date, "dinner", "Lemon chicken", [{"item": "chicken thighs", "qty": "1 lb"},
                                                          {"item": "carrots", "qty": "3"}]))
        # Written for the two of them: one orange each, as plan 72's was
        # (14 oranges = 1 orange x 2 people x 7 days).
        out.append(_slot(date, "snack", ORANGE, [{"item": "oranges", "qty": "2"},
                                                 {"item": "pumpkin seeds", "qty": "2 tbsp"}],
                         default_servings=2, **((extra or {}).get(ORANGE) or {})))
        out.append(_slot(date, "snack", EDAMAME, [{"item": "frozen edamame", "qty": "1 cup"}]))
    return out


@pytest.fixture
def two_adults():
    tools.add_member("Emily")
    tools.add_member("Sam")
    tools.set_household_meal_preferences(snacks_per_day=2, mark_complete=False)


@pytest.fixture
def stub_model(monkeypatch):
    def _stub(days):
        monkeypatch.setattr(agent, "generate_weekly_plan_llm", lambda ctx: days)
    return _stub


def _snack_days(plan_id) -> dict[str, set[str]]:
    out: dict[str, set[str]] = {}
    for m in tools.get_weekly_plan(plan_id)["meals"]:
        if m["slot"] == "snack" and m["slot_state"] == "planned":
            out.setdefault(m["meal"], set()).add(m["date"])
    return out


def _per_day(plan_id) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for m in tools.get_weekly_plan(plan_id)["meals"]:
        if m["slot"] == "snack" and m["slot_state"] == "planned":
            out.setdefault(m["date"], []).append(m["meal"])
    return out


def test_two_snacks_a_day_for_two_adults_is_at_least_four_snacks_none_on_more_than_three_days(
        two_adults, stub_model):
    """The card's test. CATCH: on main the week keeps 2 snacks, 7 days each."""
    week = _monday()
    stub_model(_tester_home_week(week))
    plan_id = agent.generate_weekly_plan(week)["weekly_plan_id"]

    days = _snack_days(plan_id)
    assert len(days) >= 4, days
    assert max(len(d) for d in days.values()) <= 3, days
    # Every day still has its two snacks, two different ones.
    per_day = _per_day(plan_id)
    assert len(per_day) == 7
    assert all(len(s) == 2 and len(set(s)) == 2 for s in per_day.values()), per_day
    # The model's own two stay on the week, just not every day.
    assert ORANGE in days and EDAMAME in days


def test_an_unanswered_count_reads_as_enough_for_three_days_each():
    assert tools.get_household_memory()["snack_dishes_per_week"] == 5
    assert snack_variety.default_snack_dishes(1) == 3
    assert snack_variety.default_snack_dishes(3) == 7


def test_a_count_they_set_is_theirs_even_at_two(two_adults, stub_model):
    """The stepper at 2 is an answer: two dishes, every day, as they said."""
    week = _monday()
    tools.edit_preference("snack_dishes_per_week", 2)
    assert tools.get_household_memory()["snack_dishes_per_week_set"] is True
    stub_model(_tester_home_week(week))
    plan_id = agent.generate_weekly_plan(week)["weekly_plan_id"]
    assert set(_snack_days(plan_id)) == {ORANGE, EDAMAME}


def test_same_snack_every_day_is_fine_stands_it_down(two_adults, stub_model):
    week = _monday()
    tools.save_week_intake(week, freeform="same snack every day is fine")
    stub_model(_tester_home_week(week))
    plan_id = agent.generate_weekly_plan(week)["weekly_plan_id"]
    assert set(_snack_days(plan_id)) == {ORANGE, EDAMAME}


def test_not_the_same_snack_is_not_an_ask_for_it():
    assert snack_variety.asks_for_same_snacks("same snack every day is fine")
    assert snack_variety.asks_for_same_snacks("Happy to have the same snacks all week")
    assert not snack_variety.asks_for_same_snacks("not the same snack every day please")
    assert not snack_variety.asks_for_same_snacks("snacks every day")


def test_a_snack_they_asked_for_keeps_its_days(two_adults, stub_model):
    week = _monday()
    tools.save_week_intake(week, freeform="oranges for snack every day")
    stub_model(_tester_home_week(week, extra={ORANGE: {"derived_from": {"freeform": "oranges for snack"}}}))
    plan_id = agent.generate_weekly_plan(week)["weekly_plan_id"]
    days = _snack_days(plan_id)
    assert len(days[ORANGE]) == 7
    assert len(days[EDAMAME]) <= 3


def test_variety_respects_a_hard_allergy(two_adults, stub_model):
    """A dairy allergy: no pool snack with cheese, yogurt or tzatziki lands."""
    tools.set_member_dietary_restrictions("Sam", ["dairy allergy"])
    week = _monday()
    stub_model(_tester_home_week(week))
    plan_id = agent.generate_weekly_plan(week)["weekly_plan_id"]
    names = set(_snack_days(plan_id))
    for pool in snack_variety.SNACK_POOL:
        if any(w in " ".join(l[0] for l in pool["lines"]) + " " + " ".join(pool.get("contains") or ())
               for w in ("cheese", "yogurt", "cheddar", "tzatziki")):
            assert pool["name"] not in names, names


def test_prefers_a_snack_that_reuses_what_the_week_buys(two_adults, stub_model):
    """The week's dinner already buys carrots: the first pool pick is the
    carrot one, not a snack that adds two new lines to the list."""
    week = _monday()
    stub_model(_tester_home_week(week))
    plan_id = agent.generate_weekly_plan(week)["weekly_plan_id"]
    assert "Carrot Sticks with Hummus" in _snack_days(plan_id)


def test_the_list_reads_per_person_per_day(two_adults, stub_model):
    """Three orange days for two adults is six oranges, not fourteen; a pool
    snack is written per person too (three apple days, two adults: six)."""
    week = _monday()
    stub_model(_tester_home_week(week))
    plan_id = agent.generate_weekly_plan(week)["weekly_plan_id"]
    days = _snack_days(plan_id)
    assert len(days[ORANGE]) == 3
    assert len(days["Apple Slices with Cheddar"]) == 3, days
    tools.approve_weekly_plan(plan_id)
    lines = {r["item"].lower(): r["quantity"] for r in tools.list_grocery_list()}
    assert str(lines.get("oranges")).split()[0] == "6", lines
    assert str(lines.get("apples")).split()[0] == "6", lines


def test_a_household_that_already_set_the_stepper_keeps_its_number():
    """The startup backfill reads edit_preference's preference_events row."""
    from app import db
    from app.db import get_conn
    tools.set_household_meal_preferences(snacks_per_day=2, mark_complete=False)
    tools.edit_preference("snack_dishes_per_week", 2)
    conn = get_conn()
    conn.execute("UPDATE meal_preferences SET snack_dishes_per_week_set = 0")
    conn.commit()
    assert tools.get_household_memory()["snack_dishes_per_week"] == 5
    db._backfill_snack_dishes_set(conn)
    conn.commit()
    conn.close()
    assert tools.get_household_memory()["snack_dishes_per_week"] == 2


def test_a_pool_snack_never_echoes_that_days_meal(two_adults, stub_model):
    """Carrot soup every night: no Carrot Sticks snack (plan_quality's
    same-day echo would copy an over-cap snack back over it)."""
    week = _monday()
    days = [dict(d, meal_name="Carrot ginger soup") if d["slot"] == "dinner" else d
            for d in _tester_home_week(week)]
    stub_model(days)
    plan_id = agent.generate_weekly_plan(week)["weekly_plan_id"]
    snacks = _snack_days(plan_id)
    assert "Carrot Sticks with Hummus" not in snacks
    assert max(len(d) for d in snacks.values()) <= 3, snacks


# ---------- the pool protects itself (review, 2026-10-19) ----------

def _pool_with(*words):
    return {p["name"] for p in snack_variety.SNACK_POOL
            if any(w in " ".join([l[0] for l in p["lines"]] + list(p.get("contains") or ())).lower()
                   for w in words)}


@pytest.mark.parametrize("restriction, words", [
    ("milk allergy", ("milk", "cheese", "yogurt", "tzatziki")),
    ("lactose intolerant", ("lactose", "cheese", "yogurt", "tzatziki")),
    ("corn", ("corn",)),
    ("citrus allergy", ("citrus",)),
])
def test_a_pool_snack_never_carries_what_someone_cant_have(two_adults, stub_model, restriction, words):
    """CATCH: a "milk allergy" house was given Greek Yogurt and Apple Slices
    with Cheddar — the shared matcher knew those only as "dairy"."""
    tools.set_member_dietary_restrictions("Sam", [restriction])
    week = _monday()
    stub_model(_tester_home_week(week))
    plan_id = agent.generate_weekly_plan(week)["weekly_plan_id"]
    names = set(_snack_days(plan_id))
    held = _pool_with(*words)
    assert held and not (names & held), (restriction, names & held)
    assert all(len(s) == 2 for s in _per_day(plan_id).values())


def test_a_saved_recipe_of_the_pool_name_is_checked_on_its_own_list(two_adults, stub_model, monkeypatch):
    """Read off the pass itself: the allergen sweep at the foot of the week
    would also catch it, later and with a model call."""
    picked = []
    real = snack_variety.enforce_snack_variety

    def spy(*a, **k):
        out = real(*a, **k)
        picked.extend(r["with"] for r in out["replaced"])
        return out
    monkeypatch.setattr(agent._snack_variety, "enforce_snack_variety", spy)
    tools.set_member_dietary_restrictions("Sam", ["peanut allergy"])
    tools.add_recipe(name="Carrot Sticks with Hummus", default_servings=2, food_groups=["vegetable"],
                     ingredients=[{"item": "carrots", "qty": "2"}, {"item": "peanut butter", "qty": "2 tbsp"}])
    week = _monday()
    stub_model(_tester_home_week(week))
    plan_id = agent.generate_weekly_plan(week)["weekly_plan_id"]
    assert picked and "Carrot Sticks with Hummus" not in picked
    assert "Carrot Sticks with Hummus" not in _snack_days(plan_id)


def test_a_pool_recipe_from_a_smaller_table_is_written_again_for_this_one(two_adults, stub_model):
    """Saved for one last time ("1 apples"); two adults now: "2"."""
    from app.tools import recipes
    pool = next(p for p in snack_variety.SNACK_POOL if p["name"] == "Apple Slices with Cheddar")
    tools.add_recipe(name=pool["name"], ingredients=snack_variety.pool_ingredients(pool, 1),
                     default_servings=1, food_groups=pool["groups"])
    week = _monday()
    stub_model(_tester_home_week(week))
    plan_id = agent.generate_weekly_plan(week)["weekly_plan_id"]
    assert pool["name"] in _snack_days(plan_id)
    apples = next(i for i in recipes.saved_ingredients(pool["name"]) if i["item"].lower() == "apples")
    assert apples["qty"] == "2"
