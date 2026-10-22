"""
A grocery add named by the phone happens once, however often it is sent.

The Shop tab's add sheet gives every add a client id before its first send
(groAddLine in static/shell.js), and the offline queue re-sends a queued add
under that same id (static/grocery-offline.js). Without this, a reply lost
in a store's dead zone meant the add went twice and add_grocery_item merged
the second into the first — "2 milk" became 4. Now the first request leaves
a receipt (table grocery_add_receipts), and a repeat of the id is answered
with the first answer and adds nothing.

Scoped to the household: the key is (household_id, client_id), so one
household's id never answers another's add. An add with no id behaves
exactly as before. Lives outside app/tools because it is about a REQUEST
arriving twice, not about the list — add_grocery_item's own merge rules are
untouched.
"""
import json

from .db import get_conn
from .tools._shared import household_id

# How long a receipt is kept. A queued add replays when the phone next has
# signal and the app open, which can be days after the store, not minutes.
RETENTION_DAYS = 30


class AddInFlight(Exception):
    """The same id is being added right now by an earlier request."""


def add_once(client_id, do_add):
    """Run `do_add()` once per (household, client_id); a repeat returns its result."""
    if not client_id:
        return do_add()
    hid = household_id()
    conn = get_conn()
    try:
        conn.execute(
            "DELETE FROM grocery_add_receipts WHERE created_at < datetime('now', ?)",
            (f"-{RETENTION_DAYS} days",),
        )
        # Claim the id BEFORE adding, so two copies of one add arriving
        # together (the online send still running when the queue's replay
        # lands) cannot both get past the check.
        claimed = conn.execute(
            "INSERT OR IGNORE INTO grocery_add_receipts (household_id, client_id) VALUES (?, ?)",
            (hid, client_id),
        ).rowcount
        conn.commit()
        if not claimed:
            row = conn.execute(
                "SELECT result_json FROM grocery_add_receipts WHERE household_id = ? AND client_id = ?",
                (hid, client_id),
            ).fetchone()
            if row is None or row["result_json"] is None:
                raise AddInFlight()
            return json.loads(row["result_json"])
    finally:
        conn.close()

    try:
        result = do_add()
    except BaseException:
        # Nothing was added (or the add failed partway and said so) — let
        # the same id try again rather than answer every retry with a 409.
        _forget(hid, client_id)
        raise
    conn = get_conn()
    try:
        conn.execute(
            "UPDATE grocery_add_receipts SET result_json = ? WHERE household_id = ? AND client_id = ?",
            (json.dumps(result, default=str), hid, client_id),
        )
        conn.commit()
    finally:
        conn.close()
    return result


def _forget(hid, client_id):
    conn = get_conn()
    try:
        conn.execute(
            "DELETE FROM grocery_add_receipts WHERE household_id = ? AND client_id = ?",
            (hid, client_id),
        )
        conn.commit()
    finally:
        conn.close()
