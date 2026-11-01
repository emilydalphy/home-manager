"""
Quarts and gallons add up with cups (Loop Board, "quarts and gallons don't
add up with cups"; Emily 2026-11-01: option A, "fix it properly").

On main a quart and a gallon were unit words with no family, so they
counted like onions: "1 gallon" of milk then "2 cups" read "1 gallon + 2
cups", 4 cups used off a "2 quarts" Kitchen row left "2 quarts", and a
plan's half gallon was bought as "1 gallon". A quart is 4 cups and a gallon
16 now (quantities._CUPS_PER_BIG); where quarts or gallons are in play a
volume reads in them from a quart up (whole cups, up), in cups under one.
Plain cups stay cups — 16 cups of flour is not a gallon of flour.

The migration test builds the rows the way main stored them and checks
that a night dropped AFTER the upgrade takes back what main's rounding put
on, not what the new rounding would have (the reason an earlier attempt at
this was withheld: "24 cups" instead of "1 gallon").
"""
from __future__ import annotations

from datetime import timedelta

import pytest
from conftest import household_today

from app import db as app_db
from app import tools
from app.db import get_conn
from app.tools import quantities


# ---------- how an amount reads ----------

@pytest.mark.parametrize("raw,stored", [
    ("3.5 quarts", "3 1/2 quarts"),
    ("1 quart", "1 quart"),
    ("4 quarts", "1 gallon"),        # 16 cups
    ("6 quarts", "1 1/2 gallons"),   # 24 cups
    ("5 quarts", "5 quarts"),        # 20 cups: not a half gallon
    ("0.75 gallon", "3 quarts"),
    ("1/2 quart", "2 cups"),         # under a quart is cups
    ("1.1 quarts", "1 1/4 quarts"),  # whole cups, rounded up
    ("2 gal", "2 gallons"),
    ("16 cups", "16 cups"),          # GUARD: plain cups never roll up
    ("1 1/3 cups", "1 1/3 cups"),
])
def test_how_a_quart_or_gallon_reads_on_the_list(raw, stored):
    assert quantities._normalize_grocery_quantity(raw) == stored
    assert quantities.tidy_display_quantity("Milk", stored) == stored


@pytest.mark.parametrize("rows,total", [
    (["2 quarts", "1.5 quarts"], "3 1/2 quarts"),
    (["1 quart", "2 cups"], "1 1/2 quarts"),
    (["0.5 gallon", "2 cups"], "2 1/2 quarts"),
    (["0.5 gallon"], "1/2 gallon"),
    (["0.5 gallon", "0.5 gallon"], "1 gallon"),
    (["3 cups", "1 cup"], "4 cups"),  # GUARD: cups alone stay cups
])
def test_ledger_rows_add_up_across_quarts_gallons_and_cups(rows, total):
    assert quantities._sum_ledger_quantities(rows) == total


def test_a_line_joined_before_this_change_reads_as_one_amount():
    assert quantities.tidy_display_quantity("Milk", "1 gallon + 2 cups") == "4 1/2 quarts"


# ---------- the list ----------

def _rows(name: str) -> list[tuple[str, bool]]:
    """(quantity, is the household's own) for every needed row of `name`."""
    conn = get_conn()
    rows = conn.execute(
        "SELECT quantity, source_weekly_plan_id FROM grocery_items "
        "WHERE household_id = ? AND LOWER(item) = ? AND status IN ('needed', 'spice') ORDER BY id",
        (tools.household_id(), name.lower()),
    ).fetchall()
    conn.close()
    return [(r["quantity"], r["source_weekly_plan_id"] is None) for r in rows]


def _shown(name: str) -> list[str]:
    return [i["quantity"] for i in tools.list_grocery_list() if i["item"].lower() == name.lower()]


def _week(recipes: dict[str, list[dict]], own: dict[str, str] | None = None):
    """Emily alone, one dinner per recipe on the days after next Monday,
    the household's own lines added first, approved."""
    conn = get_conn()
    conn.execute("DELETE FROM members WHERE household_id = ? AND name != 'Emily'", (tools.household_id(),))
    conn.commit()
    conn.close()
    for name, ingredients in recipes.items():
        tools.add_recipe(name, ingredients=ingredients, default_servings=1)
    tools.add_recipe("Toast", ingredients=[{"item": "Bread", "qty": "1 loaf", "category": "pantry"}],
                     default_servings=1)
    for item, qty in (own or {}).items():
        tools.add_grocery_item(item, quantity=qty, category="dairy")
    today = household_today()
    start = today - timedelta(days=today.weekday()) + timedelta(days=7)
    plan = tools.create_weekly_plan(start.isoformat())["weekly_plan_id"]
    days = []
    for i, name in enumerate(recipes):
        day = (start + timedelta(days=i + 1)).isoformat()
        tools.plan_meal(day, name, slot="dinner", weekly_plan_id=plan)
        days.append(day)
    tools.approve_weekly_plan(plan, "Emily")
    return plan, days


def _drop(plan: int, day: str) -> None:
    tools.swap_meal_in_plan(plan, day, "Toast", slot="dinner")


def test_a_gallon_then_two_cups_typed_is_one_amount():
    tools.add_grocery_item("Milk", quantity="1 gallon", category="dairy")
    tools.add_grocery_item("Milk", quantity="2 cups", category="dairy")
    assert _rows("Milk") == [("4 1/2 quarts", True)]  # main: "1 gallon + 2 cups"
    assert _shown("Milk") == ["4 1/2 quarts"]


def test_a_plans_quarts_of_stock_read_in_quarts_not_cups():
    _week({
        "Soup": [{"item": "Chicken stock", "qty": "2 quarts", "category": "pantry"}],
        "Stew": [{"item": "Chicken stock", "qty": "1 1/2 quarts", "category": "pantry"}],
        "Shake": [{"item": "Milk", "qty": "1 gallon", "category": "dairy"}],
    })
    assert [q for q, _own in _rows("Chicken stock")] == ["3 1/2 quarts"]  # not "14 cups"
    assert _rows("Milk") == [("1 gallon", False)]  # not "16 cups"


def test_a_plans_half_gallon_is_bought_as_a_half_gallon():
    _week({"Pudding": [{"item": "Milk", "qty": "1/2 gallon", "category": "dairy"}]})
    assert _rows("Milk") == [("1/2 gallon", False)]  # main: "1 gallon"


def test_a_plans_cups_of_flour_stay_cups():
    """GUARD: no quart or gallon in play, so 16 cups is 16 cups."""
    _week({"Bread": [{"item": "Flour", "qty": "16 cups", "category": "pantry"}]})
    assert [q for q, _own in _rows("Flour")] == ["16 cups"]


def test_typed_quart_and_a_plans_quart_are_one_line_and_the_drop_leaves_the_typed_one():
    plan, days = _week({"Pancakes": [{"item": "Buttermilk", "qty": "1 quart", "category": "dairy"}]},
                       own={"Buttermilk": "1 quart"})
    assert _rows("Buttermilk") == [("2 quarts", True)]
    _drop(plan, days[0])
    assert _rows("Buttermilk") == [("1 quart", True)]


def test_a_plans_quarts_beside_a_typed_gallon_keep_their_own_line():
    """A plan's share only joins a household's line in the line's own word,
    so their typed gallon is never re-said in quarts (review, 2026-11-01)."""
    plan, days = _week({"Custard": [{"item": "Milk", "qty": "3 quarts", "category": "dairy"}]},
                       own={"Milk": "1 gallon"})
    assert _rows("Milk") == [("1 gallon", True), ("3 quarts", False)]
    _drop(plan, days[0])
    assert _rows("Milk") == [("1 gallon", True)]


@pytest.mark.parametrize("order", [(0, 1), (1, 0)])
def test_thirds_of_a_quart_on_a_typed_quart_line_come_back_to_what_was_typed(order):
    """Review BLOCKER (2026-11-01): a third of a quart rounded to "1 1/3
    cups" left the household's line in cups ("13 1/3 cups", then "12
    cups"). Shares are whole cups now, so the line stays in quarts. The
    third comes from scaling: a one-quart recipe for three, cooked for one."""
    plan, days = _week(
        {
            "Shake": [{"item": "Juice", "qty": "1 1/3 quarts", "category": "dairy"}],
            "Pancakes": [{"item": "Juice", "qty": "1/3 quart", "category": "dairy"}],
        },
        own={"Juice": "3 quarts"},
    )
    assert _rows("Juice") == [("4 3/4 quarts", True)]  # 12 + 7 cups (6 2/3, up)
    _drop(plan, days[order[0]])
    assert _rows("Juice") == [("3 1/2 quarts", True) if order[0] == 0 else ("4 1/2 quarts", True)]
    _drop(plan, days[order[1]])
    assert _rows("Juice") == [("3 quarts", True)]
    assert _shown("Juice") == ["3 quarts"]


def test_a_one_quart_recipe_for_three_cooked_for_one_is_whole_cups():
    """The third of a quart comes from scaling, not from the recipe."""
    conn = get_conn()
    conn.execute("DELETE FROM members WHERE household_id = ? AND name != 'Emily'", (tools.household_id(),))
    conn.commit()
    has_emily = conn.execute("SELECT 1 FROM members WHERE household_id = ?", (tools.household_id(),)).fetchone()
    conn.close()
    if not has_emily:
        tools.add_member("Emily")
    tools.add_recipe("Smoothie", ingredients=[{"item": "Kefir", "qty": "1 quart", "category": "dairy"}],
                     default_servings=3)
    tools.add_recipe("Toast", ingredients=[{"item": "Bread", "qty": "1 loaf", "category": "pantry"}],
                     default_servings=1)
    tools.add_grocery_item("Kefir", quantity="3 quarts", category="dairy")
    today = household_today()
    start = today - timedelta(days=today.weekday()) + timedelta(days=7)
    plan = tools.create_weekly_plan(start.isoformat())["weekly_plan_id"]
    day = (start + timedelta(days=1)).isoformat()
    tools.plan_meal(day, "Smoothie", slot="dinner", weekly_plan_id=plan)
    tools.approve_weekly_plan(plan, "Emily")
    # A third of a quart is 1 1/3 cups, bought as 2 — cups, so it keeps a
    # plan line of its own beside the household's quarts (the line's word).
    assert _rows("Kefir") == [("3 quarts", True), ("2 cups", False)]
    _drop(plan, day)
    assert _rows("Kefir") == [("3 quarts", True)]
    assert quantities._normalize_grocery_quantity("0.333333 quart") == "2 cups"


def test_the_reviewers_gallon_of_juice_ends_as_a_gallon():
    plan, days = _week(
        {
            "Shake": [{"item": "Juice", "qty": "1 1/3 quarts", "category": "dairy"}],
            "Pancakes": [{"item": "Juice", "qty": "1/3 quart", "category": "dairy"}],
        },
        own={"Juice": "1 gallon"},
    )
    _drop(plan, days[0])
    _drop(plan, days[1])
    assert _rows("Juice") == [("1 gallon", True)]  # review: "16 cups"


def test_typed_two_quarts_and_a_plans_two_quarts_come_back_as_two_quarts():
    plan, days = _week({"Soup": [{"item": "Broth", "qty": "2 quarts", "category": "pantry"}]},
                       own={"Broth": "2 quarts"})
    assert [q for q, _own in _rows("Broth")] == ["1 gallon"]
    _drop(plan, days[0])
    assert [q for q, _own in _rows("Broth")] == ["2 quarts"]  # not "1/2 gallon"


def test_typed_half_gallon_stays_a_half_through_a_plans_gallons_and_quarts():
    """Review SHOULD-FIX 5: "1/2 gallon" came back "2 quarts"."""
    plan, days = _week(
        {
            "Shake": [{"item": "Milk", "qty": "1 gallon", "category": "dairy"}],
            "Custard": [{"item": "Milk", "qty": "1 quart", "category": "dairy"}],
            "Latte": [{"item": "Milk", "qty": "2 cups", "category": "dairy"}],
        },
        own={"Milk": "1/2 gallon"},
    )
    for day in days:
        _drop(plan, day)
    assert _rows("Milk") == [("1/2 gallon", True)]


def test_typed_half_gallon_and_a_plans_half_gallon_make_a_gallon_and_the_drop_leaves_the_half():
    plan, days = _week({"Pudding": [{"item": "Milk", "qty": "1/2 gallon", "category": "dairy"}]},
                       own={"Milk": "1/2 gallon"})
    assert _rows("Milk") == [("1 gallon", True)]  # main: "2 gallons"
    _drop(plan, days[0])
    assert _rows("Milk") == [("1/2 gallon", True)]  # main: "1 gallon"


def test_a_typed_half_gallon_stays_a_half_gallon():
    """GUARD: the 2026-10-26 typed-half rule (quantities.typed_half_pack)."""
    tools.add_grocery_item("Milk", quantity="½ gallon", category="dairy")
    assert _rows("Milk") == [("1/2 gallon", True)]
    assert _shown("Milk") == ["1/2 gallon"]


# ---------- the Kitchen ----------

def _kitchen(name: str) -> list[str]:
    return [i["quantity"] for i in tools.get_inventory() if i["item"].lower() == name.lower()]


def test_cups_used_off_quarts_and_gallons_come_off():
    tools.update_inventory("Chicken stock", "add", quantity="2 quarts")
    tools.update_inventory("Chicken stock", "use", quantity="4 cups")
    assert _kitchen("Chicken stock") == ["1 quart"]  # main: "2 quarts"
    tools.update_inventory("Milk", "add", quantity="1 gallon")
    tools.update_inventory("Milk", "use", quantity="2 cups")
    assert _kitchen("Milk") == ["3 1/2 quarts"]
    tools.update_inventory("Milk", "add", quantity="2 cups")
    assert _kitchen("Milk") == ["1 gallon"]


def test_a_quart_used_off_cups_comes_off_too():
    tools.update_inventory("Broth", "add", quantity="6 cups")
    tools.update_inventory("Broth", "use", quantity="1 quart")
    assert _kitchen("Broth") == ["2 cups"]


@pytest.mark.parametrize("start,used,left", [
    ("1 gallon", "1 tbsp", "3 3/4 quarts"),   # review: "255 tbsp"
    ("2 quarts", "2 tbsp", "1 3/4 quarts"),   # review: "126 tbsp"
    ("1 quart", "1 tsp", "3 3/4 cups"),       # review: "191 tsp"
    ("1/2 gallon", "1/3 cup", "1 3/4 quarts"),  # review: "7.7 cups"
    ("1 gallon", "1/2 cup", "3 3/4 quarts"),  # review: "15.5 cups"
    ("1 quart", "1/3 cup", "3 2/3 cups"),
])
def test_a_little_off_a_quart_or_gallon_reads_like_the_list_and_never_up(start, used, left):
    tools.update_inventory("Milk", "add", quantity=start)
    tools.update_inventory("Milk", "use", quantity=used)
    assert _kitchen("Milk") == [left]


def test_plain_cups_rows_are_left_as_they_were():
    """GUARD: no quart or gallon in play, the Kitchen's old wording."""
    tools.update_inventory("Flour", "add", quantity="2 cups")
    tools.update_inventory("Flour", "use", quantity="1 tbsp")
    assert _kitchen("Flour") == ["31 tbsp"]


# ---------- the one-time conversion of rows main wrote ----------

def _set(table: str, item: str, qty: str, own: bool | None = None) -> None:
    conn = get_conn()
    extra = "" if own is None else (" AND source_weekly_plan_id IS NULL" if own else " AND source_weekly_plan_id IS NOT NULL")
    conn.execute(f"UPDATE {table} SET quantity = ? WHERE LOWER(item) = ?{extra}", (qty, item.lower()))
    conn.commit()
    conn.close()


def _upgrade() -> None:
    """Startup on a database last stamped by main (data version 6)."""
    conn = get_conn()
    conn.execute(f"PRAGMA user_version = {app_db._DATA_VERSION_QUARTS_ARE_CUPS - 1}")
    app_db._run_once_data_migrations(conn)
    conn.commit()
    conn.close()


def test_an_upgraded_list_reads_right_and_a_drop_afterwards_leaves_the_right_amount():
    plan, days = _week(
        {
            "Pudding": [{"item": "Milk", "qty": "1/2 gallon", "category": "dairy"}],
            "Custard": [{"item": "Cream", "qty": "1/2 gallon", "category": "dairy"}],
        },
        own={"Milk": "1/2 gallon"},
    )
    # As main stored them: the household's "1/2 gallon" with the plan's
    # half gallon bought as a whole one ("2 gallons", ledger "0.5 gallon");
    # a plan-only half gallon as "1 gallon"; a typed gallon and two cups
    # joined; a Kitchen row joined the same way.
    _set("grocery_items", "Milk", "2 gallons", own=True)
    _set("grocery_items", "Cream", "1 gallon", own=False)
    tools.add_grocery_item("Orange juice", quantity="1 gallon", category="dairy")
    _set("grocery_items", "Orange juice", "1 gallon + 2 cups")
    tools.update_inventory("Water", "add", quantity="1 gallon")
    _set("inventory_items", "Water", "1 gallon + 2 cups")
    conn = get_conn()
    assert sorted(r["quantity"] for r in conn.execute(
        "SELECT l.quantity FROM meal_plan_grocery_links l JOIN grocery_items g ON g.id = l.grocery_item_id "
        "WHERE LOWER(g.item) IN ('milk', 'cream')"
    )) == ["0.5 gallon", "0.5 gallon"]
    conn.close()

    _upgrade()
    # The household's own half gallon (as main would have left it, a whole
    # gallon: main's drop took back the one gallon it put on) plus the
    # plan's half gallon read the new way.
    assert _rows("Milk") == [("1 1/2 gallons", True)]
    assert _rows("Cream") == [("1/2 gallon", False)]
    assert _rows("Orange juice") == [("4 1/2 quarts", True)]
    assert _kitchen("Water") == ["4 1/2 quarts"]

    _drop(plan, days[0])
    assert _rows("Milk") == [("1 gallon", True)]  # not "24 cups", not "1 1/2 gallons"
    _drop(plan, days[1])
    assert _rows("Cream") == []

    # Run once: a second startup converts nothing again.
    _set("grocery_items", "Orange juice", "1 gallon + 2 cups")
    conn = get_conn()
    app_db._run_once_data_migrations(conn)
    conn.commit()
    conn.close()
    assert _rows("Orange juice") == [("1 gallon + 2 cups", True)]
    assert _rows("Milk") == [("1 gallon", True)]


def test_an_upgraded_quart_line_with_the_plans_quart_is_read_the_same():
    """A shape the old rounding already got right is left reading the same."""
    plan, days = _week({"Pancakes": [{"item": "Buttermilk", "qty": "1 quart", "category": "dairy"}]},
                       own={"Buttermilk": "1 quart"})
    _upgrade()
    assert _rows("Buttermilk") == [("2 quarts", True)]
    _drop(plan, days[0])
    assert _rows("Buttermilk") == [("1 quart", True)]


def test_scale_recipe_keeps_quarts_of_stock_in_quarts():
    """GUARD: cooking is not shopping (overnight/kitchen-hunt-quarts)."""
    tools.add_recipe("Soup", ingredients=[{"item": "chicken stock", "qty": "1 1/2 quarts"}], default_servings=4)
    scaled = tools.scale_recipe("Soup", 4)
    assert [i["qty"] for i in scaled["scaled_ingredients"]] == ["1.5 quarts"]


def _status(item: str, status: str) -> None:
    conn = get_conn()
    conn.execute("UPDATE grocery_items SET status = ? WHERE household_id = ? AND LOWER(item) = ? "
                 "AND source_weekly_plan_id IS NULL", (status, tools.household_id(), item.lower()))
    conn.commit()
    conn.close()


def test_upgrade_restates_lines_in_the_cart_or_taken_off_in_every_household(monkeypatch, caplog):
    """Review SHOULD-FIX 2 and 3: a household's line ticked into the cart or
    taken off the list still holds the plan's old whole gallon (untick or
    Undo brings it back as it was), in a second household as in the first;
    and one row the conversion can't read is logged and skipped, never a
    startup that fails every restart."""
    from app import households

    other = households.create_household("Second", "second-household-passphrase")
    other_id = other["id"] if isinstance(other, dict) else int(other)
    with tools.use_household(other_id):
        plan, days = _week(
            {
                "Pudding": [{"item": "Milk", "qty": "1/2 gallon", "category": "dairy"}],
                "Custard": [{"item": "Cream", "qty": "1/2 gallon", "category": "dairy"}],
            },
            own={"Milk": "1/2 gallon", "Cream": "1/2 gallon"},
        )
        # As main stored them, then ticked into the cart / taken off.
        _set("grocery_items", "Milk", "2 gallons", own=True)
        _set("grocery_items", "Cream", "2 gallons", own=True)
        _status("Milk", "in_cart")
        conn = get_conn()
        cream_id = conn.execute("SELECT id FROM grocery_items WHERE household_id = ? AND item = 'Cream' "
                                "AND source_weekly_plan_id IS NULL", (other_id,)).fetchone()["id"]
        conn.close()
        tools.remove_grocery_item(cream_id)
        tools.add_grocery_item("Oat milk", quantity="1 gallon", category="dairy")
        _set("grocery_items", "Oat milk", "1 gallon + 2 cups")
        tools.add_grocery_item("Poison", quantity="1 gallon", category="dairy")
        _set("grocery_items", "Poison", "1 gallon + 3 cups")

    real_fold = app_db._folded_volume

    def fold(qty, _q):
        if qty == "1 gallon + 3 cups":
            raise ValueError("a row this cannot read")
        return real_fold(qty, _q)

    monkeypatch.setattr(app_db, "_folded_volume", fold)
    with caplog.at_level("ERROR", logger="home_manager"):
        _upgrade()
    assert "left grocery_items row" in caplog.text

    with tools.use_household(other_id):
        conn = get_conn()
        got = {r["item"]: (r["quantity"], r["status"]) for r in conn.execute(
            "SELECT item, quantity, status FROM grocery_items WHERE household_id = ? AND source_weekly_plan_id IS NULL",
            (other_id,))}
        conn.close()
        assert got["Milk"] == ("1 1/2 gallons", "in_cart")
        assert got["Cream"] == ("1 1/2 gallons", "removed")
        assert got["Oat milk"] == ("4 1/2 quarts", "needed")  # converted past the poisoned row
        assert got["Poison"] == ("1 gallon + 3 cups", "needed")  # left exactly as it was
        conn = get_conn()
        ids = {r["item"]: r["id"] for r in conn.execute(
            "SELECT id, item FROM grocery_items WHERE household_id = ? AND item IN ('Milk', 'Cream') "
            "AND source_weekly_plan_id IS NULL", (other_id,))}
        conn.close()
        tools.mark_grocery_item(ids["Milk"], "needed")
        tools.undo_remove_grocery_item(ids["Cream"])
        _drop(plan, days[0])
        _drop(plan, days[1])
        assert _rows("Milk") == [("1 gallon", True)]  # review: "1 1/2 gallons"; main "1 gallon"
        assert _rows("Cream") == [("1 gallon", True)]
