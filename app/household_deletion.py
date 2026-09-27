"""
Deleting a household, or taking one adult out of it, from inside Pomona
(Loop Board "App Store: delete my household (and remove myself) from inside
Pomona", Emily 2026-09-27).

Apple rejects any app with accounts that can't be deleted in-app (App Store
guideline 5.1.1(v)), and the privacy policy needs to be able to say what
deletion actually does. So this is written to be stated plainly:

- **Delete the household.** Every row in every table that carries this
  household's id goes, then the `households` row itself. That includes the
  stored passphrase (`household_credentials`), invite links
  (`household_invites`), share links (`share_links`, `member_share_links`),
  everyone's details (`members`, with their phone numbers) and the chat's
  usage rows. Recipe page photos on disk go with the folder that holds
  them. What the server holds in memory for the household (chat history,
  open proposals, cached swap options, the week-generation records) is
  dropped too. Immediate: no undo window (Emily's default, 2026-09-27).
- **Remove myself.** One adult leaves a household that still has another
  adult in it. Their member row goes, and so does everything keyed to them
  alone (their ratings, notes, share link, invite links, morning-text log).
  Anything shared with the household that merely *names* them — who
  approved a week, who a chore was assigned to, who a trip was for — keeps
  the household's record and loses the pointer (set to NULL, or the id
  taken out of the list).

Which tables are household-owned is not a hand-kept list. It is read from
the database itself — every table with a `household_id` column — and
`tests/test_household_deletion.py` checks that against `schema.sql`, so a
table added next month is deleted too without anyone remembering to add
it here.

Everything happens in ONE transaction. A delete that fails half way rolls
back to exactly where it started, never to a household with half its
tables gone. Foreign keys are deferred to the commit (`PRAGMA
defer_foreign_keys`), so the order tables are emptied in doesn't matter —
and if anything outside the household still pointed into it, the commit
refuses and nothing is deleted.

`secure_delete` is on for the connection that deletes, so the freed pages
in the live database file are overwritten rather than left holding the
old bytes until SQLite happens to reuse them.

Backups: `app/backup.py` takes one snapshot a day and keeps
`RETENTION_DAYS` (14) days of them, pruning anything older on the same
daily run. Deleted data is therefore still in the snapshots taken before
the delete, and the last of those is pruned 15 days later. There is no
off-box copy today. See `BACKUP_RETENTION_NOTE` below — that sentence is
what the privacy policy can quote.

Household 1 — Emily's — can't be deleted or left through here at all
(`ProtectedHousehold`). The app binds household 1 by default whenever
nothing else is bound (tools._shared), and the local no-passphrase path
is household 1 too, so a slip anywhere — a test with no household bound, a
reviewer's demo — would otherwise land on her real data.

Why this module is not in `app/tools/`: everything there is callable by
the chat agent. Deleting a household on the strength of a sentence typed
into chat is exactly the thing that must not be possible — the same reason
`app/households.py` and `app/invites.py` live outside it.
"""
from __future__ import annotations

import json
import logging
import sqlite3

from .db import get_conn
from .tools._shared import DEFAULT_HOUSEHOLD_ID

logger = logging.getLogger("home_manager")

# The sentence the privacy policy can state. Kept beside the code it
# describes so the two change together.
BACKUP_RETENTION_NOTE = (
    "A deleted household is removed from the live database at once. Pomona keeps "
    "one backup snapshot of the database a day for 14 days, so deleted data can "
    "remain in those snapshots for up to 15 days after deletion, after which the "
    "last of them is pruned automatically."
)

# The word the confirm step asks for. Compared without case or surrounding
# spaces — the deliberate act is typing it, not matching capitals on a phone.
CONFIRM_WORD = "DELETE"

_ADULT_SQL = "LOWER(TRIM(age_group)) = 'adult'"

# Columns that point at a member by NAME rather than id, so the generic
# id handling in remove_member can't see them. Every column in the schema
# with "member" in its name has to be accounted for somewhere — the test
# suite enumerates them.
#   household_rhythm.member_name   one row per person per weekday: theirs go.
#   chores_profile.rotation_members_json   a JSON list of names: theirs is
#                                          taken out.
_NAME_COLUMNS = {
    ("household_rhythm", "member_name"),
    ("chores_profile", "rotation_members_json"),
}


class DeletionRefused(Exception):
    """A refusal with a sentence that can be shown to the person asking."""


class ProtectedHousehold(DeletionRefused):
    """Household 1 is never deleted or left from inside the app."""


def is_protected(household_id: int) -> bool:
    return int(household_id) == DEFAULT_HOUSEHOLD_ID


def household_tables(conn: sqlite3.Connection) -> list[str]:
    """Every table in the database that carries a household_id column."""
    names = [
        r[0]
        for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%' "
            "ORDER BY name"
        ).fetchall()
    ]
    out = []
    for name in names:
        cols = {row[1] for row in conn.execute(f"PRAGMA table_info({name})")}
        if "household_id" in cols:
            out.append(name)
    return out


def _begin(conn: sqlite3.Connection) -> None:
    """One write transaction, foreign keys checked at commit, freed pages zeroed."""
    conn.execute("PRAGMA secure_delete = ON")
    conn.execute("BEGIN IMMEDIATE")
    conn.execute("PRAGMA defer_foreign_keys = ON")


def _delete_rows(conn: sqlite3.Connection, table: str, household_id: int) -> int:
    """Delete one table's rows for the household. A seam the tests fail on purpose."""
    return conn.execute(f"DELETE FROM {table} WHERE household_id = ?", (household_id,)).rowcount


def delete_household(household_id: int) -> dict:
    """
    Delete the household and everything it owns. Returns {table: rows deleted}.

    Raises ProtectedHousehold for household 1 and DeletionRefused for a
    household that doesn't exist. Any other failure rolls the whole thing
    back and re-raises.
    """
    hid = int(household_id)
    if is_protected(hid):
        raise ProtectedHousehold("This household can't be deleted from inside Pomona.")
    conn = get_conn()
    try:
        if conn.execute("SELECT 1 FROM households WHERE id = ?", (hid,)).fetchone() is None:
            raise DeletionRefused("That household isn't here any more.")
        _begin(conn)
        counts: dict[str, int] = {}
        try:
            for table in household_tables(conn):
                n = _delete_rows(conn, table, hid)
                if n:
                    counts[table] = n
            counts["households"] = conn.execute(
                "DELETE FROM households WHERE id = ?", (hid,)
            ).rowcount
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
    finally:
        conn.close()

    # Off the database, after the commit: if either of these fails the
    # rows are already gone, and a stray file or cache entry is found and
    # swept by the same calls next time rather than blocking the delete.
    _remove_files(hid)
    forget_in_memory(hid)
    logger.info(
        "Household %s deleted from inside the app (%d rows across %d tables)",
        hid, sum(counts.values()), len(counts),
    )
    return counts


def _remove_files(hid: int) -> None:
    try:
        from . import recipe_photos

        recipe_photos.remove_household_photos(hid)
    except Exception:
        logger.exception("Removing household %s's recipe photos failed", hid)


def forget_in_memory(hid: int) -> None:
    """Drop what this process holds for the household outside the database."""
    try:
        from . import main as _main

        prefix = f"h{hid}:"
        for key in [k for k in list(_main.SESSIONS) if k.startswith(prefix)]:
            _main.SESSIONS.pop(key, None)
        for key in [k for k in list(_main.SESSION_TOUCHED) if k.startswith(prefix)]:
            _main.SESSION_TOUCHED.pop(key, None)
    except Exception:
        logger.exception("Forgetting household %s's chat sessions failed", hid)
    try:
        from .tools import proposals

        proposals._PROPOSALS.pop(hid, None)
    except Exception:
        logger.exception("Forgetting household %s's proposals failed", hid)
    try:
        from . import agent
        # By attribute, not `from .tools import swap_options`: tools/__init__
        # re-exports a FUNCTION called swap_options that shadows the module
        # (tests/test_module_name_collisions.py).
        from .tools.plate_parts import _OPTIONS_CACHE as plate_cache
        from .tools.swap_options import _OPTIONS_CACHE as swap_cache

        for cache in (
            plate_cache,
            swap_cache,
            agent._WEEK_GENERATION_RESULTS,
            agent._WEEK_GENERATION_RUNS,
        ):
            for key in [k for k in list(cache) if isinstance(k, tuple) and k and k[0] == hid]:
                cache.pop(key, None)
    except Exception:
        logger.exception("Forgetting household %s's cached options failed", hid)
    try:
        from .tools import usage

        usage._last_touched.pop(hid, None)
    except Exception:
        pass


# ---------- one adult leaving ----------


def _member_columns(conn: sqlite3.Connection) -> list[tuple[str, str, bool, bool]]:
    """
    Every column that points at a member by id: (table, column, not_null,
    is_json_list). A foreign key to members, a column named member_id or
    ending _member_id, or a JSON list ending member_ids_json.
    """
    out = []
    for table in household_tables(conn) + ["households"]:
        if table == "members":
            continue
        fk_cols = {
            r[3] for r in conn.execute(f"PRAGMA foreign_key_list({table})") if r[2] == "members"
        }
        for col in conn.execute(f"PRAGMA table_info({table})"):
            name, not_null = col[1], bool(col[3])
            if name.endswith("member_ids_json"):
                out.append((table, name, not_null, True))
            elif name in fk_cols or name == "member_id" or name.endswith("_member_id"):
                out.append((table, name, not_null, False))
    return out


def _strip_from_json_list(conn, table: str, column: str, hid: int, value) -> None:
    rows = conn.execute(
        f"SELECT rowid, {column} FROM {table} WHERE household_id = ?", (hid,)
    ).fetchall()
    for rowid, raw in rows:
        try:
            items = json.loads(raw or "[]")
        except (TypeError, ValueError):
            continue
        if not isinstance(items, list):
            continue
        kept = [x for x in items if not _same(x, value)]
        if len(kept) != len(items):
            conn.execute(
                f"UPDATE {table} SET {column} = ? WHERE rowid = ?", (json.dumps(kept), rowid)
            )


def _same(item, value) -> bool:
    if isinstance(value, int):
        try:
            return int(item) == value
        except (TypeError, ValueError):
            return False
    return isinstance(item, str) and item.strip().casefold() == str(value).strip().casefold()


def removal_check(household_id: int, member_id: int | None) -> str | None:
    """
    Why this adult can't leave this household right now, as a sentence —
    or None if they can.
    """
    hid = int(household_id)
    if is_protected(hid):
        return "This household can't be changed that way from inside Pomona."
    if member_id is None:
        return "Tell me who you are first, then try again."
    conn = get_conn()
    try:
        me = conn.execute(
            f"SELECT id FROM members WHERE id = ? AND household_id = ? AND {_ADULT_SQL}",
            (int(member_id), hid),
        ).fetchone()
        others = conn.execute(
            f"SELECT COUNT(*) FROM members WHERE household_id = ? AND id != ? AND {_ADULT_SQL}",
            (hid, int(member_id)),
        ).fetchone()[0]
    finally:
        conn.close()
    if me is None:
        return "You're not one of the adults in this household."
    if others == 0:
        return "You're the only adult here, so the household would be left with nobody. Delete the household instead."
    return None


def remove_member(household_id: int, member_id: int) -> dict:
    """
    Take one adult out of a household that keeps at least one other adult.
    Returns {"table.column": rows touched}.
    """
    hid = int(household_id)
    mid = int(member_id)
    if is_protected(hid):
        raise ProtectedHousehold("This household can't be changed that way from inside Pomona.")
    problem = removal_check(hid, mid)
    if problem:
        raise DeletionRefused(problem)
    conn = get_conn()
    touched: dict[str, int] = {}
    try:
        _begin(conn)
        try:
            # Re-checked inside the write lock: two adults each removing
            # themselves at the same moment must not leave the house empty.
            row = conn.execute(
                f"SELECT name FROM members WHERE id = ? AND household_id = ? AND {_ADULT_SQL}",
                (mid, hid),
            ).fetchone()
            others = conn.execute(
                f"SELECT COUNT(*) FROM members WHERE household_id = ? AND id != ? AND {_ADULT_SQL}",
                (hid, mid),
            ).fetchone()[0]
            if row is None or others == 0:
                raise DeletionRefused(
                    "You're the only adult here, so the household would be left with nobody. "
                    "Delete the household instead."
                )
            name = row[0]
            for table, column, not_null, is_json in _member_columns(conn):
                where_hh = "id = ?" if table == "households" else "household_id = ?"
                if is_json:
                    _strip_from_json_list(conn, table, column, hid, mid)
                    continue
                if not_null:
                    n = conn.execute(
                        f"DELETE FROM {table} WHERE {where_hh} AND {column} = ?", (hid, mid)
                    ).rowcount
                else:
                    n = conn.execute(
                        f"UPDATE {table} SET {column} = NULL WHERE {where_hh} AND {column} = ?",
                        (hid, mid),
                    ).rowcount
                if n:
                    touched[f"{table}.{column}"] = n
            n = conn.execute(
                "DELETE FROM household_rhythm WHERE household_id = ? AND LOWER(TRIM(member_name)) = LOWER(TRIM(?))",
                (hid, name),
            ).rowcount
            if n:
                touched["household_rhythm.member_name"] = n
            _strip_from_json_list(conn, "chores_profile", "rotation_members_json", hid, name)
            conn.execute("DELETE FROM members WHERE id = ? AND household_id = ?", (mid, hid))
            touched["members"] = 1
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
    finally:
        conn.close()
    logger.info("Member %s removed themselves from household %s", mid, hid)
    return touched


__all__ = [
    "BACKUP_RETENTION_NOTE",
    "CONFIRM_WORD",
    "DeletionRefused",
    "ProtectedHousehold",
    "delete_household",
    "forget_in_memory",
    "household_tables",
    "is_protected",
    "remove_member",
    "removal_check",
]
