"""
"Shop for tonight" names what tonight's dinner needs.

Found 2026-10-13 driving a demo household: on a chili night, Today's "Shop
for tonight · by 5:55" named "bell pepper, broccoli, cherry tomatoes…" —
the first three lines of the whole list, alphabetically, none of them for
the chili. The deadline was already read off the per-meal ledger
(test_shop_move_for_tonight.py); the NAMES were still read off the list.

The count stays the whole list on purpose (that file's
test_the_count_is_the_whole_list_even_though_the_deadline_is_one_cooks):
once there is a shop to do you are buying the lot. Only which things are
named changes.
"""
from __future__ import annotations

import datetime
from datetime import time

from conftest import household_today

from app import tools


def _at(hour: int, day: datetime.date) -> datetime.datetime:
    return datetime.datetime.combine(day, time(hour, 0))


def _setup():
    today = household_today()
    for n in ("Emily", "Vineeth"):
        tools.add_member(n)
    tools.add_recipe(
        "Chili",
        ingredients=[
            {"item": "Ground beef", "qty": "1 lb", "category": "meat-seafood"},
            {"item": "Kidney beans", "qty": "1 can", "category": "pantry"},
        ],
        prep_time_minutes=15, cook_time_minutes=30, default_servings=2,
    )
    tools.add_recipe(
        "Veggie Stir Fry",
        ingredients=[
            {"item": "Bell pepper", "qty": "2", "category": "produce"},
            {"item": "Broccoli", "qty": "1 head", "category": "produce"},
        ],
        prep_time_minutes=10, cook_time_minutes=10, default_servings=2,
    )
    week_start = (today - datetime.timedelta(days=2)).isoformat()
    plan_id = tools.create_weekly_plan(week_start)["weekly_plan_id"]
    tools.plan_meal(today.isoformat(), "Chili", slot="dinner", weekly_plan_id=plan_id,
                    add_ingredients_to_grocery_list=True)
    # Another night's food, and a hand-added want, both alphabetically
    # ahead of tonight's.
    tools.plan_meal((today + datetime.timedelta(days=3)).isoformat(), "Veggie Stir Fry",
                    slot="dinner", weekly_plan_id=plan_id,
                    add_ingredients_to_grocery_list=True)
    tools.add_grocery_item("Apples", quantity="6", category="produce")
    return today


def _shop(now):
    hits = [m for m in tools.today_moves(now=now)["moves"] if m["kind"] == "shop"]
    return hits[0] if hits else None


def _named(move) -> list[str]:
    return [i for stop in move["stops"] for i in stop["items"]]


def test_shop_for_tonight_names_tonights_food_not_the_list_alphabetically():
    """CATCH. Before: "Apples, Bell pepper, Broccoli…"."""
    today = _setup()
    shop = _shop(_at(8, today))

    assert shop["title"] == "Shop for tonight"
    assert sorted(_named(shop)) == ["Ground beef", "Kidney beans"], shop["stops"]
    assert set(shop["meta"].rstrip("…").split(", ")) == {"Ground beef", "Kidney beans"}


def test_other_nights_and_hand_added_lines_are_never_named_but_still_counted():
    """CATCH for the names; GUARD for the count, which stays the whole list."""
    today = _setup()
    shop = _shop(_at(8, today))

    named = _named(shop) + shop["meta"].rstrip("…").split(", ")
    for other in ("Apples", "Bell pepper", "Broccoli"):
        assert other not in named
    assert shop["detail"].startswith("5 items · by ")
    assert shop["meta"].endswith("…"), "there is more on the list than is named"


def test_a_stop_with_nothing_for_tonight_names_nothing_and_comes_last():
    """CATCH. Stores are a trip's stops; the one tonight needs leads, and a
    store holding only other nights' food names none of it."""
    today = _setup()
    # Sorts ahead of the ground beef, so the Farm stand is the store the
    # list names FIRST — the order this has to overturn.
    tools.add_grocery_item("Bacon", quantity="1 pack", category="meat-seafood")
    for row in tools.list_grocery_list(status="needed"):
        store = "Butcher" if row["item"] == "Ground beef" else "Metro"
        if row["item"] in ("Apples", "Bell pepper", "Bacon"):
            store = "Farm stand"
        tools.set_grocery_item_store(row["id"], store)

    stops = _shop(_at(8, today))["stops"]

    by_store = {s["store"]: s for s in stops}
    assert by_store["Farm stand"]["items"] == []
    assert by_store["Farm stand"]["count"] == 3
    assert by_store["Metro"]["items"] == ["Kidney beans"]
    assert by_store["Metro"]["count"] == 2
    assert stops[-1]["store"] == "Farm stand"
