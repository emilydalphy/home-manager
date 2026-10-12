"""
A spent invite link has a way to the sign-in screen (defect hunt, 2026-10-11).

/join that can't use its link says "This link has run out." On a signed-out
phone that was the whole page — in the iPhone app (no address bar, no back
gesture) a dead end until the app was force-quit. Now a "Back to the start" link to /login
sits under the message, and steps aside for "Open Pomona" when the browser
turns out to be signed in already. The same link sits under "That didn't
go through." (a 500, a 429, a lost connection), whose only button was Try
again. Same words as overnight/goodbye-has-a-way-back adds, and not "sign
in": the page is never a sign-in error (test_household_invite).
"""
from __future__ import annotations

import re

from fastapi.testclient import TestClient

from app.main import app


def _page() -> str:
    res = TestClient(app).get("/join", headers={"accept": "text/html"}, follow_redirects=False)
    assert res.status_code == 200
    return res.text


def _gone_block(html: str) -> str:
    start = html.index('id="jn-gone"')
    return html[start:html.index("</div>", start)]


def test_the_run_out_message_links_to_sign_in():
    gone = _gone_block(_page())
    link = re.search(r'<a class="jn-signin" id="jn-signin" href="([^"]+)"( hidden)?>([^<]+)</a>', gone)
    assert link, "a run-out invite needs a way to the sign-in screen"
    assert link.group(1) == "/login"
    # Shown by default: a signed-out (or offline) phone never gets the
    # whoami answer that would swap it for "Open Pomona".
    assert link.group(2) is None


def test_a_signed_in_browser_sees_open_pomona_instead():
    html = _page()
    script = html[html.index("async function runOut"):html.index("async function join")]
    ok = script[script.index("if (await signedIn())"):]
    assert "'jn-open').hidden = false" in ok
    assert "'jn-signin').hidden = true" in ok


def test_the_link_is_tall_enough_to_tap_and_can_hide():
    html = _page()
    rule = re.search(r"\.jn-signin\s*\{([^}]*)\}", html)
    assert rule and "min-height: 44px" in rule.group(1)
    # display:inline-flex would beat the [hidden] attribute without this.
    assert re.search(r"\.jn-signin\[hidden\]\s*\{\s*display:\s*none", html)


def test_sign_in_opens_for_a_signed_out_device():
    res = TestClient(app).get("/login", headers={"accept": "text/html"}, follow_redirects=False)
    assert res.status_code == 200


def test_the_error_state_has_the_same_way_back():
    html = _page()
    start = html.index('id="jn-trouble"')
    trouble = html[start:html.index("</div>", start)]
    link = re.search(r'<a class="jn-signin" id="jn-trouble-signin" href="([^"]+)"( hidden)?>([^<]+)</a>', trouble)
    assert link, "a 500 / 429 / lost connection needs a way out besides Try again"
    assert link.group(1) == "/login" and link.group(2) is None
    assert link.group(3) == "Back to the start"
    # Hidden only once whoami says this browser is signed in.
    script = html[html.index("async function trouble"):html.index("async function runOut")]
    assert "if (await signedIn()) document.getElementById('jn-trouble-signin').hidden = true" in script
