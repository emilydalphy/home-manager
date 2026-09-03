"""
The needs-attention queue.
"""
from __future__ import annotations

import json
from ..db import get_conn
from ._shared import household_id, require_household_row
from . import coordination as _coordination
from . import inventory as _inventory


# The first real multi-item "needs your attention" surface — until now the
# only precedent (get_feedback_nudge) was a single computed check with
# nothing persisted. Built for inventory-depletion matches that are too
# uncertain to act on silently, but kept general (kind + freeform
# detail_json) so other soft nudges can land here later instead of each
# inventing their own one-off pattern.
def add_attention_item(kind: str, summary: str, detail: dict | None = None) -> dict:
    """
    Queue something for later review rather than guessing or silently
    dropping it. Skips creating a duplicate if a pending item with the same
    kind+summary already exists, so the same ambiguous match doesn't spam
    the queue every time it's encountered again before being resolved.
    """
    conn = get_conn()
    existing = conn.execute(
        "SELECT id FROM attention_items WHERE household_id = ? AND kind = ? AND summary = ? AND status = 'pending'",
        (household_id(), kind, summary),
    ).fetchone()
    if existing:
        conn.close()
        return {"id": existing["id"], "created": False}
    cur = conn.execute(
        "INSERT INTO attention_items (household_id, kind, summary, detail_json) VALUES (?, ?, ?, ?)",
        (household_id(), kind, summary, json.dumps(detail or {})),
    )
    conn.commit()
    item_id = cur.lastrowid
    conn.close()
    return {"id": item_id, "created": True}


def resolve_attention_item(item_id: int, status: str = "resolved") -> dict:
    """Mark a queued attention item 'resolved' (handled) or 'dismissed' (not relevant/skip it) — either way it stops showing up in get_attention_items."""
    conn = get_conn()
    require_household_row(conn, "attention_items", item_id, label="attention item")
    conn.execute(
        "UPDATE attention_items SET status = ?, resolved_at = datetime('now') WHERE id = ? AND household_id = ?",
        (status, item_id, household_id()),
    )
    conn.commit()
    conn.close()
    return {"id": item_id, "status": status}


def record_attention_item_usage(item_id: int, amount_used: str = "") -> dict:
    """
    Resolve a "needs_amount_used" inventory_depletion attention item by
    applying the amount the person says they actually used, rather than
    asking them to instead go figure out and report what's left (less
    intuitive in the moment, right after cooking). Reuses the same
    lenient subtract logic as the general chat "use" flow (_try_subtract_
    quantity) — appropriate here because, unlike the original automated
    depletion attempt, this IS an explicit person-confirmed amount, so an
    unparseable/freeform tracked quantity can safely be treated as "used
    it all" rather than queued again. Leaving amount_used blank means
    "used all of it," same convention as update_inventory's "use" action.
    Marks the attention item resolved either way (even if the candidate
    row no longer exists) so it doesn't stay stuck in the queue.
    """
    conn = get_conn()
    row = conn.execute(
        "SELECT id, detail_json FROM attention_items WHERE id = ? AND household_id = ? AND status = 'pending'",
        (item_id, household_id()),
    ).fetchone()
    if not row:
        conn.close()
        return {"item_id": item_id, "applied": False, "reason": "not found or already resolved"}
    detail = json.loads(row["detail_json"])
    candidate_item_id = detail.get("candidate_item_id")
    inv_row = None
    if candidate_item_id is not None:
        inv_row = conn.execute(
            "SELECT id, item, quantity FROM inventory_items WHERE id = ? AND household_id = ?",
            (candidate_item_id, household_id()),
        ).fetchone()
    conn.close()

    if inv_row is None:
        resolve_attention_item(item_id, "resolved")
        return {"item_id": item_id, "applied": False, "reason": "tracked item no longer exists"}

    remaining, reconciled = _inventory._try_subtract_quantity(inv_row["quantity"] or "", amount_used)
    conn = get_conn()
    if remaining is None:
        conn.execute("DELETE FROM inventory_items WHERE id = ?", (inv_row["id"],))
        conn.commit()
        conn.close()
        resolve_attention_item(item_id, "resolved")
        return {"item_id": item_id, "applied": True, "item": inv_row["item"], "removed": True, "units_reconciled": True}
    conn.execute(
        "UPDATE inventory_items SET quantity = ?, updated_at = datetime('now') WHERE id = ?",
        (remaining, inv_row["id"]),
    )
    conn.commit()
    conn.close()
    resolve_attention_item(item_id, "resolved")
    return {"item_id": item_id, "applied": True, "item": inv_row["item"], "quantity": remaining, "units_reconciled": reconciled}


def get_attention_items() -> list[dict]:
    """
    The unified "needs your attention" list — combines the feedback nudge
    (a recently-cooked meal with no rating yet, see get_feedback_nudge)
    with persisted queue items (currently: low-confidence
    ingredient-to-inventory matches from checking a meal off as cooked, see
    check_off_meal). Check this proactively near the start of a
    conversation, the same way get_expiring_soon/get_cross_location_duplicates
    are checked, and work anything pending into the reply in one low-key
    way — not an interrogation checklist. Each item has an `id` (None for
    the feedback nudge, since that's computed rather than a real row —
    only pass real ids to resolve_attention_item), `kind`, `summary`, and
    `detail`.
    """
    items = []
    nudge = _coordination.get_feedback_nudge()
    if nudge.get("has_nudge"):
        items.append({
            "id": None,
            "kind": "feedback_nudge",
            "summary": f"{nudge['meal']} was cooked recently and hasn't been rated yet — worth asking how it went.",
            "detail": {"meal": nudge["meal"], "cooked_at": nudge["cooked_at"]},
        })
    conn = get_conn()
    rows = conn.execute(
        "SELECT id, kind, summary, detail_json, created_at FROM attention_items WHERE household_id = ? AND status = 'pending' ORDER BY created_at ASC",
        (household_id(),),
    ).fetchall()
    conn.close()
    for r in rows:
        items.append({
            "id": r["id"], "kind": r["kind"], "summary": r["summary"],
            "detail": json.loads(r["detail_json"]), "created_at": r["created_at"],
        })
    return items
