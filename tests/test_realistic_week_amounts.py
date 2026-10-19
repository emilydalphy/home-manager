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
  2. a batch doesn't multiply herbs and garlic past what one pot needs
     (recipes._aromatic_ceiling) — only the batch part is capped.
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
                 "Garlic cloves", "Chicken (thighs)",
                 # review: brackets that name a different thing, or "fresh"
                 "Oregano (fresh)", "Bay leaves (fresh)", "Pepper (bell)", "Cloves (garlic)"):
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
    # Citrus is not capped: a batch of three nights squeezes three times.
    assert lines["Lemons"] == "6"


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
    assert "Fresh parsley" not in lines and "Garlic" not in lines

# ---------- slice 3: store units, one amount per item ----------

def _breakfast_week():
    _two_adults()
    tools.add_recipe("Yogurt Bowl", default_servings=2, ingredients=[
        {"item": "Greek yogurt", "qty": "1 cup", "category": "dairy"},
        {"item": "Granola", "qty": "0.5 cup", "category": "pantry"},
        {"item": "Blueberries", "qty": "1 cup", "category": "produce"},
    ])
    tools.add_recipe("Toast", default_servings=2, ingredients=[
        {"item": "Multigrain bread", "qty": "2 slices", "category": "pantry"},
        {"item": "Cherry tomatoes", "qty": "1 cup", "category": "produce"},
    ])
    tools.add_recipe("Other Toast", default_servings=2, ingredients=[
        {"item": "Whole grain bread", "qty": "2 slices", "category": "pantry"},
    ])
    plan_id = _plan()
    bowls = [tools.plan_meal(_d(n), "Yogurt Bowl", slot="breakfast", weekly_plan_id=plan_id)["entry_id"]
             for n in range(5)]
    for n in range(4):
        tools.plan_meal(_d(n), "Toast", slot="lunch", weekly_plan_id=plan_id)
    tools.plan_meal(_d(4), "Other Toast", slot="lunch", weekly_plan_id=plan_id)
    tools.approve_weekly_plan(plan_id, "Emily")
    return plan_id, bowls


def test_a_week_of_breakfasts_reads_in_store_units():
    """CATCH. Main: Greek yogurt "5 cups", Granola "2 1/2 cups", Blueberries
    "5 cups", Cherry tomatoes "4 cups", and the two breads as "8 slices" and
    "2 slices" on two lines."""
    _breakfast_week()
    lines = _lines()
    assert lines["Greek yogurt"] == "2 tubs (750 g)"
    assert lines["Granola"] == "1 bag"
    assert lines["Blueberries"] == "3 pints"
    assert lines["Cherry tomatoes"] == "2 pints"
    bread = {k: v for k, v in lines.items() if k.endswith("bread")}
    assert list(bread.values()) == ["1 loaf"]


def test_dropping_breakfasts_rereads_the_ledger_in_store_units():
    """CATCH (ledger). The ledger keeps each morning's unrounded cup; the
    line is re-read from it in tubs and pints, rounded once."""
    _plan_id, bowls = _breakfast_week()
    for entry in bowls[:3]:
        _grocery._reverse_meal_grocery_contributions(entry)
    lines = _lines()
    assert lines["Greek yogurt"] == "1 tub (750 g)"
    assert lines["Blueberries"] == "1 pint"
    conn = get_conn()
    ledger = [r["quantity"] for r in conn.execute(
        "SELECT l.quantity FROM meal_plan_grocery_links l JOIN grocery_items g ON g.id = l.grocery_item_id "
        "WHERE g.item = 'Greek yogurt'"
    )]
    conn.close()
    assert ledger == ["1 cup", "1 cup"]


def test_a_package_and_a_measure_are_one_store_amount():
    """CATCH. Main: Butter "1 tub + 3/4 cup", Feta "1 cup crumbled + 12 oz",
    Baby spinach "1 bag + 2 cups"."""
    _two_adults()
    tools.add_recipe("Shrimp", default_servings=2, ingredients=[
        {"item": "Butter", "qty": "4 tbsp", "category": "dairy"},
        {"item": "Feta", "qty": "1 cup crumbled", "category": "dairy"},
        {"item": "Baby spinach", "qty": "1 bag", "category": "produce"},
    ])
    tools.add_recipe("Eggs", default_servings=2, ingredients=[
        {"item": "Butter", "qty": "1 tub", "category": "dairy"},
        {"item": "Feta", "qty": "12 oz", "category": "dairy"},
        {"item": "Baby spinach", "qty": "2 cups", "category": "produce"},
    ])
    plan_id = _plan()
    for n in range(3):
        tools.plan_meal(_d(n), "Shrimp", slot="dinner", weekly_plan_id=plan_id)
    tools.plan_meal(_d(3), "Eggs", slot="dinner", weekly_plan_id=plan_id)
    tools.approve_weekly_plan(plan_id, "Emily")
    lines = _lines()
    assert lines["Butter"] == "1 lb"
    assert lines["Baby spinach"] == "1 bag"
    # 3 cups crumbled (~450 g) + 12 oz (~340 g) is four 200 g blocks.
    assert lines["Feta"] == "4 blocks (200 g)"


def test_a_cut_goes_in_the_name_not_the_quantity():
    """CATCH. Main: Chicken thighs "12 oz, boneless, skinless"."""
    _two_adults()
    tools.add_recipe("Thai Basil Chicken", default_servings=2, ingredients=[
        {"item": "Chicken thighs", "qty": "12 oz, boneless, skinless", "category": "meat/seafood"},
        {"item": "Onions", "qty": "2, large", "category": "produce"},
    ])
    plan_id = _plan()
    tools.plan_meal(_d(0), "Thai Basil Chicken", slot="dinner", weekly_plan_id=plan_id)
    tools.approve_weekly_plan(plan_id, "Emily")
    lines = _lines()
    assert lines["Boneless skinless chicken thighs"] == "12 oz"
    # A size word stays the quantity's note, as it always has.
    assert lines["Onions"] == "2, large"


def test_a_households_edit_of_a_pints_line_survives_a_dropped_morning():
    """CATCH. "3 pints" edited to 1 is measured against the meals in pints,
    so the edit stands when a morning goes (grocery._with_household_edit);
    read in cups, it had nothing to be measured against and was forgotten."""
    plan_id, bowls = _breakfast_week()
    conn = get_conn()
    item_id = conn.execute("SELECT id FROM grocery_items WHERE item = 'Blueberries'").fetchone()["id"]
    conn.close()
    tools.update_grocery_item(item_id, quantity="1 pint")
    # A morning swapped out and back in: the line is re-read off its whole
    # ledger in pints, and the household's edit goes back on top.
    _grocery._reverse_meal_grocery_contributions(bowls[0])
    from app.tools import recipes
    recipes._add_recipe_ingredients_to_grocery_list(
        bowls[0], [{"item": "Blueberries", "qty": "1 cup", "category": "produce"}], plan_id, default_servings=2,
    )
    assert _lines()["Blueberries"] == "1 pint"


# ---------- review follow-ups (2026-10-19) ----------

def test_a_big_table_without_a_batch_buys_all_its_garlic():
    """CATCH. Eight eaters off a recipe for four with 6 cloves want 12 — the
    ceiling is for a batch's extra nights, not tonight's pot."""
    for name in ("A", "B", "C", "D", "E", "F", "G", "H"):
        tools.add_member(name)
    tools.add_recipe("Garlic Chicken", default_servings=4, ingredients=[
        {"item": "Garlic", "qty": "6 cloves", "category": "produce"},
    ])
    plan_id = _plan()
    tools.plan_meal(_d(0), "Garlic Chicken", slot="dinner", weekly_plan_id=plan_id)
    tools.approve_weekly_plan(plan_id, "Emily")
    assert _lines()["Garlic"] == "2 heads"


def test_a_batched_lemon_recipe_buys_every_lemon():
    """CATCH (of this branch's first cut, which capped citrus at 3). Lemon
    bars made for three nights want three times the lemons."""
    _two_adults()
    tools.add_recipe("Lemon Bars", default_servings=2, ingredients=[
        {"item": "Lemons", "qty": "2", "category": "produce"},
    ])
    plan_id = _plan()
    ids = [tools.plan_meal(_d(n), "Lemon Bars", slot="dinner", weekly_plan_id=plan_id)["entry_id"]
           for n in (0, 1, 2)]
    tools.set_cook_ahead(ids[0], ids[1:])
    tools.approve_weekly_plan(plan_id, "Emily")
    assert _lines()["Lemons"] == "6"


def test_measures_past_the_package_add_to_it():
    """CATCH. A tub of butter and 3 cups is 2.5 lb (3 lbs bought), not the
    larger alone; under the tub's size the tub still covers it."""
    _two_adults()
    tools.add_recipe("Baking", default_servings=2, ingredients=[
        {"item": "Butter", "qty": "3 cups", "category": "dairy"},
    ])
    tools.add_recipe("Eggs", default_servings=2, ingredients=[
        {"item": "Butter", "qty": "1 tub", "category": "dairy"},
    ])
    plan_id = _plan()
    tools.plan_meal(_d(0), "Baking", slot="dinner", weekly_plan_id=plan_id)
    tools.plan_meal(_d(1), "Eggs", slot="dinner", weekly_plan_id=plan_id)
    tools.approve_weekly_plan(plan_id, "Emily")
    assert _lines()["Butter"] == "3 lbs"
