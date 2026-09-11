"""
Values every domain module needs.

Which household am I working on?
--------------------------------
Every query in this package is scoped to one household. That household used
to be the constant ``HOUSEHOLD_ID = 1``, because V1 had exactly one. It is
now a **request-scoped** value: ``household_id()`` reads a ``ContextVar``
that the web layer sets once per request, from the signed session cookie
(or, on the public share routes, from the share token itself).

Why a ContextVar rather than threading a parameter through every function:
there are ~230 call sites across 18 modules, and every one of them is a
plain SQL bind inside a function body. Adding a parameter to all of them
would be a far larger diff, would change the signature of every tool the
chat agent calls (``TOOL_FUNCTIONS`` in agent.py is built from these), and
would give the model an argument it could get *wrong*. A ContextVar keeps
the household out of the model's reach entirely — it is set by the server
from the cookie and cannot be influenced by anything the model says.

``ContextVar`` is also the right primitive specifically because the app
serves ``def`` (sync) routes: Starlette runs those in a worker thread, and
``anyio`` copies the calling context into that thread, so a value set in
middleware is visible to the route and to everything it calls — while
staying isolated between concurrent requests in a way a module-level global
never could be. ``tests/test_multi_household.py`` pins that propagation
down with a real request, because if it ever silently stopped working every
request would quietly read the default household — i.e. the beta tester
would see Emily's family data. That is the failure this whole file exists
to prevent, so it is tested rather than assumed.

The default is 1, which is what keeps every non-web caller working
unchanged: ``seed.py``, ``reset_household.py``, one-off scripts and the
existing test suite all still operate on Emily's household without knowing
this mechanism exists.

Note there is deliberately **no** ``HOUSEHOLD_ID`` constant any more. If
some call site is ever missed, or a new one is written from memory against
the old name, it raises ImportError/NameError immediately instead of
silently reading household 1 — a loud failure rather than a cross-household
data leak.
"""
from __future__ import annotations

import os
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Iterator


# Emily's household, and the household every non-web entry point works on.
DEFAULT_HOUSEHOLD_ID = 1

_current_household_id: ContextVar[int] = ContextVar(
    "current_household_id", default=DEFAULT_HOUSEHOLD_ID
)


def household_id() -> int:
    """The household this call is operating on."""
    return _current_household_id.get()


def set_current_household_id(value: int):
    """
    Bind the household for the current context; returns a reset token.

    Prefer ``use_household`` — this exists for the ASGI middleware, which
    sets on the way in and resets on the way out across an await boundary
    and so cannot use a ``with`` block spanning both.
    """
    return _current_household_id.set(int(value))


def reset_current_household_id(token) -> None:
    _current_household_id.reset(token)


@contextmanager
def use_household(value: int) -> Iterator[int]:
    """
    Run a block as a specific household, restoring the previous one after.

    Used by the public share-link paths, where the token — not a login —
    is what identifies the household, and by the tests that prove two
    households stay isolated.
    """
    token = _current_household_id.set(int(value))
    try:
        yield int(value)
    finally:
        _current_household_id.reset(token)


# ---------- Which adult is acting? ----------
#
# Slice 1 of "each adult has their own login" (Loop Board, 2026-09-11). The
# household passphrase opens the door; a one-tap "Who's this?" pick, kept in
# the same signed cookie, says which adult is holding the phone. It rides
# next to the household id for the same reason the household does: set by
# the server from the cookie, out of the model's reach, request-scoped.
#
# Two different questions, two different functions:
#
#   member_id()      the id the cookie CLAIMS. Cheap, no database. May be
#                    stale — the member could have been removed, or marked a
#                    child, since the pick.
#   current_member() the adult that claim actually resolves to, checked
#                    against the household's own members table on every
#                    call. This is the one writes and notifications use. A
#                    claim that no longer checks out is simply "nobody is
#                    picked" — never an error, and never another
#                    household's member, because the lookup is scoped to
#                    household_id() as well as the id.
#
# A household with exactly one adult never sees the question, and
# current_member() answers with that adult without a pick. That is a
# deliberate inference rather than a stored fact: there is no one else it
# could be, and asking would be an extra step for nothing.
#
# Default None: scripts, tests and the chat agent's tool calls run with no
# adult picked unless a request set one, and every write below falls back
# to the free-text name it accepted before this existed.

_current_member_id: ContextVar[int | None] = ContextVar("current_member_id", default=None)


def member_id() -> int | None:
    """The member id the session claims, unverified — see current_member()."""
    return _current_member_id.get()


def set_current_member_id(value: int | None):
    """Bind the acting member for the current context; returns a reset token."""
    return _current_member_id.set(int(value) if value is not None else None)


def reset_current_member_id(token) -> None:
    _current_member_id.reset(token)


@contextmanager
def use_member(value: int | None) -> Iterator[int | None]:
    """Run a block as a specific adult (or as nobody), restoring after."""
    token = _current_member_id.set(int(value) if value is not None else None)
    try:
        yield value
    finally:
        _current_member_id.reset(token)


_ADULT_SQL = "LOWER(TRIM(age_group)) = 'adult'"


def _member_row_dict(row) -> dict:
    name = (row["name"] or "").strip()
    return {
        "id": row["id"],
        "name": name,
        "initial": (name[:1] or "?").upper(),
        "color": row["color"] or "",
    }


def household_adults() -> list[dict]:
    """The household's adults, in creation order — the pick list."""
    from ..db import get_conn  # db has no app imports, but keep this lazy

    conn = get_conn()
    rows = conn.execute(
        f"SELECT id, name, color FROM members WHERE household_id = ? AND {_ADULT_SQL} ORDER BY id ASC",
        (household_id(),),
    ).fetchall()
    conn.close()
    return [_member_row_dict(r) for r in rows]


def current_member() -> dict | None:
    """
    The adult acting on this request, verified, or None.

    Verified means: the claimed id names an adult in THIS household right
    now. Anything else — a removed member, one re-marked as a child, an id
    from some other household — reads as no pick. A household with exactly
    one adult resolves to that adult with no pick at all.
    """
    from ..db import get_conn

    claimed = member_id()
    conn = get_conn()
    if claimed is not None:
        row = conn.execute(
            f"SELECT id, name, color FROM members WHERE id = ? AND household_id = ? AND {_ADULT_SQL}",
            (claimed, household_id()),
        ).fetchone()
        if row is not None:
            conn.close()
            return _member_row_dict(row)
    rows = conn.execute(
        f"SELECT id, name, color FROM members WHERE household_id = ? AND {_ADULT_SQL} ORDER BY id ASC LIMIT 2",
        (household_id(),),
    ).fetchall()
    conn.close()
    if len(rows) == 1:
        return _member_row_dict(rows[0])
    return None


# The values a client sends when it does not know who is acting. Anything
# else — a real name, or "ai" for a row the planner added itself — is kept.
_UNATTRIBUTED = frozenset({"", "user"})


def acting_name(supplied: str | None = "") -> str:
    """
    The name to record on a write: the session's adult when the caller did
    not name one, otherwise what the caller said.

    Older clients and the grocery offline queue still send a free-text
    author (or nothing, which arrives as "" or "user"); the chat agent may
    pass a name the household said out loud ("Vineeth approved it"). Those
    explicit names are kept — the session fills in the blanks, it does not
    overrule a person who was named. "ai" is never a person and is never
    replaced.
    """
    supplied = (supplied or "").strip()
    if supplied.lower() not in _UNATTRIBUTED:
        return supplied
    member = current_member()
    return member["name"] if member else supplied


def acting_member_id_for(name: str) -> int | None:
    """
    The session member's id, but only when `name` is that member's name —
    so a member id never gets attached to a name that is not theirs.
    """
    member = current_member()
    if member and (name or "").strip().lower() == member["name"].lower():
        return member["id"]
    return None


# The app's own public URL, so a tool can hand back a real, absolute link
# (e.g. for the Eater self-service link) instead of the chat agent having
# to guess/type out a domain itself — which it has no way to know and will
# otherwise hallucinate. Set via Railway (or wherever this is hosted) env
# vars, e.g. PUBLIC_BASE_URL=https://home-manager-production-4949.up.railway.app
# (no trailing slash). Falls back to a relative path if unset (e.g. local
# dev), which still works fine since the app only has one host there.
PUBLIC_BASE_URL = os.environ.get("PUBLIC_BASE_URL", "").rstrip("/")


def _absolute_url(path: str) -> str:
    return f"{PUBLIC_BASE_URL}{path}" if PUBLIC_BASE_URL else path


def require_household_row(conn, table: str, id_value: int, *, label: str, id_column: str = "id") -> None:
    """
    Confirm a row exists **in the caller's own household** before writing to
    it, or raise ``ValueError``.

    This exists to close a specific class of bug found by the multi-household
    security review: a write scoped with a plain
    ``WHERE id = ? AND household_id = ?`` silently matches zero rows for an
    id that belongs to another household, and the function still returned
    its normal success shape — so an accidental (or probing) foreign id
    looked, from the outside, exactly like the write had happened.

    The fix is this upfront check, not a smarter error message. The
    ``ValueError`` text is deliberately the *same* regardless of *why* the
    row wasn't found — wrong household, or never existed at all. Do not
    special-case either branch (e.g. "belongs to another household" vs.
    "doesn't exist") in the message: keeping those two cases
    indistinguishable to the caller is a verified property from the
    original review, not an accident. A caller cannot use these functions
    to probe whether *some other household's* id is real.
    """
    row = conn.execute(
        f"SELECT 1 FROM {table} WHERE {id_column} = ? AND household_id = ?",
        (id_value, household_id()),
    ).fetchone()
    if row is None:
        # Close on the caller's behalf — every call site is about to bail
        # out via the exception below, matching the existing
        # close-then-raise idiom the rest of this package already uses
        # (see e.g. move_grocery_item_to_inventory).
        conn.close()
        raise ValueError(f"No {label} with id {id_value}.")
