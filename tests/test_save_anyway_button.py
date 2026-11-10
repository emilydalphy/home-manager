"""
"Save it anyway" is a button (Emily, 2026-11-09):

  "The allergy refusal in chat gets a 'Save it anyway' button. Only a tap on
  that button arms the override, for that exact dish and that exact list, in
  that conversation. No typed reply arms it, not even 'yes'. The turn
  counter is per conversation, not process-wide."

Her tests, through POST /api/chat with the model stubbed and the tap's own
route: "ok thanks" and "yes" in chat both leave the dish refused; the tap
saves it; a tap in one household's conversation never arms another's.
"""
from __future__ import annotations

import types

import pytest
from fastapi.testclient import TestClient

from app import agent, households, main, security, tools
from app.tools import allergen_gate


class _Usage:
    input_tokens = cache_read_input_tokens = cache_creation_input_tokens = output_tokens = 0


CHEESECAKE = {"name": "Cheesecake Bars", "ingredients": [{"item": "cream cheese", "qty": "1 block"}]}


def _script(monkeypatch, turns):
    """Each turn is (tool_name, tool_input) or None for a text-only reply.
    Returns the list of requests the fake model was sent."""
    responses, sent = [], []
    for i, turn in enumerate(turns):
        if turn:
            responses.append(types.SimpleNamespace(
                content=[types.SimpleNamespace(type="tool_use", name=turn[0], input=turn[1], id=f"tu_{i}")],
                stop_reason="tool_use", usage=_Usage()))
        responses.append(types.SimpleNamespace(
            content=[types.SimpleNamespace(type="text", text="Cheesecake Bars has cream cheese, which Emily can’t have.")],
            stop_reason="end_turn", usage=_Usage()))

    def create(**kw):
        sent.append(kw)
        return responses.pop(0)
    monkeypatch.setattr(agent, "_client", lambda: types.SimpleNamespace(messages=types.SimpleNamespace(create=create)))
    return sent


@pytest.fixture(autouse=True)
def _clean():
    allergen_gate._OFFERS.clear()
    allergen_gate._TAP_NOTES.clear()
    yield
    allergen_gate._OFFERS.clear()
    allergen_gate._TAP_NOTES.clear()


@pytest.fixture
def dairy_free():
    tools.add_member("Emily")
    tools.set_member_dietary_restrictions("Emily", ["dairy free"])


def _chat(client, message):
    res = client.post("/api/chat", json={"session_id": "default", "message": message})
    assert res.status_code == 200, res.text
    return res.json()


def _button(body):
    cards = [a for a in body["actions"] if a.get("save_anyway")]
    assert len(cards) == 1, body["actions"]
    return cards[0]


def test_typed_answers_never_save_it_and_the_tap_does(monkeypatch, signed_in, dairy_free):
    retry = ("add_recipe", {**CHEESECAKE, "override": True})
    sent = _script(monkeypatch, [("add_recipe", CHEESECAKE), retry, retry, None])

    card = _button(_chat(signed_in, "save my cheesecake bars recipe"))
    assert card["save_anyway"]["label"] == "Save it anyway"
    assert card["kicker"] == "Not saved" and card["change"] == "Cheesecake Bars"
    assert tools.existing_recipe_named("Cheesecake Bars") is None

    # The model tries again with override on, as the old path let it.
    _chat(signed_in, "ok thanks")
    assert tools.existing_recipe_named("Cheesecake Bars") is None
    _chat(signed_in, "yes")
    assert tools.existing_recipe_named("Cheesecake Bars") is None

    res = signed_in.post("/api/chat/save-anyway", json={"token": card["save_anyway"]["token"]})
    assert res.status_code == 200, res.text
    assert res.json()["said"] == "Cheesecake Bars was saved."
    assert tools.existing_recipe_named("Cheesecake Bars")

    # Spent: a second tap is gone.
    again = signed_in.post("/api/chat/save-anyway", json={"token": card["save_anyway"]["token"]})
    assert again.status_code == 410

    # The model is told on the next turn, so it never says it wasn't saved.
    _chat(signed_in, "thanks")
    told = " ".join(b["text"] for b in sent[-1]["system"] if isinstance(b, dict))
    assert "Cheesecake Bars was saved." in told


def test_a_tap_in_one_households_conversation_never_arms_anothers(monkeypatch, signed_in, dairy_free):
    _script(monkeypatch, [("add_recipe", CHEESECAKE)])
    token = _button(_chat(signed_in, "save my cheesecake bars recipe"))["save_anyway"]["token"]

    beta = households.create_household("The Beta Testers", "beta-tester-passphrase")
    with TestClient(main.app) as other:
        assert other.post("/login", data={"password": "beta-tester-passphrase", "next": "/"},
                          follow_redirects=False).status_code == 303
        res = other.post("/api/chat/save-anyway", json={"token": token})
        assert res.status_code == 410
    with tools.use_household(beta):
        assert tools.existing_recipe_named("Cheesecake Bars") is None
    assert tools.existing_recipe_named("Cheesecake Bars") is None

    # Household 1, but another sign-in: another conversation, not armed.
    with TestClient(main.app) as same_house:
        same_house.post("/login", data={"password": "test-password", "next": "/"}, follow_redirects=False)
        assert same_house.post("/api/chat/save-anyway", json={"token": token}).status_code == 410
    assert tools.existing_recipe_named("Cheesecake Bars") is None

    # Neither spent it: the conversation that was shown it still can.
    assert signed_in.post("/api/chat/save-anyway", json={"token": token}).status_code == 200
    assert tools.existing_recipe_named("Cheesecake Bars")


def test_planning_says_plan_it_anyway_and_the_tap_plans_exactly_that(monkeypatch, signed_in, dairy_free):
    day = tools.cooker.household_today().isoformat()
    _script(monkeypatch, [("plan_meal", {"meal_date": day, "meal": "Grilled Cheese", "slot": "dinner"})])
    card = _button(_chat(signed_in, "grilled cheese tonight"))
    assert card["save_anyway"]["label"] == "Plan it anyway" and card["kicker"] == "Not planned"
    res = signed_in.post("/api/chat/save-anyway", json={"token": card["save_anyway"]["token"]})
    assert res.status_code == 200, res.text
    assert res.json() == {"status": "planned", "said": "Grilled Cheese was planned.", "tab": "week", "href": None}


def test_the_turn_counter_is_per_conversation():
    a = allergen_gate._CHAT_CONVERSATION.set("h1:a")
    try:
        first = allergen_gate.begin_chat_turn()
        allergen_gate.set_chat_conversation("h1:b")
        assert allergen_gate.begin_chat_turn() == 1
        allergen_gate.set_chat_conversation("h1:a")
        assert allergen_gate.begin_chat_turn() == first + 1
    finally:
        allergen_gate._CHAT_CONVERSATION.reset(a)
        allergen_gate._TURN_COUNTS.pop("h1:a", None)
        allergen_gate._TURN_COUNTS.pop("h1:b", None)


def test_the_cookie_not_the_body_names_the_conversation():
    """The tap's route reads nothing but the token from the body."""
    assert set(main.SaveAnywayRequest.model_fields) == {"token"}
    assert security.COOKIE_NAME  # the conversation key comes from it (_chat_session_id)


# ---------- review round (2026-11-10) ----------

ALFREDO = [{"item": "heavy cream", "qty": "1 cup"}, {"item": "shrimp", "qty": "1 lb"}]


def _shellfish_added_since():
    tools.add_member("Sam")
    tools.set_member_dietary_restrictions("Sam", ["shellfish allergy"])


def test_an_allergy_added_after_the_offer_is_refused_again_for_plan_meal(monkeypatch, signed_in, dairy_free):
    tools.add_recipe("Shrimp Alfredo", ingredients=ALFREDO)
    day = tools.cooker.household_today().isoformat()
    _script(monkeypatch, [("plan_meal", {"meal_date": day, "meal": "Shrimp Alfredo", "slot": "dinner"})])
    token = _button(_chat(signed_in, "shrimp alfredo tonight"))["save_anyway"]["token"]
    _shellfish_added_since()

    res = signed_in.post("/api/chat/save-anyway", json={"token": token})
    assert res.status_code == 409, res.text
    body = res.json()
    assert body["status"] == "refused" and "Shrimp Alfredo" in body["said"]
    assert not any(e.get("meal") == "Shrimp Alfredo" for e in tools.get_meal_plan(days_ahead=1) or [])
    # The fresh button is for what the refusal says now: both clashes.
    fresh = body["save_anyway"]["token"]
    assert fresh != token
    ok = signed_in.post("/api/chat/save-anyway", json={"token": fresh})
    assert ok.status_code == 200, ok.text


def test_an_allergy_added_after_the_offer_is_refused_again_for_a_dictated_recipe(monkeypatch, signed_in):
    tools.add_member("Emily")
    tools.set_member_dietary_restrictions("Emily", ["dairy free"])
    _script(monkeypatch, [("add_recipe", {"name": "Shrimp Alfredo", "ingredients": ALFREDO})])
    token = _button(_chat(signed_in, "save my shrimp alfredo"))["save_anyway"]["token"]
    _shellfish_added_since()

    res = signed_in.post("/api/chat/save-anyway", json={"token": token})
    assert res.status_code == 409, res.text
    assert "shrimp" in res.json()["said"].lower()
    assert tools.existing_recipe_named("Shrimp Alfredo") is None


def test_a_call_the_tool_could_not_replay_gets_no_button(monkeypatch, signed_in, dairy_free):
    day = tools.cooker.household_today().isoformat()
    _script(monkeypatch, [("plan_meal", {"meal_date": day, "meal": "Grilled Cheese", "slot": "dinner",
                                         "ingredients": [{"item": "cheddar"}]})])
    body = _chat(signed_in, "grilled cheese tonight")
    assert not any(a.get("save_anyway") for a in body["actions"])


def test_a_replay_that_crashes_answers_a_sentence_not_a_500(monkeypatch, signed_in, dairy_free):
    _script(monkeypatch, [("add_recipe", CHEESECAKE)])
    token = _button(_chat(signed_in, "save my cheesecake bars recipe"))["save_anyway"]["token"]

    def boom(**kw):
        raise TypeError("unexpected keyword")
    monkeypatch.setitem(agent.TOOL_FUNCTIONS, "add_recipe", boom)
    res = signed_in.post("/api/chat/save-anyway", json={"token": token})
    assert res.status_code == 409
    assert res.json() == {"status": "failed", "said": main.SAVE_ANYWAY_FAILED}
    assert tools.existing_recipe_named("Cheesecake Bars") is None
