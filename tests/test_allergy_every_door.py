"""
A dish with a household member's saved allergen never lands on the week in
silence — the doors that had no test of their own (allergy door hunt,
2026-10-29).

The rule is enforced at many doors (allergen_gate: the draft's split_safe /
repick / sweep, chat's refuse_if_clashing, the swap's pick_gate and
offer_gate, the approval confirm). Most already had a pinning test; these
are the ones that did not, each walked through its real route or tool:

  - a freezer portion planned onto a night by the week draft (was OPEN:
    its night's name is not a recipe, so the sweep and approval could not
    read the dish's list — fixed in freezer_portions.safe_to_plan);
  - "Build a plan" on a left-out day (/fill-day);
  - a different protein on a planned dish (/change-part);
  - a recipe imported from a link, then picked for an open night;
  - a holiday's "bring a dish" on a draft week (the approval confirm).

Sam is allergic to peanuts throughout.
"""
import datetime

import pytest

from app import tools
from app.tools import plate_parts as pp
from app.tools import swap_in_place as sip
from app.tools import freezer_portions as _fp
from conftest import household_today

from tests.test_freezer_portion_planned import (  # noqa: F401  (stub_model is a fixture)
    _a_frozen_portion, _dinners, _draft_next_week, _freezer, _monday, stub_model,
)
from tests.test_holidays import _thanksgiving


TODAY = household_today()
D1 = (TODAY + datetime.timedelta(days=1)).isoformat()
D2 = (TODAY + datetime.timedelta(days=2)).isoformat()
SATAY = [{"item": "Peanut butter", "qty": "3 tbsp", "category": "pantry"},
         {"item": "Rice noodles", "qty": "400 g", "category": "pantry"}]


def _peanut_allergy():
    if not any(m["name"] == "Sam" for m in tools.list_members()):
        tools.add_member("Sam")
    tools.set_member_dietary_restrictions("Sam", ["peanut allergy"])


def _dish(name, ingredients, protein="chicken"):
    return {
        "meal_name": name, "reason": "Quick.", "ingredients": ingredients,
        "instructions": ["Cook it.", "Serve."], "food_groups": ["protein", "vegetable", "carb"],
        "main_protein": protein, "prep_time_minutes": 5, "cook_time_minutes": 15, "default_servings": 2,
    }


def _rows(plan_id, day):
    return sorted((m["slot"], m["slot_state"], m.get("meal"))
                  for m in tools.get_weekly_plan(plan_id)["meals"] if m["date"] == day)


# ---------- a freezer portion planned onto a night ----------

def test_a_frozen_portion_with_the_allergen_stays_in_the_freezer(stub_model):
    """CATCH. Frozen on a night off before Sam's allergy was saved; the
    next week's draft put it on the rush night as "Leftovers from the
    freezer — Thai Noodle Bowl" with no word anywhere."""
    for name in ("Emily", "Sam"):
        tools.add_member(name)
    tools.add_recipe("Thai Noodle Bowl", ingredients=SATAY, prep_time_minutes=10, cook_time_minutes=20,
                     default_servings=4)
    _a_frozen_portion("Thai Noodle Bowl", day_index=1)
    _peanut_allergy()

    out = _draft_next_week(stub_model, rush=tools._week_dates(_monday(1))[2])

    assert not any("Thai Noodle Bowl" in (m or "") for m in _dinners(out["plan_id"]).values())
    # Still in the freezer: what to do with it is the household's call.
    assert [r["item"] for r in _freezer()] == ["Thai Noodle Bowl (cooked)"]


def test_a_safe_portion_still_gets_its_night():
    """GUARD: the gate holds back the clash, not every portion."""
    _peanut_allergy()
    tools.add_recipe("Kofte", ingredients=[{"item": "Ground lamb", "qty": "1 lb"}])
    tools.add_recipe("Thai Noodle Bowl", ingredients=SATAY)
    portions = [{"dish": "Kofte", "inventory_item_id": 1}, {"dish": "Thai Noodle Bowl", "inventory_item_id": 2}]
    assert [p["dish"] for p in _fp.safe_to_plan(portions)] == ["Kofte"]
    # Read by name too, for a portion whose recipe has gone.
    assert _fp.safe_to_plan([{"dish": "Peanut Stew", "inventory_item_id": 3}]) == []


# ---------- "Build a plan" on a left-out day ----------

@pytest.fixture
def left_out_day():
    _peanut_allergy()
    start = (TODAY - datetime.timedelta(days=1)).isoformat()
    plan_id = tools.create_weekly_plan(start)["weekly_plan_id"]
    tools.plan_meal(D1, "Tacos", slot="dinner", weekly_plan_id=plan_id)
    for slot in ("breakfast", "lunch", "dinner"):
        tools.plan_slot_empty(weekly_plan_id=plan_id, meal_date=D2, slot=slot,
                              reason=tools.SKIPPED_DAY_REASON,
                              derived_from={"constraint": "skipped_day"})
    return start, plan_id


def test_fill_day_never_plans_a_dish_with_the_allergen(left_out_day, signed_in, monkeypatch):
    start, plan_id = left_out_day
    asked = []

    def picker(context):
        asked.append(context["slot"])
        if context["slot"] == "dinner":
            return _dish("Satay Chicken Noodles", SATAY)
        return _dish(f"Plain {context['slot']}", [{"item": "Oats", "qty": "1 cup", "category": "pantry"}])

    monkeypatch.setattr(sip, "_pick_replacement", picker)
    res = signed_in.post(f"/api/week/{start}/fill-day", json={"date": D2})
    assert res.status_code == 200, res.text
    assert asked.count("dinner") >= 1
    meals = [meal for _slot, _state, meal in _rows(plan_id, D2)]
    assert "Satay Chicken Noodles" not in meals
    dinner = next(r for r in _rows(plan_id, D2) if r[0] == "dinner")
    assert dinner[1] != "planned", "the clashing dinner is left as it was, never written"
    assert res.json().get("partial") is True


# ---------- a different protein on a planned dish ----------

def test_change_part_refuses_a_rewrite_that_brings_the_allergen_in(signed_in, monkeypatch):
    _peanut_allergy()
    tools.add_recipe("Chicken Stir-Fry", ingredients=[{"item": "Chicken thighs", "qty": "1 lb"},
                                                       {"item": "Broccoli", "qty": "1 head"}],
                     food_groups=["protein", "vegetable"], main_protein="chicken",
                     instructions=["Stir-fry."], prep_time_minutes=10, cook_time_minutes=15)
    start = TODAY.isoformat()
    plan_id = tools.create_weekly_plan(start)["weekly_plan_id"]
    tools.plan_meal(D1, "Chicken Stir-Fry", slot="dinner", weekly_plan_id=plan_id)
    entry = next(m for m in tools.get_weekly_plan(plan_id)["meals"] if m["date"] == D1 and m["slot"] == "dinner")
    monkeypatch.setattr(pp, "_ask_variant", lambda ctx, role="protein": _dish(
        "Peanut Tofu Stir-Fry", [{"item": "Tofu", "qty": "1 block"}, {"item": "Peanuts", "qty": "1/2 cup"},
                                 {"item": "Broccoli", "qty": "1 head"}], protein="tofu"))
    res = signed_in.post(f"/api/week/{start}/change-part",
                         json={"entry_id": entry["entry_id"], "role": "protein", "choice": "Tofu"})
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["status"] == "refused" and "peanut" in body["message"].lower()
    assert ("dinner", "planned", "Chicken Stir-Fry") in _rows(plan_id, D1)


# ---------- a recipe imported from a link, then planned ----------

def test_an_imported_recipe_with_the_allergen_is_refused_on_an_open_night(signed_in):
    _peanut_allergy()
    # The import sheet's save (the link's draft, reviewed): the library may
    # hold it — the week may not.
    saved = signed_in.post("/api/recipes/add", json={
        "name": "Weeknight Noodles", "ingredients": [{"item": "Smooth peanut butter", "qty": "1/4 cup"},
                                                     {"item": "Spaghetti", "qty": "200 g"}],
        "instructions": ["Boil.", "Toss."], "source_url": "https://example.com/noodles"})
    assert saved.status_code == 200, saved.text
    start = TODAY.isoformat()
    plan_id = tools.create_weekly_plan(start)["weekly_plan_id"]
    tools.plan_slot_open(plan_id, D1, "dinner", "I would rather ask than guess.")
    res = signed_in.post(f"/api/week/{start}/slot", json={
        "date": D1, "slot": "dinner", "choice": "Weeknight Noodles", "weekly_plan_id": plan_id})
    assert res.status_code == 200, res.text
    assert res.json()["status"] == "refused" and "peanut" in res.json()["message"].lower()
    assert next(r for r in _rows(plan_id, D1) if r[0] == "dinner")[1] == "open"


# ---------- a holiday's "bring a dish" on a draft ----------

def test_bringing_a_dish_with_the_allergen_on_a_draft_asks_at_approval():
    """GUARD on the draft half of this door. The answer plans the dish as
    typed; Approve then stops on the clash for the household's own yes.
    (On an APPROVED week it lands with no word — reported as Needs Emily,
    not pinned.)"""
    tools.add_member("Emily")
    _peanut_allergy()
    tools.add_recipe("Satay Noodles", ingredients=SATAY)
    tools.add_recipe("Chili", ingredients=[{"item": "Kidney beans", "qty": "1 tin"}])
    tg = _thanksgiving()
    plan_id = tools.create_weekly_plan(tg)["weekly_plan_id"]
    tools.plan_meal(tg, "Chili", slot="dinner", weekly_plan_id=plan_id)
    tools.answer_holiday(tg, "out", bring_dish="Satay Noodles")
    assert ("dinner", "planned", "Satay Noodles") in _rows(plan_id, tg)
    out = tools.approve_weekly_plan(plan_id, approved_by="Emily")
    assert out["status"] == "needs_confirmation"
    assert tools.get_weekly_plan(plan_id)["status"] != "approved"
