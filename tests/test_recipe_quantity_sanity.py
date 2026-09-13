"""
Recipe quantities pass a sanity check (no stick of butter in a 2-serving
soup).

Emily, 2026-09-13, on her phone, Turkish-Style Lentil Soup, Serves 2:
"½ cups Red lentils · 1 Onion · 1 lb Carrots · 1 tbsp Tomato paste · ½ tsp
Cumin · ½ tsp Paprika · 2 cups Vegetable broth · 1 stick Butter · 1 Lemon ·
1 bunch Mint (fresh)". Her words: "One stick of butter is a crazy amount
for this whole recipe. how can we make sure it makes better judgement
calls on this."

Where it came from, reproduced on main with exactly this recipe saved for
two: every qty is the SHOPPING line the generation prompt asks for (butter
is bought by the stick), the cook view keeps a shopping qty whenever it
measures something, and "stick" is a real kitchen unit — so it went
straight through, and scale_recipe kept it whole because a stick is
discrete. "½ cups" is _format_quantity pluralising everything but exactly
one. Nothing anywhere asked whether the amount made sense for the number
of people. Now recipes._PLAUSIBLE_PER_SERVING does, deterministically,
before a recipe is saved and again wherever a cook reads an amount.
"""
from __future__ import annotations

import datetime

import pytest

from app import tools
from app.tools import plan_quality, quantities, recipes
from app.tools.plan_quality import check_week


# Exactly what the model wrote, as it would have been saved for a table of
# two (the prompt tells it to write for the household's own table).
LENTIL_SOUP = [
    {"item": "Red lentils", "qty": "1/2 cup", "category": "pantry"},
    {"item": "Onion", "qty": "1", "category": "produce"},
    {"item": "Carrots", "qty": "1 lb", "category": "produce"},
    {"item": "Tomato paste", "qty": "1 tbsp", "category": "pantry"},
    {"item": "Cumin", "qty": "1/2 tsp", "category": "pantry"},
    {"item": "Paprika", "qty": "1/2 tsp", "category": "pantry"},
    {"item": "Vegetable broth", "qty": "2 cups", "category": "pantry"},
    {"item": "Butter", "qty": "1 stick", "category": "dairy"},
    {"item": "Lemon", "qty": "1", "category": "produce"},
    {"item": "Mint (fresh)", "qty": "1 bunch", "category": "produce"},
]


def _soup(default_servings=2, name="Turkish-Style Lentil Soup"):
    tools.add_recipe(
        name, ingredients=[dict(i) for i in LENTIL_SOUP],
        instructions=["Melt the butter, soften the onion and carrots.", "Add the rest and simmer."],
        default_servings=default_servings,
    )
    return name


def _qtys(ingredients) -> dict:
    return {i["item"]: i["qty"] for i in ingredients}


def _in_range(item, qty, servings) -> bool:
    return recipes.implausible_quantity(item, qty, servings) is None


# ---------- the case Emily reported ----------

def test_the_lentil_soup_for_two_no_longer_calls_for_a_stick_of_butter():
    """The exact screen: the Serves stepper at 2 on a recipe written for 2."""
    _soup(default_servings=2)

    shown = _qtys(tools.scale_recipe("Turkish-Style Lentil Soup", 2)["scaled_ingredients"])

    assert shown["Butter"] == "1 tbsp"
    # ...and the unit reads the way a person writes it, not "½ cups".
    assert shown["Red lentils"] == "0.5 cup"
    # Everything else on the card was fine and is untouched.
    assert shown["Tomato paste"] == "1 tbsp"
    assert shown["Vegetable broth"] == "2 cups"
    assert shown["Onion"] == "1"


def test_every_line_of_the_soup_is_in_range_for_two():
    _soup(default_servings=2)
    for item, qty in _qtys(tools.scale_recipe("Turkish-Style Lentil Soup", 2)["scaled_ingredients"]).items():
        assert _in_range(item, qty, 2), (item, qty)


def test_the_cook_screen_itself_shows_a_tablespoon():
    """
    End to end through get_cooker_view, the way the phone loaded it: a
    household of two, tonight's dinner, no stepper touched.
    """
    tools.add_member("Emily")
    tools.add_member("Sam")
    _soup(default_servings=2)
    today = datetime.date.today()
    monday = today - datetime.timedelta(days=today.weekday())
    plan_id = tools.create_weekly_plan(monday.isoformat())["weekly_plan_id"]
    tools.plan_meal(today.isoformat(), "Turkish-Style Lentil Soup", slot="dinner", weekly_plan_id=plan_id)

    card = tools.get_cooker_view(plan_id)["meals"][0]

    assert card["default_servings"] == 2
    shown = _qtys(card["ingredients"])
    assert shown["Butter"] == "1 tbsp"
    assert shown["Red lentils"] == "0.5 cup"
    assert "stick" not in " ".join(shown.values())


def test_the_grocery_list_still_buys_one_stick():
    """The shopping line is never the thing being corrected."""
    tools.add_member("Emily")
    tools.add_member("Sam")
    _soup(default_servings=2)
    today = datetime.date.today()
    monday = today - datetime.timedelta(days=today.weekday())
    plan_id = tools.create_weekly_plan(monday.isoformat())["weekly_plan_id"]
    tools.plan_meal(today.isoformat(), "Turkish-Style Lentil Soup", slot="dinner", weekly_plan_id=plan_id)

    tools.approve_weekly_plan(plan_id, "Emily")

    bought = {g["item"]: g["quantity"] for g in tools.list_grocery_list()}
    assert bought["Butter"] == "1 stick"
    assert bought["Carrots"] == "1 lb"


# ---------- before a recipe is saved ----------

def test_add_recipe_writes_a_cooking_amount_for_the_line_that_was_out_of_range():
    _soup(default_servings=2)

    saved = {i["item"]: i for i in tools.get_recipe("Turkish-Style Lentil Soup")["ingredients"]}

    assert saved["Butter"]["qty"] == "1 stick", "the shopping qty is stored exactly as given"
    assert saved["Butter"]["cook_qty"] == "1 tbsp"
    # A line that was fine gets no cook_qty it didn't have.
    assert "cook_qty" not in saved["Cumin"]
    assert "cook_qty" not in saved["Vegetable broth"]


def test_a_stick_for_four_is_saved_as_written():
    """
    Per serving, not per recipe: a stick across four plates is 2 tbsp a
    head, the top of the fat range, and a real thing recipes say.
    """
    _soup(default_servings=4)
    saved = {i["item"]: i for i in tools.get_recipe("Turkish-Style Lentil Soup")["ingredients"]}
    assert "cook_qty" not in saved["Butter"]


def test_the_fill_paths_saved_measurement_is_held_to_the_same_range():
    """
    save_cooking_quantities is where the model's own cook_qty lands. A
    model that answers "1/2 cup butter" for two is corrected the same way.
    """
    _soup(default_servings=2)
    tools.save_cooking_quantities("Turkish-Style Lentil Soup", {"Butter": "1/2 cup", "Cumin": "3/4 tsp"})

    saved = {i["item"]: i for i in tools.get_recipe("Turkish-Style Lentil Soup")["ingredients"]}
    assert saved["Butter"]["cook_qty"] == "1 tbsp"
    assert saved["Cumin"]["cook_qty"] == "3/4 tsp", "a sensible measurement is kept as the model wrote it"


def test_settle_logs_what_it_changed(caplog):
    with caplog.at_level("INFO", logger="home_manager"):
        recipes.settle_cooking_quantities([dict(i) for i in LENTIL_SOUP], 2)
    assert any("Butter '1 stick'" in r.getMessage() and "1 tbsp" in r.getMessage() for r in caplog.records)


def test_settle_corrects_each_line_on_its_own_when_two_share_a_name():
    """Butter used in two steps: the line that was over is fixed, the one
    that was fine is left exactly as it was (found on review)."""
    settled = recipes.settle_cooking_quantities(
        [{"item": "Butter", "qty": "1 cup"}, {"item": "Butter", "qty": "1 tsp"}], 2,
    )
    assert settled[0]["cook_qty"] == "1 tbsp"
    assert "cook_qty" not in settled[1]


def test_settle_returns_the_same_list_when_nothing_is_wrong():
    fine = [{"item": "Olive oil", "qty": "2 tbsp"}, {"item": "Salt", "qty": "1 tsp"}]
    assert recipes.settle_cooking_quantities(fine, 4) is fine


# ---------- the validator, with and without a table ----------

def test_the_validator_flags_the_stick_only_when_told_the_servings():
    """
    Without `servings` the validator judges units alone, exactly as
    before — the fill path relies on that, so an amount the table can
    settle for free never turns into a repair call.
    """
    line = [{"item": "Butter", "qty": "1 stick"}]
    assert tools.validate_measured_quantities(line)["ok"] is True

    result = tools.validate_measured_quantities(line, servings=2)
    assert result["ok"] is False
    assert result["problems"] == [
        {"item": "Butter", "qty": "1 stick", "reason": "implausible", "suggested": "1 tbsp"},
    ]


def test_a_stored_cook_qty_out_of_range_is_not_trusted_on_the_cook_screen():
    """Recipes filled in before today are covered at read time too."""
    cooking = tools.cooking_ingredients(
        [{"item": "Butter", "qty": "1 stick", "cook_qty": "1 stick"}], servings=2,
    )
    assert cooking[0]["qty"] == "1 tbsp"
    assert cooking[0]["shopping_qty"] == "1 stick"


def test_no_servings_means_the_documented_default_of_four():
    assert tools.cooking_ingredients([{"item": "Butter", "qty": "1 stick"}])[0]["qty"] == "1 stick"
    assert tools.cooking_ingredients([{"item": "Butter", "qty": "2 sticks"}])[0]["qty"] == "2 tbsp"


# ---------- scaling stays in range, and a stick becomes tablespoons ----------

@pytest.mark.parametrize("base, target", [(4, 2), (2, 6), (4, 6), (2, 4)])
def test_scaling_keeps_every_line_in_range(base, target):
    _soup(default_servings=base)
    scaled = tools.scale_recipe("Turkish-Style Lentil Soup", target)
    for item, qty in _qtys(scaled["scaled_ingredients"]).items():
        assert _in_range(item, qty, target), (base, target, item, qty)
    assert scaled["unscaled_items"] == []


def test_a_stick_scaled_to_a_fraction_is_written_in_tablespoons():
    """
    Butter for four (a stick, at the top of the range) halved for two is
    4 tbsp — not rounded back up to a whole stick, which is how a
    four-person stick used to stay a stick for two.
    """
    _soup(default_servings=4)
    assert _qtys(tools.scale_recipe("Turkish-Style Lentil Soup", 2)["scaled_ingredients"])["Butter"] == "4 tbsp"
    assert _qtys(tools.scale_recipe("Turkish-Style Lentil Soup", 6)["scaled_ingredients"])["Butter"] == "12 tbsp"
    # A whole number of sticks is still sticks.
    assert _qtys(tools.scale_recipe("Turkish-Style Lentil Soup", 8)["scaled_ingredients"])["Butter"] == "2 sticks"


# ---------- units read naturally ----------

@pytest.mark.parametrize("amount, unit, expected", [
    (0.5, "cup", "0.5 cup"),
    (0.75, "cup", "0.75 cup"),
    (1, "cup", "1 cup"),
    (1.5, "cup", "1.5 cups"),
    (2, "cup", "2 cups"),
    (0.5, "lb", "0.5 lb"),
    (2, "lb", "2 lbs"),
    (0.5, "head", "0.5 head"),
    (2, "bunch", "2 bunches"),
    (0.5, "tsp", "0.5 tsp"),
    (0.5, "tub (48 oz)", "0.5 tub (48 oz)"),
])
def test_one_or_less_is_singular(amount, unit, expected):
    """shell.js then renders the 0.5 as ½, so this is what reads "½ cup"."""
    assert quantities._format_quantity(amount, unit) == expected


def test_a_singular_fraction_parses_back_to_the_same_amount():
    assert quantities._parse_quantity("0.5 cup") == (0.5, "cup")
    assert quantities._parse_quantity("0.5 lb") == (0.5, "lb")
    assert quantities._parse_quantity("0.5 cups") == (0.5, "cup")


# ---------- the ranges themselves ----------

def test_the_apps_own_table_passes_its_own_check():
    """COOKING_QUANTITIES_PER_4 is what an out-of-range line is replaced
    with, so every entry has to be in range at four."""
    for item, qty in recipes.COOKING_QUANTITIES_PER_4.items():
        assert _in_range(item, qty, 4), (item, qty)


@pytest.mark.parametrize("item, qty, servings", [
    # Real recipes, generous by design.
    ("Olive oil", "1/4 cup", 2),
    ("Butter", "1 stick", 4),
    ("Salt", "1 tbsp", 4),          # salted pasta water
    ("Sugar", "1 cup", 4),
    ("Chili powder", "2 tbsp", 4),
    ("Garlic", "8 cloves", 2),
    ("Onion", "2", 1),
    ("Chicken thighs", "2 lb", 4),
    ("Chicken thighs", "1 lb", 1),  # bone-in
    ("Rice", "1 cup", 1),
    ("Pasta", "1 lb", 2),
    ("Bacon", "2 oz", 4),           # a flavouring, not a portion — no floor
    # Names that borrow a class word and must not be judged by it.
    ("Low-fat yogurt", "2 cups", 2),
    ("Sugar snap peas", "1 lb", 2),
    ("Green beans", "1 lb", 2),
    ("Fresh thyme", "2 tbsp", 2),
    ("Chili oil", "2 tbsp", 2),
    ("Cinnamon stick", "1", 2),
    ("Fish sauce", "2 tbsp", 2),    # a protein word on a sauce: no weight, not judged
    ("Garlic knots", "12", 2),      # a counted dish wearing an aromatic's name
    ("Onion rings", "24", 4),
    ("Ginger snaps", "20", 4),
    # Whole things the table has no opinion about.
    ("Mint (fresh)", "1 bunch", 2),
    ("Garlic", "1 head", 2),
    ("Diced tomatoes", "1 can (14 oz)", 2),
    ("Carrots", "1 lb", 2),
])
def test_amounts_a_cook_would_actually_write_pass(item, qty, servings):
    assert _in_range(item, qty, servings), (item, qty, servings)


@pytest.mark.parametrize("item, qty, servings, shown", [
    ("Butter", "1 stick", 2, "1 tbsp"),
    ("Salted butter", "1/2 cup", 2, "1 tbsp"),
    ("Butter", "250 g", 4, "2 tbsp"),
    ("Olive oil", "1 cup", 2, "1 tbsp"),
    ("Salt", "2 tbsp", 2, "0.5 tsp"),
    ("Cumin", "3 tbsp", 2, "0.5 tsp"),
    ("Honey", "1 cup", 2, "0.5 tbsp"),
    ("Chicken thighs", "3 lb", 2, "0.75 lb"),
    ("Garlic", "12 cloves", 2, "2 cloves"),
    ("Red lentils", "6 cups", 2, "0.5 cup"),
])
def test_amounts_out_of_range_show_the_apps_own_figure_for_that_table(item, qty, servings, shown):
    assert not _in_range(item, qty, servings)
    assert tools.plausible_cooking_quantity(item, qty, servings) == shown


def test_an_item_the_table_has_never_met_is_capped_at_the_range_in_its_own_unit():
    """No table entry for duck fat: the fat ceiling (2 tbsp a head) for
    two, written back in the cup the line came in."""
    assert tools.plausible_cooking_quantity("Duck fat", "1 cup", 2) == "0.25 cup"
    assert tools.plausible_cooking_quantity("Duck fat", "2 tbsp", 2) == "2 tbsp"


def test_implausible_quantity_says_what_it_measured():
    problem = tools.implausible_quantity("Butter", "1 stick", 2)
    assert problem["class"] == "fat"
    assert problem["family"] == "tsp"
    assert problem["per_serving"] == pytest.approx(12)  # 8 tbsp = 24 tsp, over 2
    assert problem["high"] == 6


# ---------- the flag: plan quality ----------

def _entry(**overrides):
    base = {
        "date": "2026-09-07", "slot": "dinner", "slot_state": "planned",
        "meal_name": "Turkish-Style Lentil Soup",
        "reasoning": "You asked for something warm.", "food_groups": ["protein", "vegetable"],
        "main_protein": None, "prep_time_minutes": None, "cook_time_minutes": None,
        "is_new_recipe": True, "links_to": None,
        "ingredients": [dict(i) for i in LENTIL_SOUP], "instructions": [], "default_servings": 2,
    }
    base.update(overrides)
    return base


def test_plan_quality_flags_the_stick_as_info_and_says_what_the_cook_sees():
    violations = [v for v in check_week([_entry()], {}) if v.rule == "quantities_plausible"]
    assert len(violations) == 1
    v = violations[0]
    assert v.severity == "info"
    assert v.date == "2026-09-07" and v.slot == "dinner"
    assert v.message == (
        "Turkish-Style Lentil Soup: Butter '1 stick' is more than 2 would use — the cook view shows 1 tbsp."
    )


def test_plan_quality_is_quiet_when_the_amounts_fit_the_table():
    assert not [v for v in check_week([_entry(default_servings=4)], {}) if v.rule == "quantities_plausible"]


def test_plan_quality_reports_the_line_as_written_not_the_correction():
    """A recipe saved today already carries the corrected cook_qty; the
    flag is about what the model wrote, and says what the cook sees."""
    saved = [dict(i, cook_qty="1 tbsp") if i["item"] == "Butter" else dict(i) for i in LENTIL_SOUP]
    violations = [v for v in check_week([_entry(ingredients=saved)], {}) if v.rule == "quantities_plausible"]
    assert [v.message for v in violations] == [
        "Turkish-Style Lentil Soup: Butter '1 stick' is more than 2 would use — the cook view shows 1 tbsp."
    ]


def test_plan_quality_ignores_an_empty_or_unplanned_slot():
    entries = [_entry(slot_state="planned_empty"), _entry(ingredients=[])]
    assert not [v for v in check_week(entries, {}) if v.rule == "quantities_plausible"]


def test_plan_quality_reads_the_recipes_servings_from_the_database():
    """_load_plan_entries carries default_servings, or the rule would judge
    a two-person recipe as if it were for four and stay quiet."""
    _soup(default_servings=2)
    plan_id = tools.create_weekly_plan("2026-09-07")["weekly_plan_id"]
    tools.plan_meal("2026-09-07", "Turkish-Style Lentil Soup", slot="dinner", weekly_plan_id=plan_id)

    entries = plan_quality._load_plan_entries(plan_id)

    assert entries[0]["default_servings"] == 2
    assert [v.rule for v in check_week(entries, {}) if v.rule == "quantities_plausible"] == ["quantities_plausible"]
