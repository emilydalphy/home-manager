"""
Sharing with Claude: nothing leaves for Anthropic until the household says yes.

Loop Board "App Store: ask permission before household details go to the
AI, and say who it is" (2026-09-27; Apple guideline 5.1.2(i)). The rule
lives in app/ai_consent.py and is enforced at agent.py's two call points —
`_create_with_retry` and `_stream_forced_tool_call` — which every request
to Anthropic in this app goes through.

conftest gives every test household a yes (see its `_database` fixture);
these tests take it away with `withdraw_ai_consent` and watch the refusal.
Every test that asserts a refusal also asserts the fake client was never
touched: the point is that nothing is SENT, not just that an error comes
back.
"""
from __future__ import annotations

import contextvars
import threading
import types

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from starlette.exceptions import HTTPException as StarletteHTTPException

from app import agent, ai_consent, chat_themes, main, tools
from app.db import get_conn
from conftest import withdraw_ai_consent


class _Usage:
    input_tokens = 0
    cache_read_input_tokens = 0
    cache_creation_input_tokens = 0
    output_tokens = 0


def _reply(text="ok"):
    return types.SimpleNamespace(
        content=[types.SimpleNamespace(type="text", text=text)],
        stop_reason="end_turn",
        usage=_Usage(),
    )


class _CountingClient:
    """A fake Anthropic client that remembers whether anything was sent."""

    def __init__(self):
        self.sent = []
        outer = self

        class _Messages:
            def create(self, **kwargs):
                outer.sent.append(kwargs)
                return _reply()

            def stream(self, **kwargs):
                outer.sent.append(kwargs)
                raise AssertionError("stream should never be opened without consent")

        self.messages = _Messages()


def _new_household(name="Other house") -> int:
    conn = get_conn()
    cur = conn.execute("INSERT INTO households (name) VALUES (?)", (name,))
    conn.commit()
    hid = cur.lastrowid
    conn.close()
    return hid


# --------------------------------------------------------------------------
# The two call points
# --------------------------------------------------------------------------


@pytest.mark.parametrize("status", ["", "declined"])
def test_create_with_retry_sends_nothing_without_a_yes(status):
    """CATCH. Never asked, or said Not now: the request is refused before it's made."""
    withdraw_ai_consent(1, status)
    client = _CountingClient()
    with pytest.raises(agent.AIConsentRequiredError) as err:
        agent._create_with_retry(client, label="test", model="m", max_tokens=5, messages=[])
    assert client.sent == [], "nothing may be sent to Anthropic without the household's yes"
    assert str(err.value) == ai_consent.REFUSAL_LINE


def test_the_refusal_is_an_assistant_unavailable_error_so_every_route_shows_its_line():
    """CATCH. The subclass is what makes all 20-odd existing handlers say the sentence as-is."""
    assert issubclass(agent.AIConsentRequiredError, agent.AssistantUnavailableError)


def test_create_with_retry_goes_ahead_with_a_yes():
    """GUARD (mutation: always refuse). A household that allowed it is served as before."""
    client = _CountingClient()
    agent._create_with_retry(client, label="test", model="m", max_tokens=5, messages=[])
    assert len(client.sent) == 1


def test_streamed_generation_sends_nothing_without_a_yes():
    """CATCH. The week's streamed generation is the other call point, and it checks too."""
    withdraw_ai_consent(1)
    client = _CountingClient()
    with pytest.raises(agent.AIConsentRequiredError):
        agent._stream_forced_tool_call(
            client, label="test_stream", max_tokens=5, tool_schema={"name": "t"},
            tool_name="t", content="hi", result_key="days",
        )
    assert client.sent == []


def test_consent_is_per_household():
    """CATCH. One household's yes is not another's."""
    other = _new_household()
    withdraw_ai_consent(other)
    client = _CountingClient()
    with tools.use_household(other):
        with pytest.raises(agent.AIConsentRequiredError):
            agent._create_with_retry(client, label="test", model="m", max_tokens=5, messages=[])
    assert client.sent == []
    # Household 1 still has its yes.
    agent._create_with_retry(client, label="test", model="m", max_tokens=5, messages=[])
    assert len(client.sent) == 1


def test_an_unreadable_answer_fails_closed(monkeypatch):
    """CATCH. If the answer can't be read, nothing is sent."""
    def boom(hid=None):
        raise RuntimeError("database is locked")

    monkeypatch.setattr(ai_consent, "has_consent", boom)
    client = _CountingClient()
    with pytest.raises(agent.AIConsentRequiredError):
        agent._create_with_retry(client, label="test", model="m", max_tokens=5, messages=[])
    assert client.sent == []


# --------------------------------------------------------------------------
# Work nobody is watching
# --------------------------------------------------------------------------


def test_a_background_thread_respects_the_households_answer(monkeypatch):
    """CATCH. The chat's theme label runs in its own thread, after the reply — and still asks first."""
    other = _new_household()
    withdraw_ai_consent(other)
    client = _CountingClient()
    monkeypatch.setattr(chat_themes, "_client", lambda: client)
    result = {}

    def run():
        result["theme"] = chat_themes.classify("what's for dinner")

    with tools.use_household(other):
        ctx = contextvars.copy_context()
    thread = threading.Thread(target=lambda: ctx.run(run))
    thread.start()
    thread.join()
    assert result["theme"] == ""
    assert client.sent == []


def test_chat_turn_is_refused_with_the_plain_line(signed_in, monkeypatch):
    """CATCH. The chat says the sentence and sends nothing."""
    withdraw_ai_consent(1)
    client = _CountingClient()
    monkeypatch.setattr(agent, "_client", lambda: client)
    res = signed_in.post("/api/chat", json={"message": "plan dinner"})
    assert res.status_code == 503
    assert res.json()["detail"] == ai_consent.REFUSAL_LINE
    assert client.sent == []


def test_a_refusal_behind_a_generic_500_still_reads_as_the_plain_line():
    """
    CATCH. Most routes wrap their work in `except Exception: raise
    HTTPException(500, ...)`. A refusal underneath one comes out as the 503
    sentence, not "something went wrong", and isn't filed as breakage.
    """
    mini = FastAPI()
    mini.add_exception_handler(StarletteHTTPException, main.record_server_errors)

    @mini.get("/api/wrapped")
    def wrapped():
        try:
            raise agent.AIConsentRequiredError(ai_consent.REFUSAL_LINE)
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Server error: {e}")

    res = TestClient(mini).get("/api/wrapped")
    assert res.status_code == 503
    assert res.json()["detail"] == ai_consent.REFUSAL_LINE
    assert res.json()["ai_consent_required"] is True


# --------------------------------------------------------------------------
# The answer: stored, read back, changeable
# --------------------------------------------------------------------------


def test_allowing_stores_the_date_the_wording_and_who(signed_in):
    """CATCH. The yes is kept per household with its date and wording version."""
    withdraw_ai_consent(1)
    assert signed_in.get("/api/whoami").json()["ai_consent"] == ""
    res = signed_in.post("/api/ai-consent", json={"allow": True})
    assert res.status_code == 200
    body = res.json()
    assert body["status"] == "granted"
    assert body["version"] == ai_consent.CONSENT_VERSION
    assert body["at"]
    assert signed_in.get("/api/whoami").json()["ai_consent"] == "granted"
    assert signed_in.get("/api/ai-consent").json()["status"] == "granted"


def test_turning_it_off_stops_the_next_call(signed_in, monkeypatch):
    """CATCH. Preferences' Turn off is real: the very next AI call is refused."""
    client = _CountingClient()
    monkeypatch.setattr(agent, "_client", lambda: client)
    assert signed_in.post("/api/ai-consent", json={"allow": False}).json()["status"] == "declined"
    res = signed_in.post("/api/chat", json={"message": "plan dinner"})
    assert res.status_code == 503
    assert client.sent == []


def test_consent_state_is_read_from_this_households_row_only(signed_in):
    """GUARD (mutation: record without a WHERE). Saying yes in one house changes no other."""
    other = _new_household()
    withdraw_ai_consent(other)
    withdraw_ai_consent(1)
    signed_in.post("/api/ai-consent", json={"allow": True})
    assert ai_consent.state(other)["status"] == ""


def test_existing_households_arrive_unanswered_and_keep_everything():
    """
    CATCH. The migration adds the answer as '' — so Emily's household and the
    beta testers' are asked once on their next visit, and no column they
    already have is touched.
    """
    conn = get_conn()
    cols = {r["name"]: r for r in conn.execute("PRAGMA table_info(households)")}
    conn.close()
    assert cols["ai_consent"]["dflt_value"] == "''"
    assert cols["ai_consent_version"]["dflt_value"] == "''"
    assert "ai_consent_at" in cols and "ai_consent_member_id" in cols


def test_the_chat_agent_has_no_way_to_grant_consent():
    """GUARD. The model must never be able to say yes for the household."""
    import pathlib

    tools_dir = pathlib.Path(tools.__file__).parent
    for path in tools_dir.glob("*.py"):
        text = path.read_text()
        assert "ai_consent" not in text, f"{path.name} reaches the consent record"
    names = {t["name"] for t in agent.TOOL_DEFINITIONS}
    assert names, "the chat's tool catalogue should be readable here"
    assert not any("consent" in n for n in names)


def test_a_real_bug_after_a_refusal_is_still_reported_as_a_bug():
    """
    GUARD (mutation: walk the whole cause chain). Only the refusal itself,
    or the exception raised straight from it, reads as the consent line; a
    genuine failure further down stays a 500 and is recorded as breakage.
    """
    mini = FastAPI()
    mini.add_exception_handler(StarletteHTTPException, main.record_server_errors)

    @mini.get("/api/wrapped-bug")
    def wrapped_bug():
        try:
            try:
                raise agent.AIConsentRequiredError(ai_consent.REFUSAL_LINE)
            except agent.AssistantUnavailableError:
                raise KeyError("a real bug in the fallback")
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Server error: {e}")

    res = TestClient(mini).get("/api/wrapped-bug")
    assert res.status_code == 500
