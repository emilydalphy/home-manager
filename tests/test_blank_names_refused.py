"""
A blank name makes nobody (defect hunt, 2026-10-20).

Chat's add_member("") and add_pet("", ...) saved empty rows, and a
restriction or age said about "" created a nameless person on the way —
an empty line in Who's here, counted at every meal. The HTTP routes
already refused a blank name; the tools did not, so the chat could.
Refused at the tool, with a sentence the chat can relay.
"""
import pytest

from app import agent, tools


def _people():
    return [m["name"] for m in tools.list_members()]


@pytest.mark.parametrize("name", ["", "   ", None])
def test_add_member_refuses_a_blank_name(name):
    with pytest.raises(ValueError, match="their name"):
        tools.add_member(name)
    assert _people() == []


def test_the_chat_tool_is_the_refusing_one():
    """The chat dispatches through TOOL_FUNCTIONS — the refusal is there too."""
    with pytest.raises(ValueError):
        agent.TOOL_FUNCTIONS["add_member"](name=" ")
    assert _people() == []


@pytest.mark.parametrize("write", [
    lambda: tools.set_member_dietary_restrictions("", ["peanut allergy"]),
    lambda: tools.set_member_age_group("  ", "adult"),
    lambda: tools.set_member_age("", 30),
])
def test_saying_something_about_nobody_creates_nobody(write):
    with pytest.raises(ValueError):
        write()
    assert _people() == []


@pytest.mark.parametrize("name", ["", "  "])
def test_add_pet_refuses_a_blank_name(name):
    with pytest.raises(ValueError, match="pet's name"):
        tools.add_pet(name, "dog")
    assert tools.list_pets() == []


def test_a_real_name_still_saves():
    tools.add_member("Theo")
    tools.add_pet("Biscuit", "dog")
    assert _people() == ["Theo"]
    assert [p["name"] for p in tools.list_pets()] == ["Biscuit"]


def test_blank_fact_says_why_nothing_was_added():
    result = tools.add_fact("family", "   ")
    assert result["added"] is False and result["reason"]


def test_restrictions_reply_names_the_member_as_stored():
    tools.add_member("Theo")
    result = tools.set_member_dietary_restrictions("theo", ["sesame"])
    assert result["name"] == "Theo"
    assert result["dietary_restrictions"] == ["sesame"]
    assert _people() == ["Theo"]
