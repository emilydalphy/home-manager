"""
Spices in measures (Loop Board, 2026-10-19).

    "As a household checking my spice rack, I see how much of each spice
    this week's recipes need, in teaspoons and tablespoons (e.g. 'Ground
    cumin · 2½ tsp')."

Recipe spices used to be written as packages, so "Spices this week"
totalled them as packages: "Flaky salt · 7 boxes". Now a recipe's spice is
converted to teaspoons at ingest (spices.spice_measure) — a pinch, dash and
smidgen by the decided table, "to taste" per serving for salt and pepper,
a package as "a little" — summed across the week with batch scaling, and
the row says the total. Ticking one buys a jar.
"""
from __future__ import annotations

import datetime
import json

import pytest

from conftest import household_today

from app import tools
from app.db import get_conn
from app.tools import recipes as _recipes
from app.tools import spices


def _monday() -> datetime.date:
    today = household_today()
    return today - datetime.timedelta(days=today.weekday())


def _day(offset: int) -> str:
    return (_monday() + datetime.timedelta(days=offset)).isoformat()


def _section() -> dict[str, dict]:
    return {sp["item"]: sp for sp in tools.list_spices_this_week()["items"]}


def _needed() -> dict[str, str]:
    return {i["item"]: i["quantity"] for i in tools.list_grocery_list()}


def _household():
    tools.add_member("Emily")
    tools.add_member("Jamie")


def _saved_as_is(name: str, ingredients: list[dict], servings: int = 2) -> None:
    """A recipe already on disk from before this change — written straight
    to the table, past add_recipe's save-time rewrite, the way an old
    "1 box" recipe actually sits in a household's database."""
    tools.add_recipe(name, ingredients=[{"item": "Water", "qty": "1 cup"}], default_servings=servings)
    conn = get_conn()
    conn.execute("UPDATE recipes SET ingredients_json = ? WHERE name = ?", (json.dumps(ingredients), name))
    conn.commit()
    conn.close()


# ---------- the conversion table ----------

@pytest.mark.parametrize("qty, tsp", [
    ("a pinch", 1 / 16), ("pinch", 1 / 16), ("2 pinches", 1 / 8),
    ("a dash", 1 / 8), ("1 dash", 1 / 8), ("a smidgen", 1 / 32),
])
def test_pinch_dash_and_smidgen_convert_by_the_table_and_are_estimates(qty, tsp):
    assert spices.spice_measure("Ground cumin", qty, 4) == (pytest.approx(tsp), spices.ESTIMATE)


def test_to_taste_is_a_quarter_teaspoon_of_salt_and_an_eighth_of_pepper_per_serving():
    assert spices.spice_measure("Kosher salt", "to taste", 4) == (1.0, spices.ESTIMATE)
    assert spices.spice_measure("Black pepper", "to taste", 4) == (0.5, spices.ESTIMATE)
    assert spices.spice_measure("Salt", "", 6) == (1.5, spices.ESTIMATE), "blank reads as to taste"
    # Anything else "to taste" says nothing about servings: "a little".
    assert spices.spice_measure("Smoked paprika", "to taste", 4) == (0.25, spices.LITTLE)


def test_a_measure_is_measured_and_a_package_is_a_little():
    assert spices.spice_measure("Ground cumin", "1 1/2 tsp") == (1.5, spices.MEASURED)
    assert spices.spice_measure("Ground cumin", "1 tbsp") == (3.0, spices.MEASURED)
    assert spices.spice_measure("Olive oil", "1/4 cup") == (12.0, spices.MEASURED)
    for package in ("1 jar", "1 box", "2 boxes", "1 bottle", "1 bag", "1 container"):
        assert spices.spice_measure("Flaky salt", package) == (0.25, spices.LITTLE), package
    # A count or a weight is not ours to convert; it keeps its old path.
    assert spices.spice_measure("Bay leaves", "2") is None
    assert spices.spice_measure("Saffron", "1 g") is None


@pytest.mark.parametrize("item, tsp, estimated, text", [
    ("Ground cumin", 2.5, False, "2½ tsp"),
    ("Flaky salt", 7 / 16, True, "about ½ tsp"),
    ("Flaky salt", 1 / 16, True, "about ⅛ tsp"),
    ("Salt", 4.5, True, "about 1½ tbsp"),
    ("Ground cumin", 3.0, False, "1 tbsp"),
    ("Ground cumin", 15.0, False, "5 tbsp"),
    ("Olive oil", 12.0, False, "4 tbsp"),
    ("Olive oil", 32.0, False, "⅔ cup"),
    ("Olive oil", 18.0, False, "⅓ cup"),
])
def test_the_rows_amount_reads_in_tsp_then_tbsp_then_cups_for_oil(item, tsp, estimated, text):
    assert spices.amount_text(item, tsp, estimated) == text


# ---------- the week ----------

@pytest.fixture
def week():
    """A batch dinner (cooked Monday for three nights) and a salted snack
    every afternoon for seven days, for two people."""
    _household()
    tools.add_recipe("Big chili", ingredients=[
        {"item": "Ground beef", "qty": "1 lb", "category": "meat/seafood"},
        {"item": "Ground cumin", "qty": "1 tsp", "category": "pantry"},
        {"item": "Salt", "qty": "to taste", "category": "pantry"},
        {"item": "Olive oil", "qty": "2 tbsp", "category": "pantry"},
    ], default_servings=2)
    tools.add_recipe("Popcorn", ingredients=[
        {"item": "Popcorn kernels", "qty": "1/2 cup", "category": "pantry"},
        {"item": "Flaky salt", "qty": "a pinch", "category": "pantry"},
        {"item": "Ground cumin", "qty": "1/4 tsp", "category": "pantry"},
    ], default_servings=2)
    plan_id = tools.create_weekly_plan(_monday().isoformat())["weekly_plan_id"]
    cook = tools.plan_meal(_day(0), "Big chili", slot="dinner", weekly_plan_id=plan_id)["entry_id"]
    reheats = [tools.plan_meal(_day(d), "Big chili", slot="dinner", weekly_plan_id=plan_id)["entry_id"] for d in (1, 2)]
    assert not isinstance(tools.set_cook_ahead(cook, reheats), str)
    for d in range(7):
        tools.plan_meal(_day(d), "Popcorn", slot="snack", weekly_plan_id=plan_id)
    tools.approve_weekly_plan(plan_id, approved_by="Emily")
    return plan_id


def test_a_week_sums_a_batch_dinner_and_seven_snacks(week):
    section = _section()
    # Seven pinches: 7/16 tsp, a guess, to the nearest eighth.
    assert section["Flaky salt"]["quantity"] == "about ½ tsp"
    # The batch cooks three nights' worth once: 3 tsp of cumin, plus the
    # snacks' 7 x 1/4 tsp = 4.75 tsp, measured — no "about".
    assert section["Ground cumin"]["quantity"] == "1½ tbsp"
    # To taste for a batch of 6 servings: 6 x 1/4 tsp.
    assert section["Salt"]["quantity"] == "about 1½ tsp"
    # 3 x 2 tbsp of oil = 6 tbsp, past 4 tbsp: in measuring cups.
    assert section["Olive oil"]["quantity"] == "⅓ cup"


def test_ticking_buys_one_jar_and_the_measure_stays_on_the_row(week):
    salt = _section()["Flaky salt"]
    tools.tick_spice(salt["id"])
    assert _needed()["Flaky salt"] == "1 jar"
    assert _section()["Flaky salt"]["quantity"] == "about ½ tsp"
    oil = _section()["Olive oil"]
    tools.tick_spice(oil["id"])
    assert _needed()["Olive oil"] == "1 bottle"


def test_more_than_a_jar_holds_buys_two():
    assert spices.store_quantity("Ground cumin", "0.5 cup") == "1 jar"
    assert spices.store_quantity("Ground cumin", "0.75 cup") == "2 jars"
    # A household's own amount is left as they wrote it.
    assert spices.store_quantity("Ground cumin", "3 jars") is None


def test_dropping_a_snack_takes_its_pinch_back_off_the_row(week):
    tools.clear_plan_slot(week, _day(6), "snack")
    assert _section()["Flaky salt"]["quantity"] == "about ⅜ tsp"


# ---------- a recipe still written in packages ----------

def test_a_package_unit_never_sums_as_packages():
    """An old recipe's "1 box" of flaky salt on seven snacks: never 7
    boxes, on the row or on the line — "a little", until it is
    regenerated with a measure."""
    _household()
    _saved_as_is("Old popcorn", [
        {"item": "Popcorn kernels", "qty": "1/2 cup", "category": "pantry"},
        {"item": "Flaky salt", "qty": "1 box", "category": "pantry"},
    ])
    plan_id = tools.create_weekly_plan(_monday().isoformat())["weekly_plan_id"]
    for d in range(7):
        tools.plan_meal(_day(d), "Old popcorn", slot="snack", weekly_plan_id=plan_id)
    tools.approve_weekly_plan(plan_id, approved_by="Emily")
    salt = _section()["Flaky salt"]
    assert salt["quantity"] == "a little"
    conn = get_conn()
    line = conn.execute("SELECT quantity FROM grocery_items WHERE item = 'Flaky salt'").fetchone()
    conn.close()
    assert "box" not in line["quantity"]
    tools.tick_spice(salt["id"])
    assert _needed()["Flaky salt"] == "1 jar"


def test_a_package_beside_a_measure_counts_as_a_quarter_teaspoon():
    _household()
    _saved_as_is("Old rub", [{"item": "Smoked paprika", "qty": "1 jar", "category": "pantry"}])
    tools.add_recipe("New rub", ingredients=[{"item": "Smoked paprika", "qty": "2 tsp", "category": "pantry"}],
                     default_servings=2)
    plan_id = tools.create_weekly_plan(_monday().isoformat())["weekly_plan_id"]
    tools.plan_meal(_day(0), "Old rub", slot="dinner", weekly_plan_id=plan_id)
    tools.plan_meal(_day(1), "New rub", slot="dinner", weekly_plan_id=plan_id)
    tools.approve_weekly_plan(plan_id, approved_by="Emily")
    assert _section()["Smoked paprika"]["quantity"] == "about 2¼ tsp"


def test_a_ledger_row_from_before_reads_through_the_same_table():
    """A line already on a list from before this change carries the
    recipe's own words in its ledger ("1 box", "a pinch")."""
    assert spices._ledger_measure("Flaky salt", "1 box", 0) == (0.25, spices.LITTLE)
    assert spices._ledger_measure("Flaky salt", "a pinch", 0) == (1 / 16, spices.ESTIMATE)
    assert spices._ledger_measure("Flaky salt", "0.0625 tsp", 1) == (0.0625, spices.ESTIMATE)
    assert spices._ledger_measure("Flaky salt", "0.25 tsp", 2) == (0.25, spices.LITTLE)


def test_a_new_recipe_never_saves_a_spice_as_a_package():
    saved = _recipes._spices_as_measures([
        {"item": "Paprika", "qty": "1 jar", "cook_qty": "2 tsp"},
        {"item": "Flaky salt", "qty": "1 box"},
        {"item": "Ground cumin", "qty": "1 jar"},
        {"item": "Soy sauce", "qty": "1 bottle"},
    ])
    assert [i["qty"] for i in saved] == ["2 tsp", "to taste", "", "1 bottle"]
    tools.add_recipe("Rub", ingredients=[{"item": "Garam masala", "qty": "1 jar"}], default_servings=2)
    stored = tools.get_recipe("Rub")
    assert all("jar" not in (i.get("qty") or "") for i in stored["ingredients"])


# ---------- review, 2026-10-19 ----------

@pytest.mark.parametrize("qty, tsp, kind", [
    ("1/2 tsp freshly ground", 0.5, spices.MEASURED),
    ("1 1/2 tbsp ground", 4.5, spices.MEASURED),
    ("2 tsp plus more to taste", 2.0, spices.ESTIMATE),
    ("1 tsp, or to taste", 1.0, spices.ESTIMATE),
])
def test_a_measure_with_words_after_it_is_still_the_measure(qty, tsp, kind):
    assert spices.spice_measure("Black pepper", qty, 2) == (tsp, kind)


def test_a_described_measure_sums_and_ticks_to_a_jar():
    _household()
    tools.add_recipe("Steak", ingredients=[
        {"item": "Black pepper", "qty": "1/2 tsp freshly ground", "category": "pantry"},
        {"item": "Steak", "qty": "1 lb", "category": "meat/seafood"},
    ], default_servings=2)
    plan_id = tools.create_weekly_plan(_monday().isoformat())["weekly_plan_id"]
    for d in (0, 2):
        tools.plan_meal(_day(d), "Steak", slot="dinner", weekly_plan_id=plan_id)
    tools.approve_weekly_plan(plan_id, approved_by="Emily")
    pepper = _section()["Black pepper"]
    assert pepper["quantity"] == "1 tsp"
    tools.tick_spice(pepper["id"])
    assert _needed()["Black pepper"] == "1 jar"


def test_a_line_still_in_package_words_at_deploy_shops_as_a_jar_and_re_reads_in_tsp(week):
    """A list approved before this change: "7 boxes" on the line, "1 box"
    per snack in its ledger. Ticked, it buys one jar; a meal added later
    re-reads the whole line in teaspoons rather than "6 boxes + 0.0625 tsp"."""
    conn = get_conn()
    line = conn.execute("SELECT id FROM grocery_items WHERE item = 'Flaky salt'").fetchone()["id"]
    conn.execute("UPDATE grocery_items SET quantity = '7 boxes' WHERE id = ?", (line,))
    conn.execute("UPDATE meal_plan_grocery_links SET quantity = '1 box', estimated = 0 WHERE grocery_item_id = ?", (line,))
    conn.commit()
    conn.close()
    assert _section()["Flaky salt"]["quantity"] == "a little"
    tools.tick_spice(line)
    assert _needed()["Flaky salt"] == "1 jar"

    tools.add_recipe("Salted nuts", ingredients=[
        {"item": "Almonds", "qty": "1 cup", "category": "pantry"},
        {"item": "Flaky salt", "qty": "a pinch", "category": "pantry"},
    ], default_servings=2)
    tools.swap_meal_in_plan(week, _day(6), "Salted nuts", slot="snack")

    conn = get_conn()
    stored = conn.execute("SELECT quantity FROM grocery_items WHERE id = ?", (line,)).fetchone()["quantity"]
    ledger = [r["quantity"] for r in conn.execute(
        "SELECT quantity FROM meal_plan_grocery_links WHERE grocery_item_id = ?", (line,))]
    conn.close()
    assert "box" not in stored and "+" not in stored, stored
    assert all(q.endswith("tsp") for q in ledger), ledger
    # 6 x "a little" (1/4 tsp) left + the new snack's pinch: about 1⅝ tsp.
    assert _section()["Flaky salt"]["quantity"] == "about 1⅝ tsp"
    assert _needed()["Flaky salt"] == "1 jar"


def test_the_row_rounds_half_up_like_the_line():
    assert spices.amount_text("Ground cumin", 6.75) == "2¼ tbsp"
    assert spices.amount_text("Ground cumin", 5 / 16) == "⅜ tsp"


def test_a_bought_spice_goes_into_the_kitchen_as_the_jar(week):
    salt = _section()["Flaky salt"]
    tools.tick_spice(salt["id"])
    tools.mark_grocery_item(salt["id"], "purchased")
    conn = get_conn()
    row = conn.execute("SELECT quantity FROM inventory_items WHERE item = 'Flaky salt'").fetchone()
    conn.close()
    assert row is not None and row["quantity"] == "1 jar"
