"""
Loop Board 19a (Emily, 2026-09-05): stores are asked just-in-time on the
Grocery tab's first real trip ("Where do you usually shop?"), not during
onboarding. The card itself is a shell.js concern (see groStoresPromptHtml/
groStoresPromptShouldShow in static/shell.js) — these tests cover its
backend half: the memory payload the card reads to decide whether to show,
the existing write path it saves through, and the quiet dismissal it
persists.
"""
from app import tools
from app.db import get_conn
from app.tools._shared import household_id


def test_memory_defaults_no_usual_stores_and_not_dismissed():
    memory = tools.get_household_memory()
    assert memory["usual_stores"] == []
    assert memory["stores_prompt_dismissed"] is False


def test_edit_preference_usual_stores_writes_the_field():
    result = tools.edit_preference("usual_stores", ["Costco", "No Frills"])
    assert result == {"usual_stores": ["Costco", "No Frills"]}

    memory = tools.get_household_memory()
    assert memory["usual_stores"] == ["Costco", "No Frills"]
    # Picking a store must never silently dismiss the prompt on its own —
    # dismissal is its own explicit action ("One list is fine").
    assert memory["stores_prompt_dismissed"] is False


def test_add_usual_stores_merges_rather_than_replacing():
    tools.add_usual_stores(["Costco"])
    tools.add_usual_stores(["Metro"])
    memory = tools.get_household_memory()
    assert memory["usual_stores"] == ["Costco", "Metro"]


def test_dismiss_stores_prompt_persists():
    memory = tools.get_household_memory()
    assert memory["stores_prompt_dismissed"] is False

    result = tools.dismiss_stores_prompt()
    assert result == {"dismissed": True}

    memory = tools.get_household_memory()
    assert memory["stores_prompt_dismissed"] is True
    # Dismissing declines the question — it must not silently invent a
    # usual store as a side effect.
    assert memory["usual_stores"] == []


def test_dismiss_stores_prompt_survives_a_later_preference_write():
    tools.dismiss_stores_prompt()
    # An unrelated preference write later in the same household shouldn't
    # clobber the dismissal timestamp (both funnel through the same
    # meal_preferences upsert-by-household_id row).
    tools.edit_preference("cooking_time_preference", "quick")
    memory = tools.get_household_memory()
    assert memory["stores_prompt_dismissed"] is True


def test_dismiss_stores_prompt_sets_a_real_timestamp_column():
    tools.dismiss_stores_prompt()
    conn = get_conn()
    row = conn.execute(
        "SELECT stores_prompt_dismissed_at FROM meal_preferences WHERE household_id = ?",
        (household_id(),),
    ).fetchone()
    conn.close()
    assert row["stores_prompt_dismissed_at"] != ""


# ---------- HTTP: the two calls the Grocery tab card actually makes ----------

def test_memory_edit_usual_stores_over_http(signed_in):
    res = signed_in.post("/api/memory/edit", json={"field": "usual_stores", "value": ["Loblaws"]})
    assert res.status_code == 200
    assert res.json()["usual_stores"] == ["Loblaws"]


def test_stores_prompt_dismiss_endpoint_persists_over_http(signed_in):
    before = signed_in.get("/api/memory").json()
    assert before["stores_prompt_dismissed"] is False

    res = signed_in.post("/api/memory/stores-prompt-dismiss")
    assert res.status_code == 200
    assert res.json()["stores_prompt_dismissed"] is True

    after = signed_in.get("/api/memory").json()
    assert after["stores_prompt_dismissed"] is True
