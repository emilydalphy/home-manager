"""
The household's own pick is never refused for time (Emily, 2026-10-18:
"If I ask for chicken it should let me do it").

Thursday's Thai Basil Beef Stir-Fry (25 minutes), Tweak -> Change the
protein -> Ground chicken, answered "I left it as it was — Ground chicken
takes 55 minutes, and Thursday only has 30." Two things were wrong:
  * the 55 — the rewrite was never told the dish's own time, so it
    estimated from scratch, and ground chicken in a stir-fry is exactly as
    quick as ground beef (plate_parts._keep_quick_time);
  * the refusal — a dish the HOUSEHOLD picked was held to the cap that
    shapes what Pomona suggests (swap_in_place.over_cap_said).

The model is never called: `asker` hands back the rewrite.
"""
from __future__ import annotations

import datetime
import json

import pytest

from conftest import household_today

from app import tools
from app.db import get_conn
from app.tools import plate_parts as pp
from app.tools import swap_in_place as sip
from app.tools import weekly_plan as wp

TODAY = household_today()
START = TODAY.isoformat()
# A weekday dinner (Mon-Thu, so the weeknight cap applies) with the next
# day still inside the seeded week, for its leftover lunch.
COOK_DAY = next(TODAY + datetime.timedelta(days=i) for i in range(1, 6)
                if (TODAY + datetime.timedelta(days=i)).weekday() <= 3)
NEXT_DAY = COOK_DAY + datetime.timedelta(days=1)
D1, D2 = COOK_DAY.isoformat(), NEXT_DAY.isoformat()
WEEKDAY = COOK_DAY.strftime("%A")
STIR_FRY = "Thai Basil Beef Stir-Fry with Jasmine Rice"


def _rewrite(name, protein_row, prep, cook, protein="chicken", seen=None):
    def asker(context):
        if seen is not None:
            seen.append(context)
        return {
            "meal_name": name, "reason": "Chicken instead of beef.",
            "ingredients": [protein_row,
                            {"item": "Thai basil", "qty": "1 bunch", "category": "produce"},
                            {"item": "Jasmine rice", "qty": "2 cups", "category": "pantry"}],
            "instructions": ["Cook the rice.", "Stir-fry the protein.", "Toss with basil."],
            "food_groups": ["protein", "vegetable", "carb"], "main_protein": protein,
            "prep_time_minutes": prep, "cook_time_minutes": cook,
        }
    return asker


GROUND_CHICKEN = {"item": "Ground chicken", "qty": "1 lb", "category": "meat/seafood"}
THIGHS = {"item": "Bone-in chicken thighs", "qty": "2 lbs", "category": "meat/seafood"}


@pytest.fixture
def week():
    tools.add_member("Emily")
    tools.add_member("Vineeth")
    tools.edit_preference("weeknight_max_minutes", 30)
    tools.add_recipe(
        STIR_FRY,
        ingredients=[{"item": "Ground beef", "qty": "1 lb", "category": "meat/seafood"},
                     {"item": "Thai basil", "qty": "1 bunch", "category": "produce"},
                     {"item": "Jasmine rice", "qty": "2 cups", "category": "pantry"}],
        food_groups=["protein", "vegetable", "carb"], main_protein="beef",
        instructions=["Cook the rice.", "Stir-fry the beef.", "Toss with basil."],
        prep_time_minutes=10, cook_time_minutes=15,
    )
    plan_id = tools.create_weekly_plan(START)["weekly_plan_id"]
    entry = tools.plan_meal(D1, STIR_FRY, slot="dinner", weekly_plan_id=plan_id)["entry_id"]
    pp._OPTIONS_CACHE.clear()
    return plan_id, entry


def _with_leftover_lunch(plan_id, cook):
    """The cook feeds the next day's lunch — the shape that reached the
    cap refusal (change_part gated a chain; a single night it did not)."""
    tools.plan_meal(D2, STIR_FRY, slot="lunch", weekly_plan_id=plan_id,
                    derived_from={"links_to": f"entry_id:{cook}"})
    conn = get_conn()
    conn.execute("UPDATE meal_plan_entries SET derived_from_json = ? WHERE id = ?",
                 (json.dumps({"make_double_for": [f"{D2}:lunch"]}), cook))
    conn.commit()
    conn.close()


def _total(name):
    r = tools.get_recipe(name)
    return (r["prep_time_minutes"] or 0) + (r["cook_time_minutes"] or 0)


def test_ground_chicken_in_the_stir_fry_goes_on_and_keeps_its_25_minutes(week):
    plan_id, cook = week
    _with_leftover_lunch(plan_id, cook)
    seen: list = []
    # What the model said on the live card: 55 minutes, from nothing.
    out = pp.change_part(plan_id, cook, "protein", "Ground chicken",
                         asker=_rewrite("Thai Basil Chicken Stir-Fry with Jasmine Rice",
                                        GROUND_CHICKEN, 15, 40, seen=seen))
    assert out["status"] == "changed", out.get("message")
    assert seen[0]["current_minutes"] == {"prep": 10, "cook": 15}, "the rewrite is told the dish's time"
    assert _total(out["meal"]) == 25, "a same-method protein swap doesn't inflate the time"
    assert "over_cap_said" not in out, "25 minutes fits a 30-minute night; nothing to say"


def test_the_households_own_pick_over_the_cap_goes_on_and_the_toast_names_the_dish(week, signed_in, monkeypatch):
    plan_id, cook = week
    name = "Thai Basil Chicken Thigh Stir-Fry with Jasmine Rice"
    monkeypatch.setattr(pp, "_ask_variant",
                        lambda ctx, role="protein": _rewrite(name, THIGHS, 10, 30)(ctx))
    res = signed_in.post(f"/api/week/{START}/change-part",
                         json={"entry_id": cook, "role": "protein", "choice": "Chicken thighs"})
    assert res.status_code == 200
    body = res.json()
    assert body["status"] == "changed", body.get("message")
    assert body["day"]["dinner"]["title"] == name
    # A slower protein keeps the model's longer figure, and says so once.
    assert body["over_cap_said"] == f"Done — {name} runs about 40 minutes, a bit over {WEEKDAY}’s 30."


def test_a_dish_on_two_meals_says_both_and_then_the_time(week):
    plan_id, cook = week
    _with_leftover_lunch(plan_id, cook)
    name = "Thai Basil Chicken Thigh Stir-Fry with Jasmine Rice"
    out = pp.change_part(plan_id, cook, "protein", "Chicken thighs", asker=_rewrite(name, THIGHS, 15, 40))
    assert out["status"] == "changed", out.get("message")
    assert out["over_cap_said"].startswith("Swapped to " + name)
    assert out["over_cap_said"].endswith(f"It runs about 55 minutes, over {WEEKDAY}’s 30.")


def test_pomonas_own_pick_is_still_held_to_the_cap(week):
    """The cap still shapes what Pomona chooses on its own: the write's
    backstop refuses a slow dish a non-household door hands it."""
    plan_id, cook = week
    _with_leftover_lunch(plan_id, cook)
    group = sip.fed_days(plan_id, cook)
    assert len(group) == 2
    slow = _rewrite("Slow Braise", THIGHS, 15, 40)({})
    with pytest.raises(wp.SlotRefused):
        sip.apply_pick_to_days(plan_id, group, slow)


# ---------- Card B: salmon -> shrimp on Wednesday's dinner ----------

SALMON = "Seared Salmon with Lemon-Dill Potatoes and Asparagus"
SHRIMP_NAME = "Seared Shrimp with Lemon-Dill Potatoes and Asparagus"


def _shrimp_rewrite(seen=None):
    def asker(context):
        if seen is not None:
            seen.append(context)
        return {
            "meal_name": SHRIMP_NAME, "reason": "Shrimp instead of salmon — a little quicker.",
            "ingredients": [{"item": "Large shrimp, peeled", "qty": "1 lb", "category": "meat/seafood"},
                            {"item": "Baby potatoes", "qty": "1.5 lbs", "category": "produce"},
                            {"item": "Asparagus", "qty": "1 bunch", "category": "produce"},
                            {"item": "Fresh dill", "qty": "1 bunch", "category": "produce"},
                            {"item": "Lemon", "qty": "1", "category": "produce"}],
            "instructions": ["Roast the potatoes.", "Roast the asparagus.", "Sear the shrimp two minutes a side.",
                             "Toss with lemon and dill."],
            "food_groups": ["protein", "vegetable", "carb"], "main_protein": "shrimp",
            "prep_time_minutes": 10, "cook_time_minutes": 25,
        }
    return asker


def _salmon_week(pending: bool):
    tools.add_member("Emily")
    tools.add_member("Vineeth")
    tools.edit_preference("weeknight_max_minutes", 30)
    if pending:
        # How a generated DRAFT holds a new dish: a pending row and its
        # dish note, written up at approval.
        tools.add_recipe(SALMON, ingredients=[], food_groups=["protein", "vegetable", "carb"],
                         main_protein="salmon", prep_time_minutes=10, cook_time_minutes=25,
                         details_pending=True, dish_note="Seared salmon, dill potatoes, asparagus")
    else:
        tools.add_recipe(
            SALMON,
            ingredients=[{"item": "Salmon fillets", "qty": "1.5 lbs", "category": "meat/seafood"},
                         {"item": "Baby potatoes", "qty": "1.5 lbs", "category": "produce"},
                         {"item": "Asparagus", "qty": "1 bunch", "category": "produce"},
                         {"item": "Fresh dill", "qty": "1 bunch", "category": "produce"},
                         {"item": "Lemon", "qty": "1", "category": "produce"}],
            food_groups=["protein", "vegetable", "carb"], main_protein="salmon",
            instructions=["Roast the potatoes.", "Roast the asparagus.", "Sear the salmon.",
                          "Toss with lemon and dill."],
            prep_time_minutes=10, cook_time_minutes=25, default_servings=4,
        )
    plan_id = tools.create_weekly_plan(START)["weekly_plan_id"]
    entry = tools.plan_meal(D1, SALMON, slot="dinner", weekly_plan_id=plan_id)["entry_id"]
    pp._OPTIONS_CACHE.clear()
    return plan_id, entry


@pytest.mark.parametrize("pending", [True, False], ids=["pending-draft-dish", "written-up-dish"])
def test_salmon_to_shrimp_on_a_draft_renames_the_dish_and_keeps_the_rest(pending, signed_in, monkeypatch):
    plan_id, entry = _salmon_week(pending)
    seen: list = []
    monkeypatch.setattr(pp, "_ask_variant", lambda ctx, role="protein": _shrimp_rewrite(seen)(ctx))
    res = signed_in.post(f"/api/week/{START}/change-part",
                         json={"entry_id": entry, "role": "protein", "choice": "Shrimp"})
    assert res.status_code == 200
    body = res.json()
    assert body["status"] == "changed", body.get("message")
    assert body["day"]["dinner"]["title"] == SHRIMP_NAME
    saved = tools.get_recipe(SHRIMP_NAME)
    assert saved["main_protein"] == "shrimp"
    items = {i["item"].lower() for i in saved["ingredients"]}
    assert "asparagus" in items and "baby potatoes" in items and not any("salmon" in i for i in items)


def test_salmon_to_shrimp_on_an_approved_week_moves_the_shopping(signed_in):
    plan_id, entry = _salmon_week(pending=False)
    tools.approve_weekly_plan(plan_id, approved_by="Emily")
    names = lambda: {(g["item"] or "").lower() for g in tools.list_grocery_list("needed")}
    assert any("salmon" in n for n in names())
    out = pp.change_part(plan_id, entry, "protein", "Shrimp", asker=_shrimp_rewrite())
    assert out["status"] == "changed", out.get("message")
    after = names()
    assert not any("salmon" in n for n in after), after
    shrimp = [g for g in tools.list_grocery_list("needed") if "shrimp" in (g["item"] or "").lower()]
    assert shrimp and shrimp[0]["quantity"], shrimp


# ---------- review follow-ups, 2026-10-19 ----------

def test_a_slow_typed_choice_keeps_the_models_longer_time(week):
    plan_id, cook = week
    name = "Slow-Cooked Pork Shoulder Stir-Fry"
    pork = {"item": "Pork shoulder", "qty": "2 lbs", "category": "meat/seafood"}
    out = pp.change_part(plan_id, cook, "protein", "slow-cooked pork shoulder with beans",
                         asker=_rewrite(name, pork, 15, 40, protein="pork"))
    assert out["status"] == "changed", out.get("message")
    assert _total(out["meal"]) == 55, "a slow dish the household typed keeps its honest time"
    assert out["over_cap_said"].startswith(f"Done — {name} runs about 55 minutes")


def test_only_a_short_quick_choice_counts_as_quick():
    for quick in ("Ground chicken", "shrimp", "canned chickpeas", "thin-sliced flank steak"):
        assert pp._is_quick_protein(quick), quick
    for slow in ("braised short rib strips", "dried chickpeas", "tofu", "lentils",
                 "bone-in ground-style thighs", "slow-cooked pork shoulder with beans"):
        assert not pp._is_quick_protein(slow), slow


def test_the_chat_cards_own_pick_over_the_cap_goes_on_and_says_so(week):
    plan_id, _cook = week
    from app.tools import proposals as prop
    prop._PROPOSALS.clear()
    name = "Chicken Thigh Traybake"
    card = tools.propose_plan_changes(plan_id, [
        {"date": D1, "slot": "dinner", "action": "change",
         "candidates": [_rewrite(name, THIGHS, 10, 30)({})]},
    ])
    applied = tools.apply_proposal(card["proposal_id"])
    assert applied["status"] == "applied", applied
    row = applied["proposal"]["applied"][0]
    assert row["over_cap_said"] == f"Done — {name} runs about 40 minutes, a bit over {WEEKDAY}’s 30."
    assert row["over_cap_note"] == f"{name} runs about 40 minutes, a bit over {WEEKDAY}’s 30."


def test_the_chat_cards_toast_says_the_over_limit_line():
    from pathlib import Path
    shell = (Path(__file__).resolve().parent.parent / "static" / "shell.js").read_text(encoding="utf-8")
    i = shell.index("  function changeCardSavedLine(applied) {")
    body = shell[i:shell.index("  function changeCardHtml(", i)]
    assert "applied[0].over_cap_said" in body and "a.over_cap_note" in body


def test_another_on_the_chat_card_is_pomonas_pick_and_held_to_the_cap(week):
    plan_id, cook = week
    _with_leftover_lunch(plan_id, cook)
    from app.tools import proposals as prop
    prop._PROPOSALS.clear()
    card = tools.propose_plan_changes(plan_id, [
        {"date": D1, "slot": "dinner", "action": "change",
         "candidates": [_rewrite("Chicken Basil Bowls", GROUND_CHICKEN, 10, 15)({})]},
    ])
    picks = iter([_rewrite("Slow Chicken Braise", THIGHS, 15, 40)({}),
                  _rewrite("Quick Chicken Lettuce Cups", GROUND_CHICKEN, 10, 10)({})])
    again = prop.another_for_row(card["proposal_id"], 0, picker=lambda ctx: next(picks))
    assert again["status"] == "picked"
    names = [c["meal_name"] for c in again["proposal"]["rows"][0]["candidates"]]
    assert names == ["Chicken Basil Bowls", "Quick Chicken Lettuce Cups"], "the slow one was not offered"
    stored = prop.get_proposal(card["proposal_id"])["rows"][0]["candidates"]
    assert stored[-1].get("by_pomona") is True and not stored[0].get("by_pomona")


def test_a_pomona_pick_on_the_chat_card_is_not_let_through_save_uncapped(week):
    """Belt and braces: a by_pomona candidate that runs long on a chained
    dinner meets the write's own cap backstop — refused per row."""
    plan_id, cook = week
    _with_leftover_lunch(plan_id, cook)
    from app.tools import proposals as prop
    prop._PROPOSALS.clear()
    card = tools.propose_plan_changes(plan_id, [
        {"date": D1, "slot": "dinner", "action": "change",
         "candidates": [_rewrite("Slow Chicken Braise", THIGHS, 15, 40)({})]},
    ])
    prop.get_proposal(card["proposal_id"])["rows"][0]["candidates"][0]["by_pomona"] = True
    applied = tools.apply_proposal(card["proposal_id"])
    assert applied["status"] == "refused" and "only has 30" in applied["refused"][0]["why"]
