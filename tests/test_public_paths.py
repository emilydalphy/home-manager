"""
A handful of addresses work without signing in. Exactly which ones is a
question about *files*, and it was being answered as a question about
*text*.

"Does this address start with /static/icons/?" is true of
"/static/icons/../shell.js", which is not an icon. The app served its own
front-end source to anyone who asked that way, while refusing the same
file under its real name. Nothing private leaked -- there is no household
data under /static/ -- but the same function also decides which requests
get an error recorded against a household, so one imprecise answer was
quietly deciding two different things.

These tests drive the real ASGI application, not just the helper. An
ordinary HTTP client rewrites ".." out of a URL before it is ever sent,
so a test written with one would pass against the bug -- it never asks
the question. The requests below hand the server the literal path.
"""
import asyncio

import pytest

from app import security
from app.main import app


def _request(path: str) -> tuple[int, int]:
    """Send one request with the path exactly as written. Returns (status, body length)."""
    seen: dict = {"body": b""}

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        if message["type"] == "http.response.start":
            seen["status"] = message["status"]
        elif message["type"] == "http.response.body":
            seen["body"] += message.get("body", b"")

    scope = {
        "type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1",
        "method": "GET", "path": path, "raw_path": path.encode(),
        "root_path": "", "query_string": b"",
        "headers": [(b"host", b"example.test")],
        # A remote client: localhost is trusted by design elsewhere.
        "client": ("203.0.113.9", 1234),
        "server": ("example.test", 443), "scheme": "https",
    }
    asyncio.run(app(scope, receive, send))
    return seen["status"], len(seen["body"])


@pytest.mark.parametrize("path", [
    "/static/icons/../shell.js",
    "/static/icons/../../static/shell.js",
    "/static/./icons/../shell.js",
    # A literal "#" or "?" INSIDE the path. uvicorn decodes these out of
    # %23 and %3F, and Starlette's request.url.path truncates at them --
    # so the gate used to be shown "/static/icons/" while the router went
    # on to serve the real file. Judging scope["path"] is what closes it.
    "/static/icons/#/../../shell.js",
    "/static/icons/?/../../shell.js",
    "/static/theme.css#/../shell.js",
])
def test_a_dressed_up_address_does_not_open_a_private_file(path):
    """
    The bug. Every one of these names /static/shell.js, which requires a
    session -- and every one of them starts with a public prefix as plain
    text.
    """
    status, length = _request(path)
    assert status == 401, (
        f"{path} answered {status} ({length} bytes) without a session; it "
        f"resolves to a file that needs one"
    )


def test_the_file_it_resolves_to_is_genuinely_private():
    """
    Guards the test above from passing for the wrong reason. If shell.js
    became public, the assertions above would still hold while testing
    nothing at all.
    """
    assert _request("/static/shell.js")[0] == 401


def test_the_genuinely_public_files_still_work():
    """
    The other half. Over-tightening this would lock the sign-in page out
    of its own stylesheet, which is a worse failure than the bug -- nobody
    could sign in at all.
    """
    status, length = _request("/static/theme.css")
    assert status == 200 and length > 0
    assert _request("/login")[0] == 200


@pytest.mark.parametrize("path,expected", [
    ("/static/theme.css", True),
    ("/static/icons/icon-192.png", True),
    ("/login", True),
    ("/healthz", True),
    ("/share/", True),          # trailing slash must survive normalising
    ("/share/abc123", True),
    ("/static/shell.js", False),
    ("/static/icons/../shell.js", False),
    # An odd spelling of a genuinely public file stays public: the exact
    # set is matched on the resolved address too, not just the prefixes.
    ("/static/junk/../theme.css", True),
    ("/static/./theme.css", True),
    # No percent-encoded case here on purpose. The server decodes the path
    # before this function ever sees it, so "%2e%2e" arrives as ".." and is
    # covered by the row above. Asserting it here would test an input this
    # function cannot receive -- and inviting it to decode a second time is
    # its own bug, since a filename may legitimately contain a "%2e".
    # Checked against a live uvicorn rather than assumed: before the fix
    # both spellings served 192,778 bytes of shell.js unauthenticated;
    # after it, both are refused.
    ("/week", False),
    ("/api/chat", False),
])
def test_is_public_path_judges_the_resolved_address(path, expected):
    """
    Stated directly against the helper as well, because this function has
    a second caller: it decides whether an error gets recorded against a
    household. On a public path there is no household to attribute one to.
    """
    assert security.is_public_path(path) is expected


def test_normalising_did_not_swallow_the_share_prefix():
    """
    normpath drops a trailing slash, which would have turned "/share/"
    into "/share" -- no longer matching its own prefix, and the two public
    share flows would have started demanding a passphrase from people who
    do not have one.
    """
    for path in ("/share/", "/member-share/", "/api/share/", "/api/member-share/"):
        assert security.is_public_path(path), f"{path} stopped being public"
