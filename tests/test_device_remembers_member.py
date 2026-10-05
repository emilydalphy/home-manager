"""
This phone remembers who's using it, even after signing in again (Loop
Board, 2026-10-04 — Gowthami's household: "each time we had to reclick who
it was that was using it").

The pick lived in one place and that place was the session: every /login
minted a fresh cookie with nobody in it, and the cookie expired thirty days
after the passphrase was typed however often the phone was opened. So the
answer to "Who's this?" was good until the next sign-out, and no longer.

There are two things now, with two different lives (app/security.py):

  hm_session            the household's sign-in. Ends on sign-out, and
                        renews itself while the app is being used
                        (`_renew_if_due`) so a daily phone never times out.
  pomona_device_member  which adult this BROWSER is. Signed with the same
                        secret, re-applied by /login when the passphrase
                        opens the household it names, and deliberately NOT
                        cleared by signing out — that is the same phone.

Plus a localStorage copy in static/shell.js, for the one case a signed
cookie cannot cover: with SESSION_SECRET unset, a restart invalidates every
signature this server ever made.

The isolation bar is tests/test_multi_household.py's and
tests/test_adult_login.py's: a device can never carry a member of a
household it is not signed into, and nothing the browser sends is believed
about who an adult is — only about which of this household's adults to ask
the server to confirm.

Against main (this file over main's app/ and static/): 31 failed, 6 passed
— and that number is worth much less than it looks, so it is decomposed.
NINETEEN die on a name main has not got (`security.DEVICE_COOKIE`,
`read_device_token`, `device_token`, `device_member_for_login`), which is
the only kind of red a test of a new symbol can have. SEVEN are source
markers (five in shell.js, one on `main._is_https`, one on the block the
localStorage sweep reads). FIVE reach a real behaviour: the two headline
sign-out-and-in tests (`body["member"]["name"]` over a None member — the
reported bug itself), the renewal's own assertion, the renewal's household,
and `picked_on_this_device` being absent. The six that PASS on main each
say so in their own docstring.

So the evidence is the mutations. Every one below was applied, run over
this file + tests/test_adult_login.py + tests/test_api_js.py, and reverted.

  the HMAC comparison skipped                              3 red
  the household comparison dropped                         2
  `adult_exists` bypassed                                  4
  `_DEVICE_SIG_DOMAIN` removed (device token == session)    1
  `_sets_session_cookie` dropped (renewal eats the pick)    1
  the id bounds relaxed to `0 <=`                           1
  /logout clears the device cookie                         7
  the device token minted from `issue_session`             11
  `COOKIE_RENEW_AFTER` dropped (renew every request)        2
  the thirty-day test dropped from `_decode_session`        1
  the renewal's try/except dropped                          1
  the renewal re-minted without `session_id=`               1
  the renewal re-minted with `member or 1`                  1
  the renewal re-minted into household 1                    1
  /login falls back to the household's first adult         17
  `read_device_token` answers (1, 1) for no cookie          1
  the localStorage key loses the household                  1
  the shell mirrors `member` not `picked_on_this_device`    1
  the shell trusts localStorage without checking it         1
  either localStorage try/catch dropped                     1
  the shell's write on a pick dropped                       1
  the shell's forget-on-404 dropped                         1

TWO mutations measured ZERO and are recorded rather than quietly re-run:
pinning `saved_ids[0]` in the onboarding device write, and dropping that
route's `current_member() is None` guard. Both are belt and braces behind
two independent facts — see
test_a_second_pass_through_setup_never_takes_the_device_over.
"""
from __future__ import annotations

import hashlib
import hmac
import time
from pathlib import Path

import pytest

from app import households, main, security, tools


REPO = Path(__file__).resolve().parent.parent
SHELL_JS = (REPO / "static" / "shell.js").read_text(encoding="utf-8")

BETA_PASSPHRASE = "beta-tester-passphrase"

# What http.cookiejar stores TestClient's dotless host as. See _backdate.
_JAR_DOMAIN = "testserver.local"


def _sign_in(client, password="test-password"):
    res = client.post("/login", data={"password": password, "next": "/"}, follow_redirects=False)
    assert res.status_code == 303
    return res


def _adult(name: str, household: int = 1) -> int:
    with tools.use_household(household):
        member_id = tools.add_member(name)["member_id"]
        # Onboarding writes "Adult" capitalised; the app folds case, so the
        # test does the same thing on purpose.
        tools.set_member_age_group(name, "Adult")
    return member_id


def _child(name: str, household: int = 1) -> int:
    with tools.use_household(household):
        member_id = tools.add_member(name)["member_id"]
        tools.set_member_age_group(name, "child")
    return member_id


@pytest.fixture
def two_adults():
    return {"Emily": _adult("Emily"), "Vineeth": _adult("Vineeth")}


def _sign_out(client):
    """Sign out the way the app does — the device cookie has to live through it."""
    res = client.get("/logout", follow_redirects=False)
    assert res.status_code == 303
    return res


# ---------------------------------------------------------------------------
# The reported bug
# ---------------------------------------------------------------------------


def test_signing_out_and_in_again_keeps_the_person(client, two_adults):
    """
    CATCH — the tester's whole report. Pick once, sign out, sign in, and
    the question is not asked again.
    """
    _sign_in(client)
    client.post("/api/whoami/pick", json={"member_id": two_adults["Vineeth"]})
    assert client.get("/api/whoami").json()["member"]["name"] == "Vineeth"

    _sign_out(client)
    _sign_in(client)

    body = client.get("/api/whoami").json()
    assert body["member"]["name"] == "Vineeth"
    assert body["needs_pick"] is False


def test_the_pick_rides_in_its_own_cookie_not_only_the_session(client, two_adults):
    """CATCH — the mechanism: a second cookie, signed, outliving the session."""
    _sign_in(client)
    res = client.post("/api/whoami/pick", json={"member_id": two_adults["Emily"]})
    assert res.status_code == 200
    device = client.cookies[security.DEVICE_COOKIE]
    assert security.read_device_token(device) == (1, two_adults["Emily"])

    _sign_out(client)
    # The session is gone and this one is not. That asymmetry IS the fix.
    assert security.COOKIE_NAME not in client.cookies
    assert client.cookies[security.DEVICE_COOKIE] == device


def test_logging_out_does_not_forget_whose_phone_it_is(client, two_adults):
    """
    GUARD. Signing out ends the sitting, not the ownership of the
    handset. Pinned by the mutation that clears the device cookie in
    /logout: 7 red, this among them.

    The first attempt at that mutation went in `_forget_this_phone` and
    reddened NOTHING — that function returns early when the browser has no
    push cookie, which this test's client has not got, so the inserted
    line never ran. Recorded rather than quietly re-run: a mutation that
    misses says the mutation was badly chosen.
    """
    _sign_in(client)
    client.post("/api/whoami/pick", json={"member_id": two_adults["Vineeth"]})
    res = _sign_out(client)
    assert not any(
        h.startswith(f"{security.DEVICE_COOKIE}=") and "Max-Age=0" in h
        for h in res.headers.get_list("set-cookie")
    ), "sign-out must not expire the device cookie"


def test_a_sign_in_on_a_device_that_never_answered_still_asks(client, two_adults):
    """
    GUARD — nothing is remembered that was never said. Pinned by the
    mutation that makes /login fall back to the household's first adult
    when the device says nothing: 17 red, this among them (and five in
    tests/test_adult_login.py, which is the right blast radius for
    inventing a pick).
    """
    _sign_in(client)
    body = client.get("/api/whoami").json()
    assert body["member"] is None
    assert body["needs_pick"] is True
    assert security.DEVICE_COOKIE not in client.cookies


def test_switching_person_moves_the_device_token_too(client, two_adults):
    """
    CATCH — "Not you?" has to change what the phone remembers, or the next
    sign-in would hand it back to whoever had it before.
    """
    _sign_in(client)
    client.post("/api/whoami/pick", json={"member_id": two_adults["Vineeth"]})
    client.post("/api/whoami/pick", json={"member_id": two_adults["Emily"]})
    assert security.read_device_token(client.cookies[security.DEVICE_COOKIE]) == (
        1,
        two_adults["Emily"],
    )

    _sign_out(client)
    _sign_in(client)
    assert client.get("/api/whoami").json()["member"]["name"] == "Emily"


# ---------------------------------------------------------------------------
# Household isolation — the bar this must not lower
# ---------------------------------------------------------------------------


def test_a_device_token_from_another_household_is_ignored(client, two_adults):
    """
    CATCH on the security property. The cookie names household 1 and
    Emily; the passphrase opens the beta household. The session that comes
    out has nobody in it — never Emily, and never one of the beta
    household's own adults either.
    """
    beta = households.create_household("The Beta Testers", BETA_PASSPHRASE)
    _adult("Julia", beta)
    _adult("Marco", beta)

    # Refused, and NOT spent: somebody else's answer, still true for them.
    # Both of `device_member_for_login`'s checks would refuse this one on
    # their own — the household comparison first, and `adult_exists` after
    # it — so the TUPLE is asserted rather than only the member: dropping
    # the household comparison turns `(None, False)` into `(None, True)`
    # and throws a shared tablet's other household's memory away.
    # test_the_token_must_name_the_household_the_PASSPHRASE_opened is the
    # one that isolates the comparison itself.
    assert security.device_member_for_login(
        security.device_token(1, two_adults["Emily"]), beta
    ) == (None, False)

    client.cookies.set(security.DEVICE_COOKIE, security.device_token(1, two_adults["Emily"]))
    res = _sign_in(client, BETA_PASSPHRASE)

    body = client.get("/api/whoami").json()
    assert body["household_id"] == beta
    assert body["member"] is None
    assert body["needs_pick"] is True
    assert security.read_session_member(
        next(
            h.split(";")[0].split("=", 1)[1]
            for h in res.headers.get_list("set-cookie")
            if h.startswith(f"{security.COOKIE_NAME}=")
        )
    ) is None


def test_a_device_token_naming_a_foreign_member_of_the_right_household_is_ignored(client, two_adults):
    """
    CATCH. The household matches; the member belongs to somebody else's.
    The server never mints that pair, but `device_token` will sign any two
    ints, so the read side is checked on its own: nobody, not them, and
    not a 500.
    """
    beta = households.create_household("The Beta Testers", BETA_PASSPHRASE)
    julia = _adult("Julia", beta)

    # Nobody, and spent: it names THIS household and somebody who is not
    # an adult of it. Asserted at the seam, because asking the screen
    # alone cannot tell this check from `tools.current_member()`'s, which
    # refuses a foreign member too (measured — bypassing `adult_exists`
    # left the screen's answer unchanged).
    assert security.device_member_for_login(security.device_token(1, julia), 1) == (
        None,
        True,
    )

    client.cookies.set(security.DEVICE_COOKIE, security.device_token(1, julia))
    _sign_in(client)

    body = client.get("/api/whoami").json()
    assert body["household_id"] == 1
    assert body["member"] is None
    assert body["needs_pick"] is True


def test_the_token_must_name_the_household_the_PASSPHRASE_opened(client, two_adults):
    """
    CATCH on the security property, and the one test that pins the
    household field itself.

    The token says household 1; the member it names is an adult of the
    BETA household, so `adult_exists` — the other half of the check —
    would be happy. Only comparing the token's household against the one
    the credential just established refuses it. A shared tablet with a
    stale cookie on it must not be able to pin an adult of the household
    somebody else is signing into.

    `device_token` will sign any two ints, the same way `issue_session`
    will; the server never mints this pair, which is why the read side is
    checked on its own.
    """
    beta = households.create_household("The Beta Testers", BETA_PASSPHRASE)
    julia = _adult("Julia", beta)
    _adult("Marco", beta)

    # `(member, spent)`. Not spent: a cookie naming ANOTHER household is
    # somebody else's answer, still true for them, so signing in here
    # refuses it without throwing it away.
    assert security.device_member_for_login(
        security.device_token(1, julia), beta
    ) == (None, False)

    client.cookies.set(security.DEVICE_COOKIE, security.device_token(1, julia))
    _sign_in(client, BETA_PASSPHRASE)
    body = client.get("/api/whoami").json()
    assert body["household_id"] == beta
    assert body["member"] is None, "a household-1 cookie may not pick a beta household adult"
    assert body["needs_pick"] is True


def test_a_device_token_is_not_a_session_cookie_for_any_value(client, two_adults):
    """
    CATCH on a class rather than a case. `<household>.<member>.<hmac>` is
    the same three-part grammar as the LEGACY session cookie
    `<sid>.<issued>.<hmac>` that `_decode_session` still honours, signed
    with the same secret over the same payload string — so one WAS a
    validly signed instance of the other. Measured before the fix:
    `read_session_parts(device_token(1, <a recent unix time>))` came back
    as a household-1 session with session id "1".

    Unreachable with real member ids (they start at 1 and count up), and
    not a thing to leave one deletion away. The signature is
    domain-separated now (_DEVICE_SIG_DOMAIN).
    """
    looks_like_a_timestamp = int(time.time()) - 60
    token = security.device_token(1, looks_like_a_timestamp)
    assert security.read_device_token(token) == (1, looks_like_a_timestamp)
    assert security.read_session_parts(token) is None
    assert security.read_session_member(token) is None

    # ...and a session cookie is not a device token, in either shape.
    assert security.read_device_token(security.issue_session(1, two_adults["Emily"])) is None
    sid, issued = "abc", str(int(time.time()))
    legacy_payload = f"{sid}.{issued}"
    legacy_sig = security._b64(
        hmac.new(security._secret(), legacy_payload.encode(), hashlib.sha256).digest()
    )
    assert security.read_session_parts(f"{legacy_payload}.{legacy_sig}") is not None, (
        "premise: the legacy three-part session shape is still honoured"
    )
    assert security.read_device_token(f"{legacy_payload}.{legacy_sig}") is None


def test_an_edited_device_token_is_a_signature_failure_not_a_person(client, two_adults):
    """
    CATCH. Swapping the member id inside the cookie and keeping the
    signature makes it not a cookie at all — the same rule the session
    cookie follows (test_adult_login's forged-member test).
    """
    token = security.device_token(1, two_adults["Vineeth"])
    household, member, sig = token.split(".")
    forged = f"{household}.{two_adults['Emily']}.{sig}"
    assert security.read_device_token(forged) is None

    client.cookies.set(security.DEVICE_COOKIE, forged)
    _sign_in(client)
    body = client.get("/api/whoami").json()
    assert body["member"] is None
    assert body["needs_pick"] is True


def test_a_device_token_signed_with_another_secret_is_refused(client, two_adults):
    """CATCH — the HMAC is the HMAC, not a checksum anybody can compute."""
    payload = f"1.{two_adults['Vineeth']}"
    sig = hmac.new(b"not-the-session-secret", payload.encode(), hashlib.sha256).digest()
    assert security.read_device_token(f"{payload}.{security._b64(sig)}") is None


def test_a_child_cannot_be_remembered_as_the_devices_adult(client, two_adults):
    """
    CATCH. The pick route already refuses a child; so does the re-apply,
    independently, because a member can be re-marked a child after it was
    written down.

    The re-apply is asserted DIRECTLY, and that is a correction: asking
    only "/api/whoami says nobody" passed with `adult_exists` bypassed
    altogether — `tools.current_member()` reads a child as nobody, so the
    SECOND line of defence was doing the work and this test could not see
    which. Real defence in depth, and not evidence for the claim in the
    sentence above. Pinned now by the mutation that bypasses
    `adult_exists`: 4 red, this among them.
    """
    sam = _child("Sam")
    token = security.device_token(1, sam)
    # The re-apply's own answer: nobody, and the cookie is spent — it names
    # this household and somebody who is not an adult of it.
    assert security.device_member_for_login(token, 1) == (None, True)

    client.cookies.set(security.DEVICE_COOKIE, token)
    _sign_in(client)
    assert client.get("/api/whoami").json()["member"] is None


def test_a_remembered_member_who_was_removed_means_asking_again(client, two_adults):
    """
    CATCH — the card's own criterion. Picked, then removed from the
    household; the next sign-in asks rather than re-minting a name that
    is not there.
    """
    from app.db import get_conn

    _sign_in(client)
    client.post("/api/whoami/pick", json={"member_id": two_adults["Vineeth"]})
    conn = get_conn()
    conn.execute("PRAGMA foreign_keys = OFF")
    conn.execute("DELETE FROM members WHERE id = ?", (two_adults["Vineeth"],))
    conn.commit()
    conn.close()
    _adult("Priya")  # still two adults, so there is still a question to ask

    _sign_out(client)
    res = _sign_in(client)

    body = client.get("/api/whoami").json()
    assert body["member"] is None
    assert body["needs_pick"] is True
    # ...and the cookie that will never answer again is cleared, rather
    # than left to fail this same check for four hundred days.
    assert any(
        h.startswith(f"{security.DEVICE_COOKIE}=") and "Max-Age=0" in h
        for h in res.headers.get_list("set-cookie")
    )


def test_a_remembered_member_marked_a_child_means_asking_again(client, two_adults):
    """
    GUARD — same rule as above, reached the other way. Labelled CATCH in
    the first draft and measured GREEN on main: there is nothing
    remembered there to go stale, so "asks again" is true for the wrong
    reason. Pinned by the mutation that bypasses `adult_exists`: 4 red,
    this among them.
    """
    _sign_in(client)
    client.post("/api/whoami/pick", json={"member_id": two_adults["Vineeth"]})
    _adult("Priya")
    tools.set_member_age_group("Vineeth", "teen")
    assert security.device_member_for_login(
        client.cookies[security.DEVICE_COOKIE], 1
    ) == (None, True), "an adult who stopped being one is not this device's answer"

    _sign_out(client)
    _sign_in(client)
    assert client.get("/api/whoami").json()["member"] is None


def test_an_out_of_range_member_id_in_a_device_token_is_no_pick_not_a_500(client, two_adults):
    """
    CATCH. Python parses 10**30 happily and SQLite cannot bind it; the
    session cookie learned this the hard way (test_adult_login), so the
    device one is bounded before it ever reaches a query.
    """
    assert security.read_device_token(security.device_token(1, 10**30)) is None
    assert security.read_device_token(security.device_token(10**30, 5)) is None

    client.cookies.set(security.DEVICE_COOKIE, security.device_token(1, 10**30))
    res = _sign_in(client)
    assert res.status_code == 303
    assert client.get("/api/whoami").status_code == 200
    assert client.get("/api/whoami").json()["member"] is None


def test_a_zero_or_negative_id_is_not_a_token(client):
    """GUARD — 0 is `issue_session`'s "nobody"; it is not a member here either.
    Pinned by the mutation that relaxes both bounds to `0 <=`: 1 red."""
    assert security.read_device_token(security.device_token(1, 0)) is None
    assert security.read_device_token(security.device_token(0, 1)) is None
    assert security.read_device_token("1.-3." + "x" * 10) is None


def test_junk_in_the_device_cookie_is_not_a_crash(client, two_adults):
    """
    GUARD — anything at all can arrive in a cookie. Pinned twice: the
    mutation that skips the HMAC comparison (3 red, this among them) and
    the one that answers (1, 1) for a cookie that is not there at all
    (1 red, this one).
    """
    for junk in ["", "x", "1.2", "1.2.3.4", "a.b.c", "1.2.!!!", "." * 40]:
        assert security.read_device_token(junk) is None
    client.cookies.set(security.DEVICE_COOKIE, "1.2.3.4")
    assert _sign_in(client).status_code == 303
    assert client.get("/api/whoami").json()["member"] is None


def test_the_device_token_carries_no_session_id(client, two_adults):
    """
    GUARD. It says which adult this browser is and nothing about any
    sitting — the chat history is keyed on the session id, and a
    four-hundred-day cookie is not where that belongs. Pinned by the
    mutation that mints it from `issue_session` instead: 11 red, this
    among them.
    """
    token = security.device_token(1, two_adults["Emily"])
    assert len(token.split(".")) == 3
    assert security.read_session_parts(token) is None


# ---------------------------------------------------------------------------
# The invite link, and the device that set the household up
# ---------------------------------------------------------------------------


def test_an_invite_link_pins_the_invited_adult_on_their_device(client, two_adults):
    """
    CATCH — the card's criterion. The link says who this is once; the
    phone keeps it, so signing out and back in does not ask.
    """
    from app import invites

    with tools.use_household(1):
        token = invites.mint_invite(1, two_adults["Vineeth"], invited_by=two_adults["Emily"])
    res = client.post("/api/join", json={"token": token})
    assert res.status_code == 200
    assert security.read_device_token(client.cookies[security.DEVICE_COOKIE]) == (
        1,
        two_adults["Vineeth"],
    )

    _sign_out(client)
    _sign_in(client)
    assert client.get("/api/whoami").json()["member"]["name"] == "Vineeth"


def test_finishing_setup_pins_the_adult_who_did_it(client):
    """
    CATCH. Onboarding posts its people only at the end, so a household
    with two adults goes from none to both in one request — and the phone
    that typed them in was then asked which of them it was. Pinned both in
    the session it is standing in and on the device for next time.
    """
    _sign_in(client)
    res = client.post(
        "/api/onboarding/household",
        json={
            "members": [
                {"name": "Emily", "age_group": "Adult"},
                {"name": "Vineeth", "age_group": "Adult"},
            ],
            "pets": [],
            "goals": "",
        },
    )
    assert res.status_code == 200
    body = client.get("/api/whoami").json()
    assert body["member"]["name"] == "Emily"
    assert body["needs_pick"] is False

    _sign_out(client)
    _sign_in(client)
    assert client.get("/api/whoami").json()["member"]["name"] == "Emily"


def test_a_second_pass_through_setup_never_takes_the_device_over(client, two_adults):
    """
    GUARD. Vineeth's phone re-runs onboarding (the wizard is reachable by
    URL on a household already set up — tests/test_onboarding_rerun_redirect).
    It must stay Vineeth's.

    NO SINGLE-LINE MUTATION REDDENS THIS, and that is the honest statement
    rather than the one an earlier draft of this docstring made (it claimed
    "pins `saved_ids[0]` instead: 1 red" — measured 0). TWO independent
    facts hold it up, and breaking either leaves the other standing:
    `record_setup_adult` answers once per household, so `setup_adult_id`
    is None on a second pass; and the route's own `current_member() is
    None` guard refuses to overwrite a pick that is already there.
    Measured, each applied and reverted: pinning `saved_ids[0]` in place
    of `setup_adult_id` 0 red (that line never runs on a second pass),
    dropping the `current_member()` guard 0 red, making
    `record_setup_adult` answer on every pass 0 red — and the two
    together 1 red, this test. Same shape as the 2026-09-23 carried-over
    entry's unpinnable pair.
    """
    _sign_in(client)
    client.post("/api/whoami/pick", json={"member_id": two_adults["Vineeth"]})
    client.post(
        "/api/onboarding/household",
        json={"members": [{"name": "Emily", "age_group": "Adult"}], "pets": [], "goals": ""},
    )
    assert client.get("/api/whoami").json()["member"]["name"] == "Vineeth"
    assert security.read_device_token(client.cookies[security.DEVICE_COOKIE]) == (
        1,
        two_adults["Vineeth"],
    )


def test_leaving_the_household_forgets_the_device(client, two_adults):
    """
    CATCH. The one place the answer stops being true rather than merely
    stopping for now: an adult has removed themselves.

    In the beta household, because household 1 is protected from being
    changed this way from inside the app (household_deletion.is_protected).
    """
    beta = households.create_household("The Beta Testers", BETA_PASSPHRASE)
    _adult("Julia", beta)
    marco = _adult("Marco", beta)
    _sign_in(client, BETA_PASSPHRASE)
    client.post("/api/whoami/pick", json={"member_id": marco})
    res = client.post("/api/household/remove-me", json={})
    assert res.status_code == 200, res.text
    assert any(
        h.startswith(f"{security.DEVICE_COOKIE}=") and "Max-Age=0" in h
        for h in res.headers.get_list("set-cookie")
    )


# ---------------------------------------------------------------------------
# Sliding expiry
# ---------------------------------------------------------------------------


def _backdate(client, days: float) -> str:
    """
    Re-sign this client's cookie as if it had been minted `days` ago —
    there is no other way to reach a sliding expiry inside a test.

    Set on the SAME domain the server's own Set-Cookie lands under, so the
    jar entry is REPLACED rather than joined. httpx keys its jar on
    (domain, path, name) and http.cookiejar stores a dotless host as
    "testserver.local" — so a bare `cookies.set` (domain "") leaves a
    second `hm_session` beside the real one, both get sent, and the server
    reads whichever came first. The assertion below is what says so.
    """
    decoded = security._decode_session(client.cookies[security.COOKIE_NAME])
    assert decoded is not None
    sid, household, _issued, member = decoded
    value = security.issue_session(
        household, member, session_id=sid, issued_at=int(time.time() - days * 86400)
    )
    client.cookies.set(security.COOKIE_NAME, value, domain=_JAR_DOMAIN, path="/")
    sessions = [c for c in client.cookies.jar if c.name == security.COOKIE_NAME]
    assert len(sessions) == 1, "one session cookie in the jar, or the server reads the wrong one"
    return value


def _session_cookie_set_by(res) -> str | None:
    for h in res.headers.get_list("set-cookie"):
        if h.startswith(f"{security.COOKIE_NAME}="):
            return h.split(";")[0].split("=", 1)[1]
    return None


def test_a_session_in_daily_use_is_re_minted(client, two_adults):
    """
    CATCH — the other half of the report. The thirty days were counted
    from the one moment the passphrase was typed, so a phone opened every
    day was signed out a month later regardless.

    The session id and the member being CARRIED OVER are asserted here as
    well, and pinned on their own: the mutation that re-mints without
    `session_id=` reddens this one test (1 red), which is the chat history
    of the sitting being thrown away by a renewal.
    """
    _sign_in(client)
    client.post("/api/whoami/pick", json={"member_id": two_adults["Vineeth"]})
    before = _backdate(client, 5)

    res = client.get("/api/whoami")
    assert res.status_code == 200
    minted = _session_cookie_set_by(res)
    assert minted is not None, "an old cookie should come back renewed"
    assert minted != before
    fresh = security._decode_session(minted)
    assert fresh is not None
    # Everything but the date is carried over: same sitting, same person.
    assert fresh[0] == security._decode_session(before)[0]
    assert fresh[1] == 1
    assert fresh[3] == two_adults["Vineeth"]
    assert time.time() - fresh[2] < 60


def test_a_young_session_is_left_alone(client, two_adults):
    """
    GUARD — a Set-Cookie on every response would be noise, and the value
    churning is its own hazard. Pinned by the mutation that drops the
    COOKIE_RENEW_AFTER test: 2 red, this one and
    test_adult_login's out-of-range member id, which re-reads the cookie
    it was handed.
    """
    _sign_in(client)
    res = client.get("/api/whoami")
    assert _session_cookie_set_by(res) is None


def test_a_session_past_its_thirty_days_is_not_renewed_back_to_life(client, two_adults):
    """
    GUARD. Renewal is for a cookie still being honoured; one that has
    expired is not a session to extend — the renewal runs only on the
    branch `_decode_session` has already accepted, so this holds by
    construction rather than by a check of its own. Pinned by the
    mutation that removes the thirty-day test from `_decode_session`
    itself: 1 red, this one.
    """
    _sign_in(client)
    _backdate(client, 31)
    res = client.get("/api/whoami")
    assert res.status_code == 401
    assert _session_cookie_set_by(res) is None


def test_renewal_never_overwrites_a_response_that_set_the_cookie_itself(client, two_adults):
    """
    CATCH on the hazard the renewal creates — and GREEN on main, because
    there is no renewal there to create it. Said that way round rather
    than quoted as a red-against-main, which it is not.
    /api/whoami/pick's whole job is to put a new member in this cookie; a
    renewal built from the cookie the REQUEST carried would put it back
    with the pick thrown away. Pinned by the mutation that drops
    `_sets_session_cookie`: 1 red, this one.
    """
    _sign_in(client)
    _backdate(client, 5)
    res = client.post("/api/whoami/pick", json={"member_id": two_adults["Emily"]})
    assert res.status_code == 200
    minted = _session_cookie_set_by(res)
    assert minted is not None
    assert security.read_session_member(minted) == two_adults["Emily"], (
        "the renewal must not have overwritten the pick"
    )
    assert client.get("/api/whoami").json()["member"]["name"] == "Emily"
    assert len([
        h for h in res.headers.get_list("set-cookie")
        if h.startswith(f"{security.COOKIE_NAME}=")
    ]) == 1, "one answer about the session cookie, not two"


def test_renewal_keeps_the_household_it_was_signed_into(client, two_adults):
    """
    Isolation: a re-mint is not a chance to change households. Red on main
    for a different reason (there is no renewal to read, `assert None ==
    6`), so it is pinned by the mutation that re-mints into household 1:
    1 red, this one.
    """
    beta = households.create_household("The Beta Testers", BETA_PASSPHRASE)
    _adult("Julia", beta)
    _sign_in(client, BETA_PASSPHRASE)
    _backdate(client, 5)
    res = client.get("/api/whoami")
    assert res.json()["household_id"] == beta
    assert security.read_session_household(_session_cookie_set_by(res)) == beta


def test_a_failed_renewal_never_fails_the_request(client, two_adults, monkeypatch):
    """
    GUARD. The response has already been built by the time this runs, and
    a cookie is bookkeeping. Pinned by the mutation that drops the
    try/except: 1 red (a 500 on an ordinary read).
    """
    _sign_in(client)
    _backdate(client, 5)
    monkeypatch.setattr(
        security, "issue_session", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("nope"))
    )
    res = client.get("/api/whoami")
    assert res.status_code == 200
    assert _session_cookie_set_by(res) is None


def test_the_renewal_carries_no_member_when_nobody_is_picked(client, two_adults):
    """
    GUARD — re-minting must not invent a person, any more than it forgets
    one. Pinned by the mutation that re-mints with `member or 1`: 1 red,
    this one.
    """
    _sign_in(client)
    _backdate(client, 5)
    res = client.get("/api/whoami")
    assert security.read_session_member(_session_cookie_set_by(res)) is None


# ---------------------------------------------------------------------------
# One reading of X-Forwarded-Proto
# ---------------------------------------------------------------------------


def test_the_secure_flag_is_decided_in_one_place():
    """
    GUARD. The middleware sets a cookie of its own now, so the answer to
    "is this browser on https" is needed in two files; main._is_https
    delegates rather than keeping a second copy of the header-reading.
    """
    body = main._is_https.__code__.co_names
    assert "request_is_https" in body
    assert "headers" not in body, "main must not read the header a second time"


# ---------------------------------------------------------------------------
# The browser's own copy (static/shell.js)
# ---------------------------------------------------------------------------


def _in(needle: str, where: str) -> None:
    assert needle in SHELL_JS, f"{where}: expected {needle!r} in shell.js"


def test_the_shell_keeps_a_copy_keyed_PER_HOUSEHOLD():
    """
    GUARD on the one thing a shared device could get wrong: answering one
    household's question with another household's adult. Pinned by the
    mutation that drops the household from the key: 1 red (the key test
    below).
    """
    _in("var WHO_DEVICE_PREFIX = 'pomona.deviceMember.h'", "the key")
    _in("WHO_DEVICE_PREFIX + (householdId == null ? 'x' : householdId)", "per household")


def test_the_shells_copy_is_only_ever_a_hint_checked_against_the_real_adults():
    """
    GUARD on the security property, read off the source because the
    screen has no test harness here. localStorage is script-writable, so
    whatever it says is a claim — and all the shell may do with it is ask
    the server about one of the adults the server itself just listed. The
    pick route checks it again regardless (test_adult_login's
    foreign-member test). Pinned by the mutation that drops the
    `adults.some(...)` test and asks about whatever localStorage says:
    1 red, this one.
    """
    _in("var remembered = readDeviceMember(shellWho.household_id);", "read")
    _in(
        "shellWho.adults.some(function (a) { return a.id === remembered; })",
        "checked against the list the server sent",
    )


def test_whoami_says_whether_THIS_DEVICE_is_pinned_not_merely_who_it_resolves_to(client):
    """
    CATCH. A one-adult household resolves to its one adult with nobody
    picked at all (tools.current_member), so `member` alone cannot tell
    "this phone said so" from "there is only one of them" — and the shell
    may only remember the first. Found in a browser: a probe that picked
    through the route rather than through the screen left localStorage
    naming the previous adult, which is the drift this field closes.
    """
    one = _adult("Solo")
    _sign_in(client)
    body = client.get("/api/whoami").json()
    assert body["member"]["name"] == "Solo", "premise: one adult resolves with no pick"
    assert body["needs_pick"] is False
    assert body["picked_on_this_device"] is False

    second = _adult("Partner")
    assert client.get("/api/whoami").json()["picked_on_this_device"] is False
    client.post("/api/whoami/pick", json={"member_id": second})
    after = client.get("/api/whoami").json()
    assert after["member"]["name"] == "Partner"
    assert after["picked_on_this_device"] is True
    # Carried over by an invite and by finishing setup, not only by a tap.
    assert security.read_device_token(client.cookies[security.DEVICE_COOKIE]) == (1, second)
    assert one != second


def test_the_shell_mirrors_the_servers_pin_rather_than_keeping_a_second_record():
    """
    GUARD on the drift above, read off the source. Pinned by the mutation
    that mirrors `member` instead of `picked_on_this_device`: 1 red.
    """
    _in("if (data.picked_on_this_device && shellWho.member) {", "gated on the device's own pin")
    _in("        writeDeviceMember(shellWho.household_id, shellWho.member.id);", "mirror")


def test_the_shell_writes_its_copy_on_every_pick_including_a_switch():
    """GUARD — "Not you?" goes through pickWho, so one write covers both.
    Pinned by the mutation that drops that write: 1 red."""
    _in("writeDeviceMember(shellWho.household_id, shellWho.member && shellWho.member.id);", "write")


def test_the_shell_forgets_a_name_the_server_refuses():
    """GUARD — a 404 means the list it was checked against was stale.
    Pinned by the mutation that drops the forget: 1 red."""
    _in("if (res.status === 404) writeDeviceMember(shellWho.household_id, null);", "forget")


def test_every_localstorage_touch_in_the_who_block_is_wrapped():
    """
    GUARD. Safari in private mode THROWS on localStorage rather than
    returning null, and remembering a name must never stop the app
    opening. Pinned by the mutation that drops either try/catch: 1 red.
    """
    start = SHELL_JS.index("var WHO_DEVICE_PREFIX")
    end = SHELL_JS.index("async function loadWhoami")
    block = SHELL_JS[start:end]
    assert block.count("window.localStorage") == 3
    assert block.count("try {") == 2
    assert block.count("catch (err)") == 2
