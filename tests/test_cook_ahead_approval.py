"""
Asking about cooking ahead at approval, not only on the Cook card.

Emily, 2026-09-08 (item 8): "ALSO ask at weekly approval, and no cap on
days." The Cook card's picker (cook_ahead.py, test_cook_ahead.py) asks one
card at a time, which means a household only meets the question five cards
into a week of the same breakfast. The receipt they see the moment a week
is approved is the better place to ask it once for the whole plan — so
`cook_ahead_repeats` gathers every repeated dish, and
/api/week/{week}/cook-ahead-confirm writes the answers through the same
`set_cook_ahead` the Cook card uses.

What these pin down: the offer is computed from cook_ahead_options (never
a second set of rules that could drift from it), a dish that already has a
chain is not re-offered, the confirm writes the exact chain shape
plan_leftover_chains validates, one refusal doesn't discard the answers
either side of it, and the once-per-plan gate behaves like
defrost_asked_at.
"""
import datetime

import pytest

from app import db, households, security, tools


def _monday() -> datetime.date:
    today = datetime.date.today()
    return today - datetime.timedelta(days=today.weekday())


def _day(offset: int) -> str:
    return (_monday() + datetime.timedelta(days=offset)).isoformat()


WEEK = _monday().isoformat()
MON, TUE, WED, THU, FRI = _day(0), _day(1), _day(2), _day(3), _day(4)


def _household(*names):
    for n in names or ("Alex", "Sam", "Rae"):
        tools.add_member(n)


def _eggs():
    tools.add_recipe(
        "Egg White Bites",
        ingredients=[{"item": "egg whites", "qty": "1 cup"}],
        default_servings=3,
    )


def _chili():
    tools.add_recipe("Turkey Chili", ingredients=[{"item": "ground turkey", "qty": "1 lb"}], default_servings=3)


def _breakfast_week(days=(MON, TUE, WED, THU)):
    """The reported plan: the same breakfast on several mornings, each its
    own entry, no chain between any of them."""
    plan_id = tools.create_weekly_plan(WEEK)["weekly_plan_id"]
    ids = {}
    for d in days:
        ids[d] = tools.plan_meal(d, "Egg White Bites", slot="breakfast", weekly_plan_id=plan_id)["entry_id"]
    return plan_id, ids


def _derived_from(entry_id):
    import json

    conn = db.get_conn()
    row = conn.execute("SELECT derived_from_json FROM meal_plan_entries WHERE id = ?", (entry_id,)).fetchone()
    conn.close()
    return json.loads(row["derived_from_json"] or "{}")


def _asked_at(plan_id):
    conn = db.get_conn()
    row = conn.execute("SELECT cook_ahead_asked_at FROM weekly_plans WHERE id = ?", (plan_id,)).fetchone()
    conn.close()
    return row["cook_ahead_asked_at"]


# ---------- what the ask offers ----------

def test_the_ask_lists_a_repeated_dish_with_its_later_days(signed_in):
    _household()
    _eggs()
    plan_id, ids = _breakfast_week()

    res = signed_in.get(f"/api/week/{WEEK}/cook-ahead-items")

    assert res.status_code == 200
    body = res.json()
    assert body["weekly_plan_id"] == plan_id
    assert len(body["items"]) == 1
    item = body["items"][0]
    assert item["dish"] == "Egg White Bites"
    assert item["slot"] == "breakfast"
    # Monday cooks; the other three mornings are what it could cover.
    assert item["first"] == {"entry_id": ids[MON], "date": MON, "eaters": 3}
    assert item["later"] == [
        {"entry_id": ids[TUE], "date": TUE, "eaters": 3},
        {"entry_id": ids[WED], "date": WED, "eaters": 3},
        {"entry_id": ids[THU], "date": THU, "eaters": 3},
    ]
    # Three at each of the four mornings — the whole run, since Emily's
    # answer to "how many days" was no cap.
    assert item["eaters_total"] == 12


def test_a_dish_planned_once_is_not_a_repeat(signed_in):
    _household()
    _eggs()
    _chili()
    plan_id, _ids = _breakfast_week(days=(MON,))
    tools.plan_meal(TUE, "Turkey Chili", slot="dinner", weekly_plan_id=plan_id)

    assert signed_in.get(f"/api/week/{WEEK}/cook-ahead-items").json()["items"] == []


def test_the_same_dish_in_two_slots_is_two_separate_repeats(signed_in):
    """Breakfast eggs and dinner eggs are not one run of five — cooking a
    morning's batch for a dinner is a different claim, and cook_ahead_options
    already refuses to cross slots."""
    _household()
    _eggs()
    plan_id, ids = _breakfast_week(days=(MON, TUE))
    dinner_wed = tools.plan_meal(WED, "Egg White Bites", slot="dinner", weekly_plan_id=plan_id)["entry_id"]
    dinner_thu = tools.plan_meal(THU, "Egg White Bites", slot="dinner", weekly_plan_id=plan_id)["entry_id"]

    items = signed_in.get(f"/api/week/{WEEK}/cook-ahead-items").json()["items"]

    assert [(i["slot"], i["first"]["entry_id"], [d["entry_id"] for d in i["later"]]) for i in items] == [
        ("breakfast", ids[MON], [ids[TUE]]),
        ("dinner", dinner_wed, [dinner_thu]),
    ]


def test_a_dish_that_already_cooks_ahead_is_not_offered_again(signed_in):
    """The Cook card's picker owns a chain once it exists — re-offering the
    same dish here would let two screens argue about the same batch."""
    _household()
    _eggs()
    plan_id, ids = _breakfast_week()
    tools.set_cook_ahead(ids[MON], [ids[TUE]])

    assert signed_in.get(f"/api/week/{WEEK}/cook-ahead-items").json()["items"] == []


def test_a_reheat_night_the_planner_wrote_is_not_offered(signed_in):
    """A planner-written leftovers night is somebody's batch already, for
    the same reason — and the reheat entry itself is not a cook."""
    _household()
    _chili()
    plan_id = tools.create_weekly_plan(WEEK)["weekly_plan_id"]
    mon = tools.plan_meal(MON, "Turkey Chili", slot="dinner", weekly_plan_id=plan_id)["entry_id"]
    tue = tools.plan_meal(TUE, "Turkey Chili", slot="dinner", weekly_plan_id=plan_id)["entry_id"]
    tools.set_cook_ahead(mon, [tue])

    items = signed_in.get(f"/api/week/{WEEK}/cook-ahead-items").json()["items"]

    assert items == []
    # ...and the reheat day is a reheat, not a second source hiding in the list.
    assert tue in tools.plan_leftover_chains(plan_id)["leftovers"]


def test_the_items_endpoint_404s_for_a_week_with_no_plan(signed_in):
    assert signed_in.get("/api/week/2099-01-05/cook-ahead-items").status_code == 404


# ---------- the confirm ----------

def test_confirm_writes_the_chain_and_marks_asked(signed_in):
    _household()
    _eggs()
    plan_id, ids = _breakfast_week()
    assert _asked_at(plan_id) is None

    res = signed_in.post(
        f"/api/week/{WEEK}/cook-ahead-confirm",
        json={"choices": [{"source_entry_id": ids[MON], "covered_entry_ids": [ids[TUE], ids[WED]]}]},
    )

    assert res.status_code == 200
    body = res.json()
    assert body["refused"] == []
    assert body["applied"][0]["covered_entry_ids"] == [ids[TUE], ids[WED]]

    # The same shape test_cook_ahead.py pins for the Cook card's own write:
    # both halves agree, so plan_leftover_chains honours the pairing.
    source = _derived_from(ids[MON])
    assert source["make_double_for"] == [f"{TUE}:breakfast", f"{WED}:breakfast"]
    assert "Monday" in source["make_double_note"]
    for d in (TUE, WED):
        covered = _derived_from(ids[d])
        assert covered["links_to"] == f"{MON}:breakfast"
        assert covered["cook_ahead"] is True
    chains = tools.plan_leftover_chains(plan_id)
    assert [t["entry_id"] for t in chains["sources"][ids[MON]]["targets"]] == [ids[TUE], ids[WED]]
    assert set(chains["leftovers"]) == {ids[TUE], ids[WED]}

    assert _asked_at(plan_id) is not None


def test_confirm_handles_more_than_one_repeat_in_a_week(signed_in):
    _household()
    _eggs()
    _chili()
    plan_id, ids = _breakfast_week(days=(MON, TUE))
    chili_wed = tools.plan_meal(WED, "Turkey Chili", slot="dinner", weekly_plan_id=plan_id)["entry_id"]
    chili_thu = tools.plan_meal(THU, "Turkey Chili", slot="dinner", weekly_plan_id=plan_id)["entry_id"]

    res = signed_in.post(
        f"/api/week/{WEEK}/cook-ahead-confirm",
        json={"choices": [
            {"source_entry_id": ids[MON], "covered_entry_ids": [ids[TUE]]},
            {"source_entry_id": chili_wed, "covered_entry_ids": [chili_thu]},
        ]},
    )

    assert res.status_code == 200
    assert res.json()["refused"] == []
    assert set(tools.plan_leftover_chains(plan_id)["leftovers"]) == {ids[TUE], chili_thu}


def test_no_choices_is_cook_each_on_its_own_and_still_marks_asked(signed_in):
    """"Cook each on its own" answers the question as completely as ticking
    days does — the card is not shown again for this plan, and nothing is
    written to the plan itself."""
    _household()
    _eggs()
    plan_id, ids = _breakfast_week()

    res = signed_in.post(f"/api/week/{WEEK}/cook-ahead-confirm", json={"choices": []})

    assert res.status_code == 200
    assert res.json() == {"weekly_plan_id": plan_id, "applied": [], "refused": []}
    assert _asked_at(plan_id) is not None
    assert tools.plan_leftover_chains(plan_id)["leftovers"] == {}
    assert "make_double_for" not in _derived_from(ids[MON])


def test_a_refused_choice_is_reported_and_the_others_still_land(signed_in):
    """Not all-or-nothing: each block on the card is its own question, so a
    refusal comes back as the sentence to show rather than throwing away the
    answers around it. Here the second choice asks a morning that the first
    choice just turned into a reheat to be the day that cooks."""
    _household()
    _eggs()
    _chili()
    plan_id, ids = _breakfast_week()
    chili_wed = tools.plan_meal(WED, "Turkey Chili", slot="dinner", weekly_plan_id=plan_id)["entry_id"]
    chili_thu = tools.plan_meal(THU, "Turkey Chili", slot="dinner", weekly_plan_id=plan_id)["entry_id"]

    res = signed_in.post(
        f"/api/week/{WEEK}/cook-ahead-confirm",
        json={"choices": [
            {"source_entry_id": ids[MON], "covered_entry_ids": [ids[TUE]]},
            {"source_entry_id": ids[TUE], "covered_entry_ids": [ids[THU]]},
            {"source_entry_id": chili_wed, "covered_entry_ids": [chili_thu]},
        ]},
    )

    assert res.status_code == 200
    body = res.json()
    assert [r["source_entry_id"] for r in body["refused"]] == [ids[TUE]]
    assert "reheats an earlier batch" in body["refused"][0]["note"]
    # The first and third choices were written anyway...
    assert set(tools.plan_leftover_chains(plan_id)["leftovers"]) == {ids[TUE], chili_thu}
    # ...and the refused one wrote nothing.
    assert "make_double_for" not in _derived_from(ids[TUE])
    assert _asked_at(plan_id) is not None


def test_the_confirm_404s_for_a_week_with_no_plan(signed_in):
    assert signed_in.post("/api/week/2099-01-05/cook-ahead-confirm", json={"choices": []}).status_code == 404


# ---------- household scoping ----------

def test_another_household_sees_neither_the_repeats_nor_the_plan(client):
    """Week starts and entry ids are both guessable. Another household's
    Monday is simply not there — the week resolves to no plan at all for
    them, so both routes 404 before any entry id is looked at."""
    _household()
    _eggs()
    plan_id, ids = _breakfast_week()

    households.create_household("The Beta Testers", "beta-tester-passphrase")
    res = client.post("/login", data={"password": "beta-tester-passphrase", "next": "/"},
                      follow_redirects=False)
    assert res.status_code == 303
    assert security.COOKIE_NAME in client.cookies

    assert client.get(f"/api/week/{WEEK}/cook-ahead-items").status_code == 404
    confirm = client.post(
        f"/api/week/{WEEK}/cook-ahead-confirm",
        json={"choices": [{"source_entry_id": ids[MON], "covered_entry_ids": [ids[TUE]]}]},
    )
    assert confirm.status_code == 404
    assert "make_double_for" not in _derived_from(ids[MON])
    assert _asked_at(plan_id) is None


# ---------- the gate and its migration ----------

def test_the_gate_is_passed_through_to_the_screens(signed_in):
    _household()
    _eggs()
    plan_id, _ids = _breakfast_week()

    assert tools.get_weekly_plan(plan_id)["cook_ahead_asked_at"] is None
    assert tools.get_week_menu(plan_id)["cook_ahead_asked_at"] is None

    tools.mark_cook_ahead_asked(plan_id)

    assert tools.get_weekly_plan(plan_id)["cook_ahead_asked_at"] is not None
    assert tools.get_week_menu(plan_id)["cook_ahead_asked_at"] is not None


def test_marking_asked_more_than_once_is_allowed():
    """The Cook view's "Cooking ahead?" link can reopen and re-answer the
    card any number of times, exactly as the freezer check's re-ask can."""
    _household()
    _eggs()
    plan_id, _ids = _breakfast_week()

    tools.mark_cook_ahead_asked(plan_id)
    assert _asked_at(plan_id) is not None
    tools.mark_cook_ahead_asked(plan_id)
    assert _asked_at(plan_id) is not None


def test_the_column_migration_is_idempotent():
    """init_db runs on every boot, and Railway boots often — adding the
    column a second time must be a no-op, not a crash."""
    db.init_db()
    db.init_db()

    conn = db.get_conn()
    cols = [r["name"] for r in conn.execute("PRAGMA table_info(weekly_plans)").fetchall()]
    conn.close()

    assert cols.count("cook_ahead_asked_at") == 1
