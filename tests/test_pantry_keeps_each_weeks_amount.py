"""
Sauces and pantry staples keep each week's amount on its own line (Emily,
2026-10-31, the recommendation): last week's unbought leftover is asked about
separately, never added onto. Spices keep the one-jar-per-name rule (pinned in
tests/test_integration_hunt_2026_10_26.py).

Found: last week's waiting "Red lentils 3 cups" became the household's line
when a person added lentils onto it, and next week approved on the Sunday
summed its 3 cups on — "Red lentils 6 cups" when the week needs 3.
"""
from __future__ import annotations

from app import tools

from test_integration_hunt_2026_10_26 import NEXT, NEXT_SAT, SAT, WEEK, _approved_week, _lines

DAL = [{"item": "Red lentils", "qty": "3 cups", "category": "pantry"},
       {"item": "Onion", "qty": "1", "category": "produce"}]
SUNDAY = "2026-11-08 10:00:00"


def _person_adds_onto_this_weeks_lentils(frozen_today) -> int:
    frozen_today(f"{WEEK} 09:00:00")
    plan_a, _ = _approved_week(WEEK, "Dal", SAT, DAL)
    added = tools.add_grocery_item("Red lentils", category="pantry")
    [line] = _lines("red lentils")
    assert added["merged"] and line["source_weekly_plan_id"] is None, "premise: the line is the household's now"
    assert line["quantity"] == "3 cups"
    return plan_a


def test_next_weeks_lentils_do_not_add_onto_last_weeks_unbought_line(frozen_today):
    _person_adds_onto_this_weeks_lentils(frozen_today)
    frozen_today(SUNDAY)
    plan_b, _ = _approved_week(NEXT, "Dal", NEXT_SAT, DAL)
    lines = {(line["source_weekly_plan_id"], line["quantity"]) for line in _lines("red lentils")}
    assert lines == {(None, "3 cups"), (plan_b, "3 cups")}, lines


def test_on_monday_last_weeks_lentils_are_asked_about_on_their_own(frozen_today):
    plan_a = _person_adds_onto_this_weeks_lentils(frozen_today)
    frozen_today(SUNDAY)
    plan_b, _ = _approved_week(NEXT, "Dal", NEXT_SAT, DAL)
    frozen_today(f"{NEXT} 08:00:00")
    tools.set_aside_ended_weeks()
    live = sorted((line["source_weekly_plan_id"], line["status"], line["quantity"])
                  for line in _lines("red lentils") if line["status"] in ("needed", "spice", "carried"))
    assert live == [(plan_a, "carried", "3 cups"), (plan_b, "spice", "3 cups")], live


def test_a_household_line_holding_this_weeks_own_share_still_takes_more_of_this_week(frozen_today):
    """GUARD. The rule is about ANOTHER week's share: more of this same
    week's lentils still adds onto the line already holding them."""
    plan_a = _person_adds_onto_this_weeks_lentils(frozen_today)
    tools.add_grocery_item("Red lentils", quantity="1 cup", category="pantry", source_weekly_plan_id=plan_a)
    assert [line["quantity"] for line in _lines("red lentils")] == ["4 cups"]
