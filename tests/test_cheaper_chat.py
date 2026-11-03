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


def test_a_searched_turn_goes_through_the_stream_route_is_stored_and_resent(signed_in, monkeypatch):
    """Over the real /api/chat/stream: the turn with a tool search reaches
    "done", is stored in the chat_sessions table, and the NEXT turn sends
    the stored search blocks back to the model."""
    monkeypatch.setitem(agent.TOOL_FUNCTIONS, "list_staples", lambda **kw: {"staples": []})
    searched = _sdk_message([
        {"type": "server_tool_use", "id": "srvtoolu_1", "name": "tool_search_tool_regex",
         "input": {"pattern": "^list_staples$"}},
        {"type": "tool_search_tool_result", "tool_use_id": "srvtoolu_1",
         "content": {"type": "tool_search_tool_search_result",
                     "tool_references": [{"type": "tool_reference", "tool_name": "list_staples"}]}},
        {"type": "tool_use", "id": "toolu_1", "name": "list_staples", "input": {}},
    ], "tool_use")
    fake = _stub_client(monkeypatch, [
        searched,
        _sdk_message([{"type": "text", "text": "No staples yet."}], "end_turn"),
        _sdk_message([{"type": "text", "text": "Of course."}], "end_turn"),
    ])
    for message in ("what are our staples?", "thanks"):
        with signed_in.stream("POST", "/api/chat/stream", json={"message": message}) as response:
            assert response.status_code == 200
            lines = list(response.iter_lines())
        assert any(line.startswith("event: done") for line in lines), lines
    third = fake.requests[2]["messages"]
    blocks = [b for m in third if isinstance(m["content"], list) for b in m["content"]]
    kinds = [b["type"] if isinstance(b, dict) else b.type for b in blocks]
    assert "server_tool_use" in kinds and "tool_search_tool_result" in kinds


# ==========================================================================
# Review fixes (2026-11-03)
# ==========================================================================

# --- 1. 1-hour cache writes are priced at 2x, 5-minute at 1.25x ----------

def test_price_tokens_splits_one_hour_writes_from_five_minute_ones():
    from app.tools import usage as usage_module
    haiku = agent.CHAT_MODEL
    mixed = usage_module.price_tokens({"cache_write": 1_000_000, "cache_write_1h": 400_000}, haiku)
    assert mixed["cache_write"] == pytest.approx(0.6 * 1.25 + 0.4 * 2.00)
    assert mixed["total"] == pytest.approx(1.55)
    # No split (every row before 2026-11-03): all five-minute writes, as before.
    old = usage_module.price_tokens({"cache_write": 1_000_000}, haiku)
    assert old["cache_write"] == pytest.approx(1.25)
    sonnet = usage_module.price_tokens({"cache_write": 1_000_000, "cache_write_1h": 1_000_000}, "claude-sonnet-5")
    assert sonnet["cache_write"] == pytest.approx(4.00)
    assert set(mixed) == {"input", "cache_read", "cache_write", "output", "total", "model"}


def _usage_with_split(write_5m, write_1h, model=None):
    return types.SimpleNamespace(
        input_tokens=0, cache_read_input_tokens=0, output_tokens=0,
        cache_creation_input_tokens=write_5m + write_1h,
        cache_creation=types.SimpleNamespace(ephemeral_5m_input_tokens=write_5m,
                                             ephemeral_1h_input_tokens=write_1h),
    )


def test_an_api_call_records_its_one_hour_writes_and_the_month_prices_them():
    from app.db import get_conn
    response = types.SimpleNamespace(usage=_usage_with_split(100_000, 900_000))
    agent._record_api_call("run_agent_turn", agent.CHAT_MODEL, response, 1.0)
    conn = get_conn()
    try:
        row = conn.execute("SELECT cache_write_tokens, cache_write_1h_tokens FROM api_calls "
                           "ORDER BY id DESC LIMIT 1").fetchone()
    finally:
        conn.close()
    assert (row["cache_write_tokens"], row["cache_write_1h_tokens"]) == (1_000_000, 900_000)
    month = tools.get_month_to_date_cost()
    assert month["by_call_site"]["run_agent_turn"]["cost"]["cache_write"] == pytest.approx(0.1 * 1.25 + 0.9 * 2.0)


def test_a_chat_turn_records_its_one_hour_writes_and_chat_cost_prices_them(monkeypatch):
    from app.db import get_conn
    from app.tools import usage as usage_module
    response = types.SimpleNamespace(
        content=[types.SimpleNamespace(type="text", text="Hi.")], stop_reason="end_turn",
        usage=_usage_with_split(0, 20_000), model=agent.CHAT_MODEL,
    )
    _stub_client(monkeypatch, [response])
    agent.run_agent_turn([], "hello")
    turn = agent.LAST_TURN_USAGE.get()
    assert turn["cache_write_1h_tokens"] == 20_000 and turn["cache_write_tokens"] == 20_000
    tools.record_chat_turn(turn)
    conn = get_conn()
    try:
        cost = usage_module._chat_cost_by_model(conn, tools.household_id(), "-7 days")
    finally:
        conn.close()
    assert cost["cache_write"] == pytest.approx(20_000 * 2.00 / 1_000_000)


def test_an_old_database_gains_the_column_and_its_rows_price_as_five_minute(tmp_path, monkeypatch):
    import sqlite3
    from app import db
    path = tmp_path / "old.db"
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE api_calls (id INTEGER PRIMARY KEY, household_id INTEGER, call_site TEXT, "
                 "model TEXT NOT NULL DEFAULT '', input_tokens INTEGER NOT NULL DEFAULT 0, "
                 "cache_read_tokens INTEGER NOT NULL DEFAULT 0, cache_write_tokens INTEGER NOT NULL DEFAULT 0, "
                 "output_tokens INTEGER NOT NULL DEFAULT 0, seconds REAL NOT NULL DEFAULT 0)")
    conn.execute("INSERT INTO api_calls (household_id, call_site, model, cache_write_tokens) "
                 "VALUES (1, 'run_agent_turn', ?, 1000)", (agent.CHAT_MODEL,))
    conn.commit()
    names = {r[1] for r in conn.execute("PRAGMA table_info(api_calls)")}
    assert "cache_write_1h_tokens" not in names
    conn.close()
    assert ("api_calls", "cache_write_1h_tokens", "INTEGER NOT NULL DEFAULT 0") in db._MIGRATIONS
    assert ("chat_turns", "cache_write_1h_tokens", "INTEGER NOT NULL DEFAULT 0") in db._MIGRATIONS
    conn = sqlite3.connect(path)
    for table, column, decl in db._MIGRATIONS:
        if column == "cache_write_1h_tokens" and table == "api_calls":
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")
    assert conn.execute("SELECT cache_write_1h_tokens FROM api_calls").fetchone()[0] == 0
    conn.close()


# --- 2. stored history never replays an unloaded deferred tool -----------

def _turn(user, name, block_id, search=False):
    """One stored turn: the person's words, an assistant call to `name`
    (after a search that loaded it when `search`), its result, a reply."""
    content = []
    if search:
        content += [
            {"type": "server_tool_use", "id": f"srv_{block_id}", "name": "tool_search_tool_regex",
             "input": {"pattern": f"^{name}$"}},
            {"type": "tool_search_tool_result", "tool_use_id": f"srv_{block_id}",
             "content": {"type": "tool_search_tool_search_result",
                         "tool_references": [{"type": "tool_reference", "tool_name": name}]}},
        ]
    content.append({"type": "tool_use", "id": block_id, "name": name, "input": {}})
    return [
        {"role": "user", "content": user},
        {"role": "assistant", "content": content},
        {"role": "user", "content": [{"type": "tool_result", "tool_use_id": block_id, "content": "{}"}]},
        {"role": "assistant", "content": [{"type": "text", "text": "Done."}]},
    ]


def test_history_with_only_loaded_or_searched_tools_is_kept_whole():
    wire = agent.tools_for_request()
    history = _turn("what's on?", "get_weekly_plan", "t1") + _turn("staples?", "list_staples", "t2", search=True)
    assert agent.history_safe_for_deferred_tools(history, wire) is history


def test_a_pre_deploy_history_is_cut_at_the_first_clean_turn():
    """Stored before the deploy: list_staples was called with every tool
    loaded, so no search in the history names it. That turn goes; the clean
    turn after it stays."""
    wire = agent.tools_for_request()
    old = _turn("staples?", "list_staples", "t1")
    clean = _turn("what's on?", "get_weekly_plan", "t2")
    assert agent.history_safe_for_deferred_tools(old + clean, wire) == clean
    assert agent.history_safe_for_deferred_tools(clean + old, wire) == []


def test_a_trim_that_cuts_the_searching_turn_drops_the_later_use_too():
    """Turn 1 searched for list_staples; turn 2 reused it without searching
    again (allowed — the API expands the earlier result). Trimming turn 1
    away orphans turn 2, so both go."""
    wire = agent.tools_for_request()
    first = _turn("staples?", "list_staples", "t1", search=True)
    reuse = _turn("and again?", "list_staples", "t2")
    last = _turn("what's on?", "get_weekly_plan", "t3")
    whole = first + reuse + last
    assert agent.history_safe_for_deferred_tools(whole, wire) is whole
    trimmed = agent.trim_conversation(whole, max_turns=2)
    assert trimmed == reuse + last
    assert agent.history_safe_for_deferred_tools(trimmed, wire) == last


def test_the_route_does_not_send_a_stored_unloaded_deferred_call(signed_in, monkeypatch):
    """Over /api/chat/stream: turn one calls a deferred tool with no search
    (what a pre-deploy history looks like); turn two's request leaves that
    turn out instead of replaying it."""
    monkeypatch.setitem(agent.TOOL_FUNCTIONS, "list_staples", lambda **kw: {"staples": []})
    direct = _sdk_message([{"type": "tool_use", "id": "toolu_1", "name": "list_staples", "input": {}}], "tool_use")
    fake = _stub_client(monkeypatch, [
        direct,
        _sdk_message([{"type": "text", "text": "None yet."}], "end_turn"),
        _sdk_message([{"type": "text", "text": "Of course."}], "end_turn"),
    ])
    for message in ("staples?", "thanks"):
        with signed_in.stream("POST", "/api/chat/stream", json={"message": message}) as response:
            assert response.status_code == 200
            list(response.iter_lines())
    sent = fake.requests[2]["messages"]
    # The request list is the turn's own and grows after the call; what was sent starts here.
    assert sent[0] == {"role": "user", "content": "thanks"}
    assert not any(isinstance(m["content"], list) and any(getattr(b, "name", None) == "list_staples" or (isinstance(b, dict) and b.get("name") == "list_staples") for b in m["content"]) for m in sent)


# --- 3. the client warms once a sitting ----------------------------------

@_needs_node
def test_the_client_warms_once_a_sitting_not_on_every_pause():
    listener_at = SHELL_JS.index("  askInput.addEventListener('input', function () {")
    listener = SHELL_JS[listener_at:SHELL_JS.index("  });", listener_at) + len("  });")]
    script = """
var calls = [];
var now = 1e12;
Date.now = function () { return now; };
var Api = { fetch: function (path) { calls.push(path); return Promise.resolve({}); } };
var navigator = { onLine: true };
var handlers = {};
var askInput = { value: '', addEventListener: function (ev, fn) { handlers[ev] = fn; } };
var askDraftIsPreset = false;
function autoGrowAskInput() {}
""" + SHELL_JS[SHELL_JS.index("  var ASK_WARM_MIN_GAP_MS"):SHELL_JS.index("  var askLastWarmAt = 0;")] + """
var askLastWarmAt = 0;
""" + _function_source("warmAskCache") + "\n" + listener + """
var log = [];
askInput.value = 'w'; handlers.input(); log.push(calls.length);
now += 3 * 60 * 1000; askInput.value = 'we'; handlers.input(); log.push(calls.length);   // a pause: no second warm
now += 30 * 60 * 1000; askInput.value = 'wed'; handlers.input(); log.push(calls.length);  // still inside the hour
now += 25 * 60 * 1000; askInput.value = 'wedn'; handlers.input(); log.push(calls.length); // 58 min: due again
console.log(JSON.stringify(log));
"""
    res = nodeharness.run_node(script, timeout=30)
    assert res.returncode == 0, res.stderr
    assert json.loads(res.stdout.strip()) == [1, 1, 1, 2]


def test_sending_a_message_counts_as_the_warm_up():
    a = SHELL_JS.index("  async function sendAskMessage(")
    body = SHELL_JS[a:SHELL_JS.index("ensureAskSheetBuilt()", a)]
    assert "askLastWarmAt = Date.now();" in body.split("ensureAskSheetBuilt()")[0]


@_needs_node
def test_starting_dictation_warms_the_chat():
    script = """
var warmed = 0;
function warmAskCache() { warmed += 1; }
function autoGrowAskInput() {}
function showToast() {}
function SpeechRecognitionCtor() { this.start = function () {}; this.stop = function () {}; }
var askInput = { value: '', placeholder: 'Ask', focus: function () {} };
var clicks = [];
var micBtn = { dataset: {}, classList: { add: function () {}, remove: function () {} },
               addEventListener: function (ev, fn) { clicks.push(fn); } };
""" + _function_source("setupDictation") + """
setupDictation(askInput, micBtn);
clicks[0]();
var other = { value: '', placeholder: '', focus: function () {} };
var otherBtn = { dataset: {}, classList: { add: function () {}, remove: function () {} },
                 addEventListener: function (ev, fn) { clicks.push(fn); } };
setupDictation(other, otherBtn);
clicks[1]();
console.log(JSON.stringify(warmed));
"""
    res = nodeharness.run_node(script, timeout=30)
    assert res.returncode == 0, res.stderr
    assert json.loads(res.stdout.strip()) == 1


# --- 4. a fallback round doesn't hold the chat model's warm-up off -------

def test_a_round_the_fallback_answered_leaves_the_warm_up_due(monkeypatch):
    agent._CHAT_CACHE_WARM_AT.clear()
    try:
        answered = types.SimpleNamespace(
            content=[types.SimpleNamespace(type="text", text="Hi.")], stop_reason="end_turn",
            usage=_Usage(), model=agent.CHAT_FALLBACK_MODEL,
        )
        _stub_client(monkeypatch, [answered])
        agent.run_agent_turn([], "hello")
        assert agent.claim_chat_warmup() is True
        agent._CHAT_CACHE_WARM_AT.clear()
        own = types.SimpleNamespace(
            content=[types.SimpleNamespace(type="text", text="Hi.")], stop_reason="end_turn",
            usage=_Usage(), model=agent.CHAT_MODEL,
        )
        _stub_client(monkeypatch, [own])
        agent.run_agent_turn([], "hello")
        assert agent.claim_chat_warmup() is False
    finally:
        agent._CHAT_CACHE_WARM_AT.clear()


# --- 5. the prompt doesn't send a chores-off house looking for chores ----

def test_finding_a_tool_keeps_chores_out_while_they_are_off():
    note = agent.SYSTEM_PROMPT[agent.SYSTEM_PROMPT.index("FINDING A TOOL"):]
    assert "chores tools exist only in a house with Chores switched on" in note
    assert "never search for or call them" in note
