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

Since 2026-10-05 the pick also survives the session it was made in. The
beta tester was asked "Who's this?" on every open, because the answer lived
only in a cookie that every /login replaced and that expired thirty days
after the passphrase was typed whether or not the phone was in daily use.
So there are now two things, with two different lives: the session cookie,
which ends when somebody signs out and renews itself while they keep
using the app (`_renew_if_due`), and a long-lived `pomona_device_member`
cookie saying which adult this BROWSER is (`device_token`), which /login
re-applies when the passphrase opens the household it names
(`device_member_for_login`). The device one is deliberately not cleared by
signing out: a phone signed out and back in is the same phone.

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

# How old a session cookie has to be before an ordinary request re-mints
# it (`_renew_if_due`). The 30 days above were counted from the ONE moment
# the passphrase was typed, so a phone used every single day was signed
# out a month later anyway — which is how the beta tester met the
# "Who's this?" screen again and again. Re-minting makes the life a
# sliding one: an active device is never more than a day from a fresh
# thirty, and a device nobody opens still goes cold on time.
#
# A day, rather than every request, because each renewal is a Set-Cookie
# header on a response that did not need one. Same shape as
# touch_household_active's fifteen-minute throttle, for the same reason.
COOKIE_RENEW_AFTER = 24 * 60 * 60

# "This phone is Vineeth's" — the half of the pick that outlives the
# session (Loop Board "This phone remembers who's using it, even after
# signing in again", 2026-10-04).
#
# Separate cookie, separate life. The session cookie is the household's
# sign-in and must end when somebody signs out; which adult holds the
# phone is a fact about the PHONE, and signing out and back in does not
# make it a different phone. So /login re-applies this one (see
# `device_member_for_login`) and /logout deliberately leaves it alone.
#
# Signed with the same secret as the session, for the reason the whole of
# this file exists: /login turns what this cookie says into a SIGNED
# claim the rest of the app then trusts, and promoting an unsigned thing
# the browser sent into a signed thing the server said is the exact shape
# of bug this app keeps getting bitten by. It is not the only thing
# standing between a device and a member it may not have — see
# `device_member_for_login` — but it is the first.
DEVICE_COOKIE = "pomona_device_member"
# Longer than the session on purpose: the point is to outlast it. The same
# 400 days as push.DEVICE_COOKIE_MAX_AGE, which is the other cookie in this
# app that means "this phone" rather than "this sitting" — one number for
# one idea. (Chrome caps a cookie at 400 days anyway, so asking for more
# would buy nothing; what Safari does to it is not something this comment
# is going to claim without measuring.)
DEVICE_COOKIE_MAX_AGE = 400 * 24 * 60 * 60

# The largest integer SQLite can bind — see _decode_session.
_SQLITE_MAX_INT = 2**63 - 1

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
    # Self-hosted fonts: sign-in and the share pages need them with no session.
    "/static/fonts/",
)
_PUBLIC_EXACT = frozenset({
    # Not public in any ordinary sense — it carries its own token and 404s
    # without one (see main.health_report). It is listed here because it is
    # deliberately CROSS-household: there is no session to bind it to, which
    # is exactly why it authenticates itself instead.
    "/api/health-report",
    # The invite link's landing page and the one call it makes. The token
    # is the credential (app/invites.py); it rides in the link's fragment
    # and the POST body, never the path, so nothing logs it.
    "/join",
    "/api/join",
    "/login",
    "/logout",
    # Where a deleted household (or an adult who left one) lands — they are
    # signed out by then. A static page that reads nothing from anyone.
    "/goodbye",
    # The privacy policy, terms and support pages (app/legal.py). Apple
    # opens them from the App Store listing with no account, and a person
    # signing up reads them before they have one. Static words, nothing
    # read from any household.
    "/privacy",
    "/terms",
    "/support",
    "/static/legal.css",
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
    # Python parses any size of integer; SQLite binds only 64-bit ones, and
    # handing it a bigger number raises OverflowError from inside the
    # query — a 500 on every route that asks who is acting. A household id
    # outside that range names no household, so the cookie is simply not
    # one; a member id outside it is no pick.
    if not (0 <= household_id <= _SQLITE_MAX_INT):
        return None
    if not (0 <= member_id <= _SQLITE_MAX_INT):
        member_id = 0
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


# ---------- "This phone is mine" (the device token) ----------


# What the device token's signature covers, on top of the two numbers.
#
# Not decoration. A device token is `<household>.<member>.<hmac>` — three
# dot-separated parts — and so is the LEGACY session cookie shape
# `_decode_session` still honours, `<sid>.<issued>.<hmac>`. Same grammar,
# same secret, and the payload HMAC'd was the same string, so one was a
# validly signed instance of the other: measured, `read_session_parts` on
# `device_token(1, 1791186880)` came back as a household-1 session with
# session id "1", refused only because the member id it read as a sign-in
# time has to look recent. Member ids start at 1 and count up, so nothing
# could reach that — but two cookie formats one deletion away from being
# interchangeable is not a thing to leave standing. Tagging the payload
# means a device token is not a session cookie for any value at all.
_DEVICE_SIG_DOMAIN = "dev"


def device_token(household_id: int, member_id: int) -> str:
    """
    Mint the long-lived device cookie's value: `<household>.<member>.<hmac>`.

    No session id and no issued-at: this says nothing about a sitting, only
    which adult of which household this browser belongs to. The household
    is in it because a phone can be signed into more than one — a shared
    tablet, or Emily's phone opening the demo household — and "the adult
    this device is" is only ever an answer about one of them.
    """
    payload = f"{int(household_id)}.{int(member_id)}"
    sig = _device_sig(payload)
    return f"{payload}.{_b64(sig)}"


def _device_sig(payload: str) -> bytes:
    return hmac.new(
        _secret(), f"{_DEVICE_SIG_DOMAIN}.{payload}".encode("utf-8"), hashlib.sha256
    ).digest()


def read_device_token(cookie: str | None) -> tuple[int, int] | None:
    """
    `(household_id, member_id)` from a validly signed device cookie, else
    None. Both are bounded the same way `_decode_session` bounds them: a
    number SQLite cannot bind is not an id, and handing one to a query
    raises OverflowError from inside it.
    """
    if not cookie:
        return None
    parts = cookie.split(".")
    if len(parts) != 3:
        return None
    raw_household, raw_member, sig = parts
    expected = _device_sig(f"{raw_household}.{raw_member}")
    try:
        if not hmac.compare_digest(expected, _unb64(sig)):
            return None
        household_id = int(raw_household)
        member_id = int(raw_member)
    except (ValueError, TypeError):
        return None
    if not (0 < household_id <= _SQLITE_MAX_INT):
        return None
    if not (0 < member_id <= _SQLITE_MAX_INT):
        return None
    return household_id, member_id


def device_member_for_login(
    cookie: str | None, household_id: int
) -> tuple[int | None, bool]:
    """
    `(the adult this device should be signed in as, is the cookie spent)`.

    THREE things have to be true for an adult to come back, and the
    signature is only the first of them. The cookie has to be one this
    server minted; it has to name the household the passphrase just
    opened; and that member has to still be an adult of it. A device
    cannot carry a member of a household it is not signing into, because
    the household it names is compared against the one the CREDENTIAL
    established — never the other way round, and never taken from the
    cookie.

    The third check is what answers "if the remembered member was removed
    from the household, ask again". `tools.current_member()` would already
    read a removed member as nobody, so the pick could not stick either
    way; checking here means the question is asked at the one moment
    somebody is already looking at a screen, rather than a stale id being
    quietly re-minted into every session from now on.

    SPENT is the second value, and it is deliberately narrower than "not
    an answer". It means the cookie names THIS household and somebody who
    has left it — an answer that can never come back, so the caller clears
    it. A cookie naming ANOTHER household is not spent: it is somebody
    else's answer, still true for them, and a shared tablet whose other
    household never had to pick (one adult, so never asked) would lose its
    memory for nothing if signing in here threw it away.
    """
    read = read_device_token(cookie)
    if read is None or read[0] != int(household_id):
        return None, False
    member_id = read[1]
    # `households`, not `tools`: this is the auth path asking about an
    # explicit household id, with nothing bound yet (/login is a public
    # path), which is the same question household_exists already answers
    # from there.
    if not households.adult_exists(household_id, member_id):
        return None, True
    return member_id, False


def set_device_cookie(response, request, household_id: int, member_id: int) -> None:
    """Remember this device's adult on the response. One writer, so every
    door that pins a device (the pick, an invite link, finishing setup)
    writes the same cookie with the same flags."""
    response.set_cookie(
        DEVICE_COOKIE,
        device_token(household_id, member_id),
        max_age=DEVICE_COOKIE_MAX_AGE,
        httponly=True,
        samesite="lax",
        secure=request_is_https(request),
        path="/",
    )


def clear_device_cookie(response) -> None:
    """Forget this device's adult. Only where the answer has stopped being
    true at all — an adult who removed themselves, a household that is
    gone, a device whose cookie names neither. NOT on sign-out: signing
    out and back in is the same phone, and remembering that is the point."""
    response.delete_cookie(DEVICE_COOKIE, path="/")


def request_is_https(request) -> bool:
    """
    Is the browser on https? Railway terminates TLS at its proxy, so the
    app itself sees http and X-Forwarded-Proto is what says otherwise.

    Here rather than in main.py because every cookie this app sets needs
    the answer and two readings of one header is one too many.
    """
    forwarded = request.headers.get("x-forwarded-proto", "")
    if forwarded:
        return forwarded.split(",")[0].strip() == "https"
    return request.url.scheme == "https"


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
        response = await _call_as_household(session[1], call_next, request, session[3])
        _renew_if_due(request, response, session)
        return response

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


def _sets_session_cookie(response) -> bool:
    """Does this response already say something about the session cookie?

    Load-bearing, not tidiness: /api/whoami/pick's whole job is to put a
    new member into this cookie, and a renewal built from the cookie the
    REQUEST carried would overwrite that with the pick thrown away. Same
    for /login, the invite link, and the routes that sign somebody out.
    Whoever wrote the cookie on this response meant it; the renewal is
    only ever for the responses nobody else is touching.
    """
    name = f"{COOKIE_NAME}="
    return any(h.startswith(name) for h in response.headers.getlist("set-cookie"))


def _renew_if_due(request, response, session) -> None:
    """
    Sliding expiry: once a day, re-mint the session with today's date on
    it, so a phone in daily use is never signed out.

    Everything else about the cookie is carried over — the session id (the
    chat history is keyed on it) and the member (re-minting must never be
    a way to forget who this is). Only the sign-in time moves, which is
    the one thing being extended.

    Never raises. A cookie is bookkeeping for a request that has already
    been answered, and this runs on every authenticated request; a browser
    that keeps the cookie it has is signed in exactly as long as it was
    before, which is the behaviour this whole app had until now.
    """
    try:
        _, household_id, issued, member = session
        if time.time() - issued < COOKIE_RENEW_AFTER:
            return
        if _sets_session_cookie(response):
            return
        response.set_cookie(
            COOKIE_NAME,
            issue_session(household_id, member, session_id=session[0]),
            max_age=COOKIE_MAX_AGE,
            httponly=True,
            samesite="lax",
            secure=request_is_https(request),
            path="/",
        )
    except Exception:
        logger.exception("Renewing the session cookie failed")


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
