"""
Changing the passphrase signs everyone else out (Emily, 2026-11-01).

The household carries a sign-in epoch (households.session_epoch); every
session cookie carries the epoch it was minted under, and auth_middleware
refuses one that is behind. These pin the property through real requests:
devices of the household whose passphrase changed are signed out (to the
ordinary sign-in screen), the device that made the change can be kept in,
another household is untouched, and nobody is signed out by the deploy
itself.
"""
import time

import pytest
from fastapi.testclient import TestClient

from app import households, security
from app.db import get_conn
from app.main import app

OLD = "beta-passphrase-old"
NEW = "beta-passphrase-new"


@pytest.fixture
def beta():
    return households.create_household("The Betas", OLD)


def _device(passphrase):
    c = TestClient(app)
    c.__enter__()
    res = c.post("/login", data={"password": passphrase, "next": "/"}, follow_redirects=False)
    assert res.status_code == 303
    return c


def test_a_passphrase_change_signs_every_device_of_that_household_out(beta):
    phone, tablet = _device(OLD), _device(OLD)
    try:
        assert phone.get("/api/whoami").json()["household_id"] == beta
        assert tablet.get("/api/whoami").status_code == 200

        households.set_passphrase(beta, NEW)

        for device in (phone, tablet):
            res = device.get("/api/whoami")
            assert res.status_code == 401
            # The marker api.js keys "go to sign-in" on — not an error page.
            assert res.headers.get(security.SIGNED_OUT_HEADER) == "1"
        # A page asked for by the browser lands on the normal sign-in screen.
        page = phone.get("/", headers={"accept": "text/html"}, follow_redirects=False)
        assert page.status_code == 303
        assert page.headers["location"].startswith("/login")
        assert phone.get("/login").status_code == 200

        # The old passphrase is gone; the new one signs straight back in.
        bad = phone.post("/login", data={"password": OLD, "next": "/"}, follow_redirects=False)
        assert bad.status_code != 303
        ok = phone.post("/login", data={"password": NEW, "next": "/"}, follow_redirects=False)
        assert ok.status_code == 303
        assert phone.get("/api/whoami").json()["household_id"] == beta
        # ...and the tablet is still out until it signs in too.
        assert tablet.get("/api/whoami").status_code == 401
    finally:
        phone.__exit__(None, None, None)
        tablet.__exit__(None, None, None)


def test_another_households_cookie_is_unaffected(beta):
    emily = _device("test-password")
    other = households.create_household("The Others", "others-passphrase")
    neighbour = _device("others-passphrase")
    try:
        households.set_passphrase(beta, NEW)
        assert emily.get("/api/whoami").json()["household_id"] == 1
        assert neighbour.get("/api/whoami").json()["household_id"] == other
        assert households.session_epoch(1) == 0
        assert households.session_epoch(other) == 0
        assert households.session_epoch(beta) == 1
    finally:
        emily.__exit__(None, None, None)
        neighbour.__exit__(None, None, None)


def test_the_device_that_made_the_change_can_stay_signed_in(beta):
    mine, theirs = _device(OLD), _device(OLD)
    try:
        before = mine.cookies[security.COOKIE_NAME]
        households.set_passphrase(beta, NEW)
        kept = security.reissue_on_current_epoch(before)
        assert kept is not None
        mine.cookies.set(security.COOKIE_NAME, kept, domain="testserver.local")
        assert mine.get("/api/whoami").status_code == 200
        # Same sitting: session id and sign-in time carried over.
        assert security._decode_session(kept)[0] == security._decode_session(before)[0]
        assert security._decode_session(kept)[2] == security._decode_session(before)[2]
        assert theirs.get("/api/whoami").status_code == 401
    finally:
        mine.__exit__(None, None, None)
        theirs.__exit__(None, None, None)


def test_cookies_from_before_this_change_keep_working_until_a_change(client, beta):
    """No mass sign-out on deploy: the five-part cookie every device holds
    today reads as epoch 0, which is every household's epoch until its
    passphrase first changes — and while it is 0, new cookies keep that
    shape too."""
    fresh = security.issue_session(beta, None)
    assert len(fresh.split(".")) == 5
    client.cookies.set(security.COOKIE_NAME, fresh)
    assert client.get("/api/whoami").status_code == 200
    households.set_passphrase(beta, NEW)
    assert client.get("/api/whoami").status_code == 401
    assert len(security.issue_session(beta, None).split(".")) == 6


def test_an_edited_epoch_is_not_a_cookie(client, beta):
    households.set_passphrase(beta, NEW)
    households.set_passphrase(beta, OLD + "-again")
    good = security.issue_session(beta, None)
    parts = good.split(".")
    assert parts[4] == "2"
    parts[4] = "1"
    assert security._decode_session(".".join(parts)) is None
    # Dropping the epoch to pass as an old five-part cookie fails the signature too.
    del parts[4]
    assert security._decode_session(".".join(parts)) is None


def test_renewal_never_carries_a_signed_out_device_forward(client, beta):
    """A cookie old enough to be renewed, minted before the change, is
    refused — not re-minted onto the new epoch by the daily renewal."""
    old = security.issue_session(beta, None, issued_at=int(time.time()) - 2 * security.COOKIE_RENEW_AFTER)
    households.set_passphrase(beta, NEW)
    client.cookies.set(security.COOKIE_NAME, old)
    res = client.get("/api/whoami")
    assert res.status_code == 401
    for header in res.headers.get_list("set-cookie"):
        if header.startswith(f"{security.COOKIE_NAME}="):
            value = header.split(";", 1)[0].split("=", 1)[1].strip('"')
            assert security._decode_session(value) is None, "a signed-out cookie was renewed"


def test_setting_a_first_passphrase_signs_nobody_out(client):
    """An email household given a passphrase for the first time has changed
    no key anyone was using."""
    conn = get_conn()
    hid = conn.execute("INSERT INTO households (name) VALUES ('Email House')").lastrowid
    conn.commit()
    conn.close()
    client.cookies.set(security.COOKIE_NAME, security.issue_session(hid, None))
    households.set_passphrase(hid, "a-first-passphrase")
    assert households.session_epoch(hid) == 0
    assert client.get("/api/whoami").status_code == 200


def test_household_ones_passphrase_change_is_noticed_at_startup(monkeypatch, client):
    """HOME_MANAGER_PASSWORD is changed in Railway, not through set_passphrase:
    the first start records it (signing nobody out), the same value changes
    nothing, a different value signs household 1's devices out."""
    conn = get_conn()
    conn.execute("DELETE FROM env_passphrase_seen")
    conn.commit()
    conn.close()
    assert households.note_env_passphrase() is False  # first sight: recorded
    assert households.note_env_passphrase() is False  # unchanged
    client.post("/login", data={"password": "test-password", "next": "/"}, follow_redirects=False)
    assert client.get("/api/whoami").status_code == 200

    monkeypatch.setenv("HOME_MANAGER_PASSWORD", "a-brand-new-one")
    assert households.note_env_passphrase() is True
    assert households.session_epoch(1) == 1
    assert client.get("/api/whoami").status_code == 401
    ok = client.post("/login", data={"password": "a-brand-new-one", "next": "/"}, follow_redirects=False)
    assert ok.status_code == 303
    assert client.get("/api/whoami").status_code == 200
    # And the fingerprint is not the passphrase.
    conn = get_conn()
    stored = conn.execute("SELECT password_hash FROM env_passphrase_seen").fetchone()[0]
    conn.close()
    assert "a-brand-new-one" not in stored

