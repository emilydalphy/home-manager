"""
A public route binds its household from its token — never by falling back to
household 1 (defect hunt, 2026-10-31).

`household_id()` reads a ContextVar whose DEFAULT is household 1, so scripts,
seeds and tests work unbound. On a public route that default is the trap: no
cookie binds anything, and a route that forgets `use_household(<the token's
household>)` reads and writes Emily's household while looking perfectly
healthy. `get_shared_weekly_plan` once did exactly that (CLAUDE.md).

So these tests take the default AWAY while the public requests run: the
ContextVar is swapped for one with no default, and any household_id() call
not inside a binding raises LookupError, which the route turns into a 500.
Every public route is driven with household 2's real tokens and no cookie,
and must answer without a 500 and land only in household 2.

The last two tests close two blind spots of tests/test_isolation_attack.py,
which fills every id field of a body with the SAME id and every dict with {}:
a request mixing the caller's own id with the other household's, and an Undo
token built by hand to name the other household's rows.
"""
from __future__ import annotations

import contextvars
import datetime
import re

import pytest
from fastapi.testclient import TestClient

from conftest import household_today

from app import account_email, households, invites, ratelimit, tools
from app.db import get_conn
from app.main import app
from app.tools import _shared

B_PASSPHRASE = "public-routes-b-passphrase"


def _week_start() -> str:
    today = household_today()
    return (today - datetime.timedelta(days=today.weekday())).isoformat()


@pytest.fixture
def no_default_household(monkeypatch):
    """From here on, an unbound household_id() raises instead of reading 1.
    Seed BEFORE asking for this; the middleware and use_household bind the
    replacement as they would the real one (both read the module global)."""
    def arm():
        monkeypatch.setattr(_shared, "_current_household_id", contextvars.ContextVar("strict_household"))
    return arm


@pytest.fixture
def outbox(monkeypatch):
    """Email sign-in switched on, delivery captured (as test_email_signup)."""
    for k, v in {"SMTP_HOST": "smtp.test", "SMTP_USER": "pomona@test", "SMTP_PASSWORD": "x"}.items():
        monkeypatch.setenv(k, v)
    sent: list = []
    monkeypatch.setattr(account_email, "_deliver", lambda msg: sent.append(msg))
    return sent


def _seed_b() -> dict:
    """Household 2 with an approved week, a child with a self-service link,
    and an invited adult. Household 1 gets a namesake child, so a write that
    fell through to household 1 would have somewhere to land."""
    tools.add_member("Kid")
    tools.set_member_dietary_restrictions("Kid", ["peanuts"])
    b = households.create_household("Beta Public", B_PASSPHRASE)
    with tools.use_household(b):
        tools.add_member("Sam")
        tools.set_member_age_group("Sam", "adult")
        tools.add_member("Kid")
        tools.set_member_dietary_restrictions("Kid", ["dairy"])
        tools.add_recipe("Beta Soup", ingredients=[{"item": "lentils", "qty": "1 cup"}])
        plan = tools.create_weekly_plan(_week_start())
        plan_id = plan.get("weekly_plan_id") or plan.get("id")
        tools.plan_meal(household_today().isoformat(), "Beta Soup", slot="dinner", weekly_plan_id=plan_id)
        conn = get_conn()
        conn.execute("UPDATE weekly_plans SET status = 'approved' WHERE id = ? AND household_id = ?", (plan_id, b))
        conn.commit()
        conn.close()
        share = tools.get_or_create_share_link()["token"]
        member_token = tools.get_or_create_member_share_link("Kid")["token"]
    robin = invites.add_adult(b, "Robin")
    invite = invites.mint_invite(b, robin)
    return {"b": b, "share": share, "member": member_token, "invite": invite}


def _restrictions() -> dict:
    conn = get_conn()
    rows = conn.execute("SELECT household_id, dietary_restrictions_json FROM members WHERE name = 'Kid'").fetchall()
    conn.close()
    return {r["household_id"]: r["dietary_restrictions_json"] for r in rows}


def _ok(res):
    assert res.status_code < 500, f"{res.request.method} {res.request.url} -> {res.status_code}: {res.text[:300]}"
    return res


def test_the_share_links_and_invite_bind_the_tokens_household(no_default_household):
    s = _seed_b()
    no_default_household()
    client = TestClient(app, raise_server_exceptions=False)

    plan = _ok(client.get(f"/api/share/{s['share']}"))
    assert plan.status_code == 200
    assert "Beta Soup" in plan.text and plan.json()["household_name"] == "Beta Public"
    _ok(client.get(f"/share/{s['share']}"))
    assert _ok(client.get("/api/share/not-a-token")).status_code == 404

    view = _ok(client.get(f"/api/member-share/{s['member']}"))
    assert view.json()["dietary_restrictions"] == ["dairy"]
    _ok(client.get(f"/member-share/{s['member']}"))
    ratelimit.reset()
    added = _ok(client.post(f"/api/member-share/{s['member']}/restriction", json={"restriction": "sesame"}))
    assert added.status_code == 200 and added.json()["dietary_restrictions"] == ["dairy", "sesame"]
    ratelimit.reset()
    noted = _ok(client.post(f"/api/member-share/{s['member']}/note", json={"note": "no mushrooms"}))
    assert noted.status_code == 200 and [n["note"] for n in noted.json()["notes"]] == ["no mushrooms"]
    # Household 1's Kid, the namesake, is untouched; the note is filed under 2.
    assert _restrictions() == {1: '["peanuts"]', s["b"]: '["dairy", "sesame"]'}
    conn = get_conn()
    notes = [r["household_id"] for r in conn.execute("SELECT household_id FROM member_notes")]
    conn.close()
    assert notes == [s["b"]]

    _ok(client.get("/join"))
    ratelimit.reset()
    assert _ok(client.post("/api/join", json={"token": "nope"})).status_code == 410
    ratelimit.reset()
    assert _ok(client.post("/api/join", json={"token": s["invite"]})).status_code == 200
    who = client.get("/api/whoami").json()
    assert who["household_id"] == s["b"] and who["member"]["name"] == "Robin"


def test_email_sign_up_and_sign_in_bind_their_own_household(no_default_household, outbox):
    no_default_household()

    def code():
        return re.search(r"\b(\d{6})\b", outbox[-1].get_content()).group(1)

    def verify(client):
        ratelimit.reset()
        return _ok(client.post("/api/auth/email/verify",
                               json={"email": "new@example.com", "code": code(), "agreed": True, "next": "/"}))

    first = TestClient(app, raise_server_exceptions=False)
    assert _ok(first.post("/api/auth/email/start", json={"email": "new@example.com"})).status_code == 200
    res = verify(first)
    assert res.status_code == 200 and res.json()["new_household"] is True
    _ok(first.post("/api/onboarding/household", json={
        "members": [{"name": "Rowan", "age_group": "Adult"}], "pets": [], "goals": "", "primary_name": "Rowan"}))
    household = first.get("/api/whoami").json()["household_id"]
    assert household != 1

    phone = TestClient(app, raise_server_exceptions=False)
    ratelimit.reset()
    _ok(phone.post("/api/auth/email/start", json={"email": "new@example.com"}))
    res = verify(phone)
    assert res.status_code == 200 and res.json()["new_household"] is False
    who = phone.get("/api/whoami").json()
    assert who["household_id"] == household and who["member"]["name"] == "Rowan"
    conn = get_conn()
    assert conn.execute("SELECT COUNT(*) AS n FROM members WHERE household_id = 1").fetchone()["n"] == 0
    conn.close()


# ---------- the attack harness's two blind spots ----------

def _signed_in_b() -> tuple[int, TestClient]:
    b = households.create_household("Beta Attackers", B_PASSPHRASE)
    client = TestClient(app, raise_server_exceptions=False)
    res = client.post("/login", data={"password": B_PASSPHRASE, "next": "/"}, follow_redirects=False)
    assert res.status_code == 303
    return b, client


def _entry_recipe(entry_id: int):
    conn = get_conn()
    row = conn.execute("SELECT household_id, recipe_id FROM meal_plan_entries WHERE id = ?", (entry_id,)).fetchone()
    conn.close()
    return tuple(row)


def test_my_meal_cannot_be_pointed_at_the_other_households_recipe():
    a_recipe = tools.add_recipe("Zq Family Stew", ingredients=[{"item": "beef", "qty": "1 lb"}],
                                notes="Zq private note")["recipe_id"]
    b, client = _signed_in_b()
    with tools.use_household(b):
        tools.add_recipe("Beta Toast", ingredients=[{"item": "bread", "qty": "2 slices"}])
        plan = tools.create_weekly_plan(_week_start())
        plan_id = plan.get("weekly_plan_id") or plan.get("id")
        entry = tools.plan_meal(household_today().isoformat(), "Beta Toast", slot="dinner", weekly_plan_id=plan_id)
        entry_id = entry.get("entry_id") or entry.get("id")
    before = _entry_recipe(entry_id)

    res = client.post("/api/meal-recipe", json={"entry_id": entry_id, "recipe_id": a_recipe})
    assert res.status_code < 500
    assert res.status_code >= 400 or res.json().get("status") == "refused"
    assert "Zq" not in res.text
    assert _entry_recipe(entry_id) == before
    assert "Zq" not in client.get("/api/week-menu").text


def test_a_hand_built_undo_cannot_bring_back_or_move_the_other_households_food():
    tools.update_inventory("Zq milk", "add", quantity="1", location="fridge")
    tools.update_inventory("Zq peas", "add", quantity="1 bag", location="fridge")
    conn = get_conn()
    ids = {r["item"]: r["id"] for r in conn.execute("SELECT id, item FROM inventory_items WHERE household_id = 1")}
    conn.close()
    gone = tools.used_up_item(ids["Zq milk"])["undo"]
    tools.freeze_item(ids["Zq peas"])
    b, client = _signed_in_b()

    res = client.post("/api/whats-home/undo", json={"undo": gone})
    assert res.status_code == 400
    res = client.post("/api/whats-home/undo", json={"undo": {
        "kind": "moved", "item_id": ids["Zq peas"], "location": "pantry"}})
    assert res.status_code == 200 and res.json()["undone"] is False
    res = client.post("/api/inventory/unfreeze", json={"undo": {"item_id": ids["Zq peas"]}})
    assert res.status_code == 200 and res.json()["undone"] is False

    conn = get_conn()
    assert conn.execute("SELECT COUNT(*) AS n FROM inventory_items WHERE household_id = ?", (b,)).fetchone()["n"] == 0
    assert conn.execute("SELECT 1 FROM inventory_items WHERE id = ?", (ids["Zq milk"],)).fetchone() is None
    removal = conn.execute("SELECT restored_at FROM inventory_removals WHERE id = ?", (gone["removal_id"],)).fetchone()
    peas = conn.execute("SELECT location FROM inventory_items WHERE id = ?", (ids["Zq peas"],)).fetchone()
    conn.close()
    assert removal["restored_at"] is None
    assert (peas["location"] or "").lower() == "freezer"
    # And household 1's own Undo still works: the record was left for it.
    assert tools.restore_kitchen_item(gone)["undone"] is True
