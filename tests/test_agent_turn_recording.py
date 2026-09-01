"""
The real `run_agent_turn`, against a stubbed Anthropic client.

Every other chat test monkeypatches `run_agent_turn` away, which means the
code that *fills in* what a turn cost — and the line that logs a crashed
tool — never runs under test at all. That matters more here than usual:
both exist specifically so that failures and usage stop being invisible,
so a silent regression in them looks exactly like a healthy quiet app.
Delete the token tallying and every recorded row is zeros forever, with
nothing failing to say so.

These tests stub the HTTP client, not the loop, so the loop itself is
real.
"""
import logging
import types

from app import agent, main, tools
from app.db import get_conn


class _Usage:
    def __init__(self, input_tokens=0, cache_read=0, cache_write=0, output_tokens=0):
        self.input_tokens = input_tokens
        self.cache_read_input_tokens = cache_read
        self.cache_creation_input_tokens = cache_write
        self.output_tokens = output_tokens


def _text_block(text):
    return types.SimpleNamespace(type="text", text=text)


def _tool_block(name, tool_input, block_id="tu_1"):
    return types.SimpleNamespace(type="tool_use", name=name, input=tool_input, id=block_id)


class _FakeMessages:
    """Hands back a scripted response per round, like the real API would."""

    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = 0

    def create(self, **kwargs):
        self.calls += 1
        return self._responses.pop(0)


def _stub_client(monkeypatch, responses):
    fake = types.SimpleNamespace(messages=_FakeMessages(responses))
    monkeypatch.setattr(agent, "_client", lambda: fake)
    return fake


def _rows():
    conn = get_conn()
    rows = conn.execute("SELECT * FROM chat_turns ORDER BY id").fetchall()
    conn.close()
    return rows


def test_a_real_turn_records_the_tokens_it_actually_used(signed_in, monkeypatch):
    """
    The numbers must come from the API response, not from a test's own
    hand-set dict — otherwise the tally could be deleted entirely and
    every test would still pass while production recorded zeros.
    """
    _stub_client(monkeypatch, [
        types.SimpleNamespace(
            content=[_text_block("Evening.")],
            stop_reason="end_turn",
            usage=_Usage(input_tokens=7, cache_read=2500, cache_write=31, output_tokens=42),
        ),
    ])

    res = signed_in.post("/api/chat", json={"session_id": "default", "message": "hello"})
    assert res.status_code == 200

    row = _rows()[0]
    assert row["input_tokens"] == 7
    assert row["cache_read_tokens"] == 2500
    assert row["cache_write_tokens"] == 31
    assert row["output_tokens"] == 42
    assert row["rounds"] == 1
    assert row["seconds"] > 0, "a real turn takes a measurable amount of time"


def test_tokens_are_summed_across_every_round_of_a_turn(signed_in, monkeypatch):
    """
    One question can take several round trips, and the cost of the job is
    all of them — recording only the last round would understate a
    multi-round turn, which is exactly the shape of the expensive ones.
    """
    _stub_client(monkeypatch, [
        types.SimpleNamespace(
            content=[_tool_block("list_grocery_list", {})],
            stop_reason="tool_use",
            usage=_Usage(input_tokens=10, cache_read=100, cache_write=5, output_tokens=20),
        ),
        types.SimpleNamespace(
            content=[_text_block("Nothing on the list.")],
            stop_reason="end_turn",
            usage=_Usage(input_tokens=1, cache_read=200, cache_write=0, output_tokens=8),
        ),
    ])

    signed_in.post("/api/chat", json={"session_id": "default", "message": "what's on the list?"})

    row = _rows()[0]
    assert row["rounds"] == 2
    assert row["input_tokens"] == 11
    assert row["cache_read_tokens"] == 300
    assert row["cache_write_tokens"] == 5
    assert row["output_tokens"] == 28


def test_a_crashed_tool_is_logged(signed_in, monkeypatch, caplog):
    """
    The blind spot this branch exists to close: a tool that raises is
    handed back to the model, which apologises smoothly, and the request
    returns 200. Without a log line there is no trace anywhere that
    anything broke.
    """
    def explode(**kwargs):
        raise RuntimeError("the pantry is on fire")

    monkeypatch.setitem(agent.TOOL_FUNCTIONS, "list_grocery_list", explode)
    _stub_client(monkeypatch, [
        types.SimpleNamespace(
            content=[_tool_block("list_grocery_list", {})],
            stop_reason="tool_use",
            usage=_Usage(output_tokens=5),
        ),
        types.SimpleNamespace(
            content=[_text_block("Sorry, I couldn't check that just now.")],
            stop_reason="end_turn",
            usage=_Usage(output_tokens=9),
        ),
    ])

    with caplog.at_level(logging.ERROR, logger="home_manager"):
        res = signed_in.post("/api/chat", json={"session_id": "default", "message": "check the list"})

    assert res.status_code == 200, "a failed tool should still produce a reply"
    assert "list_grocery_list" in caplog.text, "the failing tool must be named in the logs"
    assert "the pantry is on fire" in caplog.text, "the traceback must reach the logs"


def test_a_crashed_tool_does_not_log_its_argument_values(signed_in, monkeypatch, caplog):
    """
    Tools like add_fact carry freeform household detail. Logging a failing
    call's arguments would put exactly the personal content this app is
    careful with into stdout — while chat_turns deliberately stores none.
    Argument names, never values.
    """
    def explode(**kwargs):
        raise RuntimeError("write failed")

    monkeypatch.setitem(agent.TOOL_FUNCTIONS, "add_fact", explode)
    _stub_client(monkeypatch, [
        types.SimpleNamespace(
            content=[_tool_block("add_fact", {"category": "people", "fact": "seeing a divorce lawyer"})],
            stop_reason="tool_use",
            usage=_Usage(output_tokens=5),
        ),
        types.SimpleNamespace(
            content=[_text_block("I couldn't save that.")],
            stop_reason="end_turn",
            usage=_Usage(output_tokens=6),
        ),
    ])

    with caplog.at_level(logging.ERROR, logger="home_manager"):
        signed_in.post("/api/chat", json={"session_id": "default", "message": "remember something"})

    assert "add_fact" in caplog.text, "the tool name is what identifies the failure"
    assert "category" in caplog.text, "argument names are useful and safe"
    assert "divorce" not in caplog.text.lower(), "argument VALUES must never reach the logs"


def test_a_new_measure_in_the_agent_cannot_break_chat(signed_in, monkeypatch):
    """
    The tally and the table are edited by different hands at different
    times. Adding a measure in agent.py without a column for it must be a
    thing that isn't stored — never a 500 on a reply already paid for and
    in hand.
    """
    def turn_measuring_something_new(conversation, user_message, *, proactive_check=False):
        agent.LAST_TURN_USAGE.set({
            "rounds": 1,
            "output_tokens": 3,
            "thinking_tokens": 123,  # a measure with no column for it
        })
        return "Fine.", conversation + [{"role": "user", "content": user_message}]

    monkeypatch.setattr(main, "run_agent_turn", turn_measuring_something_new)

    res = signed_in.post("/api/chat", json={"session_id": "default", "message": "hi"})
    assert res.status_code == 200, "an unknown measure must not fail the turn"
    assert len(_rows()) == 1, "the turn is still recorded, minus the unknown field"
