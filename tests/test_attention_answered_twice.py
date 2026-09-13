"""
Answering the same "how much did you use?" question twice takes the food
out twice.

THE BUG (found 2026-09-13 while fixing check_off_meal's own double
depletion — see tests/test_cook_tick_double_depletes.py and the CLAUDE.md
decision-log entry for that fix, "Two things left alone", item 2).
cooker.deplete_inventory_for_meal queues a "how much did you use?" question
via attention.add_attention_item whenever a confident ingredient match has
no recipe-stated quantity. add_attention_item's old dedupe compared
`status = 'pending'` only, so once the household answered the question it
stopped existing as far as the dedupe was concerned — a later untick then
re-tick of the same meal (an ordinary tap sequence, and the one
check_off_meal's own fix is built entirely around) ran the depletion pass
again, found the SAME confident match with no stated quantity, and queued
a brand-new attention item with no memory of the first answer. Answering
that one applied the person's amount against whatever the shelf currently
read — which already reflected the first answer — so the second answer
subtracted a second time from a single real use of the ingredient:

    Lettuce 2 heads -> tick -> answer "1" -> 1 head -> untick -> re-tick
    -> answer "1" again -> row deleted

THE FIX, same shape as check_off_meal's and grocery.mark_grocery_item's
re-tick fixes: status forgets, so the question has to be recognized by
what it's ABOUT (attention.add_attention_item now keys the dedupe on
entry_id + ingredient, not kind + summary, whenever both are present in
`detail`) rather than by its live/dead status, and a repeat answer has to
REPLACE the first rather than compound onto it (attention.
record_attention_item_usage now recomputes from the quantity recorded
before the very first answer, proven still current via inventory_items.rev,
instead of subtracting from whatever the shelf reads right now).
"""
from __future__ import annotations

import json

from app import tools
from app.db import get_conn

DAY = "2026-01-05"  # any date; nothing here is date-sensitive


def _stock_lettuce(quantity="2 heads"):
    tools.update_inventory("Lettuce", "add", quantity=quantity, category="produce")


def _lettuce():
    rows = [r for r in tools.get_inventory() if r["item"].lower() == "lettuce"]
    assert len(rows) <= 1, rows
    return rows[0] if rows else None


def _no_qty_recipe(name="Salad Wraps"):
    """A recipe whose ingredient names the item but not how much of it —
    the case deplete_inventory_for_meal cannot silently deplete and must
    ask about (see its own docstring)."""
    tools.add_recipe(name, ingredients=[{"item": "Lettuce", "qty": ""}], default_servings=1)
    return name


def _entry(name="Salad Wraps"):
    return tools.plan_meal(DAY, name, slot="dinner")["entry_id"]


def _pending_amount_item(entry_id):
    for item in tools.get_attention_items():
        if item.get("id") is None:
            continue
        detail = item["detail"]
        if detail.get("entry_id") == entry_id and detail.get("needs_amount_used"):
            return item
    return None


def _raw_row(item_id):
    conn = get_conn()
    row = conn.execute(
        "SELECT status, detail_json FROM attention_items WHERE id = ?", (item_id,)
    ).fetchone()
    conn.close()
    return row


def _rev(inventory_id):
    conn = get_conn()
    row = conn.execute("SELECT rev FROM inventory_items WHERE id = ?", (inventory_id,)).fetchone()
    conn.close()
    return row["rev"] if row else None


# ---------- the reproduction, exactly as the card describes it ----------

def test_tick_answer_untick_retick_answer_takes_the_lettuce_once():
    _stock_lettuce("2 heads")
    _no_qty_recipe()
    entry = _entry()

    tools.check_off_meal(entry, "done")
    first = _pending_amount_item(entry)
    assert first is not None, "expected a 'how much did you use?' question"
    tools.record_attention_item_usage(first["id"], "1 head")
    assert _lettuce()["quantity"] == "1 head"

    tools.check_off_meal(entry, "pending")
    tools.check_off_meal(entry, "done")

    second = _pending_amount_item(entry)
    assert second is not None, "re-ticking should ask again, not silently drop it"
    assert second["id"] == first["id"], "the re-tick must reopen the same question, not queue a new one"

    tools.record_attention_item_usage(second["id"], "1 head")
    lettuce = _lettuce()
    assert lettuce is not None, "the row must not be deleted by a second answer to the same use"
    assert lettuce["quantity"] == "1 head"


def test_the_sequence_survives_several_untick_retick_rounds():
    """Not just once: however many times the box is toggled, one real
    answer's worth of lettuce is what's actually gone."""
    _stock_lettuce("2 heads")
    _no_qty_recipe()
    entry = _entry()

    for _ in range(4):
        tools.check_off_meal(entry, "done")
        item = _pending_amount_item(entry)
        assert item is not None
        tools.record_attention_item_usage(item["id"], "1 head")
        assert _lettuce()["quantity"] == "1 head"
        tools.check_off_meal(entry, "pending")

    tools.check_off_meal(entry, "done")
    assert _lettuce()["quantity"] == "1 head"


def test_a_different_second_answer_replaces_the_first_rather_than_adding_to_it():
    """Answering "1 head" then, after a re-tick, correcting to "2 heads"
    used lands on 0 heads used total (i.e. the row is gone) — the SECOND
    answer's own arithmetic against the true original quantity, not
    2 - 1 - 2."""
    _stock_lettuce("2 heads")
    _no_qty_recipe()
    entry = _entry()

    tools.check_off_meal(entry, "done")
    first = _pending_amount_item(entry)
    tools.record_attention_item_usage(first["id"], "1 head")
    assert _lettuce()["quantity"] == "1 head"

    tools.check_off_meal(entry, "pending")
    tools.check_off_meal(entry, "done")
    second = _pending_amount_item(entry)
    assert second["id"] == first["id"]

    tools.record_attention_item_usage(second["id"], "2 heads")
    assert _lettuce() is None  # 2 heads original - 2 heads used = none left


# ---------- add_attention_item: the dedupe/reopen itself ----------

def test_add_attention_item_reopens_a_resolved_item_for_the_same_entry_and_ingredient():
    first = tools.add_attention_item(
        "inventory_depletion", "How much lettuce did you use?",
        {"entry_id": 42, "ingredient": "Lettuce", "candidate_item_id": 7, "needs_amount_used": True},
    )
    assert first["created"] is True
    tools.resolve_attention_item(first["id"], "resolved")

    again = tools.add_attention_item(
        "inventory_depletion", "How much lettuce did you use? (1 head left)",
        {"entry_id": 42, "ingredient": "Lettuce", "candidate_item_id": 7, "needs_amount_used": True},
    )
    assert again["id"] == first["id"]
    assert again["created"] is False
    assert again.get("reopened") is True
    row = _raw_row(first["id"])
    assert row["status"] == "pending"


def test_add_attention_item_reopens_a_dismissed_item_too():
    first = tools.add_attention_item(
        "inventory_depletion", "Was this the tracked garlic?",
        {"entry_id": 9, "ingredient": "Garlic", "candidate_item_id": 3},
    )
    tools.resolve_attention_item(first["id"], "dismissed")
    again = tools.add_attention_item(
        "inventory_depletion", "Was this the tracked garlic?",
        {"entry_id": 9, "ingredient": "Garlic", "candidate_item_id": 3},
    )
    assert again["id"] == first["id"]
    assert _raw_row(first["id"])["status"] == "pending"


def test_add_attention_item_does_not_reopen_a_different_ingredient_or_entry():
    lettuce_item = tools.add_attention_item(
        "inventory_depletion", "lettuce q", {"entry_id": 1, "ingredient": "Lettuce"}
    )
    tools.resolve_attention_item(lettuce_item["id"], "resolved")
    other_ingredient = tools.add_attention_item(
        "inventory_depletion", "onion q", {"entry_id": 1, "ingredient": "Onion"}
    )
    other_entry = tools.add_attention_item(
        "inventory_depletion", "lettuce q, other meal", {"entry_id": 2, "ingredient": "Lettuce"}
    )
    assert other_ingredient["created"] is True
    assert other_entry["created"] is True
    assert other_ingredient["id"] != lettuce_item["id"]
    assert other_entry["id"] != lettuce_item["id"]


def test_add_attention_item_still_dedupes_a_pending_duplicate_without_double_queueing():
    first = tools.add_attention_item(
        "inventory_depletion", "How much lettuce?", {"entry_id": 5, "ingredient": "Lettuce"}
    )
    again = tools.add_attention_item(
        "inventory_depletion", "How much lettuce?", {"entry_id": 5, "ingredient": "Lettuce"}
    )
    assert again["id"] == first["id"]
    assert again["created"] is False
    assert not again.get("reopened")  # still pending — nothing to reopen


def test_reopen_preserves_the_prior_answers_receipt():
    """The `applied` receipt a real answer writes (see
    record_attention_item_usage) must survive being merged back in on
    reopen — it's the only way the second answer can know the original
    quantity."""
    first = tools.add_attention_item(
        "inventory_depletion", "q", {"entry_id": 11, "ingredient": "Lettuce", "candidate_item_id": 99}
    )
    conn = get_conn()
    conn.execute(
        "UPDATE attention_items SET detail_json = ?, status = 'resolved' WHERE id = ?",
        (json.dumps({
            "entry_id": 11, "ingredient": "Lettuce", "candidate_item_id": 99,
            "applied": {"before": "2 heads", "after": "1 head", "rev_after": 3},
        }), first["id"]),
    )
    conn.commit()
    conn.close()

    again = tools.add_attention_item(
        "inventory_depletion", "q (1 head left)",
        {"entry_id": 11, "ingredient": "Lettuce", "candidate_item_id": 99},
    )
    assert again["id"] == first["id"]
    detail = json.loads(_raw_row(first["id"])["detail_json"])
    assert detail["applied"] == {"before": "2 heads", "after": "1 head", "rev_after": 3}


# ---------- record_attention_item_usage: the replace itself ----------

def test_first_answer_writes_a_receipt_the_replace_relies_on():
    _stock_lettuce("2 heads")
    lettuce = _lettuce()
    item = tools.add_attention_item(
        "inventory_depletion", "q",
        {"entry_id": 1, "ingredient": "Lettuce", "candidate_item_id": lettuce["id"], "needs_amount_used": True},
    )
    tools.record_attention_item_usage(item["id"], "1 head")
    detail = json.loads(_raw_row(item["id"])["detail_json"])
    applied = detail["applied"]
    assert applied["before"] == "2 heads"
    assert applied["after"] == "1 head"
    assert applied["rev_after"] == _rev(lettuce["id"])


def test_replace_only_fires_when_the_row_still_reads_what_the_first_answer_left():
    """If the household edits the tracked row between the two answers, the
    original quantity can no longer be trusted, so the second answer must
    NOT restore-and-replace — it answers fresh against the current
    quantity, the same as the very first time."""
    _stock_lettuce("2 heads")
    lettuce = _lettuce()
    item = tools.add_attention_item(
        "inventory_depletion", "q",
        {"entry_id": 1, "ingredient": "Lettuce", "candidate_item_id": lettuce["id"], "needs_amount_used": True},
    )
    tools.record_attention_item_usage(item["id"], "1 head")
    assert _lettuce()["quantity"] == "1 head"

    # Reopen (simulating the re-tick) and then the household hand-edits the
    # shelf in between the reopen and the second answer.
    tools.add_attention_item(
        "inventory_depletion", "q (1 head left)",
        {"entry_id": 1, "ingredient": "Lettuce", "candidate_item_id": lettuce["id"], "needs_amount_used": True},
    )
    tools.update_inventory("Lettuce", "set", quantity="3 heads")  # household restocked by hand

    result = tools.record_attention_item_usage(item["id"], "1 head")
    assert result["replaced_prior_answer"] is False
    assert _lettuce()["quantity"] == "2 heads"  # fresh: 3 - 1, not a guess from "2 heads" original


def test_answering_a_reopened_item_resolves_it_and_stops_it_reappearing():
    _stock_lettuce("2 heads")
    lettuce = _lettuce()
    item = tools.add_attention_item(
        "inventory_depletion", "q",
        {"entry_id": 1, "ingredient": "Lettuce", "candidate_item_id": lettuce["id"], "needs_amount_used": True},
    )
    tools.record_attention_item_usage(item["id"], "1 head")
    tools.add_attention_item(
        "inventory_depletion", "q (1 head left)",
        {"entry_id": 1, "ingredient": "Lettuce", "candidate_item_id": lettuce["id"], "needs_amount_used": True},
    )
    result = tools.record_attention_item_usage(item["id"], "1 head")
    assert result["replaced_prior_answer"] is True
    assert _raw_row(item["id"])["status"] == "resolved"
    assert tools.get_attention_items() == [] or all(
        i.get("id") != item["id"] for i in tools.get_attention_items()
    )


# ---------- a component-based batch re-ticked via a DIFFERENT sibling ----------

def _component_plan(name, count=2):
    """Two entries in one component-based batch sharing `name`, the same
    shape test_cook_tick_double_depletes.py's own _component_plan uses."""
    tools.add_recipe(name, ingredients=[{"item": "Jello", "qty": ""}], default_servings=2)
    tools.set_planning_mode("component_based")
    plan_id = tools.create_weekly_plan(DAY)["weekly_plan_id"]
    ids = [
        tools.plan_meal(DAY, name, weekly_plan_id=plan_id, component_category="treat")["entry_id"]
        for _ in range(count)
    ]
    return ids


def _pending_jello_item():
    for item in tools.get_attention_items():
        if item.get("id") is not None and item["detail"].get("ingredient") == "Jello":
            return item
    return None


def test_reticking_a_component_batch_via_a_different_sibling_still_reopens_the_same_question():
    """
    Found during adversarial review of the fix above (2026-09-13):
    deplete_inventory_for_meal is called with check_off_meal's OWN entry_id
    parameter — whichever sibling was actually tapped THIS time, not
    necessarily the one tapped originally. A component batch's claim gets
    released as soon as nothing was actually reconciled (a no-qty
    ingredient goes to queued_for_review, not depleted), which is exactly
    the untick/re-tick shape this whole file is about — so re-ticking
    through a DIFFERENT sibling checkbox in the same merged card used to
    call deplete_inventory_for_meal with that sibling's own entry_id,
    which is a different dedupe key from the one the first tick used. That
    queued a brand-new "how much Jello did you use?" question with no
    memory of the first answer, and answering it re-subtracted the same
    real use a second time — the reported bug, reached through the one
    door add_attention_item's entry_id+ingredient dedupe cannot close on
    its own, because check_off_meal now always calls
    deplete_inventory_for_meal with the batch's canonical (lowest) linked
    entry_id rather than whichever one was tapped.
    """
    tools.update_inventory("Jello", "add", quantity="2 boxes", category="other")
    tapped_first, tapped_second = _component_plan("Jello Bowl Sibling Test")

    tools.check_off_meal(tapped_first, "done")
    first = _pending_jello_item()
    assert first is not None
    tools.record_attention_item_usage(first["id"], "1 box")
    assert [i["quantity"] for i in tools.get_inventory() if i["item"] == "Jello"] == ["1 box"]

    # Untick and re-tick through the OTHER sibling in the batch.
    tools.check_off_meal(tapped_second, "pending")
    tools.check_off_meal(tapped_second, "done")

    second = _pending_jello_item()
    assert second is not None, "re-ticking the batch should ask again, not silently drop it"
    assert second["id"] == first["id"], (
        "a re-tick through a different sibling must reopen the SAME question, "
        "not queue an unrelated new one"
    )

    tools.record_attention_item_usage(second["id"], "1 box")
    jello = [i for i in tools.get_inventory() if i["item"] == "Jello"]
    assert jello and jello[0]["quantity"] == "1 box", (
        "one real box used across the whole batch, however many taps or "
        "which sibling they landed on — not removed outright by a second "
        "answer to the same use"
    )

