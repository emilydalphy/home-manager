"""
Pin SQLite's clock to the same instant Python's is pinned to.

freezegun can only reach Python. `datetime('now')` is evaluated inside SQLite,
in C, under Python's feet — and app/ has 181 of them, every `created_at`,
`updated_at` and "has this been touched today?" in the application. So a run
pinned with freezegun alone has the app reasoning on the pinned date and the
database stamping rows with the real one, and the two disagree the moment the
pin moves a single day: ten tests failed on a one-day pin for no other reason,
none of them about anything a household would notice.

HOW IT WORKS, and why it is not a reimplementation. SQLite's date functions
take an ISO timestamp wherever they take the literal 'now' — `datetime('now',
'-7 days')` and `datetime('2026-09-20 09:00:00', '-7 days')` are the same
question. So the override does one thing: swap the word 'now' for the pinned
instant and hand the call straight back to SQLite's own implementation, on a
second connection that has no overrides on it. Every modifier, every format
string, every edge of SQLite's date language keeps being SQLite's answer.
Rewriting that language in Python would have been the other option and a much
worse one — a shim that is subtly wrong makes every test lie.

WHAT IT COVERS. `date`, `datetime` and `strftime` are the only three the app
ever calls with 'now' (181 sites; checked, not assumed). The other four are
overridden anyway so a new call site is pinned the day it is written rather
than the day somebody notices. CURRENT_TIMESTAMP is a keyword rather than a
function and cannot be overridden — there are zero of those in app/ and
schema.sql, which is what makes this complete instead of a partial pin, and a
partial pin would be worse than none.

`sqlite3.connect` itself is wrapped rather than `app.db.get_conn`, for the same
reason this uses freezegun rather than a monkeypatch: several modules import
`get_conn` by name at import time and hold the original, so patching the name
in app.db would reach some call sites and not others. Nothing opens a SQLite
connection without going through `sqlite3.connect`.

Test-only. Nothing under app/ imports this, and it is inert unless a run is
pinned.
"""
from __future__ import annotations

import sqlite3
import threading

# The functions SQLite will answer 'now' for. The first three are the ones this
# app actually uses; the rest are here so a new call site is covered on the day
# it is written.
_CLOCK_FUNCTIONS = ("date", "datetime", "time", "julianday", "unixepoch", "strftime", "timediff")

_real_connect = sqlite3.connect
_local = threading.local()
# Counted, not a flag: a @pytest.mark.today inside a --today run installs a
# second time, and its uninstall must not take SQLite's clock back off the pin
# the rest of the session is still holding.
_depth = 0


def _shadow():
    """
    A connection with no overrides on it, to answer the real question.

    Thread-local rather than shared-with-a-lock: the app's sync routes run in
    Starlette's threadpool, and a SQLite connection is not safe to share across
    threads. One tiny in-memory connection per thread is cheaper than the lock
    would be anyway.
    """
    conn = getattr(_local, "shadow", None)
    if conn is None:
        conn = _real_connect(":memory:")
        _local.shadow = conn
    return conn


def _make_override(name, now_provider):
    def _call(*args):
        args = list(args)
        # 'now' is only ever a timevalue, so position does not matter — which
        # is what lets one rule serve strftime (format first) and date
        # (timevalue first) without either knowing about the other.
        swapped = False
        for i, arg in enumerate(args):
            if isinstance(arg, str) and arg.strip().lower() == "now":
                args[i] = now_provider()
                swapped = True
        if not swapped:
            # `date()` and `datetime()` with no timevalue mean 'now' too, and
            # so does `strftime(fmt)` with nothing after the format.
            if not args and name in ("date", "datetime", "time", "julianday", "unixepoch"):
                args = [now_provider()]
            elif len(args) == 1 and name == "strftime":
                args = [args[0], now_provider()]
        placeholders = ", ".join("?" for _ in args)
        row = _shadow().execute(f"SELECT {name}({placeholders})", args).fetchone()
        return row[0]

    return _call


def _install_on(conn, now_provider):
    for name in _CLOCK_FUNCTIONS:
        try:
            conn.create_function(name, -1, _make_override(name, now_provider))
        except sqlite3.NotSupportedError:  # a build without this function
            pass
    return conn


def install(now_provider):
    """
    Point every SQLite connection opened from here on at `now_provider()`.

    `now_provider` returns the pinned instant as a UTC ISO string — UTC because
    that is what SQLite's own 'now' means, so substituting it changes the date
    and nothing else about what the app was already computing.
    """
    global _depth
    _depth += 1
    if _depth > 1:
        # The provider reads the live (frozen) clock every call, so the one
        # installed first already answers for whatever is pinned now.
        return

    def _connect(*args, **kwargs):
        return _install_on(_real_connect(*args, **kwargs), now_provider)

    sqlite3.connect = _connect


def uninstall():
    global _depth
    _depth = max(0, _depth - 1)
    if _depth == 0:
        sqlite3.connect = _real_connect
