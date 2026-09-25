"""
The other adult's first open (Loop Board "First open for the adult who
didn't set Pomona up", 2026-09-25, branch household-first-open).

The first time an adult who didn't set Pomona up opens it, they see one
short welcome: their name, who set things up, what's already there, one
button into Today. Once per adult (a server-side flag on the member row),
never to the adult who set things up, and never to anyone who already
existed when this shipped. See app/tools/first_open.py for the rule.
"""
from __future__ import annotations

import sqlite3
from datetime import timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import db as _db
from app import security, tools
from app.db import get_conn
from app.main import app
from app.tools import weekly_plan as _wp

REPO = Path(__file__).resolve().parent.parent
SHELL_JS = (REPO / "static" / "shell.js").read_text(encoding="utf-8")
SHELL_CSS = (REPO / "static" / "shell.css").read_text(encoding="utf-8")


def _sign_in(c):
    res = c.post("/login", data={"password": "test-password", "next": "/"}, follow_redirects=False)
    assert res.status_code == 303
    return c.cookies[security.COOKIE_NAME]


def _adult(name: str) -> int:
    member_id = tools.add_member(name)["member_id"]
    tools.set_member_age_group(name, "Adult")
    return member_id


def _open_as(member_id: int) -> tuple[TestClient, dict]:
    """A fresh device: sign in, pick, return the client and the pick's body."""
    c = TestClient(app)
    _sign_in(c)
    body = c.post("/api/whoami/pick", json={"member_id": member_id}).json()
    return c, body


# ---------- the flag and its defaults ----------

def test_a_new_member_has_not_seen_it_and_nobody_has_set_up_yet():
    mid = _adult("Emily")
    conn = get_conn()
    member = conn.execute("SELECT first_open_seen_at FROM members WHERE id = ?", (mid,)).fetchone()
    hh = conn.execute("SELECT set_up_by_member_id FROM households WHERE id = 1").fetchone()
    conn.close()
    assert member["first_open_seen_at"] == ""
    assert hh["set_up_by_member_id"] is None


# ---------- who sets things up never sees it ----------

def test_a_one_adult_household_never_sees_it(client):
    mid = _adult("Emily")
    _sign_in(client)
    body = client.get("/api/whoami").json()
    assert body["member"]["id"] == mid
    assert body["first_open"] is False
    assert body["set_up_by"] == "Emily"
    conn = get_conn()
    hh = conn.execute("SELECT set_up_by_member_id FROM households WHERE id = 1").fetchone()
    conn.close()
    assert hh["set_up_by_member_id"] == mid


def test_the_first_adult_to_open_is_the_one_who_set_up_and_never_sees_it(client):
    emily = _adult("Emily")
    vineeth = _adult("Vineeth")
    _sign_in(client)
    before = client.get("/api/whoami").json()
    assert before["needs_pick"] is True
    assert before["first_open"] is False  # nobody picked, nothing to show

    picked = client.post("/api/whoami/pick", json={"member_id": emily}).json()
    assert picked["first_open"] is False
    assert client.get("/api/whoami").json()["first_open"] is False

    # Vineeth, on his own phone, is the other adult.
    with TestClient(app) as phone:
        _sign_in(phone)
        body = phone.post("/api/whoami/pick", json={"member_id": vineeth}).json()
        assert body["first_open"] is True
        assert body["set_up_by"] == "Emily"
        who = phone.get("/api/whoami").json()
        assert who["first_open"] is True
        assert who["set_up_by"] == "Emily"


def test_the_setup_adult_stays_out_even_on_a_second_device():
    emily = _adult("Emily")
    _adult("Vineeth")
    _open_as(emily)
    c2, body = _open_as(emily)
    assert body["first_open"] is False
    assert c2.get("/api/whoami").json()["first_open"] is False


# ---------- once per adult, on any device ----------

def test_leaving_the_welcome_marks_it_seen_for_good():
    emily = _adult("Emily")
    vineeth = _adult("Vineeth")
    _open_as(emily)
    phone, body = _open_as(vineeth)
    assert body["first_open"] is True

    # Reloading mid-welcome (nothing marked yet) shows it again.
    assert phone.get("/api/whoami").json()["first_open"] is True

    res = phone.post("/api/first-open/seen")
    assert res.status_code == 200
    assert res.json()["seen"] is True
    assert phone.get("/api/whoami").json()["first_open"] is False

    # A new phone: still seen — the flag is on the member, not the device.
    _, again = _open_as(vineeth)
    assert again["first_open"] is False

    # And marking twice keeps the first stamp.
    conn = get_conn()
    first_stamp = conn.execute("SELECT first_open_seen_at FROM members WHERE id = ?", (vineeth,)).fetchone()[0]
    conn.close()
    phone.post("/api/first-open/seen")
    conn = get_conn()
    assert conn.execute("SELECT first_open_seen_at FROM members WHERE id = ?", (vineeth,)).fetchone()[0] == first_stamp
    conn.close()


def test_marking_seen_with_nobody_picked_is_refused(client):
    _adult("Emily")
    _adult("Vineeth")
    _sign_in(client)
    res = client.post("/api/first-open/seen")
    assert res.status_code == 400


def test_a_third_adult_added_later_sees_it_too():
    emily = _adult("Emily")
    vineeth = _adult("Vineeth")
    _open_as(emily)
    phone, _ = _open_as(vineeth)
    phone.post("/api/first-open/seen")
    sam = _adult("Sam")
    _, body = _open_as(sam)
    assert body["first_open"] is True
    assert body["set_up_by"] == "Emily"


def test_switching_on_a_shared_phone_follows_the_session():
    emily = _adult("Emily")
    vineeth = _adult("Vineeth")
    c, body = _open_as(emily)
    assert body["first_open"] is False
    switched = c.post("/api/whoami/pick", json={"member_id": vineeth}).json()
    assert switched["first_open"] is True
    back = c.post("/api/whoami/pick", json={"member_id": emily}).json()
    assert back["first_open"] is False


# ---------- existing households: the migration ----------

def _old_database(path) -> sqlite3.Connection:
    """A database as it stood before this shipped: schema.sql (which does
    not declare the new columns) plus every earlier run-once step."""
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    with open(_db.SCHEMA_PATH, encoding="utf-8") as f:
        conn.executescript(f.read())
    conn.execute(f"PRAGMA user_version = {_db._DATA_VERSION_COOK_COUNTERS}")
    return conn


def test_migration_marks_every_existing_member_seen_and_names_the_setter_up(tmp_path):
    conn = _old_database(tmp_path / "old.db")
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(members)")}
    assert "first_open_seen_at" not in cols  # really the old shape
    conn.execute("INSERT INTO members (household_id, name, age_group) VALUES (1, 'Emily', 'Adult')")
    conn.execute("INSERT INTO members (household_id, name, age_group) VALUES (1, 'Vineeth', 'Adult')")
    conn.execute("INSERT INTO members (household_id, name, age_group) VALUES (1, 'Kid', 'child')")
    vineeth = conn.execute("SELECT id FROM members WHERE name = 'Vineeth'").fetchone()[0]
    # Vineeth approved the most recent week — the migration names him.
    _db._run_migrations(conn)  # add the columns first, as startup does
    conn.execute("PRAGMA user_version = 1")  # ...but keep the run-once step pending
    conn.execute("UPDATE members SET first_open_seen_at = ''")
    conn.execute("UPDATE households SET set_up_by_member_id = NULL")
    conn.execute(
        "INSERT INTO weekly_plans (household_id, week_start_date, status, approved_by_member_id, approved_at) "
        "VALUES (1, '2026-09-14', 'approved', ?, '2026-09-13 10:00:00')",
        (vineeth,),
    )
    _db._run_migrations(conn)
    conn.commit()

    stamps = {r["name"]: r["first_open_seen_at"] for r in conn.execute("SELECT name, first_open_seen_at FROM members")}
    assert stamps == {"Emily": "before-first-open", "Vineeth": "before-first-open", "Kid": "before-first-open"}
    assert conn.execute("SELECT set_up_by_member_id FROM households WHERE id = 1").fetchone()[0] == vineeth
    assert conn.execute("PRAGMA user_version").fetchone()[0] == _db._DATA_VERSION_FIRST_OPEN

    # A member added after the migration hasn't seen it, and a second
    # startup doesn't stamp them.
    conn.execute("INSERT INTO members (household_id, name, age_group) VALUES (1, 'Sam', 'Adult')")
    _db._run_migrations(conn)
    assert conn.execute("SELECT first_open_seen_at FROM members WHERE name = 'Sam'").fetchone()[0] == ""
    conn.close()


def test_migration_falls_back_to_the_first_adult_when_nobody_approved(tmp_path):
    conn = _old_database(tmp_path / "old.db")
    conn.execute("INSERT INTO members (household_id, name, age_group) VALUES (1, 'Kid', 'child')")
    conn.execute("INSERT INTO members (household_id, name, age_group) VALUES (1, 'Emily', 'adult')")
    conn.execute("INSERT INTO members (household_id, name, age_group) VALUES (1, 'Vineeth', 'adult')")
    emily = conn.execute("SELECT id FROM members WHERE name = 'Emily'").fetchone()[0]
    _db._run_migrations(conn)
    conn.commit()
    assert conn.execute("SELECT set_up_by_member_id FROM households WHERE id = 1").fetchone()[0] == emily
    conn.close()


def test_existing_adults_open_straight_into_today_after_the_migration():
    """The end-to-end promise to Emily, her partner and the beta testers:
    stamped members get no welcome, whichever of them opens first."""
    emily = _adult("Emily")
    vineeth = _adult("Vineeth")
    conn = get_conn()
    _db._mark_existing_members_first_open_seen(conn)
    conn.commit()
    conn.close()
    _, v = _open_as(vineeth)  # the partner opening first changes nothing
    _, e = _open_as(emily)
    assert v["first_open"] is False
    assert e["first_open"] is False
    assert e["set_up_by"] == "Emily"


# ---------- the preview (option A) ----------

def _plan(status: str, start, days: int = 7) -> int:
    conn = get_conn()
    cur = conn.execute(
        "INSERT INTO weekly_plans (household_id, week_start_date, content_start_date, day_count, status) "
        "VALUES (1, ?, ?, ?, ?)",
        (start.isoformat(), start.isoformat(), days, status),
    )
    conn.commit()
    conn.close()
    return cur.lastrowid


def _dinner(plan_id: int, day, name: str, state: str = "planned"):
    conn = get_conn()
    conn.execute(
        "INSERT INTO meal_plan_entries (household_id, date, slot, freeform_meal, weekly_plan_id, slot_state) "
        "VALUES (1, ?, 'dinner', ?, ?, ?)",
        (day.isoformat(), name, plan_id, state),
    )
    conn.commit()
    conn.close()


def _grocery(item: str):
    conn = get_conn()
    conn.execute("INSERT INTO grocery_items (household_id, item, status) VALUES (1, ?, 'needed')", (item,))
    conn.commit()
    conn.close()


def test_preview_with_no_week_says_so(client):
    _sign_in(client)
    body = client.get("/api/first-open").json()
    assert body["week_state"] == "none"
    assert body["dinners"] == []
    assert body["list_count"] == 0


def test_preview_names_tonight_and_the_next_couple_of_dinners(client):
    today = _wp._household_today()
    plan = _plan("approved", today - timedelta(days=1))
    _dinner(plan, today - timedelta(days=1), "Yesterday's Soup")
    _dinner(plan, today, "Chicken Skewers")
    _dinner(plan, today + timedelta(days=1), "Tacos")
    _dinner(plan, today + timedelta(days=2), "Salmon")
    _dinner(plan, today + timedelta(days=3), "Chili")
    _grocery("carrots")
    _grocery("rice")
    _sign_in(client)
    body = client.get("/api/first-open").json()
    assert body["week_state"] == "approved"
    assert [d["title"] for d in body["dinners"]] == ["Chicken Skewers", "Tacos", "Salmon"]
    assert body["dinners"][0]["is_tonight"] is True
    assert body["dinners"][1]["is_tomorrow"] is True
    assert body["list_count"] == 2


def test_preview_says_a_draft_is_a_draft(client):
    today = _wp._household_today()
    plan = _plan("draft", today)
    _dinner(plan, today, "Pasta")
    _sign_in(client)
    body = client.get("/api/first-open").json()
    assert body["week_state"] == "draft"
    assert [d["title"] for d in body["dinners"]] == ["Pasta"]


# ---------- the shell ----------

def test_shell_has_the_ab_switch_defaulting_to_the_preview():
    assert "var FIRST_OPEN_STYLE = 'preview';" in SHELL_JS
    assert "'line'" in SHELL_JS


def test_shell_opens_the_welcome_from_whoami_and_marks_it_seen_on_leaving():
    assert "shellWho.first_open = !!data.first_open;" in SHELL_JS
    assert "if (shellWho.first_open) openFirstOpen();" in SHELL_JS
    assert "fetch('/api/first-open/seen', { method: 'POST' })" in SHELL_JS
    assert "fetch('/api/first-open')" in SHELL_JS
    assert "Not now" in SHELL_JS
    assert ".first-open-screen {" in SHELL_CSS


def test_the_welcome_adds_no_animation():
    start = SHELL_CSS.index("/* ---------- The other adult's first open")
    block = SHELL_CSS[start:start + 6000]
    assert "animation" not in block.split("No animation")[1]
    assert "transition" not in block


# ---------- setup is recorded when onboarding finishes ----------

def _onboard(c, members):
    res = c.post("/api/onboarding/household", json={"members": members, "pets": [], "goals": ""})
    assert res.status_code == 200


def _members_by_name() -> dict:
    conn = get_conn()
    rows = conn.execute("SELECT id, name, first_open_seen_at FROM members").fetchall()
    conn.close()
    return {r["name"]: dict(r) for r in rows}


def _set_up_by():
    conn = get_conn()
    value = conn.execute("SELECT set_up_by_member_id FROM households WHERE id = 1").fetchone()[0]
    conn.close()
    return value


def test_finishing_onboarding_records_the_first_adult_typed(client):
    _sign_in(client)
    _onboard(client, [
        {"name": "Kid", "age_group": "child"},
        {"name": "Emily", "age_group": "Adult"},
        {"name": "Vineeth", "age_group": "Adult"},
    ])
    people = _members_by_name()
    assert _set_up_by() == people["Emily"]["id"]
    assert people["Emily"]["first_open_seen_at"] == "set-up"
    assert people["Vineeth"]["first_open_seen_at"] == ""


def test_partner_opening_the_app_before_the_inviter_still_gets_the_welcome(client):
    """The race an invite link opens: onboarding has finished on Emily's
    phone, but Vineeth's session reaches the main app before hers does. He
    must not be recorded as the one who set it up."""
    _sign_in(client)
    _onboard(client, [
        {"name": "Emily", "age_group": "Adult"},
        {"name": "Vineeth", "age_group": "Adult"},
    ])
    people = _members_by_name()

    phone, body = _open_as(people["Vineeth"]["id"])  # partner first
    assert body["first_open"] is True
    assert body["set_up_by"] == "Emily"
    assert phone.get("/api/whoami").json()["first_open"] is True

    # Emily reaches the main app afterwards: no welcome for her.
    picked = client.post("/api/whoami/pick", json={"member_id": people["Emily"]["id"]}).json()
    assert picked["first_open"] is False
    assert picked["set_up_by"] == "Emily"


def test_the_adult_in_the_session_at_setup_is_the_one_recorded():
    emily = _adult("Emily")
    vineeth = _adult("Vineeth")
    c, _ = _open_as(vineeth)
    # Undo what the pick's fallback just recorded, as if Vineeth's session
    # were simply the one finishing onboarding.
    conn = get_conn()
    conn.execute("UPDATE households SET set_up_by_member_id = NULL WHERE id = 1")
    conn.execute("UPDATE members SET first_open_seen_at = ''")
    conn.commit()
    conn.close()
    _onboard(c, [{"name": "Emily", "age_group": "Adult"}, {"name": "Vineeth", "age_group": "Adult"}])
    assert _set_up_by() == vineeth
    _, body = _open_as(emily)
    assert body["first_open"] is True
    assert body["set_up_by"] == "Vineeth"


def test_a_second_pass_through_onboarding_never_moves_the_record(client):
    _sign_in(client)
    _onboard(client, [{"name": "Emily", "age_group": "Adult"}, {"name": "Vineeth", "age_group": "Adult"}])
    people = _members_by_name()
    vineeth_client, _ = _open_as(people["Vineeth"]["id"])
    _onboard(vineeth_client, [{"name": "Vineeth", "age_group": "Adult"}])
    assert _set_up_by() == people["Emily"]["id"]


def test_onboarding_with_no_adults_leaves_the_fallback_in_charge(client):
    _sign_in(client)
    _onboard(client, [{"name": "Kid", "age_group": "child"}])
    assert _set_up_by() is None
