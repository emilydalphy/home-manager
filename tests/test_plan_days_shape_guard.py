"""
The forced tool call's `days` must be a list of meal objects; on 2026-09-21 a
live draft came back with something else and `_honest_meal_names` crashed on
`'str' object has no attribute 'get'` — three generations in a row on one
long typed note, each shown to Emily as "I couldn't save that just now".
The guard normalises the shapes a model actually produces and says in the
log what it got.
"""
import json
import logging

from app import agent
from app.tools import model_shapes


DAY = {"date": "2026-09-22", "slot": "dinner", "meal_name": "Chicken thighs", "is_new_recipe": True, "reasoning": "x"}


def test_a_proper_list_passes_through_untouched():
    assert model_shapes.coerce_result_list([DAY], "days", "t") == [DAY]


def test_a_json_string_is_parsed(caplog):
    with caplog.at_level(logging.WARNING):
        out = model_shapes.coerce_result_list(json.dumps([DAY]), "days", "t")
    assert out == [DAY]
    assert "JSON-encoded as a string" in caplog.text


def test_an_object_keyed_by_date_is_flattened(caplog):
    raw = {"2026-09-22": [DAY], "2026-09-23": [dict(DAY, date="2026-09-23")]}
    with caplog.at_level(logging.WARNING):
        out = model_shapes.coerce_result_list(raw, "days", "t")
    assert [d["date"] for d in out] == ["2026-09-22", "2026-09-23"]
    assert "object of 2 lists" in caplog.text


def test_a_lone_object_becomes_a_one_item_list():
    assert model_shapes.coerce_result_list(DAY, "days", "t") == [DAY]


def test_strings_inside_the_list_are_dropped_and_named(caplog):
    with caplog.at_level(logging.WARNING):
        out = model_shapes.coerce_result_list([DAY, "Tuesday: tacos", 7], "days", "t")
    assert out == [DAY]
    assert "dropped 2 non-object element(s)" in caplog.text
    assert "Tuesday: tacos" in caplog.text


def test_garbage_is_empty_not_a_crash(caplog):
    with caplog.at_level(logging.WARNING):
        assert model_shapes.coerce_result_list("not json {", "days", "t") == []
        assert model_shapes.coerce_result_list(42, "days", "t") == []
    assert "isn't JSON" in caplog.text


def test_the_honest_names_pass_never_sees_a_string():
    # The exact crash from the live log: a str where a meal dict should be.
    items = model_shapes.coerce_result_list([DAY, "stray"], "days", "t")
    agent._honest_meal_names(items)  # must not raise


def test_the_page_tells_a_failed_draft_apart_from_a_failed_save():
    html = open("static/plan-week.html", encoding="utf-8").read()
    assert "That draft didn’t come together — nothing’s changed. Tap Draft my week to try again." in html
    assert "wasDrafting" in html


def test_the_evening_shape_from_the_live_log_is_unwrapped(caplog):
    # 2026-09-21 22:25 on Railway: `days` came back as a JSON string whose
    # content was {"days": [...]} — the array wrapped in its own key, twice.
    raw = json.dumps({"days": [DAY]})
    with caplog.at_level(logging.WARNING):
        out = model_shapes.coerce_result_list(raw, "days", "t")
    assert out == [DAY]


def test_swap_picks_that_come_back_as_a_string_still_reach_the_sheet(monkeypatch):
    # Every Swap on the evening of 2026-09-21 said "Nothing I'd put there
    # instead": the model had answered, as a string, and list("...") is a
    # list of characters that the gate silently skipped.
    import importlib
    so = importlib.import_module("app.tools.swap_options")
    picks = [{"meal_name": "Lemon chicken", "reason": "quick", "ingredients": [{"item": "chicken"}]},
             {"meal_name": "Black bean tacos", "reason": "meatless", "ingredients": [{"item": "beans"}]}]

    class Block:
        type = "tool_use"
        input = {"options": json.dumps(picks)}

    class Resp:
        stop_reason = "end_turn"
        content = [Block()]

    out = so._model_shapes.tool_list(Resp.content[0].input, "options", "swap_options")
    assert [p["meal_name"] for p in out] == ["Lemon chicken", "Black bean tacos"]


def test_every_forced_list_reader_goes_through_the_guard():
    import re
    src = open("app/agent.py", encoding="utf-8").read()
    bare = re.findall(r'return block\.input\.get\("(\w+)", \[\]\)', src)
    assert bare == [], f"unguarded list readers: {bare}"
    for path in ("app/tools/swap_options.py", "app/tools/plate_parts.py"):
        s = open(path, encoding="utf-8").read()
        assert 'list((block.input or {}).get("options") or [])' not in s, path
