"""
Answering one of Pomona's queued questions takes one of the two answers it
understands, and nothing else.

The fourth instance of one class, fixed the way the other three were:
cooker.InvalidMealStatus (2026-09-16), chores.InvalidChoreStatus and
grocery.InvalidGroceryStatus. A marker exception that IS a ValueError
subclass, checked above get_conn, and a route whose except for it sits
BEFORE the plain ValueError that means "no such row" — ordering is
load-bearing, not tidiness, and there is a test on it below.

Measured before the guard, through the real tool on a throwaway database:
resolve_attention_item(id, "banana") returned {"status": "banana"}, the row
on disk read `banana`, and get_attention_items() came back empty — so the
question left the queue looking answered while recording a word no screen
has a name for.

WHAT IS RED ON MAIN, measured rather than claimed: 7 of the 9 here, and
that number is worth much less than it looks. Only TWO of them reach the
assertion they are named for (a 200 where a 422 belongs); the other five
die on `AttributeError: module 'app.tools' has no attribute
'InvalidAttentionStatus'` — the only kind of red a test of a new symbol can
have, and not evidence of anything. Each says which it is. The two green
ones name the mutation that pins them instead, because a guard nothing can
redden is a sentence rather than a test.
"""
from __future__ import annotations

import pytest

from app import tools
from app.db import get_conn
from app.tools import attention as _attention

# `signed_in` and `client` come from tests/conftest.py, like every other
# route test in this suite.


def _queue(summary="Did you use the lettuce?"):
    return tools.add_attention_item(
        "use_soon",
        summary,
        {"entry_id": 1, "ingredient": "Lettuce", "needs_amount_used": True},
    )["id"]


def _status(item_id):
    conn = get_conn()
    row = conn.execute("SELECT status FROM attention_items WHERE id = ?", (item_id,)).fetchone()
    conn.close()
    return row["status"]


# ---------------------------------------------------------------- the guard


def test_a_word_that_is_not_an_answer_is_refused_and_nothing_is_written():
    """
    Red on main for the right underlying reason — it really does return
    {"status": "banana"} there and the row really does read banana — but it
    dies on the missing name before it reaches that, so read it as a GUARD
    pinned by mutation rather than as a catch. Deleting the `if status not
    in` block reddens it.

    The question has to still be WAITING afterwards: a refusal that had
    already taken the row out of the queue would be the bug wearing a
    different status code.
    """
    item_id = _queue()
    with pytest.raises(tools.InvalidAttentionStatus):
        tools.resolve_attention_item(item_id, "banana")
    assert _status(item_id) == "pending"
    assert item_id in [i["id"] for i in tools.get_attention_items()]


def test_the_route_answers_422_not_404(signed_in):
    """
    CATCH — red on main on its own assertion (200 where 422 belongs), which
    is what makes it one. 422 rather than 400 for the cooked tick's own
    reason: pydantic already answers 422 for a non-string
    status here, so 400 would give one client mistake two codes.
    """
    item_id = _queue()
    res = signed_in.post(f"/api/attention/{item_id}/resolve", json={"status": "banana"})
    assert res.status_code == 422
    assert "resolved" in res.json()["detail"] and "dismissed" in res.json()["detail"]


def test_a_bad_status_and_a_missing_row_get_different_codes(signed_in):
    """
    CATCH — red on main on its own assertion, where the first of these is a
    200.

    The two mistakes are asked about in one breath because the ONLY thing
    keeping them apart is the order of two except clauses over a type that
    is a subclass of the other's. Swap them and this goes red while every
    other test in the file stays green.
    """
    item_id = _queue()
    bad_status = signed_in.post(f"/api/attention/{item_id}/resolve", json={"status": "banana"})
    no_such_row = signed_in.post("/api/attention/9999/resolve", json={"status": "resolved"})
    assert (bad_status.status_code, no_such_row.status_code) == (422, 404)


def test_the_check_runs_before_a_connection_is_opened():
    """
    GUARD. It is red on main, but only on the missing name, so the mutation
    is what pins it: move the check below get_conn and this goes red. It
    matters for two reasons the grocery sibling already records — a
    bad status never takes the write lock, and the raise would skip
    conn.close() and leak the connection.
    """
    opened = []
    real = _attention.get_conn

    def counting():
        opened.append(1)
        return real()

    _attention.get_conn = counting
    try:
        with pytest.raises(tools.InvalidAttentionStatus):
            tools.resolve_attention_item(1, "banana")
    finally:
        _attention.get_conn = real
    assert opened == [], "a word that is not an answer must not open a connection"


# ------------------------------------------------- what must NOT have moved


@pytest.mark.parametrize("status", ["resolved", "dismissed"])
def test_both_real_answers_still_work(signed_in, status):
    """
    GUARD — green on main, and the thing a too-tight guard would break.
    Pinned by the mutation that narrows ATTENTION_STATUSES to one word.
    """
    item_id = _queue()
    res = signed_in.post(f"/api/attention/{item_id}/resolve", json={"status": status})
    assert res.status_code == 200
    assert _status(item_id) == status
    assert item_id not in [i["id"] for i in res.json()["items"]]


def test_the_default_answer_is_resolved(signed_in):
    """
    GUARD. The route's model defaults to "resolved", so a body of {} must
    still settle the item rather than be refused by the new check.
    """
    item_id = _queue()
    assert signed_in.post(f"/api/attention/{item_id}/resolve", json={}).status_code == 200
    assert _status(item_id) == "resolved"


def test_pending_is_not_an_answer_this_door_gives():
    """
    GUARD — red on main only on the missing name. The judgement call in it
    is worth stating out loud.

    schema.sql documents three values and this door offers two. `pending` is
    add_attention_item's to write — its reopen path sets the status back and
    clears resolved_at in the SAME statement, while this function stamps
    resolved_at unconditionally. So a 'pending' allowed through here would
    leave a waiting question carrying the time it was answered. Same
    reasoning as GROCERY_SHOPPER_STATUSES being the shopper's three rather
    than every value that column takes.
    """
    item_id = _queue()
    tools.resolve_attention_item(item_id, "resolved")
    with pytest.raises(tools.InvalidAttentionStatus):
        tools.resolve_attention_item(item_id, "pending")
    assert _status(item_id) == "resolved"


def test_the_three_internal_callers_all_pass_a_real_answer():
    """
    GUARD on the blast radius, read off the source rather than asserted.
    Red on main only on the missing name.

    record_attention_item_usage resolves the item on three separate paths
    (applied, no candidate row, no such row) and every one of them must go
    on working — a guard that refused one of its own callers would be a
    worse bug than the one being fixed.
    """
    import inspect

    src = inspect.getsource(_attention)
    calls = [
        line.strip()
        for line in src.splitlines()
        if "resolve_attention_item(" in line and "def " not in line
    ]
    assert len(calls) == 3, f"expected three internal calls, found {calls}"
    for call in calls:
        assert any(f'"{s}"' in call for s in tools.ATTENTION_STATUSES), call


def test_the_chat_tools_schema_still_enumerates_exactly_these_two():
    """
    GUARD, red on main only on the missing name. The enum in agent.py and
    ATTENTION_STATUSES are two statements of
    one rule, and this repo's named recurring bug generator is two
    implementations of one rule drifting. If the enum is widened, the tool
    starts producing a word the function refuses — a dead end for the model
    rather than a garbage row, which is the better failure, but still one
    somebody should mean to create.
    """
    from app import agent

    schema = next(
        t for t in agent.TOOL_DEFINITIONS if t["name"] == "resolve_attention_item"
    )
    enum = schema["input_schema"]["properties"]["status"]["enum"]
    assert sorted(enum) == sorted(tools.ATTENTION_STATUSES)
