"""
Library first (Emily, 2026-11-03).

Live, one household, Oct 25–Nov 3: research $0.72 (16 calls) and the recipe
writer $0.62 for dishes the planner invented, while the shared library held
1,116 dishes researched and written — looked up by name, it hit 0 times.

Pinned here, every model call stubbed:
  * the draft is handed library dishes that fit the household, and a
    library pick is copied at approval — no research, no writer call —
    also for a household with children at the table;
  * nothing in the library fits -> the new dish is researched AND written,
    and goes back into the library (THE WRITE-BACK RULE), so the next
    household is copied it with no calls at all;
  * every new-recipe path goes through recipe_library.file_new_recipe
    (a source tripwire: a new "the model wrote it" save that skips it fails);
  * household-private recipes (typed, imported, Tell-Pomona rewrites) never
    reach the library;
  * swaps offer library dishes first (sheet and "Swap · I'll pick"), copy
    a tapped one with no writer call, and ask the model when the household
    asks for different ones;
  * the allergen gate still holds a library dish back, everywhere;
  * research requests carry prompt caching.
"""
from __future__ import annotations

import ast
import importlib
import json
from pathlib import Path

import pytest

from app import agent, tools
from app.db import get_conn
def _lf():
    return importlib.import_module("app.tools.library_first")
from app.tools import recipe_library as lib
from app.tools import recipe_research as rr

from test_menu_first_generation import (  # noqa: F401  (fixtures)
    DETAILS, _menu_week, _week_start, household, menu_model, recipe_model,
)
from test_week1_carousel import DAY1, _asker, _entry_id, _pick, week  # noqa: F401

sop = importlib.import_module("app.tools.swap_option_picks")
sip = importlib.import_module("app.tools.swap_in_place")

REPO = Path(__file__).resolve().parent.parent


def _file(name, *, meal="dinner", protein="vegetarian", cuisine="Indian", items=("Chickpeas", "Onion"),
          minutes=(10, 20), steps=None):
    return lib.file_entry(
        name,
        ingredients=[{"item": i, "qty": "1 lb", "category": "produce"} for i in items],
        instructions=steps or [f"Cook the {items[0].lower()} with the {items[-1].lower()} until done."],
        default_servings=4, prep_time_minutes=minutes[0], cook_time_minutes=minutes[1],
        cuisine=cuisine, main_protein=protein, origin="seed", meal=meal,
    )


@pytest.fixture
def research_calls(monkeypatch):
    """Research on, its model call recorded (never the network)."""
    monkeypatch.setenv("RECIPE_RESEARCH", "on")
    monkeypatch.setattr(rr, "start_link_check", lambda research: False)
    calls = []

    def _llm(name, *a, **k):
        calls.append(name)
        return {"sources": [{"name": "Cook A", "title": name, "url": "https://cook-a.example/" + name.replace(" ", "-"),
                             "rating": 4.8, "rating_count": 500, "read": True}],
                "method_notes": "Sear, then braise.", "used": {"web_search": 1, "web_fetch": 1},
                "input_tokens": 9000}

    monkeypatch.setattr(agent, "research_dish_llm", _llm)
    return calls


def _library_rows(name):
    conn = get_conn()
    try:
        return [dict(r) for r in conn.execute(
            "SELECT * FROM recipe_library WHERE dish_key = ?", (rr.dish_key(name),)).fetchall()]
    finally:
        conn.close()


# ---------- drafting the week ----------

def test_the_draft_is_handed_library_dishes_and_a_pick_is_copied_with_no_research_or_writer(
        household, menu_model, recipe_model, research_calls, monkeypatch):
    _file("Chana Masala")
    _file("Lemon Chicken", protein="chicken", cuisine="Greek", items=("Chicken thighs", "Lemons"))
    week = _week_start()
    seen_context = {}

    def _menu(context):
        seen_context.update(context)
        return _menu_week(week, new_dinner="Chana Masala")

    monkeypatch.setattr(agent, "generate_weekly_plan_llm", _menu)
    plan = agent.generate_weekly_plan(week)
    dinners = seen_context.get("library_dishes", {}).get("dinner") or []
    assert any(line.startswith("Chana Masala |") for line in dinners), "the draft wasn't handed the library"
    assert any(line.startswith("Lemon Chicken |") for line in dinners)
    seen = recipe_model()
    assert tools.approve_weekly_plan(plan["weekly_plan_id"])["status"] == "approved"
    assert seen["specs"] == [], "a library pick was written by the writer"
    assert research_calls == [], "a library pick was researched"
    mine = tools.get_recipe("Chana Masala")
    assert not mine["details_pending"] and [i["item"] for i in mine["ingredients"]][:2] == ["Chickpeas", "Onion"]


def test_the_prompt_tells_the_planner_to_choose_from_the_library_first():
    import inspect
    src = inspect.getsource(agent.generate_weekly_plan_llm)
    assert "COME FROM `library_dishes` FIRST" in src


def test_a_household_with_a_child_is_copied_the_library_recipe_as_it_is(
        household, menu_model, recipe_model, research_calls):
    tools.add_member("Theo")
    tools.set_member_age_group("Theo", "child")
    assert rr.children_eat_here()
    _file("Chana Masala")
    week = _week_start()
    menu_model(_menu_week(week, new_dinner="Chana Masala"))
    plan = agent.generate_weekly_plan(week)
    seen = recipe_model()
    tools.approve_weekly_plan(plan["weekly_plan_id"])
    assert seen["specs"] == [] and research_calls == []
    assert not tools.get_recipe("Chana Masala")["details_pending"]


def test_the_lists_skip_what_the_household_cant_or_wont_have(household):
    tools.set_member_dietary_restrictions("Emily", ["peanuts"])
    tools.edit_preference("dislikes", ["mushrooms"])
    _file("Peanut Noodles", items=("Rice noodles", "Peanut butter"))
    _file("Mushroom Risotto", items=("Arborio rice", "Mushrooms"))
    _file("Chana Masala")
    _file("Egg Salad Sandwich", meal="lunch", items=("Eggs", "Bread"))
    lists = _lf().for_prompt(tools.get_household_memory() or {}, seed="w")
    names = [line.split(" | ")[0] for line in lists.get("dinner", [])]
    assert names == ["Chana Masala"], names
    assert [line.split(" | ")[0] for line in lists["lunch"]][0] == "Egg Salad Sandwich"


def test_a_diet_the_code_cant_read_plans_as_before(household):
    _file("Chana Masala")
    tools.set_member_dietary_restrictions("Emily", ["kosher"])
    assert _lf().for_prompt(tools.get_household_memory() or {}, seed="w") == {}
    tools.set_member_dietary_restrictions("Emily", [], replace=True)
    tools.edit_preference("eating_style", "keto")
    assert _lf().for_prompt(tools.get_household_memory() or {}, seed="w") == {}


def test_the_library_switch_off_hands_nothing(household, monkeypatch):
    _file("Chana Masala")
    monkeypatch.setenv("RECIPE_LIBRARY", "off")
    assert _lf().for_prompt(tools.get_household_memory() or {}, seed="w") == {}


# ---------- nothing fits: research, write, and file it back ----------

def _pending(name="Chettinad Pepper Chicken"):
    tools.add_recipe(name=name, ingredients=[], details_pending=True, cuisine="Indian", main_protein="chicken",
                     dish_note="sear the thighs, bloom the pepper-fennel blend, braise briefly")
    return {**tools.get_recipe(name), "slot": "dinner"}


def _recipe_pass(recipe):
    """One dish through the recipe pass, as the background pass runs it
    (research allowed; Approve itself never waits on research)."""
    return agent._write_one_pending_recipe(recipe, "dinner", agent._shared_recipe_details_context(),
                                           agent._allergen_gate.hard_avoidances())


def test_a_new_dish_is_researched_written_and_filed_so_the_next_household_gets_it_free(
        household, recipe_model, research_calls):
    # A child at the table: before 2026-11-03 a recipe written for such a
    # household was never filed (written around them). Now it is, as a
    # variant that says only "children".
    tools.add_member("Theo")
    tools.set_member_age_group("Theo", "child")
    seen = recipe_model()
    assert _recipe_pass(_pending())["ok"]
    assert [s["name"] for s in seen["specs"]] == ["Chettinad Pepper Chicken"]
    assert research_calls == ["Chettinad Pepper Chicken"]
    rows = [r for r in _library_rows("Chettinad Pepper Chicken") if r["ingredients_json"] not in ("", "[]")]
    assert rows, "the written recipe never went back into the library"
    assert json.loads(rows[0]["adapted_for_json"]) == ["children"]
    assert rows[0]["meal"] == "dinner"
    assert "Theo" not in json.dumps(rows) and "Emily" not in json.dumps(rows)

    # The next household with a child: copied, no research, no writer.
    from app import households
    other = households.create_household("The Next Household", "next-household-pass")
    with tools.use_household(other):
        tools.add_member("Mia")
        tools.set_member_age_group("Mia", "child")
        assert _recipe_pass(_pending())["ok"]
        assert not tools.get_recipe("Chettinad Pepper Chicken")["details_pending"]
    assert len(seen["specs"]) == 1 and research_calls == ["Chettinad Pepper Chicken"]


def test_a_dish_written_for_the_dish_alone_is_the_base(household, recipe_model, research_calls):
    recipe_model()
    assert _recipe_pass(_pending())["ok"]
    base = lib.base_recipe("Chettinad Pepper Chicken")
    assert base and base["kind"] == "base" and base["meal"] == "dinner"
    assert base["sources"][0]["name"] == "Cook A"


def test_household_private_recipes_never_reach_the_library(household):
    tools.add_recipe(name="Linked Lasagna", ingredients=[{"item": "Noodles", "qty": "1 box"}],
                     instructions=["Layer."], source_url="https://example.com/lasagna")
    rid = tools.existing_recipe_named("Linked Lasagna")["id"]
    assert lib.file_new_recipe(rid, spec={"name": "Linked Lasagna"}) is None
    tools.add_recipe(name="Soup", ingredients=[{"item": "Carrots", "qty": "1 lb"}], instructions=["Simmer."])
    rid = tools.existing_recipe_named("Soup")["id"]
    assert lib.file_new_recipe(rid, spec={"change_requested": "less salt, like we make it"}) is None
    assert lib.file_new_recipe(rid, spec={"must_use": ["carrots"]}) is None
    tools.add_recipe(name="Nana's Soup", ingredients=[{"item": "Carrots", "qty": "1 lb"}], instructions=["Simmer."])
    assert lib.file_new_recipe(tools.existing_recipe_named("Nana's Soup")["id"], spec={}) is None
    assert _library_rows("Linked Lasagna") == [] and _library_rows("Soup") == [] and _library_rows("Nana's Soup") == []


# The functions that save a recipe the MODEL wrote, and how each reaches the
# write-back rule. A new one that appears without an entry here fails.
_FILES_ITSELF = {
    ("app/agent.py", "_write_one_pending_recipe"),      # the recipe pass
    ("app/agent.py", "_ensure_recipe_saved"),           # a whole recipe from the draft (component planner)
    ("app/tools/swap_in_place.py", "_save_recipe_if_new"),  # swaps, re-picks, a filled day
    ("app/tools/big_meal.py", "_recipe_for_main"),      # a big meal's main
}
_ROUTED = {
    # The tapped option's write-out is saved by apply_pick -> _save_recipe_if_new.
    ("app/tools/swap_option_picks.py", "choose_swap_option"),
}
_HOUSEHOLD_PRIVATE = {
    # Written from the household's own words: never shared (Emily, 2026-11-03).
    ("app/agent.py", "_rewrite_meal_recipe"),            # Tell Pomona what to change
    ("app/agent.py", "_write_dish_for_chat"),            # chat: "add my lasagna…"
    ("app/agent.py", "add_recipe_from_chat"),
    ("app/agent.py", "update_recipe_details_from_chat"),
}
_WRITE_MARKERS = {"generate_recipe_details_llm", "_write_out", "writer"}


def _writes_a_model_recipe(call: ast.Call) -> bool:
    func = call.func
    name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
    if name.endswith("add_recipe") and any(
            k.arg == "measure_spices" and isinstance(k.value, ast.Constant) and k.value.value is True
            for k in call.keywords):
        return True
    if name in _WRITE_MARKERS:
        return True
    # (writer or _write_out)(...)
    if isinstance(func, ast.BoolOp):
        return any(getattr(v, "id", "") == "_write_out" for v in func.values)
    return False


def test_every_new_recipe_path_goes_through_the_write_back_rule():
    found: dict[tuple[str, str], bool] = {}
    for path in sorted((REPO / "app").rglob("*.py")):
        rel = path.relative_to(REPO).as_posix()
        tree = ast.parse(path.read_text(encoding="utf-8"))

        def visit(node, fn=None):
            for child in ast.iter_child_nodes(node):
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    visit(child, child)
                    continue
                if fn is not None and isinstance(child, ast.Call) and _writes_a_model_recipe(child):
                    if fn.name in ("generate_recipe_details_llm", "_write_out"):
                        continue
                    src = ast.unparse(fn)
                    found[(rel, fn.name)] = "file_new_recipe(" in src or "file_written(" in src
                visit(child, fn)
        visit(tree)
    unknown = set(found) - _FILES_ITSELF - _ROUTED - _HOUSEHOLD_PRIVATE
    assert not unknown, (f"a new path saves a recipe the model wrote without the library write-back rule "
                         f"(recipe_library.file_new_recipe / library_first.file_written): {sorted(unknown)}")
    skipped = [k for k in _FILES_ITSELF if not found.get(k)]
    assert not skipped, f"these new-recipe paths no longer file into the library: {skipped}"


# Who may turn filing ON when saving a swap/re-pick (review, 2026-11-03):
# only callers whose brief is Pomona's own. (file, function) -> why.
_SAVE_DOORS = {"_save_recipe_if_new", "apply_pick", "apply_pick_to_days"}
_MAY_FILE = {
    ("app/tools/swap_in_place.py", "swap_meal_in_place"): "Swap · I'll pick: the swap's own context",
    ("app/tools/swap_in_place.py", "_fill_one_slot"): "a filled day: the swap's own context",
    ("app/tools/swap_in_place.py", "apply_pick"): "passes its caller's flag through",
    ("app/tools/swap_in_place.py", "apply_pick_to_days"): "passes its caller's flag through",
    ("app/tools/swap_option_picks.py", "choose_swap_option"): "only when the sheet was asked with no words",
    ("app/tools/allergen_gate.py", "repick_slot"): "allergen re-pick: swap context + Pomona's reason",
    ("app/tools/allergen_gate.py", "_save_pick"): "allergen re-pick of a group: swap context",
    ("app/tools/meal_variety.py", "_pick_a_snack"): "a snack short: Pomona's reason",
    ("app/tools/meal_variety.py", "_repick_entry"): "only when context_extra is Pomona's own",
}
# Modules reached from the household's own words: must never turn it on.
_NEVER_FILE_MODULES = {"app/tools/proposals.py", "app/tools/plate_parts.py", "app/tools/typed_requests.py"}


def test_only_pomonas_own_picks_turn_filing_on():
    turned_on: set[tuple[str, str]] = set()
    saves_in_private_modules = 0
    for path in sorted((REPO / "app").rglob("*.py")):
        rel = path.relative_to(REPO).as_posix()
        tree = ast.parse(path.read_text(encoding="utf-8"))

        def visit(node, fn=None):
            for child in ast.iter_child_nodes(node):
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    visit(child, child)
                    continue
                if isinstance(child, ast.Call):
                    f = child.func
                    name = f.attr if isinstance(f, ast.Attribute) else getattr(f, "id", "")
                    if name in _SAVE_DOORS and fn is not None:
                        nonlocal_count[0] += rel in _NEVER_FILE_MODULES
                        flag = next((k.value for k in child.keywords if k.arg in ("file", "file_new")), None)
                        if flag is not None and not (isinstance(flag, ast.Constant) and flag.value is False):
                            turned_on.add((rel, fn.name))
                visit(child, fn)

        nonlocal_count = [0]
        visit(tree)
        saves_in_private_modules += nonlocal_count[0]
    assert saves_in_private_modules >= 3, "the chat card / plate-part saves this test guards have moved"
    wrong = {k for k in turned_on if k[0] in _NEVER_FILE_MODULES}
    assert not wrong, f"a path from the household's own words files into the shared library: {sorted(wrong)}"
    unknown = turned_on - set(_MAY_FILE)
    assert not unknown, f"new callers turn library filing on; justify them in _MAY_FILE: {sorted(unknown)}"
    import inspect
    sig = inspect.signature(sip._save_recipe_if_new)
    assert sig.parameters["file"].default is False, "filing must be off unless a caller turns it on"
    for door in ("apply_pick", "apply_pick_to_days"):
        assert inspect.signature(getattr(sip, door)).parameters["file_new"].default is False


def test_a_chat_change_card_dish_never_reaches_the_library(week):
    """Review repro: a chat proposal whose steps carry the household's own
    words was filed as a library base."""
    from app.tools import proposals as prop
    prop._PROPOSALS.clear()
    name = "Smoky Lentil Stew"
    cand = _pick(name, protein="lentils")
    cand["instructions"] = ["Simmer the lentils.", "Add our special smoked paprika the way we like it."]
    card = tools.propose_plan_changes(week, [{"date": DAY1, "slot": "dinner", "action": "change",
                                              "candidates": [cand]}])
    applied = tools.apply_proposal(card["proposal_id"])
    assert applied["status"] == "applied", applied
    assert _library_rows(name) == [], "a chat-dictated dish was shared with every household"
    # And the recipe pass won't file it later either.
    rid = tools.existing_recipe_named(name)["id"]
    assert lib.file_new_recipe(rid, spec={}) is None


def test_a_swap_asked_for_in_the_households_words_is_not_filed(week):
    entry_id = _entry_id(week, DAY1)
    _approve(week)
    tools.swap_options(week, entry_id, asker=_asker(_pick("Paprika Lentil Stew", protein="lentils")),
                       request="something with our smoked paprika")
    full = dict(_pick("Paprika Lentil Stew", protein="lentils"), instructions=["Simmer.", "Our paprika."])
    out = tools.choose_swap_option(week, entry_id, 0, meal="Paprika Lentil Stew", writer=lambda c, p: full)
    assert out["status"] == "swapped"
    assert _library_rows("Paprika Lentil Stew") == []


def test_a_model_written_swap_goes_back_into_the_library(week):
    entry_id = _entry_id(week, DAY1)
    pick = _pick("Lemon Herb Cod", protein="cod")
    out = tools.swap_meal_in_place(week, entry_id, avoid=["Chili"], picker=lambda context: dict(pick))
    assert out["status"] == "swapped"
    rows = [r for r in _library_rows("Lemon Herb Cod") if r["ingredients_json"] not in ("", "[]")]
    assert rows and rows[0]["meal"] == "dinner"


# ---------- swaps ----------

def _approve(plan_id):
    conn = get_conn()
    conn.execute("UPDATE weekly_plans SET status = 'approved' WHERE id = ?", (plan_id,))
    conn.commit()
    conn.close()


def _three(monkeypatch=None):
    _file("Chana Masala", cuisine="Indian", protein="chickpeas", items=("Chickpeas", "Onion"))
    _file("Lemon Chicken", protein="chicken", cuisine="Greek", items=("Chicken thighs", "Lemons"))
    _file("Fish Tacos", protein="fish", cuisine="Mexican", items=("Cod", "Tortillas"))
    _file("Tofu Stir-Fry", protein="tofu", cuisine="Chinese", items=("Tofu", "Broccoli"))


def test_swap_options_offer_library_dishes_first_with_no_model_call(week):
    _three()
    ask = _asker(_pick("Model Pick", protein="beef"))
    out = tools.swap_options(week, _entry_id(week, DAY1), asker=ask)
    names = [o["meal"] for o in out["options"]]
    assert len(names) == 3 and "Model Pick" not in names, names
    assert set(names) <= {"Chana Masala", "Lemon Chicken", "Fish Tacos", "Tofu Stir-Fry"}
    assert ask.contexts == [], "the model was asked though the library filled the sheet"
    assert out["options_unavailable"] is False


def test_different_ones_or_a_request_go_to_the_model(week):
    _three()
    ask = _asker(_pick("Model Pick", protein="beef"))
    out = tools.swap_options(week, _entry_id(week, DAY1), asker=ask, request="something with beef")
    assert [o["meal"] for o in out["options"]] == ["Model Pick"] and len(ask.contexts) == 1


def test_a_tapped_library_option_on_an_approved_week_is_copied_not_written(week):
    _three()
    _approve(week)
    entry_id = _entry_id(week, DAY1)
    out = tools.swap_options(week, entry_id, asker=_asker())
    first = out["options"][0]["meal"]

    def _writer(context, pick):
        raise AssertionError("a library dish was written out by the model")

    chosen = tools.choose_swap_option(week, entry_id, 0, meal=first, writer=_writer)
    assert chosen["status"] == "swapped" and chosen["meal"] == first
    mine = tools.get_recipe(first)
    assert not mine["details_pending"] and mine["instructions"]


def test_swap_ill_pick_puts_on_a_library_dish_first_then_asks_the_model(week):
    _three()
    entry_id = _entry_id(week, DAY1)
    calls = []

    def _picker(context):
        calls.append(context)
        return _pick("Model Pick", protein="beef")

    out = tools.swap_meal_in_place(week, entry_id, picker=_picker)
    assert out["status"] == "swapped" and calls == [], "the first tap asked the model"
    assert out["meal"] in {"Chana Masala", "Lemon Chicken", "Fish Tacos", "Tofu Stir-Fry"}
    assert not tools.get_recipe(out["meal"])["details_pending"]
    again = tools.swap_meal_in_place(week, _entry_id(week, DAY1), avoid=out["avoid"], picker=_picker)
    assert again["status"] == "swapped" and len(calls) == 1 and again["meal"] == "Model Pick"


# ---------- the allergen gate still holds library dishes ----------

def test_the_allergen_gate_holds_a_library_dish_back_everywhere(week, research_calls, monkeypatch):
    tools.set_member_dietary_restrictions("Emily", ["peanuts"])
    _file("Peanut Noodles", protein="tofu", items=("Rice noodles", "Peanut butter"))
    # Steps-only allergen: judged on the list the copy would save.
    _file("Grilled Chicken Skewers", protein="chicken", items=("Chicken thighs", "Limes"),
          steps=["Grill the chicken thighs.", "Brush with 2 tbsp peanut butter and squeeze the limes over."])
    memory = tools.get_household_memory() or {}
    assert _lf().for_prompt(memory, seed="w") == {}
    # Swap sheet: none offered, so the model is asked.
    ask = _asker(_pick("Lemon Herb Cod", protein="cod"))
    out = tools.swap_options(week, _entry_id(week, DAY1), asker=ask)
    assert [o["meal"] for o in out["options"]] == ["Lemon Herb Cod"]
    # "Swap · I'll pick": the model, never the peanut dish.
    got = tools.swap_meal_in_place(week, _entry_id(week, DAY1), picker=lambda c: _pick("Lemon Herb Cod", protein="cod"))
    assert got["meal"] == "Lemon Herb Cod"
    # The recipe pass, handed the name: written for them, never copied.
    written = []
    monkeypatch.setattr(agent, "generate_recipe_details_llm",
                        lambda spec: written.append(spec) or dict(DETAILS))
    tools.add_recipe(name="Grilled Chicken Skewers", ingredients=[], details_pending=True)
    recipe = {**tools.get_recipe("Grilled Chicken Skewers"), "slot": "dinner"}
    agent._write_one_pending_recipe(recipe, "dinner", agent._shared_recipe_details_context(),
                                    agent._allergen_gate.hard_avoidances())
    assert written, "a library dish with peanut butter in its steps was copied for a peanut allergy"


@pytest.mark.parametrize("how", ["eating_style", "member", "fact"])
def test_a_vegetarian_household_is_never_copied_a_library_dish(week, monkeypatch, how):
    """Review repro (2026-11-03): eating style "vegetarian", a library Pad
    Thai with prawns and oyster sauce under a vegetable name — the recipe
    pass copied it with no writer call. Words the code's lists miss
    (prawns, oyster sauce) must not matter: a diet the gate can't read is
    never copied, by any path."""
    if how == "eating_style":
        tools.edit_preference("eating_style", "vegetarian")
    elif how == "member":
        tools.set_member_dietary_restrictions("Emily", ["vegetarian"])
    else:
        tools.add_fact("diet", "We're vegetarian", hard=True)
    _file("Vegetable Pad Thai", protein="vegetarian", cuisine="Thai",
          items=("Rice noodles", "Prawns", "Oyster sauce"))
    written = []
    monkeypatch.setattr(agent, "generate_recipe_details_llm", lambda spec: written.append(spec) or dict(DETAILS))
    tools.add_recipe(name="Vegetable Pad Thai", ingredients=[], details_pending=True)
    recipe = {**tools.get_recipe("Vegetable Pad Thai"), "slot": "dinner"}
    agent._write_one_pending_recipe(recipe, "dinner", agent._shared_recipe_details_context(),
                                    agent._allergen_gate.hard_avoidances())
    assert written, f"a {how} vegetarian household was copied a library dish with prawns"
    # The other library doors: the swap's copy, the sheet, "I'll pick", the draft list.
    lf = _lf()
    _file("Vegetable Fried Rice", protein="vegetarian", cuisine="Thai", items=("Rice", "Fish sauce"))
    assert lf.copy_for_swap("Vegetable Fried Rice") is False
    assert tools.existing_recipe_named("Vegetable Fried Rice") is None, "a refused copy left a row behind"
    assert lf.swap_picks(week, {"slot": "dinner", "entry_id": _entry_id(week, DAY1), "meal": "Pork Chops"}, []) == []
    assert lf.for_prompt(tools.get_household_memory() or {}, seed="w") == {}
    assert lf.diet_blocks_the_library() is True


def test_the_list_judges_the_version_that_would_be_served(household, monkeypatch):
    """Review: dislikes, diet and minutes were read off the LISTED version
    (the base) while servable_recipe serves another (a variant written
    around an allergy, when the base clashes). The list now judges the
    version a copy would serve: a peanut-allergic household is offered the
    peanut-free variant of a dish — unless THAT version carries a dislike."""
    tools.set_member_dietary_restrictions("Emily", ["peanuts"])
    tools.edit_preference("dislikes", ["mushrooms"])
    for name, extra in (("Noodle Bowl", "Carrots"), ("Rice Bowl", "Mushrooms")):
        _file(name, items=("Rice noodles", "Peanut butter"))   # the base clashes
        lib.file_entry(name, ingredients=[{"item": "Rice noodles", "qty": "1 lb"},
                                          {"item": "Sunflower seed butter", "qty": "1 jar"},
                                          {"item": extra, "qty": "8 oz"}],
                       instructions=[f"Cook the noodles with the {extra.lower()}."], default_servings=4,
                       prep_time_minutes=10, cook_time_minutes=20, main_protein="vegetarian",
                       origin="written", variant=True, meal="dinner", adapted_for=["allergies"])
    lists = _lf().for_prompt(tools.get_household_memory() or {}, seed="w")
    names = [line.split(" | ")[0] for line in lists.get("dinner", [])]
    assert "Noodle Bowl" in names, "the peanut-free version this household would be served wasn't offered"
    assert "Rice Bowl" not in names, "the served version carries a dislike"


# ---------- research caching ----------

def test_research_requests_carry_prompt_caching(monkeypatch):
    from test_research_input_budget import _Client, _found, _read
    monkeypatch.setenv("RECIPE_RESEARCH", "on")
    client = _Client([(_found(), "tool_use"), (_read(), "tool_use")])
    monkeypatch.setattr(agent, "_client", lambda: client)
    agent.research_dish("Chana Masala", "North Indian", "chickpeas in an onion-tomato masala")
    assert len(client.calls) == 2
    find, read = client.calls
    # The read step's several server steps (fetch, fetch, submit) re-bill the
    # pages before them: cached. The find step has one step after its
    # results, so caching would only write: not cached (review, 2026-11-03).
    assert read.get("cache_control") == {"type": "ephemeral"}, "the read step isn't cached"
    assert "cache_control" not in find
    # No explicit breakpoint on the tools: their max_uses / allowed_domains
    # change per round and per dish, so that prefix would only ever write.
    for call in client.calls:
        assert not any("cache_control" in t for t in call["tools"])
