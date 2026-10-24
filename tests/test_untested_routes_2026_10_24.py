"""
Closing test gaps, 2026-10-24 (standing work, second pass after
test_untested_routes_2026_10_22.py on its own branch).

How these were picked. Two passes over app.routes:

  1. Every @app route matched, method and path shape, against the client
     calls in tests/. GET /api/needs-you was the one household-scoped route
     no test drove over HTTP (the band is built in tests through
     tools.get_needs_you_items, never through the route Today calls).
  2. test_isolation_attack.py throws A's ids at every route, but builds
     bodies from types alone, so a route whose body needs a real date, slot
     or choice answers 400 before its own lookup and the round proves
     nothing about it. Instrumenting that test listed the routes that only
     ever answered 400/422. The ones below take another household's id or
     name in a VALID body and had no cross-household test: settle a slot by
     plan id, put an adult on a move, move the main person, set a person's
     age group, and edit/delete a What-we-know preference.

Each gets: it works for the signed-in household, and household B, signed
in, sending A's id or name in an otherwise valid body, neither reads nor
changes A's data — and writes nothing new for itself either.
"""
from __future__ import annotations

import datetime

import pytest
from fastapi.testclient import TestClient

from conftest import household_today

from app import households, tools
from app.db import get_conn
from app.main import app
from app.tools._shared import DEFAULT_HOUSEHOLD_ID
from app.tools import moves as _moves

A = DEFAULT_HOUSEHOLD_ID
B_PASSPHRASE = "untested-routes-1024-b-passphrase"
A_REASON = "Zqs deciding nearer the time for household A."


def _sign_in(client, password):
    res = client.post("/login", data={"password": password, "next": "/"}, follow_redirects=False)
    assert res.status_code == 303
    return client


@pytest.fixture
def two():
    """(a, b, b_id): two signed-in clients on two households, own cookie jars."""
    b_id = households.create_household("Untested Routes 1024 B", B_PASSPHRASE)
    with TestClient(app) as a, TestClient(app) as b:
        _sign_in(a, "test-password")
        _sign_in(b, B_PASSPHRASE)
        yield a, b, b_id


def _today() -> str:
    return household_today().isoformat()


def _week_start() -> str:
    t = household_today()
    return (t - datetime.timedelta(days=t.weekday())).isoformat()


def _adults(*names):
    for n in names:
        tools.add_member(n)
        tools.set_member_age_group(n, "adult")


def _member_ids() -> dict[str, int]:
    return {m["name"]: m["id"] for m in tools.list_members()}


def _approved_week_with_open_tonight(reason: str = A_REASON) -> int:
    """The current household's approved week with tonight's dinner handed
    back as an open decision — the shape Now's dinner card is about."""
    tools.add_recipe("Lentil Soup", ingredients=[{"item": "red lentils", "qty": "1 cup"}])
    plan_id = tools.create_weekly_plan(_week_start())["weekly_plan_id"]
    tools.plan_meal(_today(), "Lentil Soup", slot="dinner", weekly_plan_id=plan_id)
    tools.approve_weekly_plan(plan_id, approved_by="Emily")
    tools.clear_plan_slot(plan_id, _today(), "dinner")
    tools.plan_slot_open(plan_id, _today(), "dinner", reason)
    return plan_id


def _tonights_dinner_rows(plan_id: int) -> list[tuple]:
    conn = get_conn()
    rows = conn.execute(
        "SELECT e.household_id, COALESCE(r.name, e.freeform_meal) AS meal, e.slot_state "
        "FROM meal_plan_entries e LEFT JOIN recipes r ON r.id = e.recipe_id "
        "WHERE e.weekly_plan_id = ? AND e.date = ? AND e.slot = 'dinner' ORDER BY e.id",
        (plan_id, _today()),
    ).fetchall()
    conn.close()
    return [tuple(r) for r in rows]


def _count(table: str, hid: int) -> int:
    conn = get_conn()
    n = conn.execute(f"SELECT COUNT(*) AS n FROM {table} WHERE household_id = ?", (hid,)).fetchone()["n"]
    conn.close()
    return n


# ---------- GET /api/needs-you ----------

def test_needs_you_answers_the_signed_in_households_open_dinner(two):
    a, _, _ = two
    plan_id = _approved_week_with_open_tonight()
    res = a.get("/api/needs-you")
    assert res.status_code == 200, res.text
    cards = [i for i in res.json()["items"] if i["type"] == "dinner_open"]
    assert len(cards) == 1
    assert cards[0]["date"] == _today() and cards[0]["weekly_plan_id"] == plan_id
    assert A_REASON in res.text


def test_needs_you_never_shows_another_households_decision(two):
    a, b, b_id = two
    a_plan = _approved_week_with_open_tonight()
    res = b.get("/api/needs-you")
    assert res.status_code == 200, res.text
    assert [i for i in res.json()["items"] if i["type"] == "dinner_open"] == []
    assert "Zqs" not in res.text
    # And B's own open night is B's card, not A's.
    with tools.use_household(b_id):
        b_plan = _approved_week_with_open_tonight("B would rather ask about tonight.")
    cards = [i for i in b.get("/api/needs-you").json()["items"] if i["type"] == "dinner_open"]
    assert [c["weekly_plan_id"] for c in cards] == [b_plan]
    assert [c["weekly_plan_id"] for c in a.get("/api/needs-you").json()["items"]
            if c["type"] == "dinner_open"] == [a_plan]


# ---------- POST /api/week/{week_start}/slot, by plan id ----------

def test_settling_a_slot_with_another_households_plan_id_changes_nothing(two):
    a, b, b_id = two
    a_plan = _approved_week_with_open_tonight()
    before = _tonights_dinner_rows(a_plan)
    assert [(r[0], r[2]) for r in before] == [(A, "open")]
    res = b.post(f"/api/week/{_week_start()}/slot", json={
        "date": _today(), "slot": "dinner", "choice": "Zqn Tacos", "weekly_plan_id": a_plan,
    })
    assert res.status_code == 400, res.text
    assert "Zqs" not in res.text
    assert _tonights_dinner_rows(a_plan) == before
    assert _count("meal_plan_entries", b_id) == 0
    # A, through the same route and body, does settle it.
    res = a.post(f"/api/week/{_week_start()}/slot", json={
        "date": _today(), "slot": "dinner", "choice": "Tacos", "weekly_plan_id": a_plan,
    })
    assert res.status_code == 200, res.text
    assert [(r[1], r[2]) for r in _tonights_dinner_rows(a_plan)] == [("Tacos", "planned")]


# ---------- POST /api/today/moves/{move_id}/owner ----------

def _tonights_cook_move_id() -> str:
    return next(m["id"] for m in _moves.moves_for_day(_today()) if m["kind"] == "cook")


def _overrides(hid: int) -> list[tuple]:
    conn = get_conn()
    rows = conn.execute(
        "SELECT move_id, on_date, member_id FROM move_owner_overrides WHERE household_id = ?", (hid,)
    ).fetchall()
    conn.close()
    return [tuple(r) for r in rows]


def _week_with_a_cook_tonight():
    tools.add_recipe("Chicken Skewers", ingredients=[{"item": "chicken thighs", "qty": "1 lb"}],
                     prep_time_minutes=10, cook_time_minutes=25)
    plan_id = tools.create_weekly_plan(_week_start())["weekly_plan_id"]
    tools.plan_meal(_today(), "Chicken Skewers", slot="dinner", weekly_plan_id=plan_id)
    return plan_id


def test_move_owner_puts_one_of_this_households_adults_on_the_cook(two):
    a, _, _ = two
    _adults("Emily", "Vineeth")
    _week_with_a_cook_tonight()
    move_id = _tonights_cook_move_id()
    vineeth = _member_ids()["Vineeth"]
    res = a.post(f"/api/today/moves/{move_id}/owner", json={"member_id": vineeth, "date": _today()})
    assert res.status_code == 200, res.text
    assert res.json()["change"]["status"] == "set"
    assert _overrides(A) == [(move_id, _today(), vineeth)]


def test_move_owner_refuses_another_households_adult(two):
    a, b, b_id = two
    _adults("Zqnadam", "Zqnbelle")
    a_adult = _member_ids()["Zqnadam"]
    with tools.use_household(b_id):
        _adults("Bea", "Bo")
        _week_with_a_cook_tonight()
        b_move = _tonights_cook_move_id()
    res = b.post(f"/api/today/moves/{b_move}/owner", json={"member_id": a_adult, "date": _today()})
    assert res.status_code == 400, res.text
    assert "Zqn" not in res.text
    assert _overrides(b_id) == [] and _overrides(A) == []


# ---------- POST /api/memory/primary-member ----------

def _primary(hid: int) -> int | None:
    with tools.use_household(hid):
        return tools.primary_member_id()


def test_primary_member_moves_to_another_adult_here(two):
    a, _, _ = two
    _adults("Emily", "Vineeth")
    ids = _member_ids()
    res = a.post("/api/memory/primary-member", json={"member_id": ids["Vineeth"]})
    assert res.status_code == 200, res.text
    assert _primary(A) == ids["Vineeth"]


def test_primary_member_refuses_another_households_adult(two):
    a, b, b_id = two
    _adults("Zqnadam", "Zqnbelle")
    a_ids = _member_ids()
    tools.set_primary_member(a_ids["Zqnadam"])
    with tools.use_household(b_id):
        _adults("Bea", "Bo")
        b_ids = _member_ids()
        tools.set_primary_member(b_ids["Bea"])
    res = b.post("/api/memory/primary-member", json={"member_id": a_ids["Zqnbelle"]})
    assert res.status_code == 400, res.text
    assert "Zqn" not in res.text
    assert _primary(A) == a_ids["Zqnadam"]
    assert _primary(b_id) == b_ids["Bea"]


# ---------- POST /api/memory/member/age-group ----------

def _age_groups(hid: int) -> dict[str, str]:
    conn = get_conn()
    rows = conn.execute("SELECT name, age_group FROM members WHERE household_id = ?", (hid,)).fetchall()
    conn.close()
    return {r["name"]: r["age_group"] for r in rows}


def test_age_group_by_another_households_name_is_a_stranger_here(two):
    a, b, b_id = two
    _adults("Zqnadam", "Zqnbelle")
    with tools.use_household(b_id):
        _adults("Bea", "Bo")
    a_before, b_before = _age_groups(A), _age_groups(b_id)
    res = b.post("/api/memory/member/age-group", json={"name": "Zqnbelle", "age_group": "child"})
    assert res.status_code == 404, res.text
    assert _age_groups(A) == a_before
    # Looked up, never created — B does not gain a member called Zqnbelle.
    assert _age_groups(b_id) == b_before
    # The same body from A does land.
    assert a.post("/api/memory/member/age-group",
                  json={"name": "Zqnbelle", "age_group": "teen"}).status_code == 200
    assert _age_groups(A)["Zqnbelle"] == "teen"


# ---------- POST /api/memory/edit and /api/memory/delete ----------

def test_memory_edit_lands_on_the_signed_in_household_only(two):
    a, b, b_id = two
    res = a.post("/api/memory/edit", json={"field": "dislikes", "value": ["cilantro"]})
    assert res.status_code == 200, res.text
    res = b.post("/api/memory/edit", json={"field": "dislikes", "value": ["olives"]})
    assert res.status_code == 200, res.text
    a_dislikes = a.get("/api/memory").json()
    b_dislikes = b.get("/api/memory").json()
    assert "cilantro" in str(a_dislikes) and "olives" not in str(a_dislikes)
    assert "olives" in str(b_dislikes) and "cilantro" not in str(b_dislikes)


def test_memory_delete_from_another_household_leaves_the_first_ones_item(two):
    a, b, _ = two
    assert a.post("/api/memory/edit", json={"field": "dislikes", "value": ["cilantro", "olives"]}).status_code == 200
    # B has saved preferences of its own, so the delete reaches the item
    # lookup rather than stopping at "No saved preferences yet."
    assert b.post("/api/memory/edit", json={"field": "dislikes", "value": ["mushrooms"]}).status_code == 200
    res = b.post("/api/memory/delete", json={"field": "dislikes", "item": "cilantro"})
    assert res.status_code in (200, 400), res.text
    assert "cilantro" in str(a.get("/api/memory").json())
    assert "mushrooms" in str(b.get("/api/memory").json())
    # A's own delete takes exactly that one item off.
    res = a.post("/api/memory/delete", json={"field": "dislikes", "item": "cilantro"})
    assert res.status_code == 200, res.text
    after = str(a.get("/api/memory").json())
    assert "cilantro" not in after and "olives" in after
