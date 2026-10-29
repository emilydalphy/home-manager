"""
A wipe that keeps the household row but deletes its members must not leave
that row naming people who are gone (defect hunt 2026-10-29).

create_demo_household.py (every run after the first) and reset_household.py
both delete every member and keep `households`. They used to leave
`set_up_by_member_id` (and the other `*_member_id` columns) pointing at the
old ids. The setter-up is only ever claimed while that column is NULL, so
after an App Store reviewer had used the demo household once, the next
reset greeted BOTH adults with "the other adult's" welcome, with nobody
named as having set it up — and nobody could ever be recorded again.
"""
import pytest
from fastapi.testclient import TestClient

import create_demo_household as demo
import reset_household
from app import households
from app.db import get_conn
from app.main import app

PASSPHRASE = "demo-review-passphrase-123"


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv(demo.DEMO_PASSPHRASE_ENV, PASSPHRASE)
    monkeypatch.setenv("HOME_MANAGER_PASSWORD", "household-one-passphrase")


def _sign_in_as(name: str) -> dict:
    client = TestClient(app)
    res = client.post("/login", data={"password": PASSPHRASE, "next": "/"}, follow_redirects=False)
    assert res.status_code == 303, res.text
    adults = client.get("/api/whoami").json()["adults"]
    mid = next(a["id"] for a in adults if a["name"] == name)
    res = client.post("/api/whoami/pick", json={"member_id": mid})
    assert res.status_code == 200, res.text
    return res.json()


def _member_pointers(hid: int) -> dict:
    conn = get_conn()
    try:
        cols = [r[1] for r in conn.execute("PRAGMA table_info(households)") if r[1].endswith("_member_id")]
        row = conn.execute(f"SELECT {', '.join(cols)} FROM households WHERE id = ?", (hid,)).fetchone()
        return dict(zip(cols, tuple(row)))
    finally:
        conn.close()


def _dangling(hid: int) -> dict:
    conn = get_conn()
    try:
        out = {}
        for col, mid in _member_pointers(hid).items():
            if mid is not None and conn.execute(
                "SELECT 1 FROM members WHERE id = ? AND household_id = ?", (mid, hid)
            ).fetchone() is None:
                out[col] = mid
        return out
    finally:
        conn.close()


def test_a_reset_demo_household_greets_its_first_adult_as_the_one_who_set_it_up():
    demo.main()
    assert _sign_in_as("Maya")["first_open"] is False  # first to open = set it up
    demo.main()  # the reset between reviewers
    hid = demo._find_demo_household()["id"]
    assert _dangling(hid) == {}

    maya = _sign_in_as("Maya")
    assert maya["first_open"] is False and maya["set_up_by"] == "Maya"
    theo = _sign_in_as("Theo")
    assert theo["first_open"] is True and theo["set_up_by"] == "Maya"


def test_reset_household_clears_the_pointers_and_leaves_other_households_alone(monkeypatch):
    other = households.create_household("Next Door", "next-door-passphrase")
    target = households.create_household("To Be Reset", "to-be-reset-passphrase")
    conn = get_conn()
    for hid in (other, target):
        mid = conn.execute(
            "INSERT INTO members (household_id, name, age_group) VALUES (?, 'Ada', 'adult')", (hid,)
        ).lastrowid
        conn.execute(
            "UPDATE households SET set_up_by_member_id = ?, primary_member_id = ? WHERE id = ?",
            (mid, mid, hid),
        )
    conn.commit()
    conn.close()
    before_other = _member_pointers(other)

    monkeypatch.setattr("sys.argv", ["reset_household.py", "--household", str(target)])
    monkeypatch.setattr("builtins.input", lambda *_: "RESET")
    reset_household.main()

    assert set(_member_pointers(target).values()) == {None}
    assert _member_pointers(other) == before_other
    assert _dangling(other) == {}
    # Still signs in: a reset empties a household, it doesn't remove it.
    assert households.authenticate("to-be-reset-passphrase") == target
