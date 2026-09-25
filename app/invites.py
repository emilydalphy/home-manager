"""
Household invites: a one-time link that signs the second adult in.

Loop Board "Invite my partner from inside the app" (Emily, 2026-09-25 — a
one-time invite link). The adult who set Pomona up taps **Invite Vineeth**;
the phone's share sheet sends a short message with a link; Vineeth opens it
and is signed in to this household, as Vineeth, without ever typing the
passphrase and without being asked "Who's this?".

The link is a credential, so it is handled like one:

- **Only a hash is stored.** The token is `secrets.token_urlsafe(32)` (256
  random bits) and the table keeps its SHA-256. A copy of the database —
  a backup, a debugging snapshot — holds nothing that opens anything. A
  fast hash is right here where `households.py` uses pbkdf2: a passphrase
  is a few guessable words and needs slowing down, a 256-bit random token
  does not, and a fast digest is what lets a redeem find its row by index
  instead of trying every stored invite.
- **The comparison is constant-time** (`hmac.compare_digest`) even after
  the index lookup, mirroring `households.verify_passphrase`.
- **One use.** Redeeming stamps `used_at` with a conditional UPDATE
  (`... AND used_at IS NULL`), so two phones opening the same link at the
  same instant cannot both get in: exactly one UPDATE changes a row.
- **Seven days.** After that the link is simply a link that has run out.
- **Scoped.** An invite names one household and one ADULT in it. Redeeming
  re-checks both at the moment of use — an adult since re-marked as a
  child, or removed, is not signed in as anyone.
- **Using one link retires every other unused link** for the same person,
  so once they're in, no second key is left lying around in someone's
  messages. Minting deliberately does NOT retire the older one: tapping
  Invite again and then cancelling the share sheet must not quietly kill
  the link already sitting in their messages (verifier, 2026-09-25).

The token travels in the link's FRAGMENT (`/join#<token>`), never its path
or query: a browser does not send the fragment to the server at all, so it
cannot land in an access log, a proxy log, a Referer header or the error
reporter's recorded path. `static/join.html` reads it and POSTs it.

Why this module is not in `app/tools/`
--------------------------------------
Same reason as `households.py`: everything in `app/tools/` is callable by
the chat agent. The model must never be able to mint a sign-in link on the
strength of a sentence somebody typed into chat, so nothing here is
re-exported from `tools/__init__.py` and nothing in `tools/` imports this.
`tests/test_household_invite.py` holds that line.
"""
from __future__ import annotations

import hashlib
import hmac
import logging
import secrets
from datetime import datetime, timedelta, timezone

from .db import get_conn
from .tools._shared import _ADULT_SQL, use_household

logger = logging.getLogger("home_manager")

INVITE_TTL = timedelta(days=7)
MAX_NAME_LENGTH = 40
_TS_FORMAT = "%Y-%m-%d %H:%M:%S"


class InviteError(ValueError):
    """A request to mint that can't be honoured — its message is for the person."""


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _stamp(moment: datetime) -> str:
    """UTC, in the same shape SQLite's datetime('now') writes."""
    return moment.astimezone(timezone.utc).strftime(_TS_FORMAT)


def _parse(stamp: str) -> datetime:
    return datetime.strptime(stamp, _TS_FORMAT).replace(tzinfo=timezone.utc)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _adult_row(conn, household_id: int, member_id: int):
    return conn.execute(
        f"SELECT id, name FROM members WHERE id = ? AND household_id = ? AND {_ADULT_SQL}",
        (int(member_id), int(household_id)),
    ).fetchone()


def mark_joined(household_id: int, member_id: int | None) -> None:
    """
    Record the day this adult first used Pomona as themselves, once.

    Preferences reads it back as "Vineeth · Joined Sep 27" in place of the
    Invite button. Stamped by redeeming an invite, by "Who's this?", by the
    shell's first read of a device that already carries a pick, and for the
    inviter when they mint — never by the overnight report, which signs in
    with no pick. Never overwritten: a new phone is not a new join.
    """
    if not member_id:
        return
    conn = get_conn()
    try:
        conn.execute(
            f"UPDATE members SET joined_at = ? WHERE id = ? AND household_id = ? "
            f"AND joined_at IS NULL AND {_ADULT_SQL}",
            (_stamp(_now()), int(member_id), int(household_id)),
        )
        conn.commit()
    finally:
        conn.close()


def add_adult(household_id: int, name: str) -> int:
    """
    Add the person being invited as an adult, by first name only — the
    card's "If the partner isn't in the household yet, the invite step asks
    for their first name". Returns their member id.

    An adult already here under that name (any case) is used as-is rather
    than duplicated. Someone here under that name who is NOT an adult is
    refused, never quietly promoted: turning a child into a signed-in adult
    is not something an invite gets to do.
    """
    clean = " ".join((name or "").split())
    if not clean:
        raise InviteError("Add their first name first.")
    if len(clean) > MAX_NAME_LENGTH:
        raise InviteError("That name's a bit long — just their first name is fine.")
    conn = get_conn()
    try:
        row = conn.execute(
            "SELECT id, age_group FROM members WHERE household_id = ? AND LOWER(name) = LOWER(?)",
            (int(household_id), clean),
        ).fetchone()
    finally:
        conn.close()
    if row is not None:
        if (row["age_group"] or "").strip().lower() != "adult":
            raise InviteError(f"{clean} is already here, not as an adult. Only adults can be invited.")
        return row["id"]
    # Through the same two writes onboarding uses, so the new adult gets an
    # avatar colour and the attendance reconcile like anyone else added.
    # Deferred import: tools pulls in most of the app.
    from . import tools

    with use_household(int(household_id)):
        member_id = tools.add_member(clean)["member_id"]
        tools.set_member_age_group(clean, "adult")
    logger.info("Invite added an adult to household %s", household_id)
    return member_id


def mint_invite(household_id: int, member_id: int, *, invited_by: int | None = None) -> str:
    """
    Make a fresh one-time link for this adult. Returns the raw token — the
    only time it exists anywhere; the table keeps its hash.

    `invited_by` is the adult doing the inviting, when the session knows
    (none during onboarding in a two-adult house, before "Who's this?" has
    been asked). They cannot invite themselves, and they are marked joined:
    they are plainly using the app.
    """
    conn = get_conn()
    try:
        target = _adult_row(conn, household_id, member_id)
        if target is None:
            # One message for "not in this household" and "not an adult",
            # so the route can't be used to learn which ids are real.
            raise InviteError("Only adults in your household can be invited.")
        if invited_by is not None and int(invited_by) == int(member_id):
            raise InviteError("That's you — you're already in.")
        token = secrets.token_urlsafe(32)
        now = _now()
        conn.execute(
            "INSERT INTO household_invites "
            "(household_id, member_id, token_hash, invited_by_member_id, created_at, expires_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                int(household_id), int(member_id), hash_token(token),
                int(invited_by) if invited_by is not None else None,
                _stamp(now), _stamp(now + INVITE_TTL),
            ),
        )
        conn.commit()
    finally:
        conn.close()
    if invited_by is not None:
        mark_joined(household_id, invited_by)
    # Never the token — the ids are enough to follow a support question.
    logger.info("Invite minted in household %s for member %s", household_id, member_id)
    return token


def redeem_invite(token: str) -> tuple[int, int] | None:
    """
    Spend a link. `(household_id, member_id)` to sign in as, or None when
    the link is unknown, already used, retired by a newer one, past its
    seven days, or names someone who is no longer an adult in a household
    that still exists. The caller shows every None the same way: this link
    has run out, ask for a new one.
    """
    if not token or not isinstance(token, str) or len(token) > 256:
        return None
    digest = hash_token(token)
    conn = get_conn()
    try:
        row = conn.execute(
            "SELECT id, household_id, member_id, token_hash, expires_at, used_at, revoked_at "
            "FROM household_invites WHERE token_hash = ?",
            (digest,),
        ).fetchone()
        if row is None or not hmac.compare_digest(row["token_hash"], digest):
            return None
        if row["used_at"] is not None or row["revoked_at"] is not None:
            return None
        now = _now()
        if _parse(row["expires_at"]) <= now:
            return None
        # The spend itself, conditional on nobody having spent it first —
        # this UPDATE is the single-use guarantee, not the read above.
        spent = conn.execute(
            "UPDATE household_invites SET used_at = ? "
            "WHERE id = ? AND used_at IS NULL AND revoked_at IS NULL",
            (_stamp(now), row["id"]),
        ).rowcount
        if spent == 1:
            # They're in: every other unused link for them retires now.
            conn.execute(
                "UPDATE household_invites SET revoked_at = ? "
                "WHERE household_id = ? AND member_id = ? AND id != ? "
                "AND used_at IS NULL AND revoked_at IS NULL",
                (_stamp(now), row["household_id"], row["member_id"], row["id"]),
            )
        conn.commit()
        if spent != 1:
            return None
        household_id, member_id = row["household_id"], row["member_id"]
        exists = conn.execute("SELECT 1 FROM households WHERE id = ?", (household_id,)).fetchone()
        if exists is None or _adult_row(conn, household_id, member_id) is None:
            return None
    finally:
        conn.close()
    mark_joined(household_id, member_id)
    logger.info("Invite redeemed in household %s for member %s", household_id, member_id)
    return household_id, member_id


def _joined_label(stamp: str | None, household_id: int) -> str:
    """ "Sep 27", on the household's own calendar (the app's date style — held.py, the band)."""
    if not stamp:
        return ""
    from .tools.cooker import household_zone

    with use_household(int(household_id)):
        zone = household_zone()
    local = _parse(stamp).astimezone(zone)
    return f"{local.strftime('%b')} {local.day}"


def household_adults_status(household_id: int, you_id: int | None) -> list[dict]:
    """
    Every adult in the household, for Preferences' invite rows: whether
    they've joined, when, and which one is the person looking.

    `you_id` is the session's adult. When the session has none (onboarding
    in a two-adult house, before "Who's this?"), the first adult entered is
    taken to be the one setting up — the first name typed on "Who are we
    planning for?" is the person typing. That guess only decides which row
    shows no Invite button; minting never relies on it.
    """
    conn = get_conn()
    try:
        rows = conn.execute(
            f"SELECT id, name, joined_at FROM members WHERE household_id = ? AND {_ADULT_SQL} "
            "AND TRIM(name) != '' ORDER BY id ASC",
            (int(household_id),),
        ).fetchall()
    finally:
        conn.close()
    if you_id is None and rows:
        you_id = rows[0]["id"]
    return [
        {
            "id": r["id"],
            "name": r["name"],
            "is_you": r["id"] == you_id,
            "joined": r["joined_at"] is not None,
            "joined_label": _joined_label(r["joined_at"], household_id),
        }
        for r in rows
    ]


__all__ = [
    "INVITE_TTL",
    "InviteError",
    "add_adult",
    "hash_token",
    "household_adults_status",
    "mark_joined",
    "mint_invite",
    "redeem_invite",
]
