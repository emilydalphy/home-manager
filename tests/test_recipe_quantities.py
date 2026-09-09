"""
"One bottle olive oil" is not a cooking measurement.

Julia (first beta tester, 2026-09-08): "The recipe quantities are not
specific enough. It's saying stuff like 'one bottle olive oil' which is
incorrect. It should give actual measurements in the cooking view." And,
separately: "Recipe generation quality is low. The dishes sound good, but
the recipe details are not accurate."

The "1 bottle" was never the model inventing something — it is what the
app asks for. generate_weekly_plan_llm's ingredient bullet tells it to
write each qty "as how it's actually bought at the store", because that
string IS the grocery line, and quantities._PACKAGE_UNITS then buys one
bottle for the whole week however many dinners name it. Correct for the
list; nonsense in the pan, and scale_recipe made it worse by halving it to
"0.5 bottles".

So an ingredient now carries two amounts: `qty` (bought, unchanged, what
grocery reads) and `cook_qty` (what goes in, derived from
recipes.COOKING_QUANTITIES_PER_4 where a recipe hasn't got one). These
tests pin both halves of that split, and the thing that must not move: the
grocery list still says "1 bottle".
"""
from __future__ import annotations

import datetime
import types

import pytest

from app import agent, tools


# ---------- the validator's truth table ----------

@pytest.mark.parametrize("item, qty, ok, reason", [
    # Real measurements, all fine.
    ("Olive oil", "2 tbsp", True, None),
    ("Salt", "1 tsp", True, None),
    ("Baby spinach", "4 cups", True, None),
    ("Chicken thighs", "1.5 lb", True, None),
    ("Flour", "250 g", True, None),
    ("Milk", "500 ml", True, None),
    ("Garlic", "3 cloves", True, None),
    # A bare count is fine for something countable, and only for that.
    ("Lemons", "2", True, None),
    ("Bell peppers", "2", True, None),
    ("Olive oil", "2", False, "unmeasured"),
    # A per-meal produce unit is a real amount a cook can act on.
    ("Lettuce", "1 head", True, None),
    ("Cilantro", "1 bunch", True, None),
    # Package words: the whole complaint.
    ("Olive oil", "1 bottle", False, "package_unit"),
    ("Honey", "1 jar", False, "package_unit"),
    ("Baby spinach", "1 bag", False, "package_unit"),
    ("Spaghetti", "1 box", False, "package_unit"),
    ("Chicken thighs", "1 pack", False, "package_unit"),
    ("Chicken broth", "1 carton", False, "package_unit"),
    ("Cottage cheese", "1 tub", False, "package_unit"),
    ("Salt", "1 container", False, "package_unit"),
    # ...including the sized ones, which are still packages to a cook.
    ("Baby spinach", "1 lb bag", False, "package_unit"),
    # A can keeps its place, but only for a canned good and only with a
    # size on it.
    ("Diced tomatoes", "1 can (14 oz)", True, None),
    ("Black beans", "15 oz can", True, None),
    ("Diced tomatoes", "1 can", False, "unsized_can"),
    ("Olive oil", "1 can", False, "package_unit"),
    ("Paprika", "1 tin", False, "package_unit"),
    # Freeform: the handful of real cooking phrases pass, nothing else.
    ("Salt", "to taste", True, None),
    ("Parsley", "for garnish", True, None),
    ("Olive oil", "some", False, "unmeasured"),
    ("Olive oil", "", False, "missing"),
])
def test_the_validator_truth_table(item, qty, ok, reason):
    result = tools.validate_measured_quantities([{"item": item, "qty": qty}])
    assert result["ok"] is ok, result["problems"]
    if not ok:
        assert result["problems"][0]["reason"] == reason


def test_the_validator_names_what_it_would_write_instead():
    """A problem carries its own repair, so a caller never has to ask twice."""
    problems = tools.validate_measured_quantities([
        {"item": "Olive oil", "qty": "1 bottle"},
        {"item": "Salt", "qty": ""},
    ])["problems"]

    assert [p["suggested"] for p in problems] == ["2 tbsp", "1 tsp"]


def test_an_unknown_item_in_a_package_still_gets_a_measurement():
    """
    Nothing in the table matches "chipotle aioli", but a cook still cannot
    act on a bottle — the class defaults answer for it rather than letting
    a package word through.
    """
    assert tools.cooking_quantity("Chipotle aioli", shopping_qty="1 bottle") == "2 tbsp"


def test_an_unknown_item_with_no_amount_at_all_is_not_invented():
    """The one honest blank: no table entry, no class, nothing to derive from."""
    assert tools.cooking_quantity("Gochujang", shopping_qty="") is None


# ---------- the fixture Julia reported ----------

def test_one_bottle_olive_oil_becomes_two_tablespoons():
    ingredients = [
        {"item": "Olive oil", "qty": "1 bottle", "category": "pantry"},
        {"item": "Baby spinach", "qty": "1 bag", "category": "produce"},
        {"item": "Salt", "qty": "", "category": "pantry"},
    ]

    cooking = tools.cooking_ingredients(ingredients, servings=4)

    assert [(i["item"], i["qty"]) for i in cooking] == [
        ("Olive oil", "2 tbsp"), ("Baby spinach", "4 cups"), ("Salt", "1 tsp"),
    ]
    # The bought amount is not lost, just moved out of the cook's way.
    assert cooking[0]["shopping_qty"] == "1 bottle"


def test_a_stored_cook_qty_wins_over_the_table():
    """
    The table is the fallback, not the answer. A recipe that was filled in
    with real measurements keeps them.
    """
    cooking = tools.cooking_ingredients(
        [{"item": "Olive oil", "qty": "1 bottle", "cook_qty": "1/4 cup"}], servings=4,
    )
    assert cooking[0]["qty"] == "1/4 cup"


def test_a_stored_cook_qty_that_is_itself_a_package_is_not_trusted():
    cooking = tools.cooking_ingredients(
        [{"item": "Olive oil", "qty": "1 bottle", "cook_qty": "1 bottle"}], servings=4,
    )
    assert cooking[0]["qty"] == "2 tbsp"


# ---------- the Cook screen ----------

def _monday() -> datetime.date:
    today = datetime.date.today()
    return today - datetime.timedelta(days=today.weekday())


def _pasta(default_servings=4):
    tools.add_recipe(
        "Lemon Pasta",
        ingredients=[
            {"item": "Olive oil", "qty": "1 bottle", "category": "pantry"},
            {"item": "Spaghetti", "qty": "1 box", "category": "pantry"},
            {"item": "Baby spinach", "qty": "1 bag", "category": "produce"},
            {"item": "Salt", "qty": "to taste", "category": "pantry"},
        ],
        instructions=["Boil the spaghetti.", "Warm the olive oil, wilt the spinach.", "Salt and toss."],
        default_servings=default_servings,
    )


def _plan_tonight():
    plan_id = tools.create_weekly_plan(_monday().isoformat())["weekly_plan_id"]
    tools.plan_meal(datetime.date.today().isoformat(), "Lemon Pasta", slot="dinner", weekly_plan_id=plan_id)
    return plan_id


def test_the_cook_view_shows_measured_quantities_scaled_from_four_to_two():
    tools.add_member("Alex")
    tools.add_member("Sam")
    _pasta(default_servings=4)
    plan_id = _plan_tonight()

    card = tools.get_cooker_view(plan_id)["meals"][0]

    assert card["default_servings"] == 2, "two people at the table tonight"
    assert {i["item"]: i["qty"] for i in card["ingredients"]} == {
        # Half of the table's per-4 amounts, and not a package word among them.
        "Olive oil": "1 tbsp",
        "Spaghetti": "6 oz",
        "Baby spinach": "2 cups",
        "Salt": "to taste",
    }


def test_no_cook_card_ingredient_ever_shows_a_package_word():
    """
    The guarantee, stated as itself rather than as a list of amounts: a
    package unit is what Julia saw, and none may survive to the card.
    """
    tools.add_member("Alex")
    _pasta()
    plan_id = _plan_tonight()

    card = tools.get_cooker_view(plan_id)["meals"][0]

    package_words = {"bottle", "bottles", "bag", "bags", "box", "boxes", "jar", "jars",
                     "tub", "tubs", "pack", "packs", "carton", "cartons", "container", "containers"}
    for ing in card["ingredients"]:
        assert not (package_words & set((ing["qty"] or "").lower().split())), ing


def test_the_serving_stepper_scales_the_cooking_amount_not_the_bottle():
    """
    /api/recipes/scale is what the Cook screen's +/- stepper calls. It used
    to answer "0.5 bottles" — a package word wearing a fraction.
    """
    _pasta(default_servings=4)

    scaled = {i["item"]: i["qty"] for i in tools.scale_recipe("Lemon Pasta", 8)["scaled_ingredients"]}

    assert scaled["Olive oil"] == "4 tbsp"
    assert scaled["Baby spinach"] == "8 cups"


def test_half_a_head_of_garlic_is_not_an_amount():
    """
    A head, a clove, a bunch: things that only come whole. Scaling a
    recipe down used to write "0.5 heads", which is the same shape of
    nonsense as "0.5 bottles" even though a head is a real unit.
    """
    tools.add_recipe(
        "Garlic Bread",
        ingredients=[{"item": "Garlic", "qty": "1 head"}, {"item": "Bread", "qty": "1 loaf"}],
        default_servings=4,
    )

    scaled = {i["item"]: i["qty"] for i in tools.scale_recipe("Garlic Bread", 2)["scaled_ingredients"]}

    assert scaled["Garlic"] == "1 head"
    # ...and up-scaling is untouched: two tables' worth really is two heads.
    doubled = {i["item"]: i["qty"] for i in tools.scale_recipe("Garlic Bread", 8)["scaled_ingredients"]}
    assert doubled["Garlic"] == "2 heads"


# ---------- the grocery list, which must NOT move ----------

def test_the_grocery_list_still_buys_one_bottle():
    """
    The other half of the split. "1 bottle olive oil" is the correct thing
    to tell a shopper, and one bottle covers the week however many dinners
    name it (quantities._PACKAGE_UNITS) — none of the cooking work above is
    allowed to reach the list.
    """
    tools.add_member("Alex")
    tools.add_member("Sam")
    _pasta(default_servings=4)
    plan_id = _plan_tonight()

    tools.approve_weekly_plan(plan_id, "Emily")

    bought = {g["item"]: g["quantity"] for g in tools.list_grocery_list()}
    assert bought["Olive oil"] == "1 bottle"
    assert bought["Baby spinach"] == "1 bag"


def test_the_saved_recipe_keeps_its_shopping_quantities():
    """cook_qty is added alongside qty, never over it."""
    _pasta()
    tools.save_cooking_quantities("Lemon Pasta", {"Olive oil": "3 tbsp"})

    saved = {i["item"]: i for i in tools.get_recipe("Lemon Pasta")["ingredients"]}
    assert saved["Olive oil"]["qty"] == "1 bottle"
    assert saved["Olive oil"]["cook_qty"] == "3 tbsp"


# ---------- the recipe fill: validate, repair once, then the table ----------

def _tool_block(name, tool_input):
    return types.SimpleNamespace(type="tool_use", name=name, input=tool_input, id="tu_1")


class _Usage:
    input_tokens = 10
    cache_read_input_tokens = 0
    cache_creation_input_tokens = 0
    output_tokens = 10


def _response(*blocks):
    return types.SimpleNamespace(content=list(blocks), stop_reason="tool_use", usage=_Usage())


class _FakeMessages:
    def __init__(self, responses):
        self._responses = list(responses)
        self.prompts: list[str] = []
        self.tools_seen: list[str] = []
        self.schemas: list[dict] = []

    def create(self, **kwargs):
        self.prompts.append(kwargs["messages"][0]["content"])
        self.tools_seen.append(kwargs["tools"][0]["name"])
        self.schemas.append(kwargs["tools"][0])
        return self._responses.pop(0)


def _stub(monkeypatch, *responses):
    fake = types.SimpleNamespace(messages=_FakeMessages(responses))
    monkeypatch.setattr(agent, "_client", lambda: fake)
    return fake.messages


def _detail(cooking_quantities):
    return _tool_block("submit_recipe_detail", {
        "instructions": [
            "Boil the spaghetti in salted water for 9 minutes, until just tender.",
            "Warm the olive oil over medium heat and wilt the spinach, about 2 minutes.",
            "Toss, salt to taste, and serve.",
        ],
        "cooking_quantities": cooking_quantities,
        "default_servings": 4, "prep_time_minutes": 5, "cook_time_minutes": 15,
        "advance_prep_notes": "", "advance_prep_step_indices": [],
    })


_GOOD_LINES = [
    {"item": "Olive oil", "cook_qty": "3 tbsp"},
    {"item": "Spaghetti", "cook_qty": "1 lb"},
    {"item": "Baby spinach", "cook_qty": "5 cups"},
    {"item": "Salt", "cook_qty": "to taste"},
]


def test_a_clean_fill_saves_the_models_measurements_and_asks_nothing_twice(monkeypatch):
    tools.add_recipe(
        "Lemon Pasta",
        ingredients=[
            {"item": "Olive oil", "qty": "1 bottle"},
            {"item": "Spaghetti", "qty": "1 box"},
            {"item": "Baby spinach", "qty": "1 bag"},
            {"item": "Salt", "qty": "to taste"},
        ],
        default_servings=4,
    )
    messages = _stub(monkeypatch, _response(_detail(_GOOD_LINES)))

    agent.fill_in_recipe("Lemon Pasta")

    assert len(messages.prompts) == 1, "nothing to repair, so no second call"
    saved = {i["item"]: i.get("cook_qty") for i in tools.get_recipe("Lemon Pasta")["ingredients"]}
    assert saved == {"Olive oil": "3 tbsp", "Spaghetti": "1 lb",
                     "Baby spinach": "5 cups", "Salt": "to taste"}


def test_a_package_unit_in_the_fill_triggers_one_repair_call(monkeypatch):
    tools.add_recipe(
        "Lemon Pasta",
        ingredients=[{"item": "Olive oil", "qty": "1 bottle"}, {"item": "Spaghetti", "qty": "1 box"}],
        default_servings=4,
    )
    messages = _stub(
        monkeypatch,
        _response(_detail([
            {"item": "Olive oil", "cook_qty": "1 bottle"},   # the reported bug, from the model
            {"item": "Spaghetti", "cook_qty": "1 lb"},       # fine, and must not be re-asked
        ])),
        _response(_tool_block("submit_cooking_quantities", {
            "cooking_quantities": [{"item": "Olive oil", "cook_qty": "1/4 cup"}],
        })),
    )

    agent.fill_in_recipe("Lemon Pasta")

    assert messages.tools_seen == ["submit_recipe_detail", "submit_cooking_quantities"]
    # Only the offending line is in the repair prompt — the instructions
    # from the first call are already good and aren't paid for twice.
    repair_prompt = messages.prompts[1]
    assert "Olive oil" in repair_prompt and "Spaghetti" not in repair_prompt
    saved = {i["item"]: i.get("cook_qty") for i in tools.get_recipe("Lemon Pasta")["ingredients"]}
    assert saved == {"Olive oil": "1/4 cup", "Spaghetti": "1 lb"}


def test_a_repair_that_comes_back_wrong_falls_back_to_the_table(monkeypatch):
    """
    One repair call, then the deterministic table — never a third round
    trip, and never a package word on the card.
    """
    tools.add_recipe(
        "Lemon Pasta", ingredients=[{"item": "Olive oil", "qty": "1 bottle"}], default_servings=4,
    )
    messages = _stub(
        monkeypatch,
        _response(_detail([{"item": "Olive oil", "cook_qty": "1 bottle"}])),
        _response(_tool_block("submit_cooking_quantities", {
            "cooking_quantities": [{"item": "Olive oil", "cook_qty": "1 large bottle"}],
        })),
    )

    agent.fill_in_recipe("Lemon Pasta")

    assert len(messages.prompts) == 2
    saved = tools.get_recipe("Lemon Pasta")["ingredients"][0]
    assert saved["cook_qty"] == "2 tbsp"
    assert saved["qty"] == "1 bottle", "the shopping amount is untouched"


def test_a_fill_that_omits_an_ingredient_entirely_still_gets_a_measurement(monkeypatch):
    tools.add_recipe(
        "Lemon Pasta",
        ingredients=[{"item": "Olive oil", "qty": "1 bottle"}, {"item": "Salt", "qty": ""}],
        default_servings=4,
    )
    _stub(
        monkeypatch,
        _response(_detail([{"item": "Olive oil", "cook_qty": "2 tbsp"}])),
        _response(_tool_block("submit_cooking_quantities", {"cooking_quantities": []})),
    )

    agent.fill_in_recipe("Lemon Pasta")

    saved = {i["item"]: i.get("cook_qty") for i in tools.get_recipe("Lemon Pasta")["ingredients"]}
    assert saved["Salt"] == "1 tsp"


def test_the_fill_prompt_always_asks_for_real_technique(monkeypatch):
    tools.add_recipe("Lemon Pasta", ingredients=[{"item": "Olive oil", "qty": "2 tbsp"}], default_servings=4)
    messages = _stub(monkeypatch, _response(_detail([])))

    agent.fill_in_recipe("Lemon Pasta")

    prompt = messages.prompts[0]
    assert "oven temperature" in prompt and "how long" in prompt and "doneness cue" in prompt


def test_the_prompt_names_only_the_ingredients_that_need_a_cooking_amount(monkeypatch):
    """
    Which lines need one is decided deterministically before the call, so
    the model is asked about "Olive oil" and not about the chicken that
    was already written as a weight. That is what keeps the added token
    cost proportional to the actual problem — see the 2026-09-08 entry in
    CLAUDE.md.
    """
    tools.add_recipe(
        "Lemon Pasta",
        ingredients=[{"item": "Chicken thighs", "qty": "2 lb"}, {"item": "Olive oil", "qty": "1 bottle"}],
        default_servings=4,
    )
    messages = _stub(monkeypatch, _response(_detail([{"item": "Olive oil", "cook_qty": "2 tbsp"}])))

    agent.fill_in_recipe("Lemon Pasta")

    ask = messages.prompts[0].split("The household cooks from this")[1]
    assert "Olive oil" in ask and "Chicken thighs" not in ask
    assert "SHOPPING amount" in ask
    assert "package word" in ask


def test_a_recipe_already_written_in_measurements_is_asked_nothing_extra(monkeypatch):
    """No cooking_quantities ask, and not even the schema property for it —
    a clean recipe pays nothing for a problem it doesn't have."""
    tools.add_recipe(
        "Lemon Pasta",
        ingredients=[{"item": "Chicken thighs", "qty": "2 lb"}, {"item": "Olive oil", "qty": "2 tbsp"}],
        default_servings=4,
    )
    messages = _stub(monkeypatch, _response(_detail([])))

    agent.fill_in_recipe("Lemon Pasta")

    assert "cooking_quantities" not in messages.prompts[0]
    assert "cooking_quantities" not in messages.schemas[0]["input_schema"]["properties"]


# ---------- steps vs ingredients ----------

def test_a_step_naming_something_nobody_bought_is_caught():
    result = tools.check_steps_ingredients_consistency(
        [{"item": "Spaghetti"}, {"item": "Olive oil"}],
        ["Boil the spaghetti.", "Stir the heavy cream into the olive oil."],
    )

    assert result["ok"] is False
    assert result["missing_from_list"] == ["heavy cream"]
    assert result["unused_ingredients"] == []


def test_an_ingredient_no_step_ever_uses_is_caught():
    result = tools.check_steps_ingredients_consistency(
        [{"item": "Spaghetti"}, {"item": "Baby spinach"}],
        ["Boil the spaghetti and serve."],
    )

    assert result["ok"] is False
    assert result["unused_ingredients"] == ["Baby spinach"]


def test_a_recipe_whose_steps_and_ingredients_agree_is_quiet():
    result = tools.check_steps_ingredients_consistency(
        [{"item": "Spaghetti"}, {"item": "Baby spinach"}, {"item": "Olive oil"}],
        ["Boil the spaghetti.", "Warm the olive oil and wilt the spinach.", "Toss together."],
    )

    assert result == {"ok": True, "unused_ingredients": [], "missing_from_list": []}


def test_an_unknown_food_word_in_a_step_is_not_guessed_at():
    """
    A false "you forgot to buy gochujang" is worse than a missed one, so
    only words the measurement table knows count as evidence.
    """
    result = tools.check_steps_ingredients_consistency(
        [{"item": "Spaghetti"}], ["Boil the spaghetti, then stir in the gochujang."],
    )
    assert result["ok"] is True


def test_the_plan_quality_check_logs_a_soft_note_for_a_mismatched_recipe(caplog):
    from app.tools import plan_quality

    entries = [{
        "date": "2026-09-08", "slot": "dinner", "slot_state": "planned",
        "meal_name": "Lemon Pasta", "reasoning": "using up the spinach",
        "food_groups": ["carb", "vegetable"], "is_new_recipe": False,
        "ingredients": [{"item": "Spaghetti", "category": "pantry"}],
        "instructions": ["Boil the spaghetti.", "Stir in the heavy cream."],
    }]

    violations = [v for v in plan_quality.check_week(entries, {}) if v.rule == "steps_match_ingredients"]

    assert len(violations) == 1
    assert violations[0].severity == "info", "a soft note, not a broken rule"
    assert "heavy cream" in violations[0].message


def test_a_recipe_with_no_saved_steps_is_not_flagged():
    from app.tools import plan_quality

    entries = [{
        "date": "2026-09-08", "slot": "dinner", "slot_state": "planned",
        "meal_name": "Takeout", "reasoning": "nobody is cooking",
        "food_groups": [], "is_new_recipe": False, "ingredients": [], "instructions": [],
    }]

    assert [v for v in plan_quality.check_week(entries, {}) if v.rule == "steps_match_ingredients"] == []
