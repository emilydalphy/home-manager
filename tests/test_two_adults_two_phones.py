"""
A phone whose picked adult stops being an adult acts as nobody — never as
the adult who is left (defect hunt 2026-10-22).

Two adults, Emily and Vic, each signed in on their own phone. Emily marks
Vic a Teen in Who's here. Vic is still a member, so his phone stays signed
in (households.session_still_holds) — and current_member() used to fall
through to "exactly one adult, so it's her", leaving Vic's phone acting AS
Emily: his grocery adds credited to her, and her sign-in email his to
change. Two cookie jars against one app, as the household would hit it.
"""
import pytest
from fastapi.testclient import TestClient

from app import tools
from app.main import app


def _member(name: str, age_group: str) -> int:
    member_id = tools.add_member(name)["member_id"]
    tools.set_member_age_group(name, age_group)
    return member_id


def _phone(member_id: int) -> TestClient:
    phone = TestClient(app)
    res = phone.post("/login", data={"password": "test-password", "next": "/"}, follow_redirects=False)
    assert res.status_code == 303
    assert phone.post("/api/whoami/pick", json={"member_id": member_id}).status_code == 200
    return phone


def test_a_pick_re_marked_teen_is_nobody_not_the_adult_left():
    emily = _member("Emily", "adult")
    vic = _member("Vic", "adult")
    _member("Theo", "child")
    emilys_phone, vics_phone = _phone(emily), _phone(vic)

    assert emilys_phone.post(
        "/api/memory/member/age-group", json={"name": "Vic", "age_group": "teen"}
    ).status_code == 200

    who = vics_phone.get("/api/whoami").json()
    assert who["member"] is None, "Vic's phone must not become Emily's"
    assert who["picked_on_this_device"] is False
    assert emilys_phone.get("/api/whoami").json()["member"]["name"] == "Emily"

    vics_phone.post("/api/grocery-list/add", json={"item": "bread"})
    bread = next(i for i in tools.list_grocery_list() if i["item"] == "bread")
    assert bread["added_by"] == "user", "an add from Vic's phone is not Emily's"

    assert vics_phone.get("/api/account/email").json() == {"email": None, "can_change": False}


def test_made_an_adult_again_the_same_phone_is_him_again():
    _member("Emily", "adult")
    vic = _member("Vic", "adult")
    vics_phone = _phone(vic)
    tools.set_member_age_group("Vic", "teen")
    assert vics_phone.get("/api/whoami").json()["member"] is None
    tools.set_member_age_group("Vic", "adult")
    assert vics_phone.get("/api/whoami").json()["member"]["id"] == vic


def test_a_lone_adult_with_no_pick_is_still_inferred():
    """The inference the fix narrows, not removes: no pick, one adult."""
    julia = _member("Julia", "adult")
    with tools.use_member(None):
        assert tools.current_member()["id"] == julia
    with tools.use_member(julia + 999):
        assert tools.current_member() is None


# ---------- "Text me" from a known phone (chat's set_morning_text) ----------
#
# The same household, the morning text's chat tool. With two adults and no
# name given it asked "Which of you is this for?" even from a phone that had
# answered "Who's this?" — and the model, told to pass a name it cannot know,
# could only guess. The session's adult fills the blank, as acting_name does.

def _morning(member_id: int) -> dict:
    return next(a for a in tools.get_morning_text_settings()["adults"] if a["member_id"] == member_id)


def test_text_me_from_vics_phone_is_vics_and_leaves_emilys_alone():
    emily = _member("Emily", "adult")
    vic = _member("Vic", "adult")
    tools.set_morning_text_for_member(emily, phone="416-555-0101", on=True)
    with tools.use_member(vic):
        tools.set_morning_text(phone="416-555-0102", on=True)
    assert _morning(vic) == {"member_id": vic, "name": "Vic", "phone": "+14165550102", "on": True}
    assert _morning(emily)["phone"] == "+14165550101" and _morning(emily)["on"] is True
    with tools.use_member(vic):
        tools.set_morning_text(on=False)
    assert _morning(vic)["on"] is False
    assert _morning(emily)["on"] is True, "Vic's 'stop' must not touch Emily's"


def test_a_named_adult_and_a_device_with_nobody_picked_still_behave():
    emily = _member("Emily", "adult")
    vic = _member("Vic", "adult")
    with tools.use_member(vic):
        tools.set_morning_text(phone="416-555-0101", name="Emily")
    assert _morning(emily)["phone"] == "+14165550101"
    assert _morning(vic)["phone"] == ""
    with tools.use_member(None), pytest.raises(ValueError, match="Which of you"):
        tools.set_morning_text(phone="416-555-0199")
