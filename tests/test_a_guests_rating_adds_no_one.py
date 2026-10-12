"""
A rating from someone who isn't in the household adds no one to it.

`attribute_recipe_feedback` (chat: "Grandma loved the skewers") used to
get-or-create the member by name, so a visiting grandma, a nickname or a
typo became a new PERSON in the household — counted in every dinner's
headcount and portions from then on — for want of one taste row. It looks
the person up now and refuses a stranger, naming who is here.
"""
from __future__ import annotations

import types

import pytest

from app import agent, tools
from conftest import household_date


@pytest.fixture
def couple():
    tools.add_member("Emily")
    tools.add_member("Vineeth")
    tools.add_recipe(
        "Skewers",
        ingredients=[{"item": "Chicken thighs", "qty": "1 lb", "category": "meat"}],
        default_servings=4,
    )


def _names() -> list[str]:
    return [m["name"] for m in tools.list_members()]


def test_a_guest_is_refused_and_the_headcount_stays_two(couple):
    today = household_date(0)
    assert tools.headcount_for_slot(today, "dinner") == 2
    with pytest.raises(ValueError) as caught:
        tools.attribute_recipe_feedback("Skewers", "Grandma", rating="liked")
    # The refusal names who IS here, so the model can retry with a real name.
    assert "Emily" in str(caught.value) and "Vineeth" in str(caught.value)
    assert _names() == ["Emily", "Vineeth"]
    assert tools.headcount_for_slot(today, "dinner") == 2
    assert tools.get_member_taste("Emily")["has_any_data"] is False


def test_someone_here_is_still_found_whatever_the_case(couple):
    result = tools.attribute_recipe_feedback("Skewers", "  vineeth ", rating="liked")
    assert result["rating"] == "liked"
    assert tools.get_member_taste("Vineeth")["liked_recipes"] == ["Skewers"]
    assert _names() == ["Emily", "Vineeth"]


def test_through_chat_the_model_is_told_and_no_one_is_added(couple, monkeypatch):
    """End to end with only the model stubbed: the real dispatch loop runs the
    tool, and the model gets the refusal as an ordinary tool error."""
    usage = types.SimpleNamespace(
        input_tokens=0, cache_read_input_tokens=0,
        cache_creation_input_tokens=0, output_tokens=0,
    )
    seen = []
    seq = [
        types.SimpleNamespace(
            content=[types.SimpleNamespace(
                type="tool_use", name="attribute_recipe_feedback",
                input={"recipe_name": "Skewers", "member_name": "Grandma", "rating": "liked"},
                id="tu_1")],
            stop_reason="tool_use", usage=usage,
        ),
        types.SimpleNamespace(
            content=[types.SimpleNamespace(type="text", text="Noted.")],
            stop_reason="end_turn", usage=usage,
        ),
    ]

    def create(**kwargs):
        seen.append(kwargs.get("messages"))
        return seq.pop(0)

    monkeypatch.setattr(agent, "_client", lambda: types.SimpleNamespace(
        messages=types.SimpleNamespace(create=create)))
    agent.run_agent_turn([], "Grandma loved the skewers")

    results = [
        block for msg in seen[-1] if isinstance(msg.get("content"), list)
        for block in msg["content"] if isinstance(block, dict) and block.get("type") == "tool_result"
    ]
    assert results and results[-1]["is_error"] is True
    assert "isn't someone in this household" in results[-1]["content"]
    assert _names() == ["Emily", "Vineeth"]
