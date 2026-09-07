"""
Cooking three mornings' worth on Monday, because the plan says the same
breakfast five times.

Emily, 2026-09-07: "The meal plan says Egg White Bites is for every
morning. We don't want to make egg bites every morning. Add a thing asking
how many days do we want to cook it now, and the user can mark off the
days of the week it's on the plan that we should cook the portions for
now."

A day-based plan writes each morning as its own entry, so the Cook screen
had five cooks of one dish. The leftover chains already knew how to make
one night's batch feed a later one (leftovers.py); what was missing was a
way for the household to say WHICH days — so app/tools/cook_ahead.py adds
the offer (cook_ahead_options / attach_cook_ahead) and the write
(set_cook_ahead), and these tests pin down both halves plus the two things
that must not move: the chain stays the shape plan_leftover_chains already
validates, and the grocery list doesn't budge (the week still eats the
same portions — only the cooking is consolidated).
"""
import datetime

import pytest

from app import households, security, tools


def _monday() -> datetime.date:
    today = datetime.date.today()
    return today - datetime.timedelta(days=today.weekday())


def _day(offset: int) -> str:
    return (_monday() + datetime.timedelta(days=offset)).isoformat()


MON, TUE, WED, THU, FRI = _day(0), _day(1), _day(2), _day(3), _day(4)


def _household(*names):
    for n in names or ("Alex", "Sam", "Rae"):
        tools.add_member(n)


def _eggs():
    tools.add_recipe(
        "Egg White Bites",
        ingredients=[
            {"item": "egg whites", "qty": "1 cup"},
            {"item": "spinach", "qty": "2 cups"},
            {"item": "salt", "qty": "to taste"},
        ],
        default_servings=3,
    )


def _oats():
    tools.add_recipe("Overnight Oats", ingredients=[{"item": "oats", "qty": "1 cup"}], default_servings=3)


def _breakfast_week(days=(MON, TUE, WED, THU)):
    """The reported plan: the same breakfast on several mornings, each its
    own entry, no chain between any of them."""
    plan_id = tools.create_weekly_plan(_monday().isoformat())["weekly_plan_id"]
    ids = {}
    for d in days:
        ids[d] = tools.plan_meal(d, "Egg White Bites", slot="breakfast", weekly_plan_id=plan_id)["entry_id"]
    return plan_id, ids


def _cards(plan_id):
    return {m["entry_id"]: m for m in tools.get_cooker_view(plan_id)["meals"]}


def _days(card):
    return [d["entry_id"] for d in card["cook_ahead"]["days"]]


def _derived_from(entry_id):
    from app.db import get_conn
    import json

    conn = get_conn()
    row = conn.execute("SELECT derived_from_json FROM meal_plan_entries WHERE id = ?", (entry_id,)).fetchone()
    conn.close()
    return json.loads(row["derived_from_json"] or "{}")


def _qty(card):
    return {i["item"]: i["qty"] for i in card["ingredients"]}


# ---------- which days get offered ----------

def test_the_picker_offers_the_later_mornings_of_the_same_dish():
    _household()
    _eggs()
    plan_id, ids = _breakfast_week()

    cards = _cards(plan_id)

    assert _days(cards[ids[MON]]) == [ids[TUE], ids[WED], ids[THU]]
    assert cards[ids[MON]]["cook_ahead"]["days"][0] == {
        "entry_id": ids[TUE], "date": TUE, "slot": "breakfast", "eaters": 3, "selected": False,
    }
    # Only later days: Wednesday can still cook ahead for Thursday, and the
    # last morning of the run has nothing left to offer.
    assert _days(cards[ids[WED]]) == [ids[THU]]
    assert _days(cards[ids[THU]]) == []


def test_the_picker_ignores_a_different_dish_and_a_different_slot():
    _household()
    _eggs()
    _oats()
    plan_id, ids = _breakfast_week(days=(MON, WED))
    tools.plan_meal(TUE, "Overnight Oats", slot="breakfast", weekly_plan_id=plan_id)
    # The same dish, but at dinner — a different meal of the day, not a
    # repeat of this one.
    tools.plan_meal(THU, "Egg White Bites", slot="dinner", weekly_plan_id=plan_id)

    assert _days(_cards(plan_id)[ids[MON]]) == [ids[WED]]


def test_a_day_already_covered_by_another_batch_is_not_offered_twice():
    _household()
    _eggs()
    plan_id, ids = _breakfast_week()
    tools.set_cook_ahead(ids[MON], [ids[WED]])

    cards = _cards(plan_id)

    # Tuesday is still a cook, but Wednesday is Monday's now — the only
    # morning Tuesday can still claim is Thursday.
    assert _days(cards[ids[TUE]]) == [ids[THU]]
    # ...and the batch itself shows Wednesday ticked, which is what makes
    # un-ticking possible.
    assert [(d["entry_id"], d["selected"]) for d in cards[ids[MON]]["cook_ahead"]["days"]] == [
        (ids[TUE], False), (ids[WED], True), (ids[THU], False),
    ]
    # A day being cooked ahead for is not a cook, so it is offered nothing.
    assert _days(cards[ids[WED]]) == []


def test_a_day_that_is_itself_a_batch_is_not_offered_as_someone_elses_day():
    _household()
    _eggs()
    plan_id, ids = _breakfast_week()
    tools.set_cook_ahead(ids[TUE], [ids[THU]])

    assert _days(_cards(plan_id)[ids[MON]]) == [ids[WED]]


# ---------- the write ----------

def test_setting_cook_ahead_writes_both_halves_of_the_chain():
    _household()
    _eggs()
    plan_id, ids = _breakfast_week()

    tools.set_cook_ahead(ids[MON], [ids[TUE], ids[WED]])

    source = _derived_from(ids[MON])
    assert source["make_double_for"] == [f"{TUE}:breakfast", f"{WED}:breakfast"]
    assert "Monday" in source["make_double_note"]
    for d in (TUE, WED):
        covered = _derived_from(ids[d])
        assert covered["links_to"] == f"{MON}:breakfast"
        assert covered["cook_ahead"] is True

    # The agreement check both sides have to pass before anything acts on
    # a chain — a chain only this feature understood would be no chain.
    chains = tools.plan_leftover_chains(plan_id)
    assert [t["entry_id"] for t in chains["sources"][ids[MON]]["targets"]] == [ids[TUE], ids[WED]]
    assert set(chains["leftovers"]) == {ids[TUE], ids[WED]}


def test_the_cook_view_shows_one_batch_and_the_rest_made_ahead():
    _household()
    _eggs()
    plan_id, ids = _breakfast_week()

    tools.set_cook_ahead(ids[MON], [ids[TUE], ids[WED]])
    cards = _cards(plan_id)

    monday = cards[ids[MON]]
    assert monday["servings"] == 9, "three at each of the three mornings it covers"
    assert _qty(monday) == {"egg whites": "3 cups", "spinach": "6 cups", "salt": "to taste"}
    # Not "leftovers": nobody has eaten these portions yet.
    assert monday["covers_note"] == "Cooking for 9 — enough for Monday, Tuesday, and Wednesday."

    for d in (TUE, WED):
        card = cards[ids[d]]
        assert card["is_leftovers"] is True
        assert card["leftovers_headline"] == "Made ahead — Monday’s Egg White Bites"
        assert card["ingredients"] == []
        assert card["has_full_recipe"] is False
    # Thursday was never ticked and is untouched — its own cook, its own
    # recipe, offered nothing further to cover.
    assert cards[ids[THU]]["is_leftovers"] is False
    assert cards[ids[THU]]["servings"] is None


def test_unticking_a_day_gives_it_back_its_own_cook():
    _household()
    _eggs()
    plan_id, ids = _breakfast_week()
    tools.set_cook_ahead(ids[MON], [ids[TUE], ids[WED]])

    tools.set_cook_ahead(ids[MON], [ids[TUE]])

    assert _derived_from(ids[MON])["make_double_for"] == [f"{TUE}:breakfast"]
    released = _derived_from(ids[WED])
    assert "links_to" not in released
    assert "cook_ahead" not in released

    cards = _cards(plan_id)
    assert cards[ids[MON]]["servings"] == 6
    assert cards[ids[WED]]["is_leftovers"] is False
    assert _qty(cards[ids[WED]])["egg whites"] == "1 cup"


def test_unticking_every_day_makes_it_an_ordinary_morning_again():
    _household()
    _eggs()
    plan_id, ids = _breakfast_week()
    tools.set_cook_ahead(ids[MON], [ids[TUE], ids[WED]])

    tools.set_cook_ahead(ids[MON], [])

    source = _derived_from(ids[MON])
    assert "make_double_for" not in source
    assert "make_double_note" not in source
    assert tools.plan_leftover_chains(plan_id) == {"sources": {}, "leftovers": {}}

    monday = _cards(plan_id)[ids[MON]]
    assert monday.get("covers_note") is None
    assert monday["servings"] is None
    assert _qty(monday)["egg whites"] == "1 cup"


# ---------- the refusals ----------

def test_a_day_another_batch_already_covers_is_refused():
    _household()
    _eggs()
    plan_id, ids = _breakfast_week()
    tools.set_cook_ahead(ids[MON], [ids[WED]])

    result = tools.set_cook_ahead(ids[TUE], [ids[WED]])

    assert isinstance(result, str)
    assert "Wednesday" in result
    # Refused means nothing moved: Monday still covers Wednesday, and
    # Tuesday is still an ordinary cook.
    assert _derived_from(ids[WED])["links_to"] == f"{MON}:breakfast"
    assert "make_double_for" not in _derived_from(ids[TUE])


def test_a_day_that_is_a_batch_of_its_own_is_refused():
    _household()
    _eggs()
    plan_id, ids = _breakfast_week()
    tools.set_cook_ahead(ids[TUE], [ids[THU]])

    result = tools.set_cook_ahead(ids[MON], [ids[TUE]])

    assert isinstance(result, str)
    assert "Tuesday" in result
    assert _derived_from(ids[TUE])["make_double_for"] == [f"{THU}:breakfast"]


def test_a_reheat_card_cannot_be_the_one_that_cooks():
    _household()
    _eggs()
    plan_id, ids = _breakfast_week()
    tools.set_cook_ahead(ids[MON], [ids[TUE]])

    result = tools.set_cook_ahead(ids[TUE], [ids[THU]])

    assert isinstance(result, str)
    assert "reheats" in result


# ---------- what must not change ----------

def test_the_planners_own_leftovers_still_read_as_leftovers():
    """
    A dinner chained by the planner is a different thing from a batch the
    household cooked ahead on purpose, and it keeps its own words.
    """
    _household()
    tools.add_recipe("Chili", ingredients=[{"item": "beans", "qty": "2 cups"}], default_servings=3)
    plan_id = tools.create_weekly_plan(_monday().isoformat())["weekly_plan_id"]
    tue = tools.plan_meal(TUE, "Chili", slot="dinner", weekly_plan_id=plan_id)["entry_id"]
    thu = tools.plan_meal(THU, "Chili", slot="dinner", weekly_plan_id=plan_id,
                          derived_from={"links_to": f"{TUE}:dinner"})["entry_id"]
    tools.repair_leftover_chains(plan_id)

    cards = _cards(plan_id)

    assert cards[thu]["leftovers_headline"] == "Leftovers — Tuesday’s Chili"
    assert "leftovers on Thursday" in cards[tue]["covers_note"]


def test_the_grocery_list_does_not_move():
    """
    The household still eats the same number of portions this week — only
    the cooking is consolidated — so the ledger and every quantity on it
    stay exactly as approving the week left them.
    """
    _household()
    _eggs()
    plan_id, ids = _breakfast_week()
    tools.approve_weekly_plan(plan_id, "Emily")
    before = tools.list_grocery_list()
    assert before, "approving the week should have bought something to begin with"

    tools.set_cook_ahead(ids[MON], [ids[TUE], ids[WED]])

    assert tools.list_grocery_list() == before


# ---------- the route ----------

def test_the_route_returns_the_refreshed_cook_view(signed_in):
    _household()
    _eggs()
    plan_id, ids = _breakfast_week()

    res = signed_in.post(
        "/api/cooker/cook-ahead",
        json={"source_entry_id": ids[MON], "covered_entry_ids": [ids[TUE], ids[WED]]},
    )

    assert res.status_code == 200
    cards = {m["entry_id"]: m for m in res.json()["meals"]}
    assert cards[ids[MON]]["servings"] == 9
    assert cards[ids[TUE]]["is_leftovers"] is True
    # The picker comes back with the pick already on it — Tuesday and
    # Wednesday ticked, Thursday still there to tick later.
    assert [d["selected"] for d in cards[ids[MON]]["cook_ahead"]["days"]] == [True, True, False]


def test_the_route_answers_a_refusal_with_the_sentence_to_show(signed_in):
    _household()
    _eggs()
    plan_id, ids = _breakfast_week()
    tools.set_cook_ahead(ids[MON], [ids[WED]])

    res = signed_in.post(
        "/api/cooker/cook-ahead",
        json={"source_entry_id": ids[TUE], "covered_entry_ids": [ids[WED]]},
    )

    assert res.status_code == 400
    assert "Wednesday" in res.json()["detail"]


def test_the_route_cannot_reach_another_households_plan(client):
    """
    Entry ids are integers a stranger could guess. The write is scoped to
    the signed-in household exactly like every read is, so another
    household's morning is simply not there to be chained.
    """
    _household()
    _eggs()
    plan_id, ids = _breakfast_week()

    households.create_household("The Beta Testers", "beta-tester-passphrase")
    res = client.post("/login", data={"password": "beta-tester-passphrase", "next": "/"},
                      follow_redirects=False)
    assert res.status_code == 303
    assert security.COOKIE_NAME in client.cookies

    res = client.post(
        "/api/cooker/cook-ahead",
        json={"source_entry_id": ids[MON], "covered_entry_ids": [ids[TUE]]},
    )

    assert res.status_code == 400
    assert "make_double_for" not in _derived_from(ids[MON])
