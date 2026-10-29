"""
A helper ("Someone not eating here" — a nanny, a grandparent who helps with
dinners; members.eats_here = 0) is never what a household is left with, and
never the one who deletes it (defect hunt 2026-10-29).

Before: the family's last adult could "Remove me" with only the nanny left
— handing her the household, the children and their allergies, with no main
person (primary_member never resolves to a helper), so nobody could ever
take her out again. And the nanny could type DELETE and delete everyone's
everything, while taking a single person out was the main person's alone.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app import households, invites, tools
from app.db import get_conn
from app.main import app

BETA = "helper-strand-passphrase"


@pytest.fixture
def household():
    return households.create_household("The Helped", BETA)


def _adult(name: str, household: int) -> int:
    with tools.use_household(household):
        member_id = tools.add_member(name)["member_id"]
        tools.set_member_age_group(name, "adult")
    return member_id


def _device(member_id: int) -> TestClient:
    c = TestClient(app)
    res = c.post("/login", data={"password": BETA, "next": "/"}, follow_redirects=False)
    assert res.status_code == 303
    assert c.post("/api/whoami/pick", json={"member_id": member_id}).status_code == 200
    return c


def _member_ids(household: int) -> set[int]:
    conn = get_conn()
    try:
        return {r[0] for r in conn.execute("SELECT id FROM members WHERE household_id = ?", (household,))}
    finally:
        conn.close()


def test_the_last_adult_living_here_cant_leave_the_household_to_a_helper(household):
    sam = _adult("Sam", household)
    with tools.use_household(household):
        tools.add_member("Kit")
        tools.set_member_age_group("Kit", "child")
    maria = invites.add_adult(household, "Maria", eats_here=False)
    before = _member_ids(household)
    sam_phone = _device(sam)

    assert sam_phone.get("/api/household/leave").json()["can_remove_self"] is False
    res = sam_phone.post("/api/household/remove-me")
    assert res.status_code == 400
    assert "Maria" in res.json()["detail"] and "delete the household" in res.json()["detail"]
    assert _member_ids(household) == before
    # Sam is still the main person, so the sentence's "take Maria out" is his to do.
    assert sam_phone.post("/api/household/members/remove", json={"member_id": maria}).status_code == 200


def test_another_adult_living_here_still_lets_you_leave(household):
    sam = _adult("Sam", household)
    alex = _adult("Alex", household)
    invites.add_adult(household, "Maria", eats_here=False)
    res = _device(sam).post("/api/household/remove-me")
    assert res.status_code == 200, res.text
    assert sam not in _member_ids(household) and alex in _member_ids(household)


def test_a_helper_can_leave_but_not_delete(household):
    sam = _adult("Sam", household)
    maria = invites.add_adult(household, "Maria", eats_here=False)
    maria_phone = _device(maria)

    state = maria_phone.get("/api/household/leave").json()
    assert state["can_delete"] is False and state["can_remove_self"] is True

    res = maria_phone.post("/api/household/delete", json={"confirm": "DELETE"})
    assert res.status_code == 403
    assert "remove just yourself" in res.json()["detail"]
    assert households.household_exists(household)

    assert maria_phone.post("/api/household/remove-me").status_code == 200
    assert _member_ids(household) == {sam}


def test_an_adult_living_here_can_still_delete(household):
    sam = _adult("Sam", household)
    invites.add_adult(household, "Maria", eats_here=False)
    sam_phone = _device(sam)
    assert sam_phone.get("/api/household/leave").json()["can_delete"] is True
    res = sam_phone.post("/api/household/delete", json={"confirm": "DELETE"})
    assert res.status_code == 200, res.text
    assert not households.household_exists(household)


def test_the_last_adult_living_here_cant_be_made_a_teen_while_a_helper_remains(household):
    pat = _adult("Pat", household)
    with tools.use_household(household):
        tools.add_member("Kid")
        tools.set_member_age_group("Kid", "child")
    nan = invites.add_adult(household, "Nan", eats_here=False)
    nan_phone = _device(nan)

    res = nan_phone.post("/api/memory/member/age-group", json={"name": "Pat", "age_group": "teen"})
    assert res.status_code == 400
    assert "only adult who lives here" in res.json()["detail"]
    with tools.use_household(household):
        with pytest.raises(Exception, match="only adult who lives here"):
            tools.set_member_age_group_for_chat("pat", "child")
    conn = get_conn()
    try:
        assert conn.execute("SELECT age_group FROM members WHERE id = ?", (pat,)).fetchone()[0] == "adult"
    finally:
        conn.close()


def test_a_helper_who_is_somehow_the_only_adult_can_still_leave(household):
    with tools.use_household(household):
        tools.add_member("Kid")
        tools.set_member_age_group("Kid", "child")
    nan = invites.add_adult(household, "Nan", eats_here=False)
    nan_phone = _device(nan)
    state = nan_phone.get("/api/household/leave").json()
    assert state["can_remove_self"] is True and state["can_delete"] is False
    assert nan_phone.post("/api/household/remove-me").status_code == 200
