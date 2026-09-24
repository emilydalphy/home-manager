"""
The four /api/chat/proposals routes, over real HTTP.

app/tools/proposals.py is well covered (tests/test_chat_change_card.py, 24
tests) and every one of those calls the module directly. The ROUTES that
carry it to the shell had no test at all — and they are not a thin pass-
through: between them they translate a `gone` proposal into a 404, a
missing candidate into a 400, an unreachable model into a 503, and any
other exception into a 500 that is scrubbed down to one calm sentence
before it reaches the browser. Each of those is a decision, and a decision
nothing exercises is one a refactor can quietly reverse.

The 500 one was found by this file rather than confirmed by it: the first
cut of that test asserted the route's own f"Server error: {e}" reached the
client, which is what the route's source says and is not what happens —
main._client_safe_detail replaces it. The test now pins the real
behaviour, and pins it AT THIS ROUTE rather than only where that helper is
defined, because this is one of the 146 sites handing it raw exception
text.

The one thing here that is a SAFETY property rather than a contract is the
household scope. A proposal id is a bare secret token in the URL, so the
question "can another household's id be applied?" is worth asking over
HTTP rather than trusting the module test that asks it in-process: the
route is where a caller actually arrives, and a middleware change is
exactly the kind of thing that would break the binding without touching
proposals.py at all. It is scoped by construction (_PROPOSALS is keyed by
household before the id is ever looked up) so this is defence in depth —
said plainly, because an "isolation test" that cannot fail is worse than
none.

NOT covered here, deliberately: what the card MEANS (whose job is
test_chat_change_card.py) and the shell's own rendering of it.
"""
from __future__ import annotations

import datetime

import pytest

from conftest import household_today

from app import households, main as app_main, security, tools
from app.tools import proposals as prop
from app.tools import swap_in_place as _swap


BETA_PASSPHRASE = "proposal-routes-beta-passphrase"

TODAY = household_today()
DAYS = [(TODAY + datetime.timedelta(days=i)).isoformat() for i in range(7)]
DAY1, DAY2, DAY4 = DAYS[0], DAYS[1], DAYS[3]


def _sign_in(client, password):
    res = client.post(
        "/login", data={"password": password, "next": "/"}, follow_redirects=False
    )
    assert res.status_code == 303, "sign-in should redirect on success"


@pytest.fixture
def emily(client):
    _sign_in(client, "test-password")
    return client


def _cand(name, minutes=25, protein="chicken"):
    return {
        "meal_name": name,
        "reason": "Quick, and nothing to thaw.",
        "ingredients": [{"item": name.split()[0], "qty": "1 lb", "category": "meat/seafood"}],
        "instructions": ["Cook it.", "Serve it."],
        "food_groups": ["protein", "carb"],
        "main_protein": protein,
        "prep_time_minutes": 5,
        "cook_time_minutes": minutes - 5,
        "default_servings": 2,
    }


def _seed_week():
    """A plan with three dinners on it, in whichever household is bound."""
    tools.add_member("Emily")
    tools.add_member("Vineeth")
    for name in ("Pork Chops", "Chili", "Chicken Tikka"):
        tools.add_recipe(
            name,
            ingredients=[{"item": name.split()[0], "qty": "1 lb", "category": "meat/seafood"}],
            food_groups=["protein"], prep_time_minutes=10, cook_time_minutes=20,
        )
    plan_id = tools.create_weekly_plan(DAY1)["weekly_plan_id"]
    for day, dish in ((DAY1, "Pork Chops"), (DAY2, "Chili"), (DAY4, "Chicken Tikka")):
        tools.plan_meal(day, dish, slot="dinner", weekly_plan_id=plan_id, reasoning="fits the week")
    return plan_id


@pytest.fixture
def week():
    prop._PROPOSALS.clear()
    return _seed_week()


def _proposal(plan_id, dish="Shrimp Tacos", day=DAY4):
    """One open proposal with a single change row, exactly as the tool leaves it."""
    out = tools.propose_plan_changes(plan_id, [
        {"date": day, "slot": "dinner", "action": "change", "candidates": [_cand(dish, 20, "shrimp")]},
    ])
    return out["proposal_id"]


def _dinner_title(plan_id, day):
    for d in tools.get_week_menu(plan_id)["days"]:
        if d["date"] == day:
            return (d["dinner"] or {}).get("title")
    return None


# ---------- every one of them needs a signed-in household ----------

@pytest.mark.parametrize("path, body", [
    ("choose", {"row": 0, "candidate": 0}),
    ("another", {"row": 0}),
    ("apply", None),
    ("undo", None),
])
def test_a_proposal_route_refuses_a_caller_who_is_not_signed_in(client, path, body):
    res = client.post(f"/api/chat/proposals/whatever/{path}", json=body)
    assert res.status_code == 401, f"{path} answered {res.status_code}"


# ---------- an id that names nothing is a 404, in the app's own words ----------

@pytest.mark.parametrize("path, body", [
    ("choose", {"row": 0, "candidate": 0}),
    ("apply", None),
    ("undo", None),
])
def test_an_unknown_proposal_id_is_a_404_carrying_the_calm_sentence(emily, week, path, body):
    res = emily.post(f"/api/chat/proposals/no-such-id/{path}", json=body)
    assert res.status_code == 404, f"{path} answered {res.status_code}"
    assert res.json()["detail"] == prop.GONE


def test_another_on_an_unknown_proposal_is_a_404_before_any_model_call(emily, week, monkeypatch):
    # The picker must never be reached for an id that names nothing — a
    # model call is money, and a 404 is knowable without one.
    called = []
    monkeypatch.setattr(_swap, "_pick_replacement", lambda ctx: called.append(1))
    res = emily.post("/api/chat/proposals/no-such-id/another", json={"row": 0})
    assert res.status_code == 404
    assert res.json()["detail"] == prop.GONE
    assert called == []


# ---------- choose ----------

def test_choose_without_a_candidate_is_a_400_and_not_a_crash(emily, week):
    pid = _proposal(week)
    res = emily.post(f"/api/chat/proposals/{pid}/choose", json={"row": 0})
    assert res.status_code == 400
    assert "candidate is required" in res.json()["detail"]


def test_choose_names_the_row_it_was_given_and_writes_nothing(emily, week):
    pid = _proposal(week)
    res = emily.post(f"/api/chat/proposals/{pid}/choose", json={"row": 0, "candidate": 0})
    assert res.status_code == 200
    body = res.json()
    assert body["status"] == "chosen"
    assert body["proposal"]["rows"][0]["chosen"] == 0
    # A tap on an option is not a save.
    assert _dinner_title(week, DAY4) == "Chicken Tikka"


def test_choose_with_a_row_that_is_not_on_the_card_is_reported_not_raised(emily, week):
    pid = _proposal(week)
    res = emily.post(f"/api/chat/proposals/{pid}/choose", json={"row": 9, "candidate": 0})
    # A stale screen is not a server error: the route answers 200 and the
    # body says the row could not be taken.
    assert res.status_code == 200
    assert res.json()["status"] != "chosen"


# ---------- another ----------

def test_another_reaches_the_picker_and_the_new_dish_becomes_the_chosen_one(emily, week, monkeypatch):
    pid = _proposal(week)
    monkeypatch.setattr(_swap, "_pick_replacement", lambda ctx: _cand("Lemon Chicken", 25))
    res = emily.post(f"/api/chat/proposals/{pid}/another", json={"row": 0})
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["status"] == "picked"
    row = body["proposal"]["rows"][0]
    assert row["candidates"][row["chosen"]]["meal_name"] == "Lemon Chicken"
    # Still nothing written.
    assert _dinner_title(week, DAY4) == "Chicken Tikka"


def test_another_answers_503_when_the_model_cannot_be_reached(emily, week, monkeypatch):
    from app.agent import AssistantUnavailableError

    pid = _proposal(week)

    def _down(ctx):
        raise AssistantUnavailableError("the assistant is unavailable just now")

    monkeypatch.setattr(_swap, "_pick_replacement", _down)
    res = emily.post(f"/api/chat/proposals/{pid}/another", json={"row": 0})
    # 503, not 500: this is "come back in a moment", not "this app is broken".
    assert res.status_code == 503
    assert "unavailable" in res.json()["detail"]


def test_another_answers_500_when_the_picker_breaks_in_some_other_way(emily, week, monkeypatch):
    pid = _proposal(week)

    def _boom(ctx):
        raise RuntimeError("picker exploded")

    monkeypatch.setattr(_swap, "_pick_replacement", _boom)
    res = emily.post(f"/api/chat/proposals/{pid}/another", json={"row": 0})
    assert res.status_code == 500
    # The route BUILDS its detail as f"Server error: {e}" — and the
    # household never sees that, because main._client_safe_detail replaces
    # every 5xx (bar 503) with one calm sentence on the way out. Asserted
    # here rather than only where that helper is defined, because this
    # route is one of the 146 that hand it raw exception text and the
    # scrubbing is the only thing between "picker exploded" and a toast.
    # The exception is not lost: the route logged the traceback first.
    assert res.json()["detail"] == app_main.SERVER_TROUBLE_LINE
    assert "picker exploded" not in res.text


# ---------- apply, and undo ----------

def test_apply_lands_the_chosen_dish_on_the_week(emily, week):
    pid = _proposal(week)
    res = emily.post(f"/api/chat/proposals/{pid}/apply")
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["status"] == "applied"
    assert body["refused"] == []
    assert _dinner_title(week, DAY4) == "Shrimp Tacos"


def test_applying_twice_does_not_plan_the_dish_a_second_time(emily, week):
    pid = _proposal(week)
    assert emily.post(f"/api/chat/proposals/{pid}/apply").status_code == 200
    second = emily.post(f"/api/chat/proposals/{pid}/apply")
    assert second.status_code == 200
    assert second.json()["status"] == "applied"
    assert _dinner_title(week, DAY4) == "Shrimp Tacos"


def test_undo_puts_the_night_back_the_way_it_was(emily, week):
    pid = _proposal(week)
    emily.post(f"/api/chat/proposals/{pid}/apply")
    assert _dinner_title(week, DAY4) == "Shrimp Tacos"
    res = emily.post(f"/api/chat/proposals/{pid}/undo")
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["status"] == "restored"
    assert [r["date"] for r in body["restored"]] == [DAY4]
    assert _dinner_title(week, DAY4) == "Chicken Tikka"


def test_undo_on_a_proposal_that_was_never_applied_changes_nothing(emily, week):
    pid = _proposal(week)
    res = emily.post(f"/api/chat/proposals/{pid}/undo")
    assert res.status_code == 200
    assert res.json()["restored"] == []
    assert _dinner_title(week, DAY4) == "Chicken Tikka"


# ---------- the household scope, over HTTP ----------

@pytest.fixture
def beta_household():
    return households.create_household("Proposal Routes Beta", BETA_PASSPHRASE)


def test_one_households_proposal_id_names_nothing_in_another(client, beta_household, week):
    """
    DEFENCE IN DEPTH, and it says so rather than claiming a leak was found:
    _PROPOSALS is keyed by household before an id is ever looked up, so this
    holds by construction today. It is asserted at the ROUTE because that is
    where a caller arrives, and because the binding it rests on
    (security.auth_middleware setting the household ContextVar per request)
    lives nowhere near proposals.py — a change there would break this
    without touching the module this is about.
    """
    _sign_in(client, "test-password")
    pid = _proposal(week)
    # Emily can reach her own.
    assert client.post(f"/api/chat/proposals/{pid}/choose",
                       json={"row": 0, "candidate": 0}).status_code == 200

    _sign_in(client, BETA_PASSPHRASE)
    assert client.cookies[security.COOKIE_NAME]
    for path, body in (("choose", {"row": 0, "candidate": 0}),
                       ("another", {"row": 0}),
                       ("apply", None),
                       ("undo", None)):
        res = client.post(f"/api/chat/proposals/{pid}/{path}", json=body)
        assert res.status_code == 404, f"{path} answered {res.status_code} for the wrong household"
        assert res.json()["detail"] == prop.GONE

    # And the week the id belongs to is untouched by any of that.
    _sign_in(client, "test-password")
    assert _dinner_title(week, DAY4) == "Chicken Tikka"


def test_the_isolation_test_above_can_actually_fail(client, beta_household, week, monkeypatch):
    """
    The mutation that makes the guard worthless: one bucket for everybody.
    Without this, `test_one_households_proposal_id_names_nothing_in_another`
    is the kind of isolation test that passes because the fixture never
    really crossed a boundary — which this repo has been bitten by before
    (see tests/test_leftover_chain_household_filter.py's own note).
    """
    shared: dict = {}
    monkeypatch.setattr(prop, "_household_bucket", lambda: shared)

    _sign_in(client, "test-password")
    pid = _proposal(week)
    _sign_in(client, BETA_PASSPHRASE)
    res = client.post(f"/api/chat/proposals/{pid}/choose", json={"row": 0, "candidate": 0})
    assert res.status_code == 200, (
        "with one shared bucket the other household DOES reach it — so the "
        "test above is measuring the household key and not something else"
    )
