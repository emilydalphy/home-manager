"""
Put a week back exactly as it was — the record an answer leaves behind so
the tap that made it can be taken back.

Lifted whole out of tonight.py on 2026-09-24, when drop_dish_from_day (the
Review stepper's "−") stopped refusing a dish that feeds a later night and
started re-planning that night itself. Both answers now move a cook onto
another night, delete a leftovers row, re-point a chain and shift a fridge
move — so both need the same "and here is how to take it back", and two
implementations of that is the one failure this codebase has been bitten by
most. The night off's own machinery was already right; what was wrong was
that it was private to the module that happened to write it first.

The shape, unchanged from the night off (2026-09-22):

  * SNAPSHOT every column of every row the answer is about to touch, plus
    the grocery links and prep rows hanging off them, BEFORE the first
    write and on the caller's own transaction.
  * FINGERPRINT those rows immediately after the write. Undo puts the week
    back only while they still look exactly like this — an Undo tapped
    after somebody else cooked, swapped or moved one of these nights must
    not quietly undo THEIR change too.
  * STAMP the record on the derived_from of whichever row stands on the
    answer afterwards (the `holder`), so it is written once and read once
    and travels with the thing it is about.

Nothing here decides anything; the caller owns the transaction, the holder
and the sentence it says afterwards.
"""
from __future__ import annotations

import json

from ._shared import household_id


def columns(conn, table: str) -> set[str]:
    return {r["name"] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}


def row_dicts(conn, sql: str, params) -> list[dict]:
    return [{k: r[k] for k in r.keys()} for r in conn.execute(sql, params).fetchall()]


def touched_ids(rows_all, direct: set[int], refs: list[str]) -> set[int]:
    """The rows an answer may rewrite: the ones it moves or deletes by name,
    plus every row whose derived_from names one of the nights involved — a
    chain reference ("2026-09-23:dinner") is rewritten in place, and Undo
    has to be able to put those back too. Over-inclusion is harmless (a
    snapshot of a row that didn't change restores to itself);
    under-inclusion is an Undo that leaves a chain pointing at the wrong
    night."""
    touched = set(direct)
    for r in rows_all:
        text = r["derived_from_json"] or ""
        if any(f'"{ref}"' in text for ref in refs):
            touched.add(r["id"])
    return touched


def snapshot(conn, entry_ids: set[int]) -> dict:
    """Every column of every row an answer is about to touch, with the
    grocery links and prep rows hanging off them — taken BEFORE the first
    write, on the caller's own transaction."""
    ids = sorted(entry_ids)
    if not ids:
        return {"entries": [], "links": [], "prep": []}
    marks = ",".join("?" * len(ids))
    hh = household_id()
    return {
        "entries": row_dicts(
            conn, f"SELECT * FROM meal_plan_entries WHERE household_id = ? AND id IN ({marks})", (hh, *ids)),
        "links": row_dicts(
            conn, f"SELECT * FROM meal_plan_grocery_links WHERE household_id = ? AND meal_plan_entry_id IN ({marks})",
            (hh, *ids)),
        "prep": row_dicts(
            conn, f"SELECT * FROM prep_tasks WHERE household_id = ? AND meal_plan_entry_id IN ({marks})", (hh, *ids)),
    }


FINGERPRINT_COLS = ("date", "slot", "slot_state", "cooked_status", "derived_from_json")


def fingerprint(conn, entry_ids, holder_id: int) -> list[list]:
    """What the touched rows look like right after the answer. Undo puts the
    week back only while they still look exactly like this — the same
    "written once, read once" rule the nights swap's moved_from token
    keeps. The holder's own derived_from carries this record, so it is
    compared without it."""
    ids = sorted(set(entry_ids) | {holder_id})
    marks = ",".join("?" * len(ids))
    out = []
    for r in conn.execute(
        f"SELECT id, {', '.join(FINGERPRINT_COLS)} FROM meal_plan_entries "
        f"WHERE household_id = ? AND id IN ({marks}) ORDER BY id",
        (household_id(), *ids),
    ).fetchall():
        cols = FINGERPRINT_COLS[:-1] if r["id"] == holder_id else FINGERPRINT_COLS
        out.append([r["id"], *[r[c] for c in cols]])
    return out


def stamp(conn, holder_id: int, key: str, record: dict) -> None:
    """Leave the record on the holder's own derived_from, under `key` — each
    answer keys its own, so a night off and a stepper's "−" on the same
    week can never read each other's."""
    row = conn.execute(
        "SELECT derived_from_json FROM meal_plan_entries WHERE id = ? AND household_id = ?",
        (holder_id, household_id()),
    ).fetchone()
    derived = json.loads(row["derived_from_json"] or "{}")
    derived[key] = record
    conn.execute(
        "UPDATE meal_plan_entries SET derived_from_json = ? WHERE id = ?",
        (json.dumps(derived), holder_id),
    )


def read(row, key: str) -> dict | None:
    """The record a holder row is carrying, or None — a derived_from that
    will not parse is a row with no record on it, never an error."""
    try:
        return json.loads(row["derived_from_json"] or "{}").get(key) or None
    except (TypeError, ValueError):
        return None


def still_as_left(conn, record: dict, holder_id: int) -> bool:
    """Whether every row the answer touched still reads exactly as the
    answer left it."""
    touched = {fp[0] for fp in record.get("after") or []} - {holder_id}
    return fingerprint(conn, touched, holder_id) == record.get("after")


def reinsert(conn, table: str, rows: list[dict], ignore: bool = False) -> None:
    """Put snapshot rows back with their own ids. Columns are checked
    against the table itself rather than trusted off the record."""
    if not rows:
        return
    known = columns(conn, table)
    verb = "INSERT OR IGNORE" if ignore else "INSERT"
    for r in rows:
        cols = [c for c in r if c in known]
        conn.execute(
            f"{verb} INTO {table} ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})",
            tuple(r[c] for c in cols),
        )


def restore(conn, record: dict, holder_id: int) -> None:
    """
    Put the snapshot back, on the caller's own open transaction: every row
    as it read, the rows the answer deleted re-inserted with their own ids,
    their prep rows and grocery links with them, and the holder itself gone
    when it is a row the answer invented.

    The grocery LIST is not touched here. A link comes back only if its
    line is still there — the list is the household's, and a line they have
    deleted since stays deleted rather than being linked into a meal again.
    """
    from . import weekly_plan as _weekly_plan

    hh = household_id()
    snap_ids = {e["id"] for e in record.get("entries") or []}
    if holder_id not in snap_ids:
        _weekly_plan.delete_plan_entry(conn, holder_id)
    if snap_ids:
        marks = ",".join("?" * len(snap_ids))
        conn.execute(
            f"DELETE FROM prep_tasks WHERE household_id = ? AND meal_plan_entry_id IN ({marks})",
            (hh, *sorted(snap_ids)),
        )
    entry_cols = columns(conn, "meal_plan_entries")
    for e in record.get("entries") or []:
        cols = [c for c in e if c in entry_cols]
        exists = conn.execute(
            "SELECT 1 FROM meal_plan_entries WHERE id = ? AND household_id = ?", (e["id"], hh),
        ).fetchone()
        if exists:
            sets = ", ".join(f"{c} = ?" for c in cols if c != "id")
            conn.execute(
                f"UPDATE meal_plan_entries SET {sets} WHERE id = ?",
                (*[e[c] for c in cols if c != "id"], e["id"]),
            )
        else:
            conn.execute(
                f"INSERT INTO meal_plan_entries ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})",
                tuple(e[c] for c in cols),
            )
    reinsert(conn, "prep_tasks", record.get("prep") or [])
    links = [
        l for l in record.get("links") or []
        if conn.execute("SELECT 1 FROM grocery_items WHERE id = ?", (l["grocery_item_id"],)).fetchone()
    ]
    reinsert(conn, "meal_plan_grocery_links", links, ignore=True)
