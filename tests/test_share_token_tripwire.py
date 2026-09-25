"""
A live share link must never reach error_events, whatever route it came from.

WHAT IS ACTUALLY PROTECTING IT — two things were believed to be, and only
one is. Measured 2026-09-24 by the review of overnight/client-error-network-
reason, and re-measured here:

  * `_REQUEST_SHAPE_RE` does NOT protect it. A 22-character
    secrets.token_urlsafe(16) satisfies `^[A-Za-z][A-Za-z0-9_-]{0,29}$`
    whenever it starts with a letter — about 46% of tokens — and a one-word
    member name always does. Both sail through the shape check untouched.
  * `_redact_share_token` DOES, through `_SHARE_PATH_RE` and
    `_MEMBER_PATH_RE` — i.e. a list of prefixes somebody has to keep
    current, with nothing telling them when a new route needs adding.

So the guard is a maintained list. This file is the thing that tells them.
It is the tripwire half of the card's two options, taken rather than the
other one (inverting the reduction so a segment is `{}` unless it is a known
route word) because inverting changes what is stored for EVERY route and
would lose diagnostic detail to fix a problem that is about the next route
rather than a current leak.

WHY IT READS THE APP'S OWN ROUTE TABLE and not the source of app/main.py:
the 2026-09-24 leftover-chain sweep was defeated by a pure REFORMAT, and its
entry is emphatic that a sweep which can only see one spelling is worth
little. `app.routes` is what FastAPI will actually serve, so a route
registered any way at all is in it.

MEASURED TODAY: seven route entries carry such a parameter, across two
prefix families, and all seven are covered. This file is not reporting a
leak; it exists so the eighth cannot arrive quietly.
"""
from __future__ import annotations

import re

import pytest

from app.main import app, _redact_share_token


# A path parameter whose NAME says it carries a secret or a person. Names,
# not values: a value cannot be inspected at import time, and the whole
# point is to catch a route before anybody has sent one.
SENSITIVE_PARAM_WORDS = ("token", "share", "name", "secret", "key", "passphrase")

# A real one. secrets.token_urlsafe(16) is 22 characters of [A-Za-z0-9_-];
# this one starts with a letter, which is the ~46% of tokens the shape check
# waves through, so a probe that started with a digit would pass for the
# wrong reason.
A_REAL_LOOKING_TOKEN = "kJ3lmQ8xZabcdefghijkl"


def _routes_with_a_sensitive_parameter() -> list[tuple[str, list[str]]]:
    found = []
    for route in app.routes:
        path = getattr(route, "path", "")
        if "{" not in path:
            continue
        params = re.findall(r"\{([^}:]+)", path)
        sensitive = [p for p in params if any(w in p.lower() for w in SENSITIVE_PARAM_WORDS)]
        if sensitive:
            found.append((path, params))
    return sorted(found)


def _probe(path: str, params: list[str]) -> str:
    """The path as the browser would send it, with a real-looking token in."""
    out = path
    for p in params:
        value = (
            A_REAL_LOOKING_TOKEN
            if any(w in p.lower() for w in SENSITIVE_PARAM_WORDS)
            else "1"
        )
        out = re.sub(r"\{" + re.escape(p) + r"[^}]*\}", value, out)
    return out


# --------------------------------------------------------------- the tripwire


def test_every_route_carrying_a_token_or_a_name_is_redacted():
    """
    THE POINT OF THE FILE. Green today — all seven are covered — and red the
    day somebody adds a token-bearing route without teaching the redaction
    about it, which is the failure this card is about.

    The message names the route and the prefix lists, because whoever trips
    this will be somebody who has just added a route and has no idea a
    redaction list exists.
    """
    leaks = []
    for path, params in _routes_with_a_sensitive_parameter():
        probe = _probe(path, params)
        if _redact_share_token(probe) == probe:
            leaks.append(f"{path}  (a real value would be stored verbatim: {probe})")
    assert not leaks, (
        "These routes carry a token or a person's name in the path, and "
        "_redact_share_token leaves them untouched — so a live credential "
        "would be written into error_events, which is printed into an "
        "agent's context every morning. Teach _SHARE_PATH_RE or "
        "_MEMBER_PATH_RE about them (app/main.py):\n  " + "\n  ".join(leaks)
    )


def test_the_sweep_is_actually_finding_routes():
    """
    THE GUARD ON THE GUARD, and the reason it is here: a sweep that quietly
    stops matching passes for ever and says nothing. This repo has recorded
    that exact failure twice — a household-filter sweep defeated by a
    reformat, and an assertion that compared a function's output to itself.

    Seven route entries carry such a parameter today, across /share,
    /member-share, /api/share, /api/member-share and /api/members/{name}. If
    that number drops, either routes were removed or the sweep went blind,
    and the second is the one nobody would notice.
    """
    found = _routes_with_a_sensitive_parameter()
    paths = [p for p, _ in found]
    assert len(found) >= 7, f"the sweep found only {len(found)}: {paths}"
    for expected in ("/share/{token}", "/api/members/{name}/share-link"):
        assert expected in paths, f"{expected} vanished from the sweep — is it still a route?"


def test_the_shape_check_is_not_what_protects_them():
    """
    The claim the card corrects, pinned so it cannot be believed again. A
    comment in app/main.py used to say any segment that is not a plain route
    word became `{}` before it was stored; it does not, and that is why the
    redaction is load-bearing rather than belt-and-braces.

    Note the level: _REQUEST_SHAPE_RE is a WHOLE-PATH check (it is anchored
    and eats the leading slash), not a per-segment one, so the claim has to
    be made about the paths _safe_client_where really hands it. A 22-char
    secrets.token_urlsafe(16) beginning with a letter, and a one-word member
    name, are both plain route words to it.
    """
    from app.main import _REQUEST_SHAPE_RE

    for path in (f"/share/{A_REAL_LOOKING_TOKEN}", "/api/members/Sophia/share-link"):
        assert _REQUEST_SHAPE_RE.match(path), (
            f"{path!r} passes the shape check untouched — that is the point"
        )


@pytest.mark.parametrize(
    "path, expected",
    [
        ("/share/" + A_REAL_LOOKING_TOKEN, "/share/<token>"),
        ("/api/share/" + A_REAL_LOOKING_TOKEN, "/api/share/<token>"),
        ("/member-share/" + A_REAL_LOOKING_TOKEN, "/member-share/<token>"),
        ("/api/member-share/" + A_REAL_LOOKING_TOKEN + "/note", "/api/member-share/<token>/note"),
        ("/api/members/Sophia/share-link", "/api/members/<name>/share-link"),
    ],
)
def test_the_two_live_families_really_are_redacted(path, expected):
    """
    The card asks explicitly to KEEP the parametrized cases that showed
    redaction was doing the work. These are those, restated here so the
    tripwire file is self-contained — they go red if either prefix list is
    removed, which is what proves the sweep above is guarding something real
    rather than an empty set.
    """
    assert _redact_share_token(path) == expected


def test_a_route_the_list_has_not_been_taught_is_caught():
    """
    THE TRIPWIRE PROVED TO BITE, without waiting for somebody to add a route.

    A sweep is only worth what it catches, and the honest way to show that
    is to hand it a route the redaction genuinely does not know. This is the
    shape of the eighth route — and the assertion is that the redaction
    leaves it alone, i.e. that the check above would have failed.
    """
    an_untaught_route = "/api/invites/" + A_REAL_LOOKING_TOKEN + "/accept"
    assert _redact_share_token(an_untaught_route) == an_untaught_route, (
        "if this now redacts, the prefix lists have grown and this test "
        "should be given a route they still do not cover"
    )
    # ...and the sweep's own predicate would flag it.
    probe = _probe("/api/invites/{token}/accept", ["token"])
    assert _redact_share_token(probe) == probe
