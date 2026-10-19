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
    return agent._write_one_pending_recipe(recipe, "dinner", SHARED, agent._allergen_gate.hard_avoidances())


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


def test_a_version_written_around_must_avoids_is_a_variant_never_the_base(
        b, research_calls, writer_calls, monkeypatch):
    _write(_plan_pending())
    base_before = lib.base_recipe("Grilled Chicken Skewers")

    def _no_peanut(spec):
        writer_calls.append(spec)
        out = _satay(spec)
        out["ingredients"] = [i for i in out["ingredients"] if i["item"] != "Peanut butter"] + [
            {"item": "Sunflower seed butter", "qty": "1 jar", "category": "pantry"}]
        out["instructions"] = ["Marinate the chicken in coconut milk and turmeric.",
                               "Grill on skewers; stir sunflower seed butter into a sauce and squeeze in the limes."]
        out["household_changes"] = ["No peanuts for Ravi; sunflower seed butter instead"]
        return out

    monkeypatch.setattr(agent, "generate_recipe_details_llm", _no_peanut)
    with tools.use_household(b):
        tools.add_member("Ravi")
        tools.set_member_dietary_restrictions("Ravi", ["peanuts"])
        assert _write(_plan_pending())["ok"]
        assert "Sunflower" in json.dumps(tools.get_recipe("Grilled Chicken Skewers")["ingredients"])
    assert lib.base_recipe("Grilled Chicken Skewers") == base_before
    variants = _rows("SELECT * FROM recipe_library WHERE kind = 'variant'")
    assert len(variants) == 1 and variants[0]["variant_of"] == base_before["id"]
    assert "Ravi" not in json.dumps(_rows("SELECT * FROM recipe_library"))


def test_typed_and_imported_recipes_never_reach_the_library(writer_calls):
    tools.add_recipe(name="Grandma's Dal", ingredients=[{"item": "Red lentils", "qty": "1 cup"}],
                     instructions=["Simmer."], notes="family recipe")
    tools.add_recipe(name="Linked Lasagna", ingredients=[{"item": "Noodles", "qty": "1 box"}],
                     instructions=["Layer."], source_url="https://example.com/lasagna")
    # Even handed straight to the write path, an imported row is refused.
    assert lib.file_from_recipe(tools.get_recipe("Linked Lasagna")["id"]) is None
    assert _rows("SELECT * FROM recipe_library") == []
    # And a typed recipe's tweak is not filed: it never came from the library.
    assert lib.file_tweak(tools.get_recipe("Grandma's Dal")["id"]) is None
    assert _rows("SELECT * FROM recipe_library") == []


def test_nothing_personal_goes_in_and_a_step_naming_someone_is_not_filed(writer_calls, monkeypatch):
    tools.add_member("Asha")
    tools.add_fact("health", "Asha is allergic to sesame", hard=True)

    def _named(spec):
        out = _satay(spec)
        out["instructions"] = out["instructions"] + ["Keep Asha's skewers plain."]
        out["household_changes"] = []
        return out

    monkeypatch.setattr(agent, "generate_recipe_details_llm", _named)
    assert _write(_plan_pending())["ok"]
    assert _rows("SELECT * FROM recipe_library") == [], "a recipe naming someone was filed"
    # The library's own columns: no household, no member, no notes or ratings.
    cols = {r["name"] for r in _rows("PRAGMA table_info(recipe_library)")}
    cols |= {r["name"] for r in _rows("PRAGMA table_info(recipe_library_sources)")}
    assert not cols & {"household_id", "member_id", "notes", "feedback_notes",
                       "household_changes_json", "temporarily_excluded", "times_cooked"}


def test_a_tweak_of_a_library_dish_is_a_variant_and_the_base_and_other_copy_stay(
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

    def _apply(spec_base, detail, text):
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
    variants = _rows("SELECT * FROM recipe_library WHERE kind = 'variant'")
    assert len(variants) == 1 and variants[0]["origin"] == "tweak"
    assert variants[0]["variant_of"] == base_before["id"]
    assert "extra lime" in variants[0]["instructions_json"]
    assert "kids hated" not in json.dumps(_rows("SELECT * FROM recipe_library"))


def test_a_household_waits_for_the_same_dish_being_researched_elsewhere(b, research_calls):
    held = lib.claim_research("Grilled Chicken Skewers")
    assert held is None
    box = {}

    def _b():
        with tools.use_household(b):
            box["found"] = agent.research_dish("Grilled Chicken Skewers", "Thai", "", deadline=time.monotonic() + 10)

    t = threading.Thread(target=_b)
    t.start()
    time.sleep(0.2)
    lib.file_entry("Grilled Chicken Skewers", sources=rr.pick_lead([
        {"name": "Cook A", "title": "", "url": A_URL, "rating": 4.8, "rating_count": 900, "read": True},
    ]), method_notes="notes", origin="research")
    lib.release_research("Grilled Chicken Skewers")
    t.join(5)
    assert research_calls == [], "B researched a dish A was researching"
    assert box["found"]["sources"][0]["url"] == A_URL


def test_library_off_is_per_household_as_before(b, research_calls, writer_calls, monkeypatch):
    monkeypatch.setenv("RECIPE_LIBRARY", "off")
    _write(_plan_pending())
    with tools.use_household(b):
        _write(_plan_pending())
    assert len(research_calls) == 2 and len(writer_calls) == 2
    assert _rows("SELECT * FROM recipe_library") == []


def test_household_b_cannot_read_household_as_copy_by_id(b, research_calls, writer_calls):
    _write(_plan_pending())
    a_id = tools.get_recipe("Grilled Chicken Skewers")["id"]
    with tools.use_household(b):
        _write(_plan_pending())
    with TestClient(app) as c:
        assert c.post("/login", data={"password": B_PASS, "next": "/"}, follow_redirects=False).status_code == 303
        assert c.get(f"/api/recipes/{a_id}").status_code == 404
