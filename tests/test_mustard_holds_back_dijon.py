"""
A mustard allergy holds back Dijon (defect hunt, 2026-10-20).

Mustard is a Canadian priority allergen, and recipes name the jar after
the town: "2 tbsp Dijon". Matched as the word "mustard" only, a member
with a mustard allergy had Dijon chicken pass the hard gate.
"""
import pytest

from app import tools
from app.tools import allergen_gate


@pytest.mark.parametrize("restriction", ["mustard", "Mustard allergy", "allergic to mustard"])
@pytest.mark.parametrize("item", ["Dijon", "dijon mustard", "whole-grain mustard", "piccalilli"])
def test_a_mustard_allergy_holds_back_the_jar_whatever_its_name(restriction, item):
    tools.add_member("Maya")
    tools.set_member_dietary_restrictions("Maya", [restriction])

    assert allergen_gate.hard_clashes("Chicken Dinner", [{"item": item, "qty": "2 tbsp"}])


def test_nobody_with_no_mustard_allergy_loses_dijon():
    tools.add_member("Maya")
    tools.set_member_dietary_restrictions("Maya", ["peanut"])

    assert allergen_gate.hard_clashes("Dijon Chicken", [{"item": "Dijon", "qty": "2 tbsp"}]) == []
