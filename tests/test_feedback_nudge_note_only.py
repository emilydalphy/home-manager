"""A note-only answer ("a bit too spicy", no Liked / Not a hit) stops "How did it go?"."""
from conftest import household_date

from app import tools
from app.db import get_conn


def _cooked(name: str) -> None:
    tools.add_recipe(name, ingredients=[{"item": "chicken thigh", "qty": "1 lb"}])
    entry = tools.plan_meal(household_date(0), name, slot="dinner")
    tools.check_off_meal(entry["entry_id"])


def _asked() -> list[str]:
    return [i["summary"] for i in tools.get_attention_items() if i["kind"] == "feedback_nudge"]


def test_household_note_without_verdict_answers_it():
    _cooked("Chicken Skewers")
    assert _asked() == ["How did Chicken Skewers go?"]
    tools.mark_recipe_feedback("Chicken Skewers", None, notes="a bit too spicy")
    assert _asked() == []
    assert tools.get_feedback_nudge() == {"has_nudge": False}


def test_one_off_note_answers_it():
    _cooked("Chicken Skewers")
    tools.log_recipe_note("Chicken Skewers", "a bit too spicy")
    assert _asked() == []


def test_per_person_note_answers_it():
    tools.add_member("Sam")
    _cooked("Chicken Skewers")
    tools.attribute_recipe_feedback("Chicken Skewers", "Sam", rating="liked", notes="a bit too spicy")
    assert _asked() == []


def test_note_on_another_dish_does_not_answer_this_one():
    tools.add_recipe("Lentil Soup", ingredients=[{"item": "lentils", "qty": "1 cup"}])
    tools.mark_recipe_feedback("Lentil Soup", None, notes="needs salt")
    tools.log_recipe_note("Lentil Soup", "needs salt")
    _cooked("Chicken Skewers")
    assert _asked() == ["How did Chicken Skewers go?"]


def test_blank_note_does_not_answer_it():
    _cooked("Chicken Skewers")
    conn = get_conn()
    conn.execute("UPDATE recipes SET feedback_notes = '   '")
    conn.execute(
        "INSERT INTO recipe_notes (household_id, recipe_id, note_type, note) "
        "SELECT household_id, id, 'feedback', '  ' FROM recipes"
    )
    conn.commit()
    conn.close()
    assert _asked() == ["How did Chicken Skewers go?"]
