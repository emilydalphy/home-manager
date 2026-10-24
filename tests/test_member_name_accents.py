"""
A name said in chat finds the person it means, whatever the accents, case
or stray spaces (defect hunt 2026-10-24).

SQLite's LOWER() folds only A-Z, so "zoe is allergic to nuts" about a child
saved as Zoé matched nobody, and the get-or-create behind the chat's member
tools ADDED a second child "Zoe" carrying the allergy — counted at every
meal — while the real Zoé's list stayed empty. Driven through the chat's
own tool table, with the arguments the model sends.
"""
import pytest

from app import agent, tools
from app.db import get_conn
from conftest import household_date


def _chat(_tool, **kw):
    return agent.TOOL_FUNCTIONS[_tool](**agent.read_tool_input(_tool, kw))


def _members():
    conn = get_conn()
    rows = conn.execute("SELECT name, dietary_restrictions_json FROM members ORDER BY id").fetchall()
    conn.close()
    return [(r["name"], r["dietary_restrictions_json"]) for r in rows]


def test_an_allergy_said_without_the_accent_lands_on_the_child_not_a_new_person():
    tools.add_member("Emily")
    tools.add_member("Zoé")
    for typed in ("Zoe", "zoe ", "ZOÉ"):
        result = _chat("set_member_dietary_restrictions", name=typed, restrictions=["nut allergy"])
        assert result["name"] == "Zoé"
    assert _members() == [("Emily", "[]"), ("Zoé", '["nut allergy"]')]


def test_a_lowercase_accented_initial_is_the_same_person():
    # "émile" — LOWER() never touched the É, so this missed even with the accent.
    tools.add_member("Émile")
    _chat("set_member_age_group", name="émile", age_group="child")
    _chat("add_member", name="Emile")
    assert [n for n, _ in _members()] == ["Émile"]


def test_two_people_told_apart_only_by_an_accent_are_never_merged():
    # Exact spelling (case-blind) wins; the folded match is used only when
    # it is unique. Seeded raw: a household can already hold both.
    conn = get_conn()
    conn.execute("INSERT INTO members (household_id, name) VALUES (1, 'Rene'), (1, 'René')")
    conn.commit()
    conn.close()
    _chat("set_member_dietary_restrictions", name="RENÉ", restrictions=["vegetarian"])
    _chat("set_member_dietary_restrictions", name="rene", restrictions=["no pork"])
    assert _members() == [("Rene", '["no pork"]'), ("René", '["vegetarian"]')]
    # A third spelling that folds onto both is nobody in particular: asked
    # back, never guessed onto one of them.
    with pytest.raises(ValueError, match="No household member named"):
        _chat("set_member_attendance", date_str=household_date(1), slot="dinner", member="Rêne", present=False)


def test_attendance_and_lunch_find_the_accented_person():
    tools.add_member("Emily")
    tools.add_member("Zoé")
    day = household_date(1)
    result = _chat("set_member_attendance", date_str=day, slot="dinner", member="zoe", present=False)
    assert result["absent_names"] == ["Zoé"]
    lunch = _chat("set_lunch_location", member_name="Zoe", location="out")
    # Stored under the member's own spelling — the key every reader uses.
    assert lunch["member_name"] == "Zoé"
