"""
A household is named after its main person ("Emily's house") and can be
renamed in Preferences (Emily, 2026-11-01).

Email sign-up creates the household as the "Your household" placeholder;
onboarding names it once the main person is known, and a startup backfill
names households that finished setup before. A name somebody chose is never
touched.
"""
from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from app import db, households, security, tools
from app.db import get_conn
from app.main import app
from app.tools._shared import UNNAMED_HOUSEHOLD

from test_remove_me_hands_over_sign_in import _email_household, outbox  # noqa: F401

REPO = Path(__file__).resolve().parent.parent


def _name(hid):
    conn = get_conn()
    try:
        return conn.execute("SELECT name FROM households WHERE id = ?", (hid,)).fetchone()["name"]
    finally:
        conn.close()


def _placeholder_household(person: str | None, chosen: int = 0, name: str = UNNAMED_HOUSEHOLD) -> int:
    conn = get_conn()
    hid = conn.execute("INSERT INTO households (name, name_chosen) VALUES (?, ?)", (name, chosen)).lastrowid
    conn.commit()
    conn.close()
    if person:
        with tools.use_household(hid):
            mid = tools.add_member(person)["member_id"]
            tools.set_member_age_group(person, "adult")
            tools.record_primary_member(mid)
    return hid


def test_onboarding_names_an_email_household_after_its_main_person(outbox):
    client, hid = _email_household(outbox)
    assert _name(hid) == "Rowan’s house"
    assert client.get("/api/whoami").json()["household_name"] == "Rowan’s house"
    # Every sentence that says it reads right.
    assert client.get("/api/household/leave").json()["household_name"] == "Rowan’s house"
    with tools.use_household(hid):
        token = tools.get_or_create_share_link()["token"]
    assert TestClient(app).get(f"/api/share/{token}").json()["household_name"] == "Rowan’s house"


def test_a_name_ending_in_s_takes_apostrophe_s():
    assert tools.house_name_for("James") == "James’s house"
    assert tools.house_name_for("  Mary   Ann ") == "Mary Ann’s house"
    assert tools.house_name_for("") == ""


def test_a_passphrase_household_keeps_the_name_it_was_given(client):
    hid = households.create_household("The Betas", "betas-passphrase-1")
    with tools.use_household(hid):
        mid = tools.add_member("Bea")["member_id"]
        tools.set_member_age_group("Bea", "adult")
        tools.record_primary_member(mid)
        assert tools.name_household_after_main_person() is None
    assert _name(hid) == "The Betas"
    # Household 1 too.
    assert tools.name_household_after_main_person() is None


def test_a_chosen_name_is_never_overwritten_even_if_it_is_the_placeholder_text():
    hid = _placeholder_household("Priya", chosen=1)
    with tools.use_household(hid):
        assert tools.name_household_after_main_person() is None
    assert _name(hid) == UNNAMED_HOUSEHOLD


def test_startup_backfills_households_that_set_up_before_this():
    named = _placeholder_household("Priya")
    chosen = _placeholder_household("Quinn", chosen=1)
    nobody = _placeholder_household(None)
    other = _placeholder_household("Ravi", name="Casa Ravi")
    conn = get_conn()
    db._name_households_after_main_person(conn)
    conn.commit()
    conn.close()
    assert _name(named) == "Priya’s house"
    assert _name(chosen) == UNNAMED_HOUSEHOLD
    assert _name(nobody) == UNNAMED_HOUSEHOLD
    assert _name(other) == "Casa Ravi"
    # Run again: nothing moves.
    conn = get_conn()
    db._name_households_after_main_person(conn)
    conn.commit()
    conn.close()
    assert _name(named) == "Priya’s house"


def test_renaming_in_preferences(client):
    hid = _placeholder_household("Priya")
    other = _placeholder_household("Ravi")
    phone = TestClient(app)
    phone.cookies.set(security.COOKIE_NAME, security.issue_session(hid, None))
    res = phone.post("/api/household/name", json={"name": "   "})
    assert res.status_code == 400 and res.json()["detail"] == "Type a name for your household."
    res = phone.post("/api/household/name", json={"name": "x" * 61})
    assert res.status_code == 400 and "under 60" in res.json()["detail"]
    res = phone.post("/api/household/name", json={"name": "  The   Patels "})
    assert res.status_code == 200 and res.json() == {"household_name": "The Patels"}
    assert phone.get("/api/whoami").json()["household_name"] == "The Patels"
    assert phone.get("/api/household/leave").json()["household_name"] == "The Patels"
    # Nothing names it after the main person again.
    conn = get_conn()
    db._name_households_after_main_person(conn)
    conn.commit()
    conn.close()
    assert _name(hid) == "The Patels"
    # Another household is untouched by the rename (and still gets its default).
    assert _name(other) == "Ravi’s house"


def test_a_name_nobody_can_see_is_refused_and_emoji_are_kept(client):
    hid = _placeholder_household("Priya")
    phone = TestClient(app)
    phone.cookies.set(security.COOKIE_NAME, security.issue_session(hid, None))
    for invisible in ("\u200b", "\u200d", " \ufeff ", "\u202e\u2066"):
        res = phone.post("/api/household/name", json={"name": invisible})
        assert res.status_code == 400 and res.json()["detail"] == "Type a name for your household.", invisible
    # Bidi controls are taken out of a real name, not kept in it.
    res = phone.post("/api/household/name", json={"name": "\u202eThe Patels"})
    assert res.json() == {"household_name": "The Patels"}
    family = "\U0001F468\u200d\U0001F469\u200d\U0001F467 Home"
    assert phone.post("/api/household/name", json={"name": family}).json() == {"household_name": family}


def test_the_automatic_name_fits_the_same_60_characters():
    long = "Alexandra Catherine Montgomery-Fitzwilliam Smythe Jones"
    name = tools.house_name_for(long)
    assert len(name) <= tools.HOUSEHOLD_NAME_MAX
    assert name == "Alexandra Catherine Montgomery-Fitzwilliam Smythe’s house"
    assert len(tools.house_name_for("X" * 80)) == tools.HOUSEHOLD_NAME_MAX
    assert tools.house_name_for("\u200b") == ""


def test_the_screens_say_it_well():
    js = (REPO / "static" / "shell.js").read_text(encoding="utf-8")
    assert "'<span class=\"prefs-row-title\">Household name</span>'" in js
    assert "'/api/household/name'" in js
    assert "savedLine('Household name', 'saved')" in js
    share = (REPO / "static" / "share.html").read_text(encoding="utf-8")
    assert "'Meals this week at ' + plan.household_name" in share
    assert "'s meals this week" not in share
