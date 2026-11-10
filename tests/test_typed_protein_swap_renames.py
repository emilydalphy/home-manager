"""
A typed change that swaps the main protein renames the dish (Emily,
2026-11-09: "asking to have the salmon dinner with shrimp instead actually
changes the dish").

Both typed doors — Change recipe's "Tell Pomona what to change" and Tweak's
"Something else" box — go through POST /api/meal-recipe/rewrite. When the
recipe that comes back has lost the dish's main protein and gained exactly
one other, the dish takes the name Tweak's "Change the protein" row would
give it (recipe_change.protein_swap -> plate_parts._variant_name). Any
other change keeps the name. A rewrite that leaves out the protein that
was asked for says so in plain words instead of reporting a change.
"""
from __future__ import annotations

from app import agent, tools
from app.db import get_conn

from conftest import household_date

MON = household_date(0)
TUE = household_date(1)

SALMON = "Seared Salmon with Lemon-Dill Potatoes and Asparagus"
SHRIMP = "Seared Shrimp with Lemon-Dill Potatoes and Asparagus"

_SALMON_ITEMS = [("Salmon fillets", "4"), ("Baby potatoes", "1.5 lb"), ("Asparagus", "1 bunch"),
                 ("Fresh dill", "1 bunch")]


def _detail(protein_line):
    return {
        "ingredients": [protein_line, {"item": "Baby potatoes", "qty": "1.5 lb"},
                        {"item": "Asparagus", "qty": "1 bunch"}, {"item": "Fresh dill", "qty": "1 bunch"}],
        "instructions": ["Roast the potatoes.", "Sear the protein.", "Serve with the asparagus."],
        "default_servings": 4,
    }


_SHRIMP_DETAIL = _detail({"item": "Large shrimp, peeled", "qty": "1.5 lb"})
_SALMON_DETAIL = _detail({"item": "Salmon fillets", "qty": "4"})


def _writer(*details):
    calls = []

    def write(spec):
        calls.append(spec)
        return details[min(len(calls), len(details)) - 1]
    write.calls = calls
    return write


def _setup(status):
    plan = tools.create_weekly_plan(MON, content_start_date=MON, day_count=3)["weekly_plan_id"]
    if status == "approved":
        conn = get_conn()
        try:
            conn.execute("UPDATE weekly_plans SET status = 'approved' WHERE id = ?", (plan,))
            conn.commit()
        finally:
            conn.close()
    tools.add_recipe(SALMON, [{"item": i, "qty": q, "category": "other"} for i, q in _SALMON_ITEMS],
                     instructions=["Roast.", "Sear.", "Serve."], default_servings=4,
                     prep_time_minutes=10, cook_time_minutes=25, main_protein="salmon")
    return tools.plan_meal(TUE, SALMON, slot="dinner", weekly_plan_id=plan,
                           add_ingredients_to_grocery_list=(status == "approved"))["entry_id"]


def _dish(entry):
    conn = get_conn()
    try:
        r = conn.execute("SELECT r.name, r.main_protein FROM meal_plan_entries e JOIN recipes r "
                         "ON r.id = e.recipe_id WHERE e.id = ?", (entry,)).fetchone()
        return r["name"], r["main_protein"]
    finally:
        conn.close()


def _lines():
    conn = get_conn()
    try:
        return {r["item"].lower(): r["quantity"] for r in conn.execute(
            "SELECT item, quantity FROM grocery_items WHERE status IN ('needed', 'in_cart')")}
    finally:
        conn.close()


def test_change_recipe_use_shrimp_renames_and_the_list_follows(signed_in, monkeypatch):
    entry = _setup("approved")
    assert any("salmon" in k for k in _lines())
    monkeypatch.setattr(agent, "generate_recipe_details_llm", _writer(_SHRIMP_DETAIL))

    res = signed_in.post("/api/meal-recipe/rewrite", json={"entry_id": entry, "text": "use shrimp instead"})

    assert res.status_code == 200, res.text
    body = res.json()
    assert body["status"] == "rewritten" and body["renamed"] is True
    assert body["name"] == SHRIMP
    assert body["said"].startswith(f"{SALMON} is now {SHRIMP}.")
    assert _dish(entry) == (SHRIMP, "shrimp")
    lines = _lines()
    assert not any("salmon" in k for k in lines), lines
    shrimp = [q for k, q in lines.items() if "shrimp" in k]
    assert shrimp and shrimp[0], lines

    # Put it back restores the name with the recipe.
    undo = signed_in.post("/api/meal-recipe/undo", json={"request_id": body["request_id"]})
    assert undo.status_code == 200 and undo.json()["status"] == "undone", undo.text
    assert _dish(entry) == (SALMON, "salmon")


def test_tweak_something_else_renames_a_draft_dinner(signed_in, monkeypatch):
    entry = _setup("draft")
    monkeypatch.setattr(agent, "generate_recipe_details_llm", _writer(_SHRIMP_DETAIL))

    res = signed_in.post("/api/meal-recipe/rewrite",
                         json={"entry_id": entry, "text": "can we have it with shrimp?", "this_meal_only": True})

    assert res.status_code == 200, res.text
    assert res.json()["status"] == "rewritten"
    assert _dish(entry) == (SHRIMP, "shrimp")


def test_a_change_that_keeps_the_protein_keeps_the_name(signed_in, monkeypatch):
    entry = _setup("draft")
    monkeypatch.setattr(agent, "generate_recipe_details_llm", _writer(_SALMON_DETAIL))

    res = signed_in.post("/api/meal-recipe/rewrite", json={"entry_id": entry, "text": "less dill, no onions"})

    assert res.json()["status"] == "rewritten", res.text
    assert res.json()["renamed"] is False
    assert _dish(entry) == (SALMON, "salmon")


def test_a_rewrite_that_ignores_the_protein_asked_for_says_so(signed_in, monkeypatch):
    entry = _setup("draft")
    writer = _writer(_SALMON_DETAIL)
    monkeypatch.setattr(agent, "generate_recipe_details_llm", writer)

    res = signed_in.post("/api/meal-recipe/rewrite", json={"entry_id": entry, "text": "use shrimp instead"})

    body = res.json()
    assert len(writer.calls) == 2  # asked once more, naming what it left out
    assert body["status"] == "blocked"
    assert "shrimp" in body["said"] and "as it was" in body["said"]
    assert _dish(entry) == (SALMON, "salmon")


def test_a_protein_somebody_cannot_have_is_named_plainly(signed_in, monkeypatch):
    tools.add_member("Reid")
    tools.set_member_dietary_restrictions("Reid", ["allergy: shellfish"])
    entry = _setup("draft")
    monkeypatch.setattr(agent, "generate_recipe_details_llm", _writer(_SHRIMP_DETAIL))

    res = signed_in.post("/api/meal-recipe/rewrite", json={"entry_id": entry, "text": "use shrimp instead"})

    body = res.json()
    assert body["status"] == "blocked", body
    assert _dish(entry) == (SALMON, "salmon")
