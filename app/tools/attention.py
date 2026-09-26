"""
The needs-attention queue.
"""
from __future__ import annotations

import json
from ..db import get_conn
from ._shared import household_id, require_household_row
from . import coordination as _coordination
from . import inventory as _inventory


ATTENTION_STATUSES = ("resolved", "dismissed")

# Kinds get_attention_items never returns. See the comment there.
HIDDEN_ATTENTION_KINDS = ("inventory_depletion",)


class InvalidAttentionStatus(ValueError):
    """
    A status outside ATTENTION_STATUSES handed to resolve_attention_item.
    Same marker shape as cooker.InvalidMealStatus, chores.InvalidChoreStatus
    and grocery.InvalidGroceryStatus: a ValueError subclass, so the route's
    except for it MUST come before the plain one or the 404 that means "no
    such item" swallows a refusal that means "that isn't an answer".

    The set is the two answers THIS DOOR gives, deliberately not every value
    the column takes. schema.sql documents it as `pending | resolved |
    dismissed`, and `pending` is add_attention_item's to write — its reopen
    path sets the status back and clears resolved_at in the same statement,
    while this function stamps resolved_at unconditionally. So a 'pending'
    let through here would leave a waiting question carrying the time it was
    answered, which is the sort of half-written row the guard exists to stop.

    Reaching it needs a hand-made request: the screens send the two, and the
    chat tool's own schema enumerates them (there is a test pinning that
    enum, so if it is ever dropped this stops being the only door). Measured
    2026-09-24 before the guard: {"status": "banana"} answered 200, the row
    read banana, and it left the queue exactly as a real answer would —
    recording something no screen has a name for. Nothing is lost by it
    today, which is why this closes a door rather than puts out a fire, and
    nothing heals a row already written that way; a migration inventing
    history is worse than leaving the handful that can only have come from
    somebody's curl.
    """


# The first real multi-item "needs your attention" surface — until now the
# only precedent (get_feedback_nudge) was a single computed check with
# nothing persisted. Built for inventory-depletion matches that are too
# uncertain to act on silently, but kept general (kind + freeform
# detail_json) so other soft nudges can land here later instead of each
# inventing their own one-off pattern.
def add_attention_item(kind: str, summary: str, detail: dict | None = None) -> dict:
    """
    Queue something for later review rather than guessing or silently
    dropping it.

    Dedupe/reopen: when `detail` carries both `entry_id` and `ingredient`
    (cooker.deplete_inventory_for_meal — the only caller that does; the
    other today is tonight.tonight_night_off's "use soon" note, which
    carries neither and so takes the pending-only fallback below), those two
    together name the SAME underlying question across an untick/re-tick of
    the same meal — not just while it is still pending, but after it has
    already been resolved or dismissed too. Found 2026-09-13 while fixing
    check_off_meal's own double-depletion (see its docstring): the old
    dedupe compared `status = 'pending'` only, so once a question was
    answered, the next re-tick queued a brand-new row with no memory of the
    first answer, and answering it again took the ingredient a second time
    (Lettuce 2 heads -> 1 head -> row deleted). Reopening the SAME row
    instead of inserting a fresh one is what lets
    record_attention_item_usage recognize a repeat answer as a REPLACEMENT
    of the first rather than a second, independent depletion — see that
    function's docstring for the other half of this. Anything without both
    keys in `detail` (no caller today) falls back to the original
    pending-only dedupe on kind+summary, so a duplicate-but-unrelated
    ambiguous match still doesn't spam the queue before it's resolved once.
    """
    detail = detail or {}
    entry_id = detail.get("entry_id")
    ingredient = detail.get("ingredient")
    conn = get_conn()
    existing = None
    if entry_id is not None and ingredient is not None:
        for r in conn.execute(
            "SELECT id, status, detail_json FROM attention_items WHERE household_id = ? AND kind = ?",
            (household_id(), kind),
        ).fetchall():
            d = json.loads(r["detail_json"])
            if d.get("entry_id") == entry_id and d.get("ingredient") == ingredient:
                existing = r
                break
    else:
        existing = conn.execute(
            "SELECT id, status, detail_json FROM attention_items WHERE household_id = ? AND kind = ? AND summary = ? AND status = 'pending'",
            (household_id(), kind, summary),
        ).fetchone()

    if existing is not None and existing["status"] == "pending":
        conn.close()
        return {"id": existing["id"], "created": False}

    if existing is not None:
        # Already answered or dismissed once — reopen it rather than
        # inserting a second row for the same question. `detail` overwrites
        # the stale match fields (candidate_item_id etc, in case the
        # inventory match changed since); anything it doesn't carry —
        # crucially `applied`, the previous answer's receipt — survives the
        # merge, since a plain dict update only touches the keys the new
        # call actually supplies.
        merged = {**json.loads(existing["detail_json"]), **detail}
        conn.execute(
            "UPDATE attention_items SET status = 'pending', summary = ?, detail_json = ?, resolved_at = NULL WHERE id = ?",
            (summary, json.dumps(merged), existing["id"]),
        )
        conn.commit()
        conn.close()
        return {"id": existing["id"], "created": False, "reopened": True}

    cur = conn.execute(
        "INSERT INTO attention_items (household_id, kind, summary, detail_json) VALUES (?, ?, ?, ?)",
        (household_id(), kind, summary, json.dumps(detail)),
    )
    conn.commit()
    item_id = cur.lastrowid
    conn.close()
    return {"id": item_id, "created": True}


def resolve_attention_item(item_id: int, status: str = "resolved") -> dict:
    """Mark a queued attention item 'resolved' (handled) or 'dismissed' (not relevant/skip it) — either way it stops showing up in get_attention_items."""
    # Above get_conn on purpose: a status that is not an answer never opens a
    # connection and never takes the write lock, and a caller that skips the
    # route entirely — chat, a script — is held to the same two words.
    if status not in ATTENTION_STATUSES:
        raise InvalidAttentionStatus(
            f"{status!r} isn't an answer to a queued item. "
            f"Use one of: {', '.join(ATTENTION_STATUSES)}."
        )
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

    A REPEAT answer to the same question — reached through an untick then
    re-tick of the same meal, which add_attention_item now reopens onto
    this same row instead of queuing a fresh one — REPLACES the first
    answer rather than subtracting on top of what it already took (found
    2026-09-13: Lettuce 2 heads -> answer "1" -> 1 head -> untick -> re-tick
    -> answer "1" again -> row deleted, i.e. subtracted from 1 a second
    time). The proof is the same shape grocery.mark_grocery_item's re-tick
    uses: this item's own detail_json remembers the quantity BEFORE the
    very first answer and the row's inventory_items.rev right after the
    write that answer made. A repeat answer recomputes from that same
    original quantity — never from whatever the shelf reads right now —
    but ONLY while the row still reads exactly what that write left it at;
    the moment something else has touched the row since (a manual edit,
    another depletion), the original can no longer be trusted, and this
    answers fresh against the current quantity instead, same as the very
    first time the question is asked.
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
    prior = detail.get("applied") or {}
    inv_row = None
    if candidate_item_id is not None:
        inv_row = conn.execute(
            "SELECT id, item, quantity, rev FROM inventory_items WHERE id = ? AND household_id = ?",
            (candidate_item_id, household_id()),
        ).fetchone()
    conn.close()

    if inv_row is None:
        resolve_attention_item(item_id, "resolved")
        return {"item_id": item_id, "applied": False, "reason": "tracked item no longer exists"}

    # Proven untouched since the prior answer's write -> replace it: recompute
    # from the ORIGINAL quantity (carried forward across every repeat answer,
    # not just the immediately-prior one), never from the current row.
    baseline = inv_row["quantity"] or ""
    replacing = bool(prior) and prior.get("rev_after") == inv_row["rev"]
    if replacing:
        baseline = prior.get("before") or ""

    remaining, reconciled = _inventory._try_subtract_quantity(baseline, amount_used)
    conn = get_conn()
    if remaining is None:
        conn.execute("DELETE FROM inventory_items WHERE id = ?", (inv_row["id"],))
        conn.commit()
        conn.close()
        resolve_attention_item(item_id, "resolved")
        return {
            "item_id": item_id, "applied": True, "item": inv_row["item"], "removed": True,
            "units_reconciled": True, "replaced_prior_answer": replacing,
        }
    conn.execute(
        "UPDATE inventory_items SET quantity = ?, updated_at = datetime('now') WHERE id = ?",
        (remaining, inv_row["id"]),
    )
    conn.commit()
    new_rev = conn.execute("SELECT rev FROM inventory_items WHERE id = ?", (inv_row["id"],)).fetchone()["rev"]
    detail["applied"] = {"before": baseline, "after": remaining, "rev_after": new_rev}
    conn.execute("UPDATE attention_items SET detail_json = ? WHERE id = ?", (json.dumps(detail), item_id))
    conn.commit()
    conn.close()
    resolve_attention_item(item_id, "resolved")
    return {
        "item_id": item_id, "applied": True, "item": inv_row["item"], "quantity": remaining,
        "units_reconciled": reconciled, "replaced_prior_answer": replacing,
    }


def get_attention_items() -> list[dict]:
    """
    The unified "needs your attention" list — combines the feedback nudge
    (a recently-cooked meal with no rating yet, see get_feedback_nudge)
    with persisted queue items (e.g. tonight.tonight_night_off's "use
    soon" note; never inventory-usage questions, see below). Check this proactively near the start of a
    conversation, the same way get_expiring_soon/get_cross_location_duplicates
    are checked, and work anything pending into the reply in one low-key
    way — not an interrogation checklist. Each item has an `id` (None for
    the feedback nudge, since that's computed rather than a real row —
    only pass real ids to resolve_attention_item), `kind`, `summary`, and
    `detail`.
    """
    # A lazy WRITE on the way into a read, the shape retire_expired_drafts
    # and sync_due_staples already take: a portion a night off froze and
    # nothing has planned in four weeks is queued here, once ever, rather
    # than by a scheduler nobody owns. Imported at call time — this module
    # is what freezer_portions queues through. It swallows its own failures.
    from . import freezer_portions as _freezer_portions

    _freezer_portions.sweep_use_soon()
    items = []
    nudge = _coordination.get_feedback_nudge()
    if nudge.get("has_nudge"):
        items.append({
            "id": None,
            "kind": "feedback_nudge",
            # "core loop handoffs, slice 3" item 5 (Emily, 2026-09-05): this
            # used to be written for the agent to read and decide whether to
            # ask ("...hasn't been rated yet — worth asking how it went") —
            # but it's shown verbatim in the Cook screen's attention card
            # too (see cookAttentionHtml's cook-attn-summary), where it read
            # as a status report about the cook rather than something said
            # to them. Rewritten as the actual question.
            "summary": f"How did {nudge['meal']} go?",
            "detail": {"meal": nudge["meal"], "cooked_at": nudge["cooked_at"]},
        })
    # Inventory-usage questions ("How much Garlic did you use for ...?")
    # are never shown (Emily, 2026-09-25: "Assume I made what the recipe
    # called for here, don't ask me"). cooker.deplete_inventory_for_meal no
    # longer queues them; this hides the ones queued before it stopped, on
    # every surface that reads the queue — Cook's card, chat, the morning
    # text. Hidden rather than rewritten to 'dismissed': the rows stay as
    # they were, and nothing the old answer path did is guessed at.
    conn = get_conn()
    rows = conn.execute(
        "SELECT id, kind, summary, detail_json, created_at FROM attention_items "
        "WHERE household_id = ? AND status = 'pending' AND kind NOT IN ({}) "
        "ORDER BY created_at ASC".format(",".join("?" * len(HIDDEN_ATTENTION_KINDS))),
        (household_id(), *HIDDEN_ATTENTION_KINDS),
    ).fetchall()
    conn.close()
    for r in rows:
        items.append({
            "id": r["id"], "kind": r["kind"], "summary": r["summary"],
            "detail": json.loads(r["detail_json"]), "created_at": r["created_at"],
        })
    return items
