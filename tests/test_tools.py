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


def _add_adult(name: str) -> None:
    """
    An adult, spelled the way onboarding actually spells it — "Adult",
    capitalised. Worth going through the real two calls rather than an
    INSERT: the casing is the whole point of
    test_adults_are_found_whatever_the_casing_of_age_group.
    """
    tools.add_member(name)
    tools.set_member_age_group(name, "Adult")


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


def test_picking_tonights_dinner_only_adds_groceries_when_asked_to():
    """
    The Today card's dinner pick is a tap, not a conversation — the card
    asks first, and passes the answer through. Both answers plan the meal.
    """
    tools.add_recipe("Chili", ingredients=[{"item": "beans", "qty": "1 tin"}])

    said_no = tools.resolve_needs_you_dinner(_today(), "Chili")
    assert said_no["groceries_added"] == []
    assert tools.list_grocery_list() == []
    assert "Chili" in [e["meal"] for e in tools.get_meal_plan(days_ahead=7)]

    said_yes = tools.resolve_needs_you_dinner(
        _today(1), "Chili", add_ingredients_to_grocery_list=True
    )
    assert said_yes["groceries_added"] == ["beans"]
    assert [i["item"] for i in tools.list_grocery_list()] == ["beans"]


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


# ---------- Plan the Week: approval, the receipt, and the promise ----------
# design_handoff_plan_the_week/BUILD_ORDER.md stage 1. The "a draft adds
# nothing / approval adds once / a second approval adds nothing" tests
# already live further up this file; these cover what stage 1 adds on top —
# who approved, when, and the two numbers the draft promises and the receipt
# then reports.

def test_approval_records_who_said_yes_and_when():
    plan_id = tools.create_weekly_plan(_week_start())["weekly_plan_id"]
    tools.add_recipe("Chili", ingredients=[{"item": "beans", "qty": "1 tin"}])
    tools.plan_meal(_today(), "Chili", slot="dinner", weekly_plan_id=plan_id)

    result = tools.approve_weekly_plan(plan_id, approved_by="Emily")

    assert result["approved_by"] == "Emily"
    assert result["approved_at"]
    plan = tools.get_weekly_plan(plan_id)
    assert plan["approved_by"] == "Emily"
    assert plan["approved_at"]


def test_a_draft_has_no_approver_recorded():
    plan_id = tools.create_weekly_plan(_week_start())["weekly_plan_id"]
    plan = tools.get_weekly_plan(plan_id)
    assert plan["status"] == "draft"
    assert plan["approved_by"] == ""
    assert plan["approved_at"] is None


def test_re_approving_keeps_the_original_approver():
    """
    The receipt names whoever actually settled the week. A second, no-op
    approval must not quietly re-attribute it to whoever tapped last.
    """
    plan_id = tools.create_weekly_plan(_week_start())["weekly_plan_id"]
    tools.add_recipe("Chili", ingredients=[{"item": "beans", "qty": "1 tin"}])
    tools.plan_meal(_today(), "Chili", slot="dinner", weekly_plan_id=plan_id)
    tools.approve_weekly_plan(plan_id, approved_by="Emily")

    result = tools.approve_weekly_plan(plan_id, approved_by="Marcus")

    assert result["was_already_approved"] is True
    assert result["approved_by"] == "Emily"
    assert tools.get_weekly_plan(plan_id)["approved_by"] == "Emily"


def test_the_draft_promise_matches_what_approval_actually_adds():
    """
    The number the draft screen promises ("22 items") and the number the
    receipt reports have to be the same number — they come from one shared
    query on purpose. Two recipes wanting the same ingredient count once,
    since the grocery list merges them onto a single line.
    """
    plan_id = tools.create_weekly_plan(_week_start())["weekly_plan_id"]
    tools.add_recipe("Chili", ingredients=[{"item": "beans", "qty": "1 tin"}, {"item": "onions", "qty": "2"}])
    tools.add_recipe("Soup", ingredients=[{"item": "onions", "qty": "1"}, {"item": "stock", "qty": "1 l"}])
    tools.plan_meal(_today(), "Chili", slot="dinner", weekly_plan_id=plan_id)
    tools.plan_meal(_today(1), "Soup", slot="dinner", weekly_plan_id=plan_id)

    preview = tools.preview_plan_grocery_impact(plan_id)
    assert preview["would_add_count"] == 3, "beans, onions, stock — onions counted once"

    result = tools.approve_weekly_plan(plan_id, approved_by="Emily")
    assert result["groceries_added_count"] == preview["would_add_count"]
    assert len(tools.list_grocery_list()) == 3


def test_the_promise_counts_kitchen_items_separately_and_writes_nothing():
    tools.update_inventory("beans", action="add", quantity="2 tins", location="pantry")
    plan_id = tools.create_weekly_plan(_week_start())["weekly_plan_id"]
    tools.add_recipe("Chili", ingredients=[{"item": "beans", "qty": "1 tin"}, {"item": "onions", "qty": "2"}])
    tools.plan_meal(_today(), "Chili", slot="dinner", weekly_plan_id=plan_id)

    preview = tools.preview_plan_grocery_impact(plan_id)

    assert preview["would_add_count"] == 1
    assert preview["already_have_count"] == 1
    assert tools.list_grocery_list() == [], "previewing must never write to the list"


def test_the_receipt_counts_survive_a_reload():
    """
    Neither number is recoverable after the fact, so approval persists both
    — otherwise the receipt starts lying the moment the page is refreshed.
    """
    tools.update_inventory("beans", action="add", quantity="2 tins", location="pantry")
    plan_id = tools.create_weekly_plan(_week_start())["weekly_plan_id"]
    tools.add_recipe("Chili", ingredients=[{"item": "beans", "qty": "1 tin"}, {"item": "onions", "qty": "2"}])
    tools.plan_meal(_today(), "Chili", slot="dinner", weekly_plan_id=plan_id)
    tools.approve_weekly_plan(plan_id, approved_by="Emily")

    plan = tools.get_weekly_plan(plan_id)
    assert plan["approved_grocery_added"] == 1
    assert plan["approved_grocery_skipped"] == 1


def test_the_meals_screen_gets_the_approval_state_it_needs():
    plan_id = tools.create_weekly_plan(_week_start())["weekly_plan_id"]
    tools.add_recipe("Chili", ingredients=[{"item": "beans", "qty": "1 tin"}])
    tools.plan_meal(_today(), "Chili", slot="dinner", weekly_plan_id=plan_id)

    draft = tools.get_week_menu()
    assert draft["status"] == "draft"
    assert draft["grocery_preview"]["would_add_count"] == 1

    tools.approve_weekly_plan(plan_id, approved_by="Emily")

    approved = tools.get_week_menu()
    assert approved["status"] == "approved"
    assert approved["approved_by"] == "Emily"
    assert approved["approved_grocery_added"] == 1
    # No preview once approved — the number would always be zero, and reads
    # as a promise of nothing.
    assert approved["grocery_preview"] is None


def test_the_other_adult_is_who_the_receipt_names():
    _add_adult("Emily")
    _add_adult("Marcus")
    tools.add_member("Sam")
    tools.set_member_age_group("Sam", "Child")
    plan_id = tools.create_weekly_plan(_week_start())["weekly_plan_id"]
    tools.add_recipe("Chili", ingredients=[{"item": "beans", "qty": "1 tin"}])
    tools.plan_meal(_today(), "Chili", slot="dinner", weekly_plan_id=plan_id)
    tools.approve_weekly_plan(plan_id, approved_by="Emily")

    assert tools.get_week_menu()["other_adults"] == ["Marcus"]


def test_adults_are_found_whatever_the_casing_of_age_group():
    """
    age_group is freeform and onboarding writes "Adult", not "adult". The
    exact-match query this used to do found nobody in the real database —
    no avatar colours anywhere, and an empty identity switcher.
    """
    _add_adult("Emily")          # onboarding's own casing
    tools.add_member("Marcus")
    tools.set_member_age_group("Marcus", "adult")
    tools.add_member("Sam")
    tools.set_member_age_group("Sam", "Child")

    names = [p["name"] for p in tools.get_household_people()]
    assert names == ["Emily", "Marcus"]


def test_a_week_resolves_to_its_own_plan_not_merely_the_current_one():
    """
    The week-scoped endpoints are keyed by date. Approving Sep 1-7 has to
    approve Sep 1-7, even when a different week is the household's current
    plan.
    """
    this_week = _week_start()
    next_week = (datetime.date.fromisoformat(this_week) + datetime.timedelta(days=7)).isoformat()
    this_id = tools.create_weekly_plan(this_week)["weekly_plan_id"]
    next_id = tools.create_weekly_plan(next_week)["weekly_plan_id"]

    assert tools.get_plan_id_for_week(this_week) == this_id
    assert tools.get_plan_id_for_week(next_week) == next_id
    assert tools.get_plan_id_for_week("1999-01-04") is None


def test_a_week_is_named_the_way_the_copy_writes_it():
    assert tools._format_week_range("2026-09-01") == "Sep 1–7"
    assert tools._format_week_range("2026-08-30") == "Aug 30–Sep 5"


# ---------- Plan the Week: the intake ----------
# DATA_MODEL.md. Append-only revisions, the two snapshots, and the soft
# lock. These are the parts with no visible UI, and the parts that cannot be
# retrofitted once weeks have been planned without them.

def test_the_first_answers_are_revision_one():
    intake = tools.save_week_intake(
        "2026-09-07", night_tags={"2026-09-09": ["rush"]}, created_by="Emily"
    )
    assert intake["revision"] == 1
    assert intake["night_tags"] == {"2026-09-09": ["rush"]}
    assert tools.get_week_intake("2026-09-07")["intake_id"] == intake["intake_id"]


def test_changing_an_answer_writes_a_new_revision_and_keeps_the_old_one():
    first = tools.save_week_intake("2026-09-07", night_tags={"2026-09-09": ["rush"]})
    second = tools.save_week_intake("2026-09-07", night_tags={"2026-09-09": ["left"]})

    assert second["revision"] == 2
    assert second["intake_id"] != first["intake_id"]
    assert tools.get_week_intake("2026-09-07")["night_tags"] == {"2026-09-09": ["left"]}
    # The old row is still there — "the week you had before you redid it".
    history = tools.get_week_intake_history("2026-09-07")
    assert [h["revision"] for h in history] == [1, 2]
    assert history[0]["night_tags"] == {"2026-09-09": ["rush"]}


def test_each_screen_saves_its_own_half_without_clobbering_the_other():
    """
    Q1 sends nights, Q2 sends moods and cuisines. Neither restates the
    other's answers, and neither may wipe them.
    """
    tools.save_week_intake("2026-09-07", night_tags={"2026-09-09": ["rush"]}, packed_lunch_days=["2026-09-07"])
    tools.save_week_intake("2026-09-07", moods=["Comfort food"], cuisines=["Thai"], freeform="Friday is pizza night.")

    current = tools.get_week_intake("2026-09-07")
    assert current["night_tags"] == {"2026-09-09": ["rush"]}
    assert current["packed_lunch_days"] == ["2026-09-07"]
    assert current["moods"] == ["Comfort food"]
    assert current["freeform"] == "Friday is pizza night."


def test_a_regular_night_cannot_also_carry_another_tag():
    """
    Affirming a night and constraining it are different answers. Holding
    both would leave the generator with no way to tell which was meant.
    """
    with pytest.raises(ValueError):
        tools.save_week_intake("2026-09-07", night_tags={"2026-09-09": ["normal", "rush"]})


def test_an_unknown_tag_is_refused():
    with pytest.raises(ValueError):
        tools.save_week_intake("2026-09-07", night_tags={"2026-09-09": ["busy"]})


def test_the_preferences_snapshot_is_a_copy_not_a_pointer():
    """
    The one people skip and regret. If a plan only points at live
    preferences then editing "won't eat" later makes every past plan's
    reasoning unreadable — you can no longer tell whether a strange choice
    was a bug or a preference that has since changed.
    """
    tools.add_food_dislikes(["olives"])
    intake = tools.save_week_intake("2026-09-07", moods=["Comfort food"])
    assert intake["preferences_snapshot"]["wont_eat"] == ["olives"]

    tools.add_food_dislikes(["fennel"])

    unchanged = tools.get_week_intake("2026-09-07")
    assert unchanged["preferences_snapshot"]["wont_eat"] == ["olives"], \
        "the snapshot records what was true when the answer was given"


def test_the_household_snapshot_records_the_table_as_it_was():
    _add_adult("Emily")
    _add_adult("Marcus")
    intake = tools.save_week_intake("2026-09-07", moods=["Comfort food"])
    assert intake["household_snapshot"] == {"adults": 2, "children": 0}


def test_the_second_adult_joins_the_first_ones_intake():
    """
    DATA_MODEL.md → One intake in flight. Both adults are nudged, so both
    can start; two intakes racing to generate the same week is the one
    concurrency case that will actually happen.
    """
    tools.save_week_intake("2026-09-07", night_tags={"2026-09-09": ["rush"]}, created_by="Emily")

    prefill = tools.get_week_intake_prefill("2026-09-07")

    assert prefill["in_flight"] is True
    assert prefill["intake"]["created_by"] == "Emily"
    assert prefill["intake"]["night_tags"] == {"2026-09-09": ["rush"]}


def test_q2_reads_cuisines_from_what_we_know_rather_than_a_fixed_list():
    tools.edit_preference("cuisine_preferences", ["Thai", "Greek"])

    prefill = tools.get_week_intake_prefill("2026-09-07")

    assert prefill["cuisines"] == ["Thai", "Greek"]
    assert prefill["cuisines_are_fallback"] is False


def test_q2_falls_back_to_the_onboarding_list_when_nothing_is_saved():
    prefill = tools.get_week_intake_prefill("2026-09-07")
    assert prefill["cuisines"] == tools.ONBOARDING_CUISINES
    assert prefill["cuisines_are_fallback"] is True


def test_the_guest_panel_knows_when_it_cannot_do_the_maths():
    """
    Guest steppers collect EXTRAS, added to the household. With no
    composition on record the panel has to ask for the whole table instead
    — otherwise the acknowledgement confidently states a wrong number.
    """
    assert tools.get_week_intake_prefill("2026-09-07")["household_known"] is False

    _add_adult("Emily")
    assert tools.get_week_intake_prefill("2026-09-07")["household_known"] is True


# ---------- Plan the Week: no slot is ever silently missing ----------

def test_a_week_has_all_twenty_one_slots_or_says_which_are_missing():
    week = "2026-09-07"
    plan_id = tools.create_weekly_plan(week)["weekly_plan_id"]
    audit = tools.audit_plan_slots(plan_id)
    assert audit["expected"] == 21
    assert audit["complete"] is False
    assert len(audit["missing"]) == 21

    tools.add_recipe("Chili", ingredients=[{"item": "beans", "qty": "1 tin"}])
    for day in tools._week_dates(week):
        for slot in tools.WEEK_SLOTS:
            tools.plan_meal(day, "Chili", slot=slot, weekly_plan_id=plan_id)

    assert tools.audit_plan_slots(plan_id)["complete"] is True


def test_the_three_slot_states_are_all_real_slots():
    week = "2026-09-07"
    plan_id = tools.create_weekly_plan(week)["weekly_plan_id"]
    tools.add_recipe("Chili", ingredients=[{"item": "beans", "qty": "1 tin"}])

    tools.plan_meal(week, "Chili", slot="breakfast", weekly_plan_id=plan_id)
    tools.plan_slot_empty(plan_id, week, "lunch", "You’re out — I’ve planned nothing and bought nothing.")
    tools.plan_slot_open(
        plan_id, week, "dinner",
        "Monday I’d rather ask than guess: everything under 20 minutes repeats Sunday.",
        options=[{"label": "Breakfast for dinner", "meta": "12 min"}],
    )

    audit = tools.audit_plan_slots(plan_id)
    assert audit["present"] == 3
    assert audit["hollow"] == [], "each of the three states is a real slot, not a hollow row"


def test_an_open_slot_must_name_the_constraint_that_caused_it():
    """
    A slot handed back without a reason reads as failure. The reason is
    what makes it read as diligence.
    """
    plan_id = tools.create_weekly_plan("2026-09-07")["weekly_plan_id"]
    with pytest.raises(ValueError):
        tools.plan_slot_open(plan_id, "2026-09-07", "dinner", "   ")


def test_a_plan_remembers_the_answers_it_came_from():
    week = "2026-09-07"
    intake = tools.save_week_intake(week, night_tags={"2026-09-09": ["rush"]})
    plan_id = tools.create_weekly_plan(week)["weekly_plan_id"]

    tools.attach_intake_to_plan(plan_id, intake["intake_id"])

    prefill = tools.get_week_intake_prefill(week)
    assert prefill["in_flight"] is False, "answers already turned into a plan aren't still in flight"
