"""
A dinner written as leftovers by name — "Leftover chili" planned from the
chat, no chain, no recipe — is a reheat on Cook, Today and the evening
nudge, the way the Plan tab already reads it (weekly_plan.build_slot).

Before the fix get_cooker_view left it an ordinary cook: Cook offered
"Start cooking", Today's moves listed it as `cook:<id>`, and the evening
nudge texted "Tonight: Leftover chili. Tap to start." at the start of the
dinner window. The shared source is cooker._apply_leftover_chains.
"""
from __future__ import annotations

import datetime

import pytest

from app import tools
from app.tools import digest

pytestmark = pytest.mark.today("2026-10-14 12:00")
TODAY = datetime.date(2026, 10, 14)


def _tonight(meal: str):
    tools.add_member("Emily")
    tools.add_member("Jamie")
    monday = TODAY - datetime.timedelta(days=TODAY.weekday())
    plan_id = tools.create_weekly_plan(monday.isoformat())["weekly_plan_id"]
    entry = tools.plan_meal(TODAY.isoformat(), meal, slot="dinner", weekly_plan_id=plan_id)["entry_id"]
    tools.approve_weekly_plan(plan_id, approved_by="Emily", confirm_hard_conflicts=True)
    card = next(m for m in tools.get_cooker_view(plan_id)["meals"] if m["entry_id"] == entry)
    moves = {m["id"]: m for m in tools.today_moves(TODAY.isoformat())["moves"]}
    return entry, card, moves


def test_leftover_chili_by_name_is_a_reheat_everywhere(client):
    entry, card, moves = _tonight("Leftover chili")

    assert card["is_leftovers"] is True
    assert card["leftovers_headline"] == "Leftover chili"
    assert f"reheat:{entry}" in moves and f"cook:{entry}" not in moves
    assert moves[f"reheat:{entry}"]["meta"] == "leftovers · reheat", "no freezer, no source night to name"
    nudge = digest.build_evening_nudge(datetime.datetime(2026, 10, 14, 17, 30), link=False)
    assert nudge is None, "a reheat is a line, never a 'Tap to start'"


def test_a_saved_recipe_with_leftover_in_its_name_is_still_a_cook(client):
    tools.add_recipe(
        "Leftover Turkey Soup",
        ingredients=[{"item": "Turkey", "qty": "1 lb", "category": "meat/seafood"}],
        instructions=["Simmer the turkey for 30 minutes."],
        default_servings=2,
    )
    entry, card, moves = _tonight("Leftover Turkey Soup")

    assert not card.get("is_leftovers")
    assert f"cook:{entry}" in moves
