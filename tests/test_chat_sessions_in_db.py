"""
Chat sessions live in the database, not in the server's memory (Loop Board
"Move chat sessions out of server memory", Phase 3, 2026-10-09).

They were two dicts in app/main.py, SESSIONS and SESSION_TOUCHED. Every
Railway redeploy — every merge to main — dropped a household's
conversation mid-sitting while their messages stayed on screen, and a
second uvicorn worker would have split one conversation between two
processes without anybody seeing it. Now they are the chat_sessions table,
read on every turn and cached nowhere.

What these pin, one criterion each:
- a conversation within a sitting survives a restart and is seen by a
  SECOND PROCESS (a real one: a subprocess on the same database file);
- the history round-trips through JSON into the exact request the SDK
  would have sent from the in-memory objects — thinking signature,
  tool_use and tool_result included — measured on the wire, not assumed;
- the week's TTL holds in the table;
- one household can never read another's conversation, by the key AND by
  the household_id column;
- a failed save costs the next turn its context, never this turn's reply.
The four-hour sitting rule itself is test_fresh_sitting_less_history.py's,
unchanged and now running against this table; the per-household cap is
test_multi_household.py's; deletion is test_household_deletion.py's.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

# The HTTP library the installed SDK itself is built on (anthropic 1.x ships
# its own fork, httpx2, and refuses a plain httpx client).
try:
    import httpx2 as httpx
except ImportError:  # pragma: no cover - older SDKs
    import httpx

from app import db, main, security, tools
from app.db import get_conn
from app import households
from app.tools._shared import DEFAULT_HOUSEHOLD_ID

REPO = Path(__file__).resolve().parent.parent


def _sign_in(client, password="test-password"):
    res = client.post("/login", data={"password": password, "next": "/"}, follow_redirects=False)
    assert res.status_code == 303
    return client.cookies[security.COOKIE_NAME]


def _rows():
    conn = get_conn()
    try:
        return {r["session_key"]: dict(r) for r in conn.execute("SELECT * FROM chat_sessions")}
    finally:
        conn.close()


def test_there_is_no_conversation_held_in_process_memory():
    """A per-process copy is the stale copy the move exists to remove."""
    assert not hasattr(main, "SESSIONS")
    assert not hasattr(main, "SESSION_TOUCHED")


def test_a_sitting_survives_a_restart_and_a_second_process_sees_it(client, monkeypatch):
    """
    CATCH, and the card's own criterion. Turn one goes through the real
    route; a SEPARATE Python process — which is what a restarted server,
    or a second worker, is — reads the conversation back off the same
    database file; turn two is then sent the first turn's history.

    Red against main: the subprocess reads nothing (there is no table), and
    with the dict cleared — which is what a restart does — turn two starts
    from empty.
    """
    seen = []

    def fake_turn(conversation, user_message, **_kw):
        seen.append(list(conversation))
        return f"ok {len(seen)}", conversation + [
            {"role": "user", "content": user_message},
            {"role": "assistant", "content": [{"type": "text", "text": f"ok {len(seen)}"}]},
        ]

    monkeypatch.setattr(main, "run_agent_turn", fake_turn)
    _sign_in(client)
    assert client.post("/api/chat", json={"message": "we're out of eggs"}).status_code == 200

    (key,) = _rows()
    assert key.startswith(f"h{DEFAULT_HOUSEHOLD_ID}:")

    other = subprocess.run(
        [sys.executable, "-c",
         "import json, sys\n"
         "from app import main\n"
         "history, touched = main._load_chat_session(sys.argv[1])\n"
         "print(json.dumps(history))\n",
         key],
        # The database THIS process is on, named outright rather than
        # inherited, so the other process cannot be pointed anywhere else.
        cwd=REPO, env={**os.environ, "DB_PATH": db.DB_PATH},
        capture_output=True, text=True, timeout=120,
    )
    assert other.returncode == 0, other.stderr
    assert json.loads(other.stdout.strip().splitlines()[-1]) == [
        {"role": "user", "content": "we're out of eggs"},
        {"role": "assistant", "content": [{"type": "text", "text": "ok 1"}]},
    ], other.stderr

    assert client.post("/api/chat", json={"message": "and milk"}).status_code == 200
    assert seen[0] == []
    assert seen[1][0] == {"role": "user", "content": "we're out of eggs"}


def _wire_client(captured: list, content: list):
    """A real Anthropic client whose HTTP goes nowhere: records each request
    body and answers with `content` as a real API response would."""
    import anthropic

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(json.loads(request.content))
        return httpx.Response(200, json={
            "id": "msg_test", "type": "message", "role": "assistant", "model": "claude-test",
            "content": content, "stop_reason": "tool_use", "stop_sequence": None,
            "usage": {"input_tokens": 1, "output_tokens": 1},
        })

    return anthropic.Anthropic(api_key="test", http_client=httpx.Client(transport=httpx.MockTransport(handler)))


# Opted out of conftest's offline SDK stub on purpose: the request has to be
# BUILT by the real SDK to be compared. It still goes nowhere — the client's
# transport is an in-process mock (_wire_client).
@pytest.mark.real_model_client
def test_a_real_shaped_history_reaches_the_api_byte_for_byte_after_a_round_trip():
    """
    CATCH on the risk the card names. run_agent_turn appends
    `response.content` — SDK OBJECTS — and the dict store let the SDK
    serialise them on the next request. Here the objects come from a real
    SDK response parse (thinking with a signature, text with a null
    citations field, a tool_use), sit beside the plain tool_result dict
    the agent builds, and the next request's `messages` is captured on the
    wire twice: once from the in-memory objects (what main did), once from
    the history after a save and load. They must be identical.

    Pinned by the mutation that dumps blocks with exclude_none instead of
    the SDK's exclude_unset (the stored text block loses `citations: null`
    and the request differs).
    """
    content = [
        {"type": "thinking", "thinking": "They want eggs on the list.", "signature": "sig-abc=="},
        {"type": "text", "text": "Adding eggs.", "citations": None},
        {"type": "tool_use", "id": "toolu_01", "name": "add_grocery_item",
         "input": {"item": "eggs", "quantity": "1 dozen", "notes": None}},
    ]
    captured: list = []
    client = _wire_client(captured, content)
    response = client.messages.create(
        model="claude-test", max_tokens=10, messages=[{"role": "user", "content": "we're out of eggs"}],
    )
    assert not isinstance(response.content[0], dict)  # really SDK objects

    history = [
        {"role": "user", "content": "we're out of eggs"},
        {"role": "assistant", "content": response.content},
        {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "toolu_01",
             "content": json.dumps({"id": 7, "item": "eggs"}), "is_error": False},
        ]},
        {"role": "assistant", "content": "Eggs are on the list."},
    ]

    main._save_chat_session("h1:wire", history)
    loaded, _touched = main._load_chat_session("h1:wire")

    def next_request(conversation):
        before = len(captured)
        client.messages.create(
            model="claude-test", max_tokens=10,
            messages=conversation + [{"role": "user", "content": "and milk"}],
        )
        return captured[before]["messages"]

    assert next_request(loaded) == next_request(history)
    # And the tool_use / tool_result pairing trim_conversation relies on
    # reads the same off the stored dicts.
    assert loaded[1]["content"][2]["id"] == loaded[2]["content"][0]["tool_use_id"] == "toolu_01"


def test_the_week_ttl_holds_in_the_table():
    """
    GUARD. A conversation untouched for over a week is deleted on the next
    save. Pinned by the mutation that drops _save_chat_session's
    _prune_sessions call.
    """
    with db.write() as conn:
        conn.execute(
            "INSERT INTO chat_sessions (session_key, household_id, history_json, touched_at) "
            "VALUES ('h1:old', 1, '[]', ?), ('h1:recent', 1, '[]', ?)",
            (time.time() - main._SESSION_TTL - 60, time.time() - main._SESSION_TTL + 3600),
        )
    main._save_chat_session("h1:now", [{"role": "user", "content": "hi"}])
    assert set(_rows()) == {"h1:recent", "h1:now"}


def test_one_household_cannot_read_anothers_conversation():
    """
    GUARD on isolation, both ways a read is narrowed. A key naming another
    household reads nothing from household 1; and a row whose key LOOKS
    like household 1's but whose column says otherwise reads nothing either.

    Pinned by the mutation that drops `AND household_id = ?` from the read.
    """
    beta = households.create_household("The Beta Testers", "beta passphrase words")
    with tools.use_household(beta):
        main._save_chat_session(f"h{beta}:theirs", [{"role": "user", "content": "beta's secret"}])
    with db.write() as conn:
        conn.execute(
            "INSERT INTO chat_sessions (session_key, household_id, history_json, touched_at) "
            "VALUES ('h1:forged', ?, ?, ?)",
            (beta, json.dumps([{"role": "user", "content": "also beta's"}]), time.time()),
        )

    assert main._load_chat_session(f"h{beta}:theirs") == ([], 0.0)
    assert main._load_chat_session("h1:forged") == ([], 0.0)
    with tools.use_household(beta):
        history, _ = main._load_chat_session(f"h{beta}:theirs")
    assert history == [{"role": "user", "content": "beta's secret"}]


def test_a_failed_save_never_costs_the_reply(monkeypatch):
    """GUARD. The reply is paid for and in hand; a broken save is logged, not a 500."""
    def broken():
        raise RuntimeError("disk full")

    monkeypatch.setattr(db, "write", broken)
    result = main._finish_chat_turn("h1:x", [], "hello", [
        {"role": "user", "content": "hi"}, {"role": "assistant", "content": "hello"},
    ])
    assert result["reply"] == "hello"
    assert _rows() == {}
