"""
Two calls Emily made on 2026-10-20.

1. EASIEST MEASURE. The servings stepper said half of ⅓ cup as "2 ⅔ tbsp"
   — there is no ⅔-tablespoon spoon. "Keep whichever is more intuitive and
   will result in less fractions so it's easier for the user to measure
   it." A scaled cup/tbsp/tsp amount is now said in the measures a home
   cook owns, at most two of them, fewest scoops first, never short by
   more than 5% (recipes._easiest_volume). The recipe at its own size is
   exactly as written; the steps and the list say the same thing.

2. CAPITAL L. "½ l chicken stock" reads like "½ 1". A litre is written "L"
   everywhere it is shown; l / L / litre / liter all still parse.
"""
from __future__ import annotations

import json
import shutil

import pytest

import nodeharness
from app import tools
from app.tools import quantities, recipes
from test_human_qty_and_time_formatting import SHELL_JS, _extract, _var


def _qtys(result):
    return {i["item"]: i["qty"] for i in result["scaled_ingredients"]}


# --- 1. the easiest measure -------------------------------------------------

@pytest.mark.parametrize("tsp, expected", [
    (8.0, [(2.0, "tbsp"), (2.0, "tsp")]),     # half of ⅓ cup
    (12.0, [(0.25, "cup")]),                   # half of ½ cup: one scoop, not 4 tbsp
    (6.0, [(2.0, "tbsp")]),                    # ⅔ of 3 tbsp
    (1.5, [(1.5, "tsp")]),                     # half of 1 tbsp
    (54.0, [(1.0, "cup"), (2.0, "tbsp")]),     # 1½ x ¾ cup — no ⅛ cup measure
    (18.0, [(0.25, "cup"), (2.0, "tbsp")]),
    (96.0, [(2.0, "cup")]),
])
def test_the_easiest_measure(tsp, expected):
    got = recipes._easiest_volume(tsp)
    assert [(round(n, 4), u) for n, u in got] == [(round(n, 4), u) for n, u in expected]


def test_never_short_by_more_than_five_percent_and_at_most_two_measures():
    for eighths in range(2, 48 * 8 * 3):        # ¼ tsp .. 3 cups, in ⅛ tsp steps
        tsp = eighths / 8
        terms = recipes._easiest_volume(tsp)
        assert 1 <= len(terms) <= 2
        total = sum(n * recipes._VOLUME_TSP[u] for n, u in terms)
        assert total >= tsp * 0.95 - 1e-9, (tsp, terms)


def test_the_stepper_says_the_easiest_measure():
    tools.add_recipe(
        "Test Measures",
        ingredients=[
            {"item": "parmesan", "qty": "1/3 cup grated"},
            {"item": "milk", "qty": "1/2 cup"},
            {"item": "olive oil", "qty": "3 tbsp"},
            {"item": "soy sauce", "qty": "1 tbsp"},
        ],
        instructions=["Sprinkle 1/3 cup parmesan over the top.", "Add 1 tbsp soy sauce."],
        default_servings=6,
    )
    half = tools.scale_recipe("Test Measures", 3)
    shown = _qtys(half)
    assert shown["parmesan"] == "2 tbsp + 2 tsp grated"
    assert shown["milk"] == "0.25 cup"
    assert shown["soy sauce"] == "1.5 tsp"
    assert half["scaled_instructions"] == [
        "Sprinkle 2 tbsp + 2 tsp parmesan over the top.", "Add 1 ½ tsp soy sauce.",
    ]
    assert _qtys(tools.scale_recipe("Test Measures", 4))["olive oil"] == "2 tbsp"


def test_the_recipes_own_size_is_as_written():
    tools.add_recipe(
        "Test Own Size",
        ingredients=[{"item": "parmesan", "qty": "1/3 cup grated"}, {"item": "oil", "qty": "4 tbsp"}],
        instructions=["Add 4 tbsp oil."],
        default_servings=4,
    )
    own = tools.scale_recipe("Test Own Size", 4)
    assert _qtys(own) == {"parmesan": "0.333333 cup grated", "oil": "4 tbsp"}
    assert own["scaled_instructions"] == ["Add 4 tbsp oil."]


def test_a_two_measure_amount_scales_as_one():
    tools.add_recipe(
        "Test Plus",
        ingredients=[{"item": "flour", "qty": "1/4 cup + 2 tbsp"}],
        instructions=["Whisk in 1/4 cup + 2 tbsp flour."],
        default_servings=4,
    )
    half = tools.scale_recipe("Test Plus", 2)
    assert _qtys(half)["flour"] == "3 tbsp"
    assert half["scaled_instructions"] == ["Whisk in 3 tbsp flour."]


def test_butter_keeps_its_tablespoons():
    tools.add_recipe(
        "Test Butter",
        ingredients=[{"item": "butter", "qty": "2 tbsp"}],
        instructions=["Melt 2 tbsp butter."],
        default_servings=2,
    )
    triple = tools.scale_recipe("Test Butter", 6)
    assert _qtys(triple)["butter"] == "6 tbsp"
    assert triple["scaled_instructions"] == ["Melt 6 tbsp butter."]


# --- 2. capital L -----------------------------------------------------------

def test_a_litre_is_written_capital_l():
    assert quantities._format_quantity(3, "l") == "3 L"
    assert quantities._format_quantity(0.5, "l") == "0.5 L"
    assert quantities._format_quantity(500, "ml") == "500 ml"


@pytest.mark.parametrize("text", ["2 l", "2 L", "2 litres", "2 liter", "2 Litre"])
def test_every_litre_spelling_still_parses(text):
    assert quantities._parse_quantity(text) == (2.0, "l")


def test_scaled_litres_say_l_on_the_list_and_in_the_steps():
    tools.add_recipe(
        "Test Soup",
        ingredients=[{"item": "chicken stock", "qty": "1 litre"}],
        instructions=["Pour in 1 l stock."],
        default_servings=4,
    )
    half = tools.scale_recipe("Test Soup", 2)
    assert _qtys(half)["chicken stock"] == "0.5 L"
    assert half["scaled_instructions"] == ["Pour in ½ L stock."]


def test_the_grocery_line_says_l():
    tools.add_grocery_item("Chicken stock", quantity="1 l")
    tools.add_grocery_item("Chicken stock", quantity="500 ml")
    rows = tools.list_grocery_list()
    qty = next(r["quantity"] for r in rows if r["item"].lower() == "chicken stock")
    assert qty == "1.5 L"


_needs_node = pytest.mark.skipif(shutil.which("node") is None, reason="node runs shell.js's formatter")


@_needs_node
def test_shell_draws_l_and_both_measures():
    harness = (
        _var("HUMAN_QTY_FRACTIONS") + "\n" + _extract("humanQtyAmount") + "\n" + _extract("humanQtyText") + "\n"
    )
    cases = ["0.5 l", "½ l", "3 l", "1 1/2 l", "500 ml", "2 large", "1 lb",
             "2 tbsp + 2 tsp grated", "0.25 cup + 1.5 tsp"]
    script = harness + "console.log(JSON.stringify(" + json.dumps(cases) + ".map(humanQtyText)));\n"
    res = nodeharness.run_node(script, timeout=30)
    assert res.returncode == 0, res.stderr
    assert json.loads(res.stdout.strip()) == [
        "½ L", "½ L", "3 L", "1 1/2 L", "500 ml", "2 large", "1 lb",
        "2 tbsp + 2 tsp grated", "¼ cup + 1\u2009½ tsp",
    ]


def test_every_grocery_quantity_render_capitalises_a_litre():
    """The grocery list draws the server's string as is — each of its
    quantity spans runs the litre regex (rows stored before today say "l")."""
    plain = [
        "escapeHtml(c.quantity) +", "escapeHtml(it.quantity) +", "escapeHtml(sp.total || sp.quantity)",
        "escapeHtml(it.quantity) :",
    ]
    for site in plain:
        assert site not in SHELL_JS, site
