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

from app import agent, tools


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

    result = tools.approve_weekly_plan(plan["weekly_plan_id"], approved_by="Emily")

    # Approval says it out loud once...
    assert {c["meal"] for c in result["conflicts"]} == {"Pineapple Chicken"}
    assert result["status"] == "approved", "a clash warns, it never blocks"
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
