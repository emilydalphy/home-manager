"""
"Four dinners a week" is enforced after generation, not merely asked for.

Emily, 2026-09-13: her household has dinners_per_week = 4 and the draft came
back with five distinct dinner dishes. The count reached the model as a
prompt bullet and nothing read the result back against it. See
app/tools/meal_variety.py.

Every test here stubs the model to return a deliberately over-varied week
and checks what the plan on disk looks like afterwards — the same shape
as test_plan_quality's integration tests.
"""
from __future__ import annotations

import datetime
import json

import pytest

from app import agent, tools
from app.db import get_conn
from app.tools import leftovers, meal_variety


def _monday(offset_weeks: int = 1) -> str:
    today = datetime.date.today()
    monday = today - datetime.timedelta(days=today.weekday())
    return (monday + datetime.timedelta(days=7 * offset_weeks)).isoformat()


DINNERS = ["Chili", "Salmon", "Kofte", "Halloumi Salad", "Burgers", "Shrimp", "Tacos"]


@pytest.fixture
def recipes():
    for name in DINNERS:
        tools.add_recipe(name, ingredients=[{"item": f"{name} stuff", "qty": "1"}], main_protein=name)
    tools.add_recipe("Toast", ingredients=[{"item": "bread", "qty": "1 loaf"}])


@pytest.fixture
def stub_model(monkeypatch):
    def _stub(days):
        monkeypatch.setattr(agent, "generate_weekly_plan_llm", lambda ctx: days)
    return _stub


def _week(week: str, dinners: list[str | None], dinner_extra: dict | None = None) -> list[dict]:
    """A full 21-slot week whose dinners are `dinners` in date order; a
    None dinner is left out of the model's answer entirely."""
    days = []
    extra = dinner_extra or {}
    for date, dinner in zip(tools._week_dates(week), dinners):
        for slot in ("breakfast", "lunch"):
            days.append({"date": date, "slot": slot, "meal_name": "Toast", "is_new_recipe": False,
                         "reasoning": "quick"})
        days.append({"date": date, "slot": "snack", "meal_name": "Apple", "is_new_recipe": False})
        if dinner is not None:
            days.append({"date": date, "slot": "dinner", "meal_name": dinner, "is_new_recipe": False,
                         "reasoning": "fits the week", **extra.get(date, {})})
    return days


def _dinners(plan_id: int) -> list[tuple[str, str, str, str]]:
    conn = get_conn()
    rows = conn.execute(
        """
        SELECT mpe.date, mpe.slot_state, COALESCE(r.name, mpe.freeform_meal) AS meal,
               mpe.reasoning, mpe.derived_from_json
        FROM meal_plan_entries mpe LEFT JOIN recipes r ON r.id = mpe.recipe_id
        WHERE mpe.weekly_plan_id = ? AND mpe.slot = 'dinner' ORDER BY mpe.date
        """,
        (plan_id,),
    ).fetchall()
    conn.close()
    return [(r["date"], r["slot_state"], _dish(r["meal"], r["derived_from_json"]), r["reasoning"]) for r in rows]


def _dish(meal: str | None, derived_json: str | None) -> str | None:
    """The dish a night is, as the Plan tab files it: a night eating a
    portion from the freezer is that dish (2026-09-23)."""
    frozen = (json.loads(derived_json or "{}") or {}).get(leftovers.FROM_FREEZER_KEY)
    return frozen["dish"] if isinstance(frozen, dict) else meal


def _distinct(plan_id: int) -> set[str]:
    return {m for _, state, m, _ in _dinners(plan_id) if state == "planned"}


def _links(plan_id: int) -> dict[str, str]:
    """{leftovers night: the night it eats from} for every confirmed chain."""
    chains = tools.plan_leftover_chains(plan_id)
    return {v["date"]: v["source"]["date"] for v in chains["leftovers"].values()}


def _days_apart(a: str, b: str) -> int:
    return (datetime.date.fromisoformat(b) - datetime.date.fromisoformat(a)).days


# ---------- the reported bug ----------

def test_five_dishes_against_a_preference_of_four_become_four(recipes, stub_model):
    """Emily's screenshot: 4 asked for, 5 delivered. Now 4, every night fed."""
    tools.set_household_meal_preferences(dinners_per_week=4)
    week = _monday()
    stub_model(_week(week, ["Chili", "Salmon", "Kofte", "Halloumi Salad", "Burgers", "Chili", "Kofte"]))

    plan = agent.generate_weekly_plan(week)

    dinners = _dinners(plan["weekly_plan_id"])
    assert len(dinners) == 7 and all(state == "planned" for _, state, _, _ in dinners)
    assert len(_distinct(plan["weekly_plan_id"])) == 4
    # The dish that first appeared latest is the one that went. Its night
    # is leftovers of the nearest cook (Emily, 2026-09-23: fewer dishes
    # than nights means batch cooking) — Thursday's Halloumi Salad, made
    # double — not a second cooking of a kept dish, which is what the
    # "On again — you asked for four dinners a week" line used to sit on.
    assert "Burgers" not in _distinct(plan["weekly_plan_id"])
    friday = dinners[4]
    assert friday[2] == "Halloumi Salad"
    assert _links(plan["weekly_plan_id"])[friday[0]] == dinners[3][0]
    assert tools.audit_plan_slots(plan["weekly_plan_id"])["complete"] is True


def test_a_week_within_the_count_is_left_exactly_as_generated(recipes, stub_model):
    tools.set_household_meal_preferences(dinners_per_week=4)
    week = _monday()
    generated = ["Chili", "Salmon", "Kofte", "Halloumi Salad", "Chili", "Salmon", "Kofte"]
    stub_model(_week(week, generated))

    plan = agent.generate_weekly_plan(week)

    assert [m for _, _, m, _ in _dinners(plan["weekly_plan_id"])] == generated


def test_the_default_of_seven_never_touches_a_week(recipes, stub_model):
    """dinners_per_week defaults to 7 (never set) — seven different dinners
    is exactly what that household asked for."""
    week = _monday()
    stub_model(_week(week, DINNERS))

    plan = agent.generate_weekly_plan(week)

    assert len(_distinct(plan["weekly_plan_id"])) == 7


def test_two_dishes_over_seven_nights_are_two_batches_eaten_within_three_days(recipes, stub_model):
    """
    Two dishes asked for, seven delivered. This used to alternate them as
    seven separate cooks (Chili, Salmon, Chili, …) — each night bought and
    cooked on its own. Since Emily's 2026-09-23 decision ("If I want 2
    types of lunches, but need 4 lunches, you should assume Im making
    double of each"), two dishes over seven nights is two cooks, the rest
    leftovers, none more than three days after its cook. Two cooks can't
    reach seven nights while alternating, so the cooks are re-laid:
    Chili Monday for Mon–Thu, Salmon Friday for Fri–Sun.
    """
    tools.set_household_meal_preferences(dinners_per_week=2)
    week = _monday()
    stub_model(_week(week, DINNERS))

    plan = agent.generate_weekly_plan(week)

    dinners = _dinners(plan["weekly_plan_id"])
    meals = [m for _, _, m, _ in dinners]
    assert meals == ["Chili"] * 4 + ["Salmon"] * 3
    links = _links(plan["weekly_plan_id"])
    cooks = [d for d, *_ in dinners if d not in links]
    assert cooks == [dinners[0][0], dinners[4][0]], "two cooks, one per dish"
    assert all(1 <= _days_apart(src, night) <= 3 for night, src in links.items())


# ---------- what is never dropped ----------

def test_a_dish_the_household_asked_for_in_their_own_words_survives(recipes, stub_model):
    tools.set_household_meal_preferences(dinners_per_week=4)
    week = _monday()
    dates = tools._week_dates(week)
    # Burgers is the latest-starting dish — the first to go — unless it is
    # the household's own ask.
    stub_model(_week(
        week, ["Chili", "Salmon", "Kofte", "Halloumi Salad", "Burgers", "Chili", "Kofte"],
        dinner_extra={dates[4]: {"derived_from": {"freeform": "burgers on Friday"}}},
    ))

    plan = agent.generate_weekly_plan(week)

    kept = _distinct(plan["weekly_plan_id"])
    assert len(kept) == 4
    assert "Burgers" in kept
    assert "Halloumi Salad" not in kept


def test_an_out_night_is_not_a_dish_and_is_not_a_night_to_fill(recipes, stub_model):
    tools.set_household_meal_preferences(dinners_per_week=4)
    week = _monday()
    dates = tools._week_dates(week)
    tools.save_week_intake(week, night_tags={dates[6]: ["out"]})
    stub_model(_week(week, ["Chili", "Salmon", "Kofte", "Halloumi Salad", "Burgers", "Chili", None]))

    plan = agent.generate_weekly_plan(week)

    dinners = _dinners(plan["weekly_plan_id"])
    assert dinners[6][1] == "planned_empty"
    assert len(_distinct(plan["weekly_plan_id"])) == 4


def test_a_chain_is_dropped_last_and_whole(recipes, stub_model):
    """Kofte is cooked Tuesday and reheated Wednesday. It starts EARLIER
    than Burgers, so ordinarily Burgers goes first — but with only one
    surplus dish and Burgers protected, the chain has to go, and both its
    nights go together (never a reheat of nothing)."""
    tools.set_household_meal_preferences(dinners_per_week=4)
    week = _monday()
    d = tools._week_dates(week)
    stub_model(_week(
        week, ["Chili", "Kofte", "Kofte", "Salmon", "Burgers", "Halloumi Salad", "Chili"],
        dinner_extra={
            d[1]: {"derived_from": {"make_double_for": [f"{d[2]}:dinner"]}},
            d[2]: {"derived_from": {"links_to": f"{d[1]}:dinner"}},
            d[4]: {"derived_from": {"freeform": "burgers"}},
            d[5]: {"derived_from": {"freeform": "halloumi salad"}},
        },
    ))

    plan = agent.generate_weekly_plan(week)

    # Reheat counted under Kofte: 5 dishes, not 6, so exactly one goes.
    kept = _distinct(plan["weekly_plan_id"])
    assert len(kept) == 4
    assert "Salmon" not in kept  # the latest-starting unchained candidate
    assert "Kofte" in kept
    assert tools.plan_leftover_chains(plan["weekly_plan_id"])["leftovers"]


def test_a_chained_dish_goes_whole_when_nothing_else_can(recipes, stub_model):
    tools.set_household_meal_preferences(dinners_per_week=3)
    week = _monday()
    d = tools._week_dates(week)
    stub_model(_week(
        week, ["Chili", "Kofte", "Kofte", "Salmon", "Salmon", "Salmon", "Burgers"],
        dinner_extra={
            d[1]: {"derived_from": {"make_double_for": [f"{d[2]}:dinner"]}},
            d[2]: {"derived_from": {"links_to": f"{d[1]}:dinner"}},
            d[0]: {"derived_from": {"freeform": "chili"}},
            d[3]: {"derived_from": {"freeform": "salmon"}},
            d[6]: {"derived_from": {"freeform": "burgers"}},
        },
    ))

    plan = agent.generate_weekly_plan(week)

    dinners = _dinners(plan["weekly_plan_id"])
    assert all(state == "planned" for _, state, _, _ in dinners)
    assert "Kofte" not in _distinct(plan["weekly_plan_id"])
    # Kofte's own chain went with it. The chains standing now are the
    # batch cooking the three kept dishes do (2026-09-23): this used to
    # assert there were no chains at all, when the freed nights were
    # second cookings.
    chains = tools.plan_leftover_chains(plan["weekly_plan_id"])
    assert all(v["source"]["meal"] != "Kofte" for v in chains["leftovers"].values())
    assert all(1 <= _days_apart(src, night) <= 3 for night, src in _links(plan["weekly_plan_id"]).items())


def test_when_every_dish_is_protected_nothing_is_dropped(recipes, stub_model, caplog):
    tools.set_household_meal_preferences(dinners_per_week=2)
    week = _monday()
    d = tools._week_dates(week)
    stub_model(_week(
        week, ["Chili", "Salmon", "Kofte", "Chili", "Salmon", "Kofte", "Chili"],
        dinner_extra={d[i]: {"derived_from": {"freeform": "asked"}} for i in range(7)},
    ))

    with caplog.at_level("WARNING", logger="home_manager"):
        plan = agent.generate_weekly_plan(week)

    assert len(_distinct(plan["weekly_plan_id"])) == 3
    assert "protected" in caplog.text


# ---------- when it stands down ----------

def test_a_week_that_names_its_own_count_is_not_overruled(recipes, stub_model):
    tools.set_household_meal_preferences(dinners_per_week=4)
    week = _monday()
    stub_model(_week(week, DINNERS))

    plan = agent.generate_weekly_plan(week, constraints_notes="seven different dinners this week please")

    assert len(_distinct(plan["weekly_plan_id"])) == 7


def test_a_part_week_enforces_the_prorated_count(recipes, stub_model):
    """4 a week over 5 days is 3 (see _prorate_meal_count)."""
    tools.set_household_meal_preferences(dinners_per_week=4)
    week = _monday()
    d = tools._week_dates(week)
    days = [x for x in _week(week, DINNERS) if x["date"] in d[2:]]
    stub_model(days)

    plan = agent.generate_weekly_plan(week, day_count=5, skip_days=2)

    assert len(_distinct(plan["weekly_plan_id"])) == 3


@pytest.mark.parametrize("text,expected", [
    ("5 different dinners this week", True),
    ("five dinners", True),
    ("three new recipes please", True),
    ("keep it under 30 min on weeknights", False),
    ("keep it under 30 minutes for dinner", False),
    ("we have 4 kids eating dinner most nights", False),
    ("we are 5 for dinner most nights this week", False),
    ("4 dinners", True),
    ("seven different dinners this week please", True),
    ("out Thu/Fri, one vegetarian night", False),
    ("", False),
    (None, False),
])
def test_asks_for_a_count(text, expected):
    assert meal_variety.asks_for_a_count(text) is expected


def test_enforcement_never_raises_into_generation(monkeypatch):
    monkeypatch.setattr(meal_variety._leftovers, "plan_leftover_chains", lambda *_: 1 / 0)
    result = meal_variety.enforce_distinct_count(999999, 4)
    assert result["replaced"] == []
