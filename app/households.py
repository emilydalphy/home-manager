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


def set_passphrase(household_id: int, passphrase: str) -> None:
    """Set or replace a household's passphrase."""
    _validate_passphrase(passphrase)
    existing = resolve_passphrase(passphrase)
    if existing is not None and existing != int(household_id):
        raise ValueError(
            _collision_message(existing)
        )
    conn = get_conn()
    conn.execute(
        "INSERT INTO household_credentials (household_id, password_hash) VALUES (?, ?) "
        "ON CONFLICT(household_id) DO UPDATE SET password_hash = excluded.password_hash, "
        "updated_at = datetime('now')",
        (int(household_id), hash_passphrase(passphrase)),
    )
    conn.commit()
    conn.close()


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


def _validate_passphrase(passphrase: str) -> None:
    if not passphrase or len(passphrase) < MIN_PASSPHRASE_LENGTH:
        raise ValueError(
            f"That passphrase is too short — use at least {MIN_PASSPHRASE_LENGTH} characters."
        )


__all__ = [
    "DEFAULT_HOUSEHOLD_ID",
    "MIN_PASSPHRASE_LENGTH",
    "authenticate",
    "create_household",
    "hash_passphrase",
    "household_exists",
    "list_households",
    "passphrase_in_use",
    "resolve_passphrase",
    "set_passphrase",
    "verify_passphrase",
]
