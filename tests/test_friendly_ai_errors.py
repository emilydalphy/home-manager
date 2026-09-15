"""
Loop Board (HIGH · Bug): "Chat and toasts show raw error text when the AI
call fails".

With a bad or expired Anthropic key the chat showed, as the assistant's
own reply, "Error: Server error: Error code: 401 - {'type': 'error',
'error': {'type': 'authentication_error', ...}, 'request_id': ...}", and
"Change the protein" → Save toasted the same wall of text. The fix is one
rule in one place — main._client_safe_detail, applied by the HTTP
exception handler and by _sse_event's "error" frame — so the 146 routes
that build `detail=f"Server error: {e}"` never have to change, and a route
nobody has written yet is covered too.

The Anthropic failure here is the real class (anthropic.AuthenticationError
raised by the stubbed client), so the path under test is the one the bogus
key actually takes: a 401 is not retryable, so _create_with_retry re-raises
it as-is, the route's generic `except Exception` catches it, and what the
browser reads is whatever the handler lets through.
"""
from __future__ import annotations

import json
import re
import types
from pathlib import Path

import httpx
import pytest
from anthropic import AuthenticationError

from app import agent, main as main_module
from app.agent import AssistantUnavailableError

REPO = Path(__file__).resolve().parents[1]
SHELL_JS = (REPO / "static" / "shell.js").read_text(encoding="utf-8")

# The three tells the card names, plus the two shapes Pomona's voice never
# has: a status code and a bare "Error:" prefix.
RAW_TELLS = ("Error code", "request_id", "{'type'", "Server error", "401", "Error:")


def _auth_error():
    body = {
        "type": "error",
        "error": {"type": "authentication_error", "message": "invalid x-api-key"},
        "request_id": "req_011TESTTESTTEST",
    }
    resp = httpx.Response(
        401, request=httpx.Request("POST", "https://api.anthropic.com/v1/messages"), json=body,
    )
    return AuthenticationError(f"Error code: 401 - {body}", response=resp, body=body)


class _FailingMessages:
    def __init__(self, exc):
        self._exc = exc

    def create(self, **kwargs):
        raise self._exc

    def stream(self, **kwargs):
        raise self._exc


@pytest.fixture
def bogus_key_client(monkeypatch):
    """Every Anthropic call answers 401 — the bogus-key server, in a test."""
    exc = _auth_error()
    monkeypatch.setattr(
        agent, "_client", lambda: types.SimpleNamespace(messages=_FailingMessages(exc))
    )
    return exc


def _sse_events(raw_lines):
    events, name = [], None
    for chunk in raw_lines:
        for line in chunk.split("\n"):
            if line.startswith("event:"):
                name = line[len("event:"):].strip()
            elif line.startswith("data:"):
                events.append((name, json.loads(line[len("data:"):].strip())))
    return events


def _assert_one_plain_sentence(text: str):
    for tell in RAW_TELLS:
        assert tell not in text, f"{tell!r} reached the person: {text!r}"
    assert "{" not in text and "}" not in text, text
    assert text == main_module.SERVER_TROUBLE_LINE


# ---------- the chat, over the stream the shell actually uses ----------


def test_chat_stream_hides_a_raw_anthropic_error_behind_one_plain_sentence(signed_in, bogus_key_client):
    """
    The card's own acceptance test: a faked anthropic.AuthenticationError
    on /api/chat/stream, and the client-facing text contains none of
    `Error code`, `request_id`, `{'type'`.
    """
    with signed_in.stream("POST", "/api/chat/stream", json={"message": "what's for dinner?"}) as res:
        assert res.status_code == 200
        events = _sse_events(list(res.iter_lines()))

    names = [e for e, _ in events]
    assert names[-1] == "error", names
    payload = next(p for e, p in events if e == "error")
    assert payload["status"] == 500
    _assert_one_plain_sentence(payload["detail"])


def test_chat_stream_keeps_the_assistants_own_line_for_a_503(signed_in, monkeypatch):
    """
    AssistantUnavailableError's message is written for the person (that is
    the class's contract), so the scrub leaves a 503 alone — the stream
    keeps saying "I'm having trouble reaching Claude's servers…", not the
    generic line.
    """
    def fake_run_agent_turn(history, message, proactive_check=False):
        raise AssistantUnavailableError("I'm having trouble reaching Claude's servers right now.")

    monkeypatch.setattr(main_module, "run_agent_turn", fake_run_agent_turn)
    with signed_in.stream("POST", "/api/chat/stream", json={"message": "hi"}) as res:
        events = _sse_events(list(res.iter_lines()))
    payload = next(p for e, p in events if e == "error")
    assert payload["status"] == 503
    assert payload["detail"] == "I'm having trouble reaching Claude's servers right now."


def test_the_message_still_reaches_the_server_log(signed_in, bogus_key_client, caplog):
    """The person never sees it; whoever is debugging still does."""
    with caplog.at_level("ERROR", logger="home_manager"):
        with signed_in.stream("POST", "/api/chat/stream", json={"message": "hi"}) as res:
            list(res.iter_lines())
    assert any("Chat turn failed" in r.message for r in caplog.records)
    assert any(
        r.exc_info and "invalid x-api-key" in str(r.exc_info[1]) for r in caplog.records
    ), "the traceback with the real reason should be in the log"


# ---------- "Change the protein" → Save, and every other 5xx ----------


def test_change_part_500_carries_the_one_line_not_the_exception(signed_in, monkeypatch, bogus_key_client):
    monkeypatch.setattr(main_module, "_plan_id_for_week", lambda week_start: 1)

    def boom(plan_id, entry_id, role, choice):
        raise bogus_key_client

    monkeypatch.setattr(main_module.tools, "change_part", boom)
    res = signed_in.post(
        "/api/week/2026-09-14/change-part", json={"entry_id": 1, "role": "protein", "choice": "tofu"}
    )
    assert res.status_code == 500
    _assert_one_plain_sentence(res.json()["detail"])


def test_a_500_from_any_route_never_carries_household_text(signed_in, monkeypatch):
    """
    Not only Anthropic's wording: app/tools raises with household data in
    the message ("No saved recipe named '…'"), and 146 routes interpolate
    whatever they caught. The rule is per status, not per route.
    """
    secret = "Nana's secret pierogi"
    monkeypatch.setattr(
        main_module.tools, "get_usage_summary",
        lambda **k: (_ for _ in ()).throw(RuntimeError(f"No saved recipe named '{secret}'")),
    )
    res = signed_in.get("/api/observability")
    assert res.status_code == 500
    assert secret not in res.text
    assert res.json()["detail"] == main_module.SERVER_TROUBLE_LINE


def test_4xx_details_are_the_answer_and_stay(signed_in, monkeypatch):
    """
    Below 500 the detail is written for the person and must keep working:
    the ISO-date 400, the no-plan 404, change-part's own 404.
    """
    res = signed_in.post(
        "/api/week/not-a-date/change-part", json={"entry_id": 1, "role": "protein", "choice": "tofu"}
    )
    assert res.status_code == 400
    assert res.json()["detail"] == "week_start must be an ISO date (YYYY-MM-DD)."

    res = signed_in.post(
        "/api/week/2031-01-06/change-part", json={"entry_id": 1, "role": "protein", "choice": "tofu"}
    )
    assert res.status_code == 404
    assert res.json()["detail"] == "No plan for the week of 2031-01-06."

    monkeypatch.setattr(main_module, "_plan_id_for_week", lambda week_start: 1)

    def no_entry(plan_id, entry_id, role, choice):
        raise ValueError("No plan entry with id 999 in this week.")

    monkeypatch.setattr(main_module.tools, "change_part", no_entry)
    res = signed_in.post(
        "/api/week/2026-09-14/change-part", json={"entry_id": 999, "role": "protein", "choice": "tofu"}
    )
    assert res.status_code == 404
    assert res.json()["detail"] == "No plan entry with id 999 in this week."


def test_change_part_503_keeps_the_assistants_own_line(signed_in, monkeypatch):
    monkeypatch.setattr(main_module, "_plan_id_for_week", lambda week_start: 1)

    def unavailable(plan_id, entry_id, role, choice):
        raise AssistantUnavailableError("I'm having trouble reaching Claude's servers right now.")

    monkeypatch.setattr(main_module.tools, "change_part", unavailable)
    res = signed_in.post(
        "/api/week/2026-09-14/change-part", json={"entry_id": 1, "role": "protein", "choice": "tofu"}
    )
    assert res.status_code == 503
    assert "Claude" in res.json()["detail"]


def test_the_week_draft_stream_gets_the_same_rule(signed_in, monkeypatch):
    """_sse_event is shared, so plan-week's and onboarding's draft streams
    are covered by the same one place as the chat's."""
    def boom(*a, **k):
        raise RuntimeError("Error code: 401 - {'type': 'error', 'request_id': 'req_x'}")

    monkeypatch.setattr(main_module, "generate_weekly_plan", boom)
    with signed_in.stream(
        "POST", "/api/week/2026-09-14/generate/stream", json={"constraints_notes": ""}
    ) as res:
        events = _sse_events(list(res.iter_lines()))
    payload = next(p for e, p in events if e == "error")
    assert payload["status"] == 500
    _assert_one_plain_sentence(payload["detail"])


def test_the_line_is_in_pomonas_voice():
    """
    DESIGN_SYSTEM §8, calm in trouble: the thing, its way out, and the
    reassurance — in one breath, with a contraction, no exclamation mark,
    no "Error".
    """
    line = main_module.SERVER_TROUBLE_LINE
    assert line.count(".") <= 2 and "!" not in line
    assert "'" in line or "’" in line, "contractions, always (§8 Sounding human, rule 3)"
    assert "try again" in line.lower()
    assert "data" in line.lower()
    assert not re.search(r"\b\d{3}\b", line)


# ---------- the shell: what the person reads ----------


def _block(start: str, end: str) -> str:
    block = SHELL_JS[SHELL_JS.index(start):]
    return block[:block.index(end)]


def test_the_chat_shows_the_servers_sentence_never_an_error_prefix():
    ask = _block("async function sendAskMessage(message)", "Composer auto-grow")
    assert "'Error: ' + err.message" not in ask
    assert "askNoSignal ? ASK_NO_SIGNAL_LINE : ((err && err.detail) || ASK_TROUBLE_LINE)" in ask
    # The offline case is untouched.
    assert "navigator.onLine === false || (err && err.name === 'TypeError')" in ask
    assert 'var ASK_NO_SIGNAL_LINE = "I need a signal for this one — try again when you’re back.";' in SHELL_JS
    assert 'var ASK_TROUBLE_LINE = "I couldn’t think just now — your data is fine. Try again in a minute.";' in SHELL_JS


def test_the_stream_reader_marks_which_errors_carry_the_servers_sentence():
    reader = _block("async function streamChatMessage(payload, onProgress)", "var ASK_NO_SIGNAL_LINE")
    # Both places an error can come from — the non-OK response and the
    # "error" frame — go through askError, which keeps the server's own
    # detail apart from the console-only fallback message.
    assert reader.count("throw askError(") == 2
    assert "err.detail = detail || '';" in _block("function askError(detail, fallback, status)", "\n  }\n")


def test_change_the_protein_save_fails_the_swaps_way():
    fn = _block("async function runMealChangePart(choice)", "function closeMealAddSheet()")
    assert "out.detail" not in fn, "the server's detail is never toasted"
    assert "showToast(SWAP_TROUBLE, null, 6000);" in fn
    assert "err.message" not in fn
    # ...and the good pattern it copies is still what it was.
    assert "var SWAP_TROUBLE = 'That didn’t work just now — nothing changed.';" in SHELL_JS
