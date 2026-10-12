"""
"Tonight needs a dinner" offers dinners — not the week's breakfasts and snacks.

Bug, found 2026-10-09 by walking a brand-new email sign-up through
onboarding and its first week: on the Friday before that week started,
Today's needs-you card read

    Tonight needs a dinner
    Apple slices · 3 min      Pick
    Overnight oats · 5 min    Pick

and the brand-new second adult's Today said the same the moment the invite
link opened it. _suggest_quick_dinners took the household's quickest saved
recipes, and the first week the planner writes saves every dish as a recipe
— the breakfasts and the snacks with them. Those are always the quickest,
so after one generated week they were always the two "quick dinners". The
same list feeds Plan's empty-dinner suggestions (get_week_menu).

A recipe the household has only ever had as a breakfast or a snack is not
offered as a dinner now. One it has had for lunch or dinner even once
still is, and so is one never planned at all (an import, a typed-in
recipe) — the plan is the only evidence of what a dish is for.
"""
import datetime

from app import households, tools
from conftest import household_today


def _d(offset_days: int = 0) -> str:
    return (household_today() + datetime.timedelta(days=offset_days)).isoformat()


def _recipe(name: str, minutes: int):
    tools.add_recipe(
        name,
        ingredients=[{"item": name.lower(), "qty": "1"}],
        prep_time_minutes=minutes,
        cook_time_minutes=0,
    )


def _offered() -> list[str]:
    return [o["meal"] for o in tools._suggest_quick_dinners()]


def test_the_weeks_breakfasts_and_snacks_are_not_offered_as_dinner():
    _recipe("Apple slices", 3)
    _recipe("Overnight oats", 5)
    _recipe("Lemon herb chicken", 40)
    _recipe("Black bean tacos", 30)
    # Where the week put them — days before today, so tonight stays empty.
    tools.plan_meal(_d(-3), "Apple slices", slot="snack")
    tools.plan_meal(_d(-3), "Overnight oats", slot="breakfast")
    tools.plan_meal(_d(-3), "Lemon herb chicken", slot="dinner")
    tools.plan_meal(_d(-2), "Black bean tacos", slot="dinner")

    assert _offered() == ["Black bean tacos", "Lemon herb chicken"]

    card = next(i for i in tools.get_needs_you_items() if i["type"] == "dinner_decision")
    assert card["title"] == "Tonight needs a dinner"
    assert [o["meal"] for o in card["options"]] == ["Black bean tacos", "Lemon herb chicken"]


def test_a_dish_the_household_has_had_for_dinner_is_still_a_dinner():
    # Edge: a soup only ever had at lunch, shakshuka at breakfast once and
    # at lunch once, granola only at breakfast. Only a dish whose every
    # plan row is a breakfast or a snack is left out — a lunch is a meal
    # that can be a dinner.
    _recipe("Lentil soup", 15)
    _recipe("Shakshuka", 20)
    _recipe("Granola", 2)
    tools.plan_meal(_d(-4), "Lentil soup", slot="lunch")
    tools.plan_meal(_d(-5), "Shakshuka", slot="breakfast")
    tools.plan_meal(_d(-2), "Shakshuka", slot="lunch")
    tools.plan_meal(_d(-1), "Granola", slot="breakfast")

    assert _offered() == ["Lentil soup", "Shakshuka"]


def test_a_recipe_never_planned_is_still_offered():
    _recipe("Pasta aglio e olio", 15)

    assert _offered() == ["Pasta aglio e olio"]


def test_another_households_plan_does_not_decide_what_a_dish_is_for():
    # A no-regression guard, green before the fix too: the plan rows read
    # are this household's own, so the same dish name planned next door
    # only as a breakfast changes nothing here.
    other = households.create_household("Next door", "next-door-passphrase-1")
    with tools.use_household(other):
        _recipe("Frittata", 10)
        tools.plan_meal(_d(-1), "Frittata", slot="breakfast")
    _recipe("Frittata", 10)
    tools.plan_meal(_d(-1), "Frittata", slot="dinner")

    assert _offered() == ["Frittata"]


def _week_start() -> str:
    today = household_today()
    return (today - datetime.timedelta(days=today.weekday())).isoformat()


def test_a_draft_breakfast_swapped_out_of_the_week_does_not_come_back():
    # Review, 2026-10-09: swapping a breakfast replaces its plan row, so the
    # only evidence it was a breakfast went with it and it counted as "never
    # planned". A recipe the week's draft wrote needs a lunch or dinner row
    # of its own to be offered; never-planned only exempts imports and
    # typed-in recipes.
    plan_id = tools.create_weekly_plan(_week_start())["weekly_plan_id"]
    tools.add_recipe("Overnight oats", ingredients=[{"item": "oats", "qty": "1 cup"}],
                     prep_time_minutes=5, cook_time_minutes=0, from_draft=True)
    tools.add_recipe("Yogurt bowl", ingredients=[{"item": "yogurt", "qty": "1 cup"}],
                     prep_time_minutes=4, cook_time_minutes=0, from_draft=True)
    tools.add_recipe("Tacos", ingredients=[{"item": "tortillas", "qty": "8"}],
                     prep_time_minutes=10, cook_time_minutes=20, from_draft=True)
    day = _week_start()
    tools.plan_meal(day, "Overnight oats", slot="breakfast", weekly_plan_id=plan_id)
    tools.plan_meal(day, "Tacos", slot="dinner", weekly_plan_id=plan_id)
    tools.swap_meal_in_plan(plan_id, day, "Yogurt bowl", slot="breakfast")

    assert _offered() == ["Tacos"]


def test_a_component_breakfast_is_not_offered_as_dinner():
    # Review, 2026-10-09: a component-based plan's rows carry their kind in
    # component_category; slot is unused (plan_meal stores its 'dinner'
    # default). Granola as a breakfast component is a breakfast; a protein
    # component is a meal.
    plan_id = tools.create_weekly_plan(_week_start())["weekly_plan_id"]
    _recipe("Granola", 2)
    _recipe("Turkey chili", 30)
    tools.plan_meal(_week_start(), "Granola", weekly_plan_id=plan_id, component_category="breakfast")
    tools.plan_meal(_week_start(), "Turkey chili", weekly_plan_id=plan_id, component_category="protein")

    assert _offered() == ["Turkey chili"]
