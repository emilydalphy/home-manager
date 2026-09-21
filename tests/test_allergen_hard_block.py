"""
Never draft or offer a dish with a household allergen — no "Keep it anyway".

Emily, 2026-09-20, on her phone: the draft held "Chicken Al Pastor-Style
Tacos with Pineapple-Free Salsa" for a household where she is allergic to
pineapple, and Plan drew a red card over the row with a "Keep it anyway"
button. "If there is a conflict for an allergy, just don't suggest anything
that fits that. Also make sure the formatting is fixed."

The household here is hers as she described it: Emily, allergic to
pineapple, asking for Mexican this week. Every door that puts a model's
pick on the plan is walked with a model that keeps offering pineapple, and
none of them may let it through: the week draft (app/agent.py's
_generate_weekly_plan through allergen_gate.split_safe / repick_slot), the
plate-side pass and the sweep behind it (allergen_gate.sweep_plan),
"Swap · I'll pick" (swap_in_place), the three picks (swap_options), and the
chat's change card (proposals). The prompts are checked too: every one of
them has to state the allergens as hard exclusions, because the gate is the
last line and the prompt is the first.

Every test in this file fails on main before the fix except the two swap
ones, which pin behaviour that already held (and which the bug report
credited to generation alone being unguarded).
"""
import datetime
import json

import pytest

from app import agent, tools
import importlib
from app.tools import allergen_gate, proposals as prop, swap_in_place as sip
sopt = importlib.import_module("app.tools.swap_options")
from conftest import household_today, prompt_literals


TODAY = household_today()
WEEK_START = TODAY.isoformat()
DAYS = [(TODAY + datetime.timedelta(days=i)).isoformat() for i in range(7)]


def _pineapple(name="Chicken Al Pastor-Style Tacos with Pineapple-Free Salsa"):
    """The dish that got through: the NAME says pineapple-free, the LIST
    has pineapple. Only an ingredient-list match catches it."""
    return {
        "meal_name": name, "is_new_recipe": True,
        "reason": "Mexican, like you asked.",
        "ingredients": [
            {"item": "Chicken thighs", "qty": "1 lb", "category": "meat/seafood"},
            {"item": "Pineapple", "qty": "1", "category": "produce"},
            {"item": "Corn tortillas", "qty": "1 pack", "category": "pantry"},
        ],
        "instructions": ["Marinate.", "Sear.", "Serve."],
        "food_groups": ["protein", "carb"], "cuisine": "Mexican", "main_protein": "chicken",
        "prep_time_minutes": 10, "cook_time_minutes": 20, "default_servings": 2,
    }


def _safe(name="Chicken Souvlaki Bowls", cuisine="Greek"):
    return {
        "meal_name": name, "is_new_recipe": True,
        "reason": f"No Mexican lunch without pineapple, so a {cuisine} one.",
        "ingredients": [
            {"item": "Chicken breast", "qty": "1 lb", "category": "meat/seafood"},
            {"item": "Cucumber", "qty": "1", "category": "produce"},
            {"item": "Rice", "qty": "1 cup", "category": "pantry"},
        ],
        "instructions": ["Grill.", "Bowl it."],
        "food_groups": ["protein", "carb", "vegetable"], "cuisine": cuisine, "main_protein": "chicken",
        "prep_time_minutes": 10, "cook_time_minutes": 15, "default_servings": 2,
    }


def _full_week(meal="Chili"):
    return [
        {"date": day, "slot": slot, "meal_name": meal, "is_new_recipe": False, "reasoning": "fits"}
        for day in DAYS for slot in tools.WEEK_SLOTS
    ]


@pytest.fixture
def emilys_house():
    tools.add_member("Emily")
    tools.set_member_dietary_restrictions("Emily", ["pineapple allergy"])
    tools.set_household_meal_preferences(cuisine_preferences=["Mexican"])
    # No food groups on Chili: the plate pass skips a meal it can't read,
    # so nothing here reaches the sides model. The one test that wants a
    # short plate declares its own.
    tools.add_recipe("Chili", ingredients=[{"item": "beans", "qty": "1 tin"}],
                     prep_time_minutes=10, cook_time_minutes=20)
    tools.save_week_intake(WEEK_START, cuisines=["Mexican"])
    prop._PROPOSALS.clear()
    sopt._OPTIONS_CACHE.clear()


def _names(plan_id):
    return {m["meal"] for m in tools.get_weekly_plan(plan_id)["meals"] if m.get("meal")}


def _slot(plan_id, date, slot):
    for m in tools.get_weekly_plan(plan_id)["meals"]:
        if m["date"] == date and m["slot"] == slot:
            return m
    return None


# ---------- the gate itself ----------

def test_the_match_is_on_the_ingredient_list_so_a_free_label_cannot_sneak_it_in(emilys_house):
    clashes = allergen_gate.hard_clashes(
        "Tacos with Pineapple-Free Salsa",
        ingredients=[{"item": "Pineapple", "qty": "1"}],
    )
    assert clashes and clashes[0]["matched"] == "pineapple" and clashes[0]["member"] == "Emily"
    # And a clean list under a clean name is clean.
    assert allergen_gate.hard_clashes("Tacos", ingredients=[{"item": "Chicken", "qty": "1"}]) == []


def test_a_reused_recipe_is_read_off_its_own_saved_list(emilys_house):
    tools.add_recipe("Fruit Cup", ingredients=[{"item": "pineapple chunks", "qty": "1 bag"}])
    safe, held = allergen_gate.split_safe([
        {"date": DAYS[0], "slot": "snack", "meal_name": "Fruit Cup", "is_new_recipe": False},
        {"date": DAYS[0], "slot": "dinner", "meal_name": "Chili", "is_new_recipe": False},
    ])
    assert [i["meal_name"] for i in safe] == ["Chili"]
    assert [h["item"]["meal_name"] for h in held] == ["Fruit Cup"]


def test_a_dislike_is_never_a_reason_to_hold_a_dish_back(emilys_house):
    tools.edit_preference("dislikes", ["beans"])
    safe, held = allergen_gate.split_safe([
        {"date": DAYS[0], "slot": "dinner", "meal_name": "Chili", "is_new_recipe": False},
    ])
    assert held == [] and len(safe) == 1


# ---------- the draft ----------

def test_the_draft_never_holds_the_pineapple_dish_and_the_slot_is_repicked(emilys_house, monkeypatch):
    days = _full_week()
    lunch = next(d for d in days if d["date"] == DAYS[0] and d["slot"] == "lunch")
    lunch.update(_pineapple())
    monkeypatch.setattr(agent, "generate_weekly_plan_llm", lambda ctx: days)
    picks = []

    def picker(context):
        picks.append(context)
        return _safe()

    monkeypatch.setattr(sip, "_pick_replacement", picker)

    plan = agent.generate_weekly_plan(WEEK_START)
    plan_id = plan["weekly_plan_id"]

    assert "Chicken Al Pastor-Style Tacos with Pineapple-Free Salsa" not in _names(plan_id)
    assert not any(r["name"].startswith("Chicken Al Pastor") for r in tools.list_recipes()), \
        "a held-back dish is never even saved as a recipe"
    repicked = _slot(plan_id, DAYS[0], "lunch")
    assert repicked["meal"] == "Chicken Souvlaki Bowls"
    assert repicked["slot_state"] == "planned"
    # The re-pick was told what it was replacing and why, with the clashing
    # dish on avoid and the allergy as a hard exclusion.
    assert len(picks) == 1
    ctx = picks[0]
    assert ctx["slot"] == "lunch" and ctx["date"] == DAYS[0]
    assert "Chicken Al Pastor-Style Tacos with Pineapple-Free Salsa" in ctx["avoid"]
    assert ctx["must_not_contain"] == ["Emily: pineapple allergy"]
    assert "pineapple" in ctx["replacing_because"] and "Emily" in ctx["replacing_because"]
    # And the finished draft carries no clash, no card.
    menu = tools.get_week_menu(plan_id)
    assert menu["conflicts"] == [] and menu["settle"] is None


def test_a_repick_that_still_clashes_is_tried_once_more_then_the_slot_is_handed_back(emilys_house, monkeypatch):
    days = _full_week()
    lunch = next(d for d in days if d["date"] == DAYS[0] and d["slot"] == "lunch")
    lunch.update(_pineapple())
    monkeypatch.setattr(agent, "generate_weekly_plan_llm", lambda ctx: days)
    offered = ["Pineapple Salsa Bowls", "Al Pastor Plate"]
    picks = []

    def stubborn(context):
        picks.append(context)
        return _pineapple(offered[len(picks) - 1])

    monkeypatch.setattr(sip, "_pick_replacement", stubborn)

    plan = agent.generate_weekly_plan(WEEK_START)
    plan_id = plan["weekly_plan_id"]

    assert len(picks) == sip.MAX_PICK_ATTEMPTS
    assert "Pineapple Salsa Bowls" in picks[1]["avoid"], "the failed attempt joins avoid"
    names = _names(plan_id)
    assert not any("Pineapple" in n or "Al Pastor" in n for n in names)
    handed_back = _slot(plan_id, DAYS[0], "lunch")
    assert handed_back["slot_state"] == "open"
    assert handed_back["open_reason"] == (
        "I couldn’t find a lunch without pineapple for Emily — I’d rather ask than guess."
    )
    assert tools.audit_plan_slots(plan_id)["complete"] is True
    assert tools.get_week_menu(plan_id)["settle"] is None


def test_a_picker_that_breaks_opens_the_slot_rather_than_the_week(emilys_house, monkeypatch):
    days = _full_week()
    next(d for d in days if d["date"] == DAYS[1] and d["slot"] == "dinner").update(_pineapple())
    monkeypatch.setattr(agent, "generate_weekly_plan_llm", lambda ctx: days)

    def boom(context):
        raise RuntimeError("model down")

    monkeypatch.setattr(sip, "_pick_replacement", boom)

    plan = agent.generate_weekly_plan(WEEK_START)
    handed_back = _slot(plan["weekly_plan_id"], DAYS[1], "dinner")
    assert handed_back["slot_state"] == "open"
    assert "without pineapple" in handed_back["open_reason"]
    assert tools.audit_plan_slots(plan["weekly_plan_id"])["complete"] is True


def test_the_repick_budget_caps_what_a_generation_may_spend(emilys_house, monkeypatch):
    days = _full_week()
    for i, d in enumerate(x for x in days if x["slot"] == "dinner"):
        d.update(_pineapple(f"Al Pastor Night {i}"))
    monkeypatch.setattr(agent, "generate_weekly_plan_llm", lambda ctx: days)
    calls = []

    def stubborn(context):
        calls.append(1)
        return _pineapple(f"Still Pineapple {len(calls)}")

    monkeypatch.setattr(sip, "_pick_replacement", stubborn)

    plan = agent.generate_weekly_plan(WEEK_START)

    assert len(calls) == allergen_gate.MAX_REPICK_CALLS
    dinners = [_slot(plan["weekly_plan_id"], d, "dinner") for d in DAYS]
    assert all(s["slot_state"] == "open" for s in dinners)
    assert not any("Pineapple" in n or "Al Pastor" in n for n in _names(plan["weekly_plan_id"]))


def test_a_week_where_everything_clashes_saves_nothing_and_says_so(emilys_house, monkeypatch):
    monkeypatch.setattr(agent, "generate_weekly_plan_llm", lambda ctx: [
        dict(d, **_pineapple(f"Al Pastor {i}")) for i, d in enumerate(_full_week())
    ])
    with pytest.raises(ValueError, match="can't have"):
        agent.generate_weekly_plan(WEEK_START)
    assert tools.get_weekly_plan().get("weekly_plan_id") is None


def test_the_draft_context_carries_the_allergens_as_hard_exclusions(emilys_house, monkeypatch):
    seen = {}
    monkeypatch.setattr(agent, "generate_weekly_plan_llm", lambda ctx: (seen.setdefault("ctx", ctx), _full_week())[1])
    agent.generate_weekly_plan(WEEK_START)
    assert seen["ctx"]["must_not_contain"] == ["Emily: pineapple allergy"]
    assert "pineapple" in json.dumps(seen["ctx"]).lower()


def test_both_draft_prompts_state_must_not_contain_as_absolute():
    for fn in (agent.generate_weekly_plan_llm, agent.generate_component_plan_llm):
        source = prompt_literals(fn)
        assert "`must_not_contain` is absolute" in source
        assert '"-free" name' in source
    day_prompt = prompt_literals(agent.generate_weekly_plan_llm)
    assert "go outside it and say so" in day_prompt, "outside the cuisine beats a compromise"


def test_a_component_pool_drops_the_clashing_item_before_writing(emilys_house, monkeypatch):
    tools.set_planning_mode("component_based")
    monkeypatch.setattr(agent, "generate_component_plan_llm", lambda ctx: [
        dict(_pineapple("Al Pastor Chicken"), category="protein"),
        {"meal_name": "Chili", "category": "protein", "is_new_recipe": False},
    ])
    plan = agent.generate_weekly_plan(WEEK_START)
    assert _names(plan["weekly_plan_id"]) == {"Chili"}


# ---------- the sweep: what the passes after generation add ----------

def test_a_side_the_plate_pass_attached_with_pineapple_comes_back_off(emilys_house, monkeypatch):
    monkeypatch.setattr(agent, "generate_weekly_plan_llm", lambda ctx: _full_week())
    plan = agent.generate_weekly_plan(WEEK_START)
    plan_id = plan["weekly_plan_id"]
    dinner = _slot(plan_id, DAYS[2], "dinner")
    from app.tools import plates
    plates.attach_sides(dinner["entry_id"], [
        {"name": "Pineapple slaw", "covers": ["vegetable"],
         "ingredients": [{"item": "Pineapple", "qty": "1", "category": "produce"}]},
    ], ["vegetable"])
    assert tools.check_plan_conflicts(plan_id)["settle"] is not None, "the side really does clash"

    out = allergen_gate.sweep_plan(plan_id)

    assert out["sides_removed"] == 1 and out["dishes_repicked"] == 0 and out["slots_opened"] == 0
    assert tools.get_plate_sides(dinner["entry_id"]) == []
    assert _slot(plan_id, DAYS[2], "dinner")["meal"] == "Chili", "the dish itself stays"
    assert tools.check_plan_conflicts(plan_id)["settle"] is None


def test_a_dish_that_still_clashes_at_the_end_is_repicked_silently(emilys_house, monkeypatch):
    monkeypatch.setattr(agent, "generate_weekly_plan_llm", lambda ctx: _full_week())
    plan = agent.generate_weekly_plan(WEEK_START)
    plan_id = plan["weekly_plan_id"]
    # Something after the gate put pineapple on Wednesday — planned by hand
    # here, standing in for any later pass.
    tools.add_recipe("Fruit Cup", ingredients=[{"item": "pineapple chunks", "qty": "1 bag"}])
    tools.plan_meal(DAYS[2], "Fruit Cup", slot="snack", weekly_plan_id=plan_id)
    assert tools.check_plan_conflicts(plan_id)["settle"]["meal"] == "Fruit Cup"

    out = allergen_gate.sweep_plan(plan_id, picker=lambda ctx: _safe("Apple and Cheddar", "plain"))

    assert out["dishes_repicked"] == 1
    assert "Fruit Cup" not in _names(plan_id)
    assert tools.check_plan_conflicts(plan_id)["settle"] is None


def test_a_dish_that_cannot_be_repicked_at_the_end_is_handed_back_not_kept(emilys_house, monkeypatch):
    monkeypatch.setattr(agent, "generate_weekly_plan_llm", lambda ctx: _full_week())
    plan = agent.generate_weekly_plan(WEEK_START)
    plan_id = plan["weekly_plan_id"]
    tools.add_recipe("Fruit Cup", ingredients=[{"item": "pineapple chunks", "qty": "1 bag"}])
    tools.plan_meal(DAYS[2], "Fruit Cup", slot="snack", weekly_plan_id=plan_id)

    out = allergen_gate.sweep_plan(plan_id, picker=lambda ctx: _pineapple("More Pineapple"))

    assert out["slots_opened"] == 1
    assert "Fruit Cup" not in _names(plan_id)
    opened = [m for m in tools.get_weekly_plan(plan_id)["meals"]
              if m["date"] == DAYS[2] and m["slot"] == "snack" and m["slot_state"] == "open"]
    assert opened and "without pineapple for Emily" in opened[0]["open_reason"]
    assert tools.check_plan_conflicts(plan_id)["settle"] is None


def test_generation_runs_the_sweep_after_the_plate_pass(emilys_house, monkeypatch):
    """The wiring: a side the plate pass attaches with pineapple is gone by
    the time generation returns."""
    tools.add_recipe("Chili Bowl", ingredients=[{"item": "beans", "qty": "1 tin"}],
                     food_groups=["protein", "vegetable"], prep_time_minutes=10, cook_time_minutes=20)
    monkeypatch.setattr(agent, "generate_weekly_plan_llm", lambda ctx: _full_week("Chili Bowl"))

    def sides_llm(context):
        return [{"name": "Pineapple slaw", "covers": ["carb"],
                 "ingredients": [{"item": "Pineapple", "qty": "1", "category": "produce"}]}]

    monkeypatch.setattr(agent, "generate_sides_llm", sides_llm)
    plan = agent.generate_weekly_plan(WEEK_START)
    plan_id = plan["weekly_plan_id"]
    assert any(m["meal"] == "Chili Bowl" for m in tools.get_weekly_plan(plan_id)["meals"])
    for m in tools.get_weekly_plan(plan_id)["meals"]:
        for side in tools.get_plate_sides(m["entry_id"]) if m.get("entry_id") else []:
            assert "pineapple" not in json.dumps(side).lower()
    assert tools.get_week_menu(plan_id)["settle"] is None


# ---------- Swap · I'll pick, and the three picks ----------

@pytest.fixture
def drafted(emilys_house):
    plan_id = tools.create_weekly_plan(WEEK_START)["weekly_plan_id"]
    for day in DAYS:
        tools.plan_meal(day, "Chili", slot="dinner", weekly_plan_id=plan_id)
    return plan_id


def test_swap_refuses_a_pineapple_pick_and_nothing_changes(drafted):
    entry = _slot(drafted, DAYS[3], "dinner")
    seen = []

    def picker(context):
        seen.append(context)
        return _pineapple(f"Al Pastor {len(seen)}")

    out = tools.swap_meal_in_place(drafted, entry["entry_id"], picker=picker)
    assert out["status"] == "refused"
    assert _slot(drafted, DAYS[3], "dinner")["meal"] == "Chili"
    assert seen[0]["must_not_contain"] == ["Emily: pineapple allergy"]
    assert "must_not_contain` is absolute" in sip.INSTRUCTIONS


def test_the_three_picks_never_include_the_pineapple_one(drafted):
    entry = _slot(drafted, DAYS[3], "dinner")
    out = tools.swap_options(drafted, entry["entry_id"], asker=lambda ctx: [
        _pineapple("Al Pastor Tacos"), _safe("Chicken Souvlaki Bowls"), _safe("Beef Stir-Fry", "Chinese"),
    ])
    assert [o["meal"] for o in out["options"]] == ["Chicken Souvlaki Bowls", "Beef Stir-Fry"]
    assert out["options_unavailable"] is False


# ---------- chat: the change card ----------

def test_the_change_card_never_shows_a_pineapple_candidate(drafted):
    out = tools.propose_plan_changes(drafted, [
        {"date": DAYS[3], "slot": "dinner", "action": "change",
         "candidates": [_pineapple("Al Pastor Tacos"), _safe("Chicken Souvlaki Bowls")]},
    ])
    row = out["rows"][0]
    assert [c["meal_name"] for c in row["candidates"]] == ["Chicken Souvlaki Bowls"]
    assert row.get("problem") is None
    # The model is told which dish it may not offer and why, so its reply
    # line and its next offer can be honest about it.
    raw = prop._PROPOSALS[tools.household_id()][out["proposal_id"]]["rows"][0]
    assert raw["unsafe"] == ["Al Pastor Tacos has pineapple, which Emily can’t have"]


def test_a_change_card_whose_only_candidate_clashes_tells_the_model_to_go_outside_the_cuisine(drafted):
    out = tools.propose_plan_changes(drafted, [
        {"date": DAYS[3], "slot": "dinner", "action": "change", "candidates": [_pineapple("Al Pastor Tacos")]},
    ])
    row = out["rows"][0]
    assert row["candidates"] == []
    assert row["problem"] == (
        "Al Pastor Tacos has pineapple, which Emily can’t have — offer something without it, "
        "outside the cuisine if that’s what it takes"
    )
    # A reused recipe is read off its saved list, not waved through on its name.
    tools.add_recipe("Fruit Cup", ingredients=[{"item": "pineapple chunks", "qty": "1 bag"}])
    out = tools.propose_plan_changes(drafted, [
        {"date": DAYS[3], "slot": "dinner", "action": "change",
         "candidates": [{"meal_name": "Fruit Cup", "reason": "light"}]},
    ])
    assert out["rows"][0]["candidates"] == [] and "pineapple" in out["rows"][0]["problem"]


def test_the_chat_tool_tells_the_model_about_unsafe_candidates():
    schema = next(t for t in agent.TOOL_DEFINITIONS if t["name"] == "propose_plan_changes")
    assert "`unsafe`" in schema["description"]
    assert "outside the cuisine" in schema["description"]
