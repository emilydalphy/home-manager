"""
A household's own grocery line (a standing want, source_weekly_plan_id IS
NULL) that they edited BELOW the plan's share went blank when the plan's
meals came off it — clearing the week, or dropping the last night that
used it. grocery._restate_standing_want derives "the household's own" as
what the line reads less what the plan put on, floors that at nothing, and
with no meals left wrote nothing: "Onions · " on the list, the number they
typed gone.

The rule held to (docs/DECISION_LOG.md, the restate entry): "a line the
household typed is never blanked", and Emily's 2026-10-12 call that an
edit below what the plan needs is kept. So when the plan's meals come off a
line edited below them, the line stays at what the household typed.
"""
import datetime

import pytest

from conftest import household_today

from app import tools
from app.tools import grocery

START = household_today()


@pytest.fixture
def family_of_four():
    for name in ("Emily", "Vineeth", "Rae", "Kit"):
        tools.add_member(name)


def _line(item: str):
    return next((i for i in tools.list_grocery_list() if i["item"] == item), None)


def _onion_week(nights: int) -> tuple[int, list[int]]:
    tools.add_recipe(
        "Onion Bake", ingredients=[{"item": "Onions", "qty": "1", "category": "produce"}],
        default_servings=4,
    )
    plan_id = tools.create_weekly_plan(START.isoformat())["weekly_plan_id"]
    entries = [
        tools.plan_meal(
            (START + datetime.timedelta(days=i)).isoformat(), "Onion Bake",
            slot="dinner", weekly_plan_id=plan_id,
        )["entry_id"]
        for i in range(nights)
    ]
    tools.approve_weekly_plan(plan_id, "Emily")
    return plan_id, entries


def _own_line_edited_to(typed: str, nights: int = 2):
    """A hand-added onion, two dinners wanting one each on top (3), then
    the household types the line down below the plan's 2."""
    tools.add_grocery_item("Onions", quantity="1", category="produce")
    plan_id, entries = _onion_week(nights)
    line = _line("Onions")
    assert line["quantity"] == str(1 + nights)
    tools.update_grocery_item(line["id"], quantity=typed)
    assert _line("Onions")["quantity"] == typed
    return plan_id, entries


def test_clearing_the_week_keeps_a_line_typed_below_the_plans_share(family_of_four):
    plan_id, _ = _own_line_edited_to("1")

    tools.clear_weekly_plan(plan_id)

    line = _line("Onions")
    assert line is not None, "the household's own line is never deleted"
    assert line["quantity"] == "1", "what the household typed stays on the line"


def test_dropping_every_night_one_at_a_time_keeps_what_they_typed(family_of_four):
    _, entries = _own_line_edited_to("1")

    for entry in entries:
        grocery._reverse_meal_grocery_contributions(entry)
        assert _line("Onions")["quantity"] == "1"


def test_a_line_left_alone_still_comes_back_to_the_households_own(family_of_four):
    """The ordinary case is untouched: no edit, and clearing the week takes
    the plan's two back off to the one the household asked for."""
    tools.add_grocery_item("Onions", quantity="1", category="produce")
    plan_id, _ = _onion_week(2)
    assert _line("Onions")["quantity"] == "3"

    tools.clear_weekly_plan(plan_id)

    assert _line("Onions")["quantity"] == "1"

