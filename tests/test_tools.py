"""
Smoke tests over the core data paths in app/tools.py.

Not coverage — a thin line through the flows everything else is built on,
so a refactor that breaks planning, groceries or inventory fails here
instead of on someone's phone. These are the paths worth having green
before splitting tools.py into a package.
"""
import datetime

import pytest

from app import tools


def _today(offset_days: int = 0) -> str:
    return (datetime.date.today() + datetime.timedelta(days=offset_days)).isoformat()


def _week_start() -> str:
    """Monday of the current week — what create_weekly_plan expects."""
    today = datetime.date.today()
    return (today - datetime.timedelta(days=today.weekday())).isoformat()


# ---------- household ----------

def test_add_and_list_members():
    tools.add_member("Emily")
    tools.add_member("Marcus")
    names = [m["name"] for m in tools.list_members()]
    assert names == ["Emily", "Marcus"]


def test_dietary_restrictions_round_trip():
    tools.add_member("Emily")
    tools.set_member_dietary_restrictions("Emily", ["no shellfish"])
    emily = next(m for m in tools.list_members() if m["name"] == "Emily")
    assert "no shellfish" in emily["dietary_restrictions"]


def test_dislikes_accumulate_without_duplicating():
    tools.add_food_dislikes(["olives"])
    tools.add_food_dislikes(["olives", "beetroot"])
    dislikes = tools.get_meal_planning_setup_status()["dislikes"]
    assert sorted(dislikes) == ["beetroot", "olives"]


# ---------- groceries ----------

def test_add_grocery_item_appears_on_the_list():
    tools.add_grocery_item("milk", quantity="1 L", category="dairy")
    items = tools.list_grocery_list()
    assert [i["item"] for i in items] == ["milk"]


def test_same_item_consolidates_instead_of_duplicating():
    """The quantity-concatenation bug this guards against was a real one."""
    tools.add_grocery_item("flour", quantity="2 cups", category="pantry")
    tools.add_grocery_item("flour", quantity="1 cup", category="pantry")
    items = tools.list_grocery_list()
    assert len(items) == 1, "one line per item, not two"
    assert "3" in items[0]["quantity"]


def test_marking_an_item_purchased_removes_it_from_needed():
    tools.add_grocery_item("eggs", category="dairy")
    item_id = tools.list_grocery_list()[0]["id"]
    tools.mark_grocery_item(item_id, status="purchased")
    assert tools.list_grocery_list(status="needed") == []


def test_grocery_list_groups_into_store_sections():
    tools.add_grocery_item("spinach", category="produce")
    tools.add_grocery_item("chicken thighs", category="meat/seafood")
    sections = tools.get_grocery_list_by_section()
    assert any(s for s in sections.values() if s), "sections should not all be empty"


# ---------- recipes and planning ----------

def test_planning_a_meal_does_not_touch_the_grocery_list_on_its_own():
    """Nothing reaches the shopping list unless the household said so."""
    tools.add_recipe(
        "Tomato pasta",
        ingredients=[{"item": "pasta", "qty": "500 g"}, {"item": "passata", "qty": "1 jar"}],
        food_groups=["carb", "vegetable"],
    )
    tools.plan_meal(_today(1), "Tomato pasta", slot="dinner")
    assert tools.list_grocery_list() == []


def test_planning_a_meal_adds_ingredients_when_asked_to():
    """The one-off case: the person said yes, so the ingredients go on."""
    tools.add_recipe(
        "Tomato pasta",
        ingredients=[{"item": "pasta", "qty": "500 g"}, {"item": "passata", "qty": "1 jar"}],
        food_groups=["carb", "vegetable"],
    )
    tools.plan_meal(_today(1), "Tomato pasta", slot="dinner", add_ingredients_to_grocery_list=True)
    on_list = [i["item"] for i in tools.list_grocery_list()]
    assert "pasta" in on_list and "passata" in on_list


def test_approving_a_plan_is_what_fills_the_grocery_list():
    """A draft week leaves the list alone; approving it is the yes."""
    plan_id = tools.create_weekly_plan(_week_start())["weekly_plan_id"]
    tools.add_recipe("Chili", ingredients=[{"item": "beans", "qty": "1 tin"}])
    tools.plan_meal(_today(), "Chili", slot="dinner", weekly_plan_id=plan_id)
    assert tools.list_grocery_list() == [], "an unapproved draft must not put anything on the list"

    result = tools.approve_weekly_plan(plan_id)

    assert result["groceries_added"] == ["beans"]
    assert [i["item"] for i in tools.list_grocery_list()] == ["beans"]


def test_approving_a_plan_twice_does_not_double_the_quantities():
    plan_id = tools.create_weekly_plan(_week_start())["weekly_plan_id"]
    tools.add_recipe("Chili", ingredients=[{"item": "beans", "qty": "1 tin"}])
    tools.plan_meal(_today(), "Chili", slot="dinner", weekly_plan_id=plan_id)
    tools.approve_weekly_plan(plan_id)

    assert tools.approve_weekly_plan(plan_id)["groceries_added"] == []
    beans = [i for i in tools.list_grocery_list() if i["item"] == "beans"]
    assert len(beans) == 1
    assert "2" not in beans[0]["quantity"], f"quantity should still be one tin, got {beans[0]['quantity']!r}"


def test_approving_a_plan_skips_what_is_already_in_the_kitchen():
    tools.update_inventory("beans", action="add", quantity="2 tins", location="pantry")
    plan_id = tools.create_weekly_plan(_week_start())["weekly_plan_id"]
    tools.add_recipe("Chili", ingredients=[{"item": "beans", "qty": "1 tin"}])
    tools.plan_meal(_today(), "Chili", slot="dinner", weekly_plan_id=plan_id)

    result = tools.approve_weekly_plan(plan_id)

    assert result["already_have_skipped"] == ["beans"]
    assert tools.list_grocery_list() == []


def test_re_approving_does_not_add_what_was_skipped_as_already_owned():
    """
    The subtle one: an ingredient skipped because it was in the pantry
    leaves no record of having been considered. Re-approving later, once
    the pantry has emptied, must not quietly add it after all.
    """
    tools.update_inventory("beans", action="add", quantity="2 tins", location="pantry")
    plan_id = tools.create_weekly_plan(_week_start())["weekly_plan_id"]
    tools.add_recipe("Chili", ingredients=[{"item": "beans", "qty": "1 tin"}])
    tools.plan_meal(_today(), "Chili", slot="dinner", weekly_plan_id=plan_id)
    tools.approve_weekly_plan(plan_id)
    tools.update_inventory("beans", action="remove")  # ate them

    result = tools.approve_weekly_plan(plan_id)

    assert result["was_already_approved"] is True
    assert result["groceries_added"] == []
    assert tools.list_grocery_list() == [], "a second approve must not write to the list"


def test_approving_a_plan_that_does_not_exist_is_an_error():
    with pytest.raises(ValueError):
        tools.approve_weekly_plan(4242)


def test_swapping_a_meal_in_an_unapproved_draft_leaves_the_list_alone():
    plan_id = tools.create_weekly_plan(_week_start())["weekly_plan_id"]
    tools.add_recipe("Chili", ingredients=[{"item": "beans", "qty": "1 tin"}])
    tools.add_recipe("Soup", ingredients=[{"item": "stock", "qty": "1 L"}])
    tools.plan_meal(_today(), "Chili", slot="dinner", weekly_plan_id=plan_id)

    tools.swap_meal_in_plan(plan_id, _today(), "Soup", slot="dinner")

    assert tools.list_grocery_list() == []


def test_swapping_a_meal_in_an_approved_plan_swaps_it_on_the_list_too():
    plan_id = tools.create_weekly_plan(_week_start())["weekly_plan_id"]
    tools.add_recipe("Chili", ingredients=[{"item": "beans", "qty": "1 tin"}])
    tools.add_recipe("Soup", ingredients=[{"item": "stock", "qty": "1 L"}])
    tools.plan_meal(_today(), "Chili", slot="dinner", weekly_plan_id=plan_id)
    tools.approve_weekly_plan(plan_id)

    tools.swap_meal_in_plan(plan_id, _today(), "Soup", slot="dinner")

    on_list = [i["item"] for i in tools.list_grocery_list()]
    assert "stock" in on_list, "the new meal's ingredients should replace the old ones"
    assert "beans" not in on_list, "the swapped-out meal's ingredients should come off"


def test_planned_meal_shows_up_in_the_plan():
    tools.add_recipe("Chili", ingredients=[{"item": "beans", "qty": "1 tin"}])
    tools.plan_meal(_today(1), "Chili", slot="dinner")
    planned = [e["meal"] for e in tools.get_meal_plan(days_ahead=7)]
    assert "Chili" in planned


def test_checking_a_meal_off_records_it_as_cooked():
    """Plan a week, cook one dinner, and see the plan's progress move."""
    plan_id = tools.create_weekly_plan(_week_start())["weekly_plan_id"]
    tools.add_recipe("Soup", ingredients=[{"item": "stock", "qty": "1 L"}])
    tools.plan_meal(_today(), "Soup", slot="dinner", weekly_plan_id=plan_id)

    entry = tools.get_cooker_view(plan_id)["meals"][0]
    assert entry["cooked_status"] == "pending"

    tools.check_off_meal(entry["entry_id"], status="done")
    assert tools.get_cooker_view(plan_id)["meals"][0]["cooked_status"] == "done"
    assert tools.get_plan_progress(plan_id)["meals_done"] == 1


def test_scaling_a_recipe_doubles_its_quantities():
    tools.add_recipe(
        "Rice bowl",
        ingredients=[{"item": "rice", "qty": "2 cups"}],
        default_servings=4,
    )
    scaled = tools.scale_recipe("Rice bowl", target_servings=8)
    assert scaled["base_servings"] == 4
    assert scaled["target_servings"] == 8
    rice = next(i for i in scaled["scaled_ingredients"] if i["item"] == "rice")
    assert "4" in rice["qty"], f"2 cups doubled should read as 4, got {rice['qty']!r}"


# ---------- inventory ----------

def test_adding_and_using_up_inventory():
    tools.update_inventory("rotisserie chicken", action="add", quantity="1", location="fridge")
    assert any(i["item"] == "rotisserie chicken" for i in tools.get_inventory())
    tools.update_inventory("rotisserie chicken", action="use")
    remaining = [i for i in tools.get_inventory() if i["item"] == "rotisserie chicken"]
    assert not remaining or float(remaining[0].get("quantity") or 0) == 0


def test_inventory_groups_by_location():
    tools.update_inventory("peas", action="add", quantity="1 bag", location="freezer")
    groups = tools.get_inventory_by_location()["locations"]
    freezer = next(g for g in groups if g["location"] == "freezer")
    assert [i["item"] for i in freezer["items"]] == ["peas"]


# ---------- facts and memory ----------

def test_facts_can_be_added_and_removed():
    fact = tools.add_fact("general", "We eat late on Fridays")
    assert any(f["text"] == "We eat late on Fridays" for f in tools.get_facts())
    tools.delete_fact(fact["id"])
    assert not any(f["text"] == "We eat late on Fridays" for f in tools.get_facts())


# ---------- share links ----------

def test_share_link_is_stable_and_unguessable():
    first = tools.get_or_create_share_link()
    second = tools.get_or_create_share_link()
    assert first["token"] == second["token"], "the household's link should not rotate on every call"
    assert len(first["token"]) >= 20, "token must be long enough not to be guessable"


def test_member_share_link_resolves_to_that_member():
    tools.add_member("Marcus")
    link = tools.get_or_create_member_share_link("Marcus")
    assert tools.resolve_member_share_link(link["token"])["member_name"] == "Marcus"


# ---------- self-service reset ----------

def test_clearing_the_week_takes_its_ingredients_off_the_grocery_list():
    """The whole point of the reset: no orphaned ingredients left behind."""
    plan_id = tools.create_weekly_plan(_week_start())["weekly_plan_id"]
    tools.add_recipe("Tomato pasta", ingredients=[{"item": "passata", "qty": "1 jar"}])
    tools.plan_meal(_today(), "Tomato pasta", slot="dinner", weekly_plan_id=plan_id)
    tools.approve_weekly_plan(plan_id)  # approving is what puts the plan's ingredients on the list
    tools.add_grocery_item("dish soap", category="other")  # asked for directly, not by the plan

    result = tools.clear_weekly_plan()

    assert result["meals_cleared"] == 1
    assert tools.get_meal_plan(days_ahead=7) == []
    on_list = [i["item"] for i in tools.list_grocery_list()]
    assert "passata" not in on_list, "the plan's ingredient should have come off with it"
    assert "dish soap" in on_list, "an item a person added must survive a plan reset"


def test_clearing_the_week_keeps_the_plan_itself():
    """Emptied, not deleted — the week's dates and constraints survive."""
    plan_id = tools.create_weekly_plan(_week_start())["weekly_plan_id"]
    tools.set_week_constraints("out Thursday", weekly_plan_id=plan_id)
    tools.add_recipe("Chili", ingredients=[{"item": "beans", "qty": "1 tin"}])
    tools.plan_meal(_today(), "Chili", slot="dinner", weekly_plan_id=plan_id)

    assert tools.clear_weekly_plan()["weekly_plan_id"] == plan_id
    plan = tools.get_weekly_plan()
    assert plan["weekly_plan_id"] == plan_id
    assert plan["constraints_notes"] == "out Thursday"


def test_clearing_the_week_leaves_an_already_purchased_item_alone():
    """Reversal must not yank something the shopper has already bought."""
    plan_id = tools.create_weekly_plan(_week_start())["weekly_plan_id"]
    tools.add_recipe("Soup", ingredients=[{"item": "stock", "qty": "1 L"}])
    tools.plan_meal(_today(), "Soup", slot="dinner", weekly_plan_id=plan_id)
    tools.approve_weekly_plan(plan_id)
    stock_id = next(i["id"] for i in tools.list_grocery_list() if i["item"] == "stock")
    tools.mark_grocery_item(stock_id, status="purchased")

    tools.clear_weekly_plan()

    assert [i["item"] for i in tools.list_grocery_list(status="purchased")] == ["stock"]


def test_clearing_the_week_with_no_plan_is_a_no_op():
    result = tools.clear_weekly_plan()
    assert result["weekly_plan_id"] is None
    assert result["meals_cleared"] == 0


def test_reset_preview_counts_what_would_go():
    plan_id = tools.create_weekly_plan(_week_start())["weekly_plan_id"]
    tools.add_recipe("Chili", ingredients=[{"item": "beans", "qty": "1 tin"}])
    tools.plan_meal(_today(), "Chili", slot="dinner", weekly_plan_id=plan_id)
    tools.approve_weekly_plan(plan_id)
    tools.add_grocery_item("dish soap", category="other")

    preview = tools.get_reset_preview()

    assert preview["weekly_plan_id"] == plan_id
    assert preview["meal_count"] == 1
    # "beans" from the plan plus the directly-added "dish soap"
    assert preview["grocery_count"] == 2
