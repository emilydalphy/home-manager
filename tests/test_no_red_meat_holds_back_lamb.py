"""
"No red meat" holds back lamb (defect hunt, 2026-10-20).

"Red meat" is a category no ingredient line says. Read as the phrase "red"
+ "meat", a member with "no red meat" had Lamb Kofta, beef stew and veal
pass the hard gate — only a line that literally said "red meat" was held.
Pork is counted (nutrition guidance files it as red meat; over-safe is the
right way for a hard gate to be wrong). Chicken, fish and the other "red"
foods stay on the table.
"""
import pytest

from app import tools
from app.tools import allergen_gate


RED_MEAT = ["ground lamb", "lamb shoulder", "stewing beef", "veal cutlets", "venison",
            "goat curry cut", "bison burgers", "pork chops", "bacon", "sirloin", "mutton"]
NOT_RED_MEAT = ["chicken thighs", "ground turkey", "salmon fillet", "red peppers",
                "red wine", "red lentils", "red onion", "goat cheese", "lamb's lettuce"]


@pytest.mark.parametrize("restriction", ["no red meat", "red meat", "No red meats"])
@pytest.mark.parametrize("item", RED_MEAT)
def test_no_red_meat_holds_back_the_red_meats(restriction, item):
    tools.add_member("Maya")
    tools.set_member_dietary_restrictions("Maya", [restriction])

    assert allergen_gate.hard_clashes("Dinner", [{"item": item, "qty": "1 lb"}])


@pytest.mark.parametrize("item", NOT_RED_MEAT)
def test_no_red_meat_leaves_chicken_fish_and_red_vegetables_alone(item):
    tools.add_member("Maya")
    tools.set_member_dietary_restrictions("Maya", ["no red meat"])

    assert allergen_gate.hard_clashes("Dinner", [{"item": item, "qty": "1 lb"}]) == []


def test_the_reported_lamb_kofta_is_held_back():
    tools.add_member("Maya")
    tools.set_member_dietary_restrictions("Maya", ["no red meat"])

    assert allergen_gate.hard_clashes("Lamb Kofta", [{"item": "ground lamb", "qty": "1 lb"}])


def test_a_hard_what_we_know_note_holds_back_beef_too():
    tools.add_member("Maya")
    tools.add_fact("people", "No red meat for Maya", hard=True)

    assert allergen_gate.hard_clashes("Beef Stew", [{"item": "stewing beef", "qty": "600g"}])
    assert allergen_gate.hard_clashes("Chicken Stew", [{"item": "chicken thighs", "qty": "600g"}]) == []


def test_an_avoidance_of_goat_itself_still_holds_back_goat_cheese():
    """The goat-cheese carve-out is red meat's, not a goat avoidance's."""
    tools.add_member("Maya")
    tools.set_member_dietary_restrictions("Maya", ["goat"])

    assert allergen_gate.hard_clashes("Salad", [{"item": "goat cheese", "qty": "100g"}])
