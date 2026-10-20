"""
The Cook screen's servings stepper (/api/recipes/scale -> scale_recipe)
lost what a recipe wrote beside its amounts (defect hunt, 2026-10-20):

  - "1/3 cup grated" halved came back "0.166667 cup" — "grated" gone, and
    a sixth of a cup the screen could only round to "⅛ cup";
  - "1 litre" stock read as no unit at all, so the cook card put the
    table's guess in its place ("1.5 lb" chicken stock);
  - "1/4 bunch" came back "1 bunch" at every size, the recipe's own size
    included, because a bunch "only comes whole";
  - "1 cup packed" brown sugar read "cup packed" as the unit and was
    replaced by the table's guess the same way.
"""
from app import tools
from app.tools import recipes


def _bake(**over):
    ings = [
        {"item": "parmesan", "qty": "1/3 cup grated"},
        {"item": "chicken stock", "qty": "1 litre"},
        {"item": "cilantro", "qty": "1/4 bunch"},
        {"item": "brown sugar", "qty": "1 cup packed"},
        {"item": "onion", "qty": "1 small"},
        {"item": "garlic", "qty": "2 cloves"},
    ]
    tools.add_recipe("Test Bake", ingredients=ings, instructions=["Mix."], default_servings=4, **over)


def _qtys(result):
    return {i["item"]: i["qty"] for i in result["scaled_ingredients"]}


def test_a_halved_third_of_a_cup_keeps_its_word_and_reads_in_spoons(signed_in):
    _bake()
    res = signed_in.get("/api/recipes/scale", params={"name": "Test Bake", "servings": 2})
    assert res.status_code == 200
    shown = _qtys(res.json())
    assert shown["parmesan"] == "2 tbsp + 2 tsp grated"   # the easiest measure (2026-10-20)
    assert shown["onion"] == "0.5 small"
    assert shown["brown sugar"] == "0.5 cup packed"


def test_the_recipes_own_size_says_what_the_recipe_says():
    _bake()
    shown = _qtys(tools.scale_recipe("Test Bake", 4))
    assert shown["parmesan"] == "0.333333 cup grated"
    assert shown["chicken stock"] == "1 L"
    assert shown["cilantro"] == "0.25 bunch"
    assert shown["brown sugar"] == "1 cup packed"
    assert shown["onion"] == "1 small"


def test_a_litre_scales_as_a_litre_and_a_part_bunch_as_a_part():
    _bake()
    double = _qtys(tools.scale_recipe("Test Bake", 8))
    assert double["chicken stock"] == "2 L"
    assert double["cilantro"] == "0.5 bunch"
    assert double["parmesan"] == "0.666667 cup grated"
    # A whole clove still stays whole (the old rule, kept).
    assert double["garlic"] == "4 cloves"
    single = _qtys(tools.scale_recipe("Test Bake", 1))
    assert single["cilantro"] == "0.125 bunch"   # never less than an eighth
    assert single["garlic"] == "1 clove"


def test_a_step_halving_a_third_of_a_cup_agrees_with_the_chip():
    tools.add_recipe(
        "Test Gratin",
        ingredients=[{"item": "parmesan", "qty": "1/3 cup grated"}],
        instructions=["Sprinkle 1/3 cup parmesan over the top.", "Add 1/4 bunch cilantro."],
        default_servings=4,
    )
    steps = tools.scale_recipe("Test Gratin", 2)["scaled_instructions"]
    assert steps[0] == "Sprinkle 2 tbsp + 2 tsp parmesan over the top."
    assert steps[1] == "Add ⅛ bunch cilantro."


def test_the_recipes_own_eighth_cup_is_not_turned_into_spoons():
    tools.add_recipe("Test Dressing", ingredients=[{"item": "olive oil", "qty": "1/8 cup"}], default_servings=4)
    assert _qtys(tools.scale_recipe("Test Dressing", 4))["olive oil"] == "0.125 cup"
    assert _qtys(tools.scale_recipe("Test Dressing", 2))["olive oil"] == "1 tbsp"


def test_a_sized_package_is_still_a_package_not_a_measure():
    """'8 oz package' names the package to buy; scaling must not turn it
    into '4 oz package'."""
    assert recipes._measure_before_a_word((8.0, "oz package")) == (8.0, "oz package")
    assert recipes._measure_before_a_word((1.0, "cup packed")) == (1.0, "cup")
