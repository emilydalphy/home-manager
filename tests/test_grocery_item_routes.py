"""
The per-item grocery routes, over real HTTP — and one defect found by
writing them.

Thirteen routes under /api/grocery-list/{item_id}/… were named nowhere in
tests/ (measured 2026-09-24: 197 routes in app/main.py, 16 untested, and
this was the largest single group). They are all WRITES to the shopping
list, which is the surface a household stands in front of in a shop, so
"nothing exercises them" is a worse gap here than it would be on a read.

THE DEFECT: `mark_grocery_item` took any string as a status and wrote it.
Reproduced before anything was touched — `status="teleported"` answered
200, and the line was then in a status no view's WHERE clause looks for:
off the needed list, off the trolley, off the bought list, gone from the
shop without having been removed from it, and with no screen able to put
it back. Nothing crashed, which is what makes it the expensive kind.

It is the third instance of one class in this repo: check_off_meal had it
(fixed 2026-09-16, `InvalidMealStatus`), set_chore_instance_status had it
(`InvalidChoreStatus`), and this is the same fix in the same shape. The
argument for guarding it even though the screens and the chat schema
already send only the three is the one the cooked tick's own docstring
makes — and it is STRONGER here, because four other real statuses
('removed', 'carried', 'excluded', 'spice') sit one function over, each
with bookkeeping beside it that going through this door would skip.

NOT covered here: what the grocery LIST means (many files), the offline
queue (tests/test_grocery_offline.py), and the pre-shop/carried-over
flows, which have their own files. This is the route layer.
"""
from __future__ import annotations

import pytest

from app import households, security, tools


BETA_PASSPHRASE = "grocery-routes-beta-passphrase"


def _sign_in(client, password):
    res = client.post(
        "/login", data={"password": password, "next": "/"}, follow_redirects=False
    )
    assert res.status_code == 303, "sign-in should redirect on success"


@pytest.fixture
def emily(client):
    _sign_in(client, "test-password")
    return client


@pytest.fixture
def beta_household():
    return households.create_household("Grocery Routes Beta", BETA_PASSPHRASE)


def _line(item="Rice", qty="1 bag", category="pantry"):
    return tools.add_grocery_item(item, qty, category)["item_id"]


def _statuses():
    return {r["item"]: r["status"] for r in tools.list_grocery_list(status="all")}


# ---------------------------------------------------------------------------
# The status a shopper can move a line to
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("status", ["needed", "in_cart", "purchased"])
def test_the_three_a_shopper_actually_sends_are_taken(emily, status):
    item = _line()
    res = emily.post(f"/api/grocery-list/{item}/status", json={"status": status})
    assert res.status_code == 200, res.text
    assert _statuses()["Rice"] == status


@pytest.mark.parametrize("status, why", [
    ("teleported", "a typo"),
    ("", "an empty string"),
    ("PURCHASED", "the right word in the wrong case"),
    ("removed", "a real status, set elsewhere with its own bookkeeping"),
    ("carried", "ditto — carried_from_plan_id goes with it"),
    ("excluded", "ditto — exclude_grocery_item is the door"),
    ("spice", "ditto — the spice rack's own tick"),
])
def test_anything_else_is_refused_and_the_line_is_left_where_it_was(emily, status, why):
    """
    422 rather than 404: the line exists, the request does not make sense.
    Four of these seven are REAL statuses of this column — which is the
    point. Reaching them through this door would set the status and skip
    every piece of bookkeeping that belongs with it.
    """
    item = _line()
    res = emily.post(f"/api/grocery-list/{item}/status", json={"status": status})
    assert res.status_code == 422, f"{why}: answered {res.status_code}"
    assert _statuses()["Rice"] == "needed", f"{why}: the line moved anyway"


def test_a_refused_status_is_told_apart_from_a_line_that_is_not_there(emily):
    """
    The ordering trap this fix shares with the cooker and chore routes:
    InvalidGroceryStatus IS a ValueError, so if its except clause were
    below the 404's, a bad status would read as a missing line — and the
    household would be told the thing they are looking at does not exist.
    """
    item = _line()
    bad_status = emily.post(f"/api/grocery-list/{item}/status", json={"status": "nope"})
    no_line = emily.post("/api/grocery-list/999999/status", json={"status": "purchased"})
    assert bad_status.status_code == 422
    assert no_line.status_code == 404


def test_a_non_string_status_was_already_refused_and_still_is(emily):
    """Pydantic's own 422, which is why the guard above answers 422 too —
    one client mistake should not have two codes."""
    item = _line()
    assert emily.post(f"/api/grocery-list/{item}/status", json={"status": 7}).status_code == 422


def test_the_guard_holds_for_a_caller_that_never_reaches_the_route(emily):
    """
    It is in the tool, above get_conn, not in the route — so chat and a
    script are held to the same three, and a bad status never opens a
    connection or takes the write lock.
    """
    item = _line()
    with pytest.raises(tools.InvalidGroceryStatus):
        tools.mark_grocery_item(item, status="teleported")
    assert _statuses()["Rice"] == "needed"


# ---------------------------------------------------------------------------
# The rest of the cluster
# ---------------------------------------------------------------------------

def test_update_corrects_the_quantity_and_the_aisle(emily):
    item = _line()
    res = emily.post(f"/api/grocery-list/{item}/update",
                     json={"quantity": "3 bags", "category": "produce"})
    assert res.status_code == 200, res.text
    row = [r for r in tools.list_grocery_list(status="all") if r["id"] == item][0]
    assert row["quantity"] == "3 bags"
    assert row["category"] == "produce"


def test_remove_takes_the_line_off_the_list_entirely(emily):
    item = _line()
    assert emily.post(f"/api/grocery-list/{item}/remove").status_code == 200
    assert "Rice" not in _statuses()


def test_exclude_hides_a_line_and_include_puts_it_back(emily):
    item = _line()
    assert emily.post(f"/api/grocery-list/{item}/exclude").status_code == 200
    assert "Rice" not in [r["item"] for r in tools.list_grocery_list(status="needed")]
    assert "Rice" in [r["item"] for r in tools.list_grocery_list(status="excluded")]
    assert emily.post(f"/api/grocery-list/{item}/include").status_code == 200
    assert "Rice" in [r["item"] for r in tools.list_grocery_list(status="needed")]


def test_already_have_moves_the_line_into_the_kitchen(emily):
    item = _line()
    res = emily.post(f"/api/grocery-list/{item}/already-have")
    assert res.status_code == 200, res.text
    assert "Rice" not in [r["item"] for r in tools.list_grocery_list(status="needed")]
    assert any(r["item"] == "Rice" for r in tools.get_inventory())


@pytest.mark.parametrize("path, body", [
    ("update", {"quantity": "2"}),
    ("status", {"status": "purchased"}),
    ("remove", None),
    ("exclude", None),
    ("include", None),
    ("already-have", None),
])
def test_a_line_that_is_not_there_is_a_404_on_every_one_of_them(emily, path, body):
    res = emily.post(f"/api/grocery-list/999999/{path}", json=body)
    assert res.status_code == 404, f"{path} answered {res.status_code}"


@pytest.mark.parametrize("path, body", [
    ("update", {"quantity": "2"}),
    ("status", {"status": "purchased"}),
    ("remove", None),
    ("exclude", None),
    ("include", None),
    ("already-have", None),
])
def test_none_of_them_answers_a_caller_who_is_not_signed_in(client, path, body):
    res = client.post(f"/api/grocery-list/1/{path}", json=body)
    assert res.status_code == 401, f"{path} answered {res.status_code}"


# ---------------------------------------------------------------------------
# One household's line is not another's
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("path, body", [
    ("update", {"quantity": "99"}),
    ("status", {"status": "purchased"}),
    ("remove", None),
    ("exclude", None),
    ("already-have", None),
])
def test_another_households_line_id_is_simply_not_found(client, beta_household, path, body):
    """
    Over HTTP rather than in-process, because the route is where a caller
    arrives and the binding it rests on (auth_middleware setting the
    household per request) lives nowhere near grocery.py.
    """
    _sign_in(client, "test-password")
    item = _line("Emilys rice")

    _sign_in(client, BETA_PASSPHRASE)
    assert client.cookies[security.COOKIE_NAME]
    res = client.post(f"/api/grocery-list/{item}/{path}", json=body)
    assert res.status_code == 404, f"{path} answered {res.status_code} for the wrong household"

    _sign_in(client, "test-password")
    rows = {r["item"]: r for r in tools.list_grocery_list(status="all")}
    assert "Emilys rice" in rows, f"{path} removed another household's line"
    assert rows["Emilys rice"]["status"] == "needed"
    assert rows["Emilys rice"]["quantity"] == "1 bag"


def test_that_isolation_test_can_actually_fail(client, beta_household, monkeypatch):
    """
    The mutation that makes the scope worthless: every request reads
    household 1. Without this, the test above is the kind of isolation
    test that passes because the fixture never crossed a boundary — which
    this repo has been bitten by more than once, including on another
    branch the same night.
    """
    from app.tools import _shared

    _sign_in(client, "test-password")
    item = _line("Emilys rice")

    _sign_in(client, BETA_PASSPHRASE)
    monkeypatch.setattr(_shared, "household_id", lambda: 1)
    res = client.post(f"/api/grocery-list/{item}/remove")
    assert res.status_code == 200, (
        "with the household forced to 1 the other household DOES reach it — "
        "so the test above is measuring the household scope and not something else"
    )
