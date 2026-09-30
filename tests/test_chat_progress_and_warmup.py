"""
Loop Board: "Speed: chat shows what it's doing and answers sooner".

Two halves, both display/latency only (what the chat decides and saves is
untouched):

1. Progress lines. Before each tool runs, run_agent_turn hands the stream a
   short plain line ("Writing Dairy-free pancakes…") from chat_progress's
   lookup table; /api/chat/stream puts it on the wire as a "progress"
   event, in order, before "done"; the sheet swaps it in for "Thinking…".
2. Warm-up. Opening the sheet POSTs /api/chat/warm, which sends a
   `max_tokens: 0` request (the recipe pass's warm-up, same shape) so
   round 1 reads the prompt cache. Throttled per household.

No network: the model is a stub throughout.
"""
from __future__ import annotations

import json
import shutil
import time
import types
from pathlib import Path

import nodeharness
import pytest

from app import agent, chat_progress, main as main_module

REPO = Path(__file__).resolve().parent.parent
SHELL_JS = (REPO / "static" / "shell.js").read_text(encoding="utf-8")

_needs_node = pytest.mark.skipif(
    shutil.which("node") is None, reason="node is needed to execute the shell's own functions"
)


def _usage():
    return types.SimpleNamespace(
        input_tokens=0, cache_read_input_tokens=0, cache_creation_input_tokens=0, output_tokens=0,
    )


def _tool_round(*calls):
    blocks = [
        types.SimpleNamespace(type="tool_use", id=f"t{n}", name=name, input=inp)
        for n, (name, inp) in enumerate(calls)
    ]
    return types.SimpleNamespace(content=blocks, stop_reason="tool_use", usage=_usage())


def _final(text="Done."):
    return types.SimpleNamespace(
        content=[types.SimpleNamespace(type="text", text=text)], stop_reason="end_turn", usage=_usage(),
    )


def _script_model(monkeypatch, responses):
    queue = list(responses)
    monkeypatch.setattr(
        agent, "_client",
        lambda: types.SimpleNamespace(messages=types.SimpleNamespace(create=lambda **kw: queue.pop(0))),
    )


def _parse_sse(chunks):
    events, pending = [], None
    for chunk in chunks:
        for line in chunk.split("\n"):
            if line.startswith("event:"):
                pending = line[6:].strip()
            elif line.startswith("data:"):
                events.append((pending, json.loads(line[5:].strip())))
    return events


# --------------------------------------------------------------------------
# The lines
# --------------------------------------------------------------------------


def test_lines_name_the_tool_and_its_main_input():
    line = chat_progress.progress_line
    assert line("add_recipe", {"name": "Dairy-free pancakes"}) == "Writing Dairy-free pancakes…"
    assert line("plan_meal", {"meal": "Pancakes", "meal_date": "2026-10-04"}) == "Adding Pancakes to Sunday…"
    assert line("add_grocery_item", {"item": "oat milk"}) == "Adding oat milk to the shopping list…"
    assert line("take_the_night_off", {"day": "2026-10-06"}) == "Taking Tuesday off…"


def test_a_missing_input_falls_back_to_the_plain_form_not_a_hole():
    assert chat_progress.progress_line("add_recipe", {}) == "Writing the recipe…"
    assert chat_progress.progress_line("plan_meal", {"meal": "Pancakes"}) == "Adding it to the plan…"
    assert chat_progress.progress_line("add_recipe", None) == "Writing the recipe…"


def test_an_unmapped_tool_gets_the_generic_line():
    assert chat_progress.progress_line("set_holiday_region", {"x": 1}) == "One moment…"
    assert chat_progress.progress_line("get_grocery_list_by_store", {}) == "Checking the shopping list…"


def test_every_line_ends_like_thinking_and_is_one_line():
    samples = [
        ("add_recipe", {"name": "x" * 200}), ("swap_dinner_nights", {"date_a": "2026-10-04", "date_b": "2026-10-05"}),
        ("generate_weekly_plan", {}), ("nope", {}),
    ]
    for name, inp in samples:
        text = chat_progress.progress_line(name, inp)
        assert text.endswith("…") and "\n" not in text and len(text) < 80


# --------------------------------------------------------------------------
# The turn emits them, in order, and changes nothing else
# --------------------------------------------------------------------------


def test_run_agent_turn_reports_each_tool_in_order_and_dedupes_repeats(monkeypatch):
    seen = []
    _script_model(monkeypatch, [
        _tool_round(("list_recipes", {}), ("list_recipes", {})),
        _tool_round(("add_recipe", {"name": "Pancakes"})),
        _final(),
    ])
    monkeypatch.setitem(agent.TOOL_FUNCTIONS, "list_recipes", lambda **kw: [])
    monkeypatch.setitem(agent.TOOL_FUNCTIONS, "add_recipe", lambda **kw: {"ok": True})
    token = agent._TURN_PROGRESS.set(seen.append)
    try:
        reply, _ = agent.run_agent_turn([], "add pancakes")
    finally:
        agent._TURN_PROGRESS.reset(token)
    assert seen == ["Looking through your recipes…", "Writing Pancakes…"]
    assert reply == "Done."


def test_no_listener_means_the_turn_is_exactly_what_it_was(monkeypatch):
    _script_model(monkeypatch, [_tool_round(("add_recipe", {"name": "P"})), _final("Added.")])
    monkeypatch.setitem(agent.TOOL_FUNCTIONS, "add_recipe", lambda **kw: {"ok": True})
    reply, conversation = agent.run_agent_turn([], "add P")
    assert reply == "Added."
    assert conversation[-2]["content"][0]["tool_use_id"] == "t0"


def test_a_failing_listener_does_not_break_the_turn(monkeypatch):
    def boom(_line):
        raise RuntimeError("socket gone")

    _script_model(monkeypatch, [_tool_round(("add_recipe", {"name": "P"})), _final("Added.")])
    monkeypatch.setitem(agent.TOOL_FUNCTIONS, "add_recipe", lambda **kw: {"ok": True})
    token = agent._TURN_PROGRESS.set(boom)
    try:
        reply, _ = agent.run_agent_turn([], "add P")
    finally:
        agent._TURN_PROGRESS.reset(token)
    assert reply == "Added."


def test_the_stream_sends_progress_events_between_status_and_done(signed_in, monkeypatch):
    _script_model(monkeypatch, [
        _tool_round(("add_recipe", {"name": "Pancakes"})),
        _tool_round(("plan_meal", {"meal": "Pancakes", "meal_date": "2026-10-04"})),
        _final("Added."),
    ])
    monkeypatch.setitem(agent.TOOL_FUNCTIONS, "add_recipe", lambda **kw: {"ok": True})
    monkeypatch.setitem(agent.TOOL_FUNCTIONS, "plan_meal", lambda **kw: {"ok": True})
    with signed_in.stream("POST", "/api/chat/stream", json={"message": "pancakes sunday"}) as response:
        assert response.status_code == 200
        events = _parse_sse(list(response.iter_lines()))
    names = [e for e, _ in events]
    assert names[0] == "status" and names[-1] == "done"
    assert [p["message"] for e, p in events if e == "progress"] == ["Writing Pancakes…", "Adding Pancakes to Sunday…"]
    assert names == ["status", "progress", "progress", "done"]


# --------------------------------------------------------------------------
# The sheet renders them
# --------------------------------------------------------------------------


@_needs_node
def test_the_sheet_shows_the_latest_progress_line_in_place_of_thinking():
    a = SHELL_JS.index("  function askProgressBubbleText(")
    b = SHELL_JS.index("  // Opening the chat asks the server to warm", a)
    script = SHELL_JS[a:b] + """
console.log(JSON.stringify([
  askProgressBubbleText('status', {message: 'Thinking…'}, 0),
  askProgressBubbleText('progress', {message: 'Writing Pancakes…'}, 0),
  askProgressBubbleText('progress', {}, 0),
  askProgressBubbleText('day', {}, 2),
  askProgressBubbleText('mystery', {message: 'x'}, 0),
]));
"""
    res = nodeharness.run_node(script, timeout=30)
    assert res.returncode == 0, res.stderr
    assert json.loads(res.stdout.strip()) == [
        "Thinking…", "Writing Pancakes…", None, "Building your week — 2 things planned so far…", None,
    ]


def test_the_send_path_uses_that_renderer():
    assert "askProgressBubbleText(eventName, body, plannedCount)" in SHELL_JS


# --------------------------------------------------------------------------
# The warm-up
# --------------------------------------------------------------------------


@pytest.fixture(autouse=False)
def fresh_warm_clock():
    agent._CHAT_CACHE_WARM_AT.clear()
    yield
    agent._CHAT_CACHE_WARM_AT.clear()


def test_the_warmup_is_a_max_tokens_zero_call_with_the_chats_cached_prefix(monkeypatch, fresh_warm_clock):
    sent = []
    monkeypatch.setattr(
        agent, "_client",
        lambda: types.SimpleNamespace(messages=types.SimpleNamespace(create=lambda **kw: sent.append(kw) or _final())),
    )
    assert agent.warm_chat_cache() is True
    (kw,) = sent
    assert kw["max_tokens"] == 0
    assert "tool_choice" not in kw  # refused alongside max_tokens 0, as in the recipe warm-up
    assert kw["system"] == [{"type": "text", "text": agent.SYSTEM_PROMPT, "cache_control": {"type": "ephemeral"}}]
    assert kw["tools"] == agent.tools_for_request()
    assert kw["output_config"] == agent._effort_config("chat")


def test_a_refused_warmup_returns_false_and_releases_the_claim(monkeypatch, fresh_warm_clock):
    def refuse(**kw):
        raise RuntimeError("400 max_tokens")

    monkeypatch.setattr(agent, "_client", lambda: types.SimpleNamespace(messages=types.SimpleNamespace(create=refuse)))
    assert agent.claim_chat_warmup() is True
    assert agent.warm_chat_cache() is False
    assert agent.claim_chat_warmup() is True  # not stuck behind a warm-up that never happened


def test_throttle_skips_a_second_warmup_within_the_window(monkeypatch, fresh_warm_clock):
    assert agent.claim_chat_warmup() is True
    assert agent.claim_chat_warmup() is False
    # Past the window, due again.
    key = next(iter(agent._CHAT_CACHE_WARM_AT))
    agent._CHAT_CACHE_WARM_AT[key] -= agent._CHAT_WARM_FRESH_SECONDS + 1
    assert agent.claim_chat_warmup() is True


def test_a_real_round_counts_as_a_warmup(monkeypatch, fresh_warm_clock):
    _script_model(monkeypatch, [_final("Hi.")])
    agent.run_agent_turn([], "hello")
    assert agent.claim_chat_warmup() is False


def test_the_throttle_is_per_household(monkeypatch, fresh_warm_clock):
    agent._CHAT_CACHE_WARM_AT[99999] = time.monotonic()
    assert agent.claim_chat_warmup() is True  # the household under test is not 99999


def test_the_endpoint_warms_once_then_reports_not_warming(signed_in, monkeypatch, fresh_warm_clock):
    calls = []
    monkeypatch.setattr(agent, "warm_chat_cache", lambda: calls.append(1) or True)
    first = signed_in.post("/api/chat/warm")
    second = signed_in.post("/api/chat/warm")
    assert first.status_code == 200 and first.json() == {"warming": True}
    assert second.json() == {"warming": False}
    deadline = time.time() + 3
    while not calls and time.time() < deadline:
        time.sleep(0.01)
    assert calls == [1]


def test_the_endpoint_needs_a_session(client, fresh_warm_clock):
    assert client.post("/api/chat/warm").status_code in (401, 403, 302, 303)


def test_opening_the_sheet_asks_for_the_warmup_once_per_open():
    a = SHELL_JS.index("  function openAskSheet(")
    body = SHELL_JS[a:SHELL_JS.index("  // Clears the sheet back to nothing-said-yet", a)]
    assert "if (!askAlreadyOpen) warmAskCache();" in body
    assert "'/api/chat/warm'" in SHELL_JS
