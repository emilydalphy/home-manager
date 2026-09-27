"""
Changing an ingredient keeps the batch (Emily, 2026-09-27, on the live
draft): Korean-Style Gochujang Beef Bowls cooked Thursday dinner "for 6",
eaten again at Friday lunch and Friday dinner. She changed the veg to
spinach — and Thursday became "…Bowls with spinach", cooking for 2 with
1 lb of flank steak, while Friday's lunch and dinner stayed the old dish.

Three things went wrong in one write, and each is pinned here:
  * the change reached ONE entry (plate_parts.change_part -> apply_pick),
    never the meals the cook feeds;
  * the rewrite was asked for, and saved at, one table (_table_for) — not
    the batch the Cook card reads (leftovers.batch_for_entry);
  * the swap re-inserted the cook under a new id, and the leftover nights
    link by `entry_id:` — the chain broke, so "Cooking for" fell to 2.

The model is never called: `asker` hands back the variant.
"""
from __future__ import annotations

import datetime
import json

import pytest

from conftest import household_today

from app import tools
from app.db import get_conn
from app.tools import leftovers
from app.tools import plate_parts as pp

TODAY = household_today()
START = TODAY.isoformat()
D1 = (TODAY + datetime.timedelta(days=1)).isoformat()
D2 = (TODAY + datetime.timedelta(days=2)).isoformat()
BOWLS = "Korean-Style Gochujang Beef Bowls"


def _variant(context_seen: list):
    def asker(context):
        context_seen.append(context)
        return {
            "meal_name": f"{BOWLS} with Spinach", "reason": "Spinach instead of broccoli — same time.",
            "ingredients": [
                {"item": "Flank steak", "qty": "3 lbs", "category": "meat/seafood"},
                {"item": "Gochujang", "qty": "6 tbsp", "category": "pantry"},
                {"item": "Spinach", "qty": "3 bags", "category": "produce"},
                {"item": "Jasmine rice", "qty": "3 cups", "category": "pantry"},
            ],
            "instructions": ["Sear the steak.", "Wilt the spinach.", "Build the bowls."],
            "food_groups": ["protein", "vegetable", "carb"], "main_protein": "beef",
            "prep_time_minutes": 10, "cook_time_minutes": 10,
            # What the model said — the change saves the batch, not this.
            "default_servings": 2,
        }
    return asker


@pytest.fixture
def batch():
    """Thursday's cook feeding Friday lunch and Friday dinner — linked by
    entry id, the shape weekday_lunches and meal_variety write."""
    tools.add_member("Emily")
    tools.add_member("Vineeth")
    tools.add_recipe(
        BOWLS,
        ingredients=[{"item": "Flank steak", "qty": "1 lb", "category": "meat/seafood"},
                     {"item": "Gochujang", "qty": "2 tbsp", "category": "pantry"},
                     {"item": "Broccoli", "qty": "1 head", "category": "produce"},
                     {"item": "Jasmine rice", "qty": "1 cup", "category": "pantry"}],
        food_groups=["protein", "vegetable", "carb"], main_protein="beef",
        instructions=["Sear the steak.", "Steam the broccoli.", "Build the bowls."],
        default_servings=2, prep_time_minutes=10, cook_time_minutes=10,
    )
    plan_id = tools.create_weekly_plan(START)["weekly_plan_id"]
    cook = tools.plan_meal(D1, BOWLS, slot="dinner", weekly_plan_id=plan_id)["entry_id"]
    for slot in ("lunch", "dinner"):
        tools.plan_meal(D2, BOWLS, slot=slot, weekly_plan_id=plan_id,
                        derived_from={"links_to": f"entry_id:{cook}"})
    conn = get_conn()
    conn.execute("UPDATE meal_plan_entries SET derived_from_json = ? WHERE id = ?",
                 (json.dumps({"make_double_for": [f"{D2}:dinner", f"{D2}:lunch"]}), cook))
    conn.commit()
    conn.close()
    pp._OPTIONS_CACHE.clear()
    chains = leftovers.plan_leftover_chains(plan_id)
    assert leftovers.batch_for_entry(cook, chains)["servings"] == 6, "the seed is a batch of 6"
    return plan_id, cook


def _meals(plan_id):
    conn = get_conn()
    rows = conn.execute(
        "SELECT mpe.date, mpe.slot, COALESCE(r.name, mpe.freeform_meal) AS meal "
        "FROM meal_plan_entries mpe LEFT JOIN recipes r ON r.id = mpe.recipe_id "
        "WHERE mpe.weekly_plan_id = ? ORDER BY mpe.date, mpe.slot", (plan_id,),
    ).fetchall()
    conn.close()
    return {(r["date"], r["slot"]): r["meal"] for r in rows}


def _cook_batch(plan_id):
    chains = leftovers.plan_leftover_chains(plan_id)
    assert len(chains["sources"]) == 1, "one cook, still feeding its leftover meals"
    cook_id, source = next(iter(chains["sources"].items()))
    return cook_id, source, leftovers.batch_for_entry(cook_id, chains)


def test_changing_the_veg_changes_every_meal_the_batch_feeds(batch):
    plan_id, cook = batch
    seen: list = []
    out = pp.change_part(plan_id, cook, "vegetable", "Spinach", asker=_variant(seen))
    assert out["status"] == "changed"
    new = out["meal"]
    assert new != BOWLS
    assert _meals(plan_id) == {(D1, "dinner"): new, (D2, "dinner"): new, (D2, "lunch"): new}, \
        "Friday's lunch and dinner are the changed dish too"
    assert sorted(d["date"] for d in out["days"]) == [D1, D2]
    assert len(seen) == 1, "rewritten once, not once a day"


def test_the_change_is_written_for_the_batch_and_cooking_for_stays_six(batch):
    plan_id, cook = batch
    seen: list = []
    pp.change_part(plan_id, cook, "vegetable", "Spinach", asker=_variant(seen))
    assert seen[0]["serves"] == 6, "the model is asked for the whole batch, not one table"
    cook_id, source, cook_batch = _cook_batch(plan_id)
    assert [t["date"] + ":" + t["slot"] for t in source["targets"]] == [f"{D2}:dinner", f"{D2}:lunch"], \
        "the leftover links survive the change"
    assert cook_batch["servings"] == 6, "Cooking for stays 6"
    saved = tools.get_recipe(source["meal"])
    assert saved["default_servings"] == 6, "saved at the size its amounts were written for"
    menu = next(d for d in tools.get_week_menu(plan_id)["days"] if d["date"] == D2)
    assert (menu["lunch"].get("leftover_from") or {}).get("meal") == source["meal"], \
        "Friday lunch still reads as Thursday's leftovers"


def test_undo_puts_the_whole_batch_back(batch):
    plan_id, cook = batch
    out = pp.change_part(plan_id, cook, "vegetable", "Spinach", asker=_variant([]))
    back = tools.undo_meal_swap(plan_id, out["entry_id"])
    assert back["status"] == "restored"
    assert set(_meals(plan_id).values()) == {BOWLS}, "every day back, not only Thursday"
    _cook_id, source, cook_batch = _cook_batch(plan_id)
    assert source["meal"] == BOWLS and cook_batch["servings"] == 6


def test_the_change_keeps_each_days_sides_and_undo_carries_them_back(batch):
    plan_id, cook = batch
    tools.add_component(cook, key="roasted-potatoes")
    out = pp.change_part(plan_id, cook, "vegetable", "Spinach", asker=_variant([]))
    day = next(d for d in out["days"] if d["date"] == D1)
    assert [s["name"] for s in day["dinner"]["sides"]] == ["Roasted potatoes"]
    tools.undo_meal_swap(plan_id, out["entry_id"])
    back = next(d for d in tools.get_week_menu(plan_id)["days"] if d["date"] == D1)
    assert [s["name"] for s in back["dinner"]["sides"]] == ["Roasted potatoes"]


def test_a_single_day_swap_does_not_carry_the_old_rows_chain_forward(batch):
    """apply_pick used to copy the outgoing row's derived_from whole — a
    make_double_for carried forward re-formed a chain the swap had just
    unlinked. The cook swapped on its own (its leftover meals already
    cooked) is an ordinary cook afterwards."""
    plan_id, cook = batch
    conn = get_conn()
    conn.execute("UPDATE meal_plan_entries SET cooked_status = 'done' WHERE weekly_plan_id = ? AND date = ?",
                 (plan_id, D2))
    conn.commit()
    conn.close()
    out = pp.change_part(plan_id, cook, "vegetable", "Spinach", asker=_variant([]))
    assert "days" not in out
    conn = get_conn()
    derived = json.loads(conn.execute("SELECT derived_from_json FROM meal_plan_entries WHERE id = ?",
                                      (out["entry_id"],)).fetchone()[0])
    conn.close()
    assert "make_double_for" not in derived and "make_double_note" not in derived
