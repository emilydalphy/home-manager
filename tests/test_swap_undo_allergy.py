"""
Undo on a swap must not put back a dish somebody can't have now
(overnight/swap-undo-allergy-1028b).

The flow it pins: Tuesday's Peanut Noodles swapped for something else, then
Reid's peanut allergy added, then Undo on the swap. undo_meal_swap puts the
old dish back through swap_meal_in_plan, which runs no allergy check — so
the peanut dish went straight back on the week, onto the shopping list of
an approved one. The "this meal only" tweak's Undo already refuses this
(recipe_change.undo_recipe_change); this is the swap's Undo doing the same,
with the same kind of sentence, and nothing changed.

The model is never called: swaps go through the `picker` seam.
"""
from __future__ import annotations

import datetime
import json

import pytest

from conftest import household_pin, household_today

from app import tools
from app.db import get_conn
from app.tools import swap_in_place as sip

TODAY = household_today()
pytestmark = pytest.mark.today(household_pin(10, on=TODAY))
WEEK_START = TODAY.isoformat()
D1 = (TODAY + datetime.timedelta(days=1)).isoformat()
D2 = (TODAY + datetime.timedelta(days=2)).isoformat()
NOODLES = "Peanut Noodles"
NEW = "Lemon Chicken Traybake"


def _pick(name=NEW):
    return {
        "meal_name": name, "reason": "Lighter, and nothing to thaw.", "is_new_recipe": True,
        "ingredients": [{"item": "Chicken thighs", "qty": "2 lb", "category": "meat/seafood"},
                        {"item": "Lemons", "qty": "3", "category": "produce"}],
        "instructions": ["Roast it."], "food_groups": ["protein", "vegetable", "carb"],
        "main_protein": "chicken", "prep_time_minutes": 10, "cook_time_minutes": 10,
        "default_servings": 2,
    }


@pytest.fixture
def week():
    tools.add_member("Emily")
    tools.add_member("Reid")
    tools.add_recipe(NOODLES, ingredients=[
        {"item": "Rice noodles", "qty": "1 lb", "category": "pantry"},
        {"item": "Peanut butter", "qty": "1 jar", "category": "pantry"},
    ], food_groups=["protein", "carb"], prep_time_minutes=10, cook_time_minutes=15)
    tools.add_recipe("Chili", ingredients=[{"item": "Beef", "qty": "1 lb", "category": "meat/seafood"}],
                     food_groups=["protein"])
    plan_id = tools.create_weekly_plan(WEEK_START)["weekly_plan_id"]
    tools.plan_meal(D1, NOODLES, slot="dinner", weekly_plan_id=plan_id, reasoning="you like it")
    tools.plan_meal(D2, "Chili", slot="dinner", weekly_plan_id=plan_id, reasoning="fits the week")
    return plan_id


def _entry(plan_id, day):
    conn = get_conn()
    row = conn.execute(
        "SELECT mpe.id, COALESCE(r.name, mpe.freeform_meal) AS meal, mpe.derived_from_json "
        "FROM meal_plan_entries mpe LEFT JOIN recipes r ON r.id = mpe.recipe_id "
        "WHERE mpe.weekly_plan_id = ? AND mpe.date = ? AND mpe.slot = 'dinner'",
        (plan_id, day),
    ).fetchall()
    conn.close()
    assert len(row) == 1
    return dict(row[0], derived=json.loads(row[0]["derived_from_json"] or "{}"))


def _grocery():
    rows = tools.list_grocery_list() + tools.list_grocery_list(status="spice")
    return sorted((g["item"], g["quantity"]) for g in rows)


def _swap(plan_id, day=D1):
    out = tools.swap_meal_in_place(plan_id, _entry(plan_id, day)["id"], picker=lambda c: _pick())
    assert out["status"] == "swapped"
    return out


def test_undo_refuses_to_put_back_a_dish_an_allergy_added_since_rules_out(week):
    tools.approve_weekly_plan(week, "Emily")
    out = _swap(week)
    tools.set_member_dietary_restrictions("Reid", ["peanut allergy"])
    before_list = _grocery()
    before = _entry(week, D1)

    back = tools.undo_meal_swap(week, out["entry_id"])

    assert back["status"] == "refused"
    assert "Reid" in back["message"] and "peanut" in back["message"]
    assert back["message"] == "Reid can’t have peanuts, so I can’t put Peanut Noodles back."
    after = _entry(week, D1)
    assert after["meal"] == NEW and after["id"] == before["id"], "the plan did not change"
    assert after["derived"].get("swapped_from", {}).get("meal") == NOODLES, "Undo is still there"
    assert _grocery() == before_list, "the list did not change"
    assert NOODLES not in {m for m, _q in before_list}


def test_an_allergy_the_old_dish_does_not_trip_still_lets_undo_through(week):
    out = _swap(week)
    tools.set_member_dietary_restrictions("Reid", ["shellfish allergy"])
    back = tools.undo_meal_swap(week, out["entry_id"])
    assert back["status"] == "restored"
    assert _entry(week, D1)["meal"] == NOODLES


def test_a_hard_household_fact_refuses_too(week):
    """The swap door's own scope: every allergy and hard fact in the house,
    not only the people at that night's table (_hard_clash)."""
    out = _swap(week)
    tools.add_fact("people", "No peanuts in this house", hard=True)
    back = tools.undo_meal_swap(week, out["entry_id"])
    assert back["status"] == "refused"
    assert back["message"] == "This house can’t have peanuts, so I can’t put Peanut Noodles back."
    assert _entry(week, D1)["meal"] == NEW


def test_the_whole_household_counts_even_if_reid_is_out_that_night(week):
    """Mirrors the swap door, which picks against the whole household's
    hard avoidances regardless of who is home (swap_in_place._hard_clash)."""
    out = _swap(week)
    tools.set_member_dietary_restrictions("Reid", ["peanut allergy"])
    tools.set_member_attendance(D1, "dinner", "Reid", present=False)
    assert tools.undo_meal_swap(week, out["entry_id"])["status"] == "refused"


def test_a_whole_dish_swap_undo_refuses_every_day_together(week):
    tools.plan_meal(D2, NOODLES, slot="lunch", weekly_plan_id=week, reasoning="you like it")
    tools.plan_meal(D1, NOODLES, slot="lunch", weekly_plan_id=week, reasoning="you like it")
    conn = get_conn()
    lunch = conn.execute(
        "SELECT id FROM meal_plan_entries WHERE weekly_plan_id = ? AND date = ? AND slot = 'lunch'",
        (week, D1)).fetchone()["id"]
    conn.close()
    tools.swap_options(week, lunch, asker=lambda c: [_pick()], whole_dish=True)
    out = tools.choose_swap_option(week, lunch, 0, whole_dish=True)
    assert out["status"] == "swapped"
    tools.set_member_dietary_restrictions("Reid", ["peanut allergy"])

    back = tools.undo_meal_swap(week, out["entry_id"])
    assert back["status"] == "refused" and "Reid" in back["message"]
    conn = get_conn()
    lunches = conn.execute(
        "SELECT COALESCE(r.name, mpe.freeform_meal) AS meal FROM meal_plan_entries mpe "
        "LEFT JOIN recipes r ON r.id = mpe.recipe_id WHERE mpe.weekly_plan_id = ? AND mpe.slot = 'lunch'",
        (week,)).fetchall()
    conn.close()
    assert NOODLES not in {r["meal"] for r in lunches}


def test_a_check_that_cannot_run_refuses_rather_than_guessing(week, monkeypatch):
    out = _swap(week)

    def broken(*a, **k):
        raise RuntimeError("matcher down")

    monkeypatch.setattr(sip._coordination, "check_meal_conflicts", broken)
    back = tools.undo_meal_swap(week, out["entry_id"])
    assert back["status"] == "refused"
    assert _entry(week, D1)["meal"] == NEW


def test_the_route_answers_the_sentence_and_changes_nothing(week, signed_in):
    out = _swap(week)
    tools.set_member_dietary_restrictions("Reid", ["peanut allergy"])
    res = signed_in.post(f"/api/week/{WEEK_START}/swap-undo",
                         json={"entry_id": out["entry_id"], "weekly_plan_id": week})
    # Not a 200: runSwapUndo treats every 200 as "it's back" and would
    # toast "Back as it was." over a refusal. A 409 is shown as "nothing
    # changed", which is true, and carries the sentence as `detail`.
    assert res.status_code == 409
    assert "Reid" in res.json()["detail"]
    assert _entry(week, D1)["meal"] == NEW


def test_the_chat_change_cards_undo_refuses_all_of_it_together(week, signed_in):
    """The chat's change card puts its rows back through the swap's Undo
    (proposals.undo_proposal): one row refused is the whole card refused,
    before any row is written — never half a week put back."""
    out = tools.propose_plan_changes(week, [
        {"date": D1, "slot": "dinner", "action": "change", "candidates": [_pick()]},
        {"date": D2, "slot": "dinner", "action": "change", "candidates": [_pick("Fish Tacos")]},
    ])
    pid = out["proposal_id"]
    assert tools.apply_proposal(pid)["status"] == "applied"
    tools.set_member_dietary_restrictions("Reid", ["peanut allergy"])

    res = signed_in.post(f"/api/chat/proposals/{pid}/undo")
    assert res.status_code == 409 and "Reid" in res.json()["detail"]
    assert _entry(week, D1)["meal"] == NEW
    assert _entry(week, D2)["meal"] == "Fish Tacos", "Chili was not put back on its own"
