"""
Chores v1: Skip, swap, or "not this week".

Loop Board, Phase 2. As one of the adults, say "not this week" or "can you
take the bins tonight?" without editing the chore itself, so real life can
bend the plan for a day without anyone re-doing the setup.

Three verbs, two doors each. The ⋯ on a chore row (Now's card and Plan |
Chores, one builder, so one menu) posts to three id-keyed routes; the ask
sheet says the same three things by chore NAME. The write underneath is
one function per verb — resolution is chat's problem and only chat's, so
that is the half the by-name tools own and the routes skip.

Back end over the real routes and the real tools.*; front end runs
shell.js's own builders and handlers under node (tests/nodeharness.py),
because "the row updates before the server answers and goes back if the
save fails" is behaviour a source-marker test cannot see.

39 of these 41 fail on `main` (0d359e5). The two that pass say so in
their own docstrings: both are no-regression guards, one on the by-name
tools surviving the write being lifted out from under them, one on the
switched-off read not growing a field.
"""
from __future__ import annotations

import datetime
import inspect
import json
import re
import shutil
from pathlib import Path

import nodeharness
import pytest

from app import agent, households, tools
from app.db import get_conn

REPO = Path(__file__).resolve().parent.parent
SHELL_JS = (REPO / "static" / "shell.js").read_text(encoding="utf-8")
SHELL_CSS = (REPO / "static" / "shell.css").read_text(encoding="utf-8")

TODAY = datetime.date.today()


def _d(days: int) -> str:
    return (TODAY + datetime.timedelta(days=days)).isoformat()


def _adult(name: str) -> int:
    member_id = tools.add_member(name)["member_id"]
    tools.set_member_age_group(name, "Adult")
    return member_id


def _house_on():
    emily = _adult("Emily")
    vineeth = _adult("Vineeth")
    tools.set_chores_enabled(True)
    return emily, vineeth


def _label(iso: str) -> str:
    """The chip/toast word for a day — the Python twin of choreDayLabel,
    only for the days these tests actually name (all inside the week)."""
    today = TODAY
    day = datetime.date.fromisoformat(iso)
    if day == today:
        return "Today"
    if day == today + datetime.timedelta(days=1):
        return "Tomorrow"
    return day.strftime("%a")


def _row(instance_id: int) -> dict:
    conn = get_conn()
    r = conn.execute(
        "SELECT status, due_date, assignee_id, completed_by_member_id "
        "FROM chore_instances WHERE id = ?", (instance_id,)
    ).fetchone()
    conn.close()
    return dict(r)


# ==========================================================================
# 1. Skip — not done, not missed, and the clock does not reset
# ==========================================================================

def test_skip_by_id_marks_it_skipped_and_credits_nobody():
    _house_on()
    tools.add_chore("Bathrooms", frequency="weekly", owner_name="Emily")
    inst = tools.schedule_chore_instance("Bathrooms", _d(0))["instance_id"]

    result = tools.skip_chore_instance(inst)

    assert result["status"] == "skipped" and result["chore"] == "Bathrooms"
    row = _row(inst)
    assert row["status"] == "skipped"
    assert row["completed_by_member_id"] is None, "a skip is nobody's to be credited with"


def test_skip_does_not_move_the_rhythm_and_leaves_the_next_one_where_it_was():
    """
    The whole difference between a skip and a tick: nobody did the work, so
    there is no day for the next occurrence to count from. The bathroom is
    still twelve days since it was cleaned.
    """
    _house_on()
    tools.add_chore("Bathrooms", frequency="weekly", owner_name="Emily")
    due = tools.schedule_chore_instance("Bathrooms", _d(0))["instance_id"]
    nxt = tools.schedule_chore_instance("Bathrooms", _d(7))["instance_id"]

    tools.skip_chore_instance(due)

    assert _row(nxt)["due_date"] == _d(7), "the next occurrence stays exactly where it was"
    assert _row(nxt)["status"] == "pending"
    conn = get_conn()
    rows = conn.execute(
        "SELECT COUNT(*) AS n FROM chore_instances WHERE status = 'pending'"
    ).fetchone()["n"]
    conn.close()
    assert rows == 1, "a skip writes no new occurrence — only a tick refills the schedule"


def test_skipping_the_row_on_screen_takes_the_whole_pile_with_it():
    """
    The row a household taps IS _collapse_outstanding's representative, so
    skipping only that one would put the chore straight back as due on the
    next read — the guilt pile in a different hat.
    """
    _house_on()
    tools.add_chore("Mop", frequency="weekly", owner_name="Emily")
    for back in (21, 14, 7, 0):
        tools.schedule_chore_instance("Mop", _d(-back))
    shown = tools.get_chores_due_today()
    assert len(shown) == 1 and shown[0]["stands_for"] == 4

    result = tools.skip_chore_instance(shown[0]["id"])

    assert result["also_cleared"] == 3
    assert tools.get_chores_due_today() == []


def test_skipping_one_that_is_genuinely_ahead_reaches_nothing_behind_it():
    _house_on()
    tools.add_chore("Gutters", frequency="quarterly", owner_name="Emily")
    ahead = tools.schedule_chore_instance("Gutters", _d(30))["instance_id"]
    tools.add_chore("Bins", frequency="weekly", owner_name="Emily")
    other = tools.schedule_chore_instance("Bins", _d(0))["instance_id"]

    result = tools.skip_chore_instance(ahead)

    assert result["also_cleared"] == 0
    assert _row(other)["status"] == "pending", "another chore's row is never swept"


def test_skipping_a_whoever_chore_behaves_the_same():
    _house_on()
    tools.add_chore("Wipe counters", frequency="daily", mode="whoever")
    inst = tools.schedule_chore_instance("Wipe counters", _d(0))["instance_id"]
    assert _row(inst)["assignee_id"] is None

    assert tools.skip_chore_instance(inst)["status"] == "skipped"
    assert _row(inst)["status"] == "skipped"


def test_skip_refuses_a_row_that_is_already_settled():
    _house_on()
    tools.add_chore("Bins", frequency="weekly", owner_name="Emily")
    inst = tools.schedule_chore_instance("Bins", _d(0))["instance_id"]
    tools.complete_chore(inst)
    with pytest.raises(ValueError, match="nothing to skip"):
        tools.skip_chore_instance(inst)


def test_skip_by_id_is_scoped_to_its_own_household():
    _house_on()
    tools.add_chore("Bins", frequency="weekly", owner_name="Emily")
    other = households.create_household("The Testers", "correct-horse-battery-staple")
    with tools.use_household(other):
        tools.add_member("Sam")
        tools.set_member_age_group("Sam", "Adult")
        tools.add_chore("Bins", frequency="weekly", owner_name="Sam")
        theirs = tools.schedule_chore_instance("Bins", _d(0))["instance_id"]

    with pytest.raises(ValueError):
        tools.skip_chore_instance(theirs)
    assert _row(theirs)["status"] == "pending"


# ==========================================================================
# 2. Hand to — this one occurrence, and nothing about whose chore it is
# ==========================================================================

def test_hand_changes_this_occurrence_only_and_leaves_the_owner_alone():
    emily, vineeth = _house_on()
    tools.add_chore("Bins", frequency="weekly", owner_name="Emily")
    this_one = tools.schedule_chore_instance("Bins", _d(0))["instance_id"]
    next_one = tools.schedule_chore_instance("Bins", _d(7))["instance_id"]

    result = tools.hand_chore_instance(this_one, "Vineeth")

    assert result["handed_over"] is True and result["who_label"] == "Vineeth"
    assert _row(this_one)["assignee_id"] == vineeth
    assert _row(next_one)["assignee_id"] == emily, "next week is still whoever's it always was"
    chore = tools.list_chore_definitions()[0]
    assert chore["owner"] == "Emily" and chore["mode"] == "owned"
    # The RAW columns, because the reported `owner` cannot see this leak:
    # _rotation_ids reads rotation_member_ids_json first and only falls
    # back to default_assignee_id when that JSON is empty, which
    # add_chore(owner_name=...) never leaves it. A mutation that also
    # wrote default_assignee_id passed the line above.
    conn = get_conn()
    raw = conn.execute(
        "SELECT default_assignee_id, rotation_member_ids_json, mode FROM chores WHERE id = ?",
        (chore["id"],)
    ).fetchone()
    conn.close()
    assert raw["default_assignee_id"] == emily
    assert json.loads(raw["rotation_member_ids_json"] or "[]") == [emily]
    assert raw["mode"] == "owned"


def test_hand_never_creates_a_person():
    """A typo must not become a household member — the "Vinneth" failure."""
    _house_on()
    tools.add_chore("Bins", frequency="weekly", owner_name="Emily")
    inst = tools.schedule_chore_instance("Bins", _d(0))["instance_id"]
    before = len(tools.get_household_people())

    with pytest.raises(ValueError, match="I don't know anyone called Vinneth"):
        tools.hand_chore_instance(inst, "Vinneth")

    assert len(tools.get_household_people()) == before
    assert _row(inst)["assignee_id"] is not None


def test_hand_refuses_an_outsourced_chore():
    """Nobody here does it, so there is nobody here to hand it to; making it
    somebody's is update_chore's job, done out loud."""
    _house_on()
    tools.add_chore("Deep clean", frequency="weekly", mode="outsourced", outsourced_to="Maria")
    inst = tools.schedule_chore_instance("Deep clean", _d(0))["instance_id"]
    with pytest.raises(ValueError, match="nobody here to hand it to"):
        tools.hand_chore_instance(inst, "Emily")
    assert _row(inst)["assignee_id"] is None


def test_hand_refuses_a_done_occurrence():
    _house_on()
    tools.add_chore("Bins", frequency="weekly", owner_name="Emily")
    inst = tools.schedule_chore_instance("Bins", _d(0))["instance_id"]
    tools.complete_chore(inst)
    with pytest.raises(ValueError, match="nothing to hand over"):
        tools.hand_chore_instance(inst, "Vineeth")


def test_a_hand_over_leaves_the_fairness_record_to_whoever_ticks_it():
    """assignee_id says who it is FOR; completed_by_member_id says who did
    it. A hand-over writes the first and never the second."""
    emily, vineeth = _house_on()
    tools.add_chore("Bins", frequency="weekly", owner_name="Emily")
    inst = tools.schedule_chore_instance("Bins", _d(0))["instance_id"]
    tools.hand_chore_instance(inst, "Vineeth")
    assert _row(inst)["completed_by_member_id"] is None

    tools.complete_chore(inst, done_by="Vineeth")
    assert _row(inst)["completed_by_member_id"] == vineeth
    assert _row(inst)["assignee_id"] == vineeth


def test_hand_is_scoped_to_its_own_household():
    _house_on()
    other = households.create_household("The Testers", "correct-horse-battery-staple")
    with tools.use_household(other):
        tools.add_member("Sam")
        tools.set_member_age_group("Sam", "Adult")
        tools.add_chore("Bins", frequency="weekly", owner_name="Sam")
        theirs = tools.schedule_chore_instance("Bins", _d(0))["instance_id"]
        sam = _row(theirs)["assignee_id"]

    with pytest.raises(ValueError):
        tools.hand_chore_instance(theirs, "Emily")
    assert _row(theirs)["assignee_id"] == sam


# ==========================================================================
# 3. Move — this occurrence's day, and nothing else
# ==========================================================================

def test_move_by_id_re_dates_in_place():
    emily, _ = _house_on()
    tools.add_chore("Hoover", frequency="weekly", owner_name="Emily")
    inst = tools.schedule_chore_instance("Hoover", _d(0))["instance_id"]

    result = tools.move_chore_instance(inst, _d(3))

    assert result["moved"] is True and result["instance_id"] == inst
    assert _row(inst)["due_date"] == _d(3)
    assert _row(inst)["assignee_id"] == emily, "a move is about the day and nothing else"


def test_move_refuses_to_double_a_chore_up_on_one_day():
    _house_on()
    tools.add_chore("Hoover", frequency="weekly", owner_name="Emily")
    a = tools.schedule_chore_instance("Hoover", _d(0))["instance_id"]
    tools.schedule_chore_instance("Hoover", _d(3))
    with pytest.raises(ValueError, match="already on"):
        tools.move_chore_instance(a, _d(3))
    assert _row(a)["due_date"] == _d(0)


def test_move_only_touches_a_pending_occurrence():
    _house_on()
    tools.add_chore("Hoover", frequency="weekly", owner_name="Emily")
    inst = tools.schedule_chore_instance("Hoover", _d(0))["instance_id"]
    tools.complete_chore(inst)
    with pytest.raises(ValueError, match="nothing to move"):
        tools.move_chore_instance(inst, _d(2))


def test_move_by_id_is_scoped_to_its_own_household():
    _house_on()
    other = households.create_household("The Testers", "correct-horse-battery-staple")
    with tools.use_household(other):
        tools.add_member("Sam")
        tools.set_member_age_group("Sam", "Adult")
        tools.add_chore("Bins", frequency="weekly", owner_name="Sam")
        theirs = tools.schedule_chore_instance("Bins", _d(0))["instance_id"]

    with pytest.raises(ValueError):
        tools.move_chore_instance(theirs, _d(4))
    assert _row(theirs)["due_date"] == _d(0)


def test_move_refuses_a_date_before_today():
    """
    Defect hunt, 2026-09-13: nothing stopped "move Hoover to 2020" (a
    fat-fingered year, not a real request) from writing a due_date in
    the past. Refuses with a plain sentence, the same ChoreRefused shape
    as the duplicate-day refusal above, and changes nothing.
    """
    _house_on()
    tools.add_chore("Hoover", frequency="weekly", owner_name="Emily")
    inst = tools.schedule_chore_instance("Hoover", _d(0))["instance_id"]

    with pytest.raises(tools.ChoreRefused, match="already happened"):
        tools.move_chore_instance(inst, "2020-01-01")
    assert _row(inst)["due_date"] == _d(0)


def test_move_to_today_still_works():
    """Today itself is not "the past" — pulling a future chore in to
    today must keep working."""
    _house_on()
    tools.add_chore("Hoover", frequency="weekly", owner_name="Emily")
    inst = tools.schedule_chore_instance("Hoover", _d(5))["instance_id"]

    result = tools.move_chore_instance(inst, _d(0))
    assert result["moved"] is True
    assert _row(inst)["due_date"] == _d(0)


def test_move_by_name_also_refuses_a_past_date():
    """move_chore (the chat, by-name tool) writes through
    move_chore_instance, so the guard covers "push it to next month" (kept
    working) and a past date (refused) from the same call site."""
    _house_on()
    tools.add_chore("Hoover", frequency="weekly", owner_name="Emily")
    tools.schedule_chore_instance("Hoover", _d(0))

    with pytest.raises(tools.ChoreRefused, match="already happened"):
        tools.move_chore("Hoover", "2020-06-01")


def test_the_move_route_refuses_a_past_date_as_a_sentence(signed_in):
    _house_on()
    tools.add_chore("Hoover", frequency="weekly", owner_name="Emily")
    inst = tools.schedule_chore_instance("Hoover", _d(0))["instance_id"]

    res = signed_in.post(f"/api/chores/{inst}/move", json={"due_date": "2020-01-01"})
    assert res.status_code == 200
    assert res.json()["status"] == "refused"
    assert "already happened" in res.json()["message"]
    assert _row(inst)["due_date"] == _d(0)


def test_the_by_name_tools_are_the_same_write_reached_by_name():
    """
    Resolution is the by-name tools' half; the write is one function per
    verb. skip_chore's due-one branch sweeps because skip_chore_instance
    does, and move_chore's refusals are move_chore_instance's.

    GREEN ON MAIN, and says so: this is the no-regression half — the two
    chat tools behaved exactly like this before the write was lifted out
    from under them, and the point is that they still do.
    """
    _house_on()
    tools.add_chore("Mop", frequency="weekly", owner_name="Emily")
    for back in (14, 7, 0):
        tools.schedule_chore_instance("Mop", _d(-back))
    assert tools.skip_chore("Mop")["also_cleared"] == 2

    tools.add_chore("Hoover", frequency="weekly", owner_name="Emily")
    a = tools.schedule_chore_instance("Hoover", _d(0))["instance_id"]
    tools.schedule_chore_instance("Hoover", _d(3))
    with pytest.raises(ValueError, match="already on"):
        tools.move_chore("Hoover", _d(3))
    assert _row(a)["due_date"] == _d(0)


# ==========================================================================
# 4. hand_chore — the same thing by saying so
# ==========================================================================

def test_hand_chore_by_name_takes_the_occurrence_due_now():
    emily, vineeth = _house_on()
    tools.add_chore("Bins", frequency="weekly", owner_name="Emily")
    due = tools.schedule_chore_instance("Bins", _d(-1))["instance_id"]
    ahead = tools.schedule_chore_instance("Bins", _d(6))["instance_id"]

    result = tools.hand_chore("Bins", "Vineeth")

    assert result["chore"] == "Bins" and result["instance_id"] == due
    assert _row(due)["assignee_id"] == vineeth
    assert _row(ahead)["assignee_id"] == emily


def test_hand_chore_by_name_can_pick_a_dated_occurrence():
    emily, vineeth = _house_on()
    tools.add_chore("Bins", frequency="weekly", owner_name="Emily")
    due = tools.schedule_chore_instance("Bins", _d(0))["instance_id"]
    ahead = tools.schedule_chore_instance("Bins", _d(7))["instance_id"]

    tools.hand_chore("Bins", "Vineeth", when=_d(7))

    assert _row(ahead)["assignee_id"] == vineeth
    assert _row(due)["assignee_id"] == emily


def test_hand_chore_says_so_plainly_when_there_is_nothing_coming_up():
    _house_on()
    tools.add_chore("Bins", frequency="weekly", owner_name="Emily")
    with pytest.raises(ValueError, match="no Bins coming up to hand over"):
        tools.hand_chore("Bins", "Vineeth")


def test_hand_chore_is_registered_everywhere_a_chores_tool_has_to_be():
    """The four places CLAUDE.md names — the re-export, TOOL_FUNCTIONS, the
    schema, and the per-household switch gate."""
    assert tools.hand_chore is not None
    assert agent.TOOL_FUNCTIONS["hand_chore"] is tools.hand_chore
    assert "hand_chore" in agent.CHORES_TOOLS
    schema = [d for d in agent.TOOL_DEFINITIONS if d["name"] == "hand_chore"]
    assert len(schema) == 1
    assert schema[0]["input_schema"]["required"] == ["chore_name", "to_person"]
    # Tagged `today`, so the shell refreshes the chores surfaces after it.
    from app import main as main_mod
    assert "hand_chore" in main_mod._CHORE_TOOLS


def test_hand_chore_runs_through_the_agents_own_dispatch(monkeypatch):
    import types as _types
    emily, vineeth = _house_on()
    tools.add_chore("Bins", frequency="weekly", owner_name="Emily")
    inst = tools.schedule_chore_instance("Bins", _d(0))["instance_id"]

    class _Usage:
        input_tokens = cache_read_input_tokens = cache_creation_input_tokens = output_tokens = 0

    monkeypatch.setattr(agent, "_client", lambda: _types.SimpleNamespace(messages=_types.SimpleNamespace(
        create=lambda **kw: _responses.pop(0))))
    _responses = [
        _types.SimpleNamespace(
            content=[_types.SimpleNamespace(type="tool_use", name="hand_chore", id="tu_1",
                                            input={"chore_name": "Bins", "to_person": "Vineeth"})],
            stop_reason="tool_use", usage=_Usage()),
        _types.SimpleNamespace(
            content=[_types.SimpleNamespace(type="text", text="Vineeth's got the bins this time.")],
            stop_reason="end_turn", usage=_Usage()),
    ]
    reply, conversation = agent.run_agent_turn([], "can Vineeth take the bins tonight?")
    handed = [m for m in conversation if m["role"] == "user" and isinstance(m["content"], list)][-1]["content"][0]
    assert not handed.get("is_error"), handed["content"]
    assert _row(inst)["assignee_id"] == vineeth
    assert reply == "Vineeth's got the bins this time."


# ==========================================================================
# 5. The three routes
# ==========================================================================

def test_the_routes_do_what_the_menu_says(signed_in):
    _house_on()
    tools.add_chore("Bins", frequency="weekly", owner_name="Emily")
    skip_me = tools.schedule_chore_instance("Bins", _d(0))["instance_id"]
    tools.add_chore("Hoover", frequency="weekly", owner_name="Emily")
    hand_me = tools.schedule_chore_instance("Hoover", _d(0))["instance_id"]
    tools.add_chore("Mop", frequency="weekly", owner_name="Emily")
    move_me = tools.schedule_chore_instance("Mop", _d(0))["instance_id"]

    assert signed_in.post(f"/api/chores/{skip_me}/skip").status_code == 200
    assert signed_in.post(f"/api/chores/{hand_me}/hand", json={"name": "Vineeth"}).status_code == 200
    assert signed_in.post(f"/api/chores/{move_me}/move", json={"due_date": _d(2)}).status_code == 200

    vineeth = [p["id"] for p in tools.chore_people() if p["first_name"] == "Vineeth"][0]
    assert _row(skip_me)["status"] == "skipped"
    assert _row(hand_me)["assignee_id"] == vineeth
    assert _row(move_me)["due_date"] == _d(2)


def test_a_refusal_comes_back_as_a_sentence_the_screen_can_show(signed_in):
    """
    weekly_plan.SlotRefused's own shape: a sentence written for a person
    is 200 {"status": "refused", "message"}, so the screen prints it
    instead of reporting the app broken for doing the right thing.
    """
    _house_on()
    tools.add_chore("Bins", frequency="weekly", owner_name="Emily")
    inst = tools.schedule_chore_instance("Bins", _d(0))["instance_id"]
    tools.schedule_chore_instance("Bins", _d(2))

    res = signed_in.post(f"/api/chores/{inst}/move", json={"due_date": _d(2)})
    assert res.status_code == 200
    assert res.json()["status"] == "refused" and "already on" in res.json()["message"]

    res = signed_in.post(f"/api/chores/{inst}/hand", json={"name": "Nobody"})
    assert res.status_code == 200
    assert "don't know anyone called Nobody" in res.json()["message"]
    assert "who should take it" in res.json()["message"]

    tools.set_chore_instance_status(inst, "done")
    res = signed_in.post(f"/api/chores/{inst}/skip")
    assert res.status_code == 200 and "nothing to skip" in res.json()["message"]
    # Nothing moved on any of the three.
    assert _row(inst)["due_date"] == _d(0)


def test_an_opaque_error_stays_a_404_and_never_reaches_the_household(signed_in):
    """The other half of the SlotRefused rule: require_household_row's
    "No chore instance with id 999." is not written for a reader, so it
    keeps the plain path and the screen keeps its own plain line."""
    _house_on()
    res = signed_in.post("/api/chores/999/skip")
    assert res.status_code == 404
    assert "status" not in res.json()


def test_an_unknown_status_is_refused_not_written(signed_in):
    """
    Defect hunt, 2026-09-13: set_chore_instance_status wrote any string
    it was handed straight to the column — a typo (or a hand-typed
    request) landed a row in a status no screen's WHERE clause was
    written to find: not 'pending', not 'done', just gone. The tool call
    itself must refuse before the route is even involved.
    """
    _house_on()
    tools.add_chore("Bins", frequency="weekly", owner_name="Emily")
    inst = tools.schedule_chore_instance("Bins", _d(0))["instance_id"]

    with pytest.raises(tools.InvalidChoreStatus, match="isn't a chore status"):
        tools.set_chore_instance_status(inst, "banana")
    assert _row(inst)["status"] == "pending", "the bad write must not land"


def test_the_status_route_answers_422_for_an_unknown_status(signed_in):
    _house_on()
    tools.add_chore("Bins", frequency="weekly", owner_name="Emily")
    inst = tools.schedule_chore_instance("Bins", _d(0))["instance_id"]

    res = signed_in.post(f"/api/chores/{inst}/status", json={"status": "banana"})
    assert res.status_code == 422
    assert _row(inst)["status"] == "pending"


def test_all_three_routes_refuse_while_the_switch_is_off(signed_in):
    _adult("Emily")
    tools.add_chore("Bins", frequency="weekly", owner_name="Emily")
    inst = tools.schedule_chore_instance("Bins", _d(0))["instance_id"]
    for path, body in (("skip", None), ("hand", {"name": "Emily"}), ("move", {"due_date": _d(1)})):
        res = signed_in.post(f"/api/chores/{inst}/{path}", json=body)
        assert res.status_code == 403, path
        assert res.json()["detail"] == tools.CHORES_OFF_MESSAGE
    assert _row(inst)["status"] == "pending" and _row(inst)["due_date"] == _d(0)


def test_the_reads_say_who_a_row_can_be_handed_to(signed_in):
    """The ⋯ must not cost a request to open, and it can't filter out
    whoever already has it from a name alone."""
    emily, vineeth = _house_on()
    tools.add_chore("Bins", frequency="weekly", owner_name="Emily")
    tools.schedule_chore_instance("Bins", _d(0))

    for url in ("/api/chores/today", "/api/chores/pending"):
        body = signed_in.get(url).json()
        assert [p["first_name"] for p in body["people"]] == ["Emily", "Vineeth"], url
        assert {p["id"] for p in body["people"]} == {emily, vineeth}
        assert body["chores"][0]["assignee_id"] == emily, url


def test_a_switched_off_read_says_nothing_about_the_household(signed_in):
    """GREEN ON MAIN, deliberately: `people` rides on the ENABLED answer
    only, so the switched-off one still says nothing about who lives
    here."""
    _adult("Emily")
    for url in ("/api/chores/today", "/api/chores/pending"):
        assert signed_in.get(url).json() == {"chores": [], "chores_set_up": False, "enabled": False}


# ==========================================================================
# 6. The ⋯ itself, under node
# ==========================================================================

_needs_node = pytest.mark.skipif(
    shutil.which("node") is None, reason="node is needed to execute the shell's own builders"
)


def _function(name: str) -> str:
    marker = "  async function %s(" % name
    if marker not in SHELL_JS:
        marker = "  function %s(" % name
    start = SHELL_JS.index(marker)
    end = SHELL_JS.index("\n  }\n", start)
    return SHELL_JS[start:end] + "\n  }\n"


def _var(name: str, end_marker: str = "\n  };\n") -> str:
    start = SHELL_JS.index("  var %s =" % name)
    end = SHELL_JS.index(end_marker, start)
    return SHELL_JS[start:end] + end_marker


def _node(script: str):
    res = nodeharness.run_node(script, timeout=30)
    assert res.returncode == 0, f"node failed: {res.stderr}"
    return json.loads(res.stdout.strip().splitlines()[-1])


def _menu() -> str:
    start = SHELL_JS.index("  // The ⋯ and what is behind it")
    end = SHELL_JS.index("  // ==========================================================================\n  // Grocery")
    return (SHELL_JS[start:end] + _function("todayLocalStr") + _function("addDaysLocal")
            + _function("dayNameShort") + _function("dayName"))


# A list element that reads its own markup back: the rows, and every button
# the ⋯ region wires, with attributes readable and handlers remembered.
_DOM = """
function escapeHtml(s){return String(s == null ? '' : s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');}
var TOASTS = [];
var UNDO = null;
function showToast(m, action) { TOASTS.push(m); UNDO = action && action.onClick ? action.onClick : null; }
var POSTS = [];
var FAIL_NEXT = false;
var REFUSE_NEXT = null;
function fetch(url, opts) {
  POSTS.push({ url: url, body: opts && opts.body ? JSON.parse(opts.body) : null });
  if (FAIL_NEXT) { FAIL_NEXT = false; return Promise.resolve({ ok: false }); }
  if (REFUSE_NEXT) {
    var msg = REFUSE_NEXT; REFUSE_NEXT = null;
    return Promise.resolve({ ok: true, json: function () {
      return Promise.resolve({ status: 'refused', message: msg }); } });
  }
  return Promise.resolve({ ok: true, json: function () { return Promise.resolve({ ok: true }); } });
}
var REFRESHED = [];
function makeScope() {
  var e = { innerHTML: '', acts: {}, handlers: {} };
  e.querySelectorAll = function (sel) {
    var out = [], m;
    if (sel === '[data-chore-act]') {
      var re = /<button[^>]*data-chore-act="[^"]*"[^>]*>/g;
      while ((m = re.exec(e.innerHTML)) !== null) {
        (function (tag) {
          var a = {}, ar = /([a-z-]+)="([^"]*)"/g, x;
          while ((x = ar.exec(tag)) !== null) a[x[1]] = x[2];
          var key = a['data-chore-act'] + ':' + a['data-id'] +
            (a['data-date'] ? ':' + a['data-date'] : '') +
            (a['data-person'] ? ':' + a['data-person'] : '');
          out.push({
            getAttribute: function (n) { return a[n] === undefined ? null : a[n]; },
            addEventListener: function (_t, fn) { e.acts[key] = fn; }
          });
        })(m[0]);
      }
    }
    return out;
  };
  return e;
}
// A surface: draws the rows with the shared builder and rewires each time,
// which is what both real ones do.
function makeSurface(rows, opts) {
  var scope = makeScope();
  var ctx = {
    rows: rows,
    today: !!(opts && opts.today),
    weekEnd: opts && opts.weekEnd,
    redraw: function () {
      // Now passes no day word; Plan passes planChoreWhen's — the two
      // real call sites, so the harness makes the same two calls.
      scope.innerHTML = rows.map(function (c) {
        return ctx.today ? choreRowHtml(c) : choreRowHtml(c, planChoreWhen(c));
      }).join('');
      wireChoreMenu(scope, ctx);
    },
    refresh: function () { REFRESHED.push(true); }
  };
  ctx.redraw();
  return { scope: scope, ctx: ctx };
}
function tick() { return Promise.resolve().then(function () {}).then(function () {}).then(function () {}); }
"""

_PEOPLE = [
    {"id": 1, "name": "Emily", "first_name": "Emily"},
    {"id": 2, "name": "Vineeth", "first_name": "Vineeth"},
]


def _rows():
    return [
        {"id": 11, "chore": "Bins", "status": "pending", "who_label": "Emily", "assignee_id": 1,
         "outsourced": False, "completable": True, "due_date": _d(0), "group": "today"},
        {"id": 12, "chore": "Kitchen floor", "status": "done", "who_label": "Vineeth", "assignee_id": 2,
         "outsourced": False, "completable": True, "due_date": _d(0), "group": "today"},
        {"id": 13, "chore": "Bathrooms", "status": "pending", "who_label": "Maria", "assignee_id": None,
         "outsourced": True, "completable": False, "due_date": _d(0), "group": "today"},
    ]


def _harness(body: str) -> str:
    # planChoreWhen rides along because it is where a skipped row's word
    # is decided (and where a guessed date would have shown, if there
    # were one) — the two are one claim.
    return _DOM + _var("TICK_ICON", ";\n") + _var("DOTS_ICON", ";\n") + _menu() \
        + _function("choreRowHtml") + _function("planChoreWhen") \
        + ("\nchorePeople = %s;\n" % json.dumps(_PEOPLE)) + body


@_needs_node
def test_the_row_carries_a_dots_button_and_a_done_or_outsourced_row_does_not():
    out = _node(_harness("""
var s = makeSurface(%s, { today: true });
console.log(JSON.stringify({ html: s.scope.innerHTML }));
""" % json.dumps(_rows())))
    html = out["html"]
    assert html.count('data-chore-act="menu"') == 1, "only the row still waiting to be done"
    assert 'data-id="11"' in html and 'aria-label="More for Bins"' in html
    # Nothing is open until it is tapped.
    assert 'aria-expanded="false"' in html and "chore-menu" not in html


@_needs_node
def test_the_dots_opens_exactly_three_things():
    out = _node(_harness("""
var s = makeSurface(%s, { today: true });
s.scope.acts['menu:11']();
console.log(JSON.stringify({ html: s.scope.innerHTML }));
""" % json.dumps(_rows())))
    html = out["html"]
    assert 'aria-expanded="true"' in html
    assert ">Skip this time<" in html
    assert ">Hand to Vineeth<" in html, "the other adult, named"
    assert "Emily" not in html[html.index("chore-menu"):], "never offered to whoever already has it"
    days = re.findall(r'data-chore-act="move" data-id="11" data-date="([^"]+)"', html)
    assert days == [_d(i) for i in range(1, 7)], "a week around its own day, minus the day it is on"
    assert re.search(r'>Tomorrow<', html)
    # EXACTLY three things, counted as things rather than as controls: the
    # move chips are one choice, however many days it takes to offer it.
    menu = html[html.index('<div class="chore-menu"'):]
    acts = re.findall(r'data-chore-act="(\w+)"', menu)
    assert sorted(set(acts)) == ["hand", "move", "skip"]
    assert acts.count("skip") == 1 and acts.count("hand") == 1
    assert menu.count("chore-act-label") == 1, "one label, over the days"
    # One accent-free menu: Rule 5 — Now's card has no apricot at all.
    assert "apricot" not in html and "btn-primary" not in html


@_needs_node
def test_only_one_menu_is_open_at_a_time():
    rows = _rows() + [{"id": 14, "chore": "Hoover", "status": "pending", "who_label": "Emily",
                       "assignee_id": 1, "outsourced": False, "completable": True,
                       "due_date": _d(2), "group": "week"}]
    out = _node(_harness("""
var s = makeSurface(%s, { today: false, weekEnd: '%s' });
s.scope.acts['menu:11']();
var first = (s.scope.innerHTML.match(/chore-menu/g) || []).length;
s.scope.acts['menu:14']();
var after = s.scope.innerHTML;
s.scope.acts['menu:14']();
console.log(JSON.stringify({ first: first, open14: after.indexOf('data-menu-for="14"') !== -1,
  open11: after.indexOf('data-menu-for="11"') !== -1,
  closed: s.scope.innerHTML.indexOf('chore-menu') === -1 }));
""" % (json.dumps(rows), _d(6))))
    assert out["first"] == 1 and out["open14"] and not out["open11"]
    assert out["closed"], "a second tap on the same ⋯ closes it"


@_needs_node
def test_skip_takes_the_row_off_before_the_server_answers():
    out = _node(_harness("""
var rows = %s;
var s = makeSurface(rows, { today: true });
s.scope.acts['menu:11']();
s.scope.acts['skip:11']();
var straightAway = { html: s.scope.innerHTML, posts: POSTS.slice(), left: rows.map(function (c) { return c.id; }) };
tick().then(function () {
  console.log(JSON.stringify({ straightAway: straightAway, refreshed: REFRESHED.length, toasts: TOASTS }));
});
""" % json.dumps(_rows())))
    assert out["straightAway"]["left"] == [12, 13], "gone from the list on the tap"
    assert "Bins" not in out["straightAway"]["html"]
    assert out["straightAway"]["posts"] == [{"url": "/api/chores/11/skip", "body": {}}]
    assert out["refreshed"] == 1
    assert out["toasts"] == ["Skipped Bins this time."], "a row that vanishes has to say why"


@_needs_node
def test_a_failed_skip_puts_the_row_back_where_it_was_and_says_so():
    out = _node(_harness("""
var rows = %s;
var s = makeSurface(rows, { today: true });
FAIL_NEXT = true;
s.scope.acts['menu:11']();
s.scope.acts['skip:11']();
tick().then(function () {
  console.log(JSON.stringify({ left: rows.map(function (c) { return c.id; }), status: rows[0].status,
    html: s.scope.innerHTML, toasts: TOASTS, refreshed: REFRESHED.length }));
});
""" % json.dumps(_rows())))
    assert out["left"] == [11, 12, 13], "back in its own place, not appended"
    assert out["status"] == "pending" and "Bins" in out["html"]
    assert out["toasts"] == ["That didn’t save. Try it again in a moment."]
    assert out["refreshed"] == 0


@_needs_node
def test_handing_it_over_renames_the_row_at_once_and_reverts_on_failure():
    out = _node(_harness("""
var rows = %s;
var s = makeSurface(rows, { today: true });
s.scope.acts['menu:11']();
s.scope.acts['hand:11:2']();
var straightAway = { who: rows[0].who_label, assignee: rows[0].assignee_id, posts: POSTS.slice() };
tick().then(function () {
  var rows2 = %s;
  var s2 = makeSurface(rows2, { today: true });
  FAIL_NEXT = true;
  s2.scope.acts['menu:11']();
  s2.scope.acts['hand:11:2']();
  tick().then(function () {
    console.log(JSON.stringify({ straightAway: straightAway,
      reverted: { who: rows2[0].who_label, assignee: rows2[0].assignee_id }, toasts: TOASTS }));
  });
});
""" % (json.dumps(_rows()), json.dumps(_rows()))))
    assert out["straightAway"]["who"] == "Vineeth" and out["straightAway"]["assignee"] == 2
    assert out["straightAway"]["posts"] == [{"url": "/api/chores/11/hand", "body": {"name": "Vineeth"}}]
    assert out["reverted"] == {"who": "Emily", "assignee": 1}
    assert out["toasts"] == ["Vineeth has Bins this time.",
                             "That didn’t save. Try it again in a moment."]


@_needs_node
def test_a_move_off_today_leaves_nows_card_and_changes_heading_on_plan():
    out = _node(_harness("""
var now = %s;
var nowSurface = makeSurface(now, { today: true });
nowSurface.scope.acts['menu:11']();
nowSurface.scope.acts['move:11:%s']();
var plan = %s;
var planSurface = makeSurface(plan, { today: false, weekEnd: '%s' });
planSurface.scope.acts['menu:11']();
planSurface.scope.acts['move:11:%s']();
tick().then(function () {
  console.log(JSON.stringify({ nowLeft: now.map(function (c) { return c.id; }),
    planRow: { due: plan[0].due_date, group: plan[0].group }, posts: POSTS }));
});
""" % (json.dumps(_rows()), _d(2), json.dumps(_rows()), _d(6), _d(2))))
    assert out["nowLeft"] == [12, 13], "Now only holds what is due today"
    assert out["planRow"] == {"due": _d(2), "group": "week"}
    assert [p["url"] for p in out["posts"]] == ["/api/chores/11/move", "/api/chores/11/move"]
    assert out["posts"][0]["body"] == {"due_date": _d(2)}


@_needs_node
def test_a_failed_move_puts_the_day_back():
    out = _node(_harness("""
var rows = %s;
var s = makeSurface(rows, { today: true });
FAIL_NEXT = true;
s.scope.acts['menu:11']();
s.scope.acts['move:11:%s']();
tick().then(function () {
  console.log(JSON.stringify({ left: rows.map(function (c) { return c.id; }), due: rows[0].due_date,
    toasts: TOASTS }));
});
""" % (json.dumps(_rows()), _d(2))))
    assert out["left"] == [11, 12, 13] and out["due"] == _d(0)
    assert out["toasts"] == ["That didn’t save. Try it again in a moment."]


@_needs_node
def test_with_more_than_two_people_the_names_are_chips_under_one_label():
    people = _PEOPLE + [{"id": 3, "name": "Reid", "first_name": "Reid"}]
    out = _node(_DOM + _var("TICK_ICON", ";\n") + _var("DOTS_ICON", ";\n") + _menu()
                + _function("choreRowHtml") + ("\nchorePeople = %s;\n" % json.dumps(people)) + """
var s = makeSurface(%s, { today: true });
s.scope.acts['menu:11']();
console.log(JSON.stringify({ html: s.scope.innerHTML }));
""" % json.dumps(_rows()))
    menu = out["html"][out["html"].index("chore-menu"):]
    assert ">Hand to<" in menu and ">Vineeth<" in menu and ">Reid<" in menu
    assert "Hand to Vineeth" not in menu
    assert ">Emily<" not in menu


@_needs_node
def test_with_nobody_else_in_the_house_there_is_no_hand_to_at_all():
    out = _node(_DOM + _var("TICK_ICON", ";\n") + _var("DOTS_ICON", ";\n") + _menu()
                + _function("choreRowHtml")
                + '\nchorePeople = [{"id": 1, "name": "Emily", "first_name": "Emily"}];\n' + """
var s = makeSurface(%s, { today: true });
s.scope.acts['menu:11']();
console.log(JSON.stringify({ html: s.scope.innerHTML }));
""" % json.dumps(_rows()))
    menu = out["html"][out["html"].index("chore-menu"):]
    assert "Hand to" not in menu
    assert ">Skip this time<" in menu and 'data-chore-act="move"' in menu


# ==========================================================================
# 7. Where the ⋯ lives, and what it is made of
# ==========================================================================

def test_both_surfaces_wire_the_same_menu():
    """One builder, one menu — never two (this is why choreRowHtml is
    shared in the first place)."""
    assert SHELL_JS.count("function choreMenuHtml(") == 1
    assert SHELL_JS.count("function wireChoreMenu(") == 1
    assert SHELL_JS.count("wireChoreMenu(listEl, nowChoreCtx(") == 1
    assert SHELL_JS.count("wireChoreMenu(steps, planChoreCtx(") == 1


def test_the_dots_is_a_44px_target_and_the_menu_spends_no_accent():
    def _rule(selector: str) -> str:
        start = SHELL_CSS.index(selector + " {")
        return SHELL_CSS[start:SHELL_CSS.index("}", start)]

    dots = _rule(".chore-more")
    assert "width: 44px" in dots and "height: 44px" in dots
    # Each control on its own, not "somewhere in the block" — the loose
    # version was satisfied by either one of these two.
    assert "min-height: 44px" in _rule(".chore-act"), "a verb row is 44px too"
    assert "min-height: 44px" in _rule(".chore-chip"), "and so is a chip"
    block = SHELL_CSS[SHELL_CSS.index(".chore-more {"):SHELL_CSS.index(".chore-chip:hover")]
    assert "apricot" not in block, "Rule 5 — no second primary on either surface"
    # Rule 9: every colour through a token.
    assert not re.search(r":\s*#[0-9a-fA-F]{3,8}\b", block), "no hand-written colours"


def test_the_menu_is_a_second_line_of_the_row_it_is_about():
    """Inline, and inside the row — a sibling could end up describing a
    different chore than the name above it."""
    body = SHELL_JS[SHELL_JS.index("  function choreRowHtml(c, when) {"):]
    body = body[:body.index("\n  }\n")]
    assert "(open ? choreMenuHtml(c) : '')" in body
    assert body.rindex("choreMenuHtml(c)") < body.rindex("'</div>'")


# ==========================================================================
# 8. Review pass, 2026-09-13 — what an independent reviewer found
# ==========================================================================

def test_a_skip_never_reaches_an_occurrence_that_has_not_slipped():
    """
    BLOCKER-adjacent, and a real defect: the sweep was bounded at the
    LATER of the row's day and today, copied from _mark_done — where it is
    right, because a tick means the work happened. Nobody does any work on
    a skip, so skipping the occurrence three weeks out used to take the
    two before it with it: three weeks of bins from one call.
    """
    _house_on()
    tools.add_chore("Bins", frequency="weekly", owner_name="Emily")
    ids = [tools.schedule_chore_instance("Bins", _d(n))["instance_id"] for n in (7, 14, 21)]

    result = tools.skip_chore_instance(ids[2])

    assert result["also_cleared"] == 0
    assert [_row(i)["status"] for i in ids] == ["pending", "pending", "skipped"]


def test_a_skip_on_a_due_row_still_settles_everything_owed_including_a_stale_id():
    """
    The other side of the same bound, unchanged: a row that IS due settles
    every pending occurrence due on or before today — including any dated
    between it and today, which is what keeps a card rendered before a
    date rollover from showing one chore twice.
    """
    _house_on()
    tools.add_chore("Mop", frequency="weekly", owner_name="Emily")
    ids = [tools.schedule_chore_instance("Mop", _d(-n))["instance_id"] for n in (21, 14, 7, 0)]
    ahead = tools.schedule_chore_instance("Mop", _d(7))["instance_id"]

    result = tools.skip_chore_instance(ids[0])   # the OLDEST — a stale id

    assert result["also_cleared"] == 3
    assert all(_row(i)["status"] == "skipped" for i in ids)
    assert _row(ahead)["status"] == "pending", "nothing ahead of today is settled by a skip"
    assert tools.get_chores_due_today() == []


def test_a_skip_is_one_transaction():
    """The sweep and the row's own status are one decision — a failure
    between two commits would leave the pile swept and the row the
    household tapped still sitting there due."""
    import app.tools.chores as chores_mod
    body = inspect.getsource(chores_mod.skip_chore_instance)
    code = "\n".join(l for l in body.splitlines() if not l.strip().startswith("#"))
    assert code.count("get_conn()") == 1
    assert code.count("conn.commit()") == 1
    assert "set_chore_instance_status" not in code, "the row's own status rides the same write"


def test_move_offers_days_around_the_row_not_around_today():
    """
    THE BLOCKER. Plan | Chores' "Coming up" is where the monthly and
    quarterly chores live; a window anchored on today gave a "Gutters ·
    Dec 1" row seven chips that every one of them dragged it eleven weeks
    forward — and, once moved, offered only that same week again, so
    there was no way back to December from any screen.
    """
    far = "2026-12-01"
    out = _node(_harness("""
console.log(JSON.stringify({
  far: choreMoveDays({ due_date: '%s' }),
  today: choreMoveDays({ due_date: '%s' }),
  slipped: choreMoveDays({ due_date: '%s' })
}));
""" % (far, _d(0), _d(-20))))

    far_days = [d["iso"] for d in out["far"]]
    assert far_days == ["2026-11-28", "2026-11-29", "2026-11-30",
                        "2026-12-02", "2026-12-03", "2026-12-04"]
    assert far not in far_days, "the day it is already on is not an option"
    # And the way back: from any of them, the original day is offered again.
    back = _node(_harness("console.log(JSON.stringify(choreMoveDays({ due_date: '2026-12-04' })));"))
    assert far in [d["iso"] for d in back], "a move has to be undoable from the same control"

    # A row due today (or slipped past it) never offers a day in the past.
    assert [d["iso"] for d in out["today"]] == [_d(i) for i in range(1, 7)]
    assert [d["iso"] for d in out["slipped"]] == [_d(i) for i in range(0, 7)]
    assert out["slipped"][0]["label"] == "Today", "a slipped chore can be done today"


def test_a_day_chip_says_which_friday_once_there_is_more_than_one():
    """Inside the coming week a bare weekday is unambiguous; beyond it,
    "Fri" on a December row is a question, not an answer."""
    out = _node(_harness("""
console.log(JSON.stringify({
  soon: choreMoveDays({ due_date: '%s' }).map(function (d) { return d.label; }),
  far: choreMoveDays({ due_date: '2026-12-01' }).map(function (d) { return d.label; })
}));
""" % _d(0)))
    assert out["soon"][0] == "Tomorrow"
    assert all(len(l) <= 8 for l in out["soon"]), "this week is said as a weekday"
    for label in out["far"]:
        assert re.match(r"^[A-Z][a-z]{2} [A-Z][a-z]{2} \d+$", label), label   # "Sat Nov 28"


def test_every_action_says_what_it_did_and_a_move_can_be_undone():
    """
    On Now a skip or a move-off-today makes the row vanish, and a row that
    disappears in silence is indistinguishable from a mis-tap. The move's
    Undo is also what makes a mis-aimed move recoverable from the screen
    that made it.
    """
    out = _node(_harness("""
var rows = %s;
var s = makeSurface(rows, { today: true });
s.scope.acts['menu:11']();
s.scope.acts['move:11:%s']();
tick().then(function () {
  var undone = UNDO ? 'yes' : 'no';
  if (UNDO) UNDO();
  tick().then(function () {
    console.log(JSON.stringify({ toasts: TOASTS, undoOffered: undone, posts: POSTS }));
  });
});
""" % (json.dumps(_rows()), _d(3))))
    assert out["toasts"] == ["Bins moved to " + _label(_d(3)) + "."]
    assert out["undoOffered"] == "yes"
    # The undo posts the day it came from — the exact way back.
    assert out["posts"][-1] == {"url": "/api/chores/11/move", "body": {"due_date": _d(0)}}


def test_the_skips_undo_restores_the_row_and_says_which_it_is_about():
    """Restores the ROW, not the pile it swept — the same choice
    _mark_done's docstring already makes about un-ticking, and the toast
    says "this time", which is what comes back."""
    out = _node(_harness("""
var s = makeSurface(%s, { today: true });
s.scope.acts['menu:11']();
s.scope.acts['skip:11']();
tick().then(function () {
  if (UNDO) UNDO();
  tick().then(function () { console.log(JSON.stringify({ toasts: TOASTS, posts: POSTS })); });
});
""" % json.dumps(_rows())))
    assert out["toasts"] == ["Skipped Bins this time."]
    assert out["posts"][-1] == {"url": "/api/chores/11/status", "body": {"status": "pending"}}


def test_a_hand_back_is_offered_only_when_there_is_somebody_to_hand_back_to():
    """A 'whoever' chore had no assignee, so there is no name to send."""
    rows = _rows()
    rows[0]["assignee_id"] = None
    rows[0]["who_label"] = "either of you"
    out = _node(_harness("""
var s = makeSurface(%s, { today: true });
s.scope.acts['menu:11']();
s.scope.acts['hand:11:1']();
tick().then(function () { console.log(JSON.stringify({ toasts: TOASTS, undo: !!UNDO })); });
""" % json.dumps(rows)))
    assert out["toasts"] == ["Emily has Bins this time."]
    assert out["undo"] is False


def test_a_refusal_prints_the_servers_own_sentence_and_puts_the_row_back():
    """
    Reachable: the ⋯ is open on one phone while the other ticks the row.
    CLAUDE.md's SlotRefused entry — "an app that did exactly the right
    thing must not report itself broken".
    """
    out = _node(_harness("""
var rows = %s;
var s = makeSurface(rows, { today: true });
REFUSE_NEXT = 'Bins isn’t waiting to be done — there’s nothing to skip.';
s.scope.acts['menu:11']();
s.scope.acts['skip:11']();
tick().then(function () {
  console.log(JSON.stringify({ toasts: TOASTS, left: rows.map(function (c) { return c.id; }),
    refreshed: REFRESHED.length }));
});
""" % json.dumps(_rows())))
    assert out["toasts"] == ["Bins isn’t waiting to be done — there’s nothing to skip."]
    assert out["left"] == [11, 12, 13], "the row goes back where it was"
    assert out["refreshed"] == 1, "and the screen re-reads, because it was the stale one"


def test_a_skip_on_plan_marks_the_row_rather_than_dropping_it():
    """
    Plan is one row per chore, always — the server's _top_up_unscheduled
    writes the next occurrence on the very next read precisely so a chore
    never falls off that list. Removing the row would break that for a
    beat and then have the chore reappear under a different heading.
    """
    out = _node(_harness("""
var plan = %s;
var s = makeSurface(plan, { today: false, weekEnd: '%s' });
s.scope.acts['menu:11']();
s.scope.acts['skip:11']();
console.log(JSON.stringify({ ids: plan.map(function (c) { return c.id; }),
  row: { due: plan[0].due_date, status: plan[0].status },
  html: s.scope.innerHTML }));
""" % (json.dumps([dict(r, frequency="weekly") for r in _rows()]), _d(6))))
    assert out["ids"] == [11, 12, 13], "the chore is still on the list"
    assert out["row"]["status"] == "skipped"
    # And it is settled: no tick to press, no ⋯ to open.
    row = out["html"][:out["html"].index('data-id="12"')]
    assert "chore-tick" not in row and "chore-act" not in row
    assert 'class="chore-row is-skipped" data-id="11"' in row
    assert "Skipped" in row, "and it says which state it is in"


def test_a_skipped_row_never_shows_a_date_the_client_made_up():
    """
    THE POINT of not guessing. planChoresStepHtml only shows its trouble
    line when there is no list at all, and after a skip there is one — so
    a guessed date on a failed refresh stood as fact, silently, until some
    later read happened to succeed. Measured at 25 days wrong for a
    monthly chore that already had an occurrence five days out.
    """
    out = _node(_harness("""
var plan = %s;
var before = plan[0].due_date;
var s = makeSurface(plan, { today: false, weekEnd: '%s' });
s.scope.acts['menu:11']();
s.scope.acts['skip:11']();
console.log(JSON.stringify({ before: before, after: plan[0].due_date,
  when: planChoreWhen(plan[0]) }));
""" % (json.dumps([dict(r, frequency="monthly") for r in _rows()]), _d(6))))
    assert out["after"] == out["before"], "the client invents no date"
    assert out["when"] == "Skipped", "it says the thing it actually knows"


def test_a_once_chore_is_marked_like_any_other_and_the_read_decides():
    """No special case: the server simply has no next occurrence for it,
    so the read that follows drops it."""
    rows = [dict(r, frequency="once") for r in _rows()]
    out = _node(_harness("""
var plan = %s;
var s = makeSurface(plan, { today: false, weekEnd: '%s' });
s.scope.acts['menu:11']();
s.scope.acts['skip:11']();
console.log(JSON.stringify({ ids: plan.map(function (c) { return c.id; }),
  status: plan[0].status }));
""" % (json.dumps(rows), _d(6))))
    assert out["ids"] == [11, 12, 13] and out["status"] == "skipped"


def test_two_people_with_one_first_name_get_two_different_chips():
    """A menu offering "Sam" and "Sam" asks the household to guess."""
    people = [{"id": 1, "name": "Emily", "first_name": "Emily"},
              {"id": 2, "name": "Sam Okafor", "first_name": "Sam"},
              {"id": 3, "name": "Sam Reid", "first_name": "Sam"}]
    out = _node(_DOM + _var("TICK_ICON", ";\n") + _var("DOTS_ICON", ";\n") + _menu()
                + _function("choreRowHtml") + ("\nchorePeople = %s;\n" % json.dumps(people)) + """
var s = makeSurface(%s, { today: true });
s.scope.acts['menu:11']();
console.log(JSON.stringify({ html: s.scope.innerHTML }));
""" % json.dumps(_rows()))
    menu = out["html"][out["html"].index("chore-menu"):]
    assert ">Sam Okafor<" in menu and ">Sam Reid<" in menu
    assert ">Sam<" not in menu
    # The one that shares nothing keeps its first name.
    assert 'data-person="1"' not in menu, "whoever already has it is still left out"


def test_the_by_name_date_error_quotes_what_was_typed_and_offers_the_other_answer():
    _house_on()
    tools.add_chore("Bins", frequency="weekly", owner_name="Emily")
    with pytest.raises(ValueError) as e:
        tools.hand_chore("Bins", "Vineeth", when="Next Tuesday")
    assert "'Next Tuesday'" in str(e.value), "their own words, not our lowercased copy"
    assert "or say 'this week'" in str(e.value), "the branch accepts that too"


# ==========================================================================
# 9. Second review pass, 2026-09-13
# ==========================================================================

def test_a_skipped_occurrence_is_never_reported_as_a_done_one():
    """
    hand_chore_instance said "already done" over a skip — nobody did it —
    which move_chore_instance eighty lines above already got right. It
    matters more than it did: these sentences are shown word for word now.
    """
    _house_on()
    tools.add_chore("Vacuuming", frequency="weekly", owner_name="Emily")
    inst = tools.schedule_chore_instance("Vacuuming", _d(0))["instance_id"]
    tools.skip_chore_instance(inst)

    with pytest.raises(tools.ChoreRefused) as hand:
        tools.hand_chore_instance(inst, "Vineeth")
    with pytest.raises(tools.ChoreRefused) as move:
        tools.move_chore_instance(inst, _d(2))
    with pytest.raises(tools.ChoreRefused) as skip:
        tools.skip_chore_instance(inst)

    for e in (hand, move):
        assert "already done" not in str(e.value), str(e.value)
    assert "already off this time" in str(hand.value)
    assert "already off for that day" in str(move.value)
    # skip_chore_instance keeps ONE sentence for both settled states on
    # purpose: it is true of either, and naming which would tell the
    # household something they didn't ask.
    assert "isn't waiting to be done" in str(skip.value)


def test_a_done_occurrence_still_reads_as_done():
    """The other half — the distinction has to go both ways."""
    _house_on()
    tools.add_chore("Vacuuming", frequency="weekly", owner_name="Emily")
    inst = tools.schedule_chore_instance("Vacuuming", _d(0))["instance_id"]
    tools.complete_chore(inst)
    with pytest.raises(tools.ChoreRefused, match="already done"):
        tools.hand_chore_instance(inst, "Vineeth")
    with pytest.raises(tools.ChoreRefused, match="already done that day"):
        tools.move_chore_instance(inst, _d(2))


@_needs_node
def test_a_refresh_behind_an_in_flight_read_is_not_answered_by_it():
    """
    loadPlanChores coalesces, so a ctx.refresh() fired by the ··· while a
    read that PREDATES the tap was still out got handed that read and
    started no new one — Plan settling back onto the pre-action list.
    """
    out = _node("""
var weekState = { step: 'chores', chores: null, choresTrouble: false };
var shellWho = { chores_enabled: true };
function choresEnabled() { return true; }
function renderMealsStep() {}
function goMealsStep() {}
function choreSetPeople() {}
var RESOLVE = [];
var READS = 0;
function fetch() {
  READS += 1;
  var n = READS;
  return new Promise(function (res) {
    RESOLVE.push(function () {
      res({ ok: true, json: function () { return Promise.resolve({ chores: [], read: n }); } });
    });
  });
}
var planChoresFetching = null;
var planChoresWanted = false;
""" + _function("loadPlanChores") + """
var panel = {};
loadPlanChores(panel);            // the read that predates the tap
loadPlanChores(panel);            // the ··· asking again, mid-flight
RESOLVE[0]();
setTimeout(function () {
  // The queued one has to have gone out on its own.
  if (RESOLVE[1]) RESOLVE[1]();
  setTimeout(function () {
    console.log(JSON.stringify({ reads: READS, saw: weekState.chores && weekState.chores.read }));
  }, 10);
}, 10);
""")
    assert out["reads"] == 2, "the ask after the tap got a read of its own"
    assert out["saw"] == 2, "and the list is the one from after the tap"


def test_skip_by_name_no_longer_sweeps_a_same_day_duplicate():
    """
    CHARACTERISATION, and the counter-example to this branch's own parity
    claim — written down so the next session finds it rather than
    rediscovering it. schedule_chore_instance does not dedupe, so saying
    "put the bins on the 19th" twice in chat leaves two pending rows on
    one future day; skip_chore resolves to one and now sweeps nothing,
    where before it swept the other. So "skip the bins" can leave a bins
    still due that day.

    Degenerate, reachable through the app's own chat tools with no raw
    SQL, and the new behaviour is the more defensible of the two — nobody
    did any work, so nothing ahead of today is settled. Not a bug being
    fixed here; a claim being kept honest.
    """
    _house_on()
    tools.add_chore("Bins", frequency="weekly", owner_name="Emily")
    tools.schedule_chore_instance("Bins", _d(6))
    tools.schedule_chore_instance("Bins", _d(6))

    assert tools.skip_chore("Bins")["also_cleared"] == 0

    conn = get_conn()
    rows = sorted(
        (r["status"] for r in conn.execute("SELECT status FROM chore_instances ORDER BY id"))
    )
    conn.close()
    assert rows == ["pending", "skipped"], "one of the day's two is still due"

    # And the sibling that refuses to make this state in the first place:
    # a move will not double a chore up on a day, which is why the state
    # above can only be built by scheduling twice.
    tools.add_chore("Hoover", frequency="weekly", owner_name="Emily")
    tools.schedule_chore_instance("Hoover", _d(2))
    tools.schedule_chore_instance("Hoover", _d(4))
    with pytest.raises(tools.ChoreRefused, match="already on"):
        tools.move_chore("Hoover", _d(4), from_date=_d(2))
