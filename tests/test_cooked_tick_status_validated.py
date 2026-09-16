"""
A meal is cooked or it isn't — the tick takes no third word.

THE BUG (found 2026-09-15 by the verifier of qa-times-cooked-at-cook,
pre-existing). `POST /api/cooker/check-meal` with `{"status": "skipped"}`
answered 200 and wrote `cooked_status = 'skipped'` straight into
meal_plan_entries. The chat tool's own schema has enumerated
`["pending", "done"]` since it shipped; the HTTP model never did, and
`check_off_meal` took whatever it was handed.

Nothing crashed, which is what made it worth a card rather than a shrug:
the row simply landed in a status no screen's WHERE clause looks for —
not done, not pending, just gone from both. The counter code added
alongside it handles the third value safely (no bump, no negative), so
the damage is invisible until somebody asks why a meal is on no list.

THE SHAPE OF THE FIX is chores.InvalidChoreStatus's, one door over: a
marker exception that is a sibling of require_household_row's plain
ValueError rather than the same thing, so the route can answer 422
("that request doesn't make sense") instead of the 404 that means "no
such meal". The check is in `check_off_meal`, before any connection is
opened, because chat reaches that function without going through the
route at all.

**422, not the 400 the card asked for** — a deliberate deviation, flagged
for Emily. `/api/chores/{id}/status` already answers 422 for exactly this
mistake, with its reasoning written down at the raise site, and two
different answers to one question is the thing this codebase keeps
getting bitten by. One line in app/main.py to change if she disagrees.

Each test says in its own docstring whether it is a CATCH (red before the
fix) or a NO-REGRESSION GUARD. Measured, not assumed: against an
unmodified app/ this file is 13 red / 6 green — but ONE of the 13 is red
only because it names a constant the fix introduces (AttributeError, not
a failed assertion) and says so itself. So: 12 behaviour catches, 6
guards, and 1 pinned by mutation.
"""
from __future__ import annotations

import pytest

from app import tools
from app.db import get_conn


def _seed() -> int:
    """A household with one planned dinner, and its entry id."""
    tools.add_member("Alex")
    tools.add_recipe(
        "Bean Chili",
        ingredients=[{"item": "Black Beans", "qty": "2 cans"}],
        prep_time_minutes=10,
        cook_time_minutes=40,
        default_servings=2,
    )
    plan_id = tools.create_weekly_plan("2026-09-14")["weekly_plan_id"]
    tools.plan_meal("2026-09-16", "Bean Chili", slot="dinner", weekly_plan_id=plan_id)
    conn = get_conn()
    row = conn.execute(
        "SELECT id FROM meal_plan_entries WHERE household_id = ? AND date = ?",
        (tools.household_id(), "2026-09-16"),
    ).fetchone()
    conn.close()
    return row["id"]


def _status(entry_id: int) -> str | None:
    conn = get_conn()
    row = conn.execute(
        "SELECT cooked_status FROM meal_plan_entries WHERE id = ?", (entry_id,)
    ).fetchone()
    conn.close()
    return row["cooked_status"]


# ---------- the tool ----------

def test_a_third_status_word_is_refused():
    """CATCH. The reported case: 'skipped' is not a thing a meal can be."""
    entry_id = _seed()
    with pytest.raises(tools.InvalidMealStatus):
        tools.check_off_meal(entry_id, "skipped")


def test_a_refused_status_writes_nothing():
    """
    CATCH, and the one that matters. The row must read exactly as it did
    before — the bug was not the missing error, it was the write.
    """
    entry_id = _seed()
    before = _status(entry_id)
    with pytest.raises(tools.InvalidMealStatus):
        tools.check_off_meal(entry_id, "skipped")
    assert _status(entry_id) == before
    assert _status(entry_id) not in ("skipped",)


@pytest.mark.parametrize("bad", ["", "Done", "DONE", "cooked", "true", "1", "none"])
def test_near_misses_are_refused_too(bad):
    """
    CATCH. Case and near-synonyms are the realistic client bugs, and every
    one of them wrote through before. 'Done' is refused on purpose: the
    column is compared by exact string everywhere that reads it.
    """
    entry_id = _seed()
    with pytest.raises(tools.InvalidMealStatus):
        tools.check_off_meal(entry_id, bad)
    assert _status(entry_id) != bad


def test_the_refusal_is_a_sentence_naming_what_is_allowed():
    """CATCH. A client bug is still read by a person; say the two words."""
    entry_id = _seed()
    with pytest.raises(tools.InvalidMealStatus) as caught:
        tools.check_off_meal(entry_id, "skipped")
    said = str(caught.value)
    assert "done" in said and "pending" in said
    assert "skipped" in said  # says what it was given back to them


def test_the_status_is_checked_before_the_row_is_looked_up():
    """
    CATCH. A bad status on a meal that doesn't exist is a bad status, not
    a 404 — so the guard has to run before the lookup. Pins the ORDER,
    which is what decides which status code the route can answer.
    """
    with pytest.raises(tools.InvalidMealStatus):
        tools.check_off_meal(999999, "skipped")


# ---------- the route ----------

def test_the_route_answers_422_with_a_plain_line(signed_in):
    """CATCH. Was 200 with the write done."""
    entry_id = _seed()
    res = signed_in.post(
        "/api/cooker/check-meal", json={"entry_id": entry_id, "status": "skipped"}
    )
    assert res.status_code == 422
    detail = res.json()["detail"]
    assert isinstance(detail, str), "a plain line, not pydantic's field-error list"
    assert "done" in detail and "pending" in detail
    assert _status(entry_id) != "skipped"


def test_a_missing_meal_is_still_a_404_not_a_422(signed_in):
    """
    NO-REGRESSION GUARD. The new except must not swallow the old one:
    "no such meal" and "no such status" are different answers, and the
    marker type exists precisely to keep them apart.
    """
    res = signed_in.post(
        "/api/cooker/check-meal", json={"entry_id": 999999, "status": "done"}
    )
    assert res.status_code == 404


# ---------- what must not move ----------

@pytest.mark.parametrize("good", ["done", "pending"])
def test_the_two_real_statuses_still_work(good):
    """NO-REGRESSION GUARD. The tick and the untick are the whole feature."""
    entry_id = _seed()
    tools.check_off_meal(entry_id, good)
    assert _status(entry_id) == good


def test_the_default_is_still_done():
    """NO-REGRESSION GUARD. check_off_meal(entry_id) means 'cooked'."""
    entry_id = _seed()
    tools.check_off_meal(entry_id)
    assert _status(entry_id) == "done"


def test_the_toggle_still_goes_both_ways(signed_in):
    """
    NO-REGRESSION GUARD, over the real route. The cook checkbox posts
    whatever data-next says, so tick -> untick -> tick is the ordinary
    path and the guard must be invisible on it.
    """
    entry_id = _seed()
    for expected in ("done", "pending", "done"):
        res = signed_in.post(
            "/api/cooker/check-meal", json={"entry_id": entry_id, "status": expected}
        )
        assert res.status_code == 200
        assert _status(entry_id) == expected


def test_todays_tick_still_dispatches_here():
    """
    NO-REGRESSION GUARD. moves.set_move_done sends a cook tick through
    check_off_meal, computing its status as done/pending — so the guard
    must never fire on Now's own tick. Pins that the two agree about the
    allowed words rather than assuming it.
    """
    entry_id = _seed()
    from app.tools import moves as _moves

    for done, expected in ((True, "done"), (False, "pending")):
        _moves.set_move_done(f"cook:{entry_id}", done)
        assert _status(entry_id) == expected


def test_the_allowed_words_are_the_ones_chat_is_offered():
    """
    RED ON MAIN, BUT NOT A BEHAVIOUR CATCH — it dies on AttributeError
    there, because tools.MEAL_COOKED_STATUSES is itself the change. What
    it really guards is the two lists agreeing from here on: the chat
    schema enumerated done/pending long before the HTTP side did, and
    this fix exists to make them one answer. Pinned by mutation instead:
    adding a third word to MEAL_COOKED_STATUSES without adding it to the
    schema reddens it. (Measured: that mutation reddens 6 of the 19 — this
    one is the only one that fails ON the disagreement rather than on the
    third word now being accepted.)
    """
    from app import agent

    schema = next(
        t for t in agent.TOOL_DEFINITIONS if t["name"] == "check_off_meal"
    )
    enumerated = schema["input_schema"]["properties"]["status"]["enum"]
    assert set(enumerated) == set(tools.MEAL_COOKED_STATUSES)
