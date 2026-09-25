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

SINCE 2026-09-25 the question is never asked (Emily: "Assume I made what
the recipe called for here, don't ask me"). deplete_inventory_for_meal
queues nothing, and get_attention_items hides inventory_depletion rows, so
the end-to-end tick -> answer -> re-tick tests that lived here are gone —
tests/test_cook_never_asks_usage.py pins the new behaviour. What stays is
the queue's own dedupe/reopen and the answer path, which are still code.
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

