"""
Loop Board: "Chat said nothing in the week was spicy — the ragu has red
pepper flakes" (QA walk as a new household, 2026-10-02).

Asked from the Plan tab whether anything this week was too spicy for Juno,
the chat checked Friday's fajita bowls and said "Nothing else on the week
leans spicy either" — the ragu calls for crushed red pepper flakes. The
week block it was given carried dish NAMES only and told it not to look the
week up; nothing in the turn had the ingredient lists.

BEHAVIOUR pinned here: get_week_ingredients hands back every planned dish
with its ingredient names (sides included, a batch once, the slots it
can't check named); the agent carries and dispatches it; the system prompt
and the week block both send "what's in the food" questions to it; a
scripted turn from the Plan tab that calls it sees the chili flakes; and
the lookup stays a small fraction of a whole get_weekly_plan result (the
Sept 21-23 cost work slimmed the briefing on purpose).

No network: the model is a stub.
"""
from __future__ import annotations

import datetime
import json
import types

from conftest import household_today

from app import agent, chat_progress, db, tools


TODAY = household_today()
DAYS = [(TODAY + datetime.timedelta(days=i)).isoformat() for i in range(7)]


def _week():
    tools.add_member("Emily")
    tools.add_member("Juno")
    tools.add_recipe(
        "Beef and Vegetable Ragu over Pasta",
        ingredients=[
            {"item": "Ground beef", "qty": "1 lb", "category": "meat/seafood"},
            {"item": "Crushed tomatoes", "qty": "1 can", "category": "pantry"},
            {"item": "Crushed red pepper flakes", "qty": "1 jar", "category": "pantry"},
            {"item": "Rigatoni", "qty": "1 box", "category": "pantry"},
        ],
        food_groups=["protein", "carb", "vegetable"], prep_time_minutes=15, cook_time_minutes=40,
    )
    tools.add_recipe(
        "Chicken Fajita Bowls",
        ingredients=[
            {"item": "Chicken thighs", "qty": "1.5 lb", "category": "meat/seafood"},
            {"item": "Bell peppers", "qty": "3", "category": "produce"},
            {"item": "Chili powder", "qty": "1 jar", "category": "pantry"},
        ],
        food_groups=["protein", "vegetable"], prep_time_minutes=10, cook_time_minutes=20,
    )
    tools.add_recipe(
        "Lemon Salmon Traybake",
        ingredients=[
            {"item": "Salmon fillets", "qty": "4", "category": "meat/seafood"},
            {"item": "Lemons", "qty": "2", "category": "produce"},
        ],
        food_groups=["protein"], prep_time_minutes=10, cook_time_minutes=20,
    )
    plan_id = tools.create_weekly_plan(DAYS[0])["weekly_plan_id"]
    ids = {}
    ids["ragu1"] = tools.plan_meal(DAYS[0], "Beef and Vegetable Ragu over Pasta", slot="dinner", weekly_plan_id=plan_id)["entry_id"]
    ids["ragu2"] = tools.plan_meal(DAYS[1], "Beef and Vegetable Ragu over Pasta", slot="lunch", weekly_plan_id=plan_id)["entry_id"]
    ids["fajita"] = tools.plan_meal(DAYS[4], "Chicken Fajita Bowls", slot="dinner", weekly_plan_id=plan_id)["entry_id"]
    ids["salmon"] = tools.plan_meal(DAYS[2], "Lemon Salmon Traybake", slot="dinner", weekly_plan_id=plan_id)["entry_id"]
    ids["takeout"] = tools.plan_meal(DAYS[5], "Takeout", slot="dinner", weekly_plan_id=plan_id)["entry_id"]
    conn = db.get_conn()
    conn.execute(
        "UPDATE meal_plan_entries SET sides_json = ? WHERE id = ?",
        (json.dumps([{"role": "side", "name": "Garlic Bread", "servings": 4,
                      "ingredients": [{"item": "Baguette", "qty": "1", "category": "other"},
                                      {"item": "Butter", "qty": "1 stick", "category": "dairy"}]}]),
         ids["salmon"]),
    )
    conn.commit()
    conn.close()
    return plan_id, ids


def _dish(out, name):
    return next(d for d in out["dishes"] if d["meal"] == name)


# ---------- the lookup ----------

def test_every_dish_comes_back_with_its_ingredients_including_the_chili_flakes():
    plan_id, _ = _week()
    out = tools.get_week_ingredients()
    assert out["weekly_plan_id"] == plan_id
    ragu = _dish(out, "Beef and Vegetable Ragu over Pasta")
    assert "Crushed red pepper flakes" in ragu["ingredients"]
    assert "Chili powder" in _dish(out, "Chicken Fajita Bowls")["ingredients"]
    # Names only — no quantities, categories or steps riding along.
    assert all(isinstance(i, str) for d in out["dishes"] for i in d["ingredients"])
    assert "1 jar" not in json.dumps(out)


def test_a_dish_on_two_slots_is_checked_once_and_says_both():
    _week()
    out = tools.get_week_ingredients()
    ragus = [d for d in out["dishes"] if d["meal"] == "Beef and Vegetable Ragu over Pasta"]
    assert len(ragus) == 1
    assert len(ragus[0]["when"]) == 2
    assert any(DAYS[0] in w and "dinner" in w for w in ragus[0]["when"])
    assert any(DAYS[1] in w and "lunch" in w for w in ragus[0]["when"])


def test_a_sides_ingredients_are_checked_too():
    _week()
    salmon = _dish(tools.get_week_ingredients(), "Lemon Salmon Traybake")
    assert salmon["sides"] == [{"name": "Garlic Bread", "ingredients": ["Baguette", "Butter"]}]


def test_a_dish_with_nothing_saved_is_named_as_unchecked_not_passed_as_clear():
    _week()
    out = tools.get_week_ingredients()
    assert len(out["no_ingredient_list"]) == 1
    assert "Takeout" in out["no_ingredient_list"][0] and DAYS[5] in out["no_ingredient_list"][0]
    assert all(d["meal"] != "Takeout" for d in out["dishes"])


def test_a_nobody_home_slot_is_neither_a_dish_nor_unchecked():
    _, ids = _week()
    conn = db.get_conn()
    conn.execute("UPDATE meal_plan_entries SET slot_state = 'planned_empty' WHERE id = ?", (ids["fajita"],))
    conn.commit()
    conn.close()
    out = tools.get_week_ingredients()
    assert all(d["meal"] != "Chicken Fajita Bowls" for d in out["dishes"])
    assert all("Fajita" not in u for u in out["no_ingredient_list"])


def test_no_plan_is_an_empty_answer_not_an_error():
    assert tools.get_week_ingredients() == {"weekly_plan_id": None, "dishes": [], "no_ingredient_list": []}


def test_the_lookup_is_a_small_fraction_of_the_whole_plan():
    plan_id, _ = _week()
    lookup = len(json.dumps(tools.get_week_ingredients(plan_id), default=str))
    whole = len(json.dumps(tools.get_weekly_plan(plan_id), default=str))
    assert lookup * 3 < whole


# ---------- the agent carries it and is told to use it ----------

def test_the_agent_sends_and_dispatches_the_tool():
    assert "get_week_ingredients" in {t["name"] for t in agent.tools_for_request()}
    assert agent.TOOL_FUNCTIONS["get_week_ingredients"] is tools.get_week_ingredients
    definition = next(t for t in agent.TOOL_DEFINITIONS if t["name"] == "get_week_ingredients")
    assert "spicy" in definition["description"]
    # A read: never mistaken for a write by the "I changed it" guard.
    assert definition["name"].startswith(agent._READ_ONLY_PREFIXES)


def test_the_system_prompt_says_look_before_claiming_nothing_has_it():
    p = agent.SYSTEM_PROMPT
    assert "call get_week_ingredients and check EVERY" in p
    assert "Never go by dish names" in p
    assert "red pepper flakes" in p
    assert "Say \"nothing else\" only once every dish is checked" in p
    assert "say which ones you couldn't check" in p


def test_the_plan_tab_week_block_says_its_names_are_names_only():
    plan_id, _ = _week()
    text = agent._build_chat_context_block({"kind": "weekly_plan", "weekly_plan_id": plan_id})["text"]
    assert "Beef and Vegetable Ragu over Pasta" in text
    assert "Those are dish names only" in text
    assert "get_week_ingredients" in text
    # Still names only: the briefing did not grow every ingredient.
    assert "Crushed red pepper flakes" not in text


def test_the_progress_line_names_the_look():
    assert chat_progress.progress_line("get_week_ingredients", {}) == "Checking the week's ingredients…"


# ---------- a turn from the Plan tab sees the flakes ----------

def _usage():
    return types.SimpleNamespace(
        input_tokens=0, cache_read_input_tokens=0, cache_creation_input_tokens=0, output_tokens=0,
    )


def test_a_spicy_question_from_the_plan_tab_gets_the_real_ingredients(monkeypatch):
    plan_id, _ = _week()
    calls = []
    responses = [
        types.SimpleNamespace(
            content=[types.SimpleNamespace(type="tool_use", id="t1", name="get_week_ingredients",
                                           input={"weekly_plan_id": plan_id})],
            stop_reason="tool_use", usage=_usage(),
        ),
        types.SimpleNamespace(
            content=[types.SimpleNamespace(type="text", text="The ragu has red pepper flakes in it.")],
            stop_reason="end_turn", usage=_usage(),
        ),
    ]

    def create(**kw):
        calls.append(kw)
        return responses.pop(0)

    monkeypatch.setattr(agent, "_client", lambda: types.SimpleNamespace(messages=types.SimpleNamespace(create=create)))
    reply, conversation = agent.run_agent_turn(
        [], "Juno doesn't like spicy food. Is anything this week too spicy for her?",
        context={"kind": "weekly_plan", "weekly_plan_id": plan_id},
    )
    assert reply == "The ragu has red pepper flakes in it."
    system_text = "\n".join(b["text"] for b in calls[0]["system"])
    assert "Those are dish names only" in system_text
    results = [
        block for m in conversation if m["role"] == "user" and isinstance(m["content"], list)
        for block in m["content"] if block.get("type") == "tool_result"
    ]
    assert len(results) == 1 and not results[0]["is_error"]
    assert "Crushed red pepper flakes" in results[0]["content"]


# ---------- found by the adversarial review ----------

def test_a_freeform_dish_never_borrows_a_saved_recipes_ingredients():
    plan_id, _ = _week()
    # A freeform "Chicken Fajita Bowls"-style clash: same name, no recipe.
    conn = db.get_conn()
    conn.execute(
        "INSERT INTO meal_plan_entries (household_id, weekly_plan_id, date, slot, freeform_meal, food_groups_json) "
        "SELECT household_id, weekly_plan_id, ?, 'lunch', 'Chicken Fajita Bowls', '[]' "
        "FROM meal_plan_entries WHERE weekly_plan_id = ? LIMIT 1",
        (DAYS[6], plan_id),
    )
    conn.commit()
    conn.close()
    out = tools.get_week_ingredients()
    fajitas = [d for d in out["dishes"] if d["meal"] == "Chicken Fajita Bowls"]
    assert len(fajitas) == 1
    assert all(DAYS[6] not in w for w in fajitas[0]["when"])
    assert any(DAYS[6] in u and "Fajita" in u for u in out["no_ingredient_list"])


def test_a_side_with_nothing_saved_is_named_as_unchecked():
    _, ids = _week()
    conn = db.get_conn()
    conn.execute("UPDATE meal_plan_entries SET sides_json = ? WHERE id = ?",
                 (json.dumps([{"role": "side", "name": "Slaw", "ingredients": []}]), ids["fajita"]))
    conn.commit()
    conn.close()
    out = tools.get_week_ingredients()
    assert any("Slaw (side)" in u and DAYS[4] in u for u in out["no_ingredient_list"])


def test_the_meal_card_path_says_its_week_is_names_only_too():
    _, ids = _week()
    text = agent._build_chat_context_block({"kind": "planned_meal", "entry_id": ids["salmon"]})["text"]
    assert "Beef and Vegetable Ragu over Pasta" in text
    assert "Those are dish names only. For what's IN the food, call get_week_ingredients." in text
    assert "Crushed red pepper flakes" not in text
