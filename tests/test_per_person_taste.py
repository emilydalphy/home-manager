"""
Per-person taste learning + solo-night personalization (Loop Board:
"Per-person taste learning + solo-night personalization").

Household-level recipe feedback (recipes.rating/feedback_notes, set via
mark_recipe_feedback) stays exactly as it was and remains the fallback
default everywhere. member_recipe_feedback is strictly ADDITIVE — a more
specific answer than the household's, when one exists.

Two ways a per-person row gets written:
  * explicit  — attribute_recipe_feedback, called the moment a rating comes
    with a name attached in chat ("Vineeth loved the skewers", or "that was
    just my rating" correcting an existing household one).
  * solo_auto — mark_recipe_feedback silently attributes to whoever was the
    only person home for the last time this recipe was actually cooked
    (checked off), per DESIGN_SYSTEM.md §7's silent-learning rule. An
    'explicit' row is never clobbered by a later 'solo_auto' guess.

get_member_taste is the read-back ("what does Vineeth like?"), and
_attach_personal_context_for_subset_slots (app.agent) is the generation
wiring: a subset-attendance slot gets a `personal_context` block for its
present people, used to weight/reason generation personally, while
honestly falling back to the household level wherever there's no
per-person data yet (cold start).
"""
from __future__ import annotations

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
def couple():
    """Emily and Vineeth."""
    tools.add_member("Emily")
    tools.add_member("Vineeth")
    return {m["name"]: m["id"] for m in tools.list_members()}


@pytest.fixture
def recipe():
    tools.add_recipe(
        "Chicken Skewers",
        ingredients=[{"item": "chicken thigh", "qty": "1 lb"}, {"item": "skewers", "qty": "8"}],
    )
    return "Chicken Skewers"


@pytest.fixture
def stub_model(monkeypatch):
    seen = {}

    def _stub(days):
        def _fake(context):
            seen["context"] = context
            return days
        monkeypatch.setattr(agent, "generate_weekly_plan_llm", _fake)
        return seen

    return _stub


def _cook_solo(recipe_name: str, date_str: str, slot: str, absent_member: str) -> int:
    """Plan + check off a recipe for a meal where absent_member is the only one missing."""
    tools.set_member_attendance(date_str, slot, absent_member, present=False)
    entry = tools.plan_meal(date_str, recipe_name, slot=slot)
    tools.check_off_meal(entry["entry_id"])
    return entry["entry_id"]


# ---------- explicit chat attribution ----------

def test_attribute_recipe_feedback_with_an_explicit_rating(couple, recipe):
    """'Vineeth loved the skewers' — a name and a rating together."""
    result = tools.attribute_recipe_feedback(recipe, "Vineeth", rating="liked")

    assert result == {"name": recipe, "member": "Vineeth", "rating": "liked", "source": "explicit"}
    taste = tools.get_member_taste("Vineeth")
    assert taste["liked_recipes"] == [recipe]
    assert taste["has_any_data"] is True
    # The household-level rating is untouched — this is additive, not a replacement.
    assert tools.get_recipe(recipe)["rating"] is None


def test_attribute_recipe_feedback_reuses_the_existing_household_rating_when_omitted(couple, recipe):
    """'That was just my rating' — correcting an existing household rating to belong to one person."""
    tools.mark_recipe_feedback(recipe, rating="liked")

    result = tools.attribute_recipe_feedback(recipe, "Emily")

    assert result["rating"] == "liked"
    assert tools.get_member_taste("Emily")["liked_recipes"] == [recipe]


def test_attribute_recipe_feedback_without_any_rating_to_reuse_is_an_error(couple, recipe):
    with pytest.raises(ValueError, match="no rating yet"):
        tools.attribute_recipe_feedback(recipe, "Emily")


def test_attribute_recipe_feedback_overwrites_its_own_previous_attribution(couple, recipe):
    """A later correction for the same person replaces rather than accumulates."""
    tools.attribute_recipe_feedback(recipe, "Vineeth", rating="liked")
    tools.attribute_recipe_feedback(recipe, "Vineeth", rating="disliked")

    taste = tools.get_member_taste("Vineeth")
    assert taste["liked_recipes"] == []
    assert taste["disliked_recipes"] == [recipe]


def test_attribute_recipe_feedback_creates_an_unknown_member(couple, recipe):
    """Matches the get-or-create convention set_member_dietary_restrictions already follows."""
    tools.attribute_recipe_feedback(recipe, "Grandma Rose", rating="liked")
    assert "Grandma Rose" in {m["name"] for m in tools.list_members()}


def test_get_member_taste_unknown_member_is_an_error(couple):
    with pytest.raises(ValueError, match="No household member"):
        tools.get_member_taste("Nobody")


# ---------- solo-night auto-attribution ----------

def test_solo_night_auto_attributes_the_one_person_who_was_there(couple, recipe):
    week = _week_start()
    thursday = tools._week_dates(week)[3]
    _cook_solo(recipe, thursday, "dinner", absent_member="Vineeth")

    result = tools.mark_recipe_feedback(recipe, rating="liked")

    assert result["solo_auto_attribution"] == "Emily"
    taste = tools.get_member_taste("Emily")
    assert taste["liked_recipes"] == [recipe]
    # Vineeth wasn't there — no per-person data invented for him.
    assert tools.get_member_taste("Vineeth")["has_any_data"] is False


def test_solo_auto_attribution_is_logged_to_preference_events(couple, recipe):
    from app.db import get_conn

    week = _week_start()
    thursday = tools._week_dates(week)[3]
    _cook_solo(recipe, thursday, "dinner", absent_member="Vineeth")
    tools.mark_recipe_feedback(recipe, rating="liked")

    conn = get_conn()
    row = conn.execute(
        "SELECT field, action FROM preference_events WHERE field = ?",
        (f"member:Emily:recipe:{recipe}",),
    ).fetchone()
    conn.close()
    assert row is not None and row["action"] == "write"


def test_no_auto_attribution_when_everyone_was_home(couple, recipe):
    """The ordinary case: a rating with a full table stays household-level only."""
    entry = tools.plan_meal(tools._week_dates(_week_start())[0], recipe, slot="dinner")
    tools.check_off_meal(entry["entry_id"])

    result = tools.mark_recipe_feedback(recipe, rating="liked")

    assert result["solo_auto_attribution"] is None
    assert tools.get_member_taste("Emily")["has_any_data"] is False
    assert tools.get_member_taste("Vineeth")["has_any_data"] is False


def test_no_auto_attribution_when_the_recipe_was_never_actually_cooked(couple, recipe):
    """Planned but never checked off tells us nothing about who ate it."""
    week = _week_start()
    thursday = tools._week_dates(week)[3]
    tools.set_member_attendance(thursday, "dinner", "Vineeth", present=False)
    tools.plan_meal(thursday, recipe, slot="dinner")  # never check_off_meal'd

    result = tools.mark_recipe_feedback(recipe, rating="liked")

    assert result["solo_auto_attribution"] is None


def test_no_auto_attribution_when_a_guest_makes_it_not_actually_solo(couple, recipe):
    """One household member plus a guest is two mouths, not a solo night."""
    week = _week_start()
    thursday = tools._week_dates(week)[3]
    tools.set_member_attendance(thursday, "dinner", "Vineeth", present=False)
    tools.set_guest_count(thursday, "dinner", 1)
    entry = tools.plan_meal(thursday, recipe, slot="dinner")
    tools.check_off_meal(entry["entry_id"])

    result = tools.mark_recipe_feedback(recipe, rating="liked")

    assert result["solo_auto_attribution"] is None


def test_an_explicit_attribution_is_never_overwritten_by_a_later_solo_guess(couple, recipe):
    """A stated fact outranks a silent guess, even one that comes in afterward."""
    tools.attribute_recipe_feedback(recipe, "Emily", rating="disliked")

    week = _week_start()
    thursday = tools._week_dates(week)[3]
    _cook_solo(recipe, thursday, "dinner", absent_member="Vineeth")
    result = tools.mark_recipe_feedback(recipe, rating="liked")

    assert result["solo_auto_attribution"] is None
    assert tools.get_member_taste("Emily")["disliked_recipes"] == [recipe], (
        "the explicit 'disliked' must survive the solo-night 'liked' guess"
    )


def test_solo_auto_attribution_does_not_fire_without_a_rating(couple, recipe):
    """mark_recipe_feedback(notes-only, no rating) has no verdict to attribute."""
    week = _week_start()
    thursday = tools._week_dates(week)[3]
    _cook_solo(recipe, thursday, "dinner", absent_member="Vineeth")

    result = tools.mark_recipe_feedback(recipe, notes="a bit dry")

    assert result["solo_auto_attribution"] is None
    assert tools.get_member_taste("Emily")["has_any_data"] is False


# ---------- generation weighting: personal_context on subset slots ----------

def test_subset_slot_gets_personal_context_for_the_present_person(couple, recipe, stub_model):
    tools.set_member_dietary_restrictions("Emily", ["pescatarian"])
    tools.attribute_recipe_feedback(recipe, "Emily", rating="liked")
    week = _week_start()
    thursday = tools._week_dates(week)[3]
    tools.set_member_attendance(thursday, "dinner", "Vineeth", present=False)

    seen = stub_model(_full_week(week))
    agent.generate_weekly_plan(week)

    slots = {(s["date"], s["slot"]): s for s in seen["context"]["attendance"]["slots_with_a_different_table"]}
    personal = slots[(thursday, "dinner")]["personal_context"]
    assert personal["Emily"]["dietary_restrictions"] == ["pescatarian"]
    assert personal["Emily"]["liked_recipes"] == [recipe]
    assert "Vineeth" not in personal, "an absent person isn't part of that meal's personal context"


def test_subset_slot_with_no_per_person_taste_stays_an_honest_cold_start(couple, recipe, stub_model):
    """
    Restrictions are always real, known data (an empty list IS an answer —
    "this person has none"), so personal_context still appears with it. But
    nobody has any per-person TASTE yet, so no liked/disliked_recipes should
    be invented for Emily — that's the actual cold-start gap this ticket is
    honest about.
    """
    week = _week_start()
    thursday = tools._week_dates(week)[3]
    tools.set_member_attendance(thursday, "dinner", "Vineeth", present=False)

    seen = stub_model(_full_week(week))
    agent.generate_weekly_plan(week)

    slots = {(s["date"], s["slot"]): s for s in seen["context"]["attendance"]["slots_with_a_different_table"]}
    emily_context = slots[(thursday, "dinner")]["personal_context"]["Emily"]
    assert emily_context == {"dietary_restrictions": []}
    assert "liked_recipes" not in emily_context
    assert "disliked_recipes" not in emily_context


def test_a_guests_only_slot_gets_no_personal_context(couple, recipe, stub_model):
    """Guests joining the whole regular household isn't a 'you-night' — nobody is away."""
    week = _week_start()
    saturday = tools._week_dates(week)[5]
    tools.attribute_recipe_feedback(recipe, "Emily", rating="liked")
    tools.set_guest_count(saturday, "dinner", 2)

    seen = stub_model(_full_week(week))
    agent.generate_weekly_plan(week)

    slots = {(s["date"], s["slot"]): s for s in seen["context"]["attendance"]["slots_with_a_different_table"]}
    assert "personal_context" not in slots[(saturday, "dinner")]


def test_emily_solo_thursday_generates_with_her_own_taste_context(couple, recipe, stub_model):
    """
    The brief's named end-to-end scenario: Vineeth out Thursday, Emily has
    her own recorded likes/dislikes and a dietary restriction Vineeth
    doesn't share — generation for that one slot sees exactly her personal
    context, and every other slot in the same week is untouched.
    """
    tools.add_recipe("Mushroom Risotto", ingredients=[{"item": "mushrooms", "qty": "1 lb"}])
    tools.set_member_dietary_restrictions("Emily", ["pescatarian"])
    tools.set_member_dietary_restrictions("Vineeth", ["peanut allergy"])
    tools.attribute_recipe_feedback(recipe, "Emily", rating="liked")
    tools.attribute_recipe_feedback("Mushroom Risotto", "Vineeth", rating="disliked")

    week = _week_start()
    thursday, friday = tools._week_dates(week)[3], tools._week_dates(week)[4]
    tools.set_member_attendance(thursday, "dinner", "Vineeth", present=False)

    seen = stub_model(_full_week(week))
    agent.generate_weekly_plan(week)

    slots = {(s["date"], s["slot"]): s for s in seen["context"]["attendance"]["slots_with_a_different_table"]}
    thursday_personal = slots[(thursday, "dinner")]["personal_context"]
    assert thursday_personal["Emily"]["dietary_restrictions"] == ["pescatarian"]
    assert thursday_personal["Emily"]["liked_recipes"] == [recipe]
    assert "Vineeth" not in thursday_personal

    # Friday is an ordinary, everyone-home night — not in the deviating list at all.
    assert (friday, "dinner") not in slots


# ---------- tool wiring ----------

def test_the_new_tools_are_declared_and_callable():
    declared = {d["name"] for d in agent.TOOL_DEFINITIONS}
    for name in ("attribute_recipe_feedback", "get_member_taste"):
        assert name in declared
        assert name in agent.TOOL_FUNCTIONS


def test_the_new_tool_schemas_match_their_python_signatures():
    import inspect

    for name in ("attribute_recipe_feedback", "get_member_taste"):
        definition = next(d for d in agent.TOOL_DEFINITIONS if d["name"] == name)
        fn = agent.TOOL_FUNCTIONS[name]
        real_params = set(inspect.signature(fn).parameters)
        declared = set(definition["input_schema"]["properties"])
        assert declared <= real_params, f"{name} declares {declared - real_params}, which it cannot accept"


def test_attribute_recipe_feedback_works_through_the_chat_tool(couple, recipe):
    result = agent.TOOL_FUNCTIONS["attribute_recipe_feedback"](
        recipe_name=recipe, member_name="Vineeth", rating="liked",
    )
    assert result["member"] == "Vineeth"
