"""
Cheaper chat (Emily, 2026-11-03 — fixes A, B, C, D all approved).

Live, one household, Oct 25–Nov 3: chat was ~$1.15 of $3.75, most of it
writes of a ~41K-token cached prefix (SYSTEM_PROMPT + ~139 tool schemas),
eight of them warm-ups fired by opening the sheet.

A. The warm-up waits for a keystroke, never fires on open.
B. A request loads only CHAT_ALWAYS_LOADED_TOOLS; every other tool is
   still sent, marked defer_loading, behind the server-side tool search
   tool — one search away, and the dispatch runs it as before.
C. The stable prefix carries a 1-hour cache breakpoint; the conversation
   tail keeps the 5-minute automatic one.
D. The report's target is $1.50 per household per month.

No network: the model is a stub throughout. Where a response has to carry
server-tool blocks it is a real SDK Message, so the history is stored and
resent exactly as production would.
"""
from __future__ import annotations

import json
import shutil
import time
import types
from pathlib import Path

import anthropic
import nodeharness
import pytest

from app import agent, tools

REPO = Path(__file__).resolve().parent.parent
SHELL_JS = (REPO / "static" / "shell.js").read_text(encoding="utf-8")

_needs_node = pytest.mark.skipif(
    shutil.which("node") is None, reason="node is needed to execute the shell's own functions"
)


class _Usage:
    input_tokens = cache_read_input_tokens = cache_creation_input_tokens = output_tokens = 0


def _final(text="Done."):
    return types.SimpleNamespace(
        content=[types.SimpleNamespace(type="text", text=text)], stop_reason="end_turn", usage=_Usage(),
    )


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


def _sdk_message(content, stop_reason):
    """A real anthropic Message, parsed the way the SDK parses a response."""
    return anthropic.types.Message.model_validate({
        "id": "msg_test", "type": "message", "role": "assistant", "model": agent.CHAT_MODEL,
        "content": content, "stop_reason": stop_reason, "stop_sequence": None,
        "usage": {"input_tokens": 1, "output_tokens": 1,
                  "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0},
    })


def _one_turn_request(monkeypatch, message="what's for dinner?"):
    fake = _stub_client(monkeypatch, [_final("Sure.")])
    agent.run_agent_turn([], message)
    assert len(fake.requests) == 1
    return fake.requests[0]


def _catalogue_names():
    off = not tools.chores_enabled()
    return [d["name"] for d in agent.TOOL_DEFINITIONS if not (off and d["name"] in agent.CHORES_TOOLS)]


# --------------------------------------------------------------------------
# B. Only what a message needs is loaded; everything stays reachable
# --------------------------------------------------------------------------

def test_a_request_loads_only_the_everyday_tools_and_defers_the_rest(monkeypatch):
    sent = _one_turn_request(monkeypatch)["tools"]
    loaded = [d["name"] for d in sent if not d.get("defer_loading")]
    expected = [n for n in _catalogue_names() if n in agent.CHAT_ALWAYS_LOADED_TOOLS]
    assert loaded == expected + ["tool_search_tool_regex"]
    deferred = {d["name"] for d in sent if d.get("defer_loading")}
    assert deferred == set(_catalogue_names()) - agent.CHAT_ALWAYS_LOADED_TOOLS


def test_the_search_tool_is_the_supported_server_tool_and_is_never_deferred(monkeypatch):
    sent = _one_turn_request(monkeypatch)["tools"]
    search = [d for d in sent if d.get("type", "").startswith("tool_search_tool")]
    assert search == [{"type": "tool_search_tool_regex_20251119", "name": "tool_search_tool_regex"}]
    # The API refuses a deferred tool that also carries cache_control.
    assert not any(d.get("defer_loading") and "cache_control" in d for d in sent)


def test_every_tool_chat_can_use_today_is_still_sent_in_full(monkeypatch):
    """Nothing is dropped: a deferred definition is the catalogue's own,
    byte for byte, plus the flag — the API needs it to expand a search hit."""
    sent = {d["name"]: d for d in _one_turn_request(monkeypatch)["tools"]}
    for d in agent.TOOL_DEFINITIONS:
        if d["name"] not in _catalogue_names():
            continue
        assert {k: v for k, v in sent[d["name"]].items() if k != "defer_loading"} == d


def test_the_loaded_briefing_is_well_under_a_quarter_of_the_catalogue(monkeypatch):
    sent = _one_turn_request(monkeypatch)["tools"]
    loaded = json.dumps([d for d in sent if not d.get("defer_loading")])
    whole = json.dumps(agent.TOOL_DEFINITIONS)
    assert len(loaded) < 0.25 * len(whole), (len(loaded), len(whole))


def test_every_always_loaded_name_is_a_real_tool():
    assert agent.CHAT_ALWAYS_LOADED_TOOLS <= set(agent.TOOL_FUNCTIONS)
    assert agent.CHAT_ALWAYS_LOADED_TOOLS <= {d["name"] for d in agent.TOOL_DEFINITIONS}
    assert not (agent.CHAT_ALWAYS_LOADED_TOOLS & agent.CHORES_TOOLS), "Chores is paused"


def test_the_prompt_tells_the_model_how_to_reach_a_deferred_tool():
    assert "tool_search_tool_regex" in agent.SYSTEM_PROMPT
    assert "FINDING A TOOL" in agent.SYSTEM_PROMPT


def test_the_cached_prefix_is_the_same_every_turn_and_either_side_of_the_chores_switch(monkeypatch):
    """Deferred tools are kept out of the prefix by the API, so what is
    cached is the loaded tools + the search tool + SYSTEM_PROMPT — and the
    chores switch, whose tools are all deferred, no longer splits it."""
    def prefix(req):
        return ([d for d in req["tools"] if not d.get("defer_loading")], req["system"][0])

    first = prefix(_one_turn_request(monkeypatch))
    second = prefix(_one_turn_request(monkeypatch, "and tomorrow?"))
    tools.set_chores_enabled(True)
    on = prefix(_one_turn_request(monkeypatch))
    assert first == second == on


def test_a_deferred_tool_found_by_search_runs_and_the_search_is_kept_in_history(monkeypatch):
    """The on-demand path end to end: the model searches (server side),
    calls the tool the search loaded, the dispatch runs it, and the next
    request sends the search blocks back unchanged — then the stored copy
    of the conversation is plain JSON."""
    assert "swap_dinner_nights" not in agent.CHAT_ALWAYS_LOADED_TOOLS
    ran = []
    monkeypatch.setitem(agent.TOOL_FUNCTIONS, "swap_dinner_nights",
                        lambda **kw: ran.append(kw) or {"ok": True, "message": "Swapped."})
    searched = _sdk_message([
        {"type": "server_tool_use", "id": "srvtoolu_1", "name": "tool_search_tool_regex",
         "input": {"pattern": "^swap_dinner_nights$"}},
        {"type": "tool_search_tool_result", "tool_use_id": "srvtoolu_1",
         "content": {"type": "tool_search_tool_search_result",
                     "tool_references": [{"type": "tool_reference", "tool_name": "swap_dinner_nights"}]}},
        {"type": "tool_use", "id": "toolu_1", "name": "swap_dinner_nights",
         "input": {"day_a": "Tuesday", "day_b": "Thursday"}},
    ], "tool_use")
    done = _sdk_message([{"type": "text", "text": "Tuesday and Thursday are swapped."}], "end_turn")
    fake = _stub_client(monkeypatch, [searched, done])
    reply, conversation = agent.run_agent_turn([], "swap tuesday and thursday")

    assert ran == [{"day_a": "Tuesday", "day_b": "Thursday"}]
    assert reply == "Tuesday and Thursday are swapped."
    resent = fake.requests[1]["messages"][1]["content"]
    assert [b.type for b in resent] == ["server_tool_use", "tool_search_tool_result", "tool_use"]

    from app import main as main_module
    stored = main_module._history_for_storage(conversation)
    text = json.dumps(stored)
    assert '"tool_search_tool_result"' in text and '"tool_reference"' in text
    assert json.loads(text)[1]["content"][1]["content"]["tool_references"][0]["tool_name"] == "swap_dinner_nights"


def test_a_paused_server_turn_is_resumed_not_answered(monkeypatch):
    """pause_turn (the server's own loop paused) is sent back as is and the
    model carries on — it is not a final reply, and no user line is added."""
    paused = _sdk_message([
        {"type": "server_tool_use", "id": "srvtoolu_1", "name": "tool_search_tool_regex",
         "input": {"pattern": "staple"}},
    ], "pause_turn")
    fake = _stub_client(monkeypatch, [paused, _final("Milk is on your staples.")])
    reply, _ = agent.run_agent_turn([], "add milk as a staple")
    assert reply == "Milk is on your staples."
    assert len(fake.requests) == 2
    assert fake.requests[1]["messages"][-1]["role"] == "assistant"


# --------------------------------------------------------------------------
# C. The prefix is kept for an hour; the tail for five minutes
# --------------------------------------------------------------------------

def test_the_prefix_breakpoint_is_one_hour_and_the_tail_stays_five_minutes(monkeypatch):
    req = _one_turn_request(monkeypatch)
    assert req["system"][0] == {"type": "text", "text": agent.SYSTEM_PROMPT,
                                "cache_control": {"type": "ephemeral", "ttl": "1h"}}
    # A longer TTL may only come before a shorter one: 1h prefix, 5m tail.
    assert req["cache_control"] == {"type": "ephemeral"}
    assert all("cache_control" not in b for b in req["system"][1:])


def test_the_warmup_writes_exactly_the_prefix_a_real_round_reads(monkeypatch):
    agent._CHAT_CACHE_WARM_AT.clear()
    warm = []
    monkeypatch.setattr(agent, "_client", lambda: types.SimpleNamespace(
        messages=types.SimpleNamespace(create=lambda **kw: warm.append(kw) or _final())))
    assert agent.warm_chat_cache() is True
    real = _one_turn_request(monkeypatch)
    (w,) = warm
    assert w["tools"] == real["tools"]
    assert w["system"] == [real["system"][0]]
    assert w["model"] == real["model"] and w["max_tokens"] == 0
    agent._CHAT_CACHE_WARM_AT.clear()


def test_a_warmup_twenty_minutes_ago_is_still_fresh_with_the_hour_long_cache():
    """On main the window was three minutes (a five-minute cache); with a
    one-hour prefix a second warm-up inside the hour is a wasted write."""
    agent._CHAT_CACHE_WARM_AT.clear()
    try:
        assert agent.claim_chat_warmup() is True
        key = next(iter(agent._CHAT_CACHE_WARM_AT))
        agent._CHAT_CACHE_WARM_AT[key] = time.monotonic() - 20 * 60
        assert agent.claim_chat_warmup() is False
        agent._CHAT_CACHE_WARM_AT[key] = time.monotonic() - 61 * 60
        assert agent.claim_chat_warmup() is True
    finally:
        agent._CHAT_CACHE_WARM_AT.clear()


# --------------------------------------------------------------------------
# A. No warm-up on open; one when typing starts
# --------------------------------------------------------------------------

def _function_source(name):
    a = SHELL_JS.index(f"  function {name}(")
    depth, i = 0, SHELL_JS.index("{", a)
    while True:
        c = SHELL_JS[i]
        depth += c == "{"
        depth -= c == "}"
        i += 1
        if depth == 0:
            return SHELL_JS[a:i]


def test_opening_the_sheet_sends_no_warmup():
    assert "warmAskCache" not in _function_source("openAskSheet")


@_needs_node
def test_typing_the_first_character_asks_for_the_warmup_and_nothing_before():
    listener_at = SHELL_JS.index("  askInput.addEventListener('input', function () {")
    listener = SHELL_JS[listener_at:SHELL_JS.index("  });", listener_at) + len("  });")]
    script = """
var calls = [];
var Api = { fetch: function (path, init) { calls.push([path, init && init.method]); return Promise.resolve({}); } };
var navigator = { onLine: true };
var handlers = {};
var askInput = { value: '', addEventListener: function (ev, fn) { handlers[ev] = fn; } };
var askDraftIsPreset = true;
function autoGrowAskInput() {}
var ASK_WARM_MIN_GAP_MS = 120000;
var askLastWarmAt = 0;
""" + _function_source("warmAskCache") + "\n" + listener + """
var log = [];
handlers.input(); log.push(calls.length);            // an empty box: nothing
askInput.value = 'w'; handlers.input(); log.push(calls.length);
askInput.value = 'we'; handlers.input(); log.push(calls.length);  // throttled client side
console.log(JSON.stringify({log: log, calls: calls}));
"""
    res = nodeharness.run_node(script, timeout=30)
    assert res.returncode == 0, res.stderr
    out = json.loads(res.stdout.strip())
    assert out["log"] == [0, 1, 1]
    assert out["calls"] == [["/api/chat/warm", "POST"]]


# --------------------------------------------------------------------------
# D. The target is $1.50
# --------------------------------------------------------------------------

def test_the_monthly_target_is_a_dollar_fifty(capsys):
    import observability_report

    assert observability_report._MONTHLY_TARGET_DOLLARS == 1.50
    observability_report._print_human(
        [{"household": "The Test Household", "household_id": 7,
          "errors": {"total": 0, "by_kind": {}, "recent": []},
          "usage": {"days": 7, "chat_turns": 3, "meals_cooked": 1, "plans_generated": 1,
                    "plans_approved": 1, "looks_inactive": False, "last_active_at": "2026-11-02",
                    "month_to_date_cost": {
                        "total_cost": {"input": 0.0, "cache_read": 0.0, "cache_write": 0.0,
                                       "output": 0.0, "total": 1.20},
                        "by_call_site": {}}}}],
        days=1, source="a test",
    )
    out = capsys.readouterr().out
    assert "vs target $1.50/household/month: under" in out
