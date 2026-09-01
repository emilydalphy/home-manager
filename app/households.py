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

    Note this is inherently a "the passphrase identifies the household"
    model: if two households ever chose the same passphrase, the
    lower-numbered one would win and the other could never sign in. At beta
    scale the passphrases are handed out by Emily, so this is avoidable by
    construction rather than by code — see `create_household`, which
    refuses a passphrase already in use.
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


def passphrase_in_use(passphrase: str) -> bool:
    """True if some household already uses this passphrase."""
    return authenticate(passphrase) is not None


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
    existing = authenticate(passphrase)
    if existing is not None and existing != int(household_id):
        raise ValueError(
            f"That passphrase already belongs to household {existing}. Pick a different one — "
            "the passphrase is what tells the app which household is signing in, so two "
            "households cannot share one."
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
    if passphrase_in_use(passphrase):
        raise ValueError(
            "That passphrase is already in use by another household. Pick a different one."
        )
    conn = get_conn()
    cur = conn.execute("INSERT INTO households (name) VALUES (?)", (name,))
    household_id = cur.lastrowid
    conn.commit()
    conn.close()
    set_passphrase(household_id, passphrase)
    logger.info("Created household %s (%r)", household_id, name)
    return household_id


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
    "set_passphrase",
    "verify_passphrase",
]
