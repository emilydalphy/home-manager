"""
"Remove me from this household" takes the leaver off "who cooks" too
(defect hunt 2026-10-20).

Before: setup's "Who does the cooking?" answered "Vineeth" is stored as a
household-level rhythm row whose `who` is his NAME (rhythm.set_cooking_role
takes free text), and remove_member only cleared rhythm rows keyed by
member_name. After Vineeth removed himself, Julia's Kitchen still said
Vineeth was cooking tonight (cooker-view `cook_name`), Today put his name on
every cook and prep move, and What we know said he does the cooking — for
somebody no longer in the household.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app import households, tools
from app.main import app

BETA = "leaver-cook-passphrase"


@pytest.fixture
def beta_household():
    return households.create_household("The Cooks", BETA)


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


def _say_who_cooks(device: TestClient, role: str, who: str = "") -> None:
    body = {"cooking_role": role}
    if who:
        body["cooking_role_who"] = who
    res = device.post("/api/onboarding/rhythm", json=body)
    assert res.status_code == 200, res.text


def test_the_cook_who_left_is_no_longer_the_cook(beta_household):
    julia = _adult("Julia", beta_household)
    vineeth = _adult("Vineeth", beta_household)
    julias_phone, his_phone = _device(julia), _device(vineeth)
    # Typed the way a person types it — the answer is free text.
    _say_who_cooks(julias_phone, "one_person", "vineeth ")
    assert julias_phone.get("/api/cooker-view").json()["cook_name"]

    assert his_phone.post("/api/household/remove-me").status_code == 200

    assert julias_phone.get("/api/cooker-view").json()["cook_name"] is None
    rhythm = julias_phone.get("/api/memory").json()["rhythm"]
    assert rhythm["cooking_role"] is None


def test_someone_else_leaving_keeps_the_answer(beta_household):
    julia = _adult("Julia", beta_household)
    vineeth = _adult("Vineeth", beta_household)
    julias_phone, his_phone = _device(julia), _device(vineeth)
    _say_who_cooks(julias_phone, "one_person", "Julia")

    assert his_phone.post("/api/household/remove-me").status_code == 200

    assert julias_phone.get("/api/cooker-view").json()["cook_name"] == "Julia"
    assert julias_phone.get("/api/memory").json()["rhythm"]["cooking_role"] == {
        "value": "one_person", "who": "Julia",
    }
