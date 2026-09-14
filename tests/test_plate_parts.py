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

from app import tools
from app.tools import plate_parts as pp
from app.tools import swap_in_place as sip
from app.tools._shared import use_household


TODAY = datetime.date.today()
WEEK_START = (TODAY - datetime.timedelta(days=TODAY.weekday())).isoformat()
DAYS = [(datetime.date.fromisoformat(WEEK_START) + datetime.timedelta(days=i)).isoformat()
        for i in range(7)]
MONDAY, TUESDAY = DAYS[0], DAYS[1]

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
    tools.plan_meal(TUESDAY, "Turkey burgers", slot="dinner", weekly_plan_id=plan_id, reasoning="quick")
    tools.plan_meal(MONDAY, "Chili", slot="dinner", weekly_plan_id=plan_id, reasoning="batch")
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


def test_a_side_covers_its_part_by_name_and_keto_asks_for_no_carb():
    parts = pp.parts_of_plate("dinner", ["protein"], "chicken", [{"name": "Roasted potatoes", "covers": ["carb"]}], "")
    by_role = {p["role"]: p for p in parts}
    assert by_role["carb"] == {"role": "carb", "word": "Carb", "name": "Roasted potatoes", "source": "side", "missing": False}
    assert by_role["vegetable"]["missing"] is True
    keto = pp.parts_of_plate("dinner", ["protein", "vegetable"], "salmon", [], "keto, low carb")
    assert [p["role"] for p in keto] == ["protein", "vegetable"]
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
    dinner = _dinner(week, TUESDAY)
    assert dinner["main_protein"] == "turkey"
    assert [(p["role"], p["name"], p["missing"]) for p in dinner["plate_parts"]] == [
        ("protein", "Turkey", False), ("vegetable", None, False), ("carb", None, True),
    ]
    assert all(not p["missing"] for p in _dinner(week, MONDAY)["plate_parts"])


# ---------- the options ----------

def test_options_are_asked_for_this_dish_and_cached_for_the_sitting(week):
    entry_id = _dinner(week, TUESDAY)["entry_id"]
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
    # The second open is instant — no second call.
    again = pp.part_options(week, entry_id, asker=lambda ctx: (_ for _ in ()).throw(AssertionError("called twice")))
    assert again["options"] == out["options"]


def test_options_carry_the_houses_exclusions_and_survive_a_failed_call(week):
    tools.set_member_dietary_restrictions("Emily", ["shellfish"])
    tools.add_food_dislikes(["mushrooms"])
    entry_id = _dinner(week, TUESDAY)["entry_id"]
    seen = []

    def asker(context):
        seen.append(context)
        raise RuntimeError("model down")

    out = pp.part_options(week, entry_id, asker=asker)
    assert out["options"] == []  # the sheet still has "Something else…"
    assert "shellfish" in " ".join(seen[0]["must_not_contain"]).lower()
    assert "mushrooms" in seen[0]["dislikes"]


def test_only_the_protein_is_changed_here(week):
    entry_id = _dinner(week, TUESDAY)["entry_id"]
    with pytest.raises(ValueError, match="add_component"):
        pp.part_options(week, entry_id, role="carb", asker=lambda c: [])
    with pytest.raises(ValueError, match="add_component"):
        pp.change_part(week, entry_id, "carb", "Rice", asker=lambda c: {})


# ---------- the change ----------

def test_save_rewrites_the_dish_around_the_pick_through_the_swaps_own_door(week):
    entry_id = _dinner(week, TUESDAY)["entry_id"]
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
    assert _dinner(week, TUESDAY)["plate_parts"][0]["name"] == "Beef"
    # The swap's undo note is there, so the swap's undo puts turkey back.
    entry = sip._entry(week, out["entry_id"])
    assert entry["derived_from"]["swapped_from"]["meal"] == "Turkey burgers"
    undone = tools.undo_meal_swap(week, out["entry_id"])
    assert undone["meal"] == "Turkey burgers"
    assert _dinner(week, TUESDAY)["title"] == "Turkey burgers"


def test_a_pick_the_house_cannot_have_is_refused_and_nothing_is_written(week):
    tools.set_member_dietary_restrictions("Emily", ["shellfish"])
    entry_id = _dinner(week, TUESDAY)["entry_id"]
    out = pp.change_part(
        week, entry_id, "protein", "Shrimp",
        asker=lambda c: _variant("Shrimp burgers", "shrimp",
                                 ingredients=[{"item": "Shrimp", "qty": "1 lb", "category": "meat/seafood"}]),
    )
    assert out["status"] == "refused"
    assert "Shrimp" in out["message"] and "as it was" in out["message"]
    assert _dinner(week, TUESDAY)["title"] == "Turkey burgers"
    assert not any(r["name"] == "Shrimp burgers" for r in tools.list_recipes())


def test_a_model_that_comes_back_with_nothing_is_refused_not_guessed(week):
    entry_id = _dinner(week, TUESDAY)["entry_id"]
    out = pp.change_part(week, entry_id, "protein", "Lamb", asker=lambda c: {})
    assert out["status"] == "refused" and out["message"] == pp.REFUSAL
    assert _dinner(week, TUESDAY)["title"] == "Turkey burgers"


def test_a_change_forgets_the_cached_options_for_that_slot(week):
    entry_id = _dinner(week, TUESDAY)["entry_id"]
    pp.part_options(week, entry_id, asker=lambda c: [{"name": "Ground beef"}])
    assert (tools.household_id(), entry_id) in pp._OPTIONS_CACHE
    pp.change_part(week, entry_id, "protein", "Ground beef", asker=lambda c: _variant())
    assert (tools.household_id(), entry_id) not in pp._OPTIONS_CACHE


def test_another_households_meal_is_not_changeable(week):
    entry_id = _dinner(week, TUESDAY)["entry_id"]
    with use_household(2):
        with pytest.raises(ValueError, match="No meal"):
            pp.change_part(week, entry_id, "protein", "Ground beef", asker=lambda c: _variant())
    assert _dinner(week, TUESDAY)["title"] == "Turkey burgers"


# ---------- the routes ----------

def test_the_routes_offer_and_change(signed_in, week, monkeypatch):
    entry_id = _dinner(week, TUESDAY)["entry_id"]
    monkeypatch.setattr(pp, "_ask_options", lambda ctx: [{"name": "Ground beef", "note": ""}])
    monkeypatch.setattr(pp, "_ask_variant", lambda ctx: _variant())
    res = signed_in.get(f"/api/week/{WEEK_START}/part-options?entry_id={entry_id}&role=protein")
    assert res.status_code == 200
    assert res.json()["options"] == [{"name": "Ground beef", "note": ""}]
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
    assert "(p.missing ? 'Add' : 'Change')" in SHELL_JS
    # A reheat night or a grab-and-go snack has no plate to change.
    j = SHELL_JS.index("function plateCanChange(")
    guard = SHELL_JS[j:j + 500]
    assert "entry.source === 'leftovers'" in guard and "isSnackSlot(slot) && !isRealCook(entry)" in guard
    for cls in (".plate-part.is-missing", ".plate-rows", ".plate-row-change", ".wk-add-save", ".wk-add-option.is-on"):
        assert cls in SHELL_CSS, cls


def test_the_sheet_selects_then_saves_and_the_protein_goes_through_the_swaps_undo():
    i = SHELL_JS.index("  function mealAddRowsHtml(offer, st) {")
    sheet = SHELL_JS[i:SHELL_JS.index("  var MEAL_ADD_TROUBLE")]
    assert 'id="wk-add-save"' in sheet and "(st.selected ? '' : ' disabled')" in sheet
    assert "'Leave it as it is'" in sheet and "'No ' + (st.roleWord" in sheet and "'Take ' + st.side.toLowerCase() + ' off'" in sheet
    assert "/part-options?entry_id=" in sheet and "'Change the protein'" in sheet
    assert "st.selected = { index: i, option: st.offer.options[i] };" in sheet
    k = SHELL_JS.index("  async function runMealChangePart(choice) {")
    change = SHELL_JS[k:k + 3200]
    assert "/change-part'" in change
    assert "toastSaved({ label: 'Undo', onClick: function () { if (day) runSwapUndo(panel, day, slot); } }, SWAP_UNDO_MS);" in change
    assert "reason: out.reason || ''" in change
    # The add's confirmation is S10's too: the card's line carries the
    # fact, the pop-up says it saved.
    m = SHELL_JS.index("  async function runMealAdd(pick) {")
    add = SHELL_JS[m:m + 3000]
    assert "swapState = { date: st.date, slot: st.slot, avoid: [], message: said };" in add
    assert "toastSaved({" in add


# ---------- after the verifier (round 2) ----------

def test_a_variant_that_keeps_the_dishes_name_is_saved_under_a_name_that_says_what_changed(week):
    # "Chili" rewritten with turkey: the model keeps the name (the protein
    # isn't in it), and a recipe is only saved when its name is new — so
    # without this the old beef Chili would be planned again and reported
    # as a change. Reproduced by the branch's verifier, 2026-09-13.
    entry_id = _dinner(week, MONDAY)["entry_id"]
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
    assert _dinner(week, MONDAY)["plate_parts"][0]["name"] == "Turkey"
    # A proposed name that is another saved recipe's is renamed the same way.
    entry_id = _dinner(week, TUESDAY)["entry_id"]
    out = pp.change_part(week, entry_id, "protein", "Ground beef", asker=lambda c: _variant("Chili", "beef"))
    assert out["meal"] == "Chili with ground beef"


def test_changing_the_protein_keeps_the_sides_and_so_does_undo(week):
    entry_id = _dinner(week, TUESDAY)["entry_id"]
    tools.add_component(entry_id, key="roasted-potatoes")
    dinner = _dinner(week, TUESDAY)
    assert [s["name"] for s in dinner["sides"]] == ["Roasted potatoes"]
    assert [p for p in dinner["plate_parts"] if p["role"] == "carb"][0]["name"] == "Roasted potatoes"
    out = pp.change_part(week, dinner["entry_id"], "protein", "Ground beef", asker=lambda c: _variant())
    after = _dinner(week, TUESDAY)
    assert after["title"] == "Beef burgers"
    assert [s["name"] for s in after["sides"]] == ["Roasted potatoes"]
    assert [p for p in after["plate_parts"] if p["role"] == "carb"][0]["name"] == "Roasted potatoes"
    # ...and back.
    tools.undo_meal_swap(week, out["entry_id"])
    back = _dinner(week, TUESDAY)
    assert back["title"] == "Turkey burgers"
    assert [s["name"] for s in back["sides"]] == ["Roasted potatoes"]


def test_a_plain_swap_still_leaves_the_sides_behind(week):
    # "Swap · I'll pick" is a different dish: the potatoes went with the
    # chops, not with the night. Unchanged.
    entry_id = _dinner(week, TUESDAY)["entry_id"]
    tools.add_component(entry_id, key="roasted-potatoes")
    out = tools.swap_meal_in_place(week, entry_id, picker=lambda c: dict(_variant("Lemon chicken", "chicken"), is_new_recipe=True))
    assert out["status"] == "swapped"
    assert _dinner(week, TUESDAY)["sides"] == []


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
    entry_id = _dinner(week, TUESDAY)["entry_id"]
    out = tools.add_component(entry_id, text="roasted Potatoes", side_generator=lambda ctx: (_ for _ in ()).throw(AssertionError("asked the model")))
    assert out["status"] == "added"
    side = out["side"]
    assert side["name"] == "Roasted potatoes" and side["covers"] == ["carb"]
    assert side["minutes"] and side["instructions"] and side["ingredients"][0]["qty"]
    assert [p for p in _dinner(week, TUESDAY)["plate_parts"] if p["role"] == "carb"][0]["name"] == "Roasted potatoes"
