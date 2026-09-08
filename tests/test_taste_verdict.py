"""
The SHARED verdict on a dish (Loop Board: "Taste UI: whose verdict?").

Emily, 2026-09-08: "If I say I loved it, and then Vineeth said he hated
it, yes you can suggest it on a night that's just for me. Otherwise, make
it a shared verdict, so likely won't make unless there's an overrule."

Which is: one hater at the table vetoes the dish for that table, and the
overrule is a smaller table — a night the hater isn't eating — rather than
a louder opinion. Four things are pinned here:

  * the helper's truth table (tools.taste_verdict.dish_verdict),
  * that generation is handed those verdicts, per table, and ONLY for
    dishes somebody actually rated (the token cost of this feature),
  * that the post-generation check logs a SOFT conflict — never a block —
    for a hated dish on a night its hater eats, and stays quiet on the
    solo night where the overrule applies,
  * that the household-level rating (recipes.rating, mark_recipe_feedback)
    is untouched by all of it.
"""
from __future__ import annotations

import datetime
import inspect
import json

import pytest

from app import agent, tools
from app.tools import taste_verdict


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
    tools.add_member("Emily")
    tools.add_member("Vineeth")
    return {m["name"]: m["id"] for m in tools.list_members()}


@pytest.fixture
def risotto():
    tools.add_recipe("Mushroom Risotto", ingredients=[{"item": "mushrooms", "qty": "1 lb"}])
    return "Mushroom Risotto"


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


# ---------- the helper's truth table ----------

def test_a_hater_at_the_table_vetoes_the_dish(couple, risotto):
    """Emily loves it, Vineeth doesn't, and both are eating — one hater wins."""
    tools.attribute_recipe_feedback(risotto, "Emily", rating="liked")
    tools.attribute_recipe_feedback(risotto, "Vineeth", rating="disliked")

    verdict = taste_verdict.dish_verdict(risotto, ["Emily", "Vineeth"])

    assert verdict["verdict"] == "avoid"
    assert verdict["vetoed_by"] == ["Vineeth"]
    assert verdict["loved_by"] == ["Emily"], "the veto doesn't erase who loves it"
    assert "Vineeth" in verdict["reason"] and risotto in verdict["reason"]


def test_a_lover_with_nobody_against_is_a_favourite(couple, risotto):
    tools.attribute_recipe_feedback(risotto, "Emily", rating="liked")

    verdict = taste_verdict.dish_verdict(risotto, ["Emily", "Vineeth"])

    assert verdict["verdict"] == "favourite"
    assert verdict["loved_by"] == ["Emily"]
    assert verdict["vetoed_by"] == []


def test_the_hater_not_eating_that_night_is_the_overrule(couple, risotto):
    """Emily's night. Vineeth's 'no' is about a table he isn't at."""
    tools.attribute_recipe_feedback(risotto, "Emily", rating="liked")
    tools.attribute_recipe_feedback(risotto, "Vineeth", rating="disliked")

    verdict = taste_verdict.dish_verdict(risotto, ["Emily"])

    assert verdict["verdict"] == "favourite", "on a night that's just for Emily, it's suggestible"
    assert verdict["vetoed_by"] == []


def test_a_dish_nobody_has_rated_is_neutral(couple, risotto):
    verdict = taste_verdict.dish_verdict(risotto, ["Emily", "Vineeth"])

    assert verdict == {"verdict": "neutral", "reason": "", "vetoed_by": [], "loved_by": []}


def test_a_household_level_rating_alone_never_becomes_a_personal_veto(couple, risotto):
    """
    mark_recipe_feedback keeps working exactly as it does today — it just
    isn't per-person data, so it can't make anyone a hater here.
    """
    tools.mark_recipe_feedback(risotto, rating="disliked", notes="too rich")

    verdict = taste_verdict.dish_verdict(risotto, ["Emily", "Vineeth"])

    assert verdict["verdict"] == "neutral"
    recipe = tools.get_recipe(risotto)
    assert recipe["rating"] == "disliked", "the household rating is untouched"
    assert recipe["feedback_notes"] == "too rich"


def test_an_empty_table_has_no_verdict(couple, risotto):
    tools.attribute_recipe_feedback(risotto, "Vineeth", rating="disliked")

    assert taste_verdict.dish_verdict(risotto, [])["verdict"] == "neutral"


def test_eaters_can_be_member_dicts_and_names_are_matched_case_insensitively(couple, risotto):
    tools.attribute_recipe_feedback(risotto, "Vineeth", rating="disliked")
    members = tools.list_members()

    assert taste_verdict.dish_verdict({"name": risotto}, members)["verdict"] == "avoid"
    assert taste_verdict.dish_verdict(risotto, ["vineeth"])["verdict"] == "avoid"


# ---------- what generation is actually handed ----------

def test_generation_gets_the_whole_table_verdict(couple, risotto, stub_model):
    tools.attribute_recipe_feedback(risotto, "Emily", rating="liked")
    tools.attribute_recipe_feedback(risotto, "Vineeth", rating="disliked")
    week = _week_start()

    seen = stub_model(_full_week(week))
    agent.generate_weekly_plan(week)

    lines = seen["context"]["taste_verdicts"]
    assert lines[0] == "whole table — avoid: Mushroom Risotto"


def test_a_solo_night_gets_its_own_line_overriding_the_whole_table(couple, risotto, stub_model):
    """The named scenario: Vineeth out Thursday, so Thursday says the opposite."""
    tools.attribute_recipe_feedback(risotto, "Emily", rating="liked")
    tools.attribute_recipe_feedback(risotto, "Vineeth", rating="disliked")
    week = _week_start()
    thursday = tools._week_dates(week)[3]
    tools.set_member_attendance(thursday, "dinner", "Vineeth", present=False)

    seen = stub_model(_full_week(week))
    agent.generate_weekly_plan(week)

    lines = seen["context"]["taste_verdicts"]
    assert "whole table — avoid: Mushroom Risotto" in lines
    assert f"{thursday} dinner (Emily) — loved: Mushroom Risotto" in lines


def test_only_dishes_with_per_person_feedback_get_a_verdict_line(couple, risotto, stub_model):
    """A shelf full of recipes and one opinion costs one dish's worth of prompt."""
    for name in ("Chili", "Tacos", "Pad Thai", "Roast Chicken"):
        tools.add_recipe(name, ingredients=[{"item": "something", "qty": "1"}])
    tools.mark_recipe_feedback("Chili", rating="liked")  # household-level only
    tools.attribute_recipe_feedback(risotto, "Vineeth", rating="disliked")
    week = _week_start()

    seen = stub_model(_full_week(week))
    agent.generate_weekly_plan(week)

    lines = seen["context"]["taste_verdicts"]
    assert lines == ["whole table — avoid: Mushroom Risotto"]
    assert "Chili" not in " ".join(lines), "a household rating is not per-person feedback"


def test_no_per_person_feedback_means_no_verdict_block_at_all(couple, risotto, stub_model):
    week = _week_start()

    seen = stub_model(_full_week(week))
    agent.generate_weekly_plan(week)

    assert "taste_verdicts" not in seen["context"], "a cold start costs the prompt nothing"


def test_the_verdict_block_stays_small(couple, stub_model):
    """
    Token budget. Three rated dishes, a week with two different tables in
    it — the whole block has to stay well under a paragraph, because it
    rides on every generation call of every household against the
    $1/household/month target.
    """
    for name in ("Mushroom Risotto", "Chicken Skewers", "Chili"):
        tools.add_recipe(name, ingredients=[{"item": "something", "qty": "1"}])
    tools.attribute_recipe_feedback("Mushroom Risotto", "Vineeth", rating="disliked")
    tools.attribute_recipe_feedback("Chicken Skewers", "Emily", rating="liked")
    tools.attribute_recipe_feedback("Chicken Skewers", "Vineeth", rating="liked")
    tools.attribute_recipe_feedback("Chili", "Emily", rating="disliked")
    week = _week_start()
    thursday = tools._week_dates(week)[3]
    tools.set_member_attendance(thursday, "dinner", "Vineeth", present=False)

    seen = stub_model(_full_week(week))
    agent.generate_weekly_plan(week)

    block = json.dumps(seen["context"]["taste_verdicts"])
    assert len(block) < 300, f"taste verdict block is {len(block)} chars:\n{block}"


def test_a_guests_only_night_reuses_the_whole_table_verdict(couple, risotto, stub_model):
    """
    Guests joining the whole household is a bigger table, not a different
    one — repeating the household's own answer for it would be pure cost.
    """
    tools.attribute_recipe_feedback(risotto, "Vineeth", rating="disliked")
    week = _week_start()
    saturday = tools._week_dates(week)[5]
    tools.set_guest_count(saturday, "dinner", 2)

    seen = stub_model(_full_week(week))
    agent.generate_weekly_plan(week)

    lines = seen["context"]["taste_verdicts"]
    assert lines == ["whole table — avoid: Mushroom Risotto"]


def test_the_prompt_explains_the_shared_verdict_rule():
    """The rule has to actually reach the model, not just the context."""
    src = inspect.getsource(agent.generate_weekly_plan_llm)
    instructions = src[src.index('instructions = f"""'):src.index("Call submit_weekly_plan with the result.")]
    assert "`taste_verdicts`" in instructions
    assert "vetoes it for the whole table" in instructions


# ---------- the post-generation soft conflict ----------

def _plan_one_dinner(week: str, day: str, meal: str) -> int:
    plan = tools.create_weekly_plan(week)
    tools.plan_meal(day, meal, slot="dinner", weekly_plan_id=plan["weekly_plan_id"])
    return plan["weekly_plan_id"]


def test_a_hated_dish_on_a_night_its_hater_eats_is_a_soft_conflict(couple, risotto):
    week = _week_start()
    thursday = tools._week_dates(week)[3]
    tools.attribute_recipe_feedback(risotto, "Vineeth", rating="disliked")
    plan_id = _plan_one_dinner(week, thursday, risotto)

    found = tools.check_plan_conflicts(plan_id)
    taste = [c for c in found["conflicts"] if c["source"] == "member_taste"]

    assert len(taste) == 1
    assert taste[0]["meal"] == risotto
    assert taste[0]["member"] == "Vineeth"
    assert taste[0]["severity"] == "soft", "a taste veto is never a hard block"
    assert taste[0]["date"] == thursday
    assert found["note"] is None, "a dislike is a preference, not a warning band"


def test_no_taste_conflict_on_a_night_the_hater_is_not_eating(couple, risotto):
    """The overrule, end to end: same dish, same week, Vineeth's out."""
    week = _week_start()
    thursday = tools._week_dates(week)[3]
    tools.attribute_recipe_feedback(risotto, "Emily", rating="liked")
    tools.attribute_recipe_feedback(risotto, "Vineeth", rating="disliked")
    tools.set_member_attendance(thursday, "dinner", "Vineeth", present=False)
    plan_id = _plan_one_dinner(week, thursday, risotto)

    found = tools.check_plan_conflicts(plan_id)

    assert [c for c in found["conflicts"] if c["source"] == "member_taste"] == []


def test_a_household_disliked_recipe_raises_no_taste_conflict(couple, risotto):
    """Household-level ratings behave exactly as they did before this rule."""
    week = _week_start()
    thursday = tools._week_dates(week)[3]
    tools.mark_recipe_feedback(risotto, rating="disliked")
    plan_id = _plan_one_dinner(week, thursday, risotto)

    found = tools.check_plan_conflicts(plan_id)

    assert [c for c in found["conflicts"] if c["source"] == "member_taste"] == []


def test_the_soft_conflict_is_logged_and_names_the_night(couple, risotto, caplog):
    week = _week_start()
    thursday = tools._week_dates(week)[3]
    tools.attribute_recipe_feedback(risotto, "Vineeth", rating="disliked")
    plan_id = _plan_one_dinner(week, thursday, risotto)

    with caplog.at_level("WARNING", logger="home_manager"):
        agent._log_plan_conflicts(plan_id, week)

    logged = "\n".join(r.getMessage() for r in caplog.records)
    assert "Vineeth doesn’t like Mushroom Risotto, and they’re home Thursday." in logged
    assert "dietary clash" not in logged, "a taste veto is not a safety warning"


def test_the_plan_itself_is_never_changed_by_a_taste_conflict(couple, risotto):
    week = _week_start()
    thursday = tools._week_dates(week)[3]
    tools.attribute_recipe_feedback(risotto, "Vineeth", rating="disliked")
    plan_id = _plan_one_dinner(week, thursday, risotto)

    agent._log_plan_conflicts(plan_id, week)

    meals = tools.get_weekly_plan(plan_id)["meals"]
    assert [m["meal"] for m in meals] == [risotto]


# ---------- chat ----------

def test_a_chat_swap_reports_the_shared_verdict_back(couple, risotto):
    week = _week_start()
    thursday = tools._week_dates(week)[3]
    tools.attribute_recipe_feedback(risotto, "Vineeth", rating="disliked")
    plan_id = _plan_one_dinner(week, thursday, "Tacos")

    result = tools.swap_meal_in_plan(plan_id, thursday, risotto, slot="dinner")

    assert result["taste_verdict"]["verdict"] == "avoid"
    assert result["taste_verdict"]["vetoed_by"] == ["Vineeth"]
    assert tools.get_weekly_plan(plan_id)["meals"][0]["meal"] == risotto, (
        "reported, never enforced — the swap the household asked for still happened"
    )


def test_a_chat_swap_onto_a_solo_night_reports_nothing_to_worry_about(couple, risotto):
    week = _week_start()
    thursday = tools._week_dates(week)[3]
    tools.attribute_recipe_feedback(risotto, "Emily", rating="liked")
    tools.attribute_recipe_feedback(risotto, "Vineeth", rating="disliked")
    tools.set_member_attendance(thursday, "dinner", "Vineeth", present=False)
    plan_id = _plan_one_dinner(week, thursday, "Tacos")

    result = tools.swap_meal_in_plan(plan_id, thursday, risotto, slot="dinner")

    assert result["taste_verdict"]["verdict"] == "favourite"


def test_a_swap_onto_an_unrated_dish_says_nothing_at_all(couple, risotto):
    week = _week_start()
    thursday = tools._week_dates(week)[3]
    plan_id = _plan_one_dinner(week, thursday, "Tacos")

    result = tools.swap_meal_in_plan(plan_id, thursday, risotto, slot="dinner")

    assert "taste_verdict" not in result
