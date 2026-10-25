"""
The seams between a household's first week and its second (overnight hunt,
2026-10-25: onboard, plan and live week 1 over HTTP on a moving clock, then
plan weeks 2 and 3).

Found on a throwaway database:

  Week 1: Thursday's pasta (cooked double for Saturday) got a night off, so
  the cook moved to Saturday and "the extra goes in the freezer" wrote a
  2-serving portion straight away. Saturday was then answered "We skipped
  it". Week 2's draft:

    Mon  Leftovers from the freezer — Pasta Primavera
    Tue  Leftovers — Monday's Leftovers from the freezer — Pasta Primavera
    Wed  Leftovers — Monday's Leftovers from the freezer — Pasta Primavera

  Three dinners off two frozen servings, nothing cooked and nothing bought;
  and the two servings were never made. Approval then left last week's beef
  (bought for the skipped chili, use-by five days gone) off the list as
  "already have".

Each test says CATCH (red on main) or GUARD.
"""
from __future__ import annotations

import datetime
import json

import pytest

from conftest import approving_on, household_today

from app import agent, tools
from app.db import get_conn
from app.tools import bring_over
from app.tools import freezer_portions as _fp
from app.tools import leftovers as _leftovers
from app.tools import tonight as _tonight
from app.tools import draft_opener


def _monday(offset_weeks: int = 0) -> str:
    today = household_today()
    monday = today - datetime.timedelta(days=today.weekday())
    return (monday + datetime.timedelta(days=7 * offset_weeks)).isoformat()


def _slot(date: str, name: str) -> dict:
    return {
        "date": date, "slot": "dinner", "meal_name": name, "is_new_recipe": True,
        "ingredients": [{"item": f"{name} base", "qty": "1 lb", "category": "pantry"}],
        "instructions": [f"Cook the {name.lower()} over medium heat.", "Serve."],
        "reasoning": f"{name} because", "food_groups": ["protein", "vegetable", "carb"],
        "prep_time_minutes": 10, "cook_time_minutes": 15,
    }


def _entries(plan_id: int) -> list[dict]:
    conn = get_conn()
    rows = conn.execute(
        "SELECT mpe.*, COALESCE(r.name, mpe.freeform_meal) AS meal FROM meal_plan_entries mpe "
        "LEFT JOIN recipes r ON r.id = mpe.recipe_id WHERE mpe.weekly_plan_id = ? "
        "AND mpe.slot = 'dinner' AND mpe.component_category IS NULL ORDER BY date, id",
        (plan_id,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def _freezer() -> list[dict]:
    return [i for i in tools.get_inventory() if i.get("location") == "freezer"]


def _approved(start: str, days: int) -> int:
    plan = tools.create_weekly_plan(start, day_count=days)["weekly_plan_id"]
    conn = get_conn()
    conn.execute("UPDATE weekly_plans SET status = 'approved' WHERE id = ?", (plan,))
    conn.commit()
    conn.close()
    return plan


def _double_cook(plan: int, cook_day: str, fed_day: str, dish: str = "Pasta Primavera") -> int:
    """`dish` cooked on cook_day, double, for fed_day's dinner — the shape the
    count fold writes ("Cook big, eat twice")."""
    if not any(r["name"] == dish for r in tools.list_recipes()):
        tools.add_recipe(dish, ingredients=[{"item": f"{dish} base", "qty": "1 lb", "category": "pantry"}],
                         prep_time_minutes=10, cook_time_minutes=20, default_servings=4)
    cook = tools.plan_meal(cook_day, dish, slot="dinner", weekly_plan_id=plan,
                           derived_from={"make_double_for": [f"{fed_day}:dinner"]})["entry_id"]
    tools.plan_meal(fed_day, dish, slot="dinner", weekly_plan_id=plan,
                    derived_from={"links_to": f"{cook_day}:dinner"})
    assert _leftovers.plan_leftover_chains(plan)["sources"].get(cook), "premise: a real chain"
    return cook


def _night_off_onto_the_fed_night():
    """Two days ago: the double cook. Yesterday: the night it fed. The night
    off on the cook moves it to yesterday with its extra in the freezer."""
    today = household_today()
    d0 = (today - datetime.timedelta(days=2)).isoformat()
    d1 = (today - datetime.timedelta(days=1)).isoformat()
    plan = _approved(d0, 2)
    cook = _double_cook(plan, d0, d1)
    out = _tonight.tonight_night_off(day=d0)
    assert out["kind"] == "cook_on_fed" and out["frozen"], out
    assert [r["item"] for r in _freezer()] == ["Pasta Primavera (cooked)"], "premise: the extra is in"
    return plan, cook, today, d1


# ==========================================================================
# 1. A night off's freezer portion with no cook behind it
# ==========================================================================

def test_skipping_the_cook_a_night_off_moved_leaves_nothing_to_plan():
    """CATCH. "We skipped it" on the night the cook moved to: nothing was
    cooked, so next week has no portion of it to plan. The row itself is
    only ignored, not deleted — see the next test."""
    _plan, cook, today, _d1 = _night_off_onto_the_fed_night()
    tools.answer_yesterday(cook, "skipped", today)
    assert _fp.portions_to_plan() == []


def test_ticking_it_cooked_after_a_skip_brings_the_portion_back():
    """GUARD (review, 2026-10-25). A skip has no Undo; the correction is the
    cooked tick. It must find the portion still there."""
    _plan, cook, today, _d1 = _night_off_onto_the_fed_night()
    tools.answer_yesterday(cook, "skipped", today)
    tools.check_off_meal(cook, "done")
    assert [p["dish"] for p in _fp.portions_to_plan()] == ["Pasta Primavera"]
    assert [r["item"] for r in _freezer()] == ["Pasta Primavera (cooked)"]
    conn = get_conn()
    derived = json.loads(conn.execute("SELECT derived_from_json FROM meal_plan_entries WHERE id = ?",
                                      (cook,)).fetchone()[0] or "{}")
    conn.close()
    assert derived.get(_leftovers.FREEZER_EXTRA_KEY), "the cook still carries its portion"


def test_a_night_off_on_the_night_the_cook_moved_to_takes_its_portion_back_out():
    """CATCH. The same cook called off again on its new night comes off the
    week ('drop'): its portion goes with it."""
    _plan, _cook, _today, d1 = _night_off_onto_the_fed_night()
    out = _tonight.tonight_night_off(day=d1)
    assert out["kind"] == "drop", out
    assert _freezer() == []


def test_cooking_it_keeps_the_portion():
    """GUARD. Cooked on the night it moved to, the portion is real."""
    _plan, cook, today, _d1 = _night_off_onto_the_fed_night()
    tools.answer_yesterday(cook, "had", today)
    assert [r["item"] for r in _freezer()] == ["Pasta Primavera (cooked)"]


def test_a_portion_a_later_week_already_stands_on_is_left_alone():
    """GUARD on the edge the release does not take: once a live plan has put
    the portion on a night, taking the row away would leave that night with
    nothing, which is a re-plan — not this."""
    _plan, cook, today, _d1 = _night_off_onto_the_fed_night()
    [row] = _freezer()
    later = _approved((today + datetime.timedelta(days=2)).isoformat(), 1)
    _fp.apply_to_plan(later, [{"inventory_item_id": row["id"], "dish": "Pasta Primavera",
                               "quantity": row["quantity"], "on": (today + datetime.timedelta(days=2)).isoformat()}])
    tools.answer_yesterday(cook, "skipped", today)
    assert [r["id"] for r in _freezer()] == [row["id"]]


# ==========================================================================
# 2. A freezer night is not a cook
# ==========================================================================

@pytest.fixture
def stub_model(monkeypatch):
    def _stub(days):
        monkeypatch.setattr(agent, "generate_weekly_plan_llm", lambda ctx: days)
    return _stub


def _portion(dish: str) -> int:
    """A cooked dish frozen on a night off last week (the freeze_cooked
    shape — a real portion)."""
    week = _monday(-1)
    dates = tools._week_dates(week)
    plan = _approved(week, 7)
    tools.add_recipe(dish, ingredients=[{"item": f"{dish} base", "qty": "1 lb", "category": "pantry"}],
                     prep_time_minutes=10, cook_time_minutes=20, default_servings=3)
    entry = tools.plan_meal(dates[1], dish, slot="dinner", weekly_plan_id=plan)["entry_id"]
    conn = get_conn()
    conn.execute("UPDATE meal_plan_entries SET cooked_status = 'done' WHERE id = ?", (entry,))
    conn.commit()
    conn.close()
    assert _tonight.tonight_night_off(day=dates[1])["kind"] == "freeze_cooked"
    return _freezer()[-1]["id"]


def test_the_frozen_dish_later_in_the_week_is_cooked_not_fed_from_the_freezer(stub_model):
    """CATCH. Four different dinners a week, the frozen dish also drafted
    two nights after the freezer night: on main the count fold read the
    freezer night as that dish's COOK and linked the later night to it —
    "Leftovers — Monday's Leftovers from the freezer — Kofte", a dinner
    eating a portion already eaten, with nothing bought for it."""
    tools.add_member("Emily")
    tools.add_member("Vineeth")
    tools.set_household_meal_preferences(dinners_per_week=4, breakfasts_per_week=0, lunches_per_week=0,
                                         snacks_per_week=0, mark_complete=False)
    _portion("Kofte")
    week = _monday(1)
    dates = tools._week_dates(week)
    tools.save_week_intake(week, night_tags={dates[0]: ["rush"]})
    stub_model([_slot(d, n) for d, n in zip(
        dates, ["Bean chili", "Miso cod", "Kofte", "Bibimbap", "Ratatouille", "Laksa", "Gumbo"])])
    plan = agent.generate_weekly_plan(week)["weekly_plan_id"]
    rows = _entries(plan)
    freezer_nights = [r for r in rows if _leftovers.frozen_portion_on(r["derived_from_json"])]
    assert [r["meal"] for r in freezer_nights] == ["Leftovers from the freezer — Kofte"], rows
    chains = _leftovers.plan_leftover_chains(plan)
    assert freezer_nights[0]["id"] not in chains["sources"], "a freezer night feeds nobody"
    for r in rows:
        assert "from the freezer" not in (r["meal"] or "") or r["id"] == freezer_nights[0]["id"], r
        link = json.loads(r["derived_from_json"] or "{}").get("links_to") or ""
        assert link not in (f"entry_id:{freezer_nights[0]['id']}", f"{freezer_nights[0]['date']}:dinner"), r


def test_a_link_onto_a_freezer_night_is_repaired():
    """CATCH (the second door). A links_to naming a night that eats from the
    freezer — the model's own "same dish twice" pairing landing on the night
    the portion took — is not a chain: the portion is that night's alone."""
    _portion("Kofte")
    week = _monday(1)
    dates = tools._week_dates(week)
    plan = _approved(week, 7)
    [portion] = _fp.portions_to_plan(dates)
    _fp.apply_to_plan(plan, [dict(portion, on=dates[0])])
    tools.plan_meal(dates[2], "Kofte", slot="dinner", weekly_plan_id=plan,
                    derived_from={"links_to": f"{dates[0]}:dinner"})
    tools.repair_leftover_chains(plan)
    later = next(r for r in _entries(plan) if r["date"] == dates[2])
    assert (json.loads(later["derived_from_json"] or "{}").get("links_to") or "") != f"{dates[0]}:dinner"


# ==========================================================================
# 3. Last week's food past its use-by is not this week's
# ==========================================================================

def _week_needing(item: str, qty: str = "1 lb", category: str = "meat/seafood") -> int:
    week = _monday(1)
    plan = _approved(week, 7)
    tools.add_recipe("Beef chili", ingredients=[{"item": item, "qty": qty, "category": category}],
                     prep_time_minutes=10, cook_time_minutes=30, default_servings=2)
    tools.plan_meal(tools._week_dates(week)[1], "Beef chili", slot="dinner", weekly_plan_id=plan)
    conn = get_conn()
    conn.execute("UPDATE weekly_plans SET status = 'draft' WHERE id = ?", (plan,))
    conn.commit()
    conn.close()
    return plan


def _needed(item: str) -> list[dict]:
    conn = get_conn()
    rows = conn.execute("SELECT * FROM grocery_items WHERE item = ? AND status = 'needed'", (item,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def _stock(item: str, use_by: str) -> None:
    tools.update_inventory(item, "add", quantity="1 lb", category="meat/seafood", location="fridge",
                           expiration_date=use_by)


def test_meat_past_its_use_by_is_bought_again():
    """CATCH. A pound of ground beef in the fridge, past the use-by the
    kitchen holds for it, does not cover next week's pound."""
    _stock("Ground beef", (household_today() - datetime.timedelta(days=5)).isoformat())
    plan = _week_needing("Ground beef")
    tools.approve_weekly_plan(plan)
    assert [r["quantity"] for r in _needed("Ground beef")] == ["1 lb"]


def test_meat_still_good_still_covers_it():
    """GUARD. The same pound, still in date, is still left off the list."""
    _stock("Ground beef", (household_today() + datetime.timedelta(days=10)).isoformat())
    plan = _week_needing("Ground beef")
    tools.approve_weekly_plan(plan)
    assert _needed("Ground beef") == []


def test_a_brought_over_dish_buys_again_what_has_gone_past_its_use_by():
    """CATCH. Bring over from last week: what last week's list bought for
    the dish is not bought again — unless the kitchen's own row for it is
    past its use-by. Still in date, or with no row at all, it stays bought."""
    week = _monday(-1)
    plan = tools.create_weekly_plan(week)["weekly_plan_id"]
    tools.add_recipe("Beef chili", ingredients=[
        {"item": "Ground beef", "qty": "1 lb", "category": "meat/seafood"},
        {"item": "Kidney beans", "qty": "1 can", "category": "pantry"},
    ], prep_time_minutes=10, cook_time_minutes=30, default_servings=2)
    entry = tools.plan_meal(tools._week_dates(week)[0], "Beef chili", slot="dinner", weekly_plan_id=plan)["entry_id"]
    # Approved back when last week was ahead, so its shop was bought for.
    with approving_on(datetime.date.fromisoformat(week)):
        tools.approve_weekly_plan(plan)
    conn = get_conn()
    lines = conn.execute("SELECT DISTINCT grocery_item_id FROM meal_plan_grocery_links WHERE meal_plan_entry_id = ?",
                         (entry,)).fetchall()
    conn.close()
    assert lines, "premise: the chili put its shopping on the list"
    for line in lines:
        tools.mark_grocery_item(line[0], "purchased")
    assert bring_over.bought_items([entry]) == {"ground beef", "kidney beans"}
    conn = get_conn()
    conn.execute("UPDATE inventory_items SET expiration_date = ? WHERE item = 'Ground beef'",
                 ((household_today() - datetime.timedelta(days=1)).isoformat(),))
    conn.execute("DELETE FROM inventory_items WHERE item = 'Kidney beans'")
    conn.commit()
    conn.close()
    assert bring_over.bought_items([entry]) == {"kidney beans"}
    # Put in the freezer, the same past date is no reason to buy it again.
    conn = get_conn()
    conn.execute("UPDATE inventory_items SET location = 'freezer' WHERE item = 'Ground beef'")
    conn.commit()
    conn.close()
    assert bring_over.bought_items([entry]) == {"ground beef", "kidney beans"}


# ==========================================================================
# 4. The words on the second week's draft
# ==========================================================================

@pytest.mark.parametrize("new, start", [(1, "One new dish;"), (0, "No new dishes;"), (2, "Two new dishes;")])
def test_the_opener_counts_new_dishes_in_english(monkeypatch, new, start):
    """CATCH for 1 and 0 ("One new dishes", "Zero new dishes"); GUARD for 2."""
    names = [f"New {i}" for i in range(new)] + ["Beef Chili"]
    monkeypatch.setattr(draft_opener, "_dish_names", lambda entries: names)
    entries = [{"slot": "dinner", "meal": n, "derived_from_json": "{}", "slot_state": "planned"} for n in names]
    line = draft_opener._line_two(entries, None, recent={"beef chili"})
    assert line.startswith(start), line


def test_fresh_food_bought_onto_an_expired_row_replaces_it():
    """CATCH. This week's pound, bought onto last week's two pounds already
    past their use-by: the row is the new pound with the new date — not
    three pounds "in date" (a week needing three would buy nothing), and
    not the old date (expired the day it was bought)."""
    tools.update_inventory("Ground beef", "add", quantity="2 lb", category="meat/seafood", location="fridge",
                           expiration_date=(household_today() - datetime.timedelta(days=5)).isoformat())
    tools.update_inventory("Ground beef", "add", quantity="1 lb", category="meat/seafood", location="fridge")
    [row] = [r for r in tools.get_inventory() if r["item"] == "Ground beef"]
    assert row["expiration_date"] >= household_today().isoformat(), row
    assert row["quantity"] == "1 lb", row
    plan = _week_needing("Ground beef", qty="2 lb")
    tools.approve_weekly_plan(plan)
    assert [r["quantity"] for r in _needed("Ground beef")] == ["2 lbs"]


def test_meat_in_the_freezer_is_stock_whatever_its_date():
    """GUARD (review, 2026-10-25). A freezer row's date is the category's
    fridge estimate, not how long frozen meat keeps: it still covers the
    week, and bought-last-week still holds for it."""
    tools.update_inventory("Ground beef", "add", quantity="1 lb", category="meat/seafood", location="freezer",
                           expiration_date=(household_today() - datetime.timedelta(days=7)).isoformat())
    plan = _week_needing("Ground beef")
    tools.approve_weekly_plan(plan)
    assert _needed("Ground beef") == []
