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
  * A is seeded first, and then A's real ids are READ BACK from the
    database (_a_ids). They are never assumed to be 1-3: clean_state empties
    tables but not sqlite_sequence, so in a full run A's ids are wherever
    the counters got to, and a harness aiming at 1-3 hit nothing and passed
    a planted leak (caught on review, 2026-10-06). B owns no row yet, so
    any of those ids B sends — path, query or JSON body — names A's.
  * Every route is driven once per A id, then once per NAME (A's member,
    recipe, grocery line, inventory item and staple), each field filled
    from the route's own declared parameters and body model.
  * Chores routes are only exercised up to their off-switch: Chores is
    paused, so they answer 403 before any lookup.
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
A_INVENTORY = "Zqn yogurt"
A_STAPLE = "Zqn coffee"
A_NAMES = (A_MEMBERS[0], A_RECIPE, A_GROCERY, A_INVENTORY, A_STAPLE)

# Run once, last, in this order: each ends or empties B's own account.
B_SELF_DESTRUCT = [
    ("POST", "/api/reset"),
    ("POST", "/api/household/remove-me"),
    ("GET", "/api/household/leave"),
    ("POST", "/api/household/delete"),
]
# Not attacked by id: no session involved. (FastAPI's own /docs, /redoc and
# /openapi.json are off — test_api_docs_off.py — so they are not routes.)
SKIP = {
    ("GET", "/login"), ("POST", "/login"), ("GET", "/logout"),
    ("GET", "/join"), ("POST", "/api/join"), ("GET", "/goodbye"),
    ("GET", "/healthz"), ("GET", "/robots.txt"), ("GET", "/favicon.ico"),
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
    tools.update_inventory(A_INVENTORY, "add", quantity="2", location="fridge")
    tools.update_inventory("Zqn rice", "add", quantity="1 bag", location="pantry")
    tools.add_fact("health", "Zqs peanut allergy", hard=True)
    tools.add_fact("routine", "Zqs swim on Tuesdays")
    tools.add_staple(A_STAPLE, every_days=14)
    tools.add_staple("Zqn dish soap", every_days=30)
    tools.add_attention_item("review", "Zqs check this")
    tools.add_attention_item("review", "Zqs and this")
    tools.hold_thing("Zqs remind me about the dentist", member_id=None)
    tools.hold_thing("Zqs and the plumber", member_id=None)
    tokens = [tools.get_or_create_share_link()["token"]]
    for name in A_MEMBERS:
        tokens.append(tools.get_or_create_member_share_link(name)["token"])
    return tokens


def _a_ids() -> list[int]:
    """Every id A owns, read back from the database after seeding: the
    `id` of each row under A's household_id, and of every row in a table
    with no household column (only A has written to those yet). Read, not
    assumed — see the round comment in the test."""
    conn = get_conn()
    ids = set()
    tables = [r["name"] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'")]
    for table in tables:
        cols = [c["name"] for c in conn.execute(f"PRAGMA table_info({table})")]
        if "id" not in cols or table in ("households", "household_credentials"):
            continue
        where = " WHERE household_id = ?" if "household_id" in cols else ""
        args = (A,) if where else ()
        ids.update(r["id"] for r in conn.execute(f"SELECT id FROM {table}{where}", args))
    conn.close()
    return sorted(i for i in ids if isinstance(i, int))


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
        # Only the app's own routes. FastAPI's /docs pages (on with
        # SHOW_API_DOCS=1 on a developer's machine) are plain routes with
        # nothing to aim at A.
        if not hasattr(route, "dependant"):
            continue
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
    ids = _a_ids()
    conn = get_conn()
    for table in ("grocery_items", "recipes", "inventory_items", "facts", "staples",
                  "weekly_plans", "attention_items", "held_things", "members"):
        owned = [r["id"] for r in conn.execute(f"SELECT id FROM {table} WHERE household_id = ?", (A,))]
        assert owned and set(owned) <= set(ids), f"A's seed missed {table}, or its ids aren't attacked"
    conn.close()
    # One round per id A really owns (in a full run the tables have been
    # emptied and refilled many times, so A's ids are wherever the counter
    # got to — never assume 1-3), then one per name, each with an id too.
    attacks = [{"id": i, "name": ""} for i in ids] + [
        {"id": ids[0], "name": name} for name in A_NAMES
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


# --- The photo routes, with a real image (2026-10-27) -----------------------
#
# The JSON rounds above can't reach an upload route: it takes multipart, so
# every round was refused at the door with a 422. These send a real (tiny,
# valid) PNG, so each route gets past its image checks and into the code
# that runs on a household's behalf. Only import-photo takes a field (its
# hint) that can carry A's names; the four scans take a photo and nothing
# else, so for them this proves "reached, shows nothing of A's, writes
# nothing of A's". The model is stubbed — it sees only the
# picture and B's typed hint, never a household id — and records that it
# was reached, so a route that quietly stopped at the door can't pass.

def _tiny_png() -> bytes:
    """A real 1x1 PNG: signature, IHDR, IDAT, IEND, with correct CRCs."""
    import struct
    import zlib

    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)

    ihdr = struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)
    idat = zlib.compress(b"\x00\xff\x80\x40")
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", idat) + chunk(b"IEND", b"")


def _is_upload(annotation) -> bool:
    from fastapi import UploadFile
    from starlette.datastructures import UploadFile as StarletteUpload
    args = typing.get_args(annotation) or (annotation,)
    return any(isinstance(a, type) and issubclass(a, (UploadFile, StarletteUpload)) for a in args)


def _upload_routes():
    out = []
    for method, route in _routes():
        dep = getattr(route, "dependant", None)
        if dep and any(_is_upload(b.field_info.annotation) for b in dep.body_params):
            out.append((method, route))
    return out


UPLOAD_ROUTES = {
    ("POST", "/api/inventory/scan-receipt"),
    ("POST", "/api/inventory/scan-fridge"),
    ("POST", "/api/inventory/scan-pantry"),
    ("POST", "/api/grocery-list/scan"),
    ("POST", "/api/recipes/import-photo"),
}


@pytest.mark.live_clock("recipe_photos.sweep_pending reads file mtimes, which nothing pins")
def test_household_b_photo_scans_reach_the_model_and_touch_nothing_of_as(
        a_household_named, monkeypatch, tmp_path):
    import app.main as main_module
    from app import agent, recipe_photos

    photos = tmp_path / "recipe_photos"
    monkeypatch.setenv("RECIPE_PHOTOS_DIR", str(photos))
    png = _tiny_png()
    assert recipe_photos.sniff_media_type(png) == "image/png"

    seen = []

    def scan_stub(name):
        def stub(image_b64, media_type, *a, **k):
            import base64
            assert base64.b64decode(image_b64) == png and media_type == "image/png"
            seen.append(name)
            return [{"item": "Plain toast bread", "quantity": "1", "category": "bakery"}]
        return stub

    def read_photos_stub(images, hint="", *a, **k):
        import base64
        assert [base64.b64decode(b) for b, _ in images] == [png]
        seen.append("import-photo")
        return {"found": True, "recipes": [{
            "name": "Plain toast",
            "default_servings": 1,
            "ingredients": [{"item": "bread", "qty": "1 slice", "category": "bakery"}],
            "instructions": ["Toast the bread."],
        }]}

    monkeypatch.setattr(main_module, "scan_receipt_image", scan_stub("scan-receipt"))
    monkeypatch.setattr(main_module, "scan_fridge_photo", scan_stub("scan-fridge"))
    monkeypatch.setattr(main_module, "scan_pantry_photo", scan_stub("scan-pantry"))
    monkeypatch.setattr(main_module, "scan_grocery_list_image", scan_stub("grocery-scan"))
    monkeypatch.setattr(agent, "read_recipe_from_photos_llm", read_photos_stub)

    routes = _upload_routes()
    assert {(m, r.path) for m, r in routes} == UPLOAD_ROUTES, (
        "an upload route was added or removed — add it to UPLOAD_ROUTES and stub its model call")

    tokens = _seed_a()
    b = households.create_household("Beta Attackers", B_PASSPHRASE)
    client = TestClient(app, raise_server_exceptions=False)
    assert client.post("/login", data={"password": B_PASSPHRASE, "next": "/"},
                       follow_redirects=False).status_code == 303
    assert client.get("/api/whoami").json()["household_id"] == b

    secrets_never_sent = ["Zqs", A_NAME, *tokens]
    ids = _a_ids()
    before = _owned_state()
    leaks, damage, refused = [], [], []
    attacks = [{"id": ids[0], "name": ""}, {"id": ids[-1], "name": ""}] + [
        {"id": ids[0], "name": name} for name in A_NAMES]

    for attack in attacks:
        for method, route in routes:
            dep = route.dependant
            form, files = {}, {}
            for p in dep.body_params:
                if _is_upload(p.field_info.annotation):
                    # Optional second photo left empty; the first is the PNG.
                    if p.field_info.is_required():
                        files[p.alias] = ("page.png", png, "image/png")
                else:
                    form[p.alias] = str(_value_for(p.name, p.field_info.annotation, attack))
            ratelimit.reset()
            res = client.request(method, route.path, data=form, files=files, follow_redirects=False)
            if res.status_code != 200:
                refused.append(f"{method} {route.path} -> {res.status_code} {res.text[:160]}")
            found = [s for s in secrets_never_sent if s in res.text]
            if not attack["name"] and "Zqn" in res.text:
                found.append("Zqn")
            if found:
                leaks.append(f"{method} {route.path} {form} showed {found}")
            damage += [f"{method} {route.path} {form}: {d}" for d in _damage(before, _owned_state())]

    assert not refused, "a photo route never got past the door:\n" + "\n".join(refused)
    assert set(seen) == {"scan-receipt", "scan-fridge", "scan-pantry", "grocery-scan", "import-photo"}
    assert not leaks, "household B read household A's data through a photo route:\n" + "\n".join(leaks)
    assert not damage, "household B changed household A's data through a photo route:\n" + "\n".join(damage)

    # The cookbook photo B uploaded is kept under B's folder, never A's.
    a_dir, b_dir = photos / str(A), photos / str(b)
    assert not a_dir.exists() or not any(a_dir.rglob("*.png")), "B's photo landed in A's folder"
    assert any((b_dir / "pending").glob("*.png")), "B's photo was not kept under B"
