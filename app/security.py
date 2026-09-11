"""
Access control for the deployed app.

Why this exists: V1 was designed as a single-household app on localhost,
where "no auth" was a reasonable simplification. Once it's hosted on a
public URL that stops being true — every /api route, including the ones
that write and the ones that spend money calling Claude, is reachable by
anyone who has (or guesses) the hostname.

This is still deliberately NOT the multi-tenant auth system described in
the README's "Path to a sellable product" — there are no user accounts, no
usernames, and no per-person secrets. It is one shared passphrase per
*household*, which can be deleted wholesale when real accounts arrive.

Since 2026-09-11 (slice 1 of "each adult has their own login") the signed
cookie also carries WHICH ADULT is holding the device: after the passphrase,
a household with more than one adult is asked "Who's this?" once, and the
answer rides in the same HMAC-signed payload as the household id (see
`issue_session`, `with_member`, and `POST /api/whoami/pick` in main.py).
That pick is trusted per device — a second adult's own secret is a later
slice — and it is re-verified against the household's members on every use
(`tools.current_member()`), never taken on the cookie's word alone.

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
import posixpath
import secrets
import time

from starlette.concurrency import run_in_threadpool
from starlette.responses import JSONResponse, RedirectResponse

from .tools._shared import (
    DEFAULT_HOUSEHOLD_ID,
    reset_current_household_id,
    reset_current_member_id,
    set_current_household_id,
    set_current_member_id,
)
from .tools import usage as _usage
from . import households

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
    # Not public in any ordinary sense — it carries its own token and 404s
    # without one (see main.health_report). It is listed here because it is
    # deliberately CROSS-household: there is no session to bind it to, which
    # is exactly why it authenticates itself instead.
    "/api/health-report",
    "/login",
    "/logout",
    "/healthz",
    "/robots.txt",
    "/favicon.ico",
    "/static/theme.css",
    "/static/manifest.json",
    "/static/service-worker.js",
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


def issue_session(
    household_id: int = DEFAULT_HOUSEHOLD_ID,
    member_id: int | None = None,
    *,
    session_id: str | None = None,
    issued_at: int | None = None,
) -> str:
    """
    Mint a signed cookie value:
    <session-id>.<household-id>.<issued-at>.<member-id>.<hmac>.

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

    The member id (slice 1 of per-adult login, 2026-09-11) rides there too,
    under the same signature, and is 0 until an adult has been picked. It
    is a claim the server made, not a fact: `tools.current_member()`
    re-checks it against the household's members on every use, so a stale
    id reads as "nobody picked" rather than as anyone at all.

    `session_id` and `issued_at` exist for re-minting: picking an adult
    keeps the chat history (keyed on the session id) and the original
    sign-in time (so a pick does not quietly extend a 30-day session).
    """
    sid = session_id or secrets.token_urlsafe(18)
    issued = str(int(issued_at if issued_at is not None else time.time()))
    member = int(member_id) if member_id else 0
    payload = f"{sid}.{int(household_id)}.{issued}.{member}"
    sig = hmac.new(_secret(), payload.encode("utf-8"), hashlib.sha256).digest()
    return f"{payload}.{_b64(sig)}"


def _decode_session(cookie: str | None) -> tuple[str, int, int, int | None] | None:
    """
    `(session_id, household_id, issued_at, member_id)` for a validly signed,
    unexpired cookie, else None. member_id is None when no adult is picked.

    Three cookie shapes are accepted, all signature-checked:
      3 parts  <sid>.<issued>.<hmac>                 household 1, no member
      4 parts  <sid>.<household>.<issued>.<hmac>     no member
      5 parts  <sid>.<household>.<issued>.<member>.<hmac>
    The older shapes are honoured so nobody already signed in is logged out
    by a deploy — a format fallback, never an authentication one.
    """
    if not cookie:
        return None
    parts = cookie.split(".")
    raw_member = "0"
    if len(parts) == 3:
        sid, issued, sig = parts
        raw_household = str(DEFAULT_HOUSEHOLD_ID)
        payload = f"{sid}.{issued}"
    elif len(parts) == 4:
        sid, raw_household, issued, sig = parts
        payload = f"{sid}.{raw_household}.{issued}"
    elif len(parts) == 5:
        sid, raw_household, issued, raw_member, sig = parts
        payload = f"{sid}.{raw_household}.{issued}.{raw_member}"
    else:
        return None
    try:
        household_id = int(raw_household)
        member_id = int(raw_member)
    except ValueError:
        return None
    expected = hmac.new(_secret(), payload.encode("utf-8"), hashlib.sha256).digest()
    try:
        if not hmac.compare_digest(expected, _unb64(sig)):
            return None
        issued_int = int(issued)
        if time.time() - issued_int > COOKIE_MAX_AGE:
            return None
    except (ValueError, TypeError):
        return None
    return sid, household_id, issued_int, (member_id if member_id > 0 else None)


def read_session_parts(cookie: str | None) -> tuple[str, int] | None:
    """Return `(session_id, household_id)` for a valid cookie, else None."""
    decoded = _decode_session(cookie)
    return (decoded[0], decoded[1]) if decoded else None


def read_session_member(cookie: str | None) -> int | None:
    """The member id a valid cookie claims, or None (no pick / no cookie)."""
    decoded = _decode_session(cookie)
    return decoded[3] if decoded else None


def with_member(cookie: str | None, member_id: int | None) -> str | None:
    """
    Re-mint a valid cookie with an adult picked (or cleared), keeping its
    session id and sign-in time. None if the cookie is not valid.
    """
    decoded = _decode_session(cookie)
    if not decoded:
        return None
    sid, household_id, issued, _ = decoded
    return issue_session(household_id, member_id, session_id=sid, issued_at=issued)


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
    """
    Is this address one of the few that must work without signing in?

    The address is resolved before it is judged. Asking whether a path
    *starts with* "/static/icons/" is a question about text, and text can
    be written more than one way: "/static/icons/../shell.js" starts with
    the public prefix while naming a file that is not public, and the app
    served 192KB of its own source to anyone who asked that way.

    That mattered twice over. This same function decides which requests
    get an error recorded against a household, so a loose answer here was
    quietly deciding what gets written down as well as what gets served.
    """
    resolved = posixpath.normpath(path)
    # normpath drops a trailing slash, which would turn "/share/" into
    # something that no longer matches the "/share/" prefix. Keep it.
    if path.endswith("/") and not resolved.endswith("/"):
        resolved += "/"
    return resolved in _PUBLIC_EXACT or resolved.startswith(_PUBLIC_PREFIXES)


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
    # scope["path"], not request.url.path. Starlette rebuilds the URL by
    # round-tripping it through urlsplit, and a literal "#" or "?" *inside*
    # the path -- which uvicorn will happily decode out of %23 or %3F --
    # truncates everything after it. So request.url.path reported
    # "/static/icons/" for "/static/icons/%23/../../shell.js" while the
    # router and StaticFiles went on to serve the real file. The gate has
    # to judge the same string the app is going to act on.
    path = request.scope["path"]

    if is_public_path(path):
        return await call_next(request)

    # No password configured: local development. Serve localhost, refuse
    # everything else rather than falling open on a real deployment. There
    # is one household on a laptop, so the default is the right one.
    if not _password():
        if _is_local(request.client.host if request.client else None):
            # No sign-in on a laptop, but the "Who's this?" pick still
            # lives in a cookie — read it, for household 1 only, so local
            # development sees the same one-tap-then-remembered behaviour
            # as the deployed app. Any other household in it is ignored.
            local = _decode_session(request.cookies.get(COOKIE_NAME))
            member = local[3] if local and local[1] == DEFAULT_HOUSEHOLD_ID else None
            return await _call_as_household(DEFAULT_HOUSEHOLD_ID, call_next, request, member)
        logger.error(
            "Refusing a remote request because HOME_MANAGER_PASSWORD is not set. "
            "Set it in the hosting platform's environment variables."
        )
        return JSONResponse(
            {"detail": "This app isn't configured for public access yet."},
            status_code=503,
        )

    session = _decode_session(request.cookies.get(COOKIE_NAME))

    # A validly signed cookie can still name a household that isn't there
    # any more (deleted, or — today — never real to begin with, since
    # nothing yet deletes a household in practice). The signature only
    # proves the cookie was minted by this server; it says nothing about
    # whether the household inside it still exists. Left unchecked, that
    # request would still get bound to that (nonexistent) household id:
    # reads come back silently empty, indistinguishable from a genuine new
    # household with no data yet, and writes fail as unhelpful 500s — never
    # a clean "you're signed out." Caught here, before anything is bound,
    # rather than letting it dribble out as confusing failures downstream.
    # Off the event loop, same reasoning as touch_household_active below:
    # this runs on every authenticated request (not throttled the way the
    # activity write is), so a blocking SQLite call here would stall the
    # loop thread — and every concurrent request with it — on every single
    # request rather than once per 15 minutes.
    stale_cookie = False
    if session and not await run_in_threadpool(households.household_exists, session[1]):
        logger.warning(
            "Session cookie named household %s, which no longer exists — signing out.",
            session[1],
        )
        session = None
        stale_cookie = True

    if session:
        return await _call_as_household(session[1], call_next, request, session[3])

    wants_html = "text/html" in request.headers.get("accept", "")
    if wants_html and request.method == "GET":
        response = RedirectResponse(url=f"/login?next={_safe_next(path)}", status_code=303)
    else:
        response = JSONResponse({"detail": "Please sign in again."}, status_code=401)
    if stale_cookie:
        # Clear it the same way /logout does — otherwise the browser keeps
        # sending a cookie that will fail this same check forever.
        response.delete_cookie(COOKIE_NAME, path="/")
    return response


# Reading the app is not using the app.
#
# Every authenticated request stamps `last_active_at`, which exists for one
# purpose in this codebase: the morning report's "Last active" line. Once
# that report started reading over HTTP it signed in and stamped the column
# it was about to print — so after one overnight run, "the beta tester
# hasn't opened this since August 20" was gone for good and the line could
# never again say anything but "a few seconds ago". A monitor that destroys
# the signal it monitors is worse than no monitor.
#
# Kept as a path set rather than a header the caller sends, because a
# caller-settable "don't count this" flag is a way to use the app without
# appearing to.
_NON_ACTIVITY_PATHS = frozenset({"/api/observability", "/api/whoami"})


async def _call_as_household(household_id: int, call_next, request, member_id: int | None = None):
    """
    Run the rest of the request with the household (and, when the cookie
    carries one, the acting adult) bound, unbinding after.

    The reset in `finally` is not decoration: the middleware and the
    endpoint share one context, and a server that leaked the value past the
    end of a request could hand the next caller the previous caller's
    household. `tests/test_multi_household.py` interleaves requests from
    two households against exactly this. The member binding is reset the
    same way, for the same reason.

    The member id is bound as claimed, not checked here: `tools.current_member()`
    verifies it against the household's members at the point of use, so a
    pick that has gone stale costs nothing on requests that never ask.
    """
    token = set_current_household_id(household_id)
    member_token = set_current_member_id(member_id)
    try:
        if request.url.path in _NON_ACTIVITY_PATHS:
            return await call_next(request)
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
        reset_current_member_id(member_token)
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
