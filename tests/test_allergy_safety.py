"""
An allergy the household wrote down has to reach the food.

Three separate gaps used to sit between "Emily is allergic to pineapple" and
a week of meals that honours it, and each one is enough on its own for the
allergen to land on the table:

  1. The week generator was never told. The context handed to the model
     carried twelve keys and none of them was the `facts` table, so a hard
     What-we-know note — exactly where add_fact and the What-we-know screen
     put an allergy — was invisible to the thing that plans the food.
  2. check_plan_conflicts, the safety net, read only member restrictions,
     matched only against a saved recipe's ingredient text, and skipped any
     meal that wasn't a saved recipe by exact name. A dish called
     "Pineapple Chicken" sailed through.
  3. Nothing ever called it. It was registered as a chat tool and mentioned
     in prompt prose, so a clash was reported only if the assistant happened
     to think of it mid-conversation.

Every test here fails on the code as it was before this file existed.
"""
import datetime

import pytest

from app import agent, db, tools
from app.db import get_conn


def _week_start(offset_weeks: int = 1) -> str:
    today = datetime.date.today()
    monday = today - datetime.timedelta(days=today.weekday())
    return (monday + datetime.timedelta(days=7 * offset_weeks)).isoformat()


def _full_week(week: str, meal: str = "Chili", **extra) -> list[dict]:
    return [
        {
            "date": day, "slot": slot, "meal_name": meal, "is_new_recipe": False,
            "reasoning": "fits the week", **extra,
        }
        for day in tools._week_dates(week)
        for slot in tools.WEEK_SLOTS
    ]


@pytest.fixture
def kitchen():
    """A household with one member, one safe recipe, and one that isn't."""
    tools.add_member("Emily")
    tools.add_recipe(
        "Chili",
        ingredients=[{"item": "beans", "qty": "1 tin"}, {"item": "salt to taste", "qty": ""}],
        prep_time_minutes=10, cook_time_minutes=20,
    )
    # The point of this one: the allergen is in the NAME and nowhere else.
    # Its ingredients are grape-only, so an ingredient-text-only check finds
    # nothing to complain about.
    tools.add_recipe(
        "Pineapple Chicken",
        ingredients=[{"item": "grapes", "qty": "1 cup"}, {"item": "salt to taste", "qty": ""}],
        prep_time_minutes=10, cook_time_minutes=20,
    )


# ---------- 1. the generator is told ----------

class TestTheGeneratorIsTold:
    """
    A hard fact and no member restriction — the shape a household actually
    ends up in, because the What-we-know screen never sets `hard` itself and
    add_fact used to invite allergies into the facts table.
    """

    def _seen_context(self, monkeypatch, key: str):
        seen = {}

        def _fake(context):
            seen["context"] = context
            return _full_week(_week_start()) if key == "generate_weekly_plan_llm" else [
                {"meal_name": "Chili", "category": "protein", "is_new_recipe": False},
            ]

        monkeypatch.setattr(agent, key, _fake)
        return seen

    def test_a_hard_fact_reaches_day_based_generation(self, kitchen, monkeypatch):
        tools.add_fact("people", "Emily is allergic to pineapple", hard=True)
        assert tools.list_members()[0]["dietary_restrictions"] == [], \
            "the whole point is that nothing was saved as a member restriction"

        seen = self._seen_context(monkeypatch, "generate_weekly_plan_llm")
        agent.generate_weekly_plan(_week_start())

        context = seen["context"]
        facts = context["household_facts"]
        assert any(f["text"] == "Emily is allergic to pineapple" and f["hard"] for f in facts)
        # Serialised the way the prompt actually sends it — a key the JSON
        # dump drops or mangles is the same as never adding it.
        import json
        assert "pineapple" in json.dumps(context).lower()

    def test_a_hard_fact_reaches_component_based_generation(self, kitchen, monkeypatch):
        tools.set_planning_mode("component_based")
        tools.add_fact("people", "Emily is allergic to pineapple", hard=True)

        seen = self._seen_context(monkeypatch, "generate_component_plan_llm")
        agent.generate_weekly_plan(_week_start())

        import json
        facts = seen["context"]["household_facts"]
        assert any(f["text"] == "Emily is allergic to pineapple" and f["hard"] for f in facts)
        assert "pineapple" in json.dumps(seen["context"]).lower()

    def test_both_prompts_say_a_hard_fact_is_not_negotiable(self):
        # The context is only half the fix: the model has to be told what a
        # hard fact means, or it reads as one more note among many.
        import inspect
        for fn in (agent.generate_weekly_plan_llm, agent.generate_component_plan_llm):
            source = inspect.getsource(fn)
            assert "household_facts" in source
            assert "hard=true" in source


# ---------- 2. the check itself ----------

class TestTheCheckFindsIt:

    def _plan_with(self, meal: str) -> int:
        week = _week_start()
        plan = tools.create_weekly_plan(week)
        tools.plan_meal(
            tools._week_dates(week)[0], meal, slot="dinner",
            weekly_plan_id=plan["weekly_plan_id"],
        )
        return plan["weekly_plan_id"]

    def test_a_member_restriction_catches_the_allergen_in_the_name(self, kitchen):
        tools.set_member_dietary_restrictions("Emily", ["pineapple allergy"])
        plan_id = self._plan_with("Pineapple Chicken")

        found = tools.check_plan_conflicts(plan_id)["conflicts"]

        assert [c["meal"] for c in found] == ["Pineapple Chicken"]
        assert found[0]["member"] == "Emily"
        assert found[0]["severity"] == "hard"

    def test_a_hard_fact_catches_the_allergen_in_the_name(self, kitchen):
        tools.add_fact("people", "Emily is allergic to pineapple", hard=True)
        plan_id = self._plan_with("Pineapple Chicken")

        found = tools.check_plan_conflicts(plan_id)["conflicts"]

        assert [c["meal"] for c in found] == ["Pineapple Chicken"]
        assert found[0]["source"] == "fact"
        assert found[0]["member"] == "Emily", "the person named in the note is the person warned about"
        assert found[0]["matched"] == "pineapple"

    def test_a_freeform_meal_that_was_never_a_saved_recipe_is_still_checked(self, kitchen):
        tools.add_fact("people", "Emily is allergic to pineapple", hard=True)
        plan_id = self._plan_with("grilled pineapple skewers")

        found = tools.check_plan_conflicts(plan_id)["conflicts"]

        assert [c["meal"] for c in found] == ["grilled pineapple skewers"]

    def test_a_soft_fact_is_not_treated_as_a_must_avoid(self, kitchen):
        tools.add_fact("taste", "Emily is not mad about pineapple", hard=False)
        plan_id = self._plan_with("Pineapple Chicken")

        assert tools.check_plan_conflicts(plan_id)["conflicts"] == []

    def test_salt_to_taste_does_not_look_like_an_allergy(self, kitchen):
        """
        The stopword regression. "allergic to pineapple" used to yield the
        keyword "to", which whole-word matched the "salt to taste" sitting in
        most recipes — so the safe meal was flagged alongside the unsafe one
        and the warning meant nothing.
        """
        tools.add_fact("people", "Emily is allergic to pineapple", hard=True)
        plan_id = self._plan_with("Chili")

        assert tools.check_plan_conflicts(plan_id)["conflicts"] == [], \
            "Chili has salt to taste and no pineapple — nothing to warn about"

    def test_a_restriction_written_as_a_sentence_does_not_flag_salt_to_taste(self, kitchen):
        """
        The same regression through the door that always existed: nothing
        stops someone saving the restriction as a phrase rather than a noun.
        "allergic to pineapple" yielded the keywords ["to", "pineapple"], and
        "to" flagged Chili.
        """
        tools.set_member_dietary_restrictions("Emily", ["allergic to pineapple"])
        plan_id = self._plan_with("Chili")

        assert tools.check_plan_conflicts(plan_id)["conflicts"] == []

    def test_a_plural_restriction_still_matches_a_singular_ingredient(self, kitchen):
        tools.add_recipe("Satay", ingredients=[{"item": "peanut butter", "qty": "2 tbsp"}])
        tools.set_member_dietary_restrictions("Emily", ["peanuts"])
        plan_id = self._plan_with("Satay")

        assert [c["matched"] for c in tools.check_plan_conflicts(plan_id)["conflicts"]] == ["peanuts"]

    def test_a_dislike_is_flagged_but_not_as_a_safety_matter(self, kitchen):
        tools.edit_preference("dislikes", ["pineapple"])
        plan_id = self._plan_with("Pineapple Chicken")

        result = tools.check_plan_conflicts(plan_id)
        assert [c["severity"] for c in result["conflicts"]] == ["soft"]
        assert result["note"] is None, "a preference is not a warning"


class TestASideCanClashToo:
    """
    check_plan_conflicts used to read only the meal name and, for a saved
    recipe, its own ingredients_json. A side the app attached to complete
    the plate (see plates.py) lives on the SAME entry's sides_json, not on
    the recipe — so an otherwise-clean dinner with an allergen hiding in
    its side sailed through both the draft warning and the approve-time
    confirm gate untouched.
    """

    def _plan_with_side(self, meal: str, side_item: str, side_name: str = "Pineapple salsa") -> int:
        week = _week_start()
        plan = tools.create_weekly_plan(week)
        tools.plan_meal(
            tools._week_dates(week)[0], meal, slot="dinner",
            weekly_plan_id=plan["weekly_plan_id"],
        )
        entry = tools.get_weekly_plan(plan["weekly_plan_id"])["meals"][0]
        tools.plates.attach_sides(entry["entry_id"], [{
            "name": side_name,
            "covers": [],
            "ingredients": [{"item": side_item, "qty": "1 cup", "category": "produce"}],
            "instructions": [],
            "minutes": 5,
        }], groups_covered=[])
        return plan["weekly_plan_id"]

    def test_a_clean_dinner_with_an_allergen_in_its_side_is_caught(self, kitchen):
        tools.add_fact("people", "Emily is allergic to pineapple", hard=True)
        # Chili's own name and ingredients (beans, salt to taste) are clean
        # — only the side attached to it carries the allergen.
        plan_id = self._plan_with_side("Chili", "pineapple")

        found = tools.check_plan_conflicts(plan_id)["conflicts"]

        assert [c["meal"] for c in found] == ["Chili"]
        assert found[0]["matched"] == "pineapple"
        assert found[0]["severity"] == "hard"

    def test_a_clean_side_raises_no_conflict(self, kitchen):
        tools.add_fact("people", "Emily is allergic to pineapple", hard=True)
        plan_id = self._plan_with_side("Chili", "romaine lettuce", side_name="Green salad")

        assert tools.check_plan_conflicts(plan_id)["conflicts"] == []

    def test_the_confirm_tap_is_required_for_a_side_clash_too(self, kitchen):
        # The approve-time gate (approve_weekly_plan's confirm_hard_conflicts)
        # runs check_plan_conflicts internally, so this is really the same
        # fix seen from the other caller — confirming that fix reaches both.
        tools.add_fact("people", "Emily is allergic to pineapple", hard=True)
        plan_id = self._plan_with_side("Chili", "pineapple")

        blocked = tools.approve_weekly_plan(plan_id, approved_by="Emily")
        assert blocked["status"] == "needs_confirmation"
        assert blocked["conflicts_note"] and "Chili" in blocked["conflicts_note"]
        assert tools.get_weekly_plan(plan_id)["status"] == "draft"

        confirmed = tools.approve_weekly_plan(
            plan_id, approved_by="Emily", confirm_hard_conflicts=True
        )
        assert confirmed["status"] == "approved"


# ---------- 3. it runs on its own ----------

def test_a_generated_draft_carries_its_conflict_without_anyone_asking(kitchen, monkeypatch):
    """
    No chat turn, no tool call by the assistant — generation alone has to
    produce the warning, because generation is the step that put the meal
    on the table.
    """
    week = _week_start()
    tools.add_fact("people", "Emily is allergic to pineapple", hard=True)
    monkeypatch.setattr(
        agent, "generate_weekly_plan_llm", lambda ctx: _full_week(week, meal="Pineapple Chicken")
    )

    plan = agent.generate_weekly_plan(week)

    menu = tools.get_week_menu(plan["weekly_plan_id"])
    assert menu["conflicts"], "the draft the Meals screen renders has to carry the clash"
    assert {c["meal"] for c in menu["conflicts"]} == {"Pineapple Chicken"}
    note = menu["conflicts_note"]
    assert note and "pineapple" in note.lower()
    assert "before you approve" in note


def test_an_approved_week_is_not_nagged_about_a_decision_already_made(kitchen, monkeypatch):
    week = _week_start()
    tools.add_fact("people", "Emily is allergic to pineapple", hard=True)
    monkeypatch.setattr(
        agent, "generate_weekly_plan_llm", lambda ctx: _full_week(week, meal="Pineapple Chicken")
    )
    plan = agent.generate_weekly_plan(week)

    # A hard clash needs the household's confirm tap (see the "confirm tap
    # for a hard clash" section below) — this test is about the note's
    # wording once they've given it, not about the gate itself.
    result = tools.approve_weekly_plan(
        plan["weekly_plan_id"], approved_by="Emily", confirm_hard_conflicts=True
    )

    # Approval says it out loud once...
    assert {c["meal"] for c in result["conflicts"]} == {"Pineapple Chicken"}
    assert result["status"] == "approved", "a clash warns, it never blocks once confirmed"
    # ...and the settled week stops carrying the warning.
    assert tools.get_week_menu(plan["weekly_plan_id"])["conflicts"] == []


def test_a_clean_week_says_nothing_at_all(kitchen, monkeypatch):
    week = _week_start()
    tools.add_fact("people", "Emily is allergic to pineapple", hard=True)
    monkeypatch.setattr(agent, "generate_weekly_plan_llm", lambda ctx: _full_week(week, meal="Chili"))

    plan = agent.generate_weekly_plan(week)

    menu = tools.get_week_menu(plan["weekly_plan_id"])
    assert menu["conflicts"] == []
    assert menu["conflicts_note"] is None


# ---------- 3b. a HARD clash needs a confirm tap (decision 1b, 2026-09-05) ----------
#
# A soft dislike stays warn-only, same as always. A hard clash — an
# allergy, a member restriction, a hard fact — used to only warn too; now
# approve_weekly_plan itself refuses until the household explicitly says
# "approve anyway" (confirm_hard_conflicts=True). See
# app/tools/weekly_plan.py's approve_weekly_plan and app/main.py's
# /api/week/{week_start}/approve.

def test_a_hard_clash_blocks_approval_until_confirmed(kitchen, monkeypatch):
    week = _week_start()
    tools.add_fact("people", "Emily is allergic to pineapple", hard=True)
    monkeypatch.setattr(
        agent, "generate_weekly_plan_llm", lambda ctx: _full_week(week, meal="Pineapple Chicken")
    )
    plan = agent.generate_weekly_plan(week)

    result = tools.approve_weekly_plan(plan["weekly_plan_id"], approved_by="Emily")

    assert result["status"] == "needs_confirmation"
    assert result["weekly_plan_id"] == plan["weekly_plan_id"]
    note = result["conflicts_note"]
    assert note and "Pineapple Chicken" in note, "name the meal, not just 'a clash'"
    assert "before you approve" in note

    # Nothing was written: the plan is still a draft, and nothing reached
    # the grocery list.
    assert tools.get_weekly_plan(plan["weekly_plan_id"])["status"] == "draft"
    assert tools.list_grocery_list("needed") == []


def test_confirming_the_hard_clash_approves_it(kitchen, monkeypatch):
    week = _week_start()
    tools.add_fact("people", "Emily is allergic to pineapple", hard=True)
    monkeypatch.setattr(
        agent, "generate_weekly_plan_llm", lambda ctx: _full_week(week, meal="Pineapple Chicken")
    )
    plan = agent.generate_weekly_plan(week)

    result = tools.approve_weekly_plan(
        plan["weekly_plan_id"], approved_by="Emily", confirm_hard_conflicts=True
    )

    assert result["status"] == "approved"
    assert tools.get_weekly_plan(plan["weekly_plan_id"])["status"] == "approved"


def test_a_soft_dislike_never_needs_a_confirm_tap(kitchen, monkeypatch):
    week = _week_start()
    tools.edit_preference("dislikes", ["pineapple"])
    monkeypatch.setattr(
        agent, "generate_weekly_plan_llm", lambda ctx: _full_week(week, meal="Pineapple Chicken")
    )
    plan = agent.generate_weekly_plan(week)

    result = tools.approve_weekly_plan(plan["weekly_plan_id"], approved_by="Emily")

    assert result["status"] == "approved", "a dislike is a preference, never a safety block"


def test_a_clean_week_needs_no_confirm_tap(kitchen, monkeypatch):
    week = _week_start()
    tools.add_fact("people", "Emily is allergic to pineapple", hard=True)
    monkeypatch.setattr(agent, "generate_weekly_plan_llm", lambda ctx: _full_week(week, meal="Chili"))
    plan = agent.generate_weekly_plan(week)

    result = tools.approve_weekly_plan(plan["weekly_plan_id"], approved_by="Emily")

    assert result["status"] == "approved"


def test_the_confirm_flag_only_matters_on_the_way_in_not_on_a_re_approval(kitchen, monkeypatch):
    """
    Once a hard clash has actually been confirmed and approved, tapping
    Approve again (the existing re-approval idempotency guard) must not
    start demanding a fresh confirm tap — the decision was already made.
    """
    week = _week_start()
    tools.add_fact("people", "Emily is allergic to pineapple", hard=True)
    monkeypatch.setattr(
        agent, "generate_weekly_plan_llm", lambda ctx: _full_week(week, meal="Pineapple Chicken")
    )
    plan = agent.generate_weekly_plan(week)
    tools.approve_weekly_plan(
        plan["weekly_plan_id"], approved_by="Emily", confirm_hard_conflicts=True
    )

    result = tools.approve_weekly_plan(plan["weekly_plan_id"])

    assert result["status"] == "approved"
    assert result["was_already_approved"] is True


def test_the_route_reports_needs_confirmation_without_the_success_fields(kitchen, monkeypatch):
    """
    main.py's approve route (app.main.approve_week) has to pass
    needs_confirmation straight through rather than reshaping it into the
    success payload — there's no approved_by/approved_at/groceries_added
    to report, because nothing happened yet.
    """
    from app import main as app_main

    week = _week_start()
    tools.add_fact("people", "Emily is allergic to pineapple", hard=True)
    monkeypatch.setattr(
        agent, "generate_weekly_plan_llm", lambda ctx: _full_week(week, meal="Pineapple Chicken")
    )
    plan = agent.generate_weekly_plan(week)

    response = app_main.approve_week(week, app_main.WeekApproveRequest(approved_by="Emily"))

    assert response["status"] == "needs_confirmation"
    assert "approved_by" not in response
    assert "groceries_added" not in response
    assert response["conflicts_note"] and "Pineapple Chicken" in response["conflicts_note"]


def test_the_chat_tool_schema_never_lets_the_assistant_confirm_on_its_own():
    """
    confirm_hard_conflicts has to read as a household decision, not a
    detail the assistant can default to true — see the schema in
    app/agent.py's TOOLS list and the "Household coordination & trust"
    system-prompt bullet right after check_plan_conflicts.
    """
    schema = next(t for t in agent.TOOL_DEFINITIONS if t["name"] == "approve_weekly_plan")
    props = schema["input_schema"]["properties"]
    assert "confirm_hard_conflicts" in props
    assert props["confirm_hard_conflicts"]["type"] == "boolean"
    flag_description = props["confirm_hard_conflicts"]["description"].lower()
    assert "ask" in flag_description
    assert "own initiative" in flag_description

    assert "confirm_hard_conflicts" in agent.SYSTEM_PROMPT
    assert "own initiative" in agent.SYSTEM_PROMPT


# ---------- 4. the warning has to be about food ----------

class TestItDoesNotCryWolf:
    """
    Everything above is about a warning that fires. This is about the ones
    that must not: a check that flags the safe meals too is a check the
    household learns to click past, and then the real one goes past with it.

    Each case here is a real sentence a person would write on the
    What-we-know screen, and each one flagged an innocent dish because every
    non-stopword in a hard fact became something to search recipes for.
    """

    def _plan_with(self, meal: str) -> int:
        week = _week_start()
        plan = tools.create_weekly_plan(week)
        tools.plan_meal(
            tools._week_dates(week)[0], meal, slot="dinner",
            weekly_plan_id=plan["weekly_plan_id"],
        )
        return plan["weekly_plan_id"]

    def test_a_fact_about_the_house_does_not_flag_a_house_salad(self, kitchen):
        tools.add_recipe("House Salad", ingredients=[{"item": "lettuce", "qty": "1"}])
        tools.add_fact("people", "no pork in this house", hard=True)

        assert tools.check_plan_conflicts(self._plan_with("House Salad"))["conflicts"] == []

    def test_that_same_fact_still_catches_the_pork(self, kitchen):
        """The false positive is fixed by reading the fact, not by ignoring it."""
        tools.add_fact("people", "no pork in this house", hard=True)

        found = tools.check_plan_conflicts(self._plan_with("Pork Chops"))["conflicts"]

        assert [c["matched"] for c in found] == ["pork"]

    def test_the_milk_that_is_fine_does_not_flag_the_porridge(self, kitchen):
        tools.add_recipe("Porridge", ingredients=[{"item": "oats", "qty": "1 cup"},
                                                  {"item": "oat milk", "qty": "200ml"}])
        tools.add_fact("people", "No cow milk for Emily, oat milk is fine", hard=True)

        assert tools.check_plan_conflicts(self._plan_with("Porridge"))["conflicts"] == []

    def test_the_nut_that_is_fine_does_not_flag_the_satay(self, kitchen):
        """
        The stated exception has to survive the keyword extraction. "tree
        nuts" expands through the allergen alias table (peanut, walnut,
        almond…), so without reading the second half of the sentence this
        warns about precisely the food the household just called safe.
        """
        tools.add_recipe("Satay", ingredients=[{"item": "peanut butter", "qty": "2 tbsp"}])
        tools.add_fact("people", "allergic to tree nuts but peanuts are fine", hard=True)

        assert tools.check_plan_conflicts(self._plan_with("Satay"))["conflicts"] == []

    def test_the_walnuts_that_are_not_fine_still_flag(self, kitchen):
        """Same sentence, and the half that IS an allergy still works."""
        tools.add_recipe("Walnut Loaf", ingredients=[{"item": "walnuts", "qty": "100g"}])
        tools.add_fact("people", "allergic to tree nuts but peanuts are fine", hard=True)

        found = tools.check_plan_conflicts(self._plan_with("Walnut Loaf"))["conflicts"]

        assert [c["matched"] for c in found] == ["nuts"]

    def test_a_hard_fact_that_asks_for_something_is_not_an_avoidance(self, kitchen):
        """
        "hard" means "not negotiable", not "keep this off the table". A
        requirement names no food to avoid, so it must contribute no search
        terms at all — otherwise "high-protein" hunts down a Protein Bowl.
        """
        tools.add_recipe("Protein Bowl", ingredients=[{"item": "chicken", "qty": "200g"}])
        tools.add_fact("people", "Emily needs high-protein dinners", hard=True)

        assert tools.check_plan_conflicts(self._plan_with("Protein Bowl"))["conflicts"] == []

    def test_a_requirement_fact_does_not_flag_anything_at_all(self, kitchen):
        tools.add_fact("rhythm", "Emily likes an early dinner on weeknights", hard=True)

        assert tools.check_plan_conflicts(self._plan_with("Pineapple Chicken"))["conflicts"] == []


# ---------- 5. an allergen family, not just a spelling ----------

class TestCompoundAllergens:

    def _plan_with(self, meal: str) -> int:
        week = _week_start()
        plan = tools.create_weekly_plan(week)
        tools.plan_meal(
            tools._week_dates(week)[0], meal, slot="dinner",
            weekly_plan_id=plan["weekly_plan_id"],
        )
        return plan["weekly_plan_id"]

    def test_a_nut_allergy_reaches_peanut_butter(self, kitchen):
        tools.add_recipe("Satay", ingredients=[{"item": "peanut butter", "qty": "2 tbsp"}])
        tools.set_member_dietary_restrictions("Emily", ["nut allergy"])

        found = tools.check_plan_conflicts(self._plan_with("Satay"))["conflicts"]

        assert [c["matched"] for c in found] == ["nut"]

    def test_a_nut_allergy_written_as_a_fact_reaches_peanut_butter(self, kitchen):
        """
        The commonest way this actually gets written down. There is no
        "allergic to" in it — the food comes before the word — so reading
        only the span after a trigger would lose it entirely.
        """
        tools.add_recipe("Satay", ingredients=[{"item": "peanut butter", "qty": "2 tbsp"}])
        tools.add_fact("people", "Emily has a nut allergy", hard=True)

        found = tools.check_plan_conflicts(self._plan_with("Satay"))["conflicts"]

        assert [c["matched"] for c in found] == ["nut"]
        assert found[0]["member"] == "Emily"

    def test_a_nut_allergy_reaches_walnuts_and_almonds(self, kitchen):
        tools.set_member_dietary_restrictions("Emily", ["nut allergy"])
        for dish, item in (("Walnut Loaf", "walnuts"), ("Almond Cake", "ground almonds")):
            tools.add_recipe(dish, ingredients=[{"item": item, "qty": "100g"}])
            assert tools.check_plan_conflicts(self._plan_with(dish))["conflicts"], dish

    @pytest.mark.parametrize("dish, item", [
        ("Coconut Curry", "coconut milk"),
        ("Spiced Buns", "nutmeg"),
        ("Roast Squash", "butternut squash"),
    ])
    def test_a_nut_allergy_does_not_reach_words_that_merely_contain_nut(self, kitchen, dish, item):
        """Whole-word matching, kept. These are the regressions an alias table invites."""
        tools.add_recipe(dish, ingredients=[{"item": item, "qty": "1"}])
        tools.set_member_dietary_restrictions("Emily", ["nut allergy"])

        assert tools.check_plan_conflicts(self._plan_with(dish))["conflicts"] == []

    @pytest.mark.parametrize("restriction, dish, item", [
        ("shellfish allergy", "Paella", "prawns"),
        ("dairy free", "Gratin", "double cream"),
        ("gluten free", "Carbonara", "pasta"),
        ("egg allergy", "Potato Salad", "mayonnaise"),
        ("soy allergy", "Stir Fry", "tofu"),
        ("sesame allergy", "Hummus Bowl", "tahini"),
    ])
    def test_the_other_families_in_the_starting_list(self, kitchen, restriction, dish, item):
        tools.add_recipe(dish, ingredients=[{"item": item, "qty": "1"}])
        tools.set_member_dietary_restrictions("Emily", [restriction])

        assert tools.check_plan_conflicts(self._plan_with(dish))["conflicts"], (restriction, item)

    def test_the_plural_helper_stops_inventing_words(self):
        from app.tools import coordination

        assert "nutes" not in coordination._keyword_variants("nut")
        assert "shellfishs" not in coordination._keyword_variants("shellfish")
        # ...without losing the twin it exists for.
        assert "nuts" in coordination._keyword_variants("nut")
        assert "peanut" in coordination._keyword_variants("peanuts")


# ---------- 6. the sentence the household reads ----------

class TestTheNote:

    def _plan(self, *meals: str) -> int:
        week = _week_start()
        plan = tools.create_weekly_plan(week)
        for date, meal in zip(tools._week_dates(week), meals):
            tools.plan_meal(date, meal, slot="dinner", weekly_plan_id=plan["weekly_plan_id"])
        return plan["weekly_plan_id"]

    def test_one_dish_tripping_two_facts_is_still_named(self, kitchen):
        """
        The note branched on the number of clashes, so a dish that broke two
        rules at once became "One meal looks like a clash" — vaguer than the
        one that broke one, with the dish's name sitting right there.
        """
        tools.add_fact("people", "Emily is allergic to pineapple", hard=True)
        tools.add_fact("people", "Emily can't have chicken", hard=True)

        note = tools.check_plan_conflicts(self._plan("Pineapple Chicken"))["note"]

        assert note.startswith("Pineapple Chicken looks like a clash")
        assert "Emily" in note, "the fact names a person, so the sentence should too"
        assert "two things" in note

    def test_two_dishes_are_both_named(self, kitchen):
        tools.add_recipe("Satay", ingredients=[{"item": "peanut butter", "qty": "2 tbsp"}])
        tools.add_fact("people", "Emily is allergic to pineapple", hard=True)
        tools.set_member_dietary_restrictions("Emily", ["peanuts"])

        note = tools.check_plan_conflicts(self._plan("Pineapple Chicken", "Satay"))["note"]

        assert "Pineapple Chicken and Satay look like a clash" in note

    def test_more_than_two_falls_back_to_a_count(self, kitchen):
        for dish in ("Pineapple Salsa", "Pineapple Rice", "Pineapple Curry"):
            tools.add_recipe(dish, ingredients=[{"item": "pineapple", "qty": "1"}])
        tools.add_fact("people", "Emily is allergic to pineapple", hard=True)

        note = tools.check_plan_conflicts(
            self._plan("Pineapple Salsa", "Pineapple Rice", "Pineapple Curry")
        )["note"]

        assert note.startswith("Three meals look like a clash")

    def test_the_same_dish_on_five_nights_is_one_thing_to_look_at(self, kitchen):
        tools.add_fact("people", "Emily is allergic to pineapple", hard=True)

        note = tools.check_plan_conflicts(self._plan(*(["Pineapple Chicken"] * 5)))["note"]

        assert note.startswith("Pineapple Chicken looks like a clash")

    def test_a_bare_label_is_not_hung_off_a_name(self, kitchen):
        """"a clash with Emily's shellfish" is not a sentence anyone says."""
        tools.add_recipe("Paella", ingredients=[{"item": "prawns", "qty": "200g"}])
        tools.set_member_dietary_restrictions("Emily", ["shellfish"])

        note = tools.check_plan_conflicts(self._plan("Paella"))["note"]

        assert "Emily’s shellfish" not in note
        assert "the shellfish Emily can’t have" in note

    def test_a_label_that_describes_itself_still_hangs_off_the_name(self, kitchen):
        tools.set_member_dietary_restrictions("Emily", ["pineapple allergy"])

        note = tools.check_plan_conflicts(self._plan("Pineapple Chicken"))["note"]

        assert "Emily’s pineapple allergy" in note

    def test_a_fact_names_the_person_it_is_about(self, kitchen):
        tools.add_fact("people", "Emily is allergic to pineapple", hard=True)

        note = tools.check_plan_conflicts(self._plan("Pineapple Chicken"))["note"]

        assert "something Emily can’t have" in note

    def test_a_household_wide_fact_names_nobody(self, kitchen):
        tools.add_fact("people", "no pineapple in this house", hard=True)

        note = tools.check_plan_conflicts(self._plan("Pineapple Chicken"))["note"]

        assert "something you’ve told me" in note


# ---------- 7. after the week is approved ----------

def test_approval_hands_back_a_sentence_worded_for_a_decision_already_made(kitchen, monkeypatch):
    """
    The warning survives approval (that was already true) but must stop
    telling the household to look "before you approve" a moment after they
    did — see coordination.conflicts_note_after_approval.
    """
    week = _week_start()
    tools.add_fact("people", "Emily is allergic to pineapple", hard=True)
    monkeypatch.setattr(
        agent, "generate_weekly_plan_llm", lambda ctx: _full_week(week, meal="Pineapple Chicken")
    )
    plan = agent.generate_weekly_plan(week)

    result = tools.approve_weekly_plan(
        plan["weekly_plan_id"], approved_by="Emily", confirm_hard_conflicts=True
    )

    note = result["conflicts_note"]
    assert note and "Pineapple Chicken" in note
    assert "before you approve" not in note
    assert "before you shop" in note


def test_the_approve_button_shows_the_warning_instead_of_discarding_it():
    """
    main.py's approve route passes conflicts_note back on purpose. The
    browser used to `await res.json()` and throw the result away, so the
    only thing a household saw was a fixed confirmation.
    """
    import pathlib

    shell = (pathlib.Path(__file__).resolve().parent.parent / "static" / "shell.js").read_text()
    approve = shell[shell.index("async function approveWeek("):]
    approve = approve[:approve.index("\n  function renderWeekMenu(")]

    assert "conflicts_note" in approve, \
        "approveWeek has to read the warning the endpoint sends, not just the status"
    assert "await res.json()" in approve
    assert "showToast('Approved. ' + approval.conflicts_note" in approve


# ---------- 8. component-based households get checked too ----------

def test_a_component_based_week_is_conflict_checked_as_well(kitchen, monkeypatch):
    """
    _finish_week_slots is the day-based branch's ending and nothing else's,
    so the check that lived inside it never ran for a household planning by
    component — the one generation path with no post-generation safety net
    at all.
    """
    tools.set_planning_mode("component_based")
    tools.add_fact("people", "Emily is allergic to pineapple", hard=True)
    monkeypatch.setattr(
        agent, "generate_component_plan_llm",
        lambda ctx: [{"meal_name": "Pineapple Chicken", "category": "protein", "is_new_recipe": False}],
    )
    seen = {}
    real = tools.check_plan_conflicts
    monkeypatch.setattr(
        agent.tools, "check_plan_conflicts",
        lambda plan_id=None: seen.setdefault("result", real(plan_id)),
    )

    agent.generate_weekly_plan(_week_start())

    assert "result" in seen, "generation itself has to run the check, not the assistant"
    assert {c["meal"] for c in seen["result"]["conflicts"]} == {"Pineapple Chicken"}


# ---------- 9. what the prompt is actually sent ----------

def test_the_prompt_gets_the_fact_and_not_its_bookkeeping(kitchen, monkeypatch):
    tools.add_fact("people", "Emily is allergic to pineapple", hard=True)
    seen = {}
    monkeypatch.setattr(
        agent, "generate_weekly_plan_llm",
        lambda ctx: (seen.setdefault("ctx", ctx), _full_week(_week_start()))[1],
    )

    agent.generate_weekly_plan(_week_start())

    facts = seen["ctx"]["household_facts"]
    assert facts and all(set(f) == {"category", "text", "hard"} for f in facts), \
        "id/author/updated_at are storage bookkeeping and cost tokens for nothing"
    assert facts[0]["hard"] is True

# ---------- 10. a multi-word avoidance is a phrase, not its loose words ----------

class TestAPhraseIsMatchedAsAPhrase:
    """
    The second verification pass's headline finding, and the same shape of
    bug as section 4: every significant word of an avoidance became an
    INDEPENDENT thing to hunt for, so a two-word food leaked its common
    half. "no red meat during the week" searched for "red" on its own and
    flagged Red Lentil Dahl; "avoid sugar" flagged Sugar Snap Peas.
    """

    def _plan_with(self, meal: str) -> int:
        week = _week_start()
        plan = tools.create_weekly_plan(week)
        tools.plan_meal(
            tools._week_dates(week)[0], meal, slot="dinner",
            weekly_plan_id=plan["weekly_plan_id"],
        )
        return plan["weekly_plan_id"]

    def test_red_meat_does_not_flag_a_red_lentil_dahl(self, kitchen):
        tools.add_recipe("Red Lentil Dahl", ingredients=[{"item": "red lentils", "qty": "200g"},
                                                         {"item": "coconut oil", "qty": "1 tbsp"}])
        tools.add_fact("people", "no red meat during the week", hard=True)

        assert tools.check_plan_conflicts(self._plan_with("Red Lentil Dahl"))["conflicts"] == []

    def test_red_meat_still_flags_the_red_meat(self, kitchen):
        """The false positive is fixed by reading the phrase, not by dropping it."""
        tools.add_recipe("Sunday Ragu", ingredients=[{"item": "red meat mince", "qty": "500g"}])
        tools.add_fact("people", "no red meat during the week", hard=True)

        found = tools.check_plan_conflicts(self._plan_with("Sunday Ragu"))["conflicts"]

        assert [c["matched"] for c in found] == ["red meat"]

    def test_during_is_a_stopword_so_the_phrase_is_findable_at_all(self):
        """
        "during" survived into the avoidance span and made the phrase
        unmatchable as written — a false negative hiding behind the false
        positive.
        """
        from app.tools import coordination

        assert coordination._fact_keywords("no red meat during the week", set())[0] == [
            ["red", "meat"]
        ]

    def test_beef_is_a_known_miss_for_red_meat(self, kitchen):
        """
        DOCUMENTED LIMIT, not an accident. This check matches words the
        household wrote against words on the recipe; it has no idea that
        beef, lamb and pork are what "red meat" means. Closing it needs a
        food taxonomy (or an alias entry per category), not a regex — and
        until then it is better written down here than discovered on a
        Tuesday. See _ALLERGEN_ALIASES: extend it when a real miss shows up.
        """
        tools.add_recipe("Beef Stew", ingredients=[{"item": "stewing beef", "qty": "600g"}])
        tools.add_fact("people", "no red meat during the week", hard=True)

        assert tools.check_plan_conflicts(self._plan_with("Beef Stew"))["conflicts"] == [], \
            "if this ever starts passing, delete the test and celebrate"

    def test_a_phrase_is_not_assembled_across_two_ingredients(self, kitchen):
        """
        In order, but inside ONE stretch of text. A word from the name and a
        word from an unrelated ingredient line is a coincidence.
        """
        tools.add_recipe("Red Pepper Soup", ingredients=[{"item": "red peppers", "qty": "3"},
                                                         {"item": "meat stock", "qty": "500ml"}])
        tools.add_fact("people", "no red meat during the week", hard=True)

        assert tools.check_plan_conflicts(self._plan_with("Red Pepper Soup"))["conflicts"] == []

    def test_several_foods_in_one_sentence_are_still_several_avoidances(self, kitchen):
        """
        The trap the phrase rule could have walked into: reading "pineapple,
        shellfish and eggs" as one three-word food would match nothing at
        all — a false negative, which is the worse bug of the two.
        """
        tools.add_fact("people", "Emily is allergic to pineapple, shellfish and eggs", hard=True)
        for dish, item in (("Pineapple Chicken", None), ("Paella", "prawns"),
                           ("Potato Salad", "mayonnaise")):
            if item:
                tools.add_recipe(dish, ingredients=[{"item": item, "qty": "1"}])
            assert tools.check_plan_conflicts(self._plan_with(dish))["conflicts"], dish

    def test_two_restrictions_typed_into_one_box_are_still_two(self, kitchen):
        tools.add_recipe("Omelette", ingredients=[{"item": "eggs", "qty": "3"}])
        tools.set_member_dietary_restrictions("Emily", ["no dairy, no eggs"])

        assert tools.check_plan_conflicts(self._plan_with("Omelette"))["conflicts"]

    def test_a_single_word_avoidance_still_matches_on_the_single_word(self, kitchen):
        tools.add_fact("people", "Emily is allergic to pineapple", hard=True)

        found = tools.check_plan_conflicts(self._plan_with("Pineapple Chicken"))["conflicts"]

        assert [c["matched"] for c in found] == ["pineapple"]


# ---------- 11. the compound foods that only look like the allergen ----------

class TestCompoundFoodsThatAreNotTheAllergen:
    """
    Whole-word matching already keeps "nut" off coconut and butternut. These
    are the ones written as two words, where the allergen word is standing
    right there and still isn't the allergen — the false positives from
    Emily's own week.
    """

    def _plan_with(self, meal: str) -> int:
        week = _week_start()
        plan = tools.create_weekly_plan(week)
        tools.plan_meal(
            tools._week_dates(week)[0], meal, slot="dinner",
            weekly_plan_id=plan["weekly_plan_id"],
        )
        return plan["weekly_plan_id"]

    def test_peanut_butter_is_not_dairy(self, kitchen):
        tools.add_recipe("Peanut Butter Toast", ingredients=[{"item": "peanut butter", "qty": "2 tbsp"},
                                                             {"item": "sourdough", "qty": "2 slices"}])
        tools.set_member_dietary_restrictions("Emily", ["dairy free"])

        assert tools.check_plan_conflicts(self._plan_with("Peanut Butter Toast"))["conflicts"] == []

    def test_peanut_butter_is_still_very_much_a_nut(self, kitchen):
        """The discount is per WORD, never per compound — "butter" only."""
        tools.add_recipe("Peanut Butter Toast", ingredients=[{"item": "peanut butter", "qty": "2 tbsp"}])
        tools.set_member_dietary_restrictions("Emily", ["nut allergy"])

        assert tools.check_plan_conflicts(self._plan_with("Peanut Butter Toast"))["conflicts"]

    def test_real_butter_is_still_dairy(self, kitchen):
        tools.add_recipe("Butter Chicken", ingredients=[{"item": "chicken thighs", "qty": "600g"}])
        tools.set_member_dietary_restrictions("Emily", ["dairy free"])

        found = tools.check_plan_conflicts(self._plan_with("Butter Chicken"))["conflicts"]

        assert [c["matched"] for c in found] == ["dairy"]

    def test_milk_is_still_dairy(self, kitchen):
        tools.add_recipe("Milk Pudding", ingredients=[{"item": "whole milk", "qty": "500ml"}])
        tools.set_member_dietary_restrictions("Emily", ["dairy free"])

        assert tools.check_plan_conflicts(self._plan_with("Milk Pudding"))["conflicts"]

    def test_buttermilk_is_dairy_even_though_neither_half_matches_it(self, kitchen):
        tools.add_recipe("Soda Bread", ingredients=[{"item": "buttermilk", "qty": "300ml"}])
        tools.set_member_dietary_restrictions("Emily", ["dairy free"])

        assert tools.check_plan_conflicts(self._plan_with("Soda Bread"))["conflicts"]

    def test_coconut_milk_is_not_dairy(self, kitchen):
        tools.add_recipe("Thai Curry", ingredients=[{"item": "coconut milk", "qty": "400ml"}])
        tools.set_member_dietary_restrictions("Emily", ["dairy free"])

        assert tools.check_plan_conflicts(self._plan_with("Thai Curry"))["conflicts"] == []

    def test_naming_the_compound_yourself_beats_the_exception(self, kitchen):
        """
        A discount only applies to a single-word avoidance. Someone who
        writes "allergic to coconut milk" is taken at their word.
        """
        tools.add_recipe("Thai Curry", ingredients=[{"item": "coconut milk", "qty": "400ml"}])
        tools.add_fact("people", "Emily is allergic to coconut milk", hard=True)

        found = tools.check_plan_conflicts(self._plan_with("Thai Curry"))["conflicts"]

        assert [c["matched"] for c in found] == ["coconut milk"]

    def test_sugar_snap_peas_are_a_pea(self, kitchen):
        tools.add_recipe("Sugar Snap Peas", ingredients=[{"item": "sugar snap peas", "qty": "200g"}])
        tools.add_fact("people", "avoid sugar", hard=True)

        assert tools.check_plan_conflicts(self._plan_with("Sugar Snap Peas"))["conflicts"] == []

    def test_the_sugar_that_is_sugar_still_flags(self, kitchen):
        """Only that occurrence is discounted, not the word everywhere else."""
        tools.add_recipe("Glazed Snaps", ingredients=[{"item": "sugar snap peas", "qty": "200g"},
                                                      {"item": "brown sugar", "qty": "2 tbsp"}])
        tools.add_fact("people", "avoid sugar", hard=True)

        assert tools.check_plan_conflicts(self._plan_with("Glazed Snaps"))["conflicts"]


# ---------- 12. the clash Emily found on the shopping list ----------

def test_an_approved_weeks_groceries_are_reported_as_a_clash(kitchen, monkeypatch):
    """
    What Emily actually saw: an approved week whose grocery list said
    "Pineapple chunks · 3 bag frozen" for a pineapple-allergic household.
    The allergen was in the INGREDIENTS of an innocently-named dish, so the
    only place it ever became visible was the shopping list.

    A hard clash now needs the household's confirm tap before approval
    goes through at all (decision 1b, 2026-09-05 — see the "confirm tap
    for a hard clash" section below for that gate itself); this test is
    about what lands on the list and what the sentence says once they've
    given it.
    """
    week = _week_start()
    tools.add_recipe(
        "Fruit Salad",
        ingredients=[{"item": "pineapple chunks", "qty": "3 bag frozen"},
                     {"item": "strawberries", "qty": "1 punnet"}],
    )
    tools.add_fact("people", "Emily is allergic to pineapple", hard=True)
    monkeypatch.setattr(
        agent, "generate_weekly_plan_llm", lambda ctx: _full_week(week, meal="Fruit Salad")
    )
    plan = agent.generate_weekly_plan(week)

    result = tools.approve_weekly_plan(
        plan["weekly_plan_id"], approved_by="Emily", confirm_hard_conflicts=True
    )

    # The list really does carry the allergen — this is the bug's evidence,
    # not an aside.
    bought = [i["item"].lower() for i in tools.list_grocery_list("needed")]
    assert any("pineapple" in item for item in bought)

    assert result["status"] == "approved", "a confirmed hard clash still goes through"
    note = result["conflicts_note"]
    assert note, "the week that bought the allergen cannot approve in silence"
    assert "Fruit Salad" in note, "name the meal that put it on the list"
    assert "pineapple" in note.lower()


# ---------- 13. gluten/wheat aliases false-flagging gluten-free dishes ----------

class TestGlutenAliasesDoNotFlagGlutenFreeDishes:
    """
    _ALLERGEN_ALIASES expands "gluten"/"wheat" into flour, pasta and
    noodles so the check reaches "Wheat Pasta" — but that same expansion
    used to flag "Gluten-Free Pasta" made with rice flour, because the
    words "pasta" and "flour" don't know they're sitting in a dish or
    ingredient line that says outright it's safe.
    """

    def _plan_with(self, meal: str) -> int:
        week = _week_start()
        plan = tools.create_weekly_plan(week)
        tools.plan_meal(
            tools._week_dates(week)[0], meal, slot="dinner",
            weekly_plan_id=plan["weekly_plan_id"],
        )
        return plan["weekly_plan_id"]

    def test_gluten_free_pasta_with_rice_flour_does_not_flag(self, kitchen):
        tools.add_recipe(
            "Gluten-Free Pasta",
            ingredients=[{"item": "rice flour", "qty": "200g"},
                         {"item": "eggs", "qty": "2"}],
        )
        tools.set_member_dietary_restrictions("Emily", ["gluten free"])

        assert tools.check_plan_conflicts(self._plan_with("Gluten-Free Pasta"))["conflicts"] == []

    def test_wheat_pasta_still_flags(self, kitchen):
        tools.add_recipe("Wheat Pasta", ingredients=[{"item": "durum wheat", "qty": "200g"}])
        tools.set_member_dietary_restrictions("Emily", ["wheat allergy"])

        found = tools.check_plan_conflicts(self._plan_with("Wheat Pasta"))["conflicts"]

        assert [c["meal"] for c in found] == ["Wheat Pasta"]

    def test_soba_made_with_buckwheat_noodles_does_not_flag(self, kitchen):
        tools.add_recipe("Soba", ingredients=[{"item": "buckwheat noodles", "qty": "200g"}])
        tools.set_member_dietary_restrictions("Emily", ["gluten free"])

        assert tools.check_plan_conflicts(self._plan_with("Soba"))["conflicts"] == []

    def test_udon_noodles_still_flag(self, kitchen):
        tools.add_recipe("Udon", ingredients=[{"item": "udon noodles", "qty": "200g"}])
        tools.set_member_dietary_restrictions("Emily", ["wheat allergy"])

        assert tools.check_plan_conflicts(self._plan_with("Udon"))["conflicts"]

    def test_chickpea_pasta_does_not_flag_gluten(self, kitchen):
        tools.add_recipe("Chickpea Pasta Salad", ingredients=[{"item": "chickpea pasta", "qty": "200g"}])
        tools.set_member_dietary_restrictions("Emily", ["gluten free"])

        assert tools.check_plan_conflicts(self._plan_with("Chickpea Pasta Salad"))["conflicts"] == []

    def test_almond_flour_cake_is_not_gluten_but_is_still_a_nut(self, kitchen):
        tools.add_recipe("Almond Flour Cake", ingredients=[{"item": "almond flour", "qty": "300g"}])

        tools.set_member_dietary_restrictions("Emily", ["gluten free"])
        assert tools.check_plan_conflicts(self._plan_with("Almond Flour Cake"))["conflicts"] == [], \
            "almond flour is not gluten"

        tools.set_member_dietary_restrictions("Emily", ["nut allergy"])
        found = tools.check_plan_conflicts(self._plan_with("Almond Flour Cake"))["conflicts"]
        assert [c["meal"] for c in found] == ["Almond Flour Cake"], \
            "the same dish is still a clash for a nut allergy"

    def test_gf_abbreviation_in_the_name_also_negates(self, kitchen):
        tools.add_recipe("GF Noodle Bowl", ingredients=[{"item": "rice noodles", "qty": "200g"}])
        tools.set_member_dietary_restrictions("Emily", ["gluten free"])

        assert tools.check_plan_conflicts(self._plan_with("GF Noodle Bowl"))["conflicts"] == []


# ---------- 14. backfilling allergy notes already sitting in facts ----------

class TestBackfillAllergyNotesFromFacts:
    """
    Loop Board "Allergy: backfill existing allergy NOTES into member
    restrictions" (Emily's decision 3a): a household that saved an allergy
    as a freeform What-we-know note BEFORE the allergy enforcement fix (see
    this file's module docstring) has that allergy sitting only in `facts`
    — nothing on the member record, which is what a member's own profile
    reads. db._backfill_allergy_notes_from_facts fills that specific gap,
    reusing coordination's own fact parsing (`_fact_keywords`,
    `_named_member`) rather than re-deciding what counts as an avoidance or
    who it's about.
    """

    def _run_backfill(self):
        """
        Migration functions take a connection and leave commit/close to
        the caller (see e.g. the existing
        test_migration_merges_pre_existing_duplicate_rows_keeping_the_newest
        in test_store_memory.py) — this wraps that so each test below
        doesn't have to repeat it.
        """
        conn = get_conn()
        db._backfill_allergy_notes_from_facts(conn)
        conn.commit()
        conn.close()

    def test_a_hard_allergy_phrasing_lands_on_the_member(self, kitchen):
        tools.add_fact("people", "Emily is allergic to pineapple", hard=True)

        self._run_backfill()

        member = next(m for m in tools.list_members() if m["name"] == "Emily")
        assert "allergy: pineapple" in member["dietary_restrictions"]

    def test_the_cant_have_phrasing_is_recognised_too(self, kitchen):
        tools.add_member("Moksha")
        tools.add_fact("people", "Moksha can't have shellfish", hard=True)

        self._run_backfill()

        member = next(m for m in tools.list_members() if m["name"] == "Moksha")
        assert "allergy: shellfish" in member["dietary_restrictions"]

    def test_a_household_wide_fact_is_left_alone(self, kitchen):
        """
        No member is named, so nothing is backfilled — the planner and
        check_plan_conflicts already read `facts` directly for exactly this
        case, so the member record has nothing missing to fill in.
        """
        tools.add_fact("people", "no pork in this house", hard=True)

        self._run_backfill()

        assert tools.list_members()[0]["dietary_restrictions"] == []

    def test_a_requirement_fact_is_skipped(self, kitchen):
        tools.add_fact("people", "Emily needs high-protein dinners", hard=True)

        self._run_backfill()

        member = next(m for m in tools.list_members() if m["name"] == "Emily")
        assert member["dietary_restrictions"] == []

    def test_a_fact_naming_a_non_member_is_skipped(self, kitchen):
        tools.add_fact("people", "Jordan is allergic to peanuts", hard=True)

        self._run_backfill()

        member = next(m for m in tools.list_members() if m["name"] == "Emily")
        assert member["dietary_restrictions"] == []

    def test_running_twice_changes_nothing(self, kitchen):
        tools.add_fact("people", "Emily is allergic to pineapple", hard=True)

        self._run_backfill()
        first = tools.list_members()[0]["dietary_restrictions"]
        self._run_backfill()
        second = tools.list_members()[0]["dietary_restrictions"]

        assert first == second == ["allergy: pineapple"]

    def test_an_existing_allergy_note_is_not_duplicated(self, kitchen):
        tools.set_member_dietary_restrictions("Emily", ["allergy: pineapple"])
        tools.add_fact("people", "Emily is allergic to pineapple", hard=True)

        self._run_backfill()

        member = next(m for m in tools.list_members() if m["name"] == "Emily")
        assert member["dietary_restrictions"].count("allergy: pineapple") == 1

    def test_a_soft_fact_is_backfilled_too(self, kitchen):
        """
        `hard` gates the live safety check (check_plan_conflicts), not this
        backfill: the What-we-know screen never sets `hard` itself (see
        this file's docstring), so a household with a real, unenforced
        allergy note almost never has it marked hard. Restricting the
        backfill to hard=True facts would miss almost everything it exists
        to catch.
        """
        tools.add_fact("people", "Emily is allergic to pineapple", hard=False)

        self._run_backfill()

        member = next(m for m in tools.list_members() if m["name"] == "Emily")
        assert "allergy: pineapple" in member["dietary_restrictions"]

    def test_several_facts_about_the_same_person_all_land(self, kitchen):
        tools.add_fact("people", "Emily is allergic to pineapple", hard=True)
        tools.add_fact("people", "Emily can't have shellfish", hard=True)

        self._run_backfill()

        member = next(m for m in tools.list_members() if m["name"] == "Emily")
        assert "allergy: pineapple" in member["dietary_restrictions"]
        assert "allergy: shellfish" in member["dietary_restrictions"]

    def test_facts_are_never_edited_or_deleted(self, kitchen):
        from app.tools import memory as _memory

        tools.add_fact("people", "Emily is allergic to pineapple", hard=True)

        self._run_backfill()

        facts = _memory.get_facts()
        assert any(f["text"] == "Emily is allergic to pineapple" for f in facts)


def test_a_crashed_allergy_check_asks_rather_than_waving_the_week_through(kitchen, monkeypatch):
    """
    Fail closed. The confirm exists to stop a week going past an allergy;
    a check that crashed has not found nothing, it has found out nothing,
    and approving on that is the exact gap the confirm was built to close.
    """
    week = _week_start()
    tools.add_fact("people", "Emily is allergic to pineapple", hard=True)
    monkeypatch.setattr(
        agent, "generate_weekly_plan_llm", lambda ctx: _full_week(week, meal="Pineapple Chicken")
    )
    plan = agent.generate_weekly_plan(week)
    from app.tools import coordination as _coordination

    def boom(*_a, **_k):
        raise RuntimeError("keyword table exploded")

    monkeypatch.setattr(_coordination, "check_plan_conflicts", boom)

    result = tools.approve_weekly_plan(plan["weekly_plan_id"], approved_by="Emily")

    assert result["status"] == "needs_confirmation"
    assert result["check_failed"] is True
    assert result["conflicts"] == []
    assert "couldn" in result["conflicts_note"] and "allergies" in result["conflicts_note"]
    assert tools.get_weekly_plan(plan["weekly_plan_id"])["status"] == "draft"
    assert tools.list_grocery_list("needed") == []

    # The flag still approves: a broken check must not lock the household
    # out of its own week.
    confirmed = tools.approve_weekly_plan(
        plan["weekly_plan_id"], approved_by="Emily", confirm_hard_conflicts=True
    )
    assert confirmed["status"] == "approved"
    assert tools.get_weekly_plan(plan["weekly_plan_id"])["status"] == "approved"
