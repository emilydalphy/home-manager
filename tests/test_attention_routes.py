"""
The three /api/attention routes, which had no test at all.

They back the Cook screen's attention banner — the queue a meal's check-off
writes to when an ingredient match is too uncertain to act on silently — and
two of the three take a CLIENT-SUPPLIED ROW ID. That combination (a live
screen, an id off the wire, nothing pinning it) is what makes the gap worth
closing rather than the routes being especially fragile: measured, they are
correct today, and these tests are here so they stay that way.

The third thing this file recorded was a characterisation rather than a fix:
/resolve took any status string at all, the same shape as the cooked-tick
bug fixed on 2026-09-16 ("A cooked tick takes no third word"), one door
over. That card was worked on 2026-09-25 and the test below is INVERTED
rather than deleted, so the history reads. The rest of the guard lives in
tests/test_attention_status_validated.py.
"""
from __future__ import annotations

import pytest

from app import households, security, tools
from app.db import get_conn
from app.tools._shared import DEFAULT_HOUSEHOLD_ID


OTHER_PASSPHRASE = "the-other-familys-passphrase"


@pytest.fixture
def other_household():
    return households.create_household("The Other Family", OTHER_PASSPHRASE)


def _sign_in(client, password):
    res = client.post("/login", data={"password": password, "next": "/"}, follow_redirects=False)
    assert res.status_code == 303
    return client.cookies[security.COOKIE_NAME]


def _queue(summary="Did you use the lettuce?", detail=None, household=None):
    """
    One queued item, written the way check_off_meal writes them.

    `household` is not a convenience: signing a TestClient in sets the
    household for a REQUEST, and a tool called straight from a test runs
    outside one, on the ContextVar's default. Seeding the other household's
    row without use_household writes it into household 1 and the isolation
    tests pass while proving nothing — which is how they failed the first
    time this file was run, and is worth the sentence.
    """
    write = tools.add_attention_item
    detail = detail or {"entry_id": 1, "ingredient": "Lettuce", "needs_amount_used": True}
    if household is None:
        return write("inventory_depletion", summary, detail)["id"]
    with tools.use_household(household):
        return write("inventory_depletion", summary, detail)["id"]


def _statuses():
    conn = get_conn()
    try:
        return [
            (r["household_id"], r["summary"], r["status"])
            for r in conn.execute(
                "SELECT household_id, summary, status FROM attention_items ORDER BY id"
            ).fetchall()
        ]
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# The read
# ---------------------------------------------------------------------------

def test_the_queue_comes_back_and_a_resolved_item_leaves_it(signed_in):
    item_id = _queue()
    items = signed_in.get("/api/attention").json()["items"]
    assert [i["summary"] for i in items if i["id"] == item_id] == ["Did you use the lettuce?"]

    res = signed_in.post(f"/api/attention/{item_id}/resolve", json={"status": "resolved"})
    assert res.status_code == 200
    assert item_id not in [i["id"] for i in res.json()["items"]]
    assert item_id not in [i["id"] for i in signed_in.get("/api/attention").json()["items"]]


def test_dismissed_leaves_the_queue_too(signed_in):
    """Two words for two different answers, and both mean 'stop asking'."""
    item_id = _queue()
    res = signed_in.post(f"/api/attention/{item_id}/resolve", json={"status": "dismissed"})
    assert res.status_code == 200
    assert item_id not in [i["id"] for i in res.json()["items"]]
    assert _statuses() == [(DEFAULT_HOUSEHOLD_ID, "Did you use the lettuce?", "dismissed")]


# ---------------------------------------------------------------------------
# Another household's id, on a route that takes one off the wire
# ---------------------------------------------------------------------------

def test_resolving_another_households_item_is_a_404_and_writes_nothing(client, other_household):
    """
    The id is whatever the caller sends. require_household_row is what stops
    it; this is the test that says so, which the route did not have.
    """
    _sign_in(client, OTHER_PASSPHRASE)
    theirs = _queue(summary="Their own question", household=other_household)

    _sign_in(client, "test-password")
    res = client.post(f"/api/attention/{theirs}/resolve", json={"status": "resolved"})
    assert res.status_code == 404
    assert _statuses() == [(other_household, "Their own question", "pending")]


def test_logging_usage_against_another_households_item_touches_nothing(client, other_household):
    """
    /use has no 404 branch of its own — it answers 200 and says it applied
    nothing. Either would be defensible; what matters is that the other
    household's row and their tracked food are both untouched.
    """
    _sign_in(client, OTHER_PASSPHRASE)
    with tools.use_household(other_household):
        tools.update_inventory(action="add", item="Lettuce", quantity="2 heads", location="fridge")
    theirs = _queue(summary="Their own question", household=other_household)

    _sign_in(client, "test-password")
    res = client.post(f"/api/attention/{theirs}/use", json={"amount_used": "1"})
    assert res.status_code == 200
    assert res.json()["result"]["applied"] is False
    assert _statuses() == [(other_household, "Their own question", "pending")]

    with tools.use_household(other_household):
        still = [r["quantity"] for r in tools.get_inventory() if r["item"] == "Lettuce"]
    assert still == ["2 heads"], "their lettuce must be exactly as they left it"


def test_the_update_in_resolve_is_belt_and_braces_and_nothing_can_see_it():
    """
    Recorded rather than left unmentioned: resolve_attention_item's UPDATE
    carries `AND household_id = ?` and REMOVING IT REDDENS NOTHING —
    measured, four mutations run over this file. require_household_row two
    lines above has already refused a foreign id, so the clause is
    defence in depth and no behavioural test can reach it.

    It should stay. It is the same "scoping is a property of the statement,
    not of whoever called it" rule the leftover-chain work is about, and a
    guard that happens to be unreachable today is exactly what a future
    caller resolving an id some other way would need. But a reader counting
    this file's mutation evidence should know which of the four statements
    it does not cover.
    """
    import inspect
    from app.tools import attention

    body = inspect.getsource(attention.resolve_attention_item)
    assert "require_household_row" in body
    assert "AND household_id = ?" in body


def test_the_queue_only_ever_shows_this_households_items(client, other_household):
    _sign_in(client, OTHER_PASSPHRASE)
    _queue(summary="Their own question", household=other_household)
    assert [i["summary"] for i in client.get("/api/attention").json()["items"]] == [
        "Their own question"
    ]

    _sign_in(client, "test-password")
    assert [i["summary"] for i in client.get("/api/attention").json()["items"]] == []


# ---------------------------------------------------------------------------
# Answering with an amount
# ---------------------------------------------------------------------------

def test_an_amount_comes_off_the_tracked_row_and_the_question_stops(signed_in):
    added = tools.update_inventory(action="add", item="Lettuce", quantity="2 heads", location="fridge")
    item_id = _queue(detail={
        "entry_id": 1, "ingredient": "Lettuce",
        "needs_amount_used": True, "candidate_item_id": added["item_id"],
    })

    res = signed_in.post(f"/api/attention/{item_id}/use", json={"amount_used": "1 head"})
    assert res.status_code == 200
    assert res.json()["result"]["applied"] is True
    assert item_id not in [i["id"] for i in res.json()["items"]]

    left = [r["quantity"] for r in tools.get_inventory() if r["item"] == "Lettuce"]
    assert left == ["1 head"], left


def test_answering_a_question_that_is_already_answered_changes_nothing_more(signed_in):
    """
    A second POST of the same answer — a retried request, a double tap —
    must not take the food off twice. The row is no longer pending, so the
    route says it applied nothing.
    """
    added = tools.update_inventory(action="add", item="Lettuce", quantity="2 heads", location="fridge")
    item_id = _queue(detail={
        "entry_id": 1, "ingredient": "Lettuce",
        "needs_amount_used": True, "candidate_item_id": added["item_id"],
    })
    signed_in.post(f"/api/attention/{item_id}/use", json={"amount_used": "1 head"})
    after_first = [r["quantity"] for r in tools.get_inventory() if r["item"] == "Lettuce"]

    res = signed_in.post(f"/api/attention/{item_id}/use", json={"amount_used": "1 head"})
    assert res.status_code == 200
    assert res.json()["result"]["applied"] is False
    assert [r["quantity"] for r in tools.get_inventory() if r["item"] == "Lettuce"] == after_first


def test_a_question_whose_tracked_row_has_gone_resolves_itself(signed_in):
    """
    The household tidied the fridge by hand between the question and the
    answer. Nothing to take off, and the question must not stay in the queue
    for ever asking about something that no longer exists.
    """
    added = tools.update_inventory(action="add", item="Lettuce", quantity="2 heads", location="fridge")
    item_id = _queue(detail={
        "entry_id": 1, "ingredient": "Lettuce",
        "needs_amount_used": True, "candidate_item_id": added["item_id"],
    })
    tools.update_inventory(action="remove", item="Lettuce")

    res = signed_in.post(f"/api/attention/{item_id}/use", json={"amount_used": "1 head"})
    assert res.json()["result"]["applied"] is False
    assert item_id not in [i["id"] for i in res.json()["items"]]


# ---------------------------------------------------------------------------
# The gap, characterised rather than fixed
# ---------------------------------------------------------------------------

def test_the_chat_tool_can_only_ever_send_one_of_the_two_words():
    """
    The assistant is constrained by the tool schema, so the loose path below
    is the HTTP route and only the HTTP route. Worth pinning separately: if
    the enum is ever dropped, the route's own gap stops being the only way in.
    """
    from app import agent

    schema = next(
        t for t in agent.TOOL_DEFINITIONS if t["name"] == "resolve_attention_item"
    )["input_schema"]
    assert schema["properties"]["status"]["enum"] == ["resolved", "dismissed"]


def test_the_route_takes_no_third_word(signed_in):
    """
    INVERTED 2026-09-25, and the sentence above it is the point: this test
    used to assert a 200 and a row reading `banana`. It was written as a
    characterisation with "invert this when the card is done" in its own
    docstring, and this is that.

    schema.sql documents the column as `pending | resolved | dismissed` and
    every reader filters on `status = 'pending'`, so a third word used to
    take the row out of the queue like a real answer while recording
    something no screen has a name for. It refuses now, the row is left
    exactly where it was, and the question is still waiting to be answered.
    """
    item_id = _queue()
    res = signed_in.post(f"/api/attention/{item_id}/resolve", json={"status": "banana"})
    assert res.status_code == 422
    assert _statuses() == [(DEFAULT_HOUSEHOLD_ID, "Did you use the lettuce?", "pending")]
    assert item_id in [i["id"] for i in signed_in.get("/api/attention").json()["items"]]
