"""
Chat keeps one adult too (2026-10-24, overnight test-gap pass).

A rule enforced on one door and assumed on the other. Settings' age chip
(POST /api/memory/member/age-group) has refused taking the household's only
adult off Adult since 2026-10-20 (tests/test_age_group_route_guards.py),
because with no adult "Who's this?", sign-in's member pick, approval credit
and the main person have nobody to name. Chat's set_member_age_group went
straight to the bare tool: "Emily's a teen actually" in a one-adult home
answered {"name": "Emily", "age_group": "teen"} and household_adults() was
[] afterwards.

Now both doors read one rule (tools.last_adult_leaving). The chat side
raises tools.MemberRefused, a marker in agent.REFUSALS_OWED_TO_A_PERSON, so
the sentence reaches the model as an error (no write counted, no card) and
the morning report does not read a working guard as a breakage.

Driven through the real agent loop with a stubbed API client.
"""
from __future__ import annotations

import json
import types

from app import agent, tools
from app.db import get_conn
from app.tools import _shared

ONLY_ADULT = "Emily is the only adult here. Add another adult before changing this."


class _Usage:
    input_tokens = cache_read_input_tokens = cache_creation_input_tokens = output_tokens = 0


class _FakeMessages:
    def __init__(self, responses):
        self._responses = list(responses)
        self.requests = []

    def create(self, **kwargs):
        self.requests.append(kwargs)
        return self._responses.pop(0)


def _chat_calls_set_age_group(monkeypatch, name, age_group):
    """One turn: the model calls set_member_age_group, then speaks. Returns
    the tool_result the loop handed back to the model."""
    fake = _FakeMessages([
        types.SimpleNamespace(
            content=[types.SimpleNamespace(type="tool_use", name="set_member_age_group",
                                           input={"name": name, "age_group": age_group}, id="tu_1")],
            stop_reason="tool_use", usage=_Usage()),
        types.SimpleNamespace(content=[types.SimpleNamespace(type="text", text="Okay.")],
                              stop_reason="end_turn", usage=_Usage()),
    ])
    monkeypatch.setattr(agent, "_client", lambda: types.SimpleNamespace(messages=fake))
    agent.run_agent_turn([], f"{name} is a {age_group}")
    results = [m for m in fake.requests[-1]["messages"]
               if m["role"] == "user" and isinstance(m["content"], list)]
    return results[-1]["content"][0]


def _household(*adults, kid="Sam"):
    for name in adults:
        tools.add_member(name)
        tools.set_member_age_group(name, "adult")
    tools.add_member(kid)
    tools.set_member_age_group(kid, "child")


def _age(name):
    conn = get_conn()
    row = conn.execute("SELECT age_group FROM members WHERE name = ?", (name,)).fetchone()
    conn.close()
    return row["age_group"]


def _error_rows():
    conn = get_conn()
    rows = conn.execute("SELECT kind, where_ FROM error_events").fetchall()
    conn.close()
    return [tuple(r) for r in rows]


def test_chat_cannot_take_the_only_adult_off_adult(monkeypatch):
    """CATCH. Red before: the tool answered the write and no adult was left."""
    _household("Emily")
    handed = _chat_calls_set_age_group(monkeypatch, "Emily", "teen")
    assert handed["is_error"] is True
    assert json.loads(handed["content"]) == {"error": ONLY_ADULT}
    assert _age("Emily") == "adult"
    assert [a["name"] for a in _shared.household_adults()] == ["Emily"]
    # A guard doing its job is not a breakage in the morning report.
    assert _error_rows() == []


def test_the_refusal_reads_any_word_that_is_not_adult(monkeypatch):
    """Chat stays freeform about the word ("toddler" folds into child, a
    made-up word is stored as said) — but any word that isn't Adult still
    takes the last adult away, so all of them are refused. Case and spaces
    in the name are the lookup's own (LOWER), as the write's."""
    _household("Emily")
    for word in ("toddler", "grown-up", " Child "):
        handed = _chat_calls_set_age_group(monkeypatch, "emily", word)
        assert handed["is_error"] is True, word
    assert _age("Emily") == "adult"


def test_with_two_adults_chat_still_moves_one(monkeypatch):
    """The ordinary case is untouched: one of two adults may become a teen,
    and re-marking the last adult as an adult is fine."""
    _household("Emily", "Vineeth")
    handed = _chat_calls_set_age_group(monkeypatch, "Vineeth", "teen")
    assert handed["is_error"] is False
    assert _age("Vineeth") == "teen"
    handed = _chat_calls_set_age_group(monkeypatch, "Emily", "Adult")
    assert handed["is_error"] is False
    # And anyone who isn't the last adult is anything chat says.
    handed = _chat_calls_set_age_group(monkeypatch, "Sam", "teen")
    assert handed["is_error"] is False and _age("Sam") == "teen"


def test_both_doors_say_the_same_sentence(signed_in, monkeypatch):
    """One rule, one sentence: Settings' 400 and chat's refusal match."""
    _household("Emily")
    res = signed_in.post("/api/memory/member/age-group", json={"name": "Emily", "age_group": "teen"})
    assert res.status_code == 400 and res.json()["detail"] == ONLY_ADULT
    handed = _chat_calls_set_age_group(monkeypatch, "Emily", "teen")
    assert json.loads(handed["content"])["error"] == ONLY_ADULT


def test_a_name_variant_is_still_the_only_adult_on_both_doors(signed_in, monkeypatch):
    """CATCH (merge review, 2026-10-24). The write finds its person with
    find_member_row (case-, space- and accent-blind); a plain-LOWER check
    missed "Emily " and "Émily", so both doors demoted the only adult."""
    _household("Emily")
    for variant in ("Emily ", "Émily", "  EMILY"):
        handed = _chat_calls_set_age_group(monkeypatch, variant, "teen")
        assert handed["is_error"] is True, variant
        assert json.loads(handed["content"]) == {"error": ONLY_ADULT}, variant
        res = signed_in.post("/api/memory/member/age-group", json={"name": variant, "age_group": "teen"})
        assert res.status_code == 400, (variant, res.text)
        assert res.json()["detail"] == ONLY_ADULT
    assert _age("Emily") == "adult"
    assert [a["name"] for a in _shared.household_adults()] == ["Emily"]
    assert _error_rows() == []


def test_a_name_that_could_be_two_adults_is_asked_back_not_written(signed_in, monkeypatch):
    """Two adults told apart only by an accent, and a third spelling that
    folds onto both: nobody in particular, so refused — a MemberRefused in
    chat, a 400 on the route — and neither adult changes."""
    conn = get_conn()
    conn.execute("INSERT INTO members (household_id, name, age_group) VALUES (1, 'Rene', 'adult'), (1, 'René', 'adult')")
    conn.commit()
    conn.close()
    handed = _chat_calls_set_age_group(monkeypatch, "Rêne", "teen")
    assert handed["is_error"] is True
    assert "Rene or René" in json.loads(handed["content"])["error"]
    res = signed_in.post("/api/memory/member/age-group", json={"name": "Rêne", "age_group": "teen"})
    assert res.status_code == 400 and "Rene or René" in res.json()["detail"]
    assert (_age("Rene"), _age("René")) == ("adult", "adult")
    assert _error_rows() == []
