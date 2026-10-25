"""
What we know keeps a thing said twice as one thing (defect hunt 2026-10-25).

Found on a real server: chat telling us "Sam is allergic to sesame" a second
time wrote a second fact row — two lines on What we know, the allergy twice
in every must_not_contain, and a sesame dish "a clash with two things Sam
can't have". The same for lists: ["Olives", "olives "] saved as two Won't eat
chips, "Thai, thai" as two cuisines, chat's "olives" beside a saved "Olives"
as a second dislike. And a fact filed under a word that is no tab
("Banana") was drawn nowhere — nothing could edit or delete it — while the
planner and the allergy check still read it.
"""
from __future__ import annotations

import pytest

from app import tools


@pytest.fixture
def household():
    tools.add_member("Sam")
    tools.add_member("Emily")


def test_a_fact_said_again_in_other_capitals_is_the_one_already_there(household):
    first = tools.add_fact("people", "Sam is allergic to sesame", hard=True)
    again = tools.add_fact("people", "  sam is ALLERGIC to  sesame. ", hard=True)
    assert again["added"] is False and again["already_known"] is True
    assert again["id"] == first["id"]
    facts = tools.get_facts("people")
    assert [f["text"] for f in facts] == ["Sam is allergic to sesame"]
    # Once in what the planner is told, once in the banner's reckoning.
    assert tools.swap_hard_exclusions().count("Sam is allergic to sesame") == 1
    clashes = tools.check_meal_conflicts("Sesame Chicken")
    assert len(clashes) == 1


def test_saying_it_as_a_must_avoid_makes_the_one_fact_hard_and_never_the_reverse(household):
    soft = tools.add_fact("people", "Sam is allergic to sesame")
    assert tools.check_meal_conflicts("Sesame Chicken") == []
    hard = tools.add_fact("people", "Sam is allergic to sesame", hard=True)
    assert hard["id"] == soft["id"] and hard["made_hard"] is True and hard["hard"] is True
    assert len(tools.check_meal_conflicts("Sesame Chicken")) == 1
    tools.add_fact("people", "Sam is allergic to sesame", hard=False)
    assert tools.get_facts("people")[0]["hard"] == 1


def test_different_words_or_another_tab_are_still_their_own_fact(household):
    tools.add_fact("people", "Sam is allergic to sesame")
    tools.add_fact("people", "Sam is allergic to sesame seeds")
    tools.add_fact("taste", "Sam is allergic to sesame")
    assert len(tools.get_facts()) == 3


def test_chat_saying_a_fact_twice_writes_one_row(household):
    tools.add_fact_from_chat("rhythm", "Pizza night is Friday")
    tools.add_fact_from_chat("rhythm", "pizza night is friday!")
    assert len(tools.get_facts("rhythm")) == 1


def test_a_fact_under_a_word_that_is_no_tab_is_refused(signed_in):
    res = signed_in.post("/api/facts/add", json={"category": "Banana", "text": "Sam hates beets"})
    assert res.status_code == 400
    assert "People, Taste or Rhythm" in res.json()["detail"]
    assert tools.get_facts() == []
    ok = signed_in.post("/api/facts/add", json={"category": "taste", "text": "Sam hates beets"})
    assert ok.status_code == 200 and ok.json()["added"] is True


@pytest.mark.parametrize("field,value,expected", [
    ("dislikes", ["Olives", "olives ", " OLIVES", "mushrooms"], ["Olives", "mushrooms"]),
    ("cuisine_preferences", "Thai, thai, Mexican", ["Thai", "Mexican"]),
    ("kitchen_kit", ["air_fryer", "Air_fryer"], ["air_fryer"]),
])
def test_a_list_keeps_each_thing_once_in_the_first_spelling(field, value, expected):
    assert tools.edit_preference(field, value)[field] == expected
    assert tools.get_household_memory()[field] == expected


def test_chat_adding_a_dislike_already_saved_in_other_capitals_adds_nothing():
    tools.add_food_dislikes(["Olives"])
    saved = tools.add_food_dislikes(["olives", " OLIVES ", "capers"])
    assert saved["dislikes"] == ["Olives", "capers"]


def test_onboarding_and_the_chat_cuisine_write_keep_each_once():
    tools.save_onboarding_answers(
        member_names=["Emily"], household_restrictions={}, eating_style="",
        wont_eat=["cilantro", "Cilantro"], excited_about=["Thai", "THAI ", "Italian"],
        dinners_per_week=5, breakfasts_per_week=7, lunches_per_week=7,
    )
    memory = tools.get_household_memory()
    assert memory["dislikes"] == ["cilantro"]
    assert memory["cuisine_preferences"] == ["Thai", "Italian"]


def test_a_protein_rating_is_saved_under_its_name_without_stray_spaces():
    tools.edit_preference("protein_preferences", {"Chicken ": 5})
    assert tools.get_household_memory()["protein_preferences"] == {"Chicken": 5}
    with pytest.raises(ValueError):
        tools.edit_preference("protein_preferences", {"  ": 5})
    assert tools.get_household_memory()["protein_preferences"] == {"Chicken": 5}


# ---------- review round, 2026-10-25 ----------

def test_one_hard_sentence_under_two_tabs_is_one_rule_where_it_is_read(household):
    tools.add_fact("people", "Sam is allergic to sesame", hard=True)
    tools.add_fact("taste", "sam is allergic to sesame.", hard=True)
    assert len(tools.get_facts()) == 2  # both rows stay, each on its tab
    assert sum("sesame" in line.lower() for line in tools.swap_hard_exclusions()) == 1
    assert len(tools.check_meal_conflicts("Sesame Chicken")) == 1


def test_chat_cannot_file_a_fact_on_no_tab(household):
    result = tools.add_fact_from_chat("preferences", "Sam hates beets")
    assert result["added"] is False
    assert tools.get_facts() == []
    assert tools.add_fact_from_chat("taste", "Sam hates beets")["added"] is True


def test_a_fact_said_again_in_chat_draws_no_card_unless_it_became_hard(household):
    import json
    from app import agent
    tools.add_fact("people", "Sam is allergic to sesame")
    again = tools.add_fact_from_chat("people", "Sam is allergic to sesame")
    assert agent.result_changed_nothing(json.dumps(again)) is True
    hardened = tools.add_fact_from_chat("people", "Sam is allergic to sesame", hard=True)
    assert agent.result_changed_nothing(json.dumps(hardened)) is False
