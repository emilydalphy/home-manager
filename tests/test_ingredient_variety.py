"""
Ingredients are named specifically enough to shop for (Persian vs English
cucumber).

Emily, 2026-09-13, on the grocery list: "It says 6 cucumbers - does it
mean the persian cucumbers? Because that makes sense, but 6 english
cucumbers would be a crazy amount."

Two halves. The generation prompts (and the add_recipe tool) now ask for
the kind whenever the count only makes sense for that kind — "Persian
cucumbers" with qty "6" — and the grocery list shows exactly the name the
recipe wrote, so the word survives to the store. And for the times the
model forgets, recipes._PRODUCE_COUNT_PER_SERVING is a deterministic catch
on the handful of produce where the full-size kind and a small kind are
both bought by the count: a bare count too high for the ordinary kind is
flagged into the morning report (plan_quality "produce_variety_named",
info). Nothing is rewritten — six cucumbers were very likely six Persian
ones, and only the model knows — and the list line is left as written.
"""
from __future__ import annotations

import datetime
import inspect

import pytest

from app import agent, tools
from app.tools import plan_quality, recipes
from app.tools.plan_quality import check_week


def _problem(item, qty, servings):
    return recipes.produce_count_problem(item, qty, servings)


def _message(item, qty, servings):
    problem = _problem(item, qty, servings)
    return recipes.produce_count_message(item, qty, problem, servings) if problem else None


# ---------- the case Emily reported ----------

def test_six_cucumbers_for_two_are_a_lot_unless_they_are_persian():
    assert _message("Cucumbers", "6", 2) == (
        "Cucumbers '6' would be a lot of English cucumbers for 2 — Persian ones? The recipe should say which kind"
    )


def test_six_persian_cucumbers_for_two_are_fine():
    assert _problem("Persian cucumbers", "6", 2) is None


def test_six_english_cucumbers_are_caught_too_but_not_asked_which_kind():
    """The name already says the kind; the count is simply a lot."""
    assert _message("English cucumbers", "6", 2) == "English cucumbers '6' is a lot for 2"


# ---------- what is judged, and what is left alone ----------

@pytest.mark.parametrize("item, qty, servings", [
    ("Cucumbers", "6", 4),          # 1.5 a head of English cucumber
    ("Cucumber", "3", 2),
    ("Cucumber (fresh)", "6", 2),   # a "(fresh)" tag is not a kind
    ("Cucumbers", "6 each", 2),
    ("Cucumbers", "6 large", 2),    # "large" describes; it names no kind
    ("Cucumbers", "1 dozen", 2),    # a dozen is a count of twelve
    ("Cucumbers", "6 ct", 2),
    ("Cucumbers", "6 pcs", 2),
    ("Cucumbers, sliced", "6", 2),  # a prep descriptor names no kind either
    ("Cucumbers (English)", "6", 2),
    ("Tomatoes, on the vine", "12", 4),
    ("Tomatoes", "12", 4),
    ("Vine tomatoes", "12", 4),
    ("Potatoes", "12", 4),
    ("Russet potatoes", "12", 4),
    ("Peppers", "8", 4),
    ("Bell peppers", "8", 4),
    ("Red bell peppers", "4", 2),
    ("Onions", "8", 4),
    ("Yellow onions", "4", 2),
    ("Apples", "10", 4),
])
def test_a_bare_count_too_high_for_the_ordinary_kind_is_flagged(item, qty, servings):
    assert _problem(item, qty, servings) is not None


@pytest.mark.parametrize("item, qty, servings", [
    # A named kind is never second-guessed: the model said what it meant.
    ("Persian cucumbers", "6", 2),
    ("Mini cucumbers", "6", 2),
    ("Cucumbers (Persian)", "6", 2),    # ...wherever in the name it is said
    ("Cucumbers, Persian", "6", 2),
    ("Tomatoes (cherry)", "12", 4),
    ("Potatoes (baby)", "1 dozen", 4),
    ("Cherry tomatoes", "20", 4),
    ("Grape tomatoes", "30", 4),
    ("Roma tomatoes", "12", 4),
    ("Baby potatoes", "12", 4),
    ("Fingerling potatoes", "16", 4),
    ("Sweet potatoes", "12", 4),    # not the table's potato; not judged
    ("Mini sweet peppers", "8", 2),
    ("Jalapeño peppers", "8", 4),
    ("Shishito peppers", "20", 2),
    ("Pearl onions", "20", 4),
    ("Green onions", "8", 2),       # scallions come by the bunch; not an onion count
    ("Crab apples", "20", 4),
    # The amount says so instead.
    ("Cucumbers", "6 small", 2),
    ("Cucumbers", "6 (mini)", 2),
    # Not a count at all: the kind doesn't change a weight or a package.
    ("Cucumbers", "1 lb", 2),
    ("Tomatoes", "2 lbs", 2),
    ("Cherry tomatoes", "1 pint", 2),
    ("Potatoes", "1 bag", 2),
    # A count the ordinary kind can carry.
    ("Cucumbers", "2", 4),
    ("Cucumbers", "1", 1),
    ("Tomatoes", "8", 4),
    ("Onions", "6", 4),             # French onion soup for four
    ("Apples", "8", 4),             # a pie
    ("Bell peppers", "3", 2),
    # A different thing that borrows the noun.
    ("Black pepper", "8", 4),
    ("Red pepper flakes", "8", 4),
    ("Pepper", "2 tsp", 4),
    ("Apple cider vinegar", "6", 2),
    ("Onion powder", "6", 2),
    ("Tomato paste", "6", 2),
    # Nothing to judge.
    ("Cucumbers", "", 2),
    ("", "6", 2),
    ("Carrots", "12", 2),           # not on the table
])
def test_the_rule_stays_quiet_when_the_kind_is_named_or_the_count_fits(item, qty, servings):
    assert _problem(item, qty, servings) is None


def test_no_servings_means_the_documented_default_of_four():
    assert _problem("Cucumbers", "6", None)["per_serving"] == pytest.approx(1.5)
    assert _problem("Cucumbers", "4", None) is None


def test_the_problem_says_what_it_measured():
    problem = _problem("Tomatoes", "12", 4)
    assert problem["noun"] == "tomato"
    assert problem["ordinary"] == "full-size tomatoes"
    assert problem["small"] == "cherry or plum"
    assert problem["named"] is False
    assert problem["per_serving"] == pytest.approx(3)
    assert problem["high"] == 2


def test_apples_have_no_small_kind_to_ask_about():
    assert _message("Apples", "10", 4) == (
        "Apples '10' would be a lot of full-size apples for 4 — the recipe should say which kind"
    )


def test_every_ordinary_word_in_the_table_is_lowercase_and_single():
    """_produce_class compares one lowercase word at a time."""
    for _noun, ordinary, _name, _small, high in recipes._PRODUCE_COUNT_PER_SERVING:
        assert high > 0
        for word in ordinary:
            assert word == word.lower() and " " not in word, word
            assert word not in recipes._PRODUCE_GENERIC_WORDS, word


def test_a_dozen_counts_as_twelve():
    assert _problem("Cucumbers", "1 dozen", 2)["per_serving"] == pytest.approx(6)
    assert _problem("Apples", "1 dozen", 12) is None


def test_the_message_trims_the_line_it_quotes():
    assert _message("  Cucumbers ", " 6 ", 2).startswith("Cucumbers '6' would be")


def test_the_rule_is_exposed_on_the_tools_package():
    assert tools.produce_count_problem is recipes.produce_count_problem
    assert tools.produce_count_message is recipes.produce_count_message


# ---------- nothing is rewritten ----------

def test_the_cook_view_shows_the_count_as_written():
    """The plausibility table has no vegetable class, and this rule adds
    none: a count is a word short, not a number wrong."""
    shown = recipes.cooking_ingredients([{"item": "Cucumbers", "qty": "6", "category": "produce"}], servings=2)
    assert shown[0]["qty"] == "6"
    assert recipes.settle_cooking_quantities(
        [{"item": "Cucumbers", "qty": "6", "category": "produce"}], 2,
    ) == [{"item": "Cucumbers", "qty": "6", "category": "produce"}]


def _this_week():
    today = datetime.date.today()
    return today - datetime.timedelta(days=today.weekday()), today


def _approve(*recipes_and_ingredients):
    tools.add_member("Emily")
    tools.add_member("Sam")
    monday, today = _this_week()
    plan_id = tools.create_weekly_plan(monday.isoformat())["weekly_plan_id"]
    for index, (name, ingredients) in enumerate(recipes_and_ingredients):
        tools.add_recipe(name, ingredients=ingredients, instructions=["Toss."], default_servings=2)
        day = monday + datetime.timedelta(days=index)
        tools.plan_meal(day.isoformat(), name, slot="dinner", weekly_plan_id=plan_id)
    tools.approve_weekly_plan(plan_id, "Emily")
    return {g["item"]: g["quantity"] for g in tools.list_grocery_list()}


def test_the_grocery_list_keeps_the_kind_the_recipe_named():
    bought = _approve(("Shirazi Salad", [
        {"item": "Persian cucumbers", "qty": "6", "category": "produce"},
        {"item": "Tomatoes", "qty": "2", "category": "produce"},
    ]))
    assert bought["Persian cucumbers"] == "6"
    assert "Cucumbers" not in bought


def test_persian_cucumbers_and_cucumbers_from_two_recipes_stay_two_lines():
    """
    Decided, not an accident: a Persian cucumber and an English cucumber
    are two different things at the store, and the merge (grocery._merge_key)
    is built to fail toward two lines rather than combine two things. Six
    Persian and one English on one line would read as seven of something.
    """
    bought = _approve(
        ("Shirazi Salad", [{"item": "Persian cucumbers", "qty": "6", "category": "produce"}]),
        ("Greek Salad", [{"item": "Cucumbers", "qty": "1", "category": "produce"}]),
    )
    assert bought["Persian cucumbers"] == "6"
    assert bought["Cucumbers"] == "1"


def test_a_vague_count_reaches_the_list_as_written():
    """The catch is a flag in the morning report, never an edit to the
    line: the list shows "6" of "Cucumbers", nothing appended."""
    bought = _approve(("Cucumber Salad", [{"item": "Cucumbers", "qty": "6", "category": "produce"}]))
    assert bought == {"Cucumbers": "6"}


# ---------- the flag: plan quality ----------

def _entry(**overrides):
    base = {
        "date": "2026-09-14", "slot": "dinner", "slot_state": "planned",
        "meal_name": "Cucumber Salad",
        "reasoning": "You asked for something cool.", "food_groups": ["vegetable"],
        "main_protein": None, "prep_time_minutes": None, "cook_time_minutes": None,
        "is_new_recipe": True, "links_to": None,
        "ingredients": [
            {"item": "Cucumbers", "qty": "6", "category": "produce"},
            {"item": "Red onion", "qty": "1", "category": "produce"},
            {"item": "Feta", "qty": "200 g", "category": "dairy"},
        ],
        "instructions": [], "default_servings": 2,
    }
    base.update(overrides)
    return base


def _flags(entries):
    return [v for v in check_week(entries, {}) if v.rule == "produce_variety_named"]


def test_plan_quality_flags_the_vague_count_as_info():
    violations = _flags([_entry()])
    assert len(violations) == 1
    v = violations[0]
    assert v.severity == "info"
    assert v.date == "2026-09-14" and v.slot == "dinner"
    assert v.message == (
        "Cucumber Salad: Cucumbers '6' would be a lot of English cucumbers for 2 "
        "— Persian ones? The recipe should say which kind."
    )


def test_plan_quality_is_quiet_when_the_kind_is_named():
    named = [dict(i, item="Persian cucumbers") if i["item"] == "Cucumbers" else i for i in _entry()["ingredients"]]
    assert not _flags([_entry(ingredients=named)])


def test_plan_quality_is_quiet_when_the_count_fits_the_table():
    assert not _flags([_entry(default_servings=6)])


def test_plan_quality_joins_two_vague_lines_in_one_meal():
    two = _entry()["ingredients"] + [{"item": "Tomatoes", "qty": "12", "category": "produce"}]
    violations = _flags([_entry(ingredients=two)])
    assert len(violations) == 1
    assert "Cucumbers '6'" in violations[0].message
    assert "Tomatoes '12' would be a lot of full-size tomatoes for 2 — cherry or plum ones?" in violations[0].message


def test_plan_quality_ignores_an_empty_or_unplanned_slot():
    entries = [_entry(slot_state="planned_empty"), _entry(ingredients=[])]
    assert not _flags(entries)
    # A malformed line is skipped rather than raised on (the check is log-only).
    assert not plan_quality._produce_variety_named([_entry(ingredients=["not a dict"])], {})


def test_plan_quality_reads_the_recipe_back_from_the_database():
    tools.add_recipe(
        "Cucumber Salad", ingredients=_entry()["ingredients"], instructions=["Toss."], default_servings=2,
    )
    plan_id = tools.create_weekly_plan("2026-09-14")["weekly_plan_id"]
    tools.plan_meal("2026-09-14", "Cucumber Salad", slot="dinner", weekly_plan_id=plan_id)

    entries = plan_quality._load_plan_entries(plan_id)

    assert [v.rule for v in _flags(entries)] == ["produce_variety_named"]


# ---------- the prompt half ----------

def _prompt_text(fn) -> str:
    """The instructions as the model reads them — the source's
    line-continuation backslashes joined back up."""
    return inspect.getsource(fn).replace("\\\n", "")


def test_the_generation_prompts_ask_for_the_kind_when_the_count_depends_on_it():
    texts = {
        fn.__name__: _prompt_text(fn)
        for fn in (agent.generate_weekly_plan_llm, agent.generate_component_plan_llm)
    }
    texts["sides"] = agent._SIDE_INSTRUCTIONS
    for name, text in texts.items():
        assert '"Persian cucumbers" with qty "6"' in text, name
        assert 'bare "Cucumbers"' in text, name


def test_the_day_based_prompt_says_why():
    text = _prompt_text(agent.generate_weekly_plan_llm)
    assert "six English cucumbers" in text
    assert "the list shows exactly the name you write" in text


def test_the_add_recipe_tool_asks_for_the_kind_in_the_item_name():
    schema = next(t for t in agent.TOOL_DEFINITIONS if t["name"] == "add_recipe")
    item = schema["input_schema"]["properties"]["ingredients"]["items"]["properties"]["item"]
    assert "Persian cucumbers" in item["description"]
