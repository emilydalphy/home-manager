"""
What a tick took out of the kitchen, said out loud — and put back on Undo.

Inventory card A2 (Emily, 2026-10-26, OptionA screen 2 "After dinner"):
marking a dinner eaten used to take its ingredients out of the tracked
kitchen in silence (cooker.deplete_inventory_for_meal) and an un-tick
deliberately put nothing back (see check_off_meal's docstring: with no
record of what a depletion took, any restore was a guess). This module is
the record. At tick time the depletion hands over each row AS IT WAS, this
stores them, and Undo writes those rows back exactly — quantity, location,
dates, and a row that was used up and deleted comes back under its own id.

Three things live here:
  - short_name / took_line: the sentence ("Took the chicken, spinach, rice
    and lemons out of the kitchen."), four names then "and N more".
  - record: the stored "before" rows, one receipt per tick.
  - restore: the exact put-back.

Server-held rather than carried by the client so Undo cannot be asked to
write rows the server never took.
"""
from __future__ import annotations

import json
import re

from ..db import get_conn
from ._shared import household_id
from . import inventory as _inventory

NAMES_SHOWN = 4

# Words that say how an item is cut, cooked or sold rather than what it is.
# "Chicken thighs" is the chicken; "Baby spinach" is spinach; "Jasmine rice"
# is rice. Deliberately short: a word not here stays, which only makes the
# name longer and never wrong.
_DESCRIPTORS = frozenset("""
baby fresh frozen dried canned organic whole boneless skinless bone-in
large small medium ripe raw cooked sliced diced chopped ground minced
jasmine basmati brown white long-grain short-grain
thigh thighs breast breasts drumstick drumsticks wing wings fillet fillets
filet filets tenderloin tenderloins cutlet cutlets
""".split())


def short_name(item: str | None) -> str:
    """"chicken" for "Chicken thighs (boneless)", "spinach" for "Baby
    spinach", "rice" for "Jasmine rice", "lemons" for "Lemons". The item's
    own name, lowercased, with brackets and anything after a comma dropped
    and the descriptor words above taken out. Nothing left after that (a
    row called just "Thighs") falls back to the whole name lowercased."""
    text = re.sub(r"[(\[][^)\]]*[)\]]", " ", item or "")
    text = text.split(",")[0]
    words = text.lower().split()
    kept = [w for w in words if w not in _DESCRIPTORS]
    return " ".join(kept) or " ".join(words) or (item or "").strip().lower()


def took_names(items: list[str]) -> list[str]:
    """Short names, in the order taken, each said once."""
    seen, out = set(), []
    for item in items:
        name = short_name(item)
        if name and name not in seen:
            seen.add(name)
            out.append(name)
    return out


def took_line(names: list[str]) -> str:
    """"Took the chicken, spinach, rice and lemons out of the kitchen."
    One "the" at the front, no Oxford comma; past four, "and N more"."""
    if not names:
        return ""
    if len(names) > NAMES_SHOWN:
        shown = names[:NAMES_SHOWN]
        rest = len(names) - NAMES_SHOWN
        joined = ", ".join(shown) + f" and {rest} more"
    elif len(names) == 1:
        joined = names[0]
    else:
        joined = ", ".join(names[:-1]) + " and " + names[-1]
    return f"Took the {joined} out of the kitchen."


def _changed(depleted: list[dict]) -> list[dict]:
    """The depletions that really moved a row and kept its 'before'."""
    return [d for d in depleted
            if (d.get("result") or {}).get("units_reconciled") and d.get("before")]


def record(entry_ids: list[int], depleted: list[dict]) -> dict | None:
    """Store the rows a tick changed, as they were. Returns the receipt the
    caller shows ({names, line}), or None when nothing moved. Each stored
    row carries how much came off and the row's write counter right after
    the tick, so Undo can tell whether anyone has touched it since."""
    moved = _changed(depleted)
    if not moved or not entry_ids:
        return None
    names = took_names([d["before"]["item"] for d in moved])
    conn = get_conn()
    try:
        # A row two ingredients both matched is stored once, as it was first.
        stored: dict[int, dict] = {}
        for d in moved:
            rid = d["before"]["id"]
            if rid in stored:
                continue
            now = conn.execute(
                "SELECT rev FROM inventory_items WHERE id = ? AND household_id = ?",
                (rid, household_id()),
            ).fetchone()
            stored[rid] = {"before": d["before"], "taken": d.get("taken") or "",
                           "after_rev": now["rev"] if now else None}
        conn.execute(
            "DELETE FROM eaten_kitchen_receipts WHERE household_id = ? AND entry_id = ?",
            (household_id(), min(entry_ids)),
        )
        conn.execute(
            "INSERT INTO eaten_kitchen_receipts (household_id, entry_id, entry_ids_json, rows_json) "
            "VALUES (?, ?, ?, ?)",
            (household_id(), min(entry_ids), json.dumps(sorted(entry_ids)), json.dumps(list(stored.values()))),
        )
        conn.commit()
    finally:
        conn.close()
    return {"names": names, "line": took_line(names)}


def strip_before(depleted: list[dict]) -> list[dict]:
    """The depletion list without the stored rows, for callers (chat) that
    only need to know what happened."""
    return [{k: v for k, v in d.items() if k != "before"} for d in depleted]


# An Undo older than this finds nothing: the shelves have moved on.
RECEIPT_LIFETIME = "-1 day"


def _twin(conn, row: dict):
    """The row that is this food again, on the same shelf: the Kitchen's
    own "same food" key (kitchen_add._match_key — the grocery merge key,
    sizes off) and the same DISPLAYED location, so a row saved with no
    location is still read on its category's shelf rather than matching
    a same-name row anywhere (re-verify, 2026-10-27). Most recently
    touched first, the rule inventory._find_row_by_name uses."""
    from . import kitchen_add as _kitchen_add
    from . import quantities as _quantities

    key = _kitchen_add._match_key(row.get("item") or "")
    where = _quantities._display_location(row).strip().lower()
    for r in conn.execute(
        "SELECT * FROM inventory_items WHERE household_id = ? ORDER BY updated_at DESC, id DESC",
        (household_id(),),
    ).fetchall():
        o = dict(r)
        if _kitchen_add._match_key(o["item"] or "") == key and \
                _quantities._display_location(o).strip().lower() == where:
            return o
    return None


def _add_onto(conn, row_id: int, have: str, amount: str) -> None:
    from . import grocery as _grocery

    merged, _ = _grocery._try_consolidate_quantity(have or "", amount)
    conn.execute(
        "UPDATE inventory_items SET quantity = ?, updated_at = datetime('now') WHERE id = ? AND household_id = ?",
        (merged, row_id, household_id()),
    )


def _put_back(entry: dict, written_at: str | None = None) -> None:
    """
    One row, never more back than THIS dinner took.

    - Untouched since the tick: written back exactly.
    - Changed since (a shop tick added to it, another dinner took some):
      the amount this meal took is ADDED back onto it, so what happened
      meanwhile is kept.
    - Gone: if the household took it out after this receipt was written
      ("Used it up", "Threw it out", the pre-draft check's "gone" — every
      one recorded in inventory_removals), nothing comes back: they decided
      it's gone, and putting back the pre-dinner row silently reversed that
      (merge seams, 2026-10-27). Otherwise (this tick used it up, or a
      later dinner did) what this dinner took comes back — the whole
      "before" row when this tick deleted it (after_rev None: all of it was
      taken), else only `taken` (re-verify: rice 3 cups, dinner A took 1,
      dinner B the other 2, Undo A gave back nothing). It goes onto the
      row that is this food again on that shelf (_twin: "Lemon 3" added
      since takes the lemons back, not a second row beside it), or back
      under its own id.
    """
    row = entry["before"]
    conn = get_conn()
    try:
        now = conn.execute(
            "SELECT rev, quantity FROM inventory_items WHERE id = ? AND household_id = ?",
            (row["id"], household_id()),
        ).fetchone()
        if now is not None and now["rev"] == entry.get("after_rev"):
            cols = [c for c in row if c not in ("household_id", "id", "rev")]
            conn.execute(
                f"UPDATE inventory_items SET {', '.join(f'{c} = ?' for c in cols)} "
                f"WHERE id = ? AND household_id = ?",
                [row[c] for c in cols] + [row["id"], household_id()],
            )
            conn.commit()
            return
        if now is not None:
            amount = entry.get("taken") or ""
            if amount:
                _add_onto(conn, row["id"], now["quantity"], amount)
                conn.commit()
            return
        if written_at and conn.execute(
            "SELECT 1 FROM inventory_removals WHERE household_id = ? AND inventory_item_id = ? "
            "AND removed_at >= ?",
            (household_id(), row["id"], written_at),
        ).fetchone():
            return
        whole = entry.get("after_rev") is None
        amount = row.get("quantity") if whole else (entry.get("taken") or "")
        if not amount:
            return
        twin = _twin(conn, row)
        if twin is not None:
            _add_onto(conn, twin["id"], twin["quantity"], amount)
        else:
            back = dict(row)
            back["quantity"] = amount
            if not whole:
                # Only part of it comes back: the household's chip was read
                # against a different amount.
                back.pop("amount_left", None)
                back.pop("amount_left_qty", None)
            cols = [c for c in back if c not in ("household_id", "id")]
            conn.execute(
                f"INSERT INTO inventory_items (id, household_id, {', '.join(cols)}) "
                f"VALUES (?, ?, {', '.join('?' for _ in cols)})",
                [row["id"], household_id()] + [back[c] for c in cols],
            )
        conn.commit()
    finally:
        conn.close()


def restore(entry_id: int) -> dict:
    """Put back what the tick for this entry took (see _put_back), and give
    the batch's depletion claim back so a re-tick takes the ingredients
    again. One receipt, used once, good for a day. {"restored": N}."""
    conn = get_conn()
    try:
        receipt = None
        for r in conn.execute(
            "SELECT id, entry_ids_json, rows_json, created_at FROM eaten_kitchen_receipts "
            "WHERE household_id = ? AND created_at >= datetime('now', ?) ORDER BY id DESC",
            (household_id(), RECEIPT_LIFETIME),
        ).fetchall():
            if entry_id in json.loads(r["entry_ids_json"]):
                receipt = r
                break
        if receipt is None:
            return {"restored": 0}
        rows = json.loads(receipt["rows_json"])
        conn.execute("DELETE FROM eaten_kitchen_receipts WHERE id = ? AND household_id = ?",
                     (receipt["id"], household_id()))
        ids = json.loads(receipt["entry_ids_json"])
        marks = ",".join("?" for _ in ids)
        conn.execute(
            f"UPDATE meal_plan_entries SET inventory_depleted_at = NULL "
            f"WHERE household_id = ? AND id IN ({marks})", (household_id(), *ids),
        )
        conn.commit()
    finally:
        conn.close()
    for entry in rows:
        _put_back(entry, receipt["created_at"])
    return {"restored": len(rows)}


