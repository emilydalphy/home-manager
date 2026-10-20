"""
Every chat tool's schema has to be a call its function can take
(overnight 2026-10-20, branch `holiday-chat-tools-take-their-date`).

The chat loop runs `fn(**block.input)`: the model fills in exactly the
property names the schema advertises, and nothing renames them on the way.
The six holiday / big-meal tools advertised `date` while their functions
take `date_str`, so every "we're hosting Thanksgiving", "what's the plan
for the big meal", "swap the dessert" died as a TypeError before the tool
ran — the household was told something went wrong and nothing was
recorded. The direct-call tests never saw it because they call
`tools.answer_holiday(tg, ...)` positionally.
"""
import datetime
import inspect

from app import agent, tools


def _accepts(fn):
    params = inspect.signature(fn).parameters
    takes_anything = any(p.kind == p.VAR_KEYWORD for p in params.values())
    return params, takes_anything


def test_every_property_a_schema_offers_is_a_parameter_its_function_takes():
    wrong = []
    for tool in agent.TOOL_DEFINITIONS:
        fn = agent.TOOL_FUNCTIONS.get(tool["name"])
        assert fn, f"{tool['name']} has a schema and no function"
        params, takes_anything = _accepts(fn)
        if takes_anything:
            continue
        for prop in tool["input_schema"].get("properties", {}):
            if prop not in params:
                wrong.append(f"{tool['name']}.{prop}")
    assert not wrong, f"schema offers what the function can't take: {wrong}"


def test_every_parameter_a_function_needs_is_required_by_its_schema():
    by_name = {t["name"]: t for t in agent.TOOL_DEFINITIONS}
    missing = []
    for name, fn in agent.TOOL_FUNCTIONS.items():
        params, _ = _accepts(fn)
        required = set(by_name[name]["input_schema"].get("required", []))
        for pname, p in params.items():
            if p.kind in (p.VAR_KEYWORD, p.VAR_POSITIONAL):
                continue
            if p.default is p.empty and pname not in required:
                missing.append(f"{name}.{pname}")
    assert not missing, f"function needs what the schema never asks for: {missing}"


def test_a_holiday_answered_in_chat_is_recorded():
    """The call exactly as the model sends it, through the chat's own table."""
    tg = next(
        h["date"] for h in tools.rule_holidays(datetime.date.today().year + 1)
        if h["name"] == "Thanksgiving"
    )
    tool = next(t for t in agent.TOOL_DEFINITIONS if t["name"] == "answer_holiday")
    assert tool["input_schema"]["required"] == ["date_str", "answer"]
    agent.TOOL_FUNCTIONS["answer_holiday"](**{"date_str": tg, "answer": "just_us"})
    assert tools.holiday_on(tg)["answer"]["answer"] == "just_us"
