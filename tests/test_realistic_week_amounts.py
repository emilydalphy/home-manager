"""
Realistic amounts for a week (Loop Board, High bug, 2026-10-19): "no 7
boxes of salt, 6 bunches of parsley or 6 heads of garlic for a week".

Tester Home, plan 72, two adults: Flaky salt "7 boxes" (one per orange
snack), Salt "1 container + 6 boxes", Black pepper "3 containers + 2 jars",
Fresh parsley 6 bunches (5 from one Mon–Wed batch), Garlic 6 heads, red
chili flakes beside red pepper flakes, salt beside sea salt.

Built in slices:
  1. a spice-rack thing is at most ONE package a week, whatever recipes
     wrote (grocery._rack_quantity), and the names that are one purchase
     merge (grocery._SAME_PURCHASE); "Paprika (sweet)" is a spice too.
  2. a batch doesn't multiply herbs, garlic and citrus past what one pot
     needs (recipes._aromatic_ceiling).
  3. store units: loaves, pints, tubs, bags; one store amount per item.

Every test says whether it is a CATCH (red before this branch) or a GUARD.
"""
from __future__ import annotations

import datetime

from conftest import household_today

from app import tools
from app.db import get_conn
from app.tools import grocery as _grocery
from app.tools import spices as _spices


def _monday():
    today = household_today()
    return today - datetime.timedelta(days=today.weekday()) + datetime.timedelta(days=7)


def _d(n):
    return (_monday() + datetime.timedelta(days=n)).isoformat()


def _two_adults():
    tools.add_member("Emily")
    tools.add_member("Jamie")


def _plan():
    return tools.create_weekly_plan(_monday().isoformat())["weekly_plan_id"]


def _lines(status=None):
    conn = get_conn()
    rows = conn.execute("SELECT item, quantity, status FROM grocery_items ORDER BY id").fetchall()
    conn.close()
    return {r["item"]: r["quantity"] for r in rows if status is None or r["status"] == status}


def _snack_week():
    """The card's shape: seven identical snacks, each a recipe whose
    shopping qty for flaky salt is the box."""
    _two_adults()
    tools.add_recipe("Orange Slices", default_servings=2, ingredients=[
        {"item": "Oranges", "qty": "2", "category": "produce"},
        {"item": "Flaky salt", "qty": "1 box", "category": "pantry"},
    ])
    plan_id = _plan()
    ids = [tools.plan_meal(_d(n), "Orange Slices", slot="snack", weekly_plan_id=plan_id)["entry_id"]
           for n in range(7)]
    return plan_id, ids


# ---------- slice 1: the spice rack is one package ----------

def test_seven_snacks_buy_one_box_of_flaky_salt_in_spices():
    """CATCH. Main: "7 boxes", one per snack."""
    plan_id, _ids = _snack_week()
    tools.approve_weekly_plan(plan_id, "Emily")
    assert _lines("spice") == {"Flaky salt": "1 box"}


def test_salt_kinds_and_flakes_are_one_line_and_one_package():
    """CATCH. Main: Salt "1 container + 1 box", Sea salt and Kosher salt on
    lines of their own, red chili flakes beside red pepper flakes."""
    _two_adults()
    tools.add_recipe("Shrimp", default_servings=2, ingredients=[
        {"item": "Salt", "qty": "1 container"},
        {"item": "Black pepper", "qty": "1 jar"},
        {"item": "Red chili flakes", "qty": "1 jar"},
    ])
    tools.add_recipe("Chicken", default_servings=2, ingredients=[
        {"item": "Kosher salt", "qty": "1 box"},
        {"item": "Freshly ground black pepper", "qty": "1 container"},
        {"item": "Red pepper flakes", "qty": "1 tsp"},
    ])
    tools.add_recipe("Salad", default_servings=2, ingredients=[
        {"item": "Sea salt", "qty": "1 tsp"},
        {"item": "Flaky sea salt", "qty": "a pinch"},
    ])
    plan_id = _plan()
    for n, name in enumerate(("Shrimp", "Chicken", "Salad", "Chicken", "Salad")):
        tools.plan_meal(_d(n), name, slot="dinner", weekly_plan_id=plan_id)
    tools.approve_weekly_plan(plan_id, "Emily")

    spices = _lines("spice")
    salt = [k for k in spices if _grocery._merge_key(k) == "salt"]
    assert len(salt) == 1 and spices[salt[0]] == "1 container"
    pepper = [k for k in spices if _grocery._merge_key(k) == "black pepper"]
    assert len(pepper) == 1 and spices[pepper[0]] == "1 jar"
    flakes = [k for k in spices if _grocery._merge_key(k) == "red pepper flake"]
    assert len(flakes) == 1 and spices[flakes[0]] == "1 jar"
    # Flaky salt is a finish, bought for the crunch: its own line, once.
    flaky = [k for k in spices if _grocery._merge_key(k) == "flaky salt"]
    assert len(flaky) == 1 and spices[flaky[0]] == "a pinch"


def test_taking_the_box_meal_off_leaves_what_the_others_measured():
    """CATCH (reversal). The ledger is re-read the way the merge built the
    line: the box goes with its meal, the teaspoons left read as teaspoons;
    the last meal off takes the line."""
    _two_adults()
    tools.add_recipe("Boxed", default_servings=2, ingredients=[{"item": "Salt", "qty": "1 box"}])
    tools.add_recipe("Measured", default_servings=2, ingredients=[{"item": "Sea salt", "qty": "1 tsp"}])
    plan_id = _plan()
    boxed = tools.plan_meal(_d(0), "Boxed", slot="dinner", weekly_plan_id=plan_id)["entry_id"]
    m1 = tools.plan_meal(_d(1), "Measured", slot="dinner", weekly_plan_id=plan_id)["entry_id"]
    m2 = tools.plan_meal(_d(2), "Measured", slot="dinner", weekly_plan_id=plan_id)["entry_id"]
    tools.approve_weekly_plan(plan_id, "Emily")
    assert list(_lines("spice").values()) == ["1 box"]

    _grocery._reverse_meal_grocery_contributions(boxed)
    assert list(_lines("spice").values()) == ["2 tsp"]
    _grocery._reverse_meal_grocery_contributions(m1)
    _grocery._reverse_meal_grocery_contributions(m2)
    assert _lines("spice") == {}


def test_a_measured_spice_still_adds_up():
    """GUARD. With no package anywhere, a week's teaspoons are still a sum."""
    _two_adults()
    tools.add_recipe("Curry", default_servings=2, ingredients=[{"item": "Cumin", "qty": "1 tsp"}])
    plan_id = _plan()
    for n in range(3):
        tools.plan_meal(_d(n), "Curry", slot="dinner", weekly_plan_id=plan_id)
    tools.approve_weekly_plan(plan_id, "Emily")
    assert _lines("spice") == {"Cumin": "1 tbsp"}


def test_a_box_of_pasta_is_still_one_per_dinner():
    """GUARD. The rack rule is asked of the NAME: pasta is eaten a box a
    dinner (quantities._PACKAGE_UNITS' note), so three dinners buy three."""
    _two_adults()
    tools.add_recipe("Pasta Night", default_servings=2, ingredients=[{"item": "Penne", "qty": "1 box"}])
    plan_id = _plan()
    for n in range(3):
        tools.plan_meal(_d(n), "Pasta Night", slot="dinner", weekly_plan_id=plan_id)
    tools.approve_weekly_plan(plan_id, "Emily")
    assert _lines() == {"Penne": "3 boxes"}


def test_a_households_own_salt_is_not_rewritten_by_a_measured_share():
    """GUARD. A person's "1 box" stays theirs; a plan's teaspoon beside it
    is not folded into it (nothing could take a folded share back off)."""
    _two_adults()
    tools.add_grocery_item("Salt", quantity="1 box", category="pantry")
    tools.add_recipe("Measured", default_servings=2, ingredients=[{"item": "Salt", "qty": "1 tsp"}])
    plan_id = _plan()
    tools.plan_meal(_d(0), "Measured", slot="dinner", weekly_plan_id=plan_id)
    tools.approve_weekly_plan(plan_id, "Emily")
    assert _lines("needed")["Salt"] == "1 box"


def test_paprika_with_a_note_is_a_spice():
    """CATCH. Plain Paprika sat in Spices while "Paprika (sweet)" went to the
    aisles; the same for a trailing ", optional" and "salt and pepper"."""
    for name in ("Paprika (sweet)", "Paprika, for garnish", "Red pepper flakes, optional",
                 "Salt and black pepper", "Basil (dried)"):
        assert _spices.is_spice(name), name
    for name in ("Bell pepper", "Fresh parsley, for garnish", "Basil (fresh)", "Bread and butter pickles",
                 "Garlic cloves", "Chicken (thighs)"):
        assert not _spices.is_spice(name), name


# ---------- slice 2: a batch doesn't multiply the aromatics ----------

def _batch_week():
    """The card's week: seven identical snacks and a Mon–Wed batch dinner
    (one cook feeding three nights for two)."""
    plan_id, _ids = _snack_week()
    tools.add_recipe("Garlic Shrimp", default_servings=2, ingredients=[
        {"item": "Shrimp", "qty": "1 lb", "category": "meat/seafood"},
        {"item": "Fresh parsley", "qty": "2 bunches", "category": "produce"},
        {"item": "Garlic", "qty": "8 cloves", "category": "produce"},
        {"item": "Lemons", "qty": "2", "category": "produce"},
        {"item": "Flaky salt", "qty": "1 box", "category": "pantry"},
    ])
    ids = [tools.plan_meal(_d(n), "Garlic Shrimp", slot="dinner", weekly_plan_id=plan_id)["entry_id"]
           for n in (0, 1, 2)]
    tools.set_cook_ahead(ids[0], ids[1:])
    tools.approve_weekly_plan(plan_id, "Emily")
    return plan_id, ids


def _amount(qty: str, unit: str) -> float:
    from app.tools import quantities
    parsed = quantities._parse_quantity(qty)
    assert parsed and parsed[1] == unit, qty
    return parsed[0]


def test_the_cards_week_buys_one_box_two_bunches_and_two_heads():
    """CATCH. The card's acceptance: flaky salt <= 1 box, parsley <= 2
    bunches, garlic <= 2 heads. Main: 8 boxes, 6 bunches, 3 heads."""
    _batch_week()
    lines = _lines()
    assert _amount(lines["Flaky salt"], "box") <= 1
    assert _amount(lines["Fresh parsley"], "bunch") <= 2
    assert _amount(lines["Garlic"], "head") <= 2
    # The thing the batch really is more of still scales: three nights of shrimp.
    assert lines["Shrimp"] == "3 lbs"
    assert lines["Lemons"] == "3"


def test_the_ceiling_never_takes_what_the_recipe_wrote():
    """GUARD. A pesto written for its own table with 3 bunches of basil
    buys 3; the ceiling only stops scaling past what one pot needs."""
    _two_adults()
    tools.add_recipe("Pesto", default_servings=2, ingredients=[
        {"item": "Basil", "qty": "3 bunches", "category": "produce"},
    ])
    plan_id = _plan()
    tools.plan_meal(_d(0), "Pesto", slot="dinner", weekly_plan_id=plan_id)
    tools.approve_weekly_plan(plan_id, "Emily")
    assert _lines()["Basil"] == "3 bunches"


def test_dropping_the_batch_takes_its_capped_share_off():
    """GUARD (ledger). The capped share is the meal's real, unrounded share:
    taking the cook night off takes the whole line with it."""
    _plan_id, ids = _batch_week()
    _grocery._reverse_meal_grocery_contributions(ids[0])
    lines = _lines()
    assert "Fresh parsley" not in lines and "Garlic" not in lines and "Lemons" not in lines
