"""
Chat refuses "make me a teen" too (Emily, 2026-10-26: went with the pick).

Settings has refused a signed-in adult moving their OWN age group off Adult
since 2026-10-20 (tests/test_own_age_stays_adult.py). Chat didn't: with two
adults, "make me a teen" from Emily's phone went through and her phone then
acted as nobody. Now both doors read tools.own_age_leaving_adult and say the
same sentence. Driven through the real agent loop with a stubbed API client.
"""
from __future__ import annotations

import json

from app import tools
from tests.test_chat_keeps_one_adult import _age, _chat_calls_set_age_group, _error_rows, _household

SENTENCE = "Your own age stays Adult here. Another adult can change it."


def _id(name):
    return next(a["id"] for a in tools.household_adults() if a["name"] == name)


def test_chat_refuses_your_own_age_off_adult(monkeypatch):
    """CATCH. Red before: the tool wrote Teen for the adult on this phone."""
    _household("Emily", "Vineeth")
    with tools.use_member(_id("Emily")):
        for variant, word in (("Emily", "teen"), ("emily ", "child"), ("Émily", "Teen")):
            handed = _chat_calls_set_age_group(monkeypatch, variant, word)
            assert handed["is_error"] is True, variant
            assert json.loads(handed["content"]) == {"error": SENTENCE}
    assert _age("Emily") == "adult"
    assert _error_rows() == []


def test_chat_still_moves_the_other_adult(monkeypatch):
    _household("Emily", "Vineeth")
    with tools.use_member(_id("Emily")):
        handed = _chat_calls_set_age_group(monkeypatch, "Vineeth", "teen")
        assert handed["is_error"] is False
        # Saying Adult for yourself is fine.
        assert _chat_calls_set_age_group(monkeypatch, "Emily", "adult")["is_error"] is False
    assert _age("Vineeth") == "teen"


def test_no_pick_keeps_todays_behaviour(monkeypatch):
    """Same as Settings: a device nobody picked on isn't refused by this rule."""
    _household("Emily", "Vineeth")
    handed = _chat_calls_set_age_group(monkeypatch, "Emily", "teen")
    assert handed["is_error"] is False
    assert _age("Emily") == "teen"


def test_both_doors_say_the_same_sentence(signed_in, monkeypatch):
    _household("Emily", "Vineeth")
    emily = _id("Emily")
    assert signed_in.post("/api/whoami/pick", json={"member_id": emily}).status_code == 200
    res = signed_in.post("/api/memory/member/age-group", json={"name": "Emily", "age_group": "teen"})
    assert res.status_code == 400 and res.json()["detail"] == SENTENCE
    with tools.use_member(emily):
        handed = _chat_calls_set_age_group(monkeypatch, "Emily", "teen")
    assert json.loads(handed["content"])["error"] == SENTENCE
    assert _age("Emily") == "adult"
