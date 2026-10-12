"""
"Remove me from this household" signs out every device the adult was
signed in on, not only the one they tapped it on (defect hunt 2026-10-11).

Before: remove_member deleted the member row and _signed_out cleared the
cookie on the device that asked. Julia's other phone kept a signed cookie
naming a member that no longer exists, stayed in the household she had
left, and — with Sam the only adult left — tools.current_member() resolved
it to Sam: her phone was signed in AS Sam (picked_on_this_device true).
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app import households, security, tools
from app.main import app

BETA = "leaving-beta-passphrase"


@pytest.fixture
def beta_household():
    return households.create_household("The Leavers", BETA)


def _adult(name: str, household: int, age_group: str = "adult") -> int:
    with tools.use_household(household):
        member_id = tools.add_member(name)["member_id"]
        tools.set_member_age_group(name, age_group)
    return member_id


def _device(member_id: int | None) -> TestClient:
    c = TestClient(app)
    res = c.post("/login", data={"password": BETA, "next": "/"}, follow_redirects=False)
    assert res.status_code == 303
    if member_id is not None:
        assert c.post("/api/whoami/pick", json={"member_id": member_id}).status_code == 200
    return c


def test_removing_yourself_signs_out_your_other_devices(beta_household):
    julia = _adult("Julia", beta_household)
    sam = _adult("Sam", beta_household)
    phone, ipad, sams_phone = _device(julia), _device(julia), _device(sam)
    assert ipad.get("/api/whoami").json()["member"]["id"] == julia

    assert phone.post("/api/household/remove-me").status_code == 200

    # The iPad: signed out, not quietly turned into Sam.
    stale = ipad.cookies[security.COOKIE_NAME]
    page = ipad.get("/", headers={"accept": "text/html"}, follow_redirects=False)
    assert page.status_code == 303 and page.headers["location"].startswith("/login")
    # ...and the dead cookie is cleared, so it isn't sent for ever.
    assert any(
        h.startswith(f"{security.COOKIE_NAME}=") and "Max-Age=0" in h
        for h in page.headers.get_list("set-cookie")
    )
    ipad.cookies.set(security.COOKIE_NAME, stale)
    res = ipad.get("/api/whoami")
    assert res.status_code == 401, res.text
    # Sam is untouched, and still Sam.
    me = sams_phone.get("/api/whoami")
    assert me.status_code == 200 and me.json()["member"]["id"] == sam


def test_a_device_with_no_pick_stays_signed_in(beta_household):
    """A passphrase session that never said who it was names nobody, so
    somebody else leaving is not a reason to sign it out."""
    julia = _adult("Julia", beta_household)
    sam = _adult("Sam", beta_household)
    kitchen_tablet = _device(None)
    assert _device(julia).post("/api/household/remove-me").status_code == 200
    res = kitchen_tablet.get("/api/whoami")
    assert res.status_code == 200
    assert [a["id"] for a in res.json()["adults"]] == [sam]


def test_an_adult_re_marked_a_child_is_not_signed_out(beta_household):
    """Still a member of the household — the pick just reads as no pick,
    as it always has. Only a member row that is GONE ends the session."""
    julia = _adult("Julia", beta_household)
    _adult("Sam", beta_household)
    _adult("Ana", beta_household)
    device = _device(julia)
    with tools.use_household(beta_household):
        tools.set_member_age_group("Julia", "teen")
    res = device.get("/api/whoami")
    assert res.status_code == 200
    assert res.json()["member"] is None and res.json()["needs_pick"] is True
