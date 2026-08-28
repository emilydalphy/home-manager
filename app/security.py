"""
Access control for the deployed app.

Why this exists: V1 was designed as a single-household app on localhost,
where "no auth" was a reasonable simplification. Once it's hosted on a
public URL that stops being true — every /api route, including the ones
that write and the ones that spend money calling Claude, is reachable by
anyone who has (or guesses) the hostname.

This is deliberately NOT the multi-tenant auth system described in the
README's "Path to a sellable product". It's a single shared password in
front of the whole app, which is the right shape for a household tool with
one household in it, and it can be deleted wholesale when real accounts
arrive. What it buys is that the app stops being world-readable today.

Two env vars:

  HOME_MANAGER_PASSWORD  the shared password. If unset, the app still runs
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

from starlette.responses import JSONResponse, RedirectResponse

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


def issue_session() -> str:
    """
    Mint a signed cookie value: <session-id>.<issued-at>.<hmac>.

    The session id is what /api/chat keys its conversation history on, so it
    has to be unguessable and server-generated — the bug this replaces was
    taking that id from the request body, where anyone could send the
    literal string "default" and land in the household's history.
    """
    sid = secrets.token_urlsafe(18)
    issued = str(int(time.time()))
    payload = f"{sid}.{issued}"
    sig = hmac.new(_secret(), payload.encode("utf-8"), hashlib.sha256).digest()
    return f"{payload}.{_b64(sig)}"


def read_session(cookie: str | None) -> str | None:
    """Return the session id if the cookie is validly signed and unexpired, else None."""
    if not cookie:
        return None
    parts = cookie.split(".")
    if len(parts) != 3:
        return None
    sid, issued, sig = parts
    payload = f"{sid}.{issued}"
    expected = hmac.new(_secret(), payload.encode("utf-8"), hashlib.sha256).digest()
    try:
        if not hmac.compare_digest(expected, _unb64(sig)):
            return None
        if time.time() - int(issued) > COOKIE_MAX_AGE:
            return None
    except (ValueError, TypeError):
        return None
    return sid


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
    Gate every non-public route behind the shared password.

    An unauthenticated request gets a redirect if it's a browser asking for
    a page, and a plain 401 JSON body if it's a fetch() — so the app's own
    API calls fail readably instead of receiving a login page where they
    expected data.
    """
    path = request.url.path

    if is_public_path(path):
        return await call_next(request)

    # No password configured: local development. Serve localhost, refuse
    # everything else rather than falling open on a real deployment.
    if not _password():
        if _is_local(request.client.host if request.client else None):
            return await call_next(request)
        logger.error(
            "Refusing a remote request because HOME_MANAGER_PASSWORD is not set. "
            "Set it in the hosting platform's environment variables."
        )
        return JSONResponse(
            {"detail": "This app isn't configured for public access yet."},
            status_code=503,
        )

    if read_session(request.cookies.get(COOKIE_NAME)):
        return await call_next(request)

    wants_html = "text/html" in request.headers.get("accept", "")
    if wants_html and request.method == "GET":
        return RedirectResponse(url=f"/login?next={_safe_next(path)}", status_code=303)
    return JSONResponse({"detail": "Please sign in again."}, status_code=401)


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
