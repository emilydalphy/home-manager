"""
A lunch answer said in chat lands under the member's own spelling (defect
hunt, 2026-10-12).

Lunch facts are keyed by name. A member typed in at setup as "vineeth" and
named "Vineeth" in chat ("Vineeth's in the office Fridays") used to get a
second, unread entry: the rhythm map carried both keys, every reader looked
under "vineeth", and Friday still planned him a lunch at home.
"""
from app import tools


def _adult(name):
    tools.add_member(name)
    tools.set_member_age_group(name, "adult")


def test_chat_spelling_lands_on_the_member_it_names():
    _adult("vineeth")
    tools.set_lunch_location("vineeth", "home")
    result = tools.set_lunch_location("Vineeth", "out", weekday="Friday")

    assert result["member_name"] == "vineeth"
    assert list(tools.get_household_rhythm()["lunch_location"]) == ["vineeth"]
    assert tools.effective_lunch_location("vineeth", "Friday") == "out"
    assert tools.effective_lunch_location("vineeth", "Monday") == "home"


def test_clearing_an_override_finds_it_under_any_case():
    _adult("Marcus")
    tools.set_lunch_location("Marcus", "home")
    tools.set_lunch_location("Marcus", "out", weekday="Tuesday")
    tools.clear_lunch_location_override("marcus", "Tuesday")
    assert tools.effective_lunch_location("Marcus", "Tuesday") == "home"


def test_a_name_with_no_member_yet_is_kept_as_given():
    """Setup can say where someone eats lunch before their row exists."""
    tools.set_lunch_location("Priya", "out")
    assert tools.get_household_rhythm()["lunch_location"] == {"Priya": {"standing": "out", "overrides": {}}}


def test_the_bulk_save_lands_on_the_member_it_names_too():
    """save_rhythm_answers (onboarding / Settings) keys the same way."""
    _adult("vineeth")
    tools.save_rhythm_answers(lunch_location={"Vineeth": "out"})
    assert list(tools.get_household_rhythm()["lunch_location"]) == ["vineeth"]
    assert tools.effective_lunch_location("vineeth", "Monday") == "out"
