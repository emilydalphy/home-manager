"""
"How did it go?" stops once anyone has answered it — by name included.

The chat records "Sam loved it, I wasn't keen" per person
(attribute_recipe_feedback) and leaves the household rating empty. The
feedback nudge only looked at the household rating, so the same question
came back on Kitchen's attention card and in every new chat for a week
after it had been answered.
"""
from conftest import household_date

from app import tools
from app.db import get_conn


def _cooked(name: str, days_ago: int = 0) -> int:
    tools.add_recipe(name, ingredients=[{"item": "chicken thigh", "qty": "1 lb"}])
    entry = tools.plan_meal(household_date(-days_ago), name, slot="dinner")
    tools.check_off_meal(entry["entry_id"])
    return entry["entry_id"]


def _nudges() -> list[dict]:
    return [i for i in tools.get_attention_items() if i["kind"] == "feedback_nudge"]


def test_a_verdict_by_name_answers_the_question():
    tools.add_member("Sam")
    _cooked("Chicken Skewers")
    assert _nudges()[0]["summary"] == "How did Chicken Skewers go?"

    tools.attribute_recipe_feedback("Chicken Skewers", "Sam", rating="liked")

    assert _nudges() == []
    assert tools.get_feedback_nudge() == {"has_nudge": False}


def test_a_split_verdict_answers_it_too_and_the_next_meal_is_asked():
    tools.add_member("Sam")
    tools.add_member("Alex")
    soup = _cooked("Lentil Soup", days_ago=1)
    _cooked("Chicken Skewers")
    # Ticked a day earlier, so the skewers are the newest and asked first.
    conn = get_conn()
    conn.execute(
        "UPDATE meal_plan_entries SET cooked_at = datetime('now', '-1 day') WHERE id = ?", (soup,)
    )
    conn.commit()
    conn.close()
    assert [n["summary"] for n in _nudges()] == ["How did Chicken Skewers go?"]

    tools.attribute_recipe_feedback("Chicken Skewers", "Sam", rating="liked")
    tools.attribute_recipe_feedback("Chicken Skewers", "Alex", rating="disliked")

    # The answered dish is gone; the other one still unanswered is asked.
    assert [n["summary"] for n in _nudges()] == ["How did Lentil Soup go?"]


def test_a_verdict_on_another_dish_does_not_answer_this_one():
    tools.add_member("Sam")
    tools.add_recipe("Lentil Soup", ingredients=[{"item": "lentils", "qty": "1 cup"}])
    tools.attribute_recipe_feedback("Lentil Soup", "Sam", rating="liked")
    _cooked("Chicken Skewers")

    assert [n["summary"] for n in _nudges()] == ["How did Chicken Skewers go?"]


def test_a_household_rating_still_answers_it():
    _cooked("Chicken Skewers")
    assert [n["summary"] for n in _nudges()] == ["How did Chicken Skewers go?"]

    tools.mark_recipe_feedback("Chicken Skewers", "liked")

    assert _nudges() == []
