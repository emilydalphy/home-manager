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


def _put_back(entry: dict, written_at: str | None = None) -> None:
    """One row. Untouched since the tick: written back exactly. Changed
    since (a shop tick added to it): the amount the meal took is ADDED
    back, so what came in meanwhile is kept. Used up and deleted BY THE
    TICK: back under its own id unless the household has a row for that
    food again, in which case the amount goes onto that one.

    The twin is found by the Kitchen's own "same food" key
    (kitchen_add._existing — the grocery merge key, sizes off), so "Lemon 3"
    added after the tick is where "Lemons" goes back, not a second row
    beside it (merge seams, 2026-10-27).

    A row the tick LEFT in the kitchen that is gone now was taken out by the
    household afterwards ("Used it up", "Threw it out", the pre-draft
    check's "gone", chat): they decided it's gone, so nothing comes back —
    putting back the pre-dinner row silently reversed their answer and
    overstated the amount (merge seams, 2026-10-27). The receipt's
    after_rev is None exactly when the tick itself deleted the row, and
    then the whole "before" amount is what dinner took — so nothing here
    ever restores more than dinner took."""
    from . import kitchen_add as _kitchen_add

    row = entry["before"]
    location = row.get("location") or None
    conn = get_conn()
    try:
        now = conn.execute(
            "SELECT rev FROM inventory_items WHERE id = ? AND household_id = ?",
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
        if now is None:
            if entry.get("after_rev") is not None:
                return  # dinner left some; the household took the rest out since
            if written_at and conn.execute(
                "SELECT 1 FROM inventory_removals WHERE household_id = ? AND inventory_item_id = ? "
                "AND removed_at >= ?",
                (household_id(), row["id"], written_at),
            ).fetchone():
                return
            twin = _kitchen_add._existing(conn, row["item"], location)
            if twin is None:
                cols = [c for c in row if c not in ("household_id", "id")]
                conn.execute(
                    f"INSERT INTO inventory_items (id, household_id, {', '.join(cols)}) "
                    f"VALUES (?, ?, {', '.join('?' for _ in cols)})",
                    [row["id"], household_id()] + [row[c] for c in cols],
                )
                conn.commit()
                return
            name, where, amount = twin["item"], twin["location"] or location, row["quantity"]
        else:
            name, where, amount = row["item"], location, entry.get("taken") or ""
    finally:
        conn.close()
    if amount:
        _inventory._add_to_inventory(name, amount, source=row.get("source") or "chat",
                                     category=row.get("category"), location=where)


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


