"""
Every meal is a full plate.

Emily, 2026-09-05, settling the question plan_quality's `full_plate` rule
had been warning about and deliberately not acting on: a plate is protein +
vegetable, plus a carb unless the household eats low-carb; it applies to all
four slots with a lighter two-group floor on breakfast and snack; a short
plate gets a SIDE attached rather than the week regenerated; and a one-pot
dish that already covers the rule is left alone.

Both model calls are stubbed throughout — the week generator the way
tests/test_week_generation.py does it, and the side generator the same way.
What's under test is everything around them: which meals are judged short,
which are skipped, how many side calls a week is allowed to make, and
whether the side that comes back actually reaches the shopping list, the
Cooker and the card.
"""
import datetime

import pytest

from app import agent, tools
from app.tools import plates


def _week_start() -> str:
    today = datetime.date.today()
    monday = today - datetime.timedelta(days=today.weekday())
    return (monday + datetime.timedelta(days=7)).isoformat()


# ---------- the rule itself, with no database in sight ----------

@pytest.mark.parametrize("style", [
    "keto", "Keto", "ketogenic", "low-carb", "low carb", "LOW  CARB",
    "high-protein, low-carb", "strict keto, no cheating", "carnivore", "Atkins",
])
def test_a_low_carb_eating_style_is_recognized(style):
    assert plates.is_low_carb(style) is True
    assert plates.plate_rule(style) == ("protein", "vegetable")


@pytest.mark.parametrize("style", [
    "", None, "vegetarian", "mediterranean", "high-protein", "gluten free",
    "lots of carbs", "whatever's easy",
])
def test_everything_else_still_gets_a_carb(style):
    assert plates.is_low_carb(style) is False
    assert plates.plate_rule(style) == ("protein", "vegetable", "carb")


def test_a_dinner_is_short_of_whatever_the_rule_names():
    rule = plates.plate_rule("")
    assert plates.missing_groups(
        {"slot": "dinner", "food_groups": ["protein"]}, rule
    ) == ["vegetable", "carb"]
    assert plates.is_complete(
        {"slot": "dinner", "food_groups": ["protein", "vegetable", "carb"]}, rule
    )


def test_a_keto_dinner_needs_no_carb():
    rule = plates.plate_rule("keto")
    assert plates.missing_groups({"slot": "dinner", "food_groups": ["protein"]}, rule) == ["vegetable"]
    assert plates.is_complete({"slot": "dinner", "food_groups": ["protein", "vegetable"]}, rule)


def test_breakfast_and_snack_only_have_to_reach_two_groups():
    """
    The light rule, stated exactly (see plates.LIGHT_SLOT_MIN_GROUPS): two
    of the three groups, no particular two. "Just a granola bar" and "just
    a fruit" both carry at most ONE group, which is precisely why a
    two-group floor is the whole of that promise rather than a list of
    foods the app disapproves of.
    """
    rule = plates.plate_rule("")
    for slot in ("breakfast", "snack"):
        assert plates.missing_groups({"slot": slot, "food_groups": ["carb"]}, rule) == ["protein"]
        assert plates.is_complete({"slot": slot, "food_groups": ["carb", "protein"]}, rule)
        assert plates.is_complete({"slot": slot, "food_groups": ["vegetable", "carb"]}, rule)
    # Lunch is NOT light — a packed lunch is a real plate.
    assert plates.missing_groups({"slot": "lunch", "food_groups": ["carb", "protein"]}, rule) == ["vegetable"]


def test_a_meal_with_no_recorded_groups_is_left_alone_rather_than_guessed_at():
    rule = plates.plate_rule("")
    entry = {"slot": "dinner", "food_groups": []}
    assert plates.missing_groups(entry, rule) == []
    assert plates.has_food_groups(entry) is False


# ---------- generation: the pass over a finished week ----------

SALAD = {
    "name": "Lemony green salad",
    "covers": ["vegetable"],
    "ingredients": [{"item": "Romaine", "qty": "1 head", "category": "produce"}],
    "instructions": ["Tear the romaine.", "Dress and toss."],
    "minutes": 5,
}
RICE = {
    "name": "Buttered rice",
    "covers": ["carb"],
    "ingredients": [{"item": "Long grain rice", "qty": "1 bag", "category": "pantry"}],
    "instructions": ["Simmer the rice."],
    "minutes": 20,
}


@pytest.fixture
def recipes():
    # A genuine one-pot plate: protein, vegetable and carb in one dish.
    tools.add_recipe(
        "Chili", ingredients=[{"item": "beans", "qty": "1 tin"}],
        food_groups=["protein", "vegetable", "carb"],
        prep_time_minutes=10, cook_time_minutes=20,
    )
    # Protein and nothing else.
    tools.add_recipe(
        "Grilled chicken thighs", ingredients=[{"item": "chicken thighs", "qty": "2 lb"}],
        food_groups=["protein"], prep_time_minutes=5, cook_time_minutes=25,
    )
    # One group, so short even under the light breakfast rule.
    tools.add_recipe(
        "Plain yogurt", ingredients=[{"item": "yogurt", "qty": "1 tub"}],
        food_groups=["protein"],
    )


@pytest.fixture
def stub_week(monkeypatch):
    """The week generator, canned — same idea as test_week_generation's."""
    def _stub(days):
        monkeypatch.setattr(agent, "generate_weekly_plan_llm", lambda context: days)
    return _stub


@pytest.fixture
def stub_sides(monkeypatch):
    """
    The side call, canned, recording every context it was handed so a test
    can assert on which meals were judged short and what they were told.
    """
    calls = []

    def _stub(sides=(SALAD, RICE)):
        def _fake(context):
            calls.append(context)
            # Only ever hand back sides for groups actually asked for, the
            # way the real call is instructed to.
            wanted = set(context.get("missing") or [])
            return [s for s in sides if set(s["covers"]) & wanted]
        monkeypatch.setattr(agent, "generate_sides_llm", _fake)
        return calls

    return _stub


def _week(week: str, meal: str = "Chili", overrides: dict | None = None) -> list[dict]:
    """A complete 28-slot week of `meal`, with (date, slot) overrides."""
    overrides = overrides or {}
    return [
        {
            "date": day, "slot": slot,
            "meal_name": overrides.get((day, slot), meal),
            "is_new_recipe": False, "reasoning": "fits the week",
        }
        for day in tools._week_dates(week)
        for slot in tools.WEEK_SLOTS
    ]


def _entries(plan_id):
    return {(m["date"], m["slot"]): m for m in tools.get_weekly_plan(plan_id)["meals"]}


def test_a_protein_only_dinner_gets_a_side(recipes, stub_week, stub_sides):
    week = _week_start()
    tuesday = tools._week_dates(week)[1]
    stub_week(_week(week, overrides={(tuesday, "dinner"): "Grilled chicken thighs"}))
    calls = stub_sides()

    plan = agent.generate_weekly_plan(week)

    entry = _entries(plan["weekly_plan_id"])[(tuesday, "dinner")]
    assert [s["name"] for s in entry["sides"]] == ["Lemony green salad", "Buttered rice"]
    assert entry["sides_label"] == "with Lemony green salad and Buttered rice"
    # The plate now claims what the sides actually supply, and only that.
    assert sorted(entry["food_groups"]) == ["carb", "protein", "vegetable"]
    assert len(calls) == 1
    assert calls[0]["meal"] == "Grilled chicken thighs"
    assert calls[0]["missing"] == ["vegetable", "carb"]


def test_a_one_pot_dinner_is_left_exactly_as_it_was(recipes, stub_week, stub_sides):
    """
    Emily, decision 6a: a dish whose own food_groups already cover the rule
    is complete. Nothing is bolted onto it, and no model call is spent
    asking.
    """
    week = _week_start()
    stub_week(_week(week))
    calls = stub_sides()

    plan = agent.generate_weekly_plan(week)

    assert calls == [], "a complete week should cost nothing to complete"
    assert all(not m["sides"] for m in tools.get_weekly_plan(plan["weekly_plan_id"])["meals"])


def test_a_one_pot_dinner_says_so_on_its_card(recipes, stub_week, stub_sides):
    week = _week_start()
    stub_week(_week(week))
    stub_sides()

    plan = agent.generate_weekly_plan(week)
    menu = tools.get_week_menu(plan["weekly_plan_id"])
    dinner = menu["days"][0]["dinner"]

    assert dinner["plate_note"] == "one-pot, nothing extra"
    # The reassurance half is dinner-only on purpose — see get_week_menu's
    # plate_note. Breakfast says nothing when nothing was added.
    assert menu["days"][0]["breakfast"]["plate_note"] == ""


def test_a_keto_household_gets_no_carb_bolted_on(recipes, stub_week, stub_sides):
    week = _week_start()
    tuesday = tools._week_dates(week)[1]
    tools.edit_preference("eating_style", "keto — high fat, low carb")
    stub_week(_week(week, overrides={(tuesday, "dinner"): "Grilled chicken thighs"}))
    calls = stub_sides()

    plan = agent.generate_weekly_plan(week)

    entry = _entries(plan["weekly_plan_id"])[(tuesday, "dinner")]
    assert calls[0]["missing"] == ["vegetable"], "keto's full plate is protein + vegetable"
    assert [s["name"] for s in entry["sides"]] == ["Lemony green salad"]
    assert "carb" not in entry["food_groups"]


def test_a_one_group_breakfast_gets_rounded_out(recipes, stub_week, stub_sides):
    week = _week_start()
    monday = tools._week_dates(week)[0]
    stub_week(_week(week, overrides={(monday, "breakfast"): "Plain yogurt"}))
    calls = stub_sides()

    plan = agent.generate_weekly_plan(week)

    entry = _entries(plan["weekly_plan_id"])[(monday, "breakfast")]
    # Two groups is the floor, so exactly one is asked for — not the full
    # three a dinner would want.
    assert calls[0]["missing"] == ["vegetable"]
    assert [s["name"] for s in entry["sides"]] == ["Lemony green salad"]


def test_turning_the_preference_off_stops_it_attaching_anything(recipes, stub_week, stub_sides, caplog):
    week = _week_start()
    tuesday = tools._week_dates(week)[1]
    tools.edit_preference("complete_plates", False)
    stub_week(_week(week, overrides={(tuesday, "dinner"): "Grilled chicken thighs"}))
    calls = stub_sides()

    with caplog.at_level("INFO", logger="home_manager"):
        plan = agent.generate_weekly_plan(week)

    assert calls == [], "no model call, and no money spent, when they've said don't"
    assert _entries(plan["weekly_plan_id"])[(tuesday, "dinner")]["sides"] == []
    # The rule doesn't disappear when it's switched off — it just stops
    # acting, and still says what it saw.
    assert any("complete_plates is off" in r.getMessage() for r in caplog.records)


def test_the_preference_survives_a_round_trip_and_reads_back_as_a_yes_or_no():
    assert tools.get_household_memory()["complete_plates"] is True
    assert tools.edit_preference("complete_plates", False) == {"complete_plates": False}
    assert tools.get_household_memory()["complete_plates"] is False
    # A model answering in words is answering, not setting a truthy string.
    tools.edit_preference("complete_plates", "true")
    assert tools.get_household_memory()["complete_plates"] is True
    tools.edit_preference("complete_plates", "off")
    assert tools.get_household_memory()["complete_plates"] is False


def test_a_reheat_night_gets_no_side(recipes, stub_week, stub_sides):
    """
    Nothing is cooked on a leftovers night — its batch was made earlier.
    Adding a salad to it would buy groceries for a night the household was
    told cooks nothing. The COOK night is the plate that has to be whole.
    """
    week = _week_start()
    dates = tools._week_dates(week)
    monday, wednesday = dates[0], dates[2]
    days = _week(week, overrides={
        (monday, "dinner"): "Grilled chicken thighs",
        (wednesday, "dinner"): "Grilled chicken thighs",
    })
    for day in days:
        if day["date"] == wednesday and day["slot"] == "dinner":
            day["derived_from"] = {"links_to": f"{monday}:dinner"}
    stub_week(days)
    calls = stub_sides()

    plan = agent.generate_weekly_plan(week)

    entries = _entries(plan["weekly_plan_id"])
    assert entries[(wednesday, "dinner")]["sides"] == []
    assert entries[(monday, "dinner")]["sides"], "the night that actually cooks does get one"
    assert [c["date"] for c in calls] == [monday]


def test_an_empty_or_open_slot_is_never_given_a_side(recipes, stub_week, stub_sides):
    week = _week_start()
    dates = tools._week_dates(week)
    thursday = dates[3]
    days = [d for d in _week(week) if not (d["date"] == thursday and d["slot"] == "dinner")]
    stub_week(days)
    calls = stub_sides()

    plan = agent.generate_weekly_plan(week)

    gap = _entries(plan["weekly_plan_id"])[(thursday, "dinner")]
    assert gap["slot_state"] == "open"
    assert gap["sides"] == []
    assert calls == []


def test_the_number_of_side_calls_per_week_is_capped(recipes, stub_week, stub_sides, caplog):
    """
    A week where nearly everything is short is a generation problem, not
    something to paper over with twenty-eight model calls. Six get made,
    dinners first, and the rest are named in the log rather than absorbed.
    """
    week = _week_start()
    stub_week(_week(week, meal="Grilled chicken thighs"))
    calls = stub_sides()

    with caplog.at_level("WARNING", logger="home_manager"):
        plan = agent.generate_weekly_plan(week)

    assert len(calls) == agent.MAX_PLATE_SIDE_CALLS == 6
    assert {c["slot"] for c in calls} == {"dinner"}, "the cap is spent on dinners first"
    assert sum(
        1 for m in tools.get_weekly_plan(plan["weekly_plan_id"])["meals"] if m["sides"]
    ) == 6
    assert any("past the 6-side cap" in str(r.getMessage()) for r in caplog.records)


def test_a_side_call_that_fails_costs_the_meal_its_side_and_nothing_else(
    recipes, stub_week, monkeypatch,
):
    week = _week_start()
    tuesday = tools._week_dates(week)[1]
    stub_week(_week(week, overrides={(tuesday, "dinner"): "Grilled chicken thighs"}))

    def _boom(context):
        raise RuntimeError("the side model is having a day")
    monkeypatch.setattr(agent, "generate_sides_llm", _boom)

    plan = agent.generate_weekly_plan(week)

    audit = tools.audit_plan_slots(plan["weekly_plan_id"])
    assert audit["complete"] is True, "a week must never be lost to a side dish"
    assert _entries(plan["weekly_plan_id"])[(tuesday, "dinner")]["sides"] == []


# ---------- what the side then has to reach ----------

def test_the_sides_ingredients_land_on_the_shopping_list_at_approval(
    recipes, stub_week, stub_sides,
):
    week = _week_start()
    tuesday = tools._week_dates(week)[1]
    stub_week(_week(week, overrides={(tuesday, "dinner"): "Grilled chicken thighs"}))
    stub_sides()

    plan = agent.generate_weekly_plan(week)
    before = {i["item"].lower() for i in tools.list_grocery_list()}
    assert "romaine" not in before, "a draft buys nothing"

    tools.approve_weekly_plan(plan["weekly_plan_id"])

    after = {i["item"].lower() for i in tools.list_grocery_list()}
    assert "romaine" in after
    assert "long grain rice" in after
    assert "chicken thighs" in after


def test_the_promise_on_the_draft_screen_counts_the_side_too(recipes, stub_week, stub_sides):
    week = _week_start()
    tuesday = tools._week_dates(week)[1]
    stub_week(_week(week, overrides={(tuesday, "dinner"): "Grilled chicken thighs"}))
    stub_sides()

    plan = agent.generate_weekly_plan(week)
    promised = tools.preview_plan_grocery_impact(plan["weekly_plan_id"])["would_add_count"]
    delivered = tools.approve_weekly_plan(plan["weekly_plan_id"])["groceries_added_count"]

    assert promised == delivered
    assert promised == 4, "beans, chicken thighs, romaine, rice"


def test_clearing_the_week_takes_the_sides_shopping_back_off_again(
    recipes, stub_week, stub_sides,
):
    """
    Symmetry. A side's ingredients are recorded against the same entry as
    the dish, which is the whole reason unwinding them needs no code of its
    own — see plates.py.
    """
    week = _week_start()
    tuesday = tools._week_dates(week)[1]
    stub_week(_week(week, overrides={(tuesday, "dinner"): "Grilled chicken thighs"}))
    stub_sides()

    plan = agent.generate_weekly_plan(week)
    tools.approve_weekly_plan(plan["weekly_plan_id"])
    assert "romaine" in {i["item"].lower() for i in tools.list_grocery_list()}

    tools.clear_weekly_plan(plan["weekly_plan_id"])

    assert "romaine" not in {i["item"].lower() for i in tools.list_grocery_list()}
    assert "long grain rice" not in {i["item"].lower() for i in tools.list_grocery_list()}


def test_the_cooker_shows_the_side_under_the_dishs_own_steps(recipes, stub_week, stub_sides):
    week = _week_start()
    tuesday = tools._week_dates(week)[1]
    tools.update_recipe_details(
        "Grilled chicken thighs", instructions=["Season the thighs.", "Grill 25 minutes."],
    )
    stub_week(_week(week, overrides={(tuesday, "dinner"): "Grilled chicken thighs"}))
    stub_sides()

    plan = agent.generate_weekly_plan(week)
    card = next(
        m for m in tools.get_cooker_view(plan["weekly_plan_id"])["meals"]
        if m["date"] == tuesday and m["slot"] == "dinner"
    )

    assert card["instructions"][:2] == ["Season the thighs.", "Grill 25 minutes."]
    assert card["instructions"][2] == "Alongside — Lemony green salad: Tear the romaine."
    assert card["instructions"][3] == "Alongside: Dress and toss."
    assert card["instructions"][4] == "Alongside — Buttered rice: Simmer the rice."
    assert {i["item"] for i in card["ingredients"]} == {
        "chicken thighs", "Romaine", "Long grain rice",
    }
    assert card["sides_label"] == "with Lemony green salad and Buttered rice"


def test_the_plan_card_names_the_side(recipes, stub_week, stub_sides):
    week = _week_start()
    tuesday = tools._week_dates(week)[1]
    stub_week(_week(week, overrides={(tuesday, "dinner"): "Grilled chicken thighs"}))
    stub_sides()

    plan = agent.generate_weekly_plan(week)
    menu = tools.get_week_menu(plan["weekly_plan_id"])
    day = next(d for d in menu["days"] if d["date"] == tuesday)

    assert day["dinner"]["plate_note"] == "with Lemony green salad and Buttered rice"


# ---------- telling the household, once ----------

def test_the_household_is_told_once_that_this_is_deliberate(
    recipes, stub_week, stub_sides, signed_in,
):
    week = _week_start()
    tuesday = tools._week_dates(week)[1]
    stub_week(_week(week, overrides={(tuesday, "dinner"): "Grilled chicken thighs"}))
    stub_sides()
    plan = agent.generate_weekly_plan(week)

    first = signed_in.get(f"/api/week-menu?weekly_plan_id={plan['weekly_plan_id']}").json()
    second = signed_in.get(f"/api/week-menu?weekly_plan_id={plan['weekly_plan_id']}").json()

    assert first["plates_note"], "the first look explains what happened"
    assert "small side" in first["plates_note"]
    # State the thing, then the way out (DESIGN_SYSTEM.md §8).
    assert "rather I left them alone" in first["plates_note"]
    assert second["plates_note"] is None, "told once, not every week"


def test_a_week_with_nothing_added_says_nothing(recipes, stub_week, stub_sides, signed_in):
    week = _week_start()
    stub_week(_week(week))
    stub_sides()
    plan = agent.generate_weekly_plan(week)

    body = signed_in.get(f"/api/week-menu?weekly_plan_id={plan['weekly_plan_id']}").json()

    assert body["plates_note"] is None


def test_the_assistants_own_read_of_the_menu_doesnt_burn_the_telling(
    recipes, stub_week, stub_sides, signed_in,
):
    """
    get_week_menu is also a tool the assistant calls mid-conversation.
    Marking the sentence as said there would spend the household's one
    telling on a read nobody saw — so only the screen's own fetch marks it.
    """
    week = _week_start()
    tuesday = tools._week_dates(week)[1]
    stub_week(_week(week, overrides={(tuesday, "dinner"): "Grilled chicken thighs"}))
    stub_sides()
    plan = agent.generate_weekly_plan(week)

    tools.get_week_menu(plan["weekly_plan_id"])
    tools.get_week_menu(plan["weekly_plan_id"])

    body = signed_in.get(f"/api/week-menu?weekly_plan_id={plan['weekly_plan_id']}").json()
    assert body["plates_note"], "the screen still gets to say it"


# ---------- the model is asked to get it right the first time ----------

def test_both_generation_prompts_state_the_plate_rule():
    """
    The repair pass is the exception, not the plan. Both instruction blocks
    have to carry the rule, or a component-mode household gets a pool that
    can't make a plate and no pass to fix it.
    """
    import inspect

    day_based = inspect.getsource(agent.generate_weekly_plan_llm)
    component = inspect.getsource(agent.generate_component_plan_llm)
    for source in (day_based, component):
        assert "EVERY MEAL IS A FULL PLATE" in source
        assert "low-carb" in source
