"""
"Add potatoes" from the dish itself (Emily, 2026-09-13, Loop Board "Meal
screen: add what's missing ('add potatoes') from the dish itself, not via
chat", built together with "Meal screen: ingredients at a glance").

The server half: plates.suggest_additions fills the meal screen's "Add
something" sheet for THIS dish, plates.add_component writes the pick onto
the entry as a side — the same sides_json the plate pass uses, so the
grocery list, the Cooker card and the plan card already know how to read
it — and buys it if the week is approved; plates.remove_component is the
Undo. What's pinned here is what an addition has to reach: the card's
ingredients (scaled to who's eating), the card's steps (so the meal
screen's clock can time it), the grocery list (right quantity for 2 vs 4
eaters, once — never twice), and nothing left behind when the dish is
swapped away or the addition undone.

Also here, because the same change fixed it: a side's ingredients used to
FALL OFF the Cooker card on any plain night with attendance on record
(cooker.get_cooker_view replaced the folded list with scale_recipe's).
"""
import datetime

import pytest

from app import tools
from app.tools import plates


def _monday() -> datetime.date:
    today = datetime.date.today()
    return today - datetime.timedelta(days=today.weekday())


def _day(offset: int) -> str:
    return (_monday() + datetime.timedelta(days=offset)).isoformat()


MON, TUE = _day(0), _day(1)


def _shrimp():
    tools.add_recipe(
        "Garlic-Herb Shrimp with Roasted Broccolini",
        ingredients=[
            {"item": "Shrimp", "qty": "1 lb", "category": "meat/seafood"},
            {"item": "Broccolini", "qty": "2 bunches", "category": "produce"},
            {"item": "Lemon", "qty": "1", "category": "produce"},
        ],
        food_groups=["protein", "vegetable"],
        instructions=["Toss the broccolini with oil and roast.", "Sear the shrimp with the garlic."],
        default_servings=4, prep_time_minutes=10, cook_time_minutes=20,
    )


def _members(n: int):
    for name in ["Alex", "Sam", "Rae", "Jo"][:n]:
        tools.add_member(name)


def _plan(approve: bool = True) -> tuple[int, int]:
    plan_id = tools.create_weekly_plan(_monday().isoformat())["weekly_plan_id"]
    entry_id = tools.plan_meal(
        MON, "Garlic-Herb Shrimp with Roasted Broccolini", slot="dinner", weekly_plan_id=plan_id,
    )["entry_id"]
    if approve:
        tools.approve_weekly_plan(plan_id)
    return plan_id, entry_id


def _card(plan_id: int, entry_id: int) -> dict:
    return next(m for m in tools.get_cooker_view(plan_id)["meals"] if m["entry_id"] == entry_id)


def _list() -> dict:
    return {i["item"].lower(): i for i in tools.list_grocery_list()}


# ---------- the picker ----------

def test_the_picker_offers_a_starch_a_green_and_a_sauce_for_this_dish():
    _members(2)
    _shrimp()
    plan_id, entry_id = _plan()

    offered = tools.suggest_additions(entry_id, weekly_plan_id=plan_id)

    kinds = {o["kind"] for o in offered["options"]}
    assert kinds == {"starch", "green", "sauce"}
    names = [o["name"] for o in offered["options"]]
    assert "Roasted potatoes" in names and "Rice" in names and "Crusty bread" in names
    # The dish already has broccolini: broccoli beside it is not an addition.
    assert "Steamed broccoli" not in names
    assert "Green salad" in names
    # This plate is short a carb, so the starches come first.
    assert offered["options"][0]["kind"] == "starch"
    # Each row says what it costs in time, in words the sheet prints as-is.
    assert all(o["hint"] for o in offered["options"])
    assert offered["added"] == []


def test_a_low_carb_house_still_sees_the_starches_but_last():
    _members(2)
    _shrimp()
    plan_id, entry_id = _plan()

    offered = tools.suggest_additions(entry_id, eating_style="keto", weekly_plan_id=plan_id)

    kinds = [o["kind"] for o in offered["options"]]
    assert "starch" in kinds, "they asked to add something — the picker is theirs"
    assert kinds[-1] == "starch" and kinds[0] != "starch"


def test_a_low_carb_house_tapping_add_a_carb_sees_every_carb_first():
    # Emily, 2026-09-15: the card's "+ Add a carb" on her keto plan. The
    # sheet opened for a carb leads with the carbs — all of them, ahead
    # of the six-row cap, not the one starch the low-carb ordering left.
    _members(2)
    _shrimp()
    plan_id, entry_id = _plan()

    offered = tools.suggest_additions(entry_id, eating_style="keto", weekly_plan_id=plan_id, role="carb")

    kinds = [o["kind"] for o in offered["options"]]
    assert kinds[:3] == ["starch", "starch", "starch"]
    names = [o["name"] for o in offered["options"][:3]]
    assert set(names) == {"Roasted potatoes", "Rice", "Crusty bread"}
    assert len(offered["options"]) <= plates.MAX_ADDITIONS_OFFERED
    # Opened for a veg, the greens lead instead — the same rule, any part.
    offered = tools.suggest_additions(entry_id, weekly_plan_id=plan_id, role="vegetable")
    assert offered["options"][0]["kind"] == "green"


def test_something_already_added_is_not_offered_again():
    _members(2)
    _shrimp()
    plan_id, entry_id = _plan()
    tools.add_component(entry_id, key="roasted-potatoes", weekly_plan_id=plan_id)

    offered = tools.suggest_additions(entry_id, weekly_plan_id=plan_id)

    assert "Roasted potatoes" not in [o["name"] for o in offered["options"]]
    assert offered["added"] == ["Roasted potatoes"]


# ---------- adding ----------

def test_adding_potatoes_reaches_the_card_the_steps_and_the_list_for_two():
    _members(2)
    _shrimp()
    plan_id, entry_id = _plan()

    result = tools.add_component(entry_id, key="roasted-potatoes", weekly_plan_id=plan_id)

    assert result["status"] == "added"
    assert result["name"] == "Roasted potatoes"
    assert result["grocery_added"] == ["Yukon Gold potatoes"]

    card = _card(plan_id, entry_id)
    by_item = {i["item"]: i for i in card["ingredients"]}
    # On the card, scaled to the two people eating (written for four).
    assert by_item["Yukon Gold potatoes"]["qty"] == "1 lb"
    assert by_item["Yukon Gold potatoes"]["added"] is True
    assert by_item["Yukon Gold potatoes"]["from_side"] == "Roasted potatoes"
    assert "added" not in by_item["Shrimp"]
    # Its steps follow the dish's, named, so the clock can time them.
    assert card["instructions"][-2].startswith("Alongside — Roasted potatoes: Halve the potatoes")
    assert card["instructions"][-1].startswith("Alongside: Roast at 425°")
    side = card["sides"][0]
    assert side["minutes"] == 25 and side["added_by"] == "household"
    # And on the list, for two, against this entry.
    assert _list()["yukon gold potatoes"]["quantity"] == "1 lb"


def test_the_list_buys_for_four_when_four_are_eating():
    _members(4)
    _shrimp()
    plan_id, entry_id = _plan()

    tools.add_component(entry_id, key="roasted-potatoes", weekly_plan_id=plan_id)

    assert _list()["yukon gold potatoes"]["quantity"] == "2 lbs"
    assert {i["item"]: i["qty"] for i in _card(plan_id, entry_id)["ingredients"]}["Yukon Gold potatoes"] == "2 lbs"


def test_adding_the_same_thing_twice_adds_it_once():
    _members(2)
    _shrimp()
    plan_id, entry_id = _plan()

    first = tools.add_component(entry_id, key="roasted-potatoes", weekly_plan_id=plan_id)
    second = tools.add_component(entry_id, key="roasted-potatoes", weekly_plan_id=plan_id)

    assert first["status"] == "added"
    assert second["status"] == "already"
    assert second["grocery_added"] == []
    assert len(tools.get_plate_sides(entry_id)) == 1
    assert _list()["yukon gold potatoes"]["quantity"] == "1 lb", "not bought twice"
    assert sum(1 for i in _card(plan_id, entry_id)["ingredients"] if i["item"] == "Yukon Gold potatoes") == 1


def test_a_draft_buys_nothing_until_the_week_is_approved():
    _members(2)
    _shrimp()
    plan_id, entry_id = _plan(approve=False)

    result = tools.add_component(entry_id, key="rice", weekly_plan_id=plan_id)

    assert result["status"] == "added" and result["grocery_added"] == []
    assert "long grain rice" not in _list(), "a draft buys nothing"
    # It is on the card already, though.
    assert "Long grain rice" in {i["item"] for i in _card(plan_id, entry_id)["ingredients"]}

    tools.approve_weekly_plan(plan_id)

    # Bought at approval with everything else — and scaled to the two
    # eating (2 cups for four -> 1 cup for two), the recipe way.
    assert _list()["long grain rice"]["quantity"] == "1 cup"


def test_swapping_the_dish_away_takes_the_addition_and_its_shopping_with_it():
    _members(2)
    _shrimp()
    tools.add_recipe("Chili", ingredients=[{"item": "Beans", "qty": "2 tins", "category": "pantry"}],
                     food_groups=["protein", "vegetable", "carb"], default_servings=4)
    plan_id, entry_id = _plan()
    tools.add_component(entry_id, key="roasted-potatoes", weekly_plan_id=plan_id)
    assert "yukon gold potatoes" in _list()

    tools.swap_meal_in_plan(plan_id, MON, "Chili", slot="dinner", old_entry_id=entry_id)

    # The potatoes went with the shrimp — no orphan line on the list, and
    # the chili card carries no side it never asked for.
    assert "yukon gold potatoes" not in _list()
    assert "shrimp" not in _list()
    assert "beans" in _list()
    new_card = next(m for m in tools.get_cooker_view(plan_id)["meals"] if m["date"] == MON)
    assert new_card["meal"] == "Chili" and new_card["sides"] == []


def test_undo_takes_only_the_additions_own_lines_off_the_list():
    """The sauce shares a lemon with the shrimp. Undoing the sauce leaves
    the shrimp's lemon on the list at the shrimp's own amount."""
    _members(4)
    _shrimp()
    plan_id, entry_id = _plan()
    lemon_before = _list()["lemon"]["quantity"]
    tools.add_component(entry_id, key="garlic-yogurt-sauce", weekly_plan_id=plan_id)
    assert "plain greek yogurt" in _list()
    assert _list()["lemon"]["quantity"] != lemon_before, "the sauce's lemon was added on"

    result = tools.remove_component(entry_id, "Garlic yogurt sauce", weekly_plan_id=plan_id)

    assert result["status"] == "removed"
    after = _list()
    assert "plain greek yogurt" not in after
    assert after["lemon"]["quantity"] == lemon_before
    assert "shrimp" in after
    assert tools.get_plate_sides(entry_id) == []
    assert "Plain Greek yogurt" not in {i["item"] for i in _card(plan_id, entry_id)["ingredients"]}


def test_undoing_something_that_is_not_there_changes_nothing():
    _members(2)
    _shrimp()
    plan_id, entry_id = _plan()
    before = _list()

    result = tools.remove_component(entry_id, "Roasted potatoes", weekly_plan_id=plan_id)

    assert result["status"] == "gone"
    assert _list().keys() == before.keys()


def test_an_entry_from_another_week_is_not_this_weeks_to_change():
    _members(2)
    _shrimp()
    plan_id, entry_id = _plan()

    with pytest.raises(ValueError):
        tools.add_component(entry_id, key="rice", weekly_plan_id=plan_id + 1)
    with pytest.raises(ValueError):
        tools.add_component(entry_id, key="not-a-thing", weekly_plan_id=plan_id)
    with pytest.raises(ValueError):
        tools.add_component(entry_id, weekly_plan_id=plan_id)


# ---------- free text ----------

CAULI = {
    "name": "Cauliflower rice",
    "covers": ["vegetable"],
    "ingredients": [{"item": "Cauliflower", "qty": "1 head", "category": "produce"}],
    "instructions": ["Pulse the cauliflower to rice-sized bits.", "Sauté 5 minutes with oil and salt."],
    "minutes": 10,
}


def test_free_text_asks_the_side_writer_once_by_name():
    _members(2)
    _shrimp()
    plan_id, entry_id = _plan()
    calls = []

    def fake(context):
        calls.append(context)
        return [CAULI]

    result = tools.add_component(
        entry_id, text="cauliflower rice", context={"dislikes": ["olives"]},
        side_generator=fake, weekly_plan_id=plan_id,
    )

    assert result["status"] == "added" and result["name"] == "Cauliflower rice"
    assert result["note"] == ""
    assert len(calls) == 1
    assert calls[0]["requested"] == "cauliflower rice"
    assert calls[0]["missing"] == []
    assert calls[0]["dislikes"] == ["olives"]
    assert calls[0]["meal"] == "Garlic-Herb Shrimp with Roasted Broccolini"
    card = _card(plan_id, entry_id)
    assert card["instructions"][-2] == "Alongside — Cauliflower rice: Pulse the cauliflower to rice-sized bits."
    assert card["sides"][0]["minutes"] == 10
    assert "cauliflower" in _list()


def test_free_text_the_model_cannot_write_goes_on_as_typed_and_says_so():
    _members(2)
    _shrimp()
    plan_id, entry_id = _plan()

    def broken(context):
        raise RuntimeError("no model today")

    result = tools.add_component(entry_id, text="pickled onions", side_generator=broken, weekly_plan_id=plan_id)

    assert result["status"] == "added" and result["name"] == "Pickled onions"
    assert "as written" in result["note"]
    card = _card(plan_id, entry_id)
    assert "Pickled onions" in {i["item"] for i in card["ingredients"]}
    assert card["sides"][0]["minutes"] is None and card["sides"][0]["instructions"] == []
    assert "pickled onions" in _list()


def test_free_text_and_a_pick_are_one_or_the_other():
    _members(2)
    _shrimp()
    plan_id, entry_id = _plan()
    with pytest.raises(ValueError):
        tools.add_component(entry_id, key="rice", text="rice", weekly_plan_id=plan_id)


# ---------- the card keeps its sides (the bug the overview surfaced) ----------

def test_an_app_attached_side_survives_scaling_on_a_plain_night():
    """Before 2026-09-13 a plain night with attendance on record lost its
    side's ingredients from the Cooker card: scale_recipe's list replaced
    the folded one. The steps stayed, so the card said "Alongside — Green
    salad" with no romaine above it."""
    _members(2)
    _shrimp()
    plan_id, entry_id = _plan()
    plates.attach_sides(entry_id, [{
        "name": "Green salad", "covers": ["vegetable"],
        "ingredients": [{"item": "Romaine", "qty": "1 head", "category": "produce"}],
        "instructions": ["Tear and dress."], "minutes": 5,
    }], ["vegetable"])

    card = _card(plan_id, entry_id)

    assert card["default_servings"] == 2, "the recipe itself was scaled"
    by_item = {i["item"]: i for i in card["ingredients"]}
    assert "Romaine" in by_item
    # An app-attached side carries no servings, so it is left as written
    # and reads as part of the dish — no "added" mark.
    assert by_item["Romaine"]["qty"] == "1 head"
    assert "added" not in by_item["Romaine"]


def test_an_ingredient_already_in_the_pantry_is_marked_at_home():
    _members(2)
    _shrimp()
    plan_id, entry_id = _plan()
    tools.update_inventory_items([{"item": "Lemon", "quantity": "3"}], action="add")

    by_item = {i["item"]: i for i in _card(plan_id, entry_id)["ingredients"]}

    assert by_item["Lemon"].get("at_home") is True
    assert "at_home" not in by_item["Shrimp"]


# ---------- the routes the screen calls ----------

def test_the_routes_offer_add_and_undo(signed_in):
    _members(2)
    _shrimp()
    plan_id, entry_id = _plan()
    week = _monday().isoformat()

    offered = signed_in.get(f"/api/week/{week}/additions", params={"entry_id": entry_id})
    assert offered.status_code == 200
    assert "Roasted potatoes" in [o["name"] for o in offered.json()["options"]]

    added = signed_in.post(f"/api/week/{week}/add-component", json={"entry_id": entry_id, "key": "roasted-potatoes"})
    assert added.status_code == 200
    assert added.json()["status"] == "added"
    assert _list()["yukon gold potatoes"]["quantity"] == "1 lb"

    again = signed_in.post(f"/api/week/{week}/add-component", json={"entry_id": entry_id, "key": "roasted-potatoes"})
    assert again.json()["status"] == "already"

    undone = signed_in.post(f"/api/week/{week}/remove-component", json={"entry_id": entry_id, "name": "Roasted potatoes"})
    assert undone.status_code == 200 and undone.json()["status"] == "removed"
    assert "yukon gold potatoes" not in _list()

    missing = signed_in.post(f"/api/week/{week}/add-component", json={"entry_id": 999999, "key": "rice"})
    assert missing.status_code == 404
