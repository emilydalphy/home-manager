"""
Access control for the deployed app.

Why this exists: V1 was designed as a single-household app on localhost,
where "no auth" was a reasonable simplification. Once it's hosted on a
public URL that stops being true — every /api route, including the ones
that write and the ones that spend money calling Claude, is reachable by
anyone who has (or guesses) the hostname.

This is still deliberately NOT the multi-tenant auth system described in
the README's "Path to a sellable product" — there are no user accounts, no
usernames, and no per-person logins. It is one shared passphrase per
*household*, which can be deleted wholesale when real accounts arrive.

What changed for the beta: signing in now establishes **which household**
the session belongs to, not merely that the caller is allowed in. The
household id travels in the signed cookie, and `auth_middleware` binds it
for the request so every query underneath is scoped to it. See
`app/households.py` for where household passphrases are stored, and
`app/tools/_shared.py` for how the binding reaches the queries.

Two env vars:

  HOME_MANAGER_PASSWORD  household 1's passphrase — i.e. Emily's. Kept
                         exactly as it was, so her deployment needs no
                         migration and no new secret; a second household
                         gets a stored passphrase instead (see
                         `app/households.py`). If unset, the app still runs
                         but only answers requests from localhost — so
                         `uvicorn --reload` on your laptop keeps working
                         with no setup, while a deploy that forgot to set
                         it fails closed instead of silently serving the
                         household to the internet.

  SESSION_SECRET         key used to sign the login cookie. If unset, a
                         random one is generated at startup, which works
                         fine but logs everyone out on every restart/deploy.
                         Set it in Railway to avoid that.
"""
import base64
import hashlib
import hmac
import logging
import os
import secrets
import time

from starlette.concurrency import run_in_threadpool
from starlette.responses import JSONResponse, RedirectResponse

from .tools._shared import (
    DEFAULT_HOUSEHOLD_ID,
    reset_current_household_id,
    set_current_household_id,
)
from .tools import usage as _usage

logger = logging.getLogger("home_manager")

COOKIE_NAME = "hm_session"
COOKIE_MAX_AGE = 30 * 24 * 60 * 60  # 30 days

# Paths that must stay reachable without logging in.
#
# The two share flows are public by design — an Eater gets a tokenized link
# and should never be asked for the household password. The token itself is
# the credential there (secrets.token_urlsafe(16), see tools.py).
#
# The static files listed are only the ones those public pages and the PWA
# install actually need: the shared stylesheet, the icons, the manifest and
# the service worker. Everything else under /static (the real app pages)
# requires a session like any other route.
_PUBLIC_PREFIXES = (
    "/share/",
    "/member-share/",
    "/api/share/",
    "/api/member-share/",
    "/static/icons/",
)
_PUBLIC_EXACT = frozenset({
    "/login",
    "/logout",
    "/healthz",
    "/robots.txt",
    "/favicon.ico",
    "/static/theme.css",
    "/static/manifest.json",
    "/static/service-worker.js",
    # The share pages are public, so the error reporter and the endpoint it
    # posts to have to be reachable signed-out — otherwise the two screens
    # a beta tester can reach without an account are the only two that
    # report nothing, which is exactly backwards. See
    # report_client_error for how the share token is kept out of the row.
    "/static/error-reporter.js",
    "/api/client-error",
})

_LOCAL_HOSTS = frozenset({"127.0.0.1", "::1", "localhost", "testclient"})


def _password() -> str:
    return os.environ.get("HOME_MANAGER_PASSWORD", "")


def _secret() -> bytes:
    configured = os.environ.get("SESSION_SECRET")
    if configured:
        return configured.encode("utf-8")
    global _EPHEMERAL_SECRET
    if _EPHEMERAL_SECRET is None:
        _EPHEMERAL_SECRET = secrets.token_bytes(32)
        logger.warning(
            "SESSION_SECRET is not set — using a random one for this process. "
            "Logins will not survive a restart. Set SESSION_SECRET to fix."
        )
    return _EPHEMERAL_SECRET


_EPHEMERAL_SECRET: bytes | None = None


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def issue_session(household_id: int = DEFAULT_HOUSEHOLD_ID) -> str:
    """
    Mint a signed cookie value: <session-id>.<household-id>.<issued-at>.<hmac>.

    The session id is what /api/chat keys its conversation history on, so it
    has to be unguessable and server-generated — the bug this replaces was
    taking that id from the request body, where anyone could send the
    literal string "default" and land in the household's history.

    The household id rides in the same signed payload. That is what makes
    this session *belong to* a household rather than merely proving the
    caller knew a password. It cannot be tampered with: the HMAC covers the
    whole payload, so editing the household id invalidates the signature
    and the cookie stops being accepted at all — it does not fall back to
    some other household.
    """
    sid = secrets.token_urlsafe(18)
    issued = str(int(time.time()))
    payload = f"{sid}.{int(household_id)}.{issued}"
    sig = hmac.new(_secret(), payload.encode("utf-8"), hashlib.sha256).digest()
    return f"{payload}.{_b64(sig)}"


def read_session_parts(cookie: str | None) -> tuple[str, int] | None:
    """
    Return `(session_id, household_id)` for a validly signed, unexpired
    cookie, else None.

    Accepts the older three-part `<sid>.<issued>.<hmac>` cookie and reads it
    as household 1. Those cookies were all minted when the app had exactly
    one household, so household 1 is what they actually mean — and honouring
    them means Emily (and anyone else already signed in) is not silently
    logged out by this change. They are still signature-checked, so this is
    a format fallback, not an authentication one.
    """
    if not cookie:
        return None
    parts = cookie.split(".")
    if len(parts) == 3:
        sid, issued, sig = parts
        household_id = DEFAULT_HOUSEHOLD_ID
        payload = f"{sid}.{issued}"
    elif len(parts) == 4:
        sid, raw_household, issued, sig = parts
        payload = f"{sid}.{raw_household}.{issued}"
        try:
            household_id = int(raw_household)
        except ValueError:
            return None
    else:
        return None
    expected = hmac.new(_secret(), payload.encode("utf-8"), hashlib.sha256).digest()
    try:
        if not hmac.compare_digest(expected, _unb64(sig)):
            return None
        if time.time() - int(issued) > COOKIE_MAX_AGE:
            return None
    except (ValueError, TypeError):
        return None
    return sid, household_id


def read_session(cookie: str | None) -> str | None:
    """Return the session id if the cookie is validly signed and unexpired, else None."""
    parts = read_session_parts(cookie)
    return parts[0] if parts else None


def read_session_household(cookie: str | None) -> int | None:
    """Return the household this cookie belongs to, or None if it isn't valid."""
    parts = read_session_parts(cookie)
    return parts[1] if parts else None


def check_password(candidate: str) -> bool:
    """Constant-time comparison — never `==` on a secret."""
    expected = _password()
    if not expected:
        return False
    return hmac.compare_digest(candidate.encode("utf-8"), expected.encode("utf-8"))


def is_public_path(path: str) -> bool:
    return path in _PUBLIC_EXACT or path.startswith(_PUBLIC_PREFIXES)


def _is_local(client_host: str | None) -> bool:
    return (client_host or "") in _LOCAL_HOSTS


async def auth_middleware(request, call_next):
    """
    Gate every non-public route behind the household's passphrase, and bind
    the request to the household that passphrase signed into.

    An unauthenticated request gets a redirect if it's a browser asking for
    a page, and a plain 401 JSON body if it's a fetch() — so the app's own
    API calls fail readably instead of receiving a login page where they
    expected data.

    Binding the household here, rather than in each route, is what makes
    isolation the default: a route cannot forget to scope itself, because
    scoping is not something a route does. Every query underneath reads
    `tools.household_id()`, which reads the ContextVar this sets.

    Public paths are deliberately left unbound. Those are the share links,
    where the share *token* identifies the household — resolved in
    `tools/sharing.py`, which binds the household itself for the duration
    of the lookup. Binding the default here would be the bug: a household-2
    share link would render household 1's dinners.
    """
    path = request.url.path

    if is_public_path(path):
        return await call_next(request)

    # No password configured: local development. Serve localhost, refuse
    # everything else rather than falling open on a real deployment. There
    # is one household on a laptop, so the default is the right one.
    if not _password():
        if _is_local(request.client.host if request.client else None):
            return await _call_as_household(DEFAULT_HOUSEHOLD_ID, call_next, request)
        logger.error(
            "Refusing a remote request because HOME_MANAGER_PASSWORD is not set. "
            "Set it in the hosting platform's environment variables."
        )
        return JSONResponse(
            {"detail": "This app isn't configured for public access yet."},
            status_code=503,
        )

    session = read_session_parts(request.cookies.get(COOKIE_NAME))
    if session:
        return await _call_as_household(session[1], call_next, request)

    wants_html = "text/html" in request.headers.get("accept", "")
    if wants_html and request.method == "GET":
        return RedirectResponse(url=f"/login?next={_safe_next(path)}", status_code=303)
    return JSONResponse({"detail": "Please sign in again."}, status_code=401)


async def _call_as_household(household_id: int, call_next, request):
    """
    Run the rest of the request with the household bound, unbinding after.

    The reset in `finally` is not decoration: the middleware and the
    endpoint share one context, and a server that leaked the value past the
    end of a request could hand the next caller the previous caller's
    household. `tests/test_multi_household.py` interleaves requests from
    two households against exactly this.
    """
    token = set_current_household_id(household_id)
    try:
        # Note that they're here. Throttled to one write per household per
        # 15 minutes inside touch_household_active, and it never raises —
        # a bookkeeping column must not be able to fail a real request.
        #
        # Off the event loop: this middleware is async, and SQLite writes
        # block. get_conn sets no busy timeout, so under write contention
        # a direct call could stall the loop thread — and therefore every
        # concurrent request — waiting on a lock, for a column read at day
        # granularity. Rare enough that it would only ever bite in the
        # exact conditions nobody could reproduce.
        await run_in_threadpool(_usage.touch_household_active, household_id)
        return await call_next(request)
    finally:
        reset_current_household_id(token)


def _safe_next(path: str) -> str:
    """
    Only ever hand back a same-site absolute path. Guards the classic open
    redirect where ?next=https://evil.example bounces the user off-site
    after a successful login.
    """
    if not path.startswith("/") or path.startswith("//"):
        return "/"
    return path


def sanitize_next(raw: str | None) -> str:
    return _safe_next(raw or "/")
