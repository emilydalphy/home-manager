"""
Shared Recipe Library, step 1 (Loop Board, Emily 2026-10-18): a dish
researched once serves every household, and lands in their cookbook when
they use it.

Pinned against the card's acceptance:
  * two households planning the same dish: research runs (and is billed)
    once; the second gets the recipe copied with no research and no writer
    call, sources and all;
  * each sees it in its own cookbook, and changing one copy leaves the
    other copy and the library alone;
  * a typed or link-imported recipe never reaches the library;
  * the allergen gate still runs per household: a library dish with peanuts
    is never copied for a household that can't have them;
  * nothing personal is in the library; a tweak is a variant, never the base;
  * the morning report counts library hits against new research.
Every model call is stubbed; nothing reaches the network.
"""
from __future__ import annotations

import json
import threading
import time

import pytest
from fastapi.testclient import TestClient

from conftest import household_date

from app import agent, households, tools
from app.db import get_conn
from app.main import app
from app.tools import recipe_library as lib
from app.tools import recipe_research as rr

B_PASS = "library-household-b-pass"
SHARED = {"serves": 4, "must_not_contain": []}
A_URL, C_URL = "https://cook-a.example/satay", "https://cook-c.example/satay"


@pytest.fixture(autouse=True)
def _research_on(monkeypatch):
    monkeypatch.setenv("RECIPE_RESEARCH", "on")
    monkeypatch.setattr(rr, "start_link_check", lambda research: False)


@pytest.fixture
def b():
    return households.create_household("The B Household", B_PASS)


@pytest.fixture
def research_calls(monkeypatch):
    calls = []

    def _llm(name, *a, **k):
        calls.append(name)
        return {
            "sources": [
                {"name": "Cook A", "title": "Grilled Chicken Skewers", "url": A_URL, "rating": 4.8,
                 "rating_count": 900, "read": True},
                {"name": "Cook C", "title": "Satay", "url": C_URL, "rating": 4.5,
                 "rating_count": 60, "read": True},
            ],
            "method_notes": "Marinate in coconut and turmeric; peanut sauce on the side.",
            "used": {"web_search": 1, "web_fetch": 2}, "input_tokens": 9000,
        }

    monkeypatch.setattr(agent, "research_dish_llm", _llm)
    return calls


def _satay(spec):
    return {
        "ingredients": [
            {"item": "Chicken thighs", "qty": "2 lb", "category": "meat"},
            {"item": "Coconut milk", "qty": "1 can", "category": "pantry"},
            {"item": "Peanut butter", "qty": "1 jar", "category": "pantry"},
            {"item": "Turmeric", "qty": "1 jar", "category": "spices"},
            {"item": "Limes", "qty": "2", "category": "produce"},
        ],
        "instructions": ["Marinate the chicken in coconut milk and turmeric.",
                         "Grill on skewers; stir peanut butter into a sauce and squeeze in the limes."],
        "default_servings": 4, "prep_time_minutes": 20, "cook_time_minutes": 15,
    }


@pytest.fixture
def writer_calls(monkeypatch):
    calls = []

    def _writer(spec):
        calls.append(spec)
        return _satay(spec)

    monkeypatch.setattr(agent, "generate_recipe_details_llm", _writer)
    return calls


def _plan_pending(name="Grilled Chicken Skewers"):
    tools.add_recipe(name=name, ingredients=[], details_pending=True,
                     dish_note="grilled skewers with peanut sauce", cuisine="Thai")
    return {**tools.get_recipe(name), "slot": "dinner", "cook_time_minutes": 15}


def _write(recipe):
    # The household's real writer context — must_not_contain, eating style,
    # kit, children — exactly as the recipe pass builds it.
    return agent._write_one_pending_recipe(recipe, "dinner", agent._shared_recipe_details_context(),
                                           agent._allergen_gate.hard_avoidances())


def _rows(sql, args=()):
    conn = get_conn()
    try:
        return [dict(r) for r in conn.execute(sql, args).fetchall()]
    finally:
        conn.close()


def test_two_households_one_research_and_the_second_gets_it_copied(b, research_calls, writer_calls):
    a_recipe = _plan_pending()
    assert _write(a_recipe)["ok"]
    assert research_calls == ["Grilled Chicken Skewers"] and len(writer_calls) == 1

    with tools.use_household(b):
        b_recipe = _plan_pending()
        out = _write(b_recipe)
        assert out["ok"]
        mine = tools.get_recipe("Grilled Chicken Skewers")
        b_page = rr.recipe_research_for(mine, check_links=False)
    assert research_calls == ["Grilled Chicken Skewers"], "the second household paid for research again"
    assert len(writer_calls) == 1, "the second household waited on a writer"
    assert [i["item"] for i in mine["ingredients"]][:2] == ["Chicken thighs", "Coconut milk"]
    assert mine["id"] != a_recipe["id"] and not mine["details_pending"]
    # Sources exactly as researched, no fresh lookup.
    assert b_page["credit"] == "Based on Cook A, checked against Cook C"
    assert [s["rating_count"] for s in b_page["sources"]] == [900, 60]
    # Both cookbooks link the one base.
    base = lib.base_recipe("Grilled Chicken Skewers")
    links = _rows("SELECT household_id, library_id FROM recipes WHERE name = 'Grilled Chicken Skewers' ORDER BY household_id")
    assert {r["library_id"] for r in links} == {base["id"]} and len(links) == 2
    # The report's counts: A researched new, B was a library hit.
    assert lib.use_counts(get_conn(), 1, "-1 days") == {"recipe": 0, "research": 0, "new": 1}
    assert lib.use_counts(get_conn(), b, "-1 days") == {"recipe": 1, "research": 0, "new": 0}
    with tools.use_household(b):
        assert tools.get_usage_summary(days=7)["recipe_library"]["recipe"] == 1


def test_changing_one_copy_leaves_the_other_and_the_library_alone(b, research_calls, writer_calls):
    _write(_plan_pending())
    with tools.use_household(b):
        _write(_plan_pending())
        tools.mark_recipe_feedback("Grilled Chicken Skewers", rating="disliked", notes="B thought it was dry")
        tools.flag_recipe_temporary("Grilled Chicken Skewers", excluded=True)
        conn = get_conn()
        conn.execute("UPDATE recipes SET default_servings = 9, ingredients_json = ? WHERE household_id = ? "
                     "AND name = 'Grilled Chicken Skewers'", (json.dumps([{"item": "Tofu", "qty": "1 block"}]), b))
        conn.commit()
        conn.close()
    a = tools.get_recipe("Grilled Chicken Skewers")
    assert a["rating"] is None and not a["temporarily_excluded"] and a["default_servings"] == 4
    assert "Tofu" not in json.dumps(a["ingredients"])
    base = lib.base_recipe("Grilled Chicken Skewers")
    assert base["default_servings"] == 4 and "Tofu" not in json.dumps(base["ingredients"])
    assert "dry" not in json.dumps(_rows("SELECT * FROM recipe_library"))


def test_a_library_dish_with_peanuts_is_never_copied_for_a_peanut_allergic_household(
        b, research_calls, writer_calls):
    _write(_plan_pending())
    with tools.use_household(b):
        tools.add_member("Ravi")
        tools.set_member_dietary_restrictions("Ravi", ["peanuts"])
        out = _write(_plan_pending())
        mine = tools.get_recipe("Grilled Chicken Skewers")
    # The writer ran for B (and was told), wrote peanuts again (the stub),
    # and the gate held it — never copied from the library.
    assert len(writer_calls) == 3, "B's dish was written (twice, the gate retrying), not copied"
    assert out["ok"] is False and out["clash"]
    assert mine["details_pending"] and mine["ingredients"] == []
    assert research_calls == ["Grilled Chicken Skewers"], "the library's research served B; nothing new was billed"
    assert writer_calls[1]["research"]["lead"]["name"] == "Cook A"


def test_the_first_household_writing_around_an_allergy_files_no_base(
        b, research_calls, writer_calls, monkeypatch):
    """Review, 2026-10-19: a peanut-free version is not everyone's base."""
    def _no_peanut(spec):
        writer_calls.append(spec)
        out = _satay(spec)
        out["ingredients"] = [i for i in out["ingredients"] if i["item"] != "Peanut butter"] + [
            {"item": "Sunflower seed butter", "qty": "1 jar", "category": "pantry"}]
        out["instructions"] = ["Marinate the chicken in coconut milk and turmeric.",
                               "Grill on skewers; stir sunflower seed butter into a sauce and squeeze in the limes."]
        return out

    monkeypatch.setattr(agent, "generate_recipe_details_llm", _no_peanut)
    with tools.use_household(b):
        tools.add_member("Ravi")
        tools.set_member_dietary_restrictions("Ravi", ["peanuts"])
        assert _write(_plan_pending())["ok"]
    assert lib.base_recipe("Grilled Chicken Skewers") is None, "a recipe written around an allergy became the base"
    # The research is still shared — most of the cost.
    assert lib.research_for("Grilled Chicken Skewers")["sources"][0]["url"] == A_URL
    # And the next, unrestricted household writes (no new research) and files the base.
    monkeypatch.setattr(agent, "generate_recipe_details_llm", lambda spec: writer_calls.append(spec) or _satay(spec))
    assert _write(_plan_pending())["ok"]
    assert "Peanut butter" in json.dumps(lib.base_recipe("Grilled Chicken Skewers")["ingredients"])
    assert research_calls == ["Grilled Chicken Skewers"]


@pytest.mark.parametrize("key, value", [
    ("eating_style", "vegetarian"), ("kitchen_kit", ["oven", "stovetop"]), ("children_at_table", True),
])
def test_a_household_the_base_wasnt_written_for_gets_its_own_write_not_a_copy(
        b, research_calls, writer_calls, monkeypatch, key, value):
    _write(_plan_pending())
    with tools.use_household(b):
        recipe = _plan_pending()
        shared = {**agent._shared_recipe_details_context(), key: value}
        assert agent._write_one_pending_recipe(recipe, "dinner", shared, [])["ok"]
    assert len(writer_calls) == 2, f"a {key} household was copied a base written without it"
    assert writer_calls[1][key] == value
    assert research_calls == ["Grilled Chicken Skewers"], "its write reused the library's research"
    assert writer_calls[1]["research"]["lead"]["name"] == "Cook A"


def test_an_allergen_only_the_steps_use_still_stops_the_copy(b, writer_calls, monkeypatch):
    lib.file_entry("Grilled Chicken Skewers",
                   ingredients=[{"item": "Chicken thighs", "qty": "2 lb"}, {"item": "Limes", "qty": "2"}],
                   instructions=["Grill the chicken thighs.", "Brush with 2 tbsp peanut butter and squeeze the limes over."],
                   default_servings=4, origin="seed")
    with tools.use_household(b):
        tools.add_member("Ravi")
        tools.set_member_dietary_restrictions("Ravi", ["peanuts"])
        _write(_plan_pending())
    assert writer_calls, "the base was copied for a peanut-allergic household"


def test_typed_and_imported_recipes_never_reach_the_library(writer_calls):
    tools.add_recipe(name="Grandma's Dal", ingredients=[{"item": "Red lentils", "qty": "1 cup"}],
                     instructions=["Simmer."], notes="family recipe")
    tools.add_recipe(name="Linked Lasagna", ingredients=[{"item": "Noodles", "qty": "1 box"}],
                     instructions=["Layer."], source_url="https://example.com/lasagna")
    # Even handed straight to the write path, an imported row is refused.
    assert lib.file_from_recipe(tools.get_recipe("Linked Lasagna")["id"], spec={"name": "Linked Lasagna"}) is None
    assert _rows("SELECT * FROM recipe_library") == []


def test_nothing_personal_goes_in_and_a_step_naming_someone_is_not_filed(writer_calls, monkeypatch):
    tools.add_member("Mary Asha")

    def _named(spec):
        out = _satay(spec)
        out["instructions"] = out["instructions"] + ["Keep Asha's skewers plain."]
        return out

    monkeypatch.setattr(agent, "generate_recipe_details_llm", _named)
    assert _write(_plan_pending())["ok"]
    assert _rows("SELECT * FROM recipe_library") == [], "a recipe naming someone (one word of their name) was filed"
    # The library's own columns: no household, no member, no notes or ratings.
    cols = {r["name"] for r in _rows("PRAGMA table_info(recipe_library)")}
    cols |= {r["name"] for r in _rows("PRAGMA table_info(recipe_library_sources)")}
    assert not cols & {"household_id", "member_id", "notes", "feedback_notes",
                       "household_changes_json", "temporarily_excluded", "times_cooked"}


@pytest.mark.parametrize("name, private", [
    ("Nana's Lasagne", True), ("Mum's Dal", True), ("Grandma Lasagne", True), ("Kevin's Chili", True),
    ("Biscuit Stew", True),             # the household's pet
    ("Okafor Jollof", True),            # the household's own name
    ("Theo Pancakes", True),            # a guest named in a hosting note
    ("Pip Curry", True),                # someone named in a hard fact
    ("Shepherd's Pie", False), ("General Tso's Chicken", False), ("Chicken Curry", False),
])
def test_a_dish_named_for_someone_is_never_filed(name, private):
    hid = households.create_household("The Okafor Family", "okafor-pass")
    with tools.use_household(hid):
        conn = get_conn()
        conn.execute("INSERT INTO pets (household_id, name) VALUES (?, 'Biscuit')", (hid,))
        conn.execute("INSERT INTO holiday_answers (household_id, date, holiday_name, answer, guest_notes) "
                     "VALUES (?, '2026-12-25', 'Christmas', 'hosting', 'Theo is vegetarian')", (hid,))
        conn.commit()
        conn.close()
        tools.add_fact("health", "Pip can't have shellfish", hard=True)
        filed = lib.file_entry(name, ingredients=[{"item": "Rice", "qty": "1 bag"}],
                               instructions=["Cook the rice."], origin="written")
    assert (filed is None) is private


def test_a_dish_the_household_asked_for_by_name_is_never_filed(writer_calls):
    tools.create_weekly_plan(household_date(0), constraints_notes="please do the honey walnut prawns again")
    assert lib.file_entry("Honey Walnut Prawns", ingredients=[{"item": "Prawns", "qty": "1 lb"}],
                          instructions=["Fry the prawns."], origin="written") is None
    assert lib.file_entry("Garlic Prawns", ingredients=[{"item": "Prawns", "qty": "1 lb"}],
                          instructions=["Fry the prawns."], origin="written") is not None


def test_a_rewrite_from_the_households_own_words_files_nothing(
        b, research_calls, writer_calls, monkeypatch):
    _write(_plan_pending())
    with tools.use_household(b):
        _write(_plan_pending())
        b_recipe = tools.get_recipe("Grilled Chicken Skewers")
    base_before = lib.base_recipe("Grilled Chicken Skewers")
    a_recipe = tools.get_recipe("Grilled Chicken Skewers")

    spec = {"recipe_id": a_recipe["id"], "name": "Grilled Chicken Skewers", "slot": "dinner", "cuisine": "Thai",
            "main_protein": "chicken", "tags": [], "food_groups": [], "dish_note": "",
            "prep_time_minutes": 20, "cook_time_minutes": 15,
            "ingredients": a_recipe["ingredients"], "instructions": a_recipe["instructions"]}

    def _apply(spec_base, detail, text, **_kw):
        conn = get_conn()
        conn.execute("UPDATE recipes SET instructions_json = ? WHERE id = ? AND household_id = 1",
                     (json.dumps(["Grill the chicken with extra lime."] + detail["instructions"]),
                      spec_base["recipe_id"]))
        conn.commit()
        conn.close()
        return {"status": "rewritten", "said": "Done."}

    monkeypatch.setattr(tools, "rewrite_spec", lambda entry_id: spec)
    monkeypatch.setattr(tools, "apply_rewrite", _apply)
    agent.rewrite_meal_recipe(1, "more lime please, the kids hated it")

    assert lib.base_recipe("Grilled Chicken Skewers") == base_before
    with tools.use_household(b):
        assert tools.get_recipe("Grilled Chicken Skewers")["instructions"] == b_recipe["instructions"]
    assert _rows("SELECT * FROM recipe_library WHERE kind = 'variant'") == []
    assert "extra lime" not in json.dumps(_rows("SELECT * FROM recipe_library"))


