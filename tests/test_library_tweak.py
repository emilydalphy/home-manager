"""
Library first, then a light tweak (Emily, 2026-11-04). A library recipe is
copied as filed unless it doesn't suit a child at the table or the kit the
household listed; then a small model edit changes only what needs changing.
"""
import pytest

from app import agent, households, tools
from app.tools import library_tweak as lt
from app.tools import recipe_library as lib

HOT = {
    "dish_name": "Red Lentil Curry",
    "ingredients": [{"item": "Red lentils", "qty": "1 lb", "category": "pantry"},
                    {"item": "Cayenne pepper", "qty": "1 tsp", "category": "spices"},
                    {"item": "Onion", "qty": "1", "category": "produce"}],
    "instructions": ["Cook the onion with the 1 tsp cayenne.", "Add the lentils and simmer."],
    "adapted_for": [],
}
EDIT = {
    "step_edits": [{"step": 1, "text": "Cook the onion. Adults add the 1 tsp cayenne at the table."}],
    "household_changes": ["Cayenne goes on at the table, not in the pot."],
}
KID = {"children_at_table": True}


def _fit(entry=HOT, shared=KID, edit=EDIT, clash=lambda e: False):
    calls = []

    def write_edit(e, why, s):
        calls.append(why)
        return edit

    return lt.fit(entry, entry["dish_name"], [], shared, clash=clash, write_edit=write_edit), calls


@pytest.mark.parametrize("item", ["Cayenne pepper", "Red chili flakes", "Jalapeño", "Sriracha", "Gochujang",
                                  "Thai bird chilies", "Hot sauce", "Chipotle in adobo", "Berbere", "Aleppo pepper",
                                  "Kimchi", "Red chilli", "Massaman curry paste", "Frank's RedHot"])
def test_a_hot_ingredient_is_a_reason_when_a_child_eats(item):
    entry = {**HOT, "ingredients": [{"item": item, "qty": "1"}]}
    assert lt.reasons(entry, KID) == {"heat_for_a_child": [item]}


@pytest.mark.parametrize("item", ["Sweet chili sauce", "Serrano ham", "Bell pepper", "Black pepper", "Paprika",
                                  "Mild chili powder"])
def test_a_mild_ingredient_is_no_reason(item):
    assert lt.reasons({**HOT, "ingredients": [{"item": item, "qty": "1"}]}, KID) == {}


def test_no_child_no_kit_is_no_reason_and_no_call():
    out, calls = _fit(shared={})
    assert calls == [] and out["entry"] is HOT and out["changes"] == []


def test_a_variant_already_made_for_children_needs_no_tweak():
    assert lt.reasons({**HOT, "adapted_for": ["children"]}, KID) == {}


def _steps(text):
    return {**HOT, "instructions": [text]}


def test_equipment_missing_from_a_listed_kit_is_a_reason():
    entry = _steps("Blend it in a blender, then pour into the slow cooker.")
    assert lt.reasons(entry, {"kitchen_kit": ["slow_cooker"]}) == {"equipment_they_lack": ["blender"]}


@pytest.mark.parametrize("kit, step", [
    ([], "Put it in the slow cooker."),                       # nothing listed: unknown
    (["slow_cooker"], "Put it in the slow cooker."),          # a stored key
    (["Slow cooker"], "Put it in the Crock-Pot."),            # a label, another word for it
    (["instant_pot"], "Use a pressure cooker."),
    (["blender"], "Use an immersion blender."),
    (["slow_cooker"], "Grilled onions, in a hot pan."),       # a word in a dish, not a machine
])
def test_equipment_they_own_or_nothing_listed_is_no_reason(kit, step):
    assert lt.reasons(_steps(step), {"kitchen_kit": kit}) == {}


def test_only_the_named_steps_change_and_the_list_stays():
    out, _ = _fit()
    e = out["entry"]
    assert e["instructions"] == [EDIT["step_edits"][0]["text"], HOT["instructions"][1]]
    assert e["ingredients"] == HOT["ingredients"], "the shopping list moved"
    assert out["changes"] == EDIT["household_changes"]
    assert HOT["instructions"][0].startswith("Cook the onion with"), "the library's own entry was changed"


@pytest.mark.parametrize("bad", [
    None, {}, {"step_edits": [], "household_changes": ["x"]},
    {**EDIT, "step_edits": [{"step": 9, "text": "no such step"}]},
    {**EDIT, "step_edits": [{"step": 1, "text": "  "}]},
    {**EDIT, "step_edits": [{"step": "one", "text": "x"}]},
    {**EDIT, "step_edits": ["not a dict"]},
    {**EDIT, "household_changes": []},
    # Nothing says where the heat went.
    {**EDIT, "step_edits": [{"step": 1, "text": "Cook the onion with the cayenne."}]},
])
def test_an_unusable_edit_leaves_the_recipe_as_filed(bad):
    out, _ = _fit(edit=bad)
    assert out["entry"] is HOT and out["changes"] == [] and not out["why"]


def test_an_edit_that_leaves_heat_in_another_step_is_refused():
    entry = {**HOT, "instructions": ["Fry the onion with cayenne.", "Stir in more cayenne, then the lentils."]}
    edit = {**EDIT, "step_edits": [{"step": 1, "text": "Fry the onion. Adults add cayenne at the table."}]}
    out, _ = _fit(entry=entry, edit=edit)
    assert out["entry"] is entry


def test_an_edit_that_still_needs_the_machine_is_refused():
    entry = _steps("Blend the soup in a blender.")
    shared = {"kitchen_kit": ["slow_cooker"]}
    still = {"step_edits": [{"step": 1, "text": "Use a blender or mash it."}], "household_changes": ["Mashed."]}
    assert _fit(entry=entry, shared=shared, edit=still)[0]["entry"] is entry
    done = {"step_edits": [{"step": 1, "text": "Mash the soup in the pot."}], "household_changes": ["Mashed."]}
    assert _fit(entry=entry, shared=shared, edit=done)[0]["entry"]["instructions"] == ["Mash the soup in the pot."]


def test_a_food_only_a_changed_step_names_is_a_clash():
    """Review: the gate reads a list, so "adults add crushed peanuts" passed."""
    from app.tools import allergen_gate as gate
    from app.tools import library_first as lf
    tools.add_member("Sam")
    tools.set_member_dietary_restrictions("Sam", ["peanuts"])
    avoid = gate.hard_avoidances()
    assert avoid
    assert not lf.entry_clashes(HOT, avoid), "the fixture itself clashes"
    edit = {**EDIT, "step_edits": [{"step": 1, "text": "Adults add crushed peanuts and cayenne at the table."}]}
    out, _ = _fit(edit=edit, clash=lambda e: lf.entry_clashes(e, avoid))
    assert out["entry"] is HOT


def test_kit_answers_that_name_no_machine_say_nothing():
    for kit in (["no_dishwasher"], ["cast_iron"], ["no_dishwasher", "cast_iron"]):
        assert lt.reasons(_steps("Use a blender."), {"kitchen_kit": kit}) == {}


def test_a_food_processor_is_no_reason_because_the_kit_question_never_offers_it():
    assert lt.reasons(_steps("Use a food processor."), {"kitchen_kit": ["slow_cooker"]}) == {}


def test_a_grill_pan_is_no_reason():
    assert lt.reasons(_steps("Heat a grill pan."), {"kitchen_kit": ["slow_cooker"]}) == {}


def test_an_edit_the_gate_refuses_leaves_the_recipe_as_filed():
    out, _ = _fit(clash=lambda e: e is not HOT)
    assert out["entry"] is HOT and out["changes"] == []


def test_a_failed_call_leaves_the_recipe_as_filed():
    def boom(*a):
        raise RuntimeError("overloaded")
    out = lt.fit(HOT, "x", [], KID, clash=lambda e: False, write_edit=boom)
    assert out["entry"] is HOT


def test_the_switch_turns_it_off(monkeypatch):
    monkeypatch.setenv("LIBRARY_TWEAK", "off")
    out, calls = _fit()
    assert calls == [] and out["entry"] is HOT


def _file_hot():
    return lib.file_entry(
        HOT["dish_name"], ingredients=HOT["ingredients"], instructions=HOT["instructions"],
        default_servings=4, prep_time_minutes=10, cook_time_minutes=20, cuisine="Indian",
        main_protein="vegetarian", origin="seed", meal="dinner",
        sources=[{"name": "Cook A", "title": "Lentils", "url": "https://cook-a.example/l",
                  "rating": 4.8, "rating_count": 500, "lead": True}],
    )


@pytest.fixture
def stub_edit(monkeypatch):
    calls = []

    def _llm(entry, why, shared):
        calls.append(why)
        return EDIT

    monkeypatch.setattr(agent, "tweak_library_recipe_llm", _llm)
    return calls


def _copy():
    name = HOT["dish_name"]
    tools.add_recipe(name=name, ingredients=[], details_pending=True, default_servings=4)
    return agent._copy_from_library({"name": name}, name, name, [], dict(KID))


def test_a_child_gets_the_tweak_saved_says_so_and_it_goes_back_as_a_variant(stub_edit):
    _file_hot()
    copied, _ = _copy()
    assert copied and len(stub_edit) == 1
    mine = tools.get_recipe(HOT["dish_name"])
    assert mine["instructions"][0] == EDIT["step_edits"][0]["text"]
    assert not mine["details_pending"]
    assert tools.household_changes_for(mine["id"]) == EDIT["household_changes"]
    assert all(i.get("library_amount") for i in mine["ingredients"]), "a tweak lost the researched amounts"
    variant = lib.children_variant(HOT["dish_name"], lambda e: False)
    assert variant and variant["origin"] == "tweak" and variant["sources"]
    assert variant["instructions"][0] == EDIT["step_edits"][0]["text"]


def test_the_next_household_with_children_reuses_it_with_no_call(stub_edit):
    _file_hot()
    _copy()
    assert len(stub_edit) == 1
    conn_name = HOT["dish_name"]
    with tools.use_household(households.create_household("Next Door", "next-door-pass")):
        assert _copy()[0]
        mine = tools.get_recipe(conn_name)
        assert mine["instructions"][0] == EDIT["step_edits"][0]["text"]
        assert tools.household_changes_for(mine["id"]) == [lt.REUSED_CHANGE]
    assert len(stub_edit) == 1, "a variant on file was paid for twice"


def test_a_reused_tweak_keeps_the_researched_amounts(stub_edit):
    _file_hot()
    _copy()
    with tools.use_household(households.create_household("Next Door", "next-door-pass")):
        _copy()
        assert all(i.get("library_amount") for i in tools.get_recipe(HOT["dish_name"])["ingredients"])


def test_the_filing_is_the_edit_even_if_another_door_saved_first(stub_edit, monkeypatch):
    """Review: the saved row can be the unedited recipe; a variant filed from
    it would tell every later child household a hot recipe is milder."""
    _file_hot()
    real = tools.fill_recipe_details

    def other_door_first(name, **kw):
        real(name, **{**kw, "instructions": HOT["instructions"], "household_changes": []})
        return real(name, **kw)

    monkeypatch.setattr(tools, "fill_recipe_details", other_door_first)
    _copy()
    assert lib.children_variant(HOT["dish_name"], lambda e: False)["instructions"][0] == EDIT["step_edits"][0]["text"]


def test_a_tweak_of_an_allergy_version_stays_an_allergy_version():
    fitted = {"entry": {**HOT, "adapted_for": ["allergies"], "sources": []},
              "why": {"heat_for_a_child": ["Cayenne pepper"]}, "changes": ["x"]}
    _file_hot()
    lt.file_back(HOT["dish_name"], fitted)
    assert lib.children_variant(HOT["dish_name"], lambda e: False) is None


def test_what_a_household_avoids_is_not_sent_and_only_a_kit_reason_sends_the_kit(monkeypatch):
    seen = {}

    class Block:
        type = "tool_use"
        input = {}

    class Resp:
        content = [Block()]

    def create(client, **kw):
        seen.update(kw)
        return Resp()

    class Client:
        def with_options(self, **kw):
            seen["options"] = kw
            return self

    monkeypatch.setattr(agent, "_client", lambda: Client())
    monkeypatch.setattr(agent, "_create_with_retry", create)
    entry = {**HOT, "ingredients": [{"item": "Gochujang", "qty": "1 tub", "cook_qty": "2 tbsp"}]}
    agent.tweak_library_recipe_llm(entry, {"heat_for_a_child": ["Gochujang"]},
                                   {"must_not_contain": ["sesame"], "kitchen_kit": ["grill"]})
    body = seen["messages"][0]["content"]
    assert "sesame" not in body and "grill" not in body and "2 tbsp" in body and "1 tub" not in body
    assert seen["options"]["max_retries"] == 0 and seen["max_attempts"] == 1
