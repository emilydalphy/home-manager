"""
The isolation attack: household B, signed in, throws household A's ids and
names at EVERY authenticated route the app has. Nothing of A's may come
back, and nothing of A's may change.

Security audit card (Loop Board, "Security audit before App Store
submission", 2026-10-06 night). The 2026-09-01 harness that did this by hand
(30 read routes, 18 write paths) lived outside the repo and predates the
shell rewrite, so it covered the app as it was. This one lives in the suite
and enumerates app.routes itself, so a route added next month is attacked
the day it is added without anyone remembering to list it.

How the attack is built, and why it is a fair one:
  * A is seeded first on an empty database, so A's rows are ids 1, 2, 3 in
    every table and B owns no row any id can reach. Any id-shaped value B
    sends — in the path, the query or the JSON body — names something of A's.
  * Every route is driven twice by id (1, then 2) and once by NAME (A's
    member, recipe and grocery names), each field filled from the route's
    own declared parameters and body model.
  * READS: A's private text carries "Zqs", A's names carry "Zqn". No
    response to B may contain "Zqs", A's share-link tokens, or A's
    household name; the id rounds (which send no names) may not contain
    "Zqn" either. The name round may — B typed it, an echo is not a leak.
  * WRITES: after every request, each row A owned before the attack (by
    household_id, or every row in a table with no household column) must
    be unchanged and still there, and no new row may appear under A's
    household_id.

What it does NOT do: pick valid shapes for every body (a 422 is a refusal,
and a refusal leaks nothing, but it also means that route's deeper code
was not reached by this round), nor drive the public share routes (they
are keyed by an unguessable token, not an id) or the four routes that end
B's own session or wipe B's own data — those run LAST, once, so that what
they delete can be checked to be B's alone.
"""
from __future__ import annotations

import datetime
import json
import re
import typing

import pytest
from fastapi.testclient import TestClient
from pydantic import BaseModel

from conftest import household_today

from app import households, ratelimit, security, tools
from app.db import get_conn
from app.main import app
from app.tools._shared import DEFAULT_HOUSEHOLD_ID

A = DEFAULT_HOUSEHOLD_ID
A_NAME = "Zqs Household Alpha"
B_PASSPHRASE = "isolation-attack-b-passphrase"

A_MEMBERS = ("Zqnadam", "Zqnbelle")
A_RECIPE = "Zqn Lentil Soup"
A_GROCERY = "Zqn oat milk"

# Run once, last, in this order: each ends or empties B's own account.
B_SELF_DESTRUCT = [
    ("POST", "/api/reset"),
    ("POST", "/api/household/remove-me"),
    ("GET", "/api/household/leave"),
    ("POST", "/api/household/delete"),
]
# Not attacked by id: no session involved, or the framework's own pages.
SKIP = {
    ("GET", "/login"), ("POST", "/login"), ("GET", "/logout"),
    ("GET", "/join"), ("POST", "/api/join"), ("GET", "/goodbye"),
    ("GET", "/healthz"), ("GET", "/robots.txt"), ("GET", "/favicon.ico"),
    ("GET", "/openapi.json"), ("GET", "/docs"), ("GET", "/docs/oauth2-redirect"),
    ("GET", "/redoc"),
    # Keyed by a 128-bit token, not an id; B has none of A's.
    ("GET", "/api/share/{token}"), ("GET", "/share/{token}"),
    ("GET", "/api/member-share/{token}"), ("GET", "/member-share/{token}"),
    ("POST", "/api/member-share/{token}/restriction"),
    ("POST", "/api/member-share/{token}/note"),
}
# Append-only logs. B's own requests add rows here; A's rows must still
# not change, so they are checked like the rest, only new rows are fine.
_NAME_FIELDS = re.compile(r"name|member|who|person|item|meal|dish|recipe|text|by$", re.I)


def _week_start() -> str:
    today = household_today()
    return (today - datetime.timedelta(days=today.weekday())).isoformat()


@pytest.fixture
def a_household_named():
    """Household 1's row outlives a test (clean_state empties tables, not
    households), so its real name goes back afterwards."""
    conn = get_conn()
    original = conn.execute("SELECT name FROM households WHERE id = ?", (A,)).fetchone()["name"]
    conn.execute("UPDATE households SET name = ? WHERE id = ?", (A_NAME, A))
    conn.commit()
    conn.close()
    yield
    conn = get_conn()
    conn.execute("UPDATE households SET name = ? WHERE id = ?", (original, A))
    conn.commit()
    conn.close()


def _seed_a() -> list[str]:
    """Household A with something in every corner B might reach. Returns
    A's secrets that are never sent by B (its share tokens)."""
    for name in A_MEMBERS:
        tools.add_member(name)
        tools.set_member_age_group(name, "adult")
    tools.add_recipe(
        A_RECIPE,
        ingredients=[{"item": "Zqs red lentils", "qty": "1 cup"}],
        notes="Zqs family recipe",
        instructions=["Zqs simmer"],
    )
    tools.add_recipe("Zqn Toast", ingredients=[{"item": "Zqs bread", "qty": "2 slices"}])
    plan = tools.create_weekly_plan(_week_start(), constraints_notes="Zqs notes")
    plan_id = plan.get("weekly_plan_id") or plan.get("id")
    today = household_today().isoformat()
    tools.plan_meal(today, A_RECIPE, slot="dinner", weekly_plan_id=plan_id, reasoning="Zqs why")
    tools.plan_meal(today, "Zqn Toast", slot="breakfast", weekly_plan_id=plan_id)
    tools.add_grocery_item(A_GROCERY, quantity="1")
    tools.add_grocery_item("Zqn paper towels")
    tools.update_inventory("Zqn yogurt", "add", quantity="2", location="fridge")
    tools.update_inventory("Zqn rice", "add", quantity="1 bag", location="pantry")
    tools.add_fact("health", "Zqs peanut allergy", hard=True)
    tools.add_fact("routine", "Zqs swim on Tuesdays")
    tools.add_staple("Zqn coffee", every_days=14)
    tools.add_staple("Zqn dish soap", every_days=30)
    tools.add_attention_item("review", "Zqs check this")
    tools.add_attention_item("review", "Zqs and this")
    tools.hold_thing("Zqs remind me about the dentist", member_id=None)
    tools.hold_thing("Zqs and the plumber", member_id=None)
    tokens = [tools.get_or_create_share_link()["token"]]
    for name in A_MEMBERS:
        tokens.append(tools.get_or_create_member_share_link(name)["token"])
    return tokens


def _owned_state() -> dict:
    """Every row A owns right now, by table and rowid. A table with a
    household column is keyed "scoped:<table>", so a row NEW to A there is
    caught too (B adding to A's list is as much a breach as B deleting
    from it); a table without one holds B's own new child rows as well, so
    only changes to what was already there count."""
    conn = get_conn()
    state = {}
    tables = [r["name"] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'")]
    for table in tables:
        cols = [c["name"] for c in conn.execute(f"PRAGMA table_info({table})")]
        if table == "households":
            rows = conn.execute("SELECT rowid AS _rid, * FROM households WHERE id = ?", (A,))
        elif "household_id" in cols:
            rows = conn.execute(f"SELECT rowid AS _rid, * FROM {table} WHERE household_id = ?", (A,))
        else:
            rows = conn.execute(f"SELECT rowid AS _rid, * FROM {table}")
        scoped = table == "households" or "household_id" in cols
        state[("scoped:" if scoped else "") + table] = {r["_rid"]: tuple(r) for r in rows}
    conn.close()
    return state


def _damage(before: dict, after: dict) -> list[str]:
    out = []
    for table, rows in before.items():
        now = after.get(table, {})
        for rid, row in rows.items():
            if rid not in now:
                out.append(f"{table} row {rid} deleted")
            elif now[rid] != row:
                out.append(f"{table} row {rid} changed")
        if table.startswith("scoped:"):
            for rid in set(now) - set(rows):
                out.append(f"{table} row {rid} added to A")
    return out


def _value_for(name: str, annotation, attack) -> typing.Any:
    """A value for one declared parameter/field, aimed at A."""
    origin = typing.get_origin(annotation)
    args = [a for a in typing.get_args(annotation) if a is not type(None)]
    if origin in (typing.Union, getattr(__import__("types"), "UnionType", None)) and args:
        return _value_for(name, args[0], attack)
    if origin is typing.Literal:
        return args[0]
    if origin in (list, tuple, set) or annotation in (list, tuple, set):
        inner = args[0] if args else str
        return [_value_for(name, inner, attack)]
    if origin is dict or annotation is dict:
        return {}
    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        return _model_body(annotation, attack)
    if annotation is bool:
        return True
    if annotation in (int, float):
        return attack["id"]
    if annotation is str or annotation is typing.Any:
        if "week" in name or name.endswith("date") or name in ("day", "start", "end"):
            return _week_start()
        if attack["name"] and _NAME_FIELDS.search(name):
            return attack["name"]
        if "id" in name or "token" in name:
            return str(attack["id"])
        return attack.get("choice", "x")
    return attack["id"]


def _model_body(model: type[BaseModel], attack) -> dict:
    return {
        field.alias or key: _value_for(key, field.annotation, attack)
        for key, field in model.model_fields.items()
    }


def _request_for(route, method: str, attack) -> dict:
    dep = route.dependant
    path = route.path
    for p in dep.path_params:
        value = _value_for(p.name, p.field_info.annotation, attack)
        path = re.sub(r"\{" + p.name + r"(:[^}]*)?\}", str(value), path)
    params = {}
    for q in dep.query_params:
        params[q.alias] = _value_for(q.name, q.field_info.annotation, attack)
    body = None
    if dep.body_params:
        if len(dep.body_params) == 1 and isinstance(dep.body_params[0].field_info.annotation, type) \
                and issubclass(dep.body_params[0].field_info.annotation, BaseModel):
            body = _model_body(dep.body_params[0].field_info.annotation, attack)
        else:
            body = {b.alias: _value_for(b.name, b.field_info.annotation, attack) for b in dep.body_params}
    return {"method": method, "url": path, "params": params, "json": body}


# "Use one of: resolved, dismissed." / "status must be pending or done" /
# "The answer is 'had' or 'skipped'": a route that names the words it wants.
_CHOICE = re.compile(r"one of:\s*'?(\w+)|must be '?(\w+)'? or|is '(\w+)' or|[Tt]ype '?(\w+)'? to confirm")


def _choice_from(res) -> str | None:
    if res.status_code not in (400, 422):
        return None
    m = _CHOICE.search(res.text)
    return next((g for g in m.groups() if g), None) if m else None


def _routes():
    out = []
    for route in app.routes:
        for method in sorted(getattr(route, "methods", None) or ()):
            if method == "HEAD":
                continue
            out.append((method, route))
    return out


def test_household_b_cannot_read_or_change_anything_of_household_as(a_household_named):
    tokens = _seed_a()
    b = households.create_household("Beta Attackers", B_PASSPHRASE)
    assert b != A

    client = TestClient(app, raise_server_exceptions=False)
    res = client.post("/login", data={"password": B_PASSPHRASE, "next": "/"}, follow_redirects=False)
    assert res.status_code == 303
    assert client.get("/api/whoami").json()["household_id"] == b

    secrets_never_sent = ["Zqs", A_NAME, *tokens]
    attacks = [
        {"id": 1, "name": ""},
        {"id": 2, "name": ""},
        {"id": 1, "name": A_MEMBERS[0]},
        {"id": 3, "name": A_RECIPE},
        {"id": 1, "name": A_GROCERY},
    ]

    routes = _routes()
    keys = {(m, r.path) for m, r in routes}
    stale = (SKIP | set(B_SELF_DESTRUCT)) - keys
    assert not stale, f"these skipped routes no longer exist: {sorted(stale)}"

    leaks, damage, driven = [], [], set()
    before = _owned_state()

    def send(method, route, attack):
        req = _request_for(route, method, attack)
        ratelimit.reset()
        res = client.request(req["method"], req["url"], params=req["params"],
                             json=req["json"], follow_redirects=False)
        return req, res

    def drive(method, route, attack):
        req, res = send(method, route, attack)
        choice = _choice_from(res)
        if choice:
            # Refused for a word it named: say that word, so the route's
            # own lookup — the part that has to be household-scoped — runs.
            req, res = send(method, route, {**attack, "choice": choice})
        text = res.text
        found = [s for s in secrets_never_sent if s in text]
        if not attack["name"] and "Zqn" in text:
            found.append("Zqn")
        if found:
            leaks.append(f"{method} {req['url']} {json.dumps(req['json'])[:120]} -> {res.status_code} showed {found}")
        after = _owned_state()
        for d in _damage(before, after):
            damage.append(f"{method} {req['url']} {json.dumps(req['json'])[:120]} -> {res.status_code}: {d}")
        driven.add((method, route.path))
        return after

    for attack in attacks:
        for method, route in routes:
            key = (method, route.path)
            if key in SKIP or key in B_SELF_DESTRUCT:
                continue
            before = drive(method, route, attack) if not damage else before
            if damage:
                break
        if damage:
            break

    # B wipes, leaves and deletes ITS OWN account. A must be untouched.
    by_key = {(m, r.path): r for m, r in routes}
    for key in B_SELF_DESTRUCT:
        if damage:
            break
        drive(key[0], by_key[key], {"id": 1, "name": ""})

    conn = get_conn()
    b_left = conn.execute("SELECT COUNT(*) AS n FROM households WHERE id = ?", (b,)).fetchone()["n"]
    conn.close()
    assert b_left == 0 or damage, "B's own delete never ran, so the delete path went unchecked"

    expected = keys - SKIP
    assert driven == expected or damage, f"routes not driven: {sorted(expected - driven)}"
    assert not leaks, "household B read household A's data:\n" + "\n".join(leaks)
    assert not damage, "household B changed household A's data:\n" + "\n".join(damage)

    # And A, signed in, still sees its own week.
    a = TestClient(app)
    a.post("/login", data={"password": "test-password", "next": "/"}, follow_redirects=False)
    assert A_GROCERY in a.get("/api/grocery-list").text
