"""
A chat tool's arguments are read as its own schema declares them
(agent.read_tool_input), before the tool runs.

The schema asks the model for a type; it doesn't guarantee one. A null
optional, "7" for 7 and "false" for false crashed tools or flipped a
yes to a no, and a bare string where the schema says array was stored one
LETTER per item: "nut allergy" saved as eleven one-character restrictions
and the allergy itself lost. These tests go through the real
run_agent_turn with the model client stubbed, so the dispatch itself is
what is under test.
"""
import types

from app import agent, tools


class _Usage:
    input_tokens = 1
    cache_read_input_tokens = 0
    cache_creation_input_tokens = 0
    output_tokens = 1


def _tool_turn(monkeypatch, signed_in, name, tool_input):
    """Run one chat turn whose model asks for one tool; return its tool_result."""
    sent = []

    class _Messages:
        def __init__(self):
            self.responses = [
                types.SimpleNamespace(
                    content=[types.SimpleNamespace(type="tool_use", name=name, input=tool_input, id="tu_1")],
                    stop_reason="tool_use",
                    usage=_Usage(),
                ),
                types.SimpleNamespace(
                    content=[types.SimpleNamespace(type="text", text="Done.")],
                    stop_reason="end_turn",
                    usage=_Usage(),
                ),
            ]

        def create(self, **kwargs):
            sent.append(kwargs)
            return self.responses.pop(0)

    fake = types.SimpleNamespace(messages=_Messages())
    monkeypatch.setattr(agent, "_client", lambda: fake)
    res = signed_in.post("/api/chat", json={"session_id": "default", "message": "go"})
    assert res.status_code == 200
    # The second request carries the tool_result for the first round.
    for msg in reversed(sent[-1]["messages"]):
        if msg["role"] == "user" and isinstance(msg["content"], list):
            for part in msg["content"]:
                if isinstance(part, dict) and part.get("type") == "tool_result":
                    return part
    raise AssertionError("no tool_result was sent back")


def _restrictions(name):
    return next(m for m in tools.list_members() if m["name"] == name)["dietary_restrictions"]


def test_an_allergy_sent_as_a_bare_string_is_saved_whole(signed_in, monkeypatch):
    tools.add_member("Theo")
    result = _tool_turn(
        monkeypatch, signed_in, "set_member_dietary_restrictions",
        {"name": "Theo", "restrictions": "nut allergy"},
    )
    assert not result.get("is_error"), result
    assert _restrictions("Theo") == ["nut allergy"]


def test_a_list_sent_as_json_text_is_read_as_the_list(signed_in, monkeypatch):
    tools.add_member("Theo")
    _tool_turn(
        monkeypatch, signed_in, "set_member_dietary_restrictions",
        {"name": "Theo", "restrictions": '["vegetarian", "no shellfish"]'},
    )
    assert _restrictions("Theo") == ["vegetarian", "no shellfish"]


def test_whats_planned_answers_with_a_null_or_quoted_day_count(signed_in, monkeypatch):
    for days in (None, "7"):
        result = _tool_turn(monkeypatch, signed_in, "get_meal_plan", {"days_ahead": days})
        assert not result.get("is_error"), (days, result)


def test_false_as_text_marks_the_person_away_not_home():
    read = agent.read_tool_input(
        "set_member_attendance",
        {"date_str": "2026-10-22", "slot": "dinner", "member": "Theo", "present": "false"},
    )
    assert read["present"] is False


def test_only_what_the_schema_declares_is_changed():
    original = {"recipe_name": "Chili", "target_servings": "8", "mystery": None}
    read = agent.read_tool_input("scale_recipe", original)
    assert read == {"recipe_name": "Chili", "target_servings": 8, "mystery": None}
    # The model's own block stays as it sent it — it is replayed in history.
    assert original["target_servings"] == "8"
    # Not a whole number: left for the tool's own error.
    assert agent.read_tool_input("scale_recipe", {"target_servings": "eight"})["target_servings"] == "eight"
    # A REQUIRED field sent as null is the tool's to refuse, not dropped.
    assert agent.read_tool_input("scale_recipe", {"recipe_name": None}) == {"recipe_name": None}
    # An optional null is left out so the function's own default applies.
    assert agent.read_tool_input("set_lunch_location", {"member_name": "Theo", "location": "out", "weekday": None}) == {
        "member_name": "Theo", "location": "out"}
