"""
Delete my household (and remove myself) from inside Pomona (Loop Board
"App Store: delete my household (and remove myself) from inside Pomona",
Emily 2026-09-27). Apple requires in-app account deletion.

The property that matters is not "a route exists" but "after it runs,
nothing of that household is left and nothing of anyone else's is gone".
So the central test seeds a row in EVERY table that carries a household_id
— discovered from the database, checked against schema.sql — for three
households, deletes one, and counts.
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import household_deletion, households, invites, recipe_photos, security, tools
from app.db import SCHEMA_PATH, get_conn
from app.main import app
from app.tools._shared import DEFAULT_HOUSEHOLD_ID

REPO = Path(__file__).resolve().parent.parent
BETA = "beta-tester-passphrase"
OTHER = "other-household-passphrase"


# ---------- helpers ----------

def _sign_in(client, password):
    res = client.post("/login", data={"password": password, "next": "/"}, follow_redirects=False)
    assert res.status_code == 303, "sign-in should redirect on success"
    return client.cookies[security.COOKIE_NAME]


def _adult(name: str, household: int, age_group: str = "adult") -> int:
    with tools.use_household(household):
        member_id = tools.add_member(name)["member_id"]
        tools.set_member_age_group(name, age_group)
    return member_id


def _pick(client, member_id: int):
    res = client.post("/api/whoami/pick", json={"member_id": member_id})
    assert res.status_code == 200, res.text


def _counts(household: int) -> dict[str, int]:
    conn = get_conn()
    try:
        out = {
            t: conn.execute(f"SELECT COUNT(*) FROM {t} WHERE household_id = ?", (household,)).fetchone()[0]
            for t in household_deletion.household_tables(conn)
        }
        out["households"] = conn.execute(
            "SELECT COUNT(*) FROM households WHERE id = ?", (household,)
        ).fetchone()[0]
    finally:
        conn.close()
    return out


def _schema_tables() -> set[str]:
    return set(re.findall(r"CREATE TABLE IF NOT EXISTS\s+(\w+)", Path(SCHEMA_PATH).read_text()))


# A value for a NOT NULL column with no default, by declared type. The one
# CHECK constraint in the schema gets its own.
_OVERRIDES = {("member_recipe_feedback", "rating"): "liked"}


def _seed_every_table(household: int) -> dict[str, int]:
    """
    One row in every household table for this household, with every
    foreign key to another household table pointing at THIS household's row
    in it — so the delete has real references to cut through. Returns
    {table: rowid}.
    """
    conn = get_conn()
    try:
        tables = household_deletion.household_tables(conn)
        fks = {
            t: {r[3]: r[2] for r in conn.execute(f"PRAGMA foreign_key_list({t})")}
            for t in tables
        }
        done: dict[str, int] = {}
        remaining = list(tables)
        # Parents first. A NOT NULL reference waits for its parent; a
        # nullable one is filled only if the parent is already in.
        while remaining:
            progressed = False
            for t in list(remaining):
                cols = list(conn.execute(f"PRAGMA table_info({t})"))
                needed = [
                    parent for col, parent in fks[t].items()
                    if parent not in ("households", t) and parent in tables
                    and any(c[1] == col and c[3] for c in cols)
                ]
                if any(p not in done for p in needed):
                    continue
                existing = conn.execute(
                    f"SELECT rowid FROM {t} WHERE household_id = ? LIMIT 1", (household,)
                ).fetchone()
                if existing is not None:   # e.g. the passphrase create_household stored
                    done[t] = existing[0]
                    remaining.remove(t)
                    progressed = True
                    continue
                names, values = [], []
                for cid, name, ctype, notnull, default, pk in cols:
                    if pk and ctype.upper() == "INTEGER" and name != "household_id":
                        continue
                    if name == "household_id":
                        value = household
                    elif name in fks[t] and fks[t][name] == "households":
                        value = household
                    elif name in fks[t] and fks[t][name] in done:
                        value = done[fks[t][name]]
                    elif (t, name) in _OVERRIDES:
                        value = _OVERRIDES[(t, name)]
                    elif notnull and default is None:
                        kind = (ctype or "").upper()
                        value = 1 if "INT" in kind else (1.0 if "REAL" in kind else f"{t}-{name}-{household}")
                    else:
                        continue
                    names.append(name)
                    values.append(value)
                cur = conn.execute(
                    f"INSERT INTO {t} ({', '.join(names)}) VALUES ({', '.join('?' for _ in names)})",
                    values,
                )
                done[t] = cur.lastrowid
                remaining.remove(t)
                progressed = True
            assert progressed, f"could not seed (a NOT NULL reference cycle?): {remaining}"
        conn.commit()
    finally:
        conn.close()
    return done


@pytest.fixture
def beta_household():
    return households.create_household("The Beta Testers", BETA)


@pytest.fixture
def other_household():
    return households.create_household("The Others", OTHER)


# ---------- which tables count ----------

def test_every_table_in_the_schema_is_household_scoped():
    """
    A table added to schema.sql without a household_id would be invisible to
    the delete — so it fails here, and whoever adds it decides.
    """
    conn = get_conn()
    try:
        scoped = set(household_deletion.household_tables(conn))
        live = {
            r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
            )
        }
    finally:
        conn.close()
    assert _schema_tables() - {"households"} == scoped
    # Nothing in the live database outside schema.sql, either (a table made
    # by a migration alone would dodge the line above).
    assert live - {"households"} == scoped


def test_every_member_column_is_accounted_for_when_someone_leaves():
    """Every column naming a member is handled by id or by name — none is missed."""
    conn = get_conn()
    try:
        by_id = {(t, c) for t, c, _nn, _j in household_deletion._member_columns(conn)}
        every = set()
        for t in household_deletion.household_tables(conn) + ["households"]:
            if t == "members":
                continue
            fk = {r[3] for r in conn.execute(f"PRAGMA foreign_key_list({t})") if r[2] == "members"}
            for col in conn.execute(f"PRAGMA table_info({t})"):
                if "member" in col[1] or col[1] in fk:
                    every.add((t, col[1]))
    finally:
        conn.close()
    assert every == by_id | household_deletion._NAME_COLUMNS


# ---------- deleting a household ----------

def test_delete_removes_every_row_of_that_household_and_nothing_else(beta_household, other_household):
    for hid in (DEFAULT_HOUSEHOLD_ID, beta_household, other_household):
        _seed_every_table(hid)
    before_one = _counts(DEFAULT_HOUSEHOLD_ID)
    before_other = _counts(other_household)
    before_beta = _counts(beta_household)
    # The seed really did reach every table, or this test proves nothing.
    assert all(n == 1 for n in before_beta.values()), before_beta

    household_deletion.delete_household(beta_household)

    assert all(n == 0 for n in _counts(beta_household).values()), _counts(beta_household)
    assert _counts(other_household) == before_other
    assert _counts(DEFAULT_HOUSEHOLD_ID) == before_one
    conn = get_conn()
    try:
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
    finally:
        conn.close()


def test_household_one_can_never_be_deleted():
    _adult("Emily", DEFAULT_HOUSEHOLD_ID)
    before = _counts(DEFAULT_HOUSEHOLD_ID)
    with pytest.raises(household_deletion.ProtectedHousehold):
        household_deletion.delete_household(DEFAULT_HOUSEHOLD_ID)
    with pytest.raises(household_deletion.ProtectedHousehold):
        household_deletion.delete_household("1")
    assert _counts(DEFAULT_HOUSEHOLD_ID) == before


def test_household_one_refuses_over_http(client):
    emily = _adult("Emily", DEFAULT_HOUSEHOLD_ID)
    _sign_in(client, "test-password")
    _pick(client, emily)
    state = client.get("/api/household/leave").json()
    assert state["protected"] is True
    assert state["can_delete"] is False
    res = client.post("/api/household/delete", json={"confirm": "DELETE"})
    assert res.status_code == 403
    assert households.household_exists(DEFAULT_HOUSEHOLD_ID)
    assert client.get("/api/whoami").status_code == 200


def test_a_half_failed_delete_leaves_everything_as_it_was(beta_household, monkeypatch):
    _seed_every_table(beta_household)
    before = _counts(beta_household)
    real = household_deletion._delete_rows
    seen = []

    def flaky(conn, table, hid):
        seen.append(table)
        if len(seen) == 10:   # well into the run: earlier tables already emptied
            raise RuntimeError("disk went away")
        return real(conn, table, hid)

    monkeypatch.setattr(household_deletion, "_delete_rows", flaky)
    with pytest.raises(RuntimeError):
        household_deletion.delete_household(beta_household)
    assert len(seen) == 10
    assert _counts(beta_household) == before


def test_a_half_failed_delete_over_http_says_nothing_was_deleted(client, beta_household, monkeypatch):
    julia = _adult("Julia", beta_household)
    _sign_in(client, BETA)
    before = _counts(beta_household)

    def broken(conn, table, hid):
        if table == "members":
            raise RuntimeError("boom")
        return conn.execute(f"DELETE FROM {table} WHERE household_id = ?", (hid,)).rowcount

    monkeypatch.setattr(household_deletion, "_delete_rows", broken)
    res = client.post("/api/household/delete", json={"confirm": "DELETE"})
    assert res.status_code == 500
    # The app's own 500 wording (record_server_errors), calm and true here.
    assert "your data is fine" in res.json()["detail"]
    after = _counts(beta_household)
    # The failure itself is written down against the household, as every
    # 500 is — that one new row is the only difference.
    assert after.pop("error_events") == before.pop("error_events") + 1
    assert after == before
    # Still signed in: a failed delete doesn't clear the cookie.
    assert client.get("/api/whoami").json()["member"]["id"] == julia


def test_delete_over_http_signs_out_and_goes_to_goodbye(client, beta_household):
    julia = _adult("Julia", beta_household)
    _sign_in(client, BETA)
    with tools.use_household(beta_household):
        share_token = tools.get_or_create_share_link()["token"]
        invite_token = invites.mint_invite(beta_household, _adult("Sam", beta_household), invited_by=julia)
    _pick(client, julia)

    # A second phone signed in to the same household.
    other_phone = TestClient(app)
    _sign_in(other_phone, BETA)
    assert other_phone.get("/api/whoami").status_code == 200

    # Without the word, nothing happens.
    assert client.post("/api/household/delete", json={}).status_code == 400
    assert client.post("/api/household/delete", json={"confirm": "yes"}).status_code == 400
    assert households.household_exists(beta_household)

    res = client.post("/api/household/delete", json={"confirm": " delete "})
    assert res.status_code == 200, res.text
    assert res.json() == {"deleted": True, "goodbye": "/goodbye"}
    assert security.COOKIE_NAME not in client.cookies
    assert not households.household_exists(beta_household)
    assert all(n == 0 for n in _counts(beta_household).values())

    # Signed out everywhere: this device, and the other phone on its next request.
    assert client.get("/api/whoami").status_code == 401
    assert other_phone.get("/api/whoami").status_code == 401
    # The passphrase opens nothing, the invite link has run out, the share link is gone.
    login = client.post("/login", data={"password": BETA, "next": "/"}, follow_redirects=False)
    assert login.status_code != 303
    assert households.authenticate(BETA) is None
    assert TestClient(app).post("/api/join", json={"token": invite_token}).status_code == 410
    assert TestClient(app).get(f"/api/share/{share_token}").status_code == 404


def test_the_goodbye_page_is_public():
    res = TestClient(app).get("/goodbye", headers={"accept": "text/html"}, follow_redirects=False)
    assert res.status_code == 200
    assert "been deleted" in res.text.replace("&rsquo;", "'")
    assert "left the household" in res.text.replace("&rsquo;", "'")


def test_delete_only_ever_touches_the_signed_in_household(client, beta_household, other_household):
    """No household id in the body is ever read — the cookie decides."""
    _adult("Julia", beta_household)
    _seed_every_table(other_household)
    before_other = _counts(other_household)
    _sign_in(client, BETA)
    res = client.post(
        "/api/household/delete",
        json={"confirm": "DELETE", "household_id": other_household, "household": DEFAULT_HOUSEHOLD_ID},
    )
    assert res.status_code == 200
    assert not households.household_exists(beta_household)
    assert _counts(other_household) == before_other
    assert households.household_exists(DEFAULT_HOUSEHOLD_ID)


def test_a_deleted_households_id_is_never_handed_out_again(client, beta_household):
    """An old cookie must not open whichever household is created next."""
    _adult("Julia", beta_household)
    old_cookie = _sign_in(client, BETA)
    assert client.post("/api/household/delete", json={"confirm": "DELETE"}).status_code == 200
    newer = households.create_household("Next in line", "a-brand-new-passphrase")
    assert newer != beta_household
    stale = TestClient(app)
    stale.cookies.set(security.COOKIE_NAME, old_cookie)
    assert stale.get("/api/whoami").status_code == 401


def test_a_device_that_hasnt_said_who_it_is_cant_delete(client, beta_household):
    _adult("Julia", beta_household)
    _adult("Sam", beta_household)
    _sign_in(client, BETA)
    state = client.get("/api/household/leave").json()
    assert state["needs_pick"] is True and state["can_delete"] is False
    assert client.post("/api/household/delete", json={"confirm": "DELETE"}).status_code == 400
    assert households.household_exists(beta_household)


def test_a_household_with_no_adults_yet_can_still_be_deleted(client, beta_household):
    """Setup abandoned before anyone was entered: the passphrase is enough."""
    _sign_in(client, BETA)
    state = client.get("/api/household/leave").json()
    assert state["can_delete"] is True and state["needs_pick"] is False
    assert client.post("/api/household/delete", json={"confirm": "DELETE"}).status_code == 200
    assert not households.household_exists(beta_household)


def test_delete_clears_photos_and_what_the_server_holds_in_memory(client, beta_household):
    from app import main as app_main
    from app.tools import proposals

    _adult("Julia", beta_household)
    _sign_in(client, BETA)
    folder = recipe_photos._household_dir(beta_household)
    os.makedirs(folder, exist_ok=True)
    Path(folder, "7-1.jpg").write_bytes(b"\xff\xd8\xff photo")
    keep = recipe_photos._household_dir(DEFAULT_HOUSEHOLD_ID)
    os.makedirs(keep, exist_ok=True)
    Path(keep, "3-1.jpg").write_bytes(b"\xff\xd8\xff keep")
    # Chat history is the chat_sessions table since 2026-10-09 (it was two
    # dicts on app.main): stored through the real save, so this goes red if
    # the delete ever stops reaching the rows that save writes.
    with tools.use_household(beta_household):
        app_main._save_chat_session(f"h{beta_household}:abc", [{"role": "user", "content": "hi"}])
    app_main._save_chat_session(f"h{DEFAULT_HOUSEHOLD_ID}:keep", [])
    proposals._PROPOSALS[beta_household] = {"p": {}}
    from app.tools.swap_options import _OPTIONS_CACHE as swap_cache
    swap_cache[(beta_household, 9)] = {"at": 0}
    swap_cache[(DEFAULT_HOUSEHOLD_ID, 9)] = {"at": 0}
    try:
        assert client.post("/api/household/delete", json={"confirm": "DELETE"}).status_code == 200
        assert not os.path.exists(folder)
        assert Path(keep, "3-1.jpg").exists()
        conn = get_conn()
        try:
            keys = {r[0] for r in conn.execute("SELECT session_key FROM chat_sessions")}
        finally:
            conn.close()
        assert f"h{beta_household}:abc" not in keys
        assert f"h{DEFAULT_HOUSEHOLD_ID}:keep" in keys
        assert beta_household not in proposals._PROPOSALS
        assert (beta_household, 9) not in swap_cache
        assert (DEFAULT_HOUSEHOLD_ID, 9) in swap_cache
    finally:
        swap_cache.pop((DEFAULT_HOUSEHOLD_ID, 9), None)
        Path(keep, "3-1.jpg").unlink(missing_ok=True)


def test_the_chat_agent_cannot_reach_any_of_this():
    """Deletion lives outside app/tools/, and no tool mentions it."""
    tools_dir = REPO / "app" / "tools"
    for path in tools_dir.glob("*.py"):
        text = path.read_text(encoding="utf-8")
        assert "household_deletion" not in text, path.name
        assert "delete_household" not in text, path.name


# ---------- removing yourself ----------

def _two_adult_house(client, household):
    julia = _adult("Julia", household)
    sam = _adult("Sam", household)
    kid = _adult("Kid", household, age_group="child")
    _sign_in(client, BETA)
    _pick(client, julia)
    return julia, sam, kid


def test_remove_me_takes_out_only_that_adult(client, beta_household, other_household):
    julia, sam, kid = _two_adult_house(client, beta_household)
    _seed_every_table(other_household)
    before_other = _counts(other_household)
    conn = get_conn()
    conn.execute(
        "INSERT INTO recipes (household_id, name) VALUES (?, 'Soup')", (beta_household,)
    )
    recipe = conn.execute("SELECT id FROM recipes WHERE household_id = ?", (beta_household,)).fetchone()[0]
    conn.execute(
        "INSERT INTO member_recipe_feedback (household_id, recipe_id, member_id, rating) VALUES (?, ?, ?, 'liked')",
        (beta_household, recipe, julia),
    )
    conn.execute(
        "INSERT INTO member_recipe_feedback (household_id, recipe_id, member_id, rating) VALUES (?, ?, ?, 'liked')",
        (beta_household, recipe, sam),
    )
    conn.execute(
        "INSERT INTO member_notes (household_id, member_id, note) VALUES (?, ?, 'no mushrooms')",
        (beta_household, julia),
    )
    conn.execute(
        "INSERT INTO slot_attendance (household_id, date, slot, absent_member_ids_json) VALUES (?, '2026-09-28', 'dinner', ?)",
        (beta_household, json.dumps([julia, sam])),
    )
    conn.execute(
        "INSERT INTO household_rhythm (household_id, member_name, weekday, fact_type) VALUES (?, 'Julia', 0, 'away')",
        (beta_household,),
    )
    conn.execute(
        "INSERT INTO household_rhythm (household_id, member_name, weekday, fact_type) VALUES (?, 'Sam', 0, 'away')",
        (beta_household,),
    )
    conn.execute("UPDATE households SET set_up_by_member_id = ? WHERE id = ?", (julia, beta_household))
    conn.commit()
    conn.close()
    invites.mint_invite(beta_household, julia, invited_by=sam)

    state = client.get("/api/household/leave").json()
    assert state["can_remove_self"] is True
    assert state["you"] == "Julia" and state["other_adults"] == ["Sam"]

    res = client.post("/api/household/remove-me", json={})
    assert res.status_code == 200, res.text
    assert res.json()["goodbye"] == "/goodbye?left=1"
    assert security.COOKIE_NAME not in client.cookies

    conn = get_conn()
    try:
        members = {r[0] for r in conn.execute("SELECT id FROM members WHERE household_id = ?", (beta_household,))}
        assert members == {sam, kid}
        assert conn.execute("SELECT member_id FROM member_recipe_feedback WHERE household_id = ?", (beta_household,)).fetchall()[0][0] == sam
        assert conn.execute("SELECT COUNT(*) FROM member_recipe_feedback WHERE household_id = ?", (beta_household,)).fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM member_notes WHERE member_id = ?", (julia,)).fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM household_invites WHERE member_id = ?", (julia,)).fetchone()[0] == 0
        absent = conn.execute("SELECT absent_member_ids_json FROM slot_attendance WHERE household_id = ?", (beta_household,)).fetchone()[0]
        assert json.loads(absent) == [sam]
        rhythm = [r[0] for r in conn.execute("SELECT member_name FROM household_rhythm WHERE household_id = ?", (beta_household,))]
        assert rhythm == ["Sam"]
        assert conn.execute("SELECT set_up_by_member_id FROM households WHERE id = ?", (beta_household,)).fetchone()[0] is None
        assert conn.execute("SELECT COUNT(*) FROM recipes WHERE household_id = ?", (beta_household,)).fetchone()[0] == 1
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
    finally:
        conn.close()
    assert households.household_exists(beta_household)
    assert _counts(other_household) == before_other


def test_a_trip_that_was_only_theirs_goes_rather_than_becoming_everyones(client, beta_household):
    """An empty traveller list means the WHOLE household is away (schema.sql),
    so stripping the leaver out of a solo trip would cancel everyone's meals."""
    julia, sam, _kid = _two_adult_house(client, beta_household)
    conn = get_conn()
    solo = conn.execute(
        "INSERT INTO away_stretches (household_id, from_date, from_slot, to_date, to_slot, member_ids_json) "
        "VALUES (?, '2026-10-03', 'lunch', '2026-10-04', 'dinner', ?)",
        (beta_household, json.dumps([julia])),
    ).lastrowid
    shared = conn.execute(
        "INSERT INTO away_stretches (household_id, from_date, from_slot, to_date, to_slot, member_ids_json) "
        "VALUES (?, '2026-10-10', 'lunch', '2026-10-11', 'dinner', ?)",
        (beta_household, json.dumps([julia, sam])),
    ).lastrowid
    conn.execute(
        "INSERT INTO slot_needs (household_id, date, slot, need, away_stretch_id, for_member_ids_json) "
        "VALUES (?, '2026-10-03', 'breakfast', 'quick', ?, ?)",
        (beta_household, solo, json.dumps([julia])),
    )
    conn.execute(
        "INSERT INTO slot_needs (household_id, date, slot, need, for_member_ids_json) "
        "VALUES (?, '2026-10-06', 'dinner', 'ready_made', ?)",
        (beta_household, json.dumps([julia])),
    )
    conn.execute(
        "INSERT INTO slot_needs (household_id, date, slot, need, for_member_ids_json) "
        "VALUES (?, '2026-10-07', 'dinner', 'quick', ?)",
        (beta_household, json.dumps([julia, sam])),
    )
    conn.execute(
        "INSERT INTO slot_attendance (household_id, date, slot, absent_member_ids_json, away_stretch_id) "
        "VALUES (?, '2026-10-03', 'dinner', ?, ?)",
        (beta_household, json.dumps([julia]), solo),
    )
    conn.commit()
    conn.close()

    assert client.post("/api/household/remove-me", json={}).status_code == 200

    conn = get_conn()
    try:
        trips = {r[0]: json.loads(r[1]) for r in conn.execute(
            "SELECT id, member_ids_json FROM away_stretches WHERE household_id = ?", (beta_household,))}
        assert trips == {shared: [sam]}
        needs = {r[0]: json.loads(r[1]) for r in conn.execute(
            "SELECT date, for_member_ids_json FROM slot_needs WHERE household_id = ?", (beta_household,))}
        assert needs == {"2026-10-07": [sam]}
        # No list anywhere was left reading "everyone" where it meant Julia.
        att = conn.execute(
            "SELECT absent_member_ids_json, away_stretch_id FROM slot_attendance WHERE household_id = ?",
            (beta_household,),
        ).fetchone()
        assert json.loads(att[0]) == [] and att[1] is None
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
    finally:
        conn.close()


def test_the_last_adult_cant_remove_themselves(client, beta_household):
    julia = _adult("Julia", beta_household)
    _adult("Kid", beta_household, age_group="child")
    _sign_in(client, BETA)
    _pick(client, julia)
    state = client.get("/api/household/leave").json()
    assert state["can_remove_self"] is False and state["can_delete"] is True
    res = client.post("/api/household/remove-me", json={})
    assert res.status_code == 400
    assert "Delete the household instead" in res.json()["detail"]
    with tools.use_household(beta_household):
        assert [a["id"] for a in tools.household_adults()] == [julia]


def test_remove_me_needs_to_know_who_you_are(client, beta_household):
    _adult("Julia", beta_household)
    _adult("Sam", beta_household)
    _sign_in(client, BETA)
    assert client.post("/api/household/remove-me", json={}).status_code == 400
    with tools.use_household(beta_household):
        assert len(tools.household_adults()) == 2


def test_remove_me_is_refused_in_household_one(client):
    emily = _adult("Emily", DEFAULT_HOUSEHOLD_ID)
    _adult("Vineeth", DEFAULT_HOUSEHOLD_ID)
    _sign_in(client, "test-password")
    _pick(client, emily)
    assert client.post("/api/household/remove-me", json={}).status_code == 403
    with pytest.raises(household_deletion.ProtectedHousehold):
        household_deletion.remove_member(DEFAULT_HOUSEHOLD_ID, emily)
    with tools.use_household(DEFAULT_HOUSEHOLD_ID):
        assert len(tools.household_adults()) == 2


def test_remove_member_refuses_someone_from_another_household(beta_household, other_household):
    _adult("Julia", beta_household)
    _adult("Sam", beta_household)
    stranger = _adult("Stranger", other_household)
    _adult("Friend", other_household)
    with pytest.raises(household_deletion.DeletionRefused):
        household_deletion.remove_member(beta_household, stranger)
    with tools.use_household(other_household):
        assert stranger in [a["id"] for a in tools.household_adults()]


# ---------- the entry point ----------

def test_preferences_ends_with_delete_your_household():
    js = (REPO / "static" / "shell.js").read_text(encoding="utf-8")
    start = js.index("function renderPrefsRows()")
    body = js[start:js.index("\n  }\n", start)]
    assert 'data-prefs="leave"' in body
    assert body.index('data-prefs="signout"') < body.index('data-prefs="leave"')
    assert "Delete your household</span></button>" in body
    html = (REPO / "static" / "shell.html").read_text(encoding="utf-8")
    assert 'id="leave-dialog"' in html and 'data-motion="dialog"' in html
    css = (REPO / "static" / "shell.css").read_text(encoding="utf-8")
    # The five ID lists that make a dialog a dialog (see shell.css's note).
    assert css.count("#leave-scrim") >= 4 and css.count("#leave-dialog") >= 2


def test_backup_retention_is_written_down():
    from app import backup

    assert backup.RETENTION_DAYS == 14
    assert "15 days" in household_deletion.BACKUP_RETENTION_NOTE
    assert "15 days" in (REPO / "static" / "goodbye.html").read_text(encoding="utf-8")
