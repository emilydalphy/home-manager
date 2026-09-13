"""
A hand-added item whose unit doesn't match the recipe's grew a longer line
every week (Loop Board bug, found 2026-09-13 while fuzzing the
grocery-line-to-zero work).

A standing want — "Eggs · 1", added by hand and kept until bought — sat
in one unit family, and the week's recipe wanted the same thing in
another ("2 cups"). add_grocery_item could not add the two up, so it
concatenated: "1 + 2 cups". Nothing could ever take the plan's share back
off that line, because the reversal reads a line as one number in one
unit and "1 + 2 cups" is not one; so when the week's meals left, the
"+ 2 cups" stayed, and the next week added another. Three weeks in:
"1 + 4 cups + 4 cups + 4 cups".

The rule now (grocery._merge_target): an amount joins the first line it
adds up with cleanly; failing that, it may concatenate onto a line of its
own kind — a person's add onto a person's line, a plan's onto a plan's —
and never across that line. The person's want stays exactly as typed, and
the plan's amount goes on a plan-owned line that recomputes from its
ledger, leaves with its week, and is set aside as a leftover like any
other plan line. Two lines, honest, instead of one nobody can read.
"""
from __future__ import annotations

import datetime
import random

import pytest

from app import tools
from app.db import get_conn
from tests.conftest import _TABLES


def _monday(offset_weeks: int = 0) -> datetime.date:
    today = datetime.date.today()
    monday = today - datetime.timedelta(days=today.weekday())
    return monday + datetime.timedelta(days=7 * offset_weeks)


def _lines(item: str, status: str = "needed") -> list[dict]:
    """Every line for `item` in `status`, in list order: (quantity, plan id)."""
    conn = get_conn()
    rows = conn.execute(
        "SELECT quantity, source_weekly_plan_id FROM grocery_items "
        "WHERE household_id = ? AND item = ? AND status = ? ORDER BY id",
        (tools.household_id(), item, status),
    ).fetchall()
    conn.close()
    return [{"quantity": r["quantity"] or "", "plan": r["source_weekly_plan_id"]} for r in rows]


def _week(offset_weeks: int, recipe: str, nights: int = 2, slot: str = "dinner") -> int:
    monday = _monday(offset_weeks)
    plan_id = tools.create_weekly_plan(monday.isoformat())["weekly_plan_id"]
    for i in range(nights):
        tools.plan_meal(
            (monday + datetime.timedelta(days=i)).isoformat(), recipe, slot=slot, weekly_plan_id=plan_id,
        )
    tools.approve_weekly_plan(plan_id, "Emily")
    return plan_id


@pytest.fixture
def eggs_by_the_cup():
    """A household of three; a recipe for three wanting two cups of eggs a
    night — so a two-night week puts "4 cups" on the list."""
    for name in ("Emily", "Vineeth", "Rae"):
        tools.add_member(name)
    tools.add_recipe(
        "Egg Bake", ingredients=[{"item": "Eggs", "qty": "2 cups", "category": "dairy"}],
        default_servings=3,
    )


# ---------- the bug, deterministically ----------

def test_three_weeks_in_a_row_leave_the_hand_added_line_exactly_as_typed(eggs_by_the_cup):
    """Red on main: "1 + 4 cups + 4 cups + 4 cups" after three approvals,
    with or without the weeks' meals leaving in between."""
    tools.add_grocery_item("Eggs", quantity="1", category="dairy")
    for offset in range(3):
        plan_id = _week(offset, "Egg Bake")
        assert _lines("Eggs")[0] == {"quantity": "1", "plan": None}, f"week {offset}: the person's line, untouched"
        assert [l["quantity"] for l in _lines("Eggs")[1:]] == ["4 cups"], f"week {offset}: the plan's amount on its own line"
        assert _lines("Eggs")[1]["plan"] == plan_id
        tools.clear_weekly_plan(plan_id)
        assert _lines("Eggs") == [{"quantity": "1", "plan": None}], f"week {offset}: the week's line left with the week"


def test_the_plans_line_is_a_plan_line_and_a_swap_recomputes_it(eggs_by_the_cup):
    tools.add_recipe(
        "Carrot Soup", ingredients=[{"item": "Carrot", "qty": "3", "category": "produce"}],
        default_servings=3,
    )
    tools.add_grocery_item("Eggs", quantity="1", category="dairy")
    plan_id = _week(0, "Egg Bake")
    tools.swap_meal_in_plan(plan_id, _monday().isoformat(), "Carrot Soup", slot="dinner")
    assert _lines("Eggs") == [{"quantity": "1", "plan": None}, {"quantity": "2 cups", "plan": plan_id}]


def test_a_second_recipe_in_the_plans_unit_joins_the_plans_line_not_the_persons(eggs_by_the_cup):
    tools.add_recipe(
        "Omelette", ingredients=[{"item": "Eggs", "qty": "1 cup", "category": "dairy"}],
        default_servings=3,
    )
    tools.add_grocery_item("Eggs", quantity="1", category="dairy")
    monday = _monday()
    plan_id = tools.create_weekly_plan(monday.isoformat())["weekly_plan_id"]
    tools.plan_meal(monday.isoformat(), "Egg Bake", slot="dinner", weekly_plan_id=plan_id)
    tools.plan_meal((monday + datetime.timedelta(days=1)).isoformat(), "Omelette", slot="dinner", weekly_plan_id=plan_id)
    tools.approve_weekly_plan(plan_id, "Emily")
    assert _lines("Eggs") == [{"quantity": "1", "plan": None}, {"quantity": "3 cups", "plan": plan_id}]


def test_a_persons_add_in_another_unit_stays_off_the_plans_line(eggs_by_the_cup):
    """The other direction: the plan's line is there first. The person's
    "1" is a standing want on its own line, and the plan's line is still
    the plan's — clear_stale takes it, and never the person's."""
    plan_id = _week(0, "Egg Bake")
    tools.add_grocery_item("Eggs", quantity="1", category="dairy")
    assert _lines("Eggs") == [{"quantity": "4 cups", "plan": plan_id}, {"quantity": "1", "plan": None}]
    tools.clear_weekly_plan(plan_id)
    assert _lines("Eggs") == [{"quantity": "1", "plan": None}]


def test_a_persons_second_add_joins_the_persons_line(eggs_by_the_cup):
    plan_id = _week(0, "Egg Bake")
    tools.add_grocery_item("Eggs", quantity="1", category="dairy")
    tools.add_grocery_item("eggs", quantity="2", category="dairy")
    assert _lines("Eggs") == [{"quantity": "4 cups", "plan": plan_id}, {"quantity": "3", "plan": None}]


# ---------- what did not change ----------

def test_a_standing_want_in_the_plans_own_unit_still_merges_as_before(eggs_by_the_cup):
    """The 2026-09-13 restate path is untouched: same family, one line,
    the plan's share added on and taken back off exactly."""
    tools.add_grocery_item("Eggs", quantity="1 cup", category="dairy")
    plan_id = _week(0, "Egg Bake")
    assert _lines("Eggs") == [{"quantity": "5 cups", "plan": None}]
    tools.clear_weekly_plan(plan_id)
    assert _lines("Eggs") == [{"quantity": "1 cup", "plan": None}]


def test_two_recipes_that_disagree_on_a_unit_still_share_one_plan_line(eggs_by_the_cup):
    """A plan's own disagreement is still reported on one line — that line
    is fully described by its ledger and recomputes correctly whatever it
    reads — so approval-time lines are byte-identical to before."""
    tools.add_recipe(
        "Egg Salad", ingredients=[{"item": "Eggs", "qty": "3", "category": "dairy"}],
        default_servings=3,
    )
    monday = _monday()
    plan_id = tools.create_weekly_plan(monday.isoformat())["weekly_plan_id"]
    tools.plan_meal(monday.isoformat(), "Egg Bake", slot="dinner", weekly_plan_id=plan_id)
    tools.plan_meal((monday + datetime.timedelta(days=1)).isoformat(), "Egg Salad", slot="dinner", weekly_plan_id=plan_id)
    tools.approve_weekly_plan(plan_id, "Emily")
    assert _lines("Eggs") == [{"quantity": "2 cups + 3", "plan": plan_id}]
    tools.swap_meal_in_plan(plan_id, monday.isoformat(), "Egg Salad", slot="dinner")
    assert _lines("Eggs") == [{"quantity": "6", "plan": plan_id}]
    tools.clear_weekly_plan(plan_id)
    assert _lines("Eggs") == []


def test_a_persons_two_unrelated_amounts_still_share_the_persons_line():
    """A person's own "a handful" and "2 cups" are both theirs; there is
    no ledger to keep straight, so the honest one-line report stands."""
    tools.add_grocery_item("Parsley", quantity="a handful", category="produce")
    tools.add_grocery_item("Parsley", quantity="2 cups", category="produce")
    assert _lines("Parsley") == [{"quantity": "a handful + 2 cups", "plan": None}]


def test_a_person_answering_a_pending_spice_still_ticks_it_and_next_weeks_reminder_starts_fresh():
    """The one line a person joins whatever it reads is a spice waiting
    unticked in the section: their add is the answer to it (spices.py,
    2026-09-13), so "1 jar" lands on "1 tbsp" and ticks it. The week
    after, the plan's cumin can't add up with that line, and starts its
    own pending reminder rather than a third clause on the person's."""
    tools.add_recipe(
        "Chili", ingredients=[{"item": "Ground cumin", "qty": "1 tbsp", "category": "pantry"}],
        default_servings=3,
    )
    _week(0, "Chili", nights=1)
    assert _lines("Ground cumin", "spice") and not _lines("Ground cumin")
    tools.add_grocery_item("ground cumin", "1 jar", category="pantry")
    assert _lines("Ground cumin") == [{"quantity": "1 tbsp + 1 jar", "plan": None}], "ticked, the person's now"
    assert not _lines("Ground cumin", "spice")
    week_b = _week(1, "Chili", nights=1)
    assert _lines("Ground cumin") == [{"quantity": "1 tbsp + 1 jar", "plan": None}], "no third clause"
    assert _lines("Ground cumin", "spice") == [{"quantity": "1 tbsp", "plan": week_b}]


def test_consolidating_the_list_leaves_the_two_lines_apart(eggs_by_the_cup):
    plan_id = _week(0, "Egg Bake")
    tools.add_grocery_item("Eggs", quantity="1", category="dairy")
    tools.add_grocery_item("Eggs", quantity="1", category="dairy")
    result = tools.consolidate_grocery_list()
    assert result["lines_merged_away"] == 0
    assert _lines("Eggs") == [{"quantity": "4 cups", "plan": plan_id}, {"quantity": "2", "plan": None}]


# ---------- last week's leftovers (the CARRY step) ----------

def test_the_plans_line_is_set_aside_as_a_leftover_and_keep_joins_this_weeks_plan_line(eggs_by_the_cup):
    tools.add_grocery_item("Eggs", quantity="1", category="dairy")
    week_a = _week(0, "Egg Bake")
    week_b = _week(1, "Egg Bake")
    assert _lines("Eggs", "carried") == [{"quantity": "4 cups", "plan": week_a}]
    carried = next(c for c in tools.list_carried_over_items() if c["item"] == "Eggs")
    assert carried["this_week_quantity"] == "4 cups", "the plan's line, not the person's"
    result = tools.keep_carried_over_item(carried["item_id"])
    assert result["merged_into"] is not None
    assert _lines("Eggs") == [{"quantity": "1", "plan": None}, {"quantity": "8 cups", "plan": week_b}]
    tools.undo_carried_over_decision(carried["item_id"])
    assert _lines("Eggs") == [{"quantity": "1", "plan": None}, {"quantity": "4 cups", "plan": week_b}]
    assert _lines("Eggs", "carried") == [{"quantity": "4 cups", "plan": week_a}]


def test_keep_with_only_the_persons_line_this_week_restores_the_leftover_on_its_own(eggs_by_the_cup):
    """Keep must not glue "4 cups" back onto "1" — the carried line comes
    back as a standing want of its own, and undo puts it back to waiting."""
    tools.add_recipe("Toast", ingredients=[{"item": "Bread", "qty": "1 loaf", "category": "pantry"}])
    tools.add_grocery_item("Eggs", quantity="1", category="dairy")
    week_a = _week(0, "Egg Bake")
    _week(1, "Toast")
    carried = next(c for c in tools.list_carried_over_items() if c["item"] == "Eggs")
    assert carried["this_week_quantity"] is None, "nothing this week the old amount can add up with"
    result = tools.keep_carried_over_item(carried["item_id"])
    assert result["merged_into"] is None
    assert _lines("Eggs") == [{"quantity": "1", "plan": None}, {"quantity": "4 cups", "plan": None}]
    tools.undo_carried_over_decision(carried["item_id"])
    assert _lines("Eggs") == [{"quantity": "1", "plan": None}]
    assert _lines("Eggs", "carried") == [{"quantity": "4 cups", "plan": week_a}]


# ---------- randomised ----------

_FAMILIES = {
    "count": [""],
    "volume": ["cups", "tbsp", "tsp"],
    "weight": ["lb", "oz"],
    "metric": ["g", "kg"],
    "metric volume": ["ml", "l"],
    "package": ["bag", "bottle", "jar", "can"],
}


def _amount(rng: random.Random, unit: str) -> str:
    if unit in ("", "bag", "bottle", "jar", "can"):
        return str(rng.choice([1, 2, 3, 4]))
    if unit in ("cups", "lb", "kg", "l"):
        return str(rng.choice([0.5, 1, 1.5, 2, 3, 4]))
    return str(rng.choice([1, 2, 4, 6, 8, 12]))


def _wipe():
    conn = get_conn()
    conn.execute("PRAGMA foreign_keys = OFF")
    for table in _TABLES:
        try:
            conn.execute(f"DELETE FROM {table}")
        except Exception:
            pass
    conn.commit()
    conn.close()


def test_randomised_weeks_never_grow_a_standing_want_in_another_unit_family():
    """Forty randomised approve-and-clear runs — a standing want in one
    family, a recipe in another, one to four nights, any household size —
    each three weeks long. On main every one of these grew (200/200 in
    the 200-run version of this loop, in the scratch fuzz that found it)."""
    rng = random.Random(20260913)
    grew = []
    for run in range(40):
        _wipe()
        for i in range(rng.randint(1, 4)):
            tools.add_member(f"M{i}")
        fam_a, fam_b = rng.sample(list(_FAMILIES), 2)
        unit_a, unit_b = rng.choice(_FAMILIES[fam_a]), rng.choice(_FAMILIES[fam_b])
        own = f"{_amount(rng, unit_a)} {unit_a}".strip()
        wanted = f"{_amount(rng, unit_b)} {unit_b}".strip()
        tools.add_recipe(
            "Dish", ingredients=[{"item": "Thing", "qty": wanted, "category": "pantry"}],
            default_servings=rng.choice([2, 3, 4, 6, 8]),
        )
        tools.add_grocery_item("Thing", quantity=own, category="pantry")
        typed = _lines("Thing")[0]["quantity"]
        nights = rng.randint(1, 4)
        for offset in range(3):
            plan_id = _week(offset, "Dish", nights=nights)
            with_plan = _lines("Thing")
            tools.clear_weekly_plan(plan_id)
            after = _lines("Thing")
            if (
                with_plan[0]["quantity"] != typed or len(with_plan) != 2
                or after != [{"quantity": typed, "plan": None}]
            ):
                grew.append((own, wanted, nights, with_plan, after))
                break
    assert not grew, grew
