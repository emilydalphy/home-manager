"""
Sign in with your email and a 6-digit code (Loop Board "App Store: anyone
can sign up — email + 6-digit code creates a new household", Emily
2026-10-05: "build that login with the email + the code like slack does it").

Why this exists: until now a household only existed if Emily ran
create_household.py, so a stranger who downloaded the app had no way in.
Now an email address is enough. A new address starts a new household and
lands in onboarding; an address an adult already signs in with opens their
household as them. The household passphrase keeps working beside it,
untouched — this module never reads, reveals or resets one.

What makes a 6-digit code safe enough (the card's rules, each one here):

  * a code works once, for ten minutes (CODE_TTL_SECONDS);
  * five tries and it is dead (MAX_ATTEMPTS). Asking for a new one retires
    the older ones asked for from the SAME place (`ip_key`) — never ones
    asked for from elsewhere, or a stranger asking for codes for your
    address would cancel the one in your inbox (review, 2026-10-06). So an
    address can have a few live codes at once, and a guess is compared
    against each and costs a try on each: the guesses an address can take
    are bounded by codes x tries, and codes per address are capped at
    EMAIL_CODES_PER_ADDRESS_HOUR in app/ratelimit.py (20 x 5 = 100 an hour
    against a 1-in-a-million code);
  * the code is never stored: only an HMAC of it under SESSION_SECRET, and
    the address is stored as an HMAC too (`email_key`), so the codes table
    names nobody. The code is never logged in production (see
    `delivery_mode`);
  * asking for a code says the same thing whatever the address — a new
    one, a known one, one that is rate-limited — so nobody can learn which
    addresses have a household by asking.

Why this module is not in `app/tools/`
--------------------------------------
Same reason as households.py and invites.py: everything in app/tools/ is
callable by the chat agent, and the model must never be able to mint a
sign-in, read a code or move an email on the strength of a sentence typed
into chat. Nothing here is re-exported from tools/__init__.py.
"""
from __future__ import annotations

import hashlib
import hmac
import logging
import os
import re
import secrets
import smtplib
import time
from email.message import EmailMessage

from . import legal
from .db import get_conn
from .tools._shared import _ADULT_SQL

logger = logging.getLogger("home_manager")

CODE_TTL_SECONDS = 10 * 60
MAX_ATTEMPTS = 5
CODE_DIGITS = 6
SIGNIN = "signin"
CHANGE = "change"

# What a new household is called until somebody names it. Nothing asks for
# a household name today (create_household.py was the only writer), and
# the column is NOT NULL.
NEW_HOUSEHOLD_NAME = "Your household"

# The one answer to "send me a code", whatever happened (see module doc).
SENT_LINE = "We've sent a code if that email can be used. It works for 10 minutes."
# What a wrong, expired, used or dead code gets — one line for all of them.
BAD_CODE_LINE = "That code didn't work. Check it, or ask for a new one."
OFF_LINE = "Email sign-up isn't switched on yet. You can still sign in with a household passphrase."

# Deliberately loose. The only real test of an address is whether the code
# arrives; this only stops obvious typing slips and anything that could
# smuggle a header line into the email (no whitespace, so no CR/LF).
_EMAIL_RE = re.compile(r"^[^@\s]{1,64}@[^@\s]+\.[^@\s]{2,}$")
MAX_EMAIL_LENGTH = 254

_SMTP_TIMEOUT_SECONDS = 15


class EmailCodeError(ValueError):
    """Something the person can fix; the message is for them."""


def normalize(email: str) -> str:
    return (email or "").strip().lower()


def valid(email: str) -> bool:
    e = normalize(email)
    return len(e) <= MAX_EMAIL_LENGTH and bool(_EMAIL_RE.match(e))


def _secret() -> bytes:
    # The session's signing key, read through security so there is exactly
    # one notion of "this server's secret" (and the ephemeral fallback in
    # local dev is the same one the cookies use).
    from . import security
    return security._secret()


def email_key(email: str) -> str:
    """The address as the codes table stores it: an HMAC, never the text."""
    return hmac.new(_secret(), b"email." + normalize(email).encode("utf-8"), hashlib.sha256).hexdigest()


def _code_hash(code_id_salt: str, code: str) -> str:
    return hmac.new(_secret(), f"code.{code_id_salt}.{code}".encode("utf-8"), hashlib.sha256).hexdigest()


# ---------- Delivery ----------


def _env(name: str) -> str:
    return (os.environ.get(name) or "").strip()


def smtp_configured() -> bool:
    """The same SMTP settings the "Something not working?" emails use
    (app/feedback_email.py) — one mail account for the app."""
    return all(_env(k) for k in ("SMTP_HOST", "SMTP_USER", "SMTP_PASSWORD"))


def _looks_like_production() -> bool:
    """A deployed server, as opposed to somebody's laptop. Either half is
    enough: a passphrase set (the app then serves the internet — see
    security.py) or Railway's own variables present."""
    return bool(
        _env("HOME_MANAGER_PASSWORD")
        or _env("RAILWAY_ENVIRONMENT")
        or _env("RAILWAY_ENVIRONMENT_NAME")
        or _env("RAILWAY_PROJECT_ID")
    )


def delivery_mode() -> str:
    """
    'smtp' — codes are emailed.
    'log'  — local development only: the code is written to the server log
             so sign-up can be tried with no mail account.
    'off'  — a deployed server with no SMTP: sign-up says it isn't switched
             on yet, and nothing is generated, stored or logged.

    'log' can never happen on a deployed server: any sign of production
    turns it into 'off', and main.py also refuses it for a request that is
    not from this machine.
    """
    if smtp_configured():
        return "smtp"
    if _looks_like_production():
        return "off"
    return "log"


def build_message(to: str, code: str) -> EmailMessage:
    msg = EmailMessage()
    msg["Subject"] = "Your Pomona code"
    msg["From"] = _env("SMTP_FROM") or _env("SMTP_USER")
    msg["To"] = to
    msg.set_content(
        f"Your Pomona code is {code}. It works for 10 minutes.\n"
        "\n"
        "If you didn't ask for it, you can ignore this email. Nobody gets in without the code.\n"
    )
    return msg


def _deliver(msg: EmailMessage) -> None:
    """The one place that touches the network; tests replace this."""
    host = _env("SMTP_HOST")
    port = int(_env("SMTP_PORT") or 587)
    with smtplib.SMTP(host, port, timeout=_SMTP_TIMEOUT_SECONDS) as smtp:
        smtp.ehlo()
        smtp.starttls()
        smtp.ehlo()
        smtp.login(_env("SMTP_USER"), _env("SMTP_PASSWORD"))
        smtp.send_message(msg)


# ---------- Codes ----------


def ip_key(caller: str) -> str:
    """Where a code was asked for from, as the table stores it: an HMAC."""
    return hmac.new(_secret(), b"ip." + (caller or "").encode("utf-8"), hashlib.sha256).hexdigest()


def _send(to: str, msg: EmailMessage, mode: str, what: str) -> bool:
    if mode == "log":
        # Local development only — see delivery_mode. Never reached on a
        # deployed server.
        logger.warning("DEV ONLY (no SMTP): %s for %s:\n%s", what, to, msg.get_content())
        return True
    try:
        _deliver(msg)
        return True
    except Exception as exc:  # a mail server being down is not a 500
        logger.warning("Sending %s failed (%s)", what, type(exc).__name__)
        return False


def send_notice(email: str, body: str, mode: str | None = None) -> bool:
    """
    A plain email that is NOT a code: "your sign-in email was changed",
    "someone tried to use this address". Sent through the same door as a
    code, so a request that ends in a notice costs the same as one that
    ends in a code and the two can't be told apart by how long they take.
    """
    mode = mode or delivery_mode()
    if mode == "off":
        return False
    e = normalize(email)
    msg = EmailMessage()
    msg["Subject"] = "About your Pomona sign-in"
    msg["From"] = _env("SMTP_FROM") or _env("SMTP_USER")
    msg["To"] = e
    msg.set_content(body)
    return _send(e, msg, mode, "a sign-in notice")


def _new_code() -> str:
    return f"{secrets.randbelow(10 ** CODE_DIGITS):0{CODE_DIGITS}d}"


def issue_code(
    email: str,
    purpose: str = SIGNIN,
    *,
    household_id: int | None = None,
    member_id: int | None = None,
    mode: str | None = None,
    caller: str = "",
) -> bool:
    """
    Make a code for this address, retire any older one asked for from the
    same place (`caller`), and send it.
    True if a code went out (or, in 'log' mode, was written to the log).

    Never raises for a delivery failure: the caller says the same thing
    either way, and the failure is logged by its class name only.
    """
    mode = mode or delivery_mode()
    if mode == "off":
        return False
    e = normalize(email)
    key = email_key(e)
    where = ip_key(caller)
    code = _new_code()
    salt = secrets.token_hex(8)
    now = int(time.time())
    conn = get_conn()
    try:
        # Housekeeping first: codes that can no longer be used are only
        # rows of HMACs, but there is no reason to keep them.
        conn.execute("DELETE FROM email_codes WHERE expires_at < ?", (now - 24 * 3600,))
        conn.execute(
            "UPDATE email_codes SET used_at = ? WHERE email_key = ? AND purpose = ? AND ip_key = ? AND used_at IS NULL",
            (now, key, purpose, where),
        )
        conn.execute(
            "INSERT INTO email_codes (email_key, purpose, household_id, member_id, code_hash, ip_key, expires_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (key, purpose, household_id, member_id, f"{salt}${_code_hash(salt, code)}", where, now + CODE_TTL_SECONDS),
        )
        conn.commit()
    finally:
        conn.close()
    if mode == "log":
        # Local development only — see delivery_mode. Never reached on a
        # deployed server.
        logger.warning("DEV ONLY (no SMTP): the Pomona code for %s is %s", e, code)
        return True
    return _send(e, build_message(e, code), mode, "a sign-in code")


def check_code(
    email: str,
    code: str,
    purpose: str = SIGNIN,
    *,
    household_id: int | None = None,
    member_id: int | None = None,
) -> bool:
    """
    Spend a code. True exactly once, for a live code for this address (and,
    for a 'change' code, this household and adult).

    There can be more than one live code — one per place it was asked for
    from (see issue_code) — so the guess is compared against each, and
    costs a try on EACH before it is compared: one UPDATE per code that only
    succeeds while that code is live and has tries left. Two guesses racing
    cannot both slip in under the limit, and a right code on its sixth try
    is refused like a wrong one.
    """
    code = re.sub(r"\s", "", code or "")
    if not re.fullmatch(rf"\d{{{CODE_DIGITS}}}", code):
        # Not a code at all. Still costs a try: anything else would let a
        # script learn something by sending junk.
        code = ""
    key = email_key(email)
    now = int(time.time())
    conn = get_conn()
    try:
        sql = (
            "SELECT id, code_hash FROM email_codes WHERE email_key = ? AND purpose = ? "
            "AND used_at IS NULL AND expires_at > ? AND attempts < ?"
        )
        args: list = [key, purpose, now, MAX_ATTEMPTS]
        if purpose == CHANGE:
            sql += " AND household_id = ? AND member_id = ?"
            args += [int(household_id or 0), int(member_id or 0)]
        rows = conn.execute(sql + " ORDER BY id DESC", args).fetchall()
        for row in rows:
            claimed = conn.execute(
                "UPDATE email_codes SET attempts = attempts + 1 "
                "WHERE id = ? AND used_at IS NULL AND expires_at > ? AND attempts < ?",
                (row["id"], now, MAX_ATTEMPTS),
            ).rowcount
            conn.commit()
            if not claimed:
                continue
            salt, _, stored = row["code_hash"].partition("$")
            if not code or not hmac.compare_digest(stored, _code_hash(salt, code)):
                continue
            spent = conn.execute(
                "UPDATE email_codes SET used_at = ? WHERE id = ? AND used_at IS NULL", (now, row["id"])
            ).rowcount
            conn.commit()
            return spent == 1
        return False
    finally:
        conn.close()


# ---------- Who an address belongs to ----------


def lookup(email: str) -> tuple[int, int | None] | None:
    """
    (household_id, member_id) for an address that signs in somewhere, or
    None. member_id is None for a household whose setup hasn't named its
    main person yet. An adult who is no longer an adult of that household
    (removed, or their household gone) does not count.
    """
    e = normalize(email)
    conn = get_conn()
    try:
        row = conn.execute(
            f"SELECT me.household_id, me.member_id FROM member_emails me "
            f"JOIN members ON members.id = me.member_id AND members.household_id = me.household_id "
            f"WHERE me.email = ? AND {_ADULT_SQL}",
            (e,),
        ).fetchone()
        if row:
            return int(row["household_id"]), int(row["member_id"])
        row = conn.execute(
            "SELECT se.household_id FROM signup_emails se "
            "JOIN households h ON h.id = se.household_id WHERE se.email = ?",
            (e,),
        ).fetchone()
        if row:
            return int(row["household_id"]), None
        return None
    finally:
        conn.close()


def in_use(email: str) -> bool:
    """Does this address sign in somewhere? A row naming somebody who is no
    longer an adult there does not count (lookup's own rule)."""
    return lookup(email) is not None


def _release_stale(conn, email: str) -> None:
    """Forget this address where it names somebody who is no longer an adult
    of that household — see create_household_for for why."""
    conn.execute(
        f"DELETE FROM member_emails WHERE email = ? AND NOT EXISTS ("
        f"SELECT 1 FROM members WHERE members.id = member_emails.member_id "
        f"AND members.household_id = member_emails.household_id AND {_ADULT_SQL})",
        (email,),
    )


def create_household_for(email: str) -> int:
    """
    A new, empty household for a new address, with the address waiting for
    its main person and the terms version recorded. No passphrase: this
    household signs in by email, and nothing here makes or reveals one.
    """
    e = normalize(email)
    conn = get_conn()
    try:
        conn.execute("BEGIN IMMEDIATE")
        # An address still filed against somebody who is no longer an adult
        # of that household (made a child, or the row outlived them) opens
        # nothing — lookup() already says so — and left here it would make
        # this address a dead end for ever: every verify would burn a good
        # code and then refuse (review, 2026-10-06). It is released, and the
        # address starts afresh like any new one.
        _release_stale(conn, e)
        # Re-checked inside the write lock: two verifies racing for one new
        # address must not make two households.
        if conn.execute("SELECT 1 FROM signup_emails WHERE email = ?", (e,)).fetchone() or conn.execute(
            "SELECT 1 FROM member_emails WHERE email = ?", (e,)
        ).fetchone():
            conn.rollback()
            raise EmailCodeError("That email already has a household.")
        hid = conn.execute("INSERT INTO households (name) VALUES (?)", (NEW_HOUSEHOLD_NAME,)).lastrowid
        conn.execute("INSERT INTO signup_emails (email, household_id) VALUES (?, ?)", (e, hid))
        conn.execute(
            "INSERT INTO legal_acceptances (household_id, version) VALUES (?, ?)", (hid, legal.LEGAL_VERSION)
        )
        conn.commit()
    finally:
        conn.close()
    logger.info("Email sign-up created household %s (terms %s)", hid, legal.LEGAL_VERSION)
    return int(hid)


def claim_signup_email(household_id: int, member_id: int | None) -> bool:
    """
    Onboarding has named the main person: the address that started the
    household becomes theirs. Only for an adult of this household, and only
    if they have no address yet. True if it moved.
    """
    if member_id is None:
        return False
    hid, mid = int(household_id), int(member_id)
    conn = get_conn()
    try:
        row = conn.execute("SELECT email FROM signup_emails WHERE household_id = ?", (hid,)).fetchone()
        if row is None:
            return False
        if not conn.execute(
            f"SELECT 1 FROM members WHERE id = ? AND household_id = ? AND {_ADULT_SQL}", (mid, hid)
        ).fetchone():
            return False
        if conn.execute("SELECT 1 FROM member_emails WHERE member_id = ?", (mid,)).fetchone():
            return False
        conn.execute(
            "INSERT INTO member_emails (email, household_id, member_id) VALUES (?, ?, ?)", (row["email"], hid, mid)
        )
        conn.execute("DELETE FROM signup_emails WHERE household_id = ?", (hid,))
        conn.commit()
    finally:
        conn.close()
    logger.info("Household %s: the sign-up email now belongs to member %s", hid, mid)
    return True


def email_for_member(household_id: int, member_id: int) -> str | None:
    conn = get_conn()
    try:
        row = conn.execute(
            "SELECT email FROM member_emails WHERE household_id = ? AND member_id = ?",
            (int(household_id), int(member_id)),
        ).fetchone()
        return row["email"] if row else None
    finally:
        conn.close()


def set_member_email(household_id: int, member_id: int, email: str) -> str:
    """Give an adult this (already verified) address, replacing any they had."""
    e = normalize(email)
    hid, mid = int(household_id), int(member_id)
    conn = get_conn()
    try:
        conn.execute("BEGIN IMMEDIATE")
        if not conn.execute(
            f"SELECT 1 FROM members WHERE id = ? AND household_id = ? AND {_ADULT_SQL}", (mid, hid)
        ).fetchone():
            conn.rollback()
            raise EmailCodeError("Only an adult in the household can sign in with an email.")
        _release_stale(conn, e)
        taken = conn.execute("SELECT member_id FROM member_emails WHERE email = ?", (e,)).fetchone()
        if (taken and int(taken["member_id"]) != mid) or conn.execute(
            "SELECT 1 FROM signup_emails WHERE email = ? AND household_id != ?", (e, hid)
        ).fetchone():
            conn.rollback()
            raise EmailCodeError("That email is already used in Pomona.")
        conn.execute("DELETE FROM member_emails WHERE member_id = ?", (mid,))
        conn.execute("INSERT INTO member_emails (email, household_id, member_id) VALUES (?, ?, ?)", (e, hid, mid))
        # The household's own sign-up address, now claimed by this adult.
        conn.execute("DELETE FROM signup_emails WHERE email = ? AND household_id = ?", (e, hid))
        conn.commit()
    finally:
        conn.close()
    return e


def accepted_legal_version(household_id: int) -> str | None:
    """The terms version this household agreed to at sign-up, if it signed up that way."""
    conn = get_conn()
    try:
        row = conn.execute(
            "SELECT version FROM legal_acceptances WHERE household_id = ? ORDER BY id DESC LIMIT 1",
            (int(household_id),),
        ).fetchone()
        return row["version"] if row else None
    finally:
        conn.close()
