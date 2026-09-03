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
