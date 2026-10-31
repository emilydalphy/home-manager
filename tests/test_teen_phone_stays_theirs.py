"""
Emily's picks, 2026-10-26 ("go with your picks"), on the card "As the second
adult, when the other adult marks me a Teen, my phone stops acting as them":

54. After Vic is marked Teen, his phone signing in again used to be read as
    the one adult left (Emily): the device's remembered answer was "spent",
    the fresh session had no pick, and a one-adult house infers its adult.
    Now the phone stays Vic's: the session names him, which reads as nobody.
55. Vic's phone got no word about the change. It now gets one short note,
    once.
"""
from fastapi.testclient import TestClient

from app import security, tools
from app.db import get_conn
from app.main import app

NOTE = ("You’re down as a Teen here now, so what you do on this phone isn’t under "
        "your name. An adult can change it in Preferences, under Who’s here.")


def _member(name: str, age_group: str) -> int:
    member_id = tools.add_member(name)["member_id"]
    tools.set_member_age_group(name, age_group)
    return member_id


def _sign_in(phone):
    res = phone.post("/login", data={"password": "test-password", "next": "/"}, follow_redirects=False)
    assert res.status_code == 303
    return res


def _phone(member_id: int) -> TestClient:
    phone = TestClient(app)
    _sign_in(phone)
    assert phone.post("/api/whoami/pick", json={"member_id": member_id}).status_code == 200
    return phone


def test_signing_in_again_after_being_marked_teen_is_not_the_adult_left():
    """CATCH. Red before: whoami said Emily after Vic signed in again."""
    emily = _member("Emily", "adult")
    vic = _member("Vic", "adult")
    emilys_phone, vics_phone = _phone(emily), _phone(vic)
    assert emilys_phone.post(
        "/api/memory/member/age-group", json={"name": "Vic", "age_group": "teen"}
    ).status_code == 200

    vics_phone.get("/logout", follow_redirects=False)
    res = _sign_in(vics_phone)
    # The device cookie is kept, not cleared.
    assert not any(h.startswith(f"{security.DEVICE_COOKIE}=;") or
                   (h.startswith(f"{security.DEVICE_COOKIE}=") and "Max-Age=0" in h)
                   for h in res.headers.get_list("set-cookie"))

    who = vics_phone.get("/api/whoami").json()
    assert who["member"] is None, "Vic's phone must not sign in as Emily"
    assert who["needs_pick"] is False
    vics_phone.post("/api/grocery-list/add", json={"item": "bread"})
    bread = next(i for i in tools.list_grocery_list() if i["item"] == "bread")
    assert bread["added_by"] == "user"
    assert vics_phone.get("/api/account/email").json() == {"email": None, "can_change": False}
    # Emily's own phone is untouched.
    assert emilys_phone.get("/api/whoami").json()["member"]["name"] == "Emily"


def test_made_an_adult_again_the_phone_is_his_after_signing_in():
    _member("Emily", "adult")
    vic = _member("Vic", "adult")
    vics_phone = _phone(vic)
    tools.set_member_age_group("Vic", "teen")
    _sign_in(vics_phone)
    tools.set_member_age_group("Vic", "adult")
    _sign_in(vics_phone)
    assert vics_phone.get("/api/whoami").json()["member"]["name"] == "Vic"


def test_a_lone_adult_never_picked_is_still_inferred():
    _member("Emily", "adult")
    phone = TestClient(app)
    _sign_in(phone)
    assert phone.get("/api/whoami").json()["member"]["name"] == "Emily"


def test_the_teen_phone_gets_one_note_once():
    """CATCH (55). Red before: whoami carried no note."""
    emily = _member("Emily", "adult")
    vic = _member("Vic", "adult")
    emilys_phone, vics_phone = _phone(emily), _phone(vic)
    assert vics_phone.get("/api/whoami").json()["age_note"] == ""

    tools.set_member_age_group("Vic", "teen")
    assert vics_phone.get("/api/whoami").json()["age_note"] == NOTE
    assert vics_phone.get("/api/whoami").json()["age_note"] == ""
    # Signing in again doesn't repeat it.
    _sign_in(vics_phone)
    assert vics_phone.get("/api/whoami").json()["age_note"] == ""
    # Never on the adult's phone.
    assert emilys_phone.get("/api/whoami").json()["age_note"] == ""


def test_a_later_change_gets_its_own_note():
    _member("Emily", "adult")
    vic = _member("Vic", "adult")
    vics_phone = _phone(vic)
    tools.set_member_age_group("Vic", "teen")
    assert vics_phone.get("/api/whoami").json()["age_note"] == NOTE
    tools.set_member_age_group("Vic", "adult")
    assert vics_phone.get("/api/whoami").json()["age_note"] == ""
    tools.set_member_age_group("Vic", "child")
    assert vics_phone.get("/api/whoami").json()["age_note"].startswith("You’re down as a Child here now")


def test_no_note_without_a_pick():
    _member("Emily", "adult")
    _member("Vic", "teen")
    phone = TestClient(app)
    _sign_in(phone)
    assert phone.get("/api/whoami").json()["age_note"] == ""
    conn = get_conn()
    seen = conn.execute("SELECT age_note_shown_for FROM members WHERE name = 'Vic'").fetchone()[0]
    conn.close()
    assert seen == ""


def test_the_shell_shows_the_note_at_boot():
    from pathlib import Path
    js = (Path(__file__).resolve().parent.parent / "static" / "shell.js").read_text(encoding="utf-8")
    assert "shellWho.age_note = typeof data.age_note === 'string'" in js
    assert "if (shellWho.age_note) showToast(shellWho.age_note, null, 9000);" in js
