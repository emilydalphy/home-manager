"""
The plate, part by part (Emily, 2026-09-13, "Shaping the Draft" Flows A
and B): the card shows the plate as Protein · Veg · Carb with a dashed
"+ Add a carb" where the rule finds a gap; the protein opens "Change the
protein" with options written for THIS dish; Save rewrites the recipe
around the pick and lands it through the swap's own gates and apply, so
Undo is the swap's own. A veg or carb opens "Add something" (already
built) for that part, and the sheet saves on purpose (S10).

BEHAVIOUR for app/tools/plate_parts.py and the two routes (the model is
injectable — `asker` — and never called); SOURCE for the shell.
"""
from __future__ import annotations

import datetime
from pathlib import Path

import pytest

from conftest import household_today

from app import tools
from app.tools import plate_parts as pp
from app.tools import swap_in_place as sip
from app.tools._shared import use_household


# Swapping a dish onto a night that has ALREADY GONE BY is refused now
# (overnight/swap-refuses-the-past, 2026-09-17), on the HOUSEHOLD's clock —
# so this week is seeded from the household's own today rather than from
# this calendar week's Monday, which put the first days of it behind today
# on every weekday but Monday and made every swap below a swap into the
# past. The names are POSITIONS in the seeded week, not weekdays; nothing
# in this file asserts a weekday. Same harness-artifact class the
# add_dish_day branch fixed in test_swap_atomic.py on 2026-09-16, and it
# presents the same way: the app is right and the seed is wrong.
TODAY = household_today()
WEEK_START = TODAY.isoformat()
DAYS = [(datetime.date.fromisoformat(WEEK_START) + datetime.timedelta(days=i)).isoformat()
        for i in range(7)]
DAY1, DAY2 = DAYS[0], DAYS[1]

REPO = Path(__file__).resolve().parent.parent
SHELL_JS = (REPO / "static" / "shell.js").read_text(encoding="utf-8")
SHELL_CSS = (REPO / "static" / "shell.css").read_text(encoding="utf-8")


def _variant(name="Beef burgers", protein="beef", ingredients=None, reason="Beef instead of turkey. Same recipe, same time."):
    return {
        "meal_name": name, "reason": reason,
        "ingredients": ingredients if ingredients is not None else [
            {"item": "Ground beef", "qty": "1 lb", "category": "meat/seafood"},
            {"item": "Burger buns", "qty": "4", "category": "pantry"},
            {"item": "Coleslaw mix", "qty": "1 bag", "category": "produce"},
        ],
        "instructions": ["Shape the patties.", "Grill four minutes a side.", "Build with the slaw."],
        "food_groups": ["protein", "vegetable"], "main_protein": protein,
        "prep_time_minutes": 10, "cook_time_minutes": 15, "default_servings": 2,
    }


@pytest.fixture
def week():
    tools.add_member("Emily")
    tools.add_member("Vineeth")
    tools.add_recipe(
        "Turkey burgers",
        ingredients=[{"item": "Ground turkey", "qty": "1 lb", "category": "meat/seafood"},
                     {"item": "Burger buns", "qty": "4", "category": "pantry"},
                     {"item": "Coleslaw mix", "qty": "1 bag", "category": "produce"}],
        food_groups=["protein", "vegetable"], main_protein="turkey",
        instructions=["Shape the patties.", "Grill four minutes a side."],
        prep_time_minutes=10, cook_time_minutes=15,
    )
    tools.add_recipe("Chili", ingredients=[{"item": "Ground beef", "qty": "1 lb", "category": "meat/seafood"}],
                     food_groups=["protein", "vegetable", "carb"], main_protein="beef")
    plan_id = tools.create_weekly_plan(WEEK_START)["weekly_plan_id"]
    tools.plan_meal(DAY2, "Turkey burgers", slot="dinner", weekly_plan_id=plan_id, reasoning="quick")
    tools.plan_meal(DAY1, "Chili", slot="dinner", weekly_plan_id=plan_id, reasoning="batch")
    pp._OPTIONS_CACHE.clear()
    return plan_id


def _dinner(plan_id, day):
    for d in tools.get_week_menu(plan_id)["days"]:
        if d["date"] == day:
            return d["dinner"]
    return None


# ---------- the plate as the card shows it ----------

def test_the_plate_names_the_protein_and_marks_what_is_missing():
    parts = pp.parts_of_plate("dinner", ["protein", "vegetable"], "turkey", [], "")
    assert [(p["role"], p["name"], p["missing"]) for p in parts] == [
        ("protein", "Turkey", False), ("vegetable", None, False), ("carb", None, True),
    ]
    assert parts[1]["source"] == "dish"


def test_a_side_covers_its_part_by_name_and_keto_reads_none_for_its_carb():
    parts = pp.parts_of_plate("dinner", ["protein"], "chicken", [{"name": "Roasted potatoes", "covers": ["carb"]}], "")
    by_role = {p["role"]: p for p in parts}
    assert by_role["carb"] == {"role": "carb", "word": "Carb", "name": "Roasted potatoes", "source": "side", "missing": False}
    assert by_role["vegetable"]["missing"] is True
    # A keto household's rule has no carb — but the card still OFFERS one
    # (Emily, 2026-09-15: "for the kebab meal I would like an option to
    # add a carb"). Dashed, theirs to take; the planner never fills it.
    # ...reading "None" rather than as a shortfall (Emily, 2026-09-21:
    # "None" only for a household on none), still a tap to add one.
    keto = pp.parts_of_plate("dinner", ["protein", "vegetable"], "salmon", [], "keto, low carb")
    assert [(p["role"], p["missing"]) for p in keto] == [("protein", False), ("vegetable", False), ("carb", False)]
    assert keto[-1] == {"role": "carb", "word": "Carb", "name": "None", "source": None, "missing": False, "empty": True}
    # Once they take it, it reads like anyone else's carb.
    keto = pp.parts_of_plate("dinner", ["protein", "vegetable"], "salmon",
                             [{"name": "Roasted potatoes", "covers": ["carb"]}], "keto, low carb")
    assert keto[-1] == {"role": "carb", "word": "Carb", "name": "Roasted potatoes", "source": "side", "missing": False}
    # And a keto dish that says nothing about what it covers is still
    # unknown, not short: no dashed chip there either.
    assert [p["role"] for p in pp.parts_of_plate("dinner", [], "steak", [], "keto")] == ["protein"]
    # Breakfast is held to the lighter rule: protein only.
    assert [p["role"] for p in pp.parts_of_plate("breakfast", [], "", [], "")] == ["protein"]


def test_a_dish_with_nothing_recorded_is_unknown_not_short():
    # "Roast Chicken" with no groups and no protein saved (an older recipe)
    # gets a plain Protein chip to change, and no dashed "add" chips — the
    # card never guesses out loud.
    parts = pp.parts_of_plate("dinner", [], "", [], "")
    assert parts == [{"role": "protein", "word": "Protein", "name": None, "source": "dish", "missing": False}]
    # ...unless a side is on it, which is a fact.
    parts = pp.parts_of_plate("dinner", [], "", [{"name": "Steamed rice", "covers": ["carb"]}], "")
    assert [(p["role"], p["name"]) for p in parts] == [("protein", None), ("carb", "Steamed rice")]


def test_the_week_menu_carries_the_parts_on_a_planned_slot(week):
    dinner = _dinner(week, DAY2)
    assert dinner["main_protein"] == "turkey"
    # The dish's own "Burger buns" is a carb the recipe's food_groups
    # never recorded — the same class of gap as Emily's Cajun Salmon/Sweet
    # Potato Mash night (2026-09-22): plates.dish_has_carb catches it
    # deterministically, so the plate isn't reported short a carb it
    # already has, and it's named off that same ingredient rather than
    # left as a bare "In the dish".
    assert [(p["role"], p["name"], p["missing"]) for p in dinner["plate_parts"]] == [
        ("protein", "Turkey", False), ("vegetable", "Coleslaw mix", False), ("carb", "Burger buns", False),
    ]
    assert all(not p["missing"] for p in _dinner(week, DAY1)["plate_parts"])


# ---------- the options ----------

def test_options_are_asked_for_this_dish_and_cached_for_the_sitting(week):
    entry_id = _dinner(week, DAY2)["entry_id"]
    seen = []

    def asker(context):
        seen.append(context)
        return [{"name": "Ground beef", "note": ""}, {"name": "ground pork", "note": "richer"},
                {"name": "Ground turkey", "note": "what's there"}, {"name": "Ground chicken", "note": "lighter"},
                {"name": "Black bean patties", "note": "vegetarian"}]

    out = pp.part_options(week, entry_id, asker=asker)
    assert out["meal"] == "Turkey burgers" and out["current"] == "turkey"
    # The dish, its ingredients and its method are what the call is told.
    assert seen[0]["dish"] == "Turkey burgers"
    assert seen[0]["current_protein"] == "turkey"
    assert any(i["item"] == "Ground turkey" for i in seen[0]["ingredients"])
    assert seen[0]["method"][0] == "Shape the patties."
    # Never the protein it already has; at most four; names capitalised.
    assert [o["name"] for o in out["options"]] == ["Ground beef", "Ground pork", "Ground chicken", "Black bean patties"]
    assert out["options"][1]["note"] == "richer"
    assert out["options_unavailable"] is False  # the call worked
    # The second open is instant — no second call.
    again = pp.part_options(week, entry_id, asker=lambda ctx: (_ for _ in ()).throw(AssertionError("called twice")))
    assert again["options"] == out["options"]
    assert again["options_unavailable"] is False


def test_options_carry_the_houses_exclusions_and_survive_a_failed_call(week):
    tools.set_member_dietary_restrictions("Emily", ["shellfish"])
    tools.add_food_dislikes(["mushrooms"])
    entry_id = _dinner(week, DAY2)["entry_id"]
    seen = []

    def asker(context):
        seen.append(context)
        raise RuntimeError("model down")

    out = pp.part_options(week, entry_id, asker=asker)
    assert out["options"] == []  # the sheet still has "Something else…"
    # A failed call is flagged apart from the model genuinely finding
    # nothing (bug card, 2026-09-15) — the sheet's "couldn't think of
    # options" line can eventually tell the two apart even though both
    # leave `options` empty today.
    assert out["options_unavailable"] is True
    assert "shellfish" in " ".join(seen[0]["must_not_contain"]).lower()
    assert "mushrooms" in seen[0]["dislikes"]


def test_options_unavailable_is_false_when_the_model_genuinely_finds_nothing(week):
    # A successful call that simply has nothing to offer is not the same
    # as the call failing — options_unavailable stays False so the two
    # are told apart at the data layer even though the sheet's copy
    # (checked in the shell/source tests) reads the same either way.
    entry_id = _dinner(week, DAY2)["entry_id"]
    out = pp.part_options(week, entry_id, asker=lambda context: [])
    assert out["options"] == []
    assert out["options_unavailable"] is False


def test_an_unknown_role_is_refused(week):
    entry_id = _dinner(week, DAY2)["entry_id"]
    with pytest.raises(ValueError, match="No such part"):
        pp.part_options(week, entry_id, role="dessert", asker=lambda c: [])
    with pytest.raises(ValueError, match="No such part"):
        pp.change_part(week, entry_id, "dessert", "Rice", asker=lambda c: {})


def test_a_missing_part_is_not_a_change_its_still_add():
    """
    "Change" only ever applies to a part that's already on the plate
    (Emily, 2026-09-22): a MISSING vegetable or carb keeps opening the
    catalogue add-sheet — this is what part_options/change_part check
    before spending a model call, pure and needing no database plan.
    """
    assert pp._current_part(
        {"food_groups": ["protein"]}, {"ingredients": []}, "carb", [],
    ) == ("", None)
    assert pp._current_part(
        {"food_groups": ["protein", "carb"]},
        {"ingredients": [{"item": "Rice", "category": "pantry"}]}, "carb", [],
    ) == ("Rice", "dish")
    assert pp._current_part(
        {"food_groups": ["protein"]}, {"ingredients": []}, "carb",
        [{"name": "Steamed rice", "covers": ["carb"]}],
    ) == ("Steamed rice", "side")


# ---------- the change ----------

def test_save_rewrites_the_dish_around_the_pick_through_the_swaps_own_door(week):
    entry_id = _dinner(week, DAY2)["entry_id"]
    seen = []

    def asker(context):
        seen.append(context)
        return _variant()

    out = pp.change_part(week, entry_id, "protein", "Ground beef", asker=asker)
    assert out["status"] == "changed"
    assert out["meal"] == "Beef burgers" and out["replaced"] == "Turkey burgers"
    assert out["reason"] == "Beef instead of turkey. Same recipe, same time."
    assert out["day"]["dinner"]["title"] == "Beef burgers"
    assert seen[0]["new_protein"] == "Ground beef" and seen[0]["dish"] == "Turkey burgers"
    # Saved as a recipe of its own (cookable, shoppable), the dish's
    # protein recorded, the plate's parts following.
    saved = tools.get_recipe("Beef burgers")
    assert saved["main_protein"] == "beef"
    assert _dinner(week, DAY2)["plate_parts"][0]["name"] == "Beef"
    # The swap's undo note is there, so the swap's undo puts turkey back.
    entry = sip._entry(week, out["entry_id"])
    assert entry["derived_from"]["swapped_from"]["meal"] == "Turkey burgers"
    undone = tools.undo_meal_swap(week, out["entry_id"])
    assert undone["meal"] == "Turkey burgers"
    assert _dinner(week, DAY2)["title"] == "Turkey burgers"


def test_a_pick_the_house_cannot_have_is_refused_and_nothing_is_written(week):
    tools.set_member_dietary_restrictions("Emily", ["shellfish"])
    entry_id = _dinner(week, DAY2)["entry_id"]
    out = pp.change_part(
        week, entry_id, "protein", "Shrimp",
        asker=lambda c: _variant("Shrimp burgers", "shrimp",
                                 ingredients=[{"item": "Shrimp", "qty": "1 lb", "category": "meat/seafood"}]),
    )
    assert out["status"] == "refused"
    assert "Shrimp" in out["message"] and "as it was" in out["message"]
    assert _dinner(week, DAY2)["title"] == "Turkey burgers"
    assert not any(r["name"] == "Shrimp burgers" for r in tools.list_recipes())


def test_a_model_that_comes_back_with_nothing_is_refused_not_guessed(week):
    entry_id = _dinner(week, DAY2)["entry_id"]
    out = pp.change_part(week, entry_id, "protein", "Lamb", asker=lambda c: {})
    assert out["status"] == "refused" and out["message"] == pp.REFUSAL
    assert _dinner(week, DAY2)["title"] == "Turkey burgers"


def test_a_change_forgets_the_cached_options_for_that_slot(week):
    entry_id = _dinner(week, DAY2)["entry_id"]
    pp.part_options(week, entry_id, asker=lambda c: [{"name": "Ground beef"}])
    assert (tools.household_id(), entry_id, "protein") in pp._OPTIONS_CACHE
    pp.change_part(week, entry_id, "protein", "Ground beef", asker=lambda c: _variant())
    assert (tools.household_id(), entry_id, "protein") not in pp._OPTIONS_CACHE


def test_changing_a_dish_sourced_vegetable_replaces_it_not_adds(week):
    """
    The real bug (Emily, 2026-09-22): "Cajun Salmon with Green Beans and
    Sweet Potato Mash" — the green beans are the DISH's own, not a side.
    "Change" on the veg must rewrite the recipe around the new one, the
    same way a protein change does, not bolt on a second vegetable.
    """
    tools.add_recipe(
        "Cajun Salmon with Green Beans and Sweet Potato Mash",
        ingredients=[
            {"item": "Salmon fillets", "qty": "4", "category": "meat/seafood"},
            {"item": "Green beans", "qty": "1 lb", "category": "produce"},
            {"item": "Sweet potato", "qty": "2", "category": "produce"},
        ],
        food_groups=["protein", "vegetable"], main_protein="salmon",
        prep_time_minutes=10, cook_time_minutes=25,
    )
    plan_id = tools.create_weekly_plan(WEEK_START)["weekly_plan_id"]
    tools.plan_meal(DAY2, "Cajun Salmon with Green Beans and Sweet Potato Mash", slot="dinner", weekly_plan_id=plan_id)
    entry_id = _dinner(plan_id, DAY2)["entry_id"]
    before = _dinner(plan_id, DAY2)
    assert [p for p in before["plate_parts"] if p["role"] == "vegetable"][0]["name"] == "Green beans"
    assert [p for p in before["plate_parts"] if p["role"] == "carb"][0]["name"] == "Sweet potato"

    seen = []

    def asker(context):
        seen.append(context)
        assert context["current_vegetable"] == "Green beans"
        return {
            "meal_name": "Cajun Salmon with Broccoli and Sweet Potato Mash",
            "reason": "Broccoli instead of green beans — same time.",
            "ingredients": [
                {"item": "Salmon fillets", "qty": "4", "category": "meat/seafood"},
                {"item": "Broccoli", "qty": "1 head", "category": "produce"},
                {"item": "Sweet potato", "qty": "2", "category": "produce"},
            ],
            "instructions": ["Season and pan-sear the salmon.", "Steam the broccoli.", "Mash the sweet potato."],
            "food_groups": ["protein", "vegetable", "carb"], "main_protein": "salmon",
            "prep_time_minutes": 10, "cook_time_minutes": 25, "default_servings": 4,
        }

    out = pp.change_part(plan_id, entry_id, "vegetable", "Broccoli", asker=asker)

    assert out["status"] == "changed"
    assert out["meal"] == "Cajun Salmon with Broccoli and Sweet Potato Mash"
    assert out["replaced"] == "Cajun Salmon with Green Beans and Sweet Potato Mash"
    after = _dinner(plan_id, DAY2)
    assert after["title"] == "Cajun Salmon with Broccoli and Sweet Potato Mash"
    assert [p for p in after["plate_parts"] if p["role"] == "vegetable"][0]["name"] == "Broccoli"
    # The protein and the carb are untouched — this replaced one part.
    assert [p for p in after["plate_parts"] if p["role"] == "protein"][0]["name"] == "Salmon"
    assert [p for p in after["plate_parts"] if p["role"] == "carb"][0]["name"] == "Sweet potato"
    assert after["sides"] == []
    saved = tools.get_recipe("Cajun Salmon with Broccoli and Sweet Potato Mash")
    assert any(i["item"] == "Broccoli" for i in saved["ingredients"])
    assert not any(i["item"] == "Green beans" for i in saved["ingredients"])
    # Groceries reflect the new dish.
    tools.approve_weekly_plan(plan_id)
    groc = {i["item"].lower() for i in tools.list_grocery_list()}
    assert "broccoli" in groc

    # ...and Undo puts the green beans back, the same way a protein
    # change's Undo does.
    entry = sip._entry(plan_id, out["entry_id"])
    assert entry["derived_from"]["swapped_from"]["meal"] == "Cajun Salmon with Green Beans and Sweet Potato Mash"
    tools.undo_meal_swap(plan_id, out["entry_id"])
    back = _dinner(plan_id, DAY2)
    assert back["title"] == "Cajun Salmon with Green Beans and Sweet Potato Mash"
    assert [p for p in back["plate_parts"] if p["role"] == "vegetable"][0]["name"] == "Green beans"


def test_a_side_sourced_part_refuses_change_part_theres_one_door(week):
    """
    A carb added from the sheet (a side, not the dish) has exactly ONE
    door for "Change": the client's own "Add something" flow, which
    already replaces a side one-for-one with its own one-tap Undo
    (runMealAddUndo). change_part/part_options refuse a side-sourced part
    plainly rather than offering a second door the client never actually
    calls (verifier, 2026-09-22).
    """
    entry_id = _dinner(week, DAY2)["entry_id"]
    tools.add_component(entry_id, key="rice")

    with pytest.raises(ValueError, match="take it off and add a new one"):
        pp.change_part(week, entry_id, "carb", "Roasted potatoes", asker=lambda c: {})
    with pytest.raises(ValueError, match="take it off and add a new one"):
        pp.part_options(week, entry_id, role="carb", asker=lambda c: [])
    # Untouched — the refusal wrote nothing.
    assert [s["name"] for s in _dinner(week, DAY2)["sides"]] == ["Rice"]


def test_a_side_sourced_carb_still_replaces_one_for_one_through_add(week):
    """
    The one door that DOES work for a side: plates.add_component +
    remove_component (what the client's runMealAdd already calls in
    sequence) — one new side on, the old one off, never both at once.
    """
    entry_id = _dinner(week, DAY2)["entry_id"]
    tools.add_component(entry_id, key="rice")
    assert [s["name"] for s in _dinner(week, DAY2)["sides"]] == ["Rice"]

    tools.remove_component(entry_id, "Rice")
    tools.add_component(entry_id, key="roasted-potatoes")

    after = _dinner(week, DAY2)
    assert [s["name"] for s in after["sides"]] == ["Roasted potatoes"]
    assert after["title"] == "Turkey burgers", "the dish itself never changed"


def test_another_households_meal_is_not_changeable(week):
    entry_id = _dinner(week, DAY2)["entry_id"]
    with use_household(2):
        with pytest.raises(ValueError, match="No meal"):
            pp.change_part(week, entry_id, "protein", "Ground beef", asker=lambda c: _variant())
    assert _dinner(week, DAY2)["title"] == "Turkey burgers"


# ---------- the routes ----------

def test_the_routes_offer_and_change(signed_in, week, monkeypatch):
    entry_id = _dinner(week, DAY2)["entry_id"]
    monkeypatch.setattr(pp, "_ask_options", lambda ctx, role="protein": [{"name": "Ground beef", "note": ""}])
    monkeypatch.setattr(pp, "_ask_variant", lambda ctx, role="protein": _variant())
    res = signed_in.get(f"/api/week/{WEEK_START}/part-options?entry_id={entry_id}&role=protein")
    assert res.status_code == 200
    assert res.json()["options"] == [{"name": "Ground beef", "note": ""}]
    assert res.json()["options_unavailable"] is False
    res = signed_in.post(f"/api/week/{WEEK_START}/change-part",
                         json={"entry_id": entry_id, "role": "protein", "choice": "Ground beef"})
    assert res.status_code == 200
    body = res.json()
    assert body["status"] == "changed" and body["day"]["dinner"]["title"] == "Beef burgers"
    # The swap's undo route puts it back — the card's Undo is that call.
    res = signed_in.post(f"/api/week/{WEEK_START}/swap-undo", json={"entry_id": body["entry_id"]})
    assert res.status_code == 200 and res.json()["meal"] == "Turkey burgers"
    assert signed_in.get(f"/api/week/{WEEK_START}/part-options?entry_id=99999").status_code == 404


# ---------- the shell (source) ----------

def test_the_card_draws_the_plate_outside_its_body_button():
    i = SHELL_JS.index("  function daySlotCardHtml(day, slot) {")
    body = SHELL_JS[i:i + 3000]
    # The parts are buttons, so they follow the body button, never sit in it.
    assert "var plate = typeof plateRowHtml === 'function' ? plateRowHtml(day, slot, entry) : '';" in body
    assert body.index("</button>") < body.index("plate +")
    assert "(plate ? '' : chipsRowHtml(plateChips(entry)))" in body


def test_the_parts_are_chips_with_a_dashed_missing_one_and_rows_on_the_meal_step():
    assert 'class="plate-part is-missing"' in SHELL_JS
    assert "'Add a ' + escapeHtml(part.word.toLowerCase())" in SHELL_JS
    assert "data-plate-side=" in SHELL_JS
    assert "function platePartsRowsHtml(" in SHELL_JS
    assert "platePartsRowsHtml(day, slot, entry) : '') +" in SHELL_JS
    assert "(p.missing || p.empty) ? 'Add' : 'Change'" in SHELL_JS
    # A reheat night or a grab-and-go snack has no plate to change.
    j = SHELL_JS.index("function plateCanChange(")
    guard = SHELL_JS[j:j + 500]
    assert "entry.source === 'leftovers'" in guard and "isSnackSlot(slot) && !isRealCook(entry)" in guard
    for cls in (".plate-part.is-missing", ".plate-rows", ".plate-row-change", ".wk-add-save", ".wk-add-option.is-on"):
        assert cls in SHELL_CSS, cls


def test_a_blank_part_reads_as_in_the_dish_on_both_the_chip_and_the_row():
    # Bug, Emily 2026-09-15: Tuesday's Roast Chicken card showed chips
    # "Protein ⌄" (no value) while the detail page said "PROTEIN · In the
    # dish" — a blank-looking chip reads as broken. The two renderers now
    # share one fallback string (PLATE_NO_NAME) instead of disagreeing.
    assert "var PLATE_NO_NAME = 'In the dish';" in SHELL_JS
    i = SHELL_JS.index("  function platePartChipHtml(part, slot) {")
    chip = SHELL_JS[i:SHELL_JS.index("  function plateRowHtml(")]
    assert "escapeHtml(part.name || PLATE_NO_NAME)" in chip
    # The dashed "missing" chip (a part the rule wants and nothing covers)
    # is untouched — this fallback is only for a part the dish HAS but
    # never named.
    assert "is-missing" in chip and 'Add a ' in chip
    j = SHELL_JS.index("  function platePartsRowsHtml(")
    rows = SHELL_JS[j:j + 1200]
    assert "p.missing ? 'Nothing yet' : (p.name || PLATE_NO_NAME)" in rows


def test_a_dish_sourced_veg_or_carb_routes_through_change_not_add():
    """
    Emily, 2026-09-22: her "Change" on the Cajun salmon plate's veg ADDED
    steamed broccoli instead of replacing the dish's own green beans —
    because the client only ever routed 'protein' through the recipe-
    rewrite flow; a named-but-not-a-side vegetable/carb fell through to
    the plain "Add something" sheet, which can only add. `data-plate-
    source` tells the sheet which part it's looking at; 'dish' takes the
    same door the protein always has (mode 'change' — part-options,
    change-part), 'side' still takes the add-then-remove door.
    """
    i = SHELL_JS.index("  function platePartChipHtml(part, slot) {")
    chip = SHELL_JS[i:SHELL_JS.index("  function plateRowHtml(")]
    assert "data-plate-source=" in chip
    j = SHELL_JS.index("  async function openMealAddSheet(")
    sheet = SHELL_JS[j:SHELL_JS.index("  function drawMealAddRows(")]
    assert "var mode = (role === 'protein' || source === 'dish') ? 'change' : 'add';" in sheet
    assert "'&role=' + encodeURIComponent(role || 'protein')" in sheet


def test_the_protein_sheet_says_so_when_it_has_nothing_to_offer():
    # Whether the AI call failed or genuinely found nothing, the sheet
    # still works — the free-text box is always there — so one calm line
    # above it says so rather than leaving a bare, unexplained empty list.
    i = SHELL_JS.index("  function mealAddRowsHtml(offer, st) {")
    sheet = SHELL_JS[i:SHELL_JS.index("  var MEAL_ADD_TROUBLE")]
    assert "st.mode === 'change' && !options.length" in sheet
    assert "I couldn’t think of options just now — type one, or leave it." in sheet
    assert sheet.index("wk-add-empty-note") < sheet.index('id="wk-add-free"')
    assert ".wk-add-empty-note" in SHELL_CSS


def test_the_sheet_selects_then_saves_and_the_protein_goes_through_the_swaps_undo():
    i = SHELL_JS.index("  function mealAddRowsHtml(offer, st) {")
    sheet = SHELL_JS[i:SHELL_JS.index("  var MEAL_ADD_TROUBLE")]
    assert 'id="wk-add-save"' in sheet and "(st.selected ? '' : ' disabled')" in sheet
    assert "'Leave it as it is'" in sheet and "'No ' + (st.roleWord" in sheet and "'Take ' + st.side.toLowerCase() + ' off'" in sheet
    assert "/part-options?entry_id=" in sheet and "'Change the ' + (mealAddState.roleWord" in sheet
    assert "st.selected = { index: i, option: st.offer.options[i] };" in sheet
    k = SHELL_JS.index("  async function runMealChangePart(choice) {")
    change = SHELL_JS[k:k + 3200]
    assert "/change-part'" in change
    assert "toastSaved(savedLine(choice, 'swapped in')," in change
    assert "{ label: 'Undo', onClick: function () { if (day) runSwapUndo(panel, day, slot); } }, SWAP_UNDO_MS);" in change
    assert "reason: out.reason || ''" in change
    # The add's confirmation is S10's too: the card's line carries when it
    # starts, the pop-up names what went on ("Roasted potatoes was added").
    m = SHELL_JS.index("  async function runMealAdd(pick) {")
    add = SHELL_JS[m:m + 3000]
    assert "swapState = { date: st.date, slot: st.slot, avoid: [], message: said };" in add
    assert "toastSaved(savedLine(out.name, 'added'), {" in add


# ---------- after the verifier (round 2) ----------

def test_a_variant_that_keeps_the_dishes_name_is_saved_under_a_name_that_says_what_changed(week):
    # "Chili" rewritten with turkey: the model keeps the name (the protein
    # isn't in it), and a recipe is only saved when its name is new — so
    # without this the old beef Chili would be planned again and reported
    # as a change. Reproduced by the branch's verifier, 2026-09-13.
    entry_id = _dinner(week, DAY1)["entry_id"]
    out = pp.change_part(
        week, entry_id, "protein", "Ground turkey",
        asker=lambda c: _variant("Chili", "turkey",
                                 ingredients=[{"item": "Ground turkey", "qty": "1 lb", "category": "meat/seafood"}],
                                 reason="Turkey instead of beef."),
    )
    assert out["status"] == "changed"
    assert out["meal"] == "Chili with ground turkey"
    saved = tools.get_recipe("Chili with ground turkey")
    assert saved["main_protein"] == "turkey"
    assert any(i["item"] == "Ground turkey" for i in saved["ingredients"])
    assert tools.get_recipe("Chili")["main_protein"] == "beef"  # untouched
    assert _dinner(week, DAY1)["plate_parts"][0]["name"] == "Turkey"
    # A proposed name that is another saved recipe's is renamed the same way.
    entry_id = _dinner(week, DAY2)["entry_id"]
    out = pp.change_part(week, entry_id, "protein", "Ground beef", asker=lambda c: _variant("Chili", "beef"))
    assert out["meal"] == "Chili with ground beef"


def test_changing_the_protein_keeps_the_sides_and_so_does_undo(week):
    entry_id = _dinner(week, DAY2)["entry_id"]
    tools.add_component(entry_id, key="roasted-potatoes")
    dinner = _dinner(week, DAY2)
    assert [s["name"] for s in dinner["sides"]] == ["Roasted potatoes"]
    assert [p for p in dinner["plate_parts"] if p["role"] == "carb"][0]["name"] == "Roasted potatoes"
    out = pp.change_part(week, dinner["entry_id"], "protein", "Ground beef", asker=lambda c: _variant())
    after = _dinner(week, DAY2)
    assert after["title"] == "Beef burgers"
    assert [s["name"] for s in after["sides"]] == ["Roasted potatoes"]
    assert [p for p in after["plate_parts"] if p["role"] == "carb"][0]["name"] == "Roasted potatoes"
    # ...and back.
    tools.undo_meal_swap(week, out["entry_id"])
    back = _dinner(week, DAY2)
    assert back["title"] == "Turkey burgers"
    assert [s["name"] for s in back["sides"]] == ["Roasted potatoes"]


def test_a_plain_swap_still_leaves_the_sides_behind(week):
    # "Swap · I'll pick" is a different dish: the potatoes went with the
    # chops, not with the night. Unchanged.
    entry_id = _dinner(week, DAY2)["entry_id"]
    tools.add_component(entry_id, key="roasted-potatoes")
    out = tools.swap_meal_in_place(week, entry_id, picker=lambda c: dict(_variant("Lemon chicken", "chicken"), is_new_recipe=True))
    assert out["status"] == "swapped"
    assert _dinner(week, DAY2)["sides"] == []


def test_the_cut_is_offered_and_only_the_same_protein_is_not(week):
    assert [o["name"] for o in pp._clean_options(
        [{"name": "Chicken thighs"}, {"name": "Chicken breasts"}, {"name": "Chicken"}, {"name": "Ground chicken"}, {"name": "Pork loin"}],
        "chicken",
    )] == ["Chicken thighs", "Chicken breasts", "Pork loin"]


def test_a_side_with_no_role_still_shows_on_the_card():
    parts = pp.parts_of_plate("dinner", ["protein", "vegetable", "carb"], "chicken",
                              [{"name": "Garlic yogurt sauce", "covers": []}], "")
    assert parts[-1] == {"role": "side", "word": "Side", "name": "Garlic yogurt sauce", "source": "side", "missing": False}
    # ...as a plain tap into the sheet (no role), with "Take it off" there.
    assert "data-plate-part=\"' + escapeHtml(part.role === 'side' ? '' : part.role)" in SHELL_JS


def test_the_sheet_ignores_a_fetch_from_an_earlier_open_and_undo_of_a_changed_side_puts_the_old_one_back():
    i = SHELL_JS.index("  async function openMealAddSheet(")
    body = SHELL_JS[i:SHELL_JS.index("  function drawMealAddRows(")]
    assert "var thisOpen = mealAddState;" in body and "if (mealAddState !== thisOpen || !rows) return;" in body
    j = SHELL_JS.index("  async function runMealAddUndo(panel, st, name, replaced) {")
    undo = SHELL_JS[j:j + 1500]
    assert "if (replaced) {" in undo and "text: replaced" in undo


def test_a_typed_name_that_is_the_catalogues_uses_the_catalogue(week):
    # The undo of "Change the carb" puts the old side back by name; the
    # catalogue's roasted potatoes come back, amounts and step and all —
    # never a bare line with no amount (verifier, round 2).
    entry_id = _dinner(week, DAY2)["entry_id"]
    out = tools.add_component(entry_id, text="roasted Potatoes", side_generator=lambda ctx: (_ for _ in ()).throw(AssertionError("asked the model")))
    assert out["status"] == "added"
    side = out["side"]
    assert side["name"] == "Roasted potatoes" and side["covers"] == ["carb"]
    assert side["minutes"] and side["instructions"] and side["ingredients"][0]["qty"]
    assert [p for p in _dinner(week, DAY2)["plate_parts"] if p["role"] == "carb"][0]["name"] == "Roasted potatoes"
