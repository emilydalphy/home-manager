"""All set counts in a person's words: recipes, not cooks; ingredients, not
to buy (Emily, 2026-09-13).

On her phone, over "6 MEALS · 6 COOKS · 53 TO BUY": "the '6 cooks' is
confusing language. can we say it's recipes instead, more intuitive" and
"53 to buy feels intimidating, can we say 53 ingredients".

Two things follow from the word "recipes" that a relabel alone would get
wrong. A dish cooked on two nights is ONE recipe (and two cooks), so
week_receipt counts distinct dishes and keeps `cooks` apart for the week
card's own "4 cooks, 3 made ahead". And "ingredients" is the buy list as
it stands — a spice waiting unticked in "Spices this week" and last
week's leftover waiting for keep-or-drop are not on it — so the number
on the tile is the number of lines "Open the list" opens on.
"""
from __future__ import annotations

import datetime
from pathlib import Path

from app import tools
from app.db import get_conn

TODAY = datetime.date.today()
WEEK_START = (TODAY - datetime.timedelta(days=2)).isoformat()
ISO_YESTERDAY = (TODAY - datetime.timedelta(days=1)).isoformat()
ISO_TODAY = TODAY.isoformat()
ISO_TOMORROW = (TODAY + datetime.timedelta(days=1)).isoformat()

REPO = Path(__file__).resolve().parent.parent
SHELL_JS = (REPO / "static" / "shell.js").read_text(encoding="utf-8")


def _fn(name: str) -> str:
    start = SHELL_JS.index("  function %s(" % name)
    return SHELL_JS[start:SHELL_JS.index("\n  }\n", start)]


def _plan() -> int:
    return tools.create_weekly_plan(WEEK_START)["weekly_plan_id"]


def _receipt(plan_id: int) -> dict:
    return tools.week_receipt(tools.get_week_menu()["days"], plan_id)


def _set_status(item: str, status: str) -> None:
    conn = get_conn()
    conn.execute(
        "UPDATE grocery_items SET status = ? WHERE household_id = ? AND item = ?",
        (status, tools.household_id(), item),
    )
    conn.commit()
    conn.close()


# ---------- recipes: different dishes, not cook nights ----------

def test_a_dish_cooked_on_two_nights_is_one_recipe_and_two_cooks():
    tools.add_recipe("Chili", ingredients=[{"item": "beans", "qty": "1 tin"}])
    tools.add_recipe("Tacos", ingredients=[{"item": "tortillas", "qty": "8"}])
    plan_id = _plan()
    tools.plan_meal(ISO_YESTERDAY, "Chili", slot="dinner", weekly_plan_id=plan_id)
    tools.plan_meal(ISO_TODAY, "Tacos", slot="dinner", weekly_plan_id=plan_id)
    tools.plan_meal(ISO_TOMORROW, "Chili", slot="dinner", weekly_plan_id=plan_id)

    receipt = _receipt(plan_id)

    assert receipt["meals"] == 3
    assert receipt["cooks"] == 3, "the week card's count of the week's work is untouched"
    assert receipt["recipes"] == 2, "Chili twice is one recipe"
    assert receipt["title"].startswith("3 meals, 2 recipes")


def test_a_reheat_and_a_takeout_night_are_meals_but_not_recipes():
    tools.add_recipe("Chili", ingredients=[{"item": "beans", "qty": "1 tin"}])
    plan_id = _plan()
    tools.plan_meal(ISO_YESTERDAY, "Chili", slot="dinner", weekly_plan_id=plan_id)
    tools.plan_meal(ISO_TODAY, "Leftovers from last night", slot="dinner", weekly_plan_id=plan_id)
    tools.plan_meal(ISO_TOMORROW, "Takeout night", slot="dinner", weekly_plan_id=plan_id)

    receipt = _receipt(plan_id)

    assert receipt["meals"] == 3
    assert receipt["recipes"] == 1
    assert receipt["title"].startswith("3 meals, 1 recipe,")


def test_the_title_says_ingredients_not_things():
    tools.add_recipe("Chili", ingredients=[{"item": "beans", "qty": "1 tin"}])
    plan_id = _plan()
    tools.plan_meal(ISO_TODAY, "Chili", slot="dinner", weekly_plan_id=plan_id)
    for item in ("beans", "rice", "onions"):
        tools.add_grocery_item(item)

    receipt = _receipt(plan_id)

    assert receipt["title"] == "1 meal, 1 recipe, one list of 3 ingredients."
    assert "cook" not in receipt["title"] and "things" not in receipt["title"]


# ---------- ingredients: the lines actually on the buy list ----------

def test_the_ingredient_count_is_the_buy_list_without_waiting_spices_or_leftovers():
    """A spice sitting unticked under "Spices this week" (status 'spice')
    and a line from last week waiting for keep-or-drop ('carried') are
    not on the list "Open the list" opens on, so they are not in the
    number either — until a tick or a Keep moves them onto it."""
    tools.add_recipe("Chili", ingredients=[{"item": "beans", "qty": "1 tin"}])
    plan_id = _plan()
    tools.plan_meal(ISO_TODAY, "Chili", slot="dinner", weekly_plan_id=plan_id)
    for item in ("beans", "rice", "onions", "cumin", "chicken thighs"):
        tools.add_grocery_item(item)
    _set_status("cumin", "spice")
    _set_status("chicken thighs", "carried")

    receipt = _receipt(plan_id)

    assert receipt["list_count"] == 3
    assert "one list of 3 ingredients" in receipt["title"]

    # Ticking the spice puts it on the list, and so into the number.
    _set_status("cumin", "needed")
    assert _receipt(plan_id)["list_count"] == 4


def test_the_menu_payload_carries_recipes_for_the_screen():
    tools.add_recipe("Chili", ingredients=[{"item": "beans", "qty": "1 tin"}])
    plan_id = _plan()
    tools.plan_meal(ISO_TODAY, "Chili", slot="dinner", weekly_plan_id=plan_id)
    tools.approve_weekly_plan(plan_id)

    receipt = tools.get_week_menu()["receipt"]

    assert receipt is not None
    assert receipt["recipes"] == 1 and receipt["cooks"] == 1


# ---------- the tiles ----------

def test_the_all_set_tiles_read_meals_and_recipes():
    """"ingredients" was the third tile until 2026-09-18 (Emily's "All set is
    one thing" card drops it — the list is the next screen)."""
    allset = _fn("allSetStepHtml")
    labels = [line for line in allset.splitlines() if "nums.push(" in line]
    assert [l.split("label: '")[1].split("'")[0] for l in labels] == ["meals", "recipes"]
    assert "receipt.recipes" in allset, "the tile shows the distinct-dish count, not the cook nights"
    assert "'cooks'" not in allset and "'to buy'" not in allset
