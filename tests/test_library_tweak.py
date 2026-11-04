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
    "step_edits": [{"step": 1, "text": "Cook the onion. Keep the cayenne for the table."}],
    "change_ingredients": [{"item": "Cayenne pepper", "qty": "1/2 tsp"}],
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
                                  "Thai bird chilies", "Hot sauce", "Chipotle in adobo"])
def test_a_hot_ingredient_is_a_reason_when_a_child_eats(item):
    entry = {**HOT, "ingredients": [{"item": item, "qty": "1"}]}
    assert lt.reasons(entry, KID) == {"heat_for_a_child": [item]}


@pytest.mark.parametrize("item", ["Sweet chili sauce", "Serrano ham", "Bell pepper", "Black pepper", "Paprika"])
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
    entry = _steps("Blend it in a food processor, then pour into the slow cooker.")
    assert lt.reasons(entry, {"kitchen_kit": ["slow_cooker"]}) == {"equipment_they_lack": ["food processor"]}


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


def test_only_what_the_edit_names_changes():
    out, _ = _fit()
    e = out["entry"]
    assert e["instructions"] == ["Cook the onion. Keep the cayenne for the table.", HOT["instructions"][1]]
    assert [i["qty"] for i in e["ingredients"]] == ["1 lb", "1/2 tsp", "1"]
    assert out["changes"] == EDIT["household_changes"]
    assert out["touched"] == {"cayenne pepper"}
    assert HOT["ingredients"][1]["qty"] == "1 tsp", "the library's own entry was changed"


@pytest.mark.parametrize("bad", [
    None, {}, {"step_edits": [], "household_changes": ["x"]},
    {**EDIT, "step_edits": [{"step": 9, "text": "no such step"}]},
    {**EDIT, "step_edits": [{"step": 1, "text": "  "}]},
    {**EDIT, "step_edits": [{"step": "one", "text": "x"}]},
    {**EDIT, "change_ingredients": [{"item": "Saffron", "qty": "1"}]},
    {**EDIT, "remove_ingredients": ["Saffron"]},
    {**EDIT, "household_changes": []},
])
def test_an_unusable_edit_leaves_the_recipe_as_filed(bad):
    out, _ = _fit(edit=bad)
    assert out["entry"] is HOT and out["changes"] == [] and not out["why"]


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
    assert [i for i in mine["ingredients"] if i["item"] == "Cayenne pepper"][0].get("library_amount") is None
    assert [i for i in mine["ingredients"] if i["item"] == "Red lentils"][0].get("library_amount") is True
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
