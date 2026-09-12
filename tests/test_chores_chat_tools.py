"""
Chores v1: add or change anything by saying so.

Loop Board "Chores v1: Add or change anything by saying so — re-verify the
chat tools against the Chores screen" (Emily, 2026-09-12). Two things this
file exists to check:

    1. Every chores chat tool actually runs when the model calls it —
       through the real dispatch in agent.run_agent_turn (stubbed API
       client, real TOOL_FUNCTIONS, real tools.* functions, real
       throwaway DB), not just as a bare function call. update_chore,
       add_chore and the rest already have direct-function tests
       elsewhere (test_chore_owner_mode.py, test_chores_no_guilt.py,
       test_chore_outsourced.py); none of those exercise the dispatch
       path, so this file adds one agent-path test per chores tool —
       the original nine plus the two new ones below.

    2. skip_chore and move_chore themselves — the two occurrence-level
       actions the tool set was missing (the backend already supported
       'skipped' via set_chore_instance_status, exposed only to the Now
       card's HTTP route; nothing re-dated an existing row at all). Both
       are additive: skip_chore is a thin door onto
       set_chore_instance_status, and move_chore re-dates a row in place
       rather than creating a new one, so the rest of this file pins the
       rules that have to keep holding: an outsourced occurrence can be
       skipped but never ticked; a skipped occurrence settles any backlog
       behind it exactly like a tick does (no-guilt-pile) and is never
       counted against anyone; a move keeps its id and person; a move
       refuses to double a chore up on one day; and both are scoped to
       one household like everything else in this app.

Every test in section 2 fails on `main` (skip_chore/move_chore don't
exist there). Section 1's tests do too, purely because the tools don't
exist yet for the last two — the other nine would already pass their
dispatch test on `main`; they're included here so the full set is
covered in one place per the ticket's acceptance criteria.
"""
from __future__ import annotations

import datetime
import json
import types

import pytest

from app import agent, households, tools
from app.db import get_conn

TODAY = datetime.date.today().isoformat()


def _adult(name: str) -> int:
    member_id = tools.add_member(name)["member_id"]
    tools.set_member_age_group(name, "Adult")
    return member_id


# ==========================================================================
# Stubbed Anthropic client — same shape as tests/test_chores_switch.py.
# ==========================================================================

class _Usage:
    input_tokens = cache_read_input_tokens = cache_creation_input_tokens = output_tokens = 0


def _text_block(text):
    return types.SimpleNamespace(type="text", text=text)


def _tool_block(name, tool_input, block_id="tu_1"):
    return types.SimpleNamespace(type="tool_use", name=name, input=tool_input, id=block_id)


class _FakeMessages:
    def __init__(self, responses):
        self._responses = list(responses)
        self.requests = []

    def create(self, **kwargs):
        self.requests.append(kwargs)
        return self._responses.pop(0)


def _stub_client(monkeypatch, responses):
    fake = _FakeMessages(responses)
    monkeypatch.setattr(agent, "_client", lambda: types.SimpleNamespace(messages=fake))
    return fake


def _call_through_agent(monkeypatch, name: str, tool_input: dict, final_text: str = "Done.") -> dict:
    """
    Hand the model a single tool call and a closing reply, run the real
    dispatch loop, and hand back the real tool's own JSON result — after
    checking the call actually reached TOOL_FUNCTIONS and succeeded rather
    than being declined or swallowed as an error. This is what makes these
    "through the agent's tool-call path" tests rather than direct calls to
    the tools.* function, which every other chores test file already has.
    """
    _stub_client(monkeypatch, [
        types.SimpleNamespace(content=[_tool_block(name, tool_input)], stop_reason="tool_use", usage=_Usage()),
        types.SimpleNamespace(content=[_text_block(final_text)], stop_reason="end_turn", usage=_Usage()),
    ])
    reply, conversation = agent.run_agent_turn([], f"(test) please call {name}")
    tool_result_messages = [m for m in conversation if m.get("role") == "user" and isinstance(m.get("content"), list)]
    handed = tool_result_messages[-1]["content"][0]
    assert handed["type"] == "tool_result"
    assert not handed.get("is_error"), f"{name} did not run cleanly: {handed['content']}"
    assert reply == final_text
    return json.loads(handed["content"])


# ==========================================================================
# 1. Every chores tool, through the agent's own dispatch — not just the
#    function. Chores is switched on for all of these; test_chores_switch.py
#    already covers what happens while it's off.
# ==========================================================================

def test_agent_path_get_chores_profile(monkeypatch):
    tools.set_chores_enabled(True)
    result = _call_through_agent(monkeypatch, "get_chores_profile", {})
    assert result == {"has_profile": False}


def test_agent_path_set_chores_profile(monkeypatch):
    tools.set_chores_enabled(True)
    _call_through_agent(monkeypatch, "set_chores_profile", {"home_type": "Condo", "bedrooms": 2})
    assert tools.get_chores_profile()["home_type"] == "Condo"


def test_agent_path_add_chore(monkeypatch):
    tools.set_chores_enabled(True)
    _adult("Emily")
    result = _call_through_agent(
        monkeypatch, "add_chore", {"name": "Vacuuming", "frequency": "weekly", "owner_name": "Emily"}
    )
    assert result["mode"] == "owned"
    defs = tools.list_chore_definitions()
    assert [d["name"] for d in defs] == ["Vacuuming"]
    assert defs[0]["owner"] == "Emily"


def test_agent_path_list_chore_definitions(monkeypatch):
    tools.set_chores_enabled(True)
    _adult("Emily")
    tools.add_chore("Bins", frequency="weekly", owner_name="Emily")
    result = _call_through_agent(monkeypatch, "list_chore_definitions", {})
    assert [d["name"] for d in result] == ["Bins"]


def test_agent_path_update_chore(monkeypatch):
    tools.set_chores_enabled(True)
    _adult("Emily")
    chore_id = tools.add_chore("Bins", frequency="weekly", owner_name="Emily")["chore_id"]
    _call_through_agent(monkeypatch, "update_chore", {"chore_id": chore_id, "frequency": "biweekly"})
    assert tools.list_chore_definitions()[0]["frequency"] == "biweekly"


def test_agent_path_generate_chore_schedule(monkeypatch):
    tools.set_chores_enabled(True)
    _adult("Emily")
    tools.add_chore("Bins", frequency="weekly", owner_name="Emily")
    assert tools.list_chores(status="pending") == []
    _call_through_agent(monkeypatch, "generate_chore_schedule", {"days_ahead": 14})
    assert tools.list_chores(status="pending") != []


def test_agent_path_schedule_chore_instance(monkeypatch):
    tools.set_chores_enabled(True)
    _adult("Emily")
    tools.add_chore("Bins", frequency="weekly", owner_name="Emily")
    due = (datetime.date.today() + datetime.timedelta(days=3)).isoformat()
    result = _call_through_agent(monkeypatch, "schedule_chore_instance", {"chore_name": "Bins", "due_date": due})
    rows = [i for i in tools.list_chores(status="pending") if i["id"] == result["instance_id"]]
    assert rows and rows[0]["due_date"] == due


def test_agent_path_list_chores(monkeypatch):
    tools.set_chores_enabled(True)
    _adult("Emily")
    tools.add_chore("Bins", frequency="weekly", owner_name="Emily")
    tools.schedule_chore_instance("Bins", TODAY)
    result = _call_through_agent(monkeypatch, "list_chores", {})
    assert any(i["chore"] == "Bins" for i in result)


def test_agent_path_complete_chore(monkeypatch):
    tools.set_chores_enabled(True)
    _adult("Emily")
    tools.add_chore("Bins", frequency="weekly", owner_name="Emily")
    instance_id = tools.schedule_chore_instance("Bins", TODAY)["instance_id"]
    result = _call_through_agent(monkeypatch, "complete_chore", {"instance_id": instance_id})
    assert result["status"] == "done"
    assert any(i["id"] == instance_id for i in tools.list_chores(status="done"))


def test_agent_path_skip_chore(monkeypatch):
    tools.set_chores_enabled(True)
    _adult("Emily")
    tools.add_chore("Bins", frequency="weekly", owner_name="Emily")
    instance_id = tools.schedule_chore_instance("Bins", TODAY)["instance_id"]
    result = _call_through_agent(monkeypatch, "skip_chore", {"chore_name": "Bins"})
    assert result["status"] == "skipped"
    assert any(i["id"] == instance_id for i in tools.list_chores(status="skipped"))
    assert tools.list_chores(status="pending") == []


def test_agent_path_move_chore(monkeypatch):
    tools.set_chores_enabled(True)
    _adult("Emily")
    tools.add_chore("Bins", frequency="weekly", owner_name="Emily")
    instance_id = tools.schedule_chore_instance("Bins", TODAY)["instance_id"]
    new_date = (datetime.date.today() + datetime.timedelta(days=5)).isoformat()
    result = _call_through_agent(monkeypatch, "move_chore", {"chore_name": "Bins", "to_date": new_date})
    assert result["instance_id"] == instance_id
    assert result["due_date"] == new_date


# ==========================================================================
# 2. skip_chore — the rules that have to hold, called directly (the agent
#    path above already proves it's wired up end to end).
# ==========================================================================

def test_skip_allows_an_outsourced_occurrence():
    """
    The cleaner not coming is real. Skipping must not run into the same
    refusal a tick does (_refuse_if_outsourced) — it never calls it.
    """
    tools.add_chore("Windows", frequency="weekly", mode="outsourced", outsourced_to="Maria")
    tools.schedule_chore_instance("Windows", TODAY)
    result = tools.skip_chore("Windows")
    assert result["status"] == "skipped"


def test_skip_never_becomes_completable():
    """Skipping doesn't change the underlying rule: still nothing to tick."""
    tools.add_chore("Windows", frequency="weekly", mode="outsourced", outsourced_to="Maria")
    instance_id = tools.schedule_chore_instance("Windows", TODAY)["instance_id"]
    tools.skip_chore("Windows")
    with pytest.raises(ValueError):
        tools.complete_chore(instance_id)


def test_skip_clears_a_backlog_the_same_way_a_tick_does():
    """
    Resolving to "the due one" (no explicit date) also settles any pile
    behind it — the identical no-guilt-pile sweep _mark_done runs for a
    tick. Otherwise "skip the vacuuming" would leave two more slipped
    weeks sitting under the one row that was just skipped, and the next
    call would still show it as due.
    """
    _adult("Emily")
    tools.add_chore("Vacuuming", frequency="weekly", owner_name="Emily")
    today = datetime.date.today()
    for weeks_ago in (3, 2, 1):
        tools.schedule_chore_instance("Vacuuming", (today - datetime.timedelta(weeks=weeks_ago)).isoformat())
    pending = tools.list_chores(status="pending")
    assert len(pending) == 1 and pending[0]["stands_for"] == 3, "backlog should collapse to one due row first"

    result = tools.skip_chore("Vacuuming")

    assert result["also_cleared"] == 2
    assert tools.list_chores(status="pending") == []
    assert len(tools.list_chores(status="skipped")) == 3


def test_skip_of_a_named_date_does_not_touch_todays_backlog():
    """Naming a specific date is precise — it must not sweep away what's genuinely due right now too."""
    _adult("Emily")
    tools.add_chore("Vacuuming", frequency="weekly", owner_name="Emily")
    today = datetime.date.today()
    due_today = tools.schedule_chore_instance("Vacuuming", today.isoformat())["instance_id"]
    future = (today + datetime.timedelta(days=20)).isoformat()
    future_id = tools.schedule_chore_instance("Vacuuming", future)["instance_id"]

    result = tools.skip_chore("Vacuuming", when=future)

    assert result["instance_id"] == future_id
    assert result.get("also_cleared", 0) == 0
    assert due_today in {i["id"] for i in tools.list_chores(status="pending")}


def test_skip_is_scoped_to_its_own_household():
    """A skip in household 1 must never touch household 2's row of the same-named chore."""
    _adult("Emily")
    tools.add_chore("Bins", frequency="weekly", owner_name="Emily")
    mine_id = tools.schedule_chore_instance("Bins", TODAY)["instance_id"]

    other = households.create_household("The Testers", "correct-horse-battery-staple")
    with tools.use_household(other):
        tools.add_member("Sam")
        tools.set_member_age_group("Sam", "Adult")
        tools.add_chore("Bins", frequency="weekly", owner_name="Sam")
        theirs_id = tools.schedule_chore_instance("Bins", TODAY)["instance_id"]

    tools.skip_chore("Bins")  # back on household 1's context

    conn = get_conn()
    mine_status = conn.execute("SELECT status FROM chore_instances WHERE id = ?", (mine_id,)).fetchone()["status"]
    theirs_status = conn.execute("SELECT status FROM chore_instances WHERE id = ?", (theirs_id,)).fetchone()["status"]
    conn.close()
    assert mine_status == "skipped"
    assert theirs_status == "pending"


# ==========================================================================
# 3. move_chore — the rules that have to hold.
# ==========================================================================

def test_move_keeps_the_same_id_and_person():
    """Re-dating in place, not cancel-and-rebook: same row, same owner."""
    _adult("Emily")
    tools.add_chore("Bins", frequency="weekly", owner_name="Emily")
    today = datetime.date.today()
    instance_id = tools.schedule_chore_instance("Bins", (today + datetime.timedelta(days=2)).isoformat())["instance_id"]
    new_date = (today + datetime.timedelta(days=6)).isoformat()

    result = tools.move_chore("Bins", new_date)

    assert result["instance_id"] == instance_id
    assert result["due_date"] == new_date
    assert result["who_label"] == "Emily"
    conn = get_conn()
    row = conn.execute("SELECT due_date, assignee_id FROM chore_instances WHERE id = ?", (instance_id,)).fetchone()
    conn.close()
    assert row["due_date"] == new_date
    assert row["assignee_id"] is not None


def test_move_without_from_date_uses_the_due_one():
    """No from_date named: the occurrence due right now is the one that moves."""
    _adult("Emily")
    tools.add_chore("Bins", frequency="weekly", owner_name="Emily")
    today = datetime.date.today()
    instance_id = tools.schedule_chore_instance("Bins", today.isoformat())["instance_id"]
    new_date = (today + datetime.timedelta(days=4)).isoformat()

    result = tools.move_chore("Bins", new_date)

    assert result["instance_id"] == instance_id
    assert result["due_date"] == new_date


def test_move_refuses_to_create_a_duplicate():
    """Moving onto a date the chore already has an occurrence on is refused, not doubled up."""
    _adult("Emily")
    tools.add_chore("Bins", frequency="weekly", owner_name="Emily")
    today = datetime.date.today()
    d1 = (today + datetime.timedelta(days=2)).isoformat()
    d2 = (today + datetime.timedelta(days=9)).isoformat()
    tools.schedule_chore_instance("Bins", d1)
    tools.schedule_chore_instance("Bins", d2)

    with pytest.raises(ValueError):
        tools.move_chore("Bins", d2, from_date=d1)

    conn = get_conn()
    due_dates = {r["due_date"] for r in conn.execute(
        "SELECT due_date FROM chore_instances ci JOIN chores c ON c.id = ci.chore_id WHERE c.name = 'Bins'"
    ).fetchall()}
    conn.close()
    assert due_dates == {d1, d2}, "nothing should have moved once the duplicate was refused"


def test_move_only_touches_a_pending_occurrence():
    """A done occurrence is history, not a date to rearrange."""
    _adult("Emily")
    tools.add_chore("Bins", frequency="weekly", owner_name="Emily")
    today = datetime.date.today()
    instance_id = tools.schedule_chore_instance("Bins", today.isoformat())["instance_id"]
    tools.complete_chore(instance_id)

    with pytest.raises(ValueError):
        tools.move_chore("Bins", (today + datetime.timedelta(days=3)).isoformat(), from_date=today.isoformat())


def test_move_is_scoped_to_its_own_household():
    """
    Two households can each have their own 'Bins' due the same day —
    moving household 1's onto that date must not be blocked by household
    2's row of the same name, and must not touch household 2's row.
    """
    _adult("Emily")
    tools.add_chore("Bins", frequency="weekly", owner_name="Emily")
    today = datetime.date.today()
    mine_id = tools.schedule_chore_instance("Bins", (today + datetime.timedelta(days=2)).isoformat())["instance_id"]
    target = (today + datetime.timedelta(days=9)).isoformat()

    other = households.create_household("The Testers", "correct-horse-battery-staple")
    with tools.use_household(other):
        tools.add_member("Sam")
        tools.set_member_age_group("Sam", "Adult")
        tools.add_chore("Bins", frequency="weekly", owner_name="Sam")
        theirs_id = tools.schedule_chore_instance("Bins", target)["instance_id"]

    result = tools.move_chore("Bins", target)  # household 1's context — only mine_id is pending here

    assert result["instance_id"] == mine_id
    assert result["due_date"] == target

    conn = get_conn()
    theirs = conn.execute("SELECT due_date, status FROM chore_instances WHERE id = ?", (theirs_id,)).fetchone()
    conn.close()
    assert theirs["due_date"] == target
    assert theirs["status"] == "pending"
