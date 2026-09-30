"""
Speed: Swap opens instantly and Change-a-part is quicker (and keeps all
steps) — Loop Board, 2026-09-30.

  * a pick chosen on a DRAFT is saved at once as a pending recipe (no
    write-out wait); on an APPROVED week the full recipe is still written;
  * the Swap sheet's wait line is the measured wait, in both places;
  * the draft asks for the Swap picks ahead (client), one at a time;
  * Change-a-part answers with a diff, applied to the stored recipe, and a
    nine-step recipe keeps all nine steps (it lost everything past the
    sixth because only method[:6] was sent as context).
The model is never called: every asker/writer is injected.
"""
from __future__ import annotations

import datetime
import importlib
import re
from pathlib import Path

import pytest

from conftest import household_today

from app import tools
from app.db import get_conn
from app.tools import plate_parts as pp

sop = importlib.import_module("app.tools.swap_options")

REPO = Path(__file__).resolve().parent.parent
SHELL_JS = (REPO / "static" / "shell.js").read_text(encoding="utf-8")
ONBOARDING = (REPO / "static" / "onboarding.html").read_text(encoding="utf-8")

TODAY = household_today()
WEEK_START = TODAY.isoformat()
DAY1 = WEEK_START
DAY2 = (TODAY + datetime.timedelta(days=1)).isoformat()

NINE = [f"Step {n} of the traybake." for n in range(1, 10)]


@pytest.fixture
def week():
    tools.add_member("Emily")
    tools.add_member("Vineeth")
    tools.add_recipe("Pork Chops", ingredients=[{"item": "Pork chops", "qty": "1 lb", "category": "meat/seafood"}],
                     food_groups=["protein"], prep_time_minutes=10, cook_time_minutes=20)
    tools.add_recipe(
        "Baked Lemon Herb Cod",
        ingredients=[{"item": "Cod fillets", "qty": "1 lb", "category": "meat/seafood"},
                     {"item": "Lemons", "qty": "2", "category": "produce"},
                     {"item": "Baby potatoes", "qty": "1 lb", "category": "produce"},
                     {"item": "Green beans", "qty": "1 bag", "category": "produce"}],
        food_groups=["protein", "vegetable", "carb"], main_protein="cod", instructions=NINE,
        default_servings=2, prep_time_minutes=15, cook_time_minutes=30,
    )
    plan_id = tools.create_weekly_plan(WEEK_START)["weekly_plan_id"]
    tools.plan_meal(DAY1, "Pork Chops", slot="dinner", weekly_plan_id=plan_id, reasoning="fits")
    tools.plan_meal(DAY2, "Baked Lemon Herb Cod", slot="dinner", weekly_plan_id=plan_id, reasoning="fits")
    sop._OPTIONS_CACHE.clear()
    pp._OPTIONS_CACHE.clear()
    return plan_id


def _entry_id(plan_id, day):
    conn = get_conn()
    row = conn.execute("SELECT id FROM meal_plan_entries WHERE weekly_plan_id = ? AND date = ? AND slot = 'dinner'",
                       (plan_id, day)).fetchone()
    conn.close()
    return row["id"]


def _approve(plan_id):
    conn = get_conn()
    conn.execute("UPDATE weekly_plans SET status = 'approved' WHERE id = ?", (plan_id,))
    conn.commit()
    conn.close()


def _option(name="Lemon chicken traybake"):
    return {"meal_name": name, "reason": "Lighter than the chops, and nothing to thaw.", "minutes": 30,
            "ingredients": ["Chicken thighs", "Lemons", "Baby potatoes"]}


def _recipe_row(name):
    conn = get_conn()
    row = conn.execute("SELECT details_pending, dish_note, ingredients_json, instructions_json FROM recipes WHERE name = ?",
                       (name,)).fetchone()
    conn.close()
    return row


def _no_writer(ctx, pick):
    raise AssertionError("a draft pick must not be written out before approval")


# ---------- a draft pick is saved as a pending recipe ----------

def test_on_a_draft_the_chosen_pick_lands_at_once_as_a_pending_recipe(week):
    entry_id = _entry_id(week, DAY1)
    tools.swap_options(week, entry_id, asker=lambda ctx: [_option()])
    out = sop.choose_swap_option(week, entry_id, 0, writer=_no_writer)
    assert out["status"] == "swapped" and out["meal"] == "Lemon chicken traybake"
    row = _recipe_row("Lemon chicken traybake")
    assert row["details_pending"] == 1
    assert "Lighter than the chops" in row["dish_note"] and "Chicken thighs" in row["dish_note"]
    assert row["ingredients_json"] == "[]" and row["instructions_json"] == "[]"
    # It is planned, and the approval's own pass (agent.fill_pending_recipes_for_plan)
    # finds it — that is where the write-up happens, with the others.
    assert [r["name"] for r in tools.pending_recipes_for_plan(week)] == ["Lemon chicken traybake"]


def test_a_pick_the_household_already_has_is_not_made_pending(week):
    tools.add_recipe("Fish Tacos", ingredients=[{"item": "Cod", "qty": "1 lb", "category": "meat/seafood"}],
                     food_groups=["protein"], instructions=["Fry.", "Fill."])
    entry_id = _entry_id(week, DAY1)
    tools.swap_options(week, entry_id, asker=lambda ctx: [_option("Fish Tacos")])
    assert sop.choose_swap_option(week, entry_id, 0, writer=_no_writer)["status"] == "swapped"
    assert _recipe_row("Fish Tacos")["details_pending"] == 0


def test_on_an_approved_week_the_full_recipe_is_still_written(week):
    _approve(week)
    entry_id = _entry_id(week, DAY1)
    tools.swap_options(week, entry_id, asker=lambda ctx: [_option()])
    asked = []

    def writer(ctx, pick):
        asked.append(pick["meal_name"])
        return {"ingredients": [{"item": "Chicken thighs", "qty": "1 lb", "category": "meat/seafood"}],
                "instructions": ["Roast it."], "food_groups": ["protein"]}

    out = sop.choose_swap_option(week, entry_id, 0, writer=writer)
    assert out["status"] == "swapped" and asked == ["Lemon chicken traybake"]
    row = _recipe_row("Lemon chicken traybake")
    assert row["details_pending"] == 0 and "Chicken thighs" in row["ingredients_json"]


# ---------- the wait line matches the wait ----------

def test_the_wait_copy_is_the_measured_wait_in_both_places():
    seconds = int(re.search(r"var SWAP_WAIT_SECONDS = (\d+);", SHELL_JS).group(1))
    assert seconds == 5, "production measured the picks call at 5.3 s; it was 10 when it read 10.5-13 s"
    words = {5: "five"}[seconds]
    assert f"about {words} seconds" in ONBOARDING
    assert "about ten seconds" not in ONBOARDING and "about ten seconds" not in SHELL_JS.split("function swapWaitLine")[1][:600]


# ---------- picks fetched ahead ----------

def test_the_draft_asks_for_swap_picks_ahead_one_at_a_time_and_only_for_rows_in_view():
    assert "function prefetchSwapPicks(" in SHELL_JS and "prefetchSwapPicks(steps);" in SHELL_JS
    body = SHELL_JS.split("function prefetchSwapPicks(")[1].split("// opts.wholeDish")[0]
    assert "IntersectionObserver" in body, "a row scrolling into view, not every row at once"
    queue = SHELL_JS.split("function prefetchSwapQueue(")[1].split("function prefetchSwapPicks(")[0]
    assert "!== 'draft'" in queue and "PREFETCH_SWAP_MAX" in queue
    assert "st.asked[key]" in queue, "a row is asked once a sitting"
    run = SHELL_JS.split("function prefetchSwapRun(")[1].split("function prefetchSwapQueue(")[0]
    assert "st.running" in run and "/swap-options" in run, "one call in flight at a time, the sheet's own route"
    assert int(re.search(r"var PREFETCH_SWAP_MAX = (\d+);", SHELL_JS).group(1)) <= 8


def test_a_second_ask_for_the_same_slot_is_answered_from_the_cache(week):
    entry_id = _entry_id(week, DAY1)
    calls = []

    def ask(ctx):
        calls.append(1)
        return [_option()]

    tools.swap_options(week, entry_id, asker=ask)
    out = tools.swap_options(week, entry_id, asker=ask)
    assert len(calls) == 1 and [o["meal"] for o in out["options"]] == ["Lemon chicken traybake"]


# ---------- change-a-part: a diff, and every step kept ----------

def _cod_entry(week):
    return _entry_id(week, DAY2)


def test_the_change_sends_the_whole_method_numbered_and_asks_for_a_diff(week):
    seen = {}

    def asker(ctx):
        seen.update(ctx)
        return {"meal_name": "Baked Lemon Herb Salmon", "main_protein": "salmon", "reason": "Salmon instead of cod.",
                "ingredients_changed": [{"replaces": "Cod fillets", "item": "Salmon fillets", "qty": "1 lb",
                                         "category": "meat/seafood"}]}

    out = pp.change_part(week, _cod_entry(week), "protein", "Salmon", asker=asker)
    assert out["status"] == "changed", out
    assert [s["step"] for s in seen["method"]] == list(range(1, 10)), "all nine steps, not method[:6]"
    assert seen["method"][8]["text"] == "Step 9 of the traybake."
    assert seen["_diff"] is True
    tool = pp.VARIANT_DIFF_TOOL["input_schema"]
    assert "ingredients" not in tool["properties"] and "instructions" not in tool["properties"], (
        "the answer is the changes, not the recipe")
    assert set(tool["required"]) == {"meal_name", "main_protein", "reason"}


def test_a_nine_step_recipe_keeps_all_nine_steps_after_a_change(week):
    def asker(ctx):
        return {"meal_name": "Baked Lemon Herb Salmon", "main_protein": "salmon", "reason": "Salmon instead of cod.",
                "ingredients_changed": [{"replaces": "Cod fillets", "item": "Salmon fillets", "qty": "1 lb",
                                         "category": "meat/seafood"}],
                "steps_changed": [{"step": 4, "text": "Roast the salmon twelve minutes."}]}

    out = pp.change_part(week, _cod_entry(week), "protein", "Salmon", asker=asker)
    assert out["status"] == "changed" and out["meal"] == "Baked Lemon Herb Salmon"
    row = _recipe_row("Baked Lemon Herb Salmon")
    import json
    steps = json.loads(row["instructions_json"])
    assert len(steps) == 9
    assert steps[3] == "Roast the salmon twelve minutes." and steps[8] == "Step 9 of the traybake."
    items = [i["item"] for i in json.loads(row["ingredients_json"])]
    assert items == ["Salmon fillets", "Lemons", "Baby potatoes", "Green beans"], "unchanged rows kept, in place"


def test_the_diff_applies_changed_added_and_removed_rows_and_steps():
    recipe = {"name": "Dish", "ingredients": [{"item": "Cod", "qty": "1"}, {"item": "Lemons", "qty": "2"},
                                               {"item": "Beans", "qty": "1"}],
              "instructions": ["a", "b", "c", "d"], "cuisine": "Greek", "main_protein": "cod",
              "prep_time_minutes": 5, "cook_time_minutes": 20}
    out = pp.apply_variant_diff(recipe, {
        "meal_name": "Dish with salmon", "main_protein": "salmon", "reason": "x",
        "ingredients_changed": [{"replaces": "cod", "item": "Salmon", "qty": "1 lb"},
                                {"replaces": "Nonexistent", "item": "Dill", "qty": "1 bunch"}],
        "ingredients_removed": ["Beans"], "ingredients_added": [{"item": "Capers", "qty": "1 jar"}],
        "steps_changed": [{"step": 2, "text": "B2"}, {"step": 99, "text": "ignored"}],
        "steps_removed": [3], "steps_added": [{"after": 0, "text": "first"}, {"after": 4, "text": "last"}],
        "cook_time_minutes": 12,
    })
    assert out["meal_name"] == "Dish with salmon" and out["main_protein"] == "salmon"
    assert [r["item"] for r in out["ingredients"]] == ["Salmon", "Lemons", "Dill", "Capers"]
    assert out["instructions"] == ["first", "a", "B2", "d", "last"]
    assert out["cuisine"] == "Greek" and out["prep_time_minutes"] == 5 and out["cook_time_minutes"] == 12


def test_a_batch_at_another_size_still_rewrites_the_whole_recipe(week):
    """The unchanged rows of a diff keep the recipe's own amounts, so a
    recipe written for a different number of servings is rewritten whole
    (the full schema) rather than half-scaled."""
    recipe = dict(tools.get_recipe("Baked Lemon Herb Cod"))
    assert pp._can_diff(recipe, recipe["default_servings"]) is True
    assert pp._can_diff(recipe, recipe["default_servings"] + 2) is False
    assert pp._can_diff(dict(recipe, details_pending=True), recipe["default_servings"]) is False
    assert pp._can_diff(dict(recipe, instructions=[]), recipe["default_servings"]) is False
    assert pp._can_diff(None, 2) is False


def test_the_model_call_asks_for_the_diff_tool_and_a_smaller_budget(week, monkeypatch):
    from app import agent
    seen = {}

    class Block:
        type = "tool_use"
        input = {"meal_name": "X", "main_protein": "salmon", "reason": "r"}

    def fake(client, **kwargs):
        seen.update(kwargs)
        return type("R", (), {"content": [Block()], "stop_reason": "tool_use"})()

    monkeypatch.setattr(agent, "_client", lambda: object())
    monkeypatch.setattr(agent, "_create_with_retry", fake)
    pp._ask_variant({"dish": "d", "_diff": True}, "protein")
    assert seen["tools"] == [pp.VARIANT_DIFF_TOOL] and seen["max_tokens"] < 2000
    assert "_diff" not in seen["messages"][0]["content"][1]["text"], "the marker is not told to the model"
    pp._ask_variant({"dish": "d"}, "protein")
    assert seen["tools"] == [pp.VARIANT_TOOL] and seen["max_tokens"] == 2000


# ---------- verifier follow-ups (2026-09-30) ----------

def test_two_asks_for_one_slot_share_one_model_call_and_one_set(week):
    """A prefetch still in flight when the sheet opens: the second ask waits
    and reads the first's answer, so the set shown is the set cached."""
    import contextvars
    import threading
    import time
    entry_id = _entry_id(week, DAY1)
    calls = []

    def slow(ctx):
        calls.append(1)
        time.sleep(0.3)
        return [_option(f"Dish {len(calls)}")]

    results = []

    def run():
        results.append(tools.swap_options(week, entry_id, asker=slow))

    threads = [threading.Thread(target=contextvars.copy_context().run, args=(run,)) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(calls) == 1
    assert [o["meal"] for o in results[0]["options"]] == [o["meal"] for o in results[1]["options"]] == ["Dish 1"]


def test_choose_refuses_when_the_dish_shown_is_not_the_dish_held(week):
    entry_id = _entry_id(week, DAY1)
    tools.swap_options(week, entry_id, asker=lambda ctx: [_option("Lemon chicken traybake")])
    with pytest.raises(ValueError, match="aren't on offer any more"):
        sop.choose_swap_option(week, entry_id, 0, writer=_no_writer, meal="Some other dish")
    assert _recipe_row("Some other dish") is None and _recipe_row("Lemon chicken traybake") is None
    out = sop.choose_swap_option(week, entry_id, 0, writer=_no_writer, meal="lemon chicken traybake ")
    assert out["status"] == "swapped" and out["meal"] == "Lemon chicken traybake"


def test_the_sheet_sends_the_dish_it_showed_and_waits_for_a_prefetch_in_flight():
    choose = SHELL_JS.split("'/swap-choose'")[1][:300]
    assert "meal: picked.meal" in choose
    opened = SHELL_JS.split("async function openSwapSheet(")[1].split("try {")[0]
    assert "await prefetchSwapSettle(" in opened
    settle = SHELL_JS.split("async function prefetchSwapSettle(")[1].split("\n  }\n")[0]
    assert "st.inflight[key]" in settle and "st.queue.filter" in settle
    picks = SHELL_JS.split("function prefetchSwapPicks(")[1].split("// opts.wholeDish")[0]
    assert picks.index("observer.disconnect()") < picks.index("new IntersectionObserver("), "the old observer goes on re-render"


def test_a_name_only_diff_is_not_a_change_and_the_whole_recipe_is_asked_for(week):
    contexts = []
    full = {"meal_name": "Baked Lemon Herb Salmon", "main_protein": "salmon", "reason": "Salmon instead of cod.",
            "ingredients": [{"item": "Salmon fillets", "qty": "1 lb", "category": "meat/seafood"}],
            "instructions": NINE, "food_groups": ["protein"]}

    def asker(ctx):
        contexts.append(dict(ctx))
        if ctx.get("_diff"):
            return {"meal_name": "Baked Lemon Herb Salmon", "main_protein": "salmon", "reason": "Salmon."}
        return full

    out = pp.change_part(week, _cod_entry(week), "protein", "Salmon", asker=asker)
    assert out["status"] == "changed"
    assert len(contexts) == 2 and contexts[0].get("_diff") and "_diff" not in contexts[1]
    assert "Salmon fillets" in _recipe_row("Baked Lemon Herb Salmon")["ingredients_json"]


def test_a_diff_is_one_row_per_item_and_a_removal_cannot_take_a_replaced_row():
    recipe = {"name": "D", "ingredients": [{"item": "Cod", "qty": "1"}, {"item": "Lemons", "qty": "2"}],
              "instructions": ["a"]}
    out = pp.apply_variant_diff(recipe, {
        "meal_name": "D2", "main_protein": "salmon", "reason": "x",
        "ingredients_changed": [{"replaces": "Cod", "item": "Salmon", "qty": "1 lb"}],
        "ingredients_added": [{"item": "salmon", "qty": "2 lb"}, {"item": "Dill", "qty": "1"}],
        "ingredients_removed": ["Cod", "Salmon"],
    })
    assert [r["item"] for r in out["ingredients"]] == ["Salmon", "Lemons", "Dill"]
    assert out["ingredients"][0]["qty"] == "1 lb"
