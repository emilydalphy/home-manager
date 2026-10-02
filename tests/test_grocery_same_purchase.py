"""
A recipe's plain name and the variety a shopper buys for it are one line.

Loop Board bug, QA walk as a new household 2026-10-02: approving the first
week put "Onions 2" AND "Yellow onion 3" under Produce, and "Rice 2 cups"
AND "Long-grain white rice 1 cup" under Pantry. The list merged singular
and plural (test_grocery_plurals.py) but nothing past that, so two recipes
that named the same purchase two ways made two lines.

The fix is an allow-list in grocery._SAME_PURCHASE, never a "shorter name
inside the longer" rule: red onion, brown rice, olive vs olive oil and
chicken breast vs thighs are all things a shopper buys separately, and the
section at the bottom pins that they stay apart.
"""
import datetime

import pytest

from app import tools
from app.db import get_conn
from app.tools import grocery


def _monday() -> datetime.date:
    today = datetime.date.today()
    return today - datetime.timedelta(days=today.weekday())


def _day(offset: int) -> str:
    return (_monday() + datetime.timedelta(days=offset)).isoformat()


MON, TUE, WED, THU = (_day(i) for i in range(4))


def _household() -> None:
    for name in ("Alex", "Sam", "Rae", "Jo"):
        tools.add_member(name)


def _needed() -> list[dict]:
    conn = get_conn()
    rows = conn.execute(
        "SELECT id, item, quantity, source_weekly_plan_id FROM grocery_items "
        "WHERE household_id = 1 AND status IN ('needed', 'spice') ORDER BY id"
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def _plan() -> int:
    """A real weekly plan to stand for "a plan's add" (the column is a foreign key)."""
    return tools.create_weekly_plan(_monday().isoformat())["weekly_plan_id"]


def _lines_for(key: str) -> list[dict]:
    return [r for r in _needed() if grocery._merge_key(r["item"]) == key]


def _qa_week() -> tuple[int, int, int]:
    """The QA walk's shape: one recipe says onions and rice, another the varieties."""
    _household()
    tools.add_recipe(
        "Chicken and Rice",
        ingredients=[
            {"item": "Onions", "qty": "2", "category": "produce"},
            {"item": "Rice", "qty": "2 cups", "category": "pantry"},
        ],
        default_servings=4,
    )
    tools.add_recipe(
        "Pilaf",
        ingredients=[
            {"item": "Yellow onion", "qty": "3", "category": "produce"},
            {"item": "Long-grain white rice", "qty": "1 cup", "category": "pantry"},
        ],
        default_servings=4,
    )
    plan_id = tools.create_weekly_plan(_monday().isoformat())["weekly_plan_id"]
    plain = tools.plan_meal(TUE, "Chicken and Rice", slot="dinner", weekly_plan_id=plan_id)["entry_id"]
    variety = tools.plan_meal(THU, "Pilaf", slot="dinner", weekly_plan_id=plan_id)["entry_id"]
    tools.approve_weekly_plan(plan_id, "Emily")
    return plan_id, plain, variety


# ---------- the QA walk, end to end ----------


def test_approving_the_week_puts_onions_on_one_line_with_the_amounts_added():
    """CATCH: main lists "Onions 2" and "Yellow onion 3"."""
    _qa_week()
    onions = _lines_for("onion")
    assert len(onions) == 1, [r["item"] for r in onions]
    assert onions[0]["item"] == "Yellow onion", "the variety names the onion to buy"
    assert onions[0]["quantity"] == "5"


def test_approving_the_week_puts_rice_on_one_line_with_the_amounts_added():
    """CATCH: main lists "Rice 2 cups" and "Long-grain white rice 1 cup"."""
    _qa_week()
    rice = _lines_for("rice")
    assert len(rice) == 1, [r["item"] for r in rice]
    assert rice[0]["item"] == "Long-grain white rice"
    assert rice[0]["quantity"] == "3 cups"


def test_the_specific_name_wins_whichever_recipe_comes_first():
    """CATCH: the order meals land in must not decide the wording."""
    _household()
    tools.add_recipe("Pilaf", ingredients=[{"item": "Yellow onions", "qty": "1"}], default_servings=4)
    tools.add_recipe("Soup", ingredients=[{"item": "Onion", "qty": "2"}], default_servings=4)
    plan_id = tools.create_weekly_plan(_monday().isoformat())["weekly_plan_id"]
    tools.plan_meal(MON, "Pilaf", slot="dinner", weekly_plan_id=plan_id)
    tools.plan_meal(WED, "Soup", slot="dinner", weekly_plan_id=plan_id)
    tools.approve_weekly_plan(plan_id, "Emily")
    onions = _lines_for("onion")
    assert [(r["item"], r["quantity"]) for r in onions] == [("Yellow onions", "3")]


# ---------- reversing still works on the merged line ----------


def test_removing_the_variety_meal_leaves_the_plain_meals_amount():
    plan_id, plain, variety = _qa_week()
    grocery._reverse_meal_grocery_contributions(variety)
    onions, rice = _lines_for("onion"), _lines_for("rice")
    assert [r["quantity"] for r in onions] == ["2"]
    assert [r["quantity"] for r in rice] == ["2 cups"]


def test_removing_the_plain_meal_leaves_the_variety_meals_amount():
    plan_id, plain, variety = _qa_week()
    grocery._reverse_meal_grocery_contributions(plain)
    assert [(r["item"], r["quantity"]) for r in _lines_for("onion")] == [("Yellow onion", "3")]
    assert [(r["item"], r["quantity"]) for r in _lines_for("rice")] == [("Long-grain white rice", "1 cup")]


def test_removing_both_meals_takes_the_merged_lines_off():
    plan_id, plain, variety = _qa_week()
    grocery._reverse_meal_grocery_contributions(plain)
    grocery._reverse_meal_grocery_contributions(variety)
    assert _lines_for("onion") == [] and _lines_for("rice") == []


def test_clearing_the_week_takes_the_merged_lines_off():
    plan_id, plain, variety = _qa_week()
    tools.clear_weekly_plan(plan_id)
    assert _lines_for("onion") == [] and _lines_for("rice") == []


def test_swapping_the_variety_meal_out_takes_its_share_back():
    plan_id, plain, variety = _qa_week()
    tools.add_recipe("Tacos", ingredients=[{"item": "Tortillas", "qty": "8"}], default_servings=4)
    tools.swap_meal_in_plan(plan_id, THU, "Tacos", slot="dinner")
    assert [r["quantity"] for r in _lines_for("onion")] == ["2"]
    assert [r["quantity"] for r in _lines_for("rice")] == ["2 cups"]


def test_a_meal_planned_after_approval_joins_the_line_and_names_it():
    """CATCH: a later plan add ("Yellow onion") onto a plan line ("Onions")."""
    _household()
    tools.add_recipe("Soup", ingredients=[{"item": "Onions", "qty": "2"}], default_servings=4)
    tools.add_recipe("Pilaf", ingredients=[{"item": "Yellow onion", "qty": "1"}], default_servings=4)
    plan_id = tools.create_weekly_plan(_monday().isoformat())["weekly_plan_id"]
    tools.plan_meal(MON, "Soup", slot="dinner", weekly_plan_id=plan_id)
    tools.approve_weekly_plan(plan_id, "Emily")
    later = tools.plan_meal(
        WED, "Pilaf", slot="dinner", weekly_plan_id=plan_id, add_ingredients_to_grocery_list=True,
    )["entry_id"]
    assert [(r["item"], r["quantity"]) for r in _lines_for("onion")] == [("Yellow onion", "3")]
    grocery._reverse_meal_grocery_contributions(later)
    assert [r["quantity"] for r in _lines_for("onion")] == ["2"]


# ---------- a person's own line and own add keep their wording ----------


def test_a_hand_added_line_keeps_the_name_the_person_typed():
    tools.add_grocery_item("Onions", quantity="2")
    result = tools.add_grocery_item("Yellow onion", quantity="3", source_weekly_plan_id=_plan())
    assert result["merged"] is True
    rows = _needed()
    assert [(r["item"], r["quantity"], r["source_weekly_plan_id"]) for r in rows] == [("Onions", "5", None)]


def test_a_person_adding_the_variety_to_a_plan_line_does_not_rename_it():
    """The shop sheet's Put back restores amount and store only, by row id."""
    tools.add_grocery_item("Onions", quantity="2", source_weekly_plan_id=_plan())
    result = tools.add_grocery_item("Yellow onion", quantity="1")
    assert result["merged"] is True and result["item"] == "Onions"
    assert [(r["item"], r["quantity"]) for r in _needed()] == [("Onions", "3")]


def test_a_person_adding_the_plain_name_merges_into_the_variety():
    """CATCH: typing "Rice" with "White rice" on the list made a second line."""
    tools.add_grocery_item("White rice", quantity="2 cups")
    result = tools.add_grocery_item("rice", quantity="1 cup")
    assert result["merged"] is True and result["item"] == "White rice"
    assert [(r["item"], r["quantity"]) for r in _needed()] == [("White rice", "3 cups")]


def test_cleaning_up_the_list_folds_the_plain_line_into_the_variety():
    """CATCH: consolidate_grocery_list on a list that already has both."""
    conn = get_conn()
    for name, qty in [("Onions", "2"), ("Yellow onion", "3"), ("Red onion", "1")]:
        conn.execute(
            "INSERT INTO grocery_items (household_id, item, quantity, category, status) "
            "VALUES (1, ?, ?, 'produce', 'needed')",
            (name, qty),
        )
    conn.commit()
    conn.close()
    assert tools.consolidate_grocery_list()["lines_merged_away"] == 1
    assert sorted((r["item"], r["quantity"]) for r in _needed()) == [("Red onion", "1"), ("Yellow onion", "5")]


def test_units_that_do_not_reconcile_stay_honest_on_one_plan_line():
    """Same purchase, different units: no conversion is guessed (existing rule)."""
    pid = _plan()
    tools.add_grocery_item("Rice", quantity="2 cups", source_weekly_plan_id=pid)
    tools.add_grocery_item("Long-grain white rice", quantity="1 bag", source_weekly_plan_id=pid)
    rows = _lines_for("rice")
    assert len(rows) == 1
    assert rows[0]["quantity"] == "2 cups + 1 bag"


# ---------- the key itself ----------


@pytest.mark.parametrize("plain,variety", [
    ("Onions", "Yellow onion"),
    ("onion", "Yellow Onions"),
    ("Onions", "Cooking onions"),
    ("Rice", "Long-grain white rice"),
    ("Rice", "long grain white rice"),
    ("Rice", "White rice"),
    ("Flour", "All-purpose flour"),
    ("Sugar", "Granulated sugar"),
])
def test_the_plain_name_and_its_variety_share_a_key(plain, variety):
    assert grocery._merge_key(plain) == grocery._merge_key(variety)


@pytest.mark.parametrize("a,b", [
    # Things a shopper buys separately. A wrong merge is invisible and means
    # something never gets bought.
    ("Red onion", "Yellow onion"),
    ("Red onion", "Onions"),
    ("White onion", "Onions"),
    ("Green onions", "Onions"),
    ("Brown rice", "White rice"),
    ("Brown rice", "Rice"),
    ("Basmati rice", "Rice"),
    ("Jasmine rice", "Long-grain white rice"),
    ("Olives", "Olive oil"),
    ("Olive", "Olive oil"),
    ("Chicken breast", "Chicken thighs"),
    ("Chicken breasts", "Chicken"),
    ("Whole wheat flour", "Flour"),
    ("Brown sugar", "Sugar"),
    ("Icing sugar", "Granulated sugar"),
    ("Onion powder", "Onions"),
    ("Rice vinegar", "Rice"),
])
def test_things_bought_separately_stay_apart(a, b):
    assert grocery._merge_key(a) != grocery._merge_key(b)
    pid = _plan()
    tools.add_grocery_item(a, quantity="1", source_weekly_plan_id=pid)
    tools.add_grocery_item(b, quantity="1", source_weekly_plan_id=pid)
    assert len(_needed()) == 2, f"{a!r} and {b!r} are different purchases"


def test_red_onion_in_a_week_with_onions_stays_its_own_line():
    _household()
    tools.add_recipe("Salsa", ingredients=[{"item": "Red onion", "qty": "1"}], default_servings=4)
    tools.add_recipe("Soup", ingredients=[{"item": "Onions", "qty": "2"}], default_servings=4)
    plan_id = tools.create_weekly_plan(_monday().isoformat())["weekly_plan_id"]
    tools.plan_meal(MON, "Salsa", slot="dinner", weekly_plan_id=plan_id)
    tools.plan_meal(WED, "Soup", slot="dinner", weekly_plan_id=plan_id)
    tools.approve_weekly_plan(plan_id, "Emily")
    assert sorted((r["item"], r["quantity"]) for r in _needed()) == [("Onions", "2"), ("Red onion", "1")]
