"""
A grocery line nobody gave an aisle still shops in the right one (found
2026-10-15, driving the App Store demo household).

Chat's add_recipe leaves `category` optional, and the demo household's
recipes carry none, so approving a week put every line — bell pepper,
chicken breast, parmesan — under Other on Shop. The same missing word kept
a chicken thigh out of the defrost ask, which picks meat by category.
recipes.store_section fills the gap from the item's name where the aisle
is used (never saved onto the recipe); an explicit
category is the recipe's own word and stands. The regulars ticked on
"Before you shop" had the same hole: saved as 'other', so milk, butter and
eggs went on the list under Other (staples._line_category).
"""
import datetime
import json

from app import tools
from app.db import get_conn
from app.tools import defrost
from app.tools._shared import household_id
from conftest import household_today


def _next_week() -> str:
    today = household_today()
    return (today - datetime.timedelta(days=today.weekday()) + datetime.timedelta(days=7)).isoformat()


def _sections() -> dict:
    return {i["item"].lower(): i["category"] for i in tools.list_grocery_list()}


def _save_legacy_recipe(name: str, ingredients: list[dict]) -> None:
    """A recipe as saved before 2026-10-15: lines with no category at all."""
    tools.add_recipe(name, ingredients=[{"item": "placeholder", "qty": "1"}])
    conn = get_conn()
    conn.execute(
        "UPDATE recipes SET ingredients_json = ? WHERE household_id = ? AND name = ?",
        (json.dumps(ingredients), household_id(), name),
    )
    conn.commit()
    conn.close()


def test_a_chat_recipe_saved_without_categories_shops_by_aisle():
    tools.add_recipe("Stir Fry", ingredients=[
        {"item": "Chicken breast", "qty": "1 lb"},
        {"item": "Bell pepper", "qty": "1"},
        {"item": "Soy sauce", "qty": "2 tbsp"},
    ])
    week = _next_week()
    plan = tools.create_weekly_plan(week)
    tools.plan_meal(tools._week_dates(week)[1], "Stir Fry", slot="dinner", weekly_plan_id=plan["weekly_plan_id"])
    tools.approve_weekly_plan(plan["weekly_plan_id"])

    sections = _sections()
    assert (sections["chicken breast"], sections["bell pepper"], sections["soy sauce"]) == ("meat/seafood", "produce", "pantry")
    # The guess is read, never written onto the recipe.
    assert all("category" not in i for i in tools.get_recipe("Stir Fry")["ingredients"])


def test_an_explicit_category_is_kept():
    from app.tools import recipes
    assert recipes.store_section({"item": "Chicken stock cubes", "qty": "1 box", "category": "other"}) == "other"


def test_names_that_fool_a_substring_are_not_meat_or_dairy():
    from app.tools import recipes
    assert not defrost._is_meat_ingredient({"item": "Minced garlic", "qty": "2 tsp"})
    assert not defrost._is_meat_ingredient({"item": "Graham crackers", "qty": "1 box"})
    assert defrost._is_meat_ingredient({"item": "Ham", "qty": "1 lb"})
    assert recipes.store_section({"item": "Butternut squash"}) == "produce"
    assert recipes.store_section({"item": "Peanut butter"}) == "pantry"
    assert recipes.store_section({"item": "Black pepper"}) == "pantry"
    assert recipes.store_section({"item": "Cornstarch"}) == "pantry"


def test_approving_a_week_of_an_old_uncategorised_recipe_files_lines_by_aisle():
    _save_legacy_recipe("Old Stir Fry", [
        {"item": "Chicken breast", "qty": "1 lb"},
        {"item": "Broccoli", "qty": "1 head"},
        {"item": "Parmesan", "qty": "1 block"},
    ])
    week = _next_week()
    plan = tools.create_weekly_plan(week)
    tools.plan_meal(tools._week_dates(week)[2], "Old Stir Fry", slot="dinner", weekly_plan_id=plan["weekly_plan_id"])
    tools.approve_weekly_plan(plan["weekly_plan_id"])

    sections = _sections()
    assert sections["chicken breast"] == "meat/seafood"
    assert sections["broccoli"] == "produce"
    assert sections["parmesan"] == "dairy"


def test_an_old_uncategorised_chicken_is_asked_about_at_the_defrost_step():
    _save_legacy_recipe("Old Skewers", [{"item": "Chicken thighs", "qty": "1 lb"}, {"item": "Rice", "qty": "1 cup"}])
    week = _next_week()
    plan = tools.create_weekly_plan(week)
    tools.plan_meal(tools._week_dates(week)[3], "Old Skewers", slot="dinner", weekly_plan_id=plan["weekly_plan_id"])

    assert [i["item"] for i in defrost.meat_items_for_plan(plan["weekly_plan_id"])] == ["Chicken thighs"]



def test_a_side_written_without_categories_shops_by_name():
    from app.tools import plates
    lines = plates.side_ingredients([{"name": "Green salad", "ingredients": [
        {"item": "Mixed greens", "qty": "1 bag"}, {"item": "Feta", "qty": "4 oz", "category": "dairy"},
    ]}])
    assert [(l["item"], l["category"]) for l in lines] == [("Mixed greens", "produce"), ("Feta", "dairy")]


# ---------- regulars: a staple nobody gave an aisle ----------

def test_regulars_ticked_before_you_shop_go_on_the_list_in_their_aisles():
    tools.add_regulars(["Milk", "Butter", "Tea", "Dish soap"])
    sections = _sections()
    assert sections["milk"] == "dairy"
    assert sections["butter"] == "dairy"
    assert sections["tea"] == "pantry"
    # No aisle for household goods on the list: it stays Other.
    assert sections["dish soap"] == "other"


def test_a_staple_with_its_own_aisle_keeps_it():
    tools.add_staple("Oat milk", category="pantry", running_low=True)
    tools.sync_due_staples()
    assert _sections()["oat milk"] == "pantry"
