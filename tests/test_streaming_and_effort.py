"""
Two of the "Make chat responses faster" levers: streaming week generation
(so the browser sees something within ~2s instead of ~37s of silence) and
per-route response effort (chat loop: medium, week/component generation:
high, utility calls: medium, all env-overridable).

This sandbox has no working ANTHROPIC_API_KEY (confirmed: two attempts
both returned 401), so everything here mocks the Anthropic client itself
-- exactly the pattern tests/test_api_call_recording.py already uses for
the non-streaming call sites. What's under test is the plumbing: does a
progress callback actually fire per streamed item, does the prompt really
put the cacheable block first, does the right effort level reach the
payload for the right route. Real timings/tuning need production traffic,
which is explicitly out of scope for this pass.
"""
from __future__ import annotations

import threading
import time
import types

import pytest

from app import agent, tools


class _Usage:
    def __init__(self, input_tokens=0, cache_read=0, cache_write=0, output_tokens=0):
        self.input_tokens = input_tokens
        self.cache_read_input_tokens = cache_read
        self.cache_creation_input_tokens = cache_write
        self.output_tokens = output_tokens


def _tool_block(tool_input):
    return types.SimpleNamespace(type="tool_use", input=tool_input)


def _delta_event(partial_json):
    return types.SimpleNamespace(
        type="content_block_delta",
        delta=types.SimpleNamespace(type="input_json_delta", partial_json=partial_json),
    )


class _FakeStreamManager:
    """
    Stands in for anthropic's `MessageStreamManager`: a context manager
    that iterates scripted events and then hands back a final Message,
    the same shape `_stream_forced_tool_call` expects from the real SDK.
    """

    def __init__(self, events, final_message):
        self._events = events
        self._final_message = final_message

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False

    def __iter__(self):
        return iter(self._events)

    def get_final_message(self):
        return self._final_message


class _FakeStreamingMessages:
    """Records every call's kwargs and hands back scripted stream managers in order."""

    def __init__(self, scripted):
        self._scripted = list(scripted)
        self.calls = []

    def stream(self, **kwargs):
        self.calls.append(kwargs)
        item = self._scripted.pop(0)
        if isinstance(item, Exception):
            raise item
        events, final_message = item
        return _FakeStreamManager(events, final_message)


def _stub_streaming_client(monkeypatch, scripted):
    fake_messages = _FakeStreamingMessages(scripted)
    fake_client = types.SimpleNamespace(messages=fake_messages)
    monkeypatch.setattr(agent, "_client", lambda: fake_client)
    return fake_messages


def _final(tool_input, output_tokens=50):
    return (
        [],
        types.SimpleNamespace(
            content=[_tool_block(tool_input)],
            stop_reason="tool_use",
            usage=_Usage(input_tokens=100, output_tokens=output_tokens),
        ),
    )


# ---------- _ArrayItemScanner ----------


def test_scanner_yields_each_array_item_as_it_completes():
    scanner = agent._ArrayItemScanner("days")
    chunks = [
        '{"days": [',
        '{"date": "2026-09-07", "meal_name": "Chili"}',
        ', {"date": "2026-09-08", ',
        '"meal_name": "Tacos"}',
        ']}',
    ]
    seen = []
    for chunk in chunks:
        seen.extend(scanner.feed(chunk))
    assert seen == [
        {"date": "2026-09-07", "meal_name": "Chili"},
        {"date": "2026-09-08", "meal_name": "Tacos"},
    ]


def test_scanner_ignores_braces_inside_strings():
    """
    A recipe's own text can contain literal braces (a rare but real
    ingredient note like "a {pinch} of salt") -- these must not be
    mistaken for JSON structure and split an item early.
    """
    scanner = agent._ArrayItemScanner("days")
    text = '{"days": [{"meal_name": "a {pinch} of salt", "note": "a\\"quote\\" too"}]}'
    seen = []
    for ch in text:  # feed one character at a time -- the hardest case
        seen.extend(scanner.feed(ch))
    assert seen == [{"meal_name": "a {pinch} of salt", "note": 'a"quote" too'}]


def test_scanner_yields_nothing_before_the_array_key_arrives():
    scanner = agent._ArrayItemScanner("days")
    assert scanner.feed('{"week_start_date": "2026-09-07", ') == []


def test_scanner_never_raises_on_garbage():
    scanner = agent._ArrayItemScanner("days")
    # Malformed input must not blow up the generation it's only meant to
    # be a progress indicator for.
    assert scanner.feed('{"days": [{{{not json') == []


# ---------- _effort_config ----------


def test_effort_defaults_match_the_approved_levels():
    assert agent._effort_config("chat") == {"effort": "medium"}
    assert agent._effort_config("generation") == {"effort": "high"}
    assert agent._effort_config("utility") == {"effort": "medium"}


def test_effort_env_override(monkeypatch):
    monkeypatch.setenv("CHAT_EFFORT", "low")
    assert agent._effort_config("chat") == {"effort": "low"}


def test_effort_invalid_override_falls_back_to_default(monkeypatch, caplog):
    monkeypatch.setenv("GENERATION_EFFORT", "extremely-high-please")
    with caplog.at_level("WARNING"):
        assert agent._effort_config("generation") == {"effort": "high"}
    assert "Ignoring invalid" in caplog.text


def test_every_call_site_sets_an_effort_route():
    """
    Structural check mirroring test_usage.py's model=MODEL guard: every
    one of the app's LLM call sites must pass an explicit output_config,
    not silently fall back to the API's own default (which is "high" for
    every call, exactly the thing measurement showed was wrong for
    everyday chat).
    """
    import inspect

    source = inspect.getsource(agent)
    labels = [
        "generate_weekly_plan_llm", "generate_component_plan_llm",
        "generate_prep_schedule_llm", "generate_recipe_detail_llm",
        "_scan_image_for_items", "generate_chore_recommendations", "run_agent_turn",
    ]
    for label in labels:
        idx = source.index(f'label="{label}"')
        window = source[idx: idx + 1500]
        assert "_effort_config(" in window or "effort_route=" in window, (
            f"{label} does not appear to set an output_config effort route"
        )


# ---------- generate_weekly_plan_llm: streaming + caching ----------


def test_weekly_plan_prompt_puts_the_cacheable_block_first(monkeypatch):
    """
    The whole point of restructuring this prompt was to make the static
    instructions (identical on every call) the cacheable prefix, with the
    household-specific JSON appended after -- the reverse of how it used
    to be laid out. Assert both the marker and the ordering, not just that
    a cache_control appears somewhere.
    """
    fake_messages = _stub_streaming_client(
        monkeypatch, [_final({"days": []})],
    )

    agent.generate_weekly_plan_llm({"week_start_date": "2026-09-07", "household_memory": {}})

    kwargs = fake_messages.calls[0]
    content = kwargs["messages"][0]["content"]
    assert len(content) == 2
    assert content[0]["cache_control"] == {"type": "ephemeral"}
    assert "Household context" not in content[0]["text"], (
        "the household-specific block must not be part of the cached prefix"
    )
    assert "Household context" in content[1]["text"]
    assert "cache_control" not in content[1]
    assert kwargs["output_config"] == {"effort": "high"}


def test_weekly_plan_generation_streams_days_as_they_complete(monkeypatch):
    """
    The actual UI-facing promise: a caller that registers a progress
    callback hears about each day as soon as the model finishes it, not
    only once the whole ~37-second call is done.
    """
    day1 = {"date": "2026-09-07", "slot": "dinner", "meal_name": "Chili"}
    day2 = {"date": "2026-09-08", "slot": "dinner", "meal_name": "Tacos"}
    import json as _json
    full_json = _json.dumps({"days": [day1, day2]})
    # Split into several partial_json deltas, the way the real SDK would.
    midpoint = full_json.index('"date": "2026-09-08"') - 5
    events = [_delta_event(full_json[:midpoint]), _delta_event(full_json[midpoint:])]
    fake_messages = _stub_streaming_client(
        monkeypatch, [(events, types.SimpleNamespace(
            content=[_tool_block({"days": [day1, day2]})],
            stop_reason="tool_use",
            usage=_Usage(input_tokens=100, output_tokens=50),
        ))],
    )

    seen = []
    token = agent._WEEK_GEN_PROGRESS.set(seen.append)
    try:
        result = agent.generate_weekly_plan_llm({"week_start_date": "2026-09-07", "household_memory": {}})
    finally:
        agent._WEEK_GEN_PROGRESS.reset(token)

    assert result == [day1, day2]
    assert seen == [day1, day2], "on_item must fire once per day, in order, before the call returns"


def test_weekly_plan_generation_ignores_a_broken_progress_callback(monkeypatch):
    """A frontend disconnecting mid-stream must not break the generation itself."""
    day1 = {"date": "2026-09-07", "slot": "dinner", "meal_name": "Chili"}
    import json as _json
    events = [_delta_event(_json.dumps({"days": [day1]}))]
    _stub_streaming_client(
        monkeypatch, [(events, types.SimpleNamespace(
            content=[_tool_block({"days": [day1]})],
            stop_reason="tool_use",
            usage=_Usage(input_tokens=10, output_tokens=10),
        ))],
    )

    def _boom(_item):
        raise RuntimeError("frontend went away")

    token = agent._WEEK_GEN_PROGRESS.set(_boom)
    try:
        result = agent.generate_weekly_plan_llm({"week_start_date": "2026-09-07", "household_memory": {}})
    finally:
        agent._WEEK_GEN_PROGRESS.reset(token)
    assert result == [day1]


def test_weekly_plan_generation_retries_transient_failures(monkeypatch):
    from anthropic import APIConnectionError

    day1 = {"date": "2026-09-07", "slot": "dinner", "meal_name": "Chili"}
    fake_messages = _stub_streaming_client(monkeypatch, [
        APIConnectionError(request=None),
        _final({"days": [day1]}),
    ])
    monkeypatch.setattr(time, "sleep", lambda *_: None)

    result = agent.generate_weekly_plan_llm({"week_start_date": "2026-09-07", "household_memory": {}})
    assert result == [day1]
    assert len(fake_messages.calls) == 2


def test_component_plan_prompt_also_puts_the_cacheable_block_first(monkeypatch):
    fake_messages = _stub_streaming_client(monkeypatch, [_final({"items": []})])
    agent.generate_component_plan_llm({"week_start_date": "2026-09-07", "household_memory": {}})
    content = fake_messages.calls[0]["messages"][0]["content"]
    assert content[0]["cache_control"] == {"type": "ephemeral"}
    assert "Household context" not in content[0]["text"]
    assert "Household context" in content[1]["text"]


# ---------- run_agent_turn: effort reaches the chat route ----------


def test_chat_loop_passes_the_chat_effort_route(monkeypatch):
    fake = types.SimpleNamespace(messages=types.SimpleNamespace(
        create=lambda **kwargs: _record_and_finish(kwargs),
    ))
    captured = {}

    def _record_and_finish(kwargs):
        captured.update(kwargs)
        return types.SimpleNamespace(
            content=[types.SimpleNamespace(type="text", text="hi there")],
            stop_reason="end_turn",
            usage=_Usage(input_tokens=10, output_tokens=5),
        )

    monkeypatch.setattr(agent, "_client", lambda: fake)
    agent.run_agent_turn([], "hello")
    assert captured.get("output_config") == {"effort": "medium"}
