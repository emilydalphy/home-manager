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

The Kitchen tab's "Add N" (static/kitchen-add.js -> /api/whats-home/add)
uses the same mechanism under its own table, kitchen_add_sends (2026-10-31):
a retap after "Couldn't save" whose first send HAD landed made 12 eggs 24.
That route also waits a few seconds for a twin still adding, so the second
answer is the first one's success, not a 409 (see `wait_seconds`).
"""
import json
import time

from .db import get_conn
from .tools._shared import household_id

# How long a receipt is kept. A queued add replays when the phone next has
# signal and the app open, which can be days after the store, not minutes.
RETENTION_DAYS = 30

# How long an answerless receipt counts as "still adding". Past this the
# request that claimed it is taken to have died between the claim and the
# answer (a Railway redeploy mid-request is enough — and Railway redeploys
# on every push) and the next send takes the claim over and adds. Without
# it an orphan answered every resend with a 409 for 30 days, and the add
# never reached the list. An add is a few database writes; a minute is
# far past any live one.
ABANDONED_AFTER_SECONDS = 60


# The receipt tables this may write — a table name is never taken from a
# request, but it is spliced into SQL, so it is checked against this.
TABLES = ("grocery_add_receipts", "kitchen_add_sends")


class AddInFlight(Exception):
    """The same id is being added right now by an earlier request."""


def add_once(client_id, do_add, table="grocery_add_receipts", wait_seconds=0):
    """Run `do_add()` once per (household, client_id); a repeat returns its result.

    `wait_seconds` > 0: a repeat that arrives while the first is still
    adding waits up to that long for the first's answer and returns it,
    instead of raising AddInFlight straight away. Grocery passes 0 — its
    offline queue keeps an add on a 409 and replays it later.
    """
    if not client_id:
        return do_add()
    if table not in TABLES:
        raise ValueError(f"unknown receipts table {table!r}")
    hid = household_id()
    deadline = time.monotonic() + max(0, wait_seconds)
    conn = get_conn()
    try:
        conn.execute(
            f"DELETE FROM {table} WHERE created_at < datetime('now', ?)",
            (f"-{RETENTION_DAYS} days",),
        )
        conn.commit()
        while True:
            # Claim the id BEFORE adding, so two copies of one add arriving
            # together (the online send still running when the queue's replay
            # lands) cannot both get past the check.
            claimed = conn.execute(
                f"INSERT OR IGNORE INTO {table} (household_id, client_id) VALUES (?, ?)",
                (hid, client_id),
            ).rowcount
            conn.commit()
            if claimed:
                break
            row = conn.execute(
                f"SELECT result_json FROM {table} WHERE household_id = ? AND client_id = ?",
                (hid, client_id),
            ).fetchone()
            if row is not None and row["result_json"] is not None:
                return json.loads(row["result_json"])
            if row is None:
                # The first add failed and let the id go (_forget) between
                # our claim and our read — claim it again.
                continue
            # Answerless: still adding, or abandoned. Take an abandoned one
            # over in one statement, so two resends cannot both take it.
            # The cost, knowingly paid: if the dead request HAD added before
            # it died, this adds again — rarer, and kinder than an add that
            # never lands at all.
            taken = conn.execute(
                f"UPDATE {table} SET created_at = datetime('now') "
                "WHERE household_id = ? AND client_id = ? AND result_json IS NULL "
                "AND created_at <= datetime('now', ?)",
                (hid, client_id, f"-{ABANDONED_AFTER_SECONDS} seconds"),
            ).rowcount
            conn.commit()
            if taken:
                break
            if time.monotonic() >= deadline:
                raise AddInFlight()
            time.sleep(0.1)
    finally:
        conn.close()

    try:
        result = do_add()
    except BaseException:
        # Nothing was added (or the add failed partway and said so) — let
        # the same id try again rather than answer every retry with a 409.
        _forget(hid, client_id, table)
        raise
    conn = get_conn()
    try:
        conn.execute(
            f"UPDATE {table} SET result_json = ? WHERE household_id = ? AND client_id = ?",
            (json.dumps(result, default=str), hid, client_id),
        )
        conn.commit()
    finally:
        conn.close()
    return result


def _forget(hid, client_id, table="grocery_add_receipts"):
    conn = get_conn()
    try:
        conn.execute(
            f"DELETE FROM {table} WHERE household_id = ? AND client_id = ?",
            (hid, client_id),
        )
        conn.commit()
    finally:
        conn.close()
