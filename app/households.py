"""
The household registry: creating households, and the passphrase that opens
each one.

Scope, deliberately small
-------------------------
This is *not* an account system. There are no users, no usernames, no email
addresses and no per-person logins — that is a later, separate piece of
work. What is here is the smallest thing that lets a second household exist
with its own data: each household has one shared passphrase, and signing in
with it establishes *which* household the session belongs to. It is the
same shape as the single shared password the app already had, with one bit
added: the credential now identifies a household rather than merely proving
the caller is allowed in at all.

Emily's household (id 1) needs no row in `household_credentials`. The
existing `HOME_MANAGER_PASSWORD` env var still signs her in, still to
household 1, so her deployment keeps working with nothing to migrate and no
new secret to set. A stored credential can be added for household 1 later
if she ever wants to stop using the env var; both paths are accepted.

Why this module is not in `app/tools/`
--------------------------------------
Everything in `app/tools/` is callable by the chat agent. Nothing here
should ever be: the model must not be able to read a password hash, mint a
household, or change a passphrase on the strength of a sentence somebody
typed into chat. Keeping the credential code outside that package means it
cannot be exposed by accident — e.g. by being re-exported from
`tools/__init__.py`, which is how every tool becomes visible.
"""
from __future__ import annotations

import hashlib
import hmac
import logging
import secrets
import sqlite3

from .db import get_conn
from .tools._shared import DEFAULT_HOUSEHOLD_ID

logger = logging.getLogger("home_manager")

# Cost of hashing one passphrase. Login checks the candidate against every
# stored credential (each has its own salt, so they cannot be looked up by
# hash), which makes a sign-in attempt cost N of these. That is fine at the
# beta's N=2 and is worth revisiting long before it is large — see
# `authenticate`.
_PBKDF2_ITERATIONS = 240_000
_ALGORITHM = "pbkdf2_sha256"

MIN_PASSPHRASE_LENGTH = 8


def hash_passphrase(passphrase: str, *, salt: bytes | None = None) -> str:
    """Encode a passphrase as `pbkdf2_sha256$iterations$salt$hash`."""
    if salt is None:
        salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256", passphrase.encode("utf-8"), salt, _PBKDF2_ITERATIONS
    )
    return f"{_ALGORITHM}${_PBKDF2_ITERATIONS}${salt.hex()}${digest.hex()}"


def verify_passphrase(passphrase: str, stored: str) -> bool:
    """Constant-time check of a candidate against a stored hash."""
    try:
        algorithm, iterations, salt_hex, hash_hex = stored.split("$")
        if algorithm != _ALGORITHM:
            return False
        digest = hashlib.pbkdf2_hmac(
            "sha256", passphrase.encode("utf-8"), bytes.fromhex(salt_hex), int(iterations)
        )
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(digest.hex(), hash_hex)


def authenticate(passphrase: str) -> int | None:
    """
    Which household does this passphrase open? None if it opens none.

    Every stored credential is checked, and *all* of them are checked even
    after one matches. Returning early would make the response time leak
    which household matched, and the loop is two iterations long — there is
    no reason to take that trade.

    This checks *stored* credentials only. It is deliberately not the whole
    answer to "which household does this passphrase open?", because
    household 1 signs in via the HOME_MANAGER_PASSWORD env var and has no
    stored row. Use `resolve_passphrase` for that question — and note the
    login route checks the env var first, so anything written here can be
    shadowed by it.
    """
    if not passphrase:
        return None
    conn = get_conn()
    rows = conn.execute(
        "SELECT household_id, password_hash FROM household_credentials ORDER BY household_id ASC"
    ).fetchall()
    conn.close()
    matched: int | None = None
    for row in rows:
        if verify_passphrase(passphrase, row["password_hash"]) and matched is None:
            matched = row["household_id"]
    return matched


def resolve_passphrase(passphrase: str) -> int | None:
    """
    Which household would this passphrase actually sign into? None if none.

    This must mirror `main._household_for_password` exactly, env var
    included. It is what the collision guards below are checked against,
    and getting it wrong is not a cosmetic bug: because the login route
    tries HOME_MANAGER_PASSWORD *first*, a household created with Emily's
    env password would send its users into **household 1, seeing Emily's
    data**, while their own household became permanently unreachable. That
    was a real hole here — the guards originally consulted stored
    credentials only, so the one household whose credential lives outside
    the table was exactly the one they could not see.
    """
    if not passphrase:
        return None
    # Imported here rather than at module scope: security imports from
    # tools._shared, and this keeps households.py free of an import cycle
    # if security ever needs anything from here.
    from . import security

    if security.check_password(passphrase):
        return DEFAULT_HOUSEHOLD_ID
    return authenticate(passphrase)


def passphrase_in_use(passphrase: str) -> bool:
    """True if this passphrase would already sign into some household."""
    return resolve_passphrase(passphrase) is not None


def household_exists(household_id: int) -> bool:
    conn = get_conn()
    row = conn.execute("SELECT id FROM households WHERE id = ?", (int(household_id),)).fetchone()
    conn.close()
    return row is not None


def session_epoch(household_id: int) -> int:
    """
    The household's sign-in epoch: 0 until its passphrase first changes,
    then one more on every change (`set_passphrase`, `note_env_passphrase`).
    A session cookie carries the epoch it was minted under, and
    `session_still_holds` refuses one that is behind — that is the whole of
    "changing the passphrase signs everyone else out" (Emily, 2026-11-01).

    0 for a household that is not there, and for a database that has not
    been migrated yet (a script, a test fixture that never ran init_db): no
    epoch has ever moved there, so 0 is the true answer, not a guess.
    """
    hid = int(household_id)
    if not (0 <= hid <= 2**63 - 1):
        return 0  # no household can have an id SQLite cannot bind
    conn = get_conn()
    try:
        row = conn.execute(
            "SELECT session_epoch FROM households WHERE id = ?", (hid,)
        ).fetchone()
    except sqlite3.OperationalError:
        return 0
    finally:
        conn.close()
    return int(row["session_epoch"] or 0) if row else 0


def _bump_session_epoch(conn, household_id: int) -> None:
    conn.execute(
        "UPDATE households SET session_epoch = COALESCE(session_epoch, 0) + 1 WHERE id = ?",
        (int(household_id),),
    )


def session_still_holds(household_id: int, member_id: int | None, epoch: int | None = None) -> bool:
    """
    Does what a signed session cookie says still stand: its household is
    there, and — when the cookie names somebody — so is that person?

    The second half (defect hunt, 2026-10-11): "Remove me from this
    household" deletes the member row (household_deletion.remove_member)
    and signs out the device it was tapped on, and nothing else. Bob's
    other phone kept a cookie naming a member that no longer exists, so it
    stayed in the household he had just left — and, with one adult left,
    tools.current_member() resolved it to her: his iPad was signed in AS
    Carol, could approve her week and point her sign-in email at his own
    inbox. remove_member is the only thing that deletes a member row, so a
    row gone means exactly "this person left".

    A member who still exists but is no longer an adult (re-marked a child)
    is NOT a reason to sign out — the row is there, and the pick reads as
    nobody: since 2026-10-22 not even in a house with one adult left
    (tools.current_member), and since 2026-10-26 not after a fresh sign-in
    on their phone either (security.device_member_for_login keeps them).

    `epoch`, when given, is the sign-in epoch the cookie was minted under:
    it must be the household's current one (`session_epoch`), or the
    passphrase has changed since and this device is signed out. None (the
    device-cookie check at /login, which is about a person, not a sitting)
    skips that half.
    """
    epoch_sql, epoch_args = "", ()
    if epoch is not None:
        epoch_sql, epoch_args = " AND COALESCE(h.session_epoch, 0) = ?", (int(epoch),)
    conn = get_conn()
    try:
        if member_id is None:
            row = conn.execute(
                f"SELECT 1 FROM households h WHERE h.id = ?{epoch_sql}",
                (int(household_id), *epoch_args),
            ).fetchone()
        else:
            row = conn.execute(
                "SELECT 1 FROM households h JOIN members m ON m.household_id = h.id "
                f"WHERE h.id = ? AND m.id = ?{epoch_sql}",
                (int(household_id), int(member_id), *epoch_args),
            ).fetchone()
        return row is not None
    finally:
        conn.close()


def adult_exists(household_id: int, member_id: int) -> bool:
    """
    Is this member an adult of this household, right now?

    Asked by the auth path (`security.device_member_for_login`) about an
    explicit household id, with nothing bound — /login is a public path,
    so there is no ContextVar to read and `tools.current_member()` cannot
    answer. Same shape and same reason as `household_exists` above.

    The age-group test is `tools._shared._ADULT_SQL`'s, written out rather
    than imported: this module deliberately depends on nothing in
    `app/tools/`, and onboarding writes "Adult" capitalised, so the
    comparison has to fold case either way.
    """
    conn = get_conn()
    row = conn.execute(
        "SELECT id FROM members "
        "WHERE id = ? AND household_id = ? AND LOWER(TRIM(age_group)) = 'adult'",
        (int(member_id), int(household_id)),
    ).fetchone()
    conn.close()
    return row is not None


def list_households() -> list[dict]:
    """Every household and whether it has a stored passphrase. Never returns hashes."""
    conn = get_conn()
    rows = conn.execute(
        "SELECT h.id, h.name, h.created_at, "
        "       (c.household_id IS NOT NULL) AS has_credential "
        "FROM households h "
        "LEFT JOIN household_credentials c ON c.household_id = h.id "
        "ORDER BY h.id ASC"
    ).fetchall()
    conn.close()
    return [
        {
            "id": r["id"],
            "name": r["name"],
            "created_at": r["created_at"],
            "has_credential": bool(r["has_credential"]),
        }
        for r in rows
    ]


def set_passphrase(household_id: int, passphrase: str) -> int:
    """
    Set or replace a household's passphrase. Returns the household's sign-in
    epoch afterwards.

    REPLACING one signs every device of the household out (Emily,
    2026-11-01: "changing the passphrase signs everyone else out of their
    other devices"): the epoch moves on, and every cookie minted under the
    old one is refused at its next request. A route that changes it from a
    signed-in device keeps THAT device in with
    `security.reissue_on_current_epoch`. Setting a first passphrase (a new
    household, or an email household given one) changes no existing key,
    so it signs nobody out.

    Today the only callers are `create_household` and
    `create_household.py --reset-passphrase` — there is no screen for it —
    so a reset from the command line signs every device out, there being no
    device that made the change.
    """
    _validate_passphrase(passphrase)
    existing = resolve_passphrase(passphrase)
    if existing is not None and existing != int(household_id):
        raise ValueError(
            _collision_message(existing)
        )
    conn = get_conn()
    try:
        conn.execute("BEGIN IMMEDIATE")
        replacing = conn.execute(
            "SELECT 1 FROM household_credentials WHERE household_id = ?", (int(household_id),)
        ).fetchone() is not None
        conn.execute(
            "INSERT INTO household_credentials (household_id, password_hash) VALUES (?, ?) "
            "ON CONFLICT(household_id) DO UPDATE SET password_hash = excluded.password_hash, "
            "updated_at = datetime('now')",
            (int(household_id), hash_passphrase(passphrase)),
        )
        if replacing:
            _bump_session_epoch(conn, household_id)
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    finally:
        conn.close()
    if replacing:
        logger.info("Household %s: passphrase changed; its other devices are signed out", household_id)
    return session_epoch(household_id)


def _env_fingerprint(secret: bytes, passphrase: str) -> str:
    return "hmac_sha256$" + hmac.new(
        secret, f"env-passphrase.{passphrase}".encode("utf-8"), hashlib.sha256
    ).hexdigest()


def note_env_passphrase() -> bool:
    """
    Household 1's passphrase is `HOME_MANAGER_PASSWORD`, changed in Railway
    rather than through `set_passphrase` — so the change is noticed here, at
    startup. A fingerprint of the last value seen is kept in
    `env_passphrase_seen` — an HMAC keyed with SESSION_SECRET, never the
    passphrase: a copy of the database alone can't be used to guess it, and
    it is cheap, which matters because this runs on every start (every test
    client starts the app). A different value moves household 1's sign-in
    epoch on, which signs every device out. True when it did. (Rotating
    SESSION_SECRET reads as a change too — it signs everyone out anyway.)

    The first startup that has this table records the value and signs
    nobody out — a deploy is not a passphrase change. Unset (local
    development) does nothing at all. Never raises: a bookkeeping check
    must not stop the app starting.
    """
    from . import security  # the module that owns the env var

    try:
        current = security._password()
        if not current:
            return False
        conn = get_conn()
        try:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT password_hash FROM env_passphrase_seen WHERE household_id = ?",
                (DEFAULT_HOUSEHOLD_ID,),
            ).fetchone()
            seen = _env_fingerprint(security._secret(), current)
            if row is not None and hmac.compare_digest(row["password_hash"], seen):
                conn.rollback()
                return False
            conn.execute(
                "INSERT INTO env_passphrase_seen (household_id, password_hash) VALUES (?, ?) "
                "ON CONFLICT(household_id) DO UPDATE SET password_hash = excluded.password_hash, "
                "updated_at = datetime('now')",
                (DEFAULT_HOUSEHOLD_ID, seen),
            )
            changed = row is not None
            if changed:
                _bump_session_epoch(conn, DEFAULT_HOUSEHOLD_ID)
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
        finally:
            conn.close()
        if changed:
            logger.info("HOME_MANAGER_PASSWORD changed; household 1's devices are signed out")
        return changed
    except Exception:
        logger.exception("Checking HOME_MANAGER_PASSWORD for a change failed")
        return False


def create_household(name: str, passphrase: str) -> int:
    """
    Create a household and the passphrase that opens it. Returns its id.

    The new household starts genuinely empty — no members, no recipes, no
    preferences. It is not seeded from household 1, and nothing is copied
    across; the app's own onboarding is what fills it in, exactly as it did
    for Emily.
    """
    name = (name or "").strip()
    if not name:
        raise ValueError("A household needs a name.")
    _validate_passphrase(passphrase)
    clash = resolve_passphrase(passphrase)
    if clash is not None:
        raise ValueError(_collision_message(clash))
    conn = get_conn()
    cur = conn.execute("INSERT INTO households (name) VALUES (?)", (name,))
    household_id = cur.lastrowid
    conn.commit()
    conn.close()
    set_passphrase(household_id, passphrase)
    logger.info("Created household %s (%r)", household_id, name)
    return household_id


def _collision_message(existing: int) -> str:
    """
    Say plainly what would have gone wrong, because the consequence is not
    obvious from "that's taken" and the operator is the only safeguard.
    """
    if existing == DEFAULT_HOUSEHOLD_ID:
        return (
            "That is already the passphrase for household 1 (the HOME_MANAGER_PASSWORD "
            "env var). If a household were given it, everyone signing in with it would "
            "land in household 1 and see its data, and the new household would be "
            "unreachable. Pick a different passphrase."
        )
    return (
        f"That passphrase already signs into household {existing}. The passphrase is what "
        "tells the app which household is signing in, so two households cannot share one. "
        "Pick a different passphrase."
    )


def forget_member_pointers(conn, household_id: int) -> list[str]:
    """
    Clear every column on the household's own row that points at a member
    (`set_up_by_member_id`, `primary_member_id`, `ai_consent_member_id`, and
    any added later — found by the `_member_id` suffix, not a list). Returns
    the columns cleared. Runs on the caller's connection; never commits.

    For the two wipes that keep the `households` row but delete its members
    (reset_household.py, create_demo_household.py). They used to leave
    these naming people who no longer existed, and `first_open_state` /
    `record_setup_adult` only ever claim a NULL setter-up: after a demo
    reset BOTH adults were greeted as "the other adult" with nobody named
    as having set it up, and nobody could ever be recorded again (defect
    hunt 2026-10-29). NULL is what a fresh household has, so the first adult
    to open the reset household is its setter-up again.
    """
    cols = [
        row[1]
        for row in conn.execute("PRAGMA table_info(households)")
        if row[1].endswith("_member_id")
    ]
    for col in cols:
        conn.execute(f"UPDATE households SET {col} = NULL WHERE id = ?", (int(household_id),))
    return cols


def _validate_passphrase(passphrase: str) -> None:
    if not passphrase or len(passphrase) < MIN_PASSPHRASE_LENGTH:
        raise ValueError(
            f"That passphrase is too short — use at least {MIN_PASSPHRASE_LENGTH} characters."
        )


__all__ = [
    "DEFAULT_HOUSEHOLD_ID",
    "MIN_PASSPHRASE_LENGTH",
    "adult_exists",
    "authenticate",
    "create_household",
    "forget_member_pointers",
    "hash_passphrase",
    "household_exists",
    "note_env_passphrase",
    "session_epoch",
    "session_still_holds",
    "list_households",
    "passphrase_in_use",
    "resolve_passphrase",
    "set_passphrase",
    "verify_passphrase",
]
