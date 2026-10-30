"""
Closing route test gaps, 2026-10-30 (third pass after 10-24 and 10-27).

How these were picked. Every @app route in app/main.py (paused area aside) is
requested by at least one test (method + path shape, path params allowed
for). So the gap is not "never requested" but "never reached": instrumenting
test_isolation_attack.py listed 111 routes that only ever answered 4xx to
household B. Most of those refusals ARE the scoped lookup saying no ("That's
already gone from the kitchen"), which is proof. Two kinds are not:

  1. The week-entry routes (swap, move, drop/add a day, sides, change the
     protein). The attack fills every int field with the same id, so A's
     entry_id always came with A's plan id (or B had no week at all), and
     _plan_id_for_request refused the PLAN before the ENTRY was ever looked
     up. Whether an entry is checked against the plan was unproven. Here B
     has its own week and sends its own plan id with A's entry ids, and its
     own entry with A's entry as the target.
  2. Routes refused for a word the attack couldn't guess (a location, an
     amount level, a use-up action), so A's item id never reached the
     lookup. Here the body is valid.

Each: household A's own call still works (so the refusal isn't blanket),
and B, signed in, neither reads nor changes anything of A's. No defect was
found; these pin it.
"""
from __future__ import annotations

import datetime

import pytest
from fastapi.testclient import TestClient

from conftest import household_today

from app import households, ratelimit, tools
from app.db import get_conn
from app.main import app
from app.tools._shared import DEFAULT_HOUSEHOLD_ID

A = DEFAULT_HOUSEHOLD_ID
B_PASSPHRASE = "route-gaps-1030-b-passphrase"


def _next_monday() -> datetime.date:
    """A week wholly in the future, so no route refuses a past night."""
    t = household_today()
    return t + datetime.timedelta(days=7 - t.weekday())


def _days(start: datetime.date) -> list[str]:
    return [(start + datetime.timedelta(days=i)).isoformat() for i in range(7)]


def _seed_week(prefix: str, start: datetime.date) -> tuple[int, list[int]]:
    """The current household: an adult, two recipes, seven dinners next
    week alternating between them. Returns (plan id, entry ids by day)."""
    tools.add_member(prefix + "adult")
    tools.set_member_age_group(prefix + "adult", "adult")
    soup, stew = prefix + " Soup", prefix + " Stew"
    tools.add_recipe(soup, ingredients=[{"item": prefix + " lentils", "qty": "1 cup"}], instructions=["Simmer."])
    tools.add_recipe(stew, ingredients=[{"item": prefix + " carrots", "qty": "2"}], instructions=["Stew."])
    plan_id = tools.create_weekly_plan(start.isoformat())["weekly_plan_id"]
    for i, day in enumerate(_days(start)):
        tools.plan_meal(day, soup if i % 2 == 0 else stew, slot="dinner", weekly_plan_id=plan_id)
    conn = get_conn()
    entries = [r["id"] for r in conn.execute(
        "SELECT id FROM meal_plan_entries WHERE weekly_plan_id = ? AND slot = 'dinner' ORDER BY date",
        (plan_id,))]
    conn.close()
    return plan_id, entries


def _a_snapshot() -> dict:
    """Everything of A's these routes could touch: its plan entries, its
    grocery list and its kitchen, row for row."""
    conn = get_conn()
    out = {
        "entries": [tuple(r) for r in conn.execute(
            "SELECT e.* FROM meal_plan_entries e JOIN weekly_plans p ON p.id = e.weekly_plan_id "
            "WHERE p.household_id = ? ORDER BY e.id", (A,))],
        "plans": [tuple(r) for r in conn.execute(
            "SELECT * FROM weekly_plans WHERE household_id = ? ORDER BY id", (A,))],
        "grocery": [tuple(r) for r in conn.execute(
            "SELECT * FROM grocery_items WHERE household_id = ? ORDER BY id", (A,))],
        "inventory": [tuple(r) for r in conn.execute(
            "SELECT * FROM inventory_items WHERE household_id = ? ORDER BY id", (A,))],
    }
    conn.close()
    return out


def _rows_of(plan_id: int) -> list[tuple]:
    conn = get_conn()
    rows = [tuple(r) for r in conn.execute(
        "SELECT * FROM meal_plan_entries WHERE weekly_plan_id = ? ORDER BY id", (plan_id,))]
    conn.close()
    return rows


def _sign_in(client, password):
    res = client.post("/login", data={"password": password, "next": "/"}, follow_redirects=False)
    assert res.status_code == 303
    return client


@pytest.fixture
def world():
    """A and B each with their own next-week plan; two signed-in clients."""
    start = _next_monday()
    a_plan, a_entries = _seed_week("Zqs", start)
    tools.update_inventory("Zqs yogurt", "add", quantity="2", location="fridge")
    b_id = households.create_household("Route Gaps 1030 B", B_PASSPHRASE)
    with tools.use_household(b_id):
        b_plan, b_entries = _seed_week("Bea", start)
        tools.update_inventory("Bea spinach", "add", quantity="1 bag", location="fridge")
    conn = get_conn()
    a_inv = conn.execute("SELECT id FROM inventory_items WHERE household_id = ?", (A,)).fetchone()["id"]
    b_inv = conn.execute("SELECT id FROM inventory_items WHERE household_id = ?", (b_id,)).fetchone()["id"]
    conn.close()
    with TestClient(app, raise_server_exceptions=False) as a, \
            TestClient(app, raise_server_exceptions=False) as b:
        _sign_in(a, "test-password")
        _sign_in(b, B_PASSPHRASE)
        yield {
            "a": a, "b": b, "b_id": b_id, "week": start.isoformat(), "days": _days(start),
            "a_plan": a_plan, "a_entries": a_entries, "a_inv": a_inv,
            "b_plan": b_plan, "b_entries": b_entries, "b_inv": b_inv,
        }


def _b_sends(w, method, url, body=None, params=None):
    ratelimit.reset()
    before = _a_snapshot()
    res = w["b"].request(method, url, json=body, params=params)
    assert res.status_code in (400, 404), f"{method} {url} {body} -> {res.status_code} {res.text[:200]}"
    assert "Zqs" not in res.text, f"{url} showed A's data: {res.text[:200]}"
    assert _a_snapshot() == before, f"{method} {url} {body} changed household A's rows"
    return res


# --- 1. Week entries: B's own plan id, A's entry id ------------------------

# Every route that takes an entry_id on a week. Bodies are otherwise valid
# for B's own week (each was checked to get past validation).
_ENTRY_ROUTES = [
    ("POST", "swap-options", lambda w, e: {"entry_id": e}),
    ("POST", "swap-in-place", lambda w, e: {"entry_id": e}),
    ("POST", "swap-choose", lambda w, e: {"entry_id": e, "option": 0, "meal": "Bea Soup",
                                          "weekly_plan_id": w["b_plan"]}),
    ("POST", "swap-undo", lambda w, e: {"entry_id": e, "weekly_plan_id": w["b_plan"]}),
    ("POST", "move-options", lambda w, e: {"entry_id": e, "weekly_plan_id": w["b_plan"]}),
    ("POST", "move-meal", lambda w, e: {"entry_id": e, "to_date": w["days"][5],
                                        "weekly_plan_id": w["b_plan"]}),
    ("POST", "drop-dish-day", lambda w, e: {"entry_id": e, "confirm_cooked": True,
                                            "weekly_plan_id": w["b_plan"]}),
    ("POST", "add-dish-day", lambda w, e: {"entry_id": e, "target_entry_id": w["b_entries"][1],
                                           "weekly_plan_id": w["b_plan"]}),
    ("POST", "add-component", lambda w, e: {"entry_id": e, "text": "potatoes",
                                            "weekly_plan_id": w["b_plan"]}),
    ("POST", "remove-component", lambda w, e: {"entry_id": e, "name": "potatoes",
                                               "weekly_plan_id": w["b_plan"]}),
    ("POST", "change-part", lambda w, e: {"entry_id": e, "choice": "tofu",
                                          "weekly_plan_id": w["b_plan"]}),
    ("GET", "part-options", None),
]


@pytest.mark.parametrize("method,route,body", _ENTRY_ROUTES, ids=[r[1] for r in _ENTRY_ROUTES])
def test_household_b_cannot_reach_household_as_meal_through_its_own_week(world, method, route, body):
    w = world
    url = f"/api/week/{w['week']}/{route}"
    for entry in w["a_entries"][:3]:
        if body is None:
            _b_sends(w, method, url, params={"entry_id": entry, "weekly_plan_id": w["b_plan"]})
        else:
            _b_sends(w, method, url, body=body(w, entry))


def test_household_b_cannot_add_its_dish_onto_household_as_night(world):
    """The other way round: B's own dish as the source, A's night as the
    day to put one more of it on."""
    w = world
    for target in w["a_entries"][:3]:
        _b_sends(w, "POST", f"/api/week/{w['week']}/add-dish-day",
                 body={"entry_id": w["b_entries"][0], "target_entry_id": target,
                       "weekly_plan_id": w["b_plan"]})


def test_moving_and_dropping_a_night_still_work_for_their_own_household(world):
    """The refusals above are about whose meal it is, not a blanket no: A
    moving and dropping its own nights lands, and B's week is untouched."""
    w = world
    a = w["a"]
    b_before = _rows_of(w["b_plan"])

    moved = a.post(f"/api/week/{w['week']}/move-meal",
                   json={"entry_id": w["a_entries"][0], "to_date": w["days"][2],
                         "weekly_plan_id": w["a_plan"]})
    assert moved.status_code == 200, moved.text
    conn = get_conn()
    on_wed = conn.execute(
        "SELECT r.name FROM meal_plan_entries e JOIN recipes r ON r.id = e.recipe_id "
        "WHERE e.weekly_plan_id = ? AND e.date = ? AND e.slot = 'dinner'",
        (w["a_plan"], w["days"][2])).fetchone()
    conn.close()
    assert on_wed["name"] == "Zqs Soup"

    dropped = a.post(f"/api/week/{w['week']}/drop-dish-day",
                     json={"entry_id": w["a_entries"][6], "weekly_plan_id": w["a_plan"]})
    assert dropped.status_code == 200, dropped.text

    assert _rows_of(w["b_plan"]) == b_before


# --- 2. Kitchen items, with a body the attack couldn't guess ---------------

def test_household_b_cannot_move_or_mark_household_as_kitchen_item(world):
    w = world
    i = w["a_inv"]
    _b_sends(w, "POST", f"/api/inventory/{i}/location", body={"location": "freezer"})
    for level in ("plenty", "half", "almost_gone"):
        # "almost gone" on food a meal needs puts it on the list: the
        # snapshot covers A's grocery list too.
        _b_sends(w, "POST", f"/api/whats-home/{i}/amount", body={"level": level})
    _b_sends(w, "GET", f"/api/whats-home/{i}")


def test_kitchen_item_routes_still_work_for_their_own_household(world):
    w = world
    a = w["a"]
    i = w["a_inv"]
    assert a.post(f"/api/inventory/{i}/location", json={"location": "freezer"}).status_code == 200
    assert a.post(f"/api/whats-home/{i}/amount", json={"level": "half"}).status_code == 200
    conn = get_conn()
    row = conn.execute("SELECT location FROM inventory_items WHERE id = ?", (i,)).fetchone()
    conn.close()
    assert row["location"] == "freezer"
    assert "Zqs yogurt" in a.get(f"/api/whats-home/{i}").text


@pytest.mark.parametrize("action", ["freeze", "side", "let_go"])
def test_household_b_cannot_resolve_household_as_use_up_item_on_its_own_plan(world, action):
    """B's own plan id in the path, A's kitchen item beside it."""
    w = world
    _b_sends(w, "POST", f"/api/week-plan/{w['b_plan']}/use-up/{w['a_inv']}", body={"action": action})
    _b_sends(w, "POST", f"/api/week-plan/{w['a_plan']}/use-up/{w['b_inv']}", body={"action": action})
