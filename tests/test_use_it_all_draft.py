"""
The draft uses everything going off that it can fit, and shows where
(Loop Board B2); what won't fit gets frozen, added as a side, or let go
(B3). Emily, 2026-10-26: "If it can't be used fresh, then yes it should be
frozen, then added to the freezer inventory so you know to use it
sometimes."

The model is stubbed at agent.generate_weekly_plan_llm (the pattern
tests/test_freezer_portion_planned.py uses). What is under test is the
CODE's check of the model's claims — telling is not preventing.

Every test says whether it is a CATCH (red on main) or a GUARD.
"""
from __future__ import annotations

import datetime
import json

from conftest import household_today

from app import agent, tools
from app.db import get_conn
from app.tools import use_up

from test_freezer_portion_planned import (  # noqa: F401  (fixtures used by name)
    _entries, _monday, _slot, household, stub_model,
)


def _inv(item, *, turns=None, location="fridge", category="produce", frozen_days_ago=None):
    conn = get_conn()
    frozen_on = None if frozen_days_ago is None else (household_today() - datetime.timedelta(days=frozen_days_ago)).isoformat()
    cur = conn.execute(
        "INSERT INTO inventory_items (household_id, item, quantity, source, expiration_date, category, location, frozen_on) "
        "VALUES (1, ?, '1', 'chat', ?, ?, ?, ?)",
        (item, turns, category, location, frozen_on),
    )
    conn.commit()
    conn.close()
    return cur.lastrowid


def _seed(week):
    """Going off across next week (each turns on the day named) plus two
    freezer rows of different ages."""
    d = tools._week_dates(week)
    ids = {
        "spinach": _inv("Baby spinach", turns=d[3]),          # turns Thu → by Wed
        "feta": _inv("Feta", turns=d[4], category="dairy"),
        "peppers": _inv("Red peppers", turns=d[3]),
        "eggs": _inv("Eggs", turns=d[6], category="dairy"),
        "onion": _inv("Half an onion", turns=d[2]),
        "cabbage": _inv("Half a cabbage", turns=d[2]),
        "cilantro": _inv("Cilantro", turns=d[2]),
        "beef": _inv("Ground beef", location="freezer", category="meat/seafood", frozen_days_ago=30),
        "thighs": _inv("Chicken thighs", location="freezer", category="meat/seafood", frozen_days_ago=90),
    }
    return d, ids


def _week_with(week, ids, d):
    """Mon pasta uses spinach+feta (claimed); Tue frittata uses peppers,
    eggs, onion (claimed, eggs only in the note); Wed chili uses the beef;
    Thu 'Lamb stew' CLAIMS the cabbage but nothing in it is cabbage; Sat
    'Chicken thigh traybake' carries the thighs unclaimed; the cilantro is
    claimed nowhere."""
    dinners = {
        0: ("Spinach and Feta Pasta", {"use_up_ids": [ids["spinach"], ids["feta"]]}, ""),
        1: ("Pepper and Onion Frittata", {"use_up_ids": [ids["peppers"], ids["eggs"], ids["onion"]]},
            "Whisk the eggs over softened peppers and onion."),
        2: ("Ground Beef Chili", {"use_up_ids": [ids["beef"]]}, ""),
        3: ("Lamb Stew", {"use_up_ids": [ids["cabbage"]]}, ""),
        4: ("Miso Cod", {}, ""),
        5: ("Chicken Thigh Traybake", {}, ""),
        6: ("Dal", {}, ""),
    }
    out = []
    for i, date in enumerate(d):
        out.append(_slot(date, "breakfast", "Grain oats"))
        out.append(_slot(date, "snack", "Grain apple"))
        out.append(_slot(date, "lunch", f"Grain salad {i}"))
        name, derived, note = dinners[i]
        extra = {"derived_from": derived} if derived else {}
        if note:
            # A dish with a written recipe is what the menu stores; this one
            # carries its eggs in the ingredient list, as a saved recipe would.
            extra["ingredients"] = [{"item": w, "qty": "1", "category": "produce"}
                                    for w in ("Eggs", "Bell pepper", "Onion")]
        out.append(_slot(date, "dinner", name, **extra))
    return out


def _draft(stub_model, week, days, off=None):
    tools.save_week_intake(week, moods=["Comfort food"], use_up_off=off or [])
    seen = stub_model(days)
    plan = agent.generate_weekly_plan(week)
    return plan["weekly_plan_id"], seen["ctx"]


def test_generation_is_handed_the_list_less_what_was_switched_off(household, stub_model):
    """CATCH — the prompt had only 'favor at least one' near-expiring item."""
    week = _monday(1)
    d, ids = _seed(week)
    _, ctx = _draft(stub_model, week, _week_with(week, ids, d), off=[ids["feta"]])
    got = [c["id"] for c in ctx["use_up"]]
    assert ids["feta"] not in got
    assert got[:2] == [ids["onion"], ids["cabbage"]] or got[0] in (ids["onion"], ids["cabbage"], ids["cilantro"])
    # Freezer rows last, oldest first.
    assert got[-2:] == [ids["thighs"], ids["beef"]]
    assert all("use_by" in c for c in ctx["use_up"] if c["kind"] == "going_off")
    assert "PLAN EVERY ONE YOU SENSIBLY CAN" in open(agent.__file__).read()


def test_tags_count_and_couldnt_fit_are_checked_against_the_dishes(household, stub_model):
    """CATCH — claims are verified in code; the count is never the model's."""
    week = _monday(1)
    d, ids = _seed(week)
    plan_id, _ = _draft(stub_model, week, _week_with(week, ids, d))
    menu = tools.get_week_menu(plan_id)
    tags = {day["date"]: (day.get("dinner") or {}).get("uses_tag") for day in menu["days"]}
    assert tags[d[0]] == "Uses the spinach and feta"
    assert tags[d[1]] == "Uses the half onion, red peppers, eggs"  # soonest-to-turn first
    # The freezer is eaten oldest first in the NIGHTS: the 90-day thighs
    # (Sat's traybake, carried unclaimed) trade nights with the 30-day beef
    # (Wed's chili), through swap_dinner_nights.
    assert tags[d[2]] == "Uses the chicken thighs"
    assert tags[d[5]] == "Uses the ground beef"
    assert tags[d[3]] == ""  # claimed the cabbage; a lamb stew doesn't carry it
    assert tags[d[4]] == "" and tags[d[6]] == ""
    summary = menu["use_up"]
    assert summary["total"] == 9 and summary["used"] == 7
    assert summary["line"] == "Uses 7 of the 9 things going off."
    rows = {r["item"]: r for r in summary["couldnt_fit"]}
    assert set(rows) == {"Half a cabbage", "Cilantro"}
    assert rows["Half a cabbage"]["primary"] == "freeze"
    assert rows["Cilantro"]["primary"] == "side"
    # The recipe pass is told to write the dish around what it uses.
    from app.tools import typed_requests
    pasta = next(e for e in _entries(plan_id) if e["date"] == d[0] and e["slot"] == "dinner")
    assert {"spinach", "feta"} <= set(typed_requests.plan_must_use(plan_id, pasta["recipe_id"]))


def test_an_item_claimed_after_it_turns_does_not_count(household, stub_model):
    """CATCH — soonest-to-turn by its date: spinach turning Thursday on a
    Friday dinner is not 'used'."""
    week = _monday(1)
    d = tools._week_dates(week)
    spinach = _inv("Baby spinach", turns=d[3])
    days = []
    for i, date in enumerate(d):
        days.append(_slot(date, "breakfast", "Grain oats"))
        days.append(_slot(date, "lunch", f"Grain salad {i}"))
        days.append(_slot(date, "snack", "Grain apple"))
        name = "Spinach Curry" if i == 4 else ["Lamb Stew", "Miso Cod", "Bibimbap", "Laksa", "", "Gumbo", "Dal"][i]
        extra = {"derived_from": {"use_up_ids": [spinach]}} if i == 4 else {}
        days.append(_slot(date, "dinner", name, **extra))
    plan_id, _ = _draft(stub_model, week, days)
    summary = tools.get_week_menu(plan_id)["use_up"]
    assert summary["used"] == 0 and summary["line"] == "Couldn’t fit the one thing going off."
    record = json.loads(get_conn().execute("SELECT use_up_json FROM weekly_plans WHERE id = ?", (plan_id,)).fetchone()[0])
    assert record["missed"][str(spinach)] == "too_late"


def test_everything_fits_line_and_no_card(household, stub_model):
    """CATCH — "Uses all N things going off." and no card."""
    week = _monday(1)
    d = tools._week_dates(week)
    spinach = _inv("Baby spinach", turns=d[3])
    feta = _inv("Feta", turns=d[4], category="dairy")
    days = []
    for i, date in enumerate(d):
        days.append(_slot(date, "breakfast", "Grain oats"))
        days.append(_slot(date, "lunch", f"Grain salad {i}"))
        days.append(_slot(date, "snack", "Grain apple"))
        name = "Spinach and Feta Pasta" if i == 0 else ["", "Miso Cod", "Bibimbap", "Laksa", "Ramen", "Gumbo", "Dal"][i]
        days.append(_slot(date, "dinner", name, **({"derived_from": {"use_up_ids": [spinach, feta]}} if i == 0 else {})))
    plan_id, _ = _draft(stub_model, week, days)
    summary = tools.get_week_menu(plan_id)["use_up"]
    assert summary["line"] == "Uses all 2 things going off." and summary["couldnt_fit"] == []


def test_freeze_it_side_and_let_go(household, stub_model, signed_in):
    """CATCH — the three taps, through the routes, each with its undo."""
    week = _monday(1)
    d, ids = _seed(week)
    plan_id, _ = _draft(stub_model, week, _week_with(week, ids, d))

    r = signed_in.post(f"/api/week-plan/{plan_id}/use-up/{ids['cabbage']}", json={"action": "freeze"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["toast"] == "Half a cabbage was moved to the freezer."
    row = get_conn().execute("SELECT location, frozen_on FROM inventory_items WHERE id = ?", (ids["cabbage"],)).fetchone()
    assert row["location"] == "freezer" and row["frozen_on"] == household_today().isoformat()
    assert "Half a cabbage" not in [x["item"] for x in tools.get_week_menu(plan_id)["use_up"]["couldnt_fit"]]
    r = signed_in.post(f"/api/week-plan/{plan_id}/use-up/{ids['cabbage']}/undo", json={"undo": body["undo"]})
    assert r.status_code == 200
    assert get_conn().execute("SELECT location FROM inventory_items WHERE id = ?", (ids["cabbage"],)).fetchone()[0] == "fridge"
    assert "Half a cabbage" in [x["item"] for x in tools.get_week_menu(plan_id)["use_up"]["couldnt_fit"]]

    # Add as a side: the earliest cooked dinner on or before its use_by
    # that isn't already using something — Mon/Tue are, so none before
    # Tue's use_by is free, and it lands on the earliest in time (Mon).
    r = signed_in.post(f"/api/week-plan/{plan_id}/use-up/{ids['cilantro']}", json={"action": "side"})
    assert r.status_code == 200, r.text
    side = r.json()
    menu = tools.get_week_menu(plan_id)
    tagged = [day for day in menu["days"] if "cilantro" in ((day.get("dinner") or {}).get("uses_tag") or "")]
    assert len(tagged) == 1 and tagged[0]["date"] <= d[1]
    assert menu["use_up"]["used"] == 8
    entry = next(e for e in _entries(plan_id) if e["id"] == side["entry_id"])
    sides = json.loads(entry["sides_json"])
    assert any(s.get("name") == "Cilantro" and s.get("ingredients") == [] for s in sides)  # nothing to buy
    signed_in.post(f"/api/week-plan/{plan_id}/use-up/{ids['cilantro']}/undo", json={"undo": side["undo"]})
    assert "cilantro" not in json.dumps([s for s in json.loads(
        next(e for e in _entries(plan_id) if e["id"] == side["entry_id"])["sides_json"])]).lower()

    r = signed_in.post(f"/api/week-plan/{plan_id}/use-up/{ids['cilantro']}", json={"action": "let_go"})
    assert r.status_code == 200
    rows = tools.get_week_menu(plan_id)["use_up"]["couldnt_fit"]
    assert "Cilantro" not in [x["item"] for x in rows]
    # Left untouched in the kitchen.
    assert get_conn().execute("SELECT location FROM inventory_items WHERE id = ?", (ids["cilantro"],)).fetchone()[0] == "fridge"


def test_no_use_up_list_means_no_summary(household, stub_model):
    """GUARD — a week with nothing going off says nothing about it."""
    week = _monday(1)
    d = tools._week_dates(week)
    days = []
    for i, date in enumerate(d):
        days.append(_slot(date, "dinner", ["Lamb Stew", "Miso Cod", "Bibimbap", "Laksa", "Ramen", "Gumbo", "Dal"][i]))
    plan_id, _ = _draft(stub_model, week, days)
    menu = tools.get_week_menu(plan_id)
    assert menu["use_up"] is None
    assert all(not (day.get("dinner") or {}).get("uses_tag") for day in menu["days"])


def test_names_and_lines():
    """GUARD — the mockup's shapes."""
    assert use_up.short_name("Baby spinach") == "spinach"
    assert use_up.short_name("Half a bag of rice") == "rice"
    assert use_up.short_name("Half an onion") == "half onion"
    assert use_up.tag_text(["spinach", "feta"]) == "Uses the spinach and feta"
    assert use_up.tag_text(["peppers", "eggs", "half onion"]) == "Uses the peppers, eggs, half onion"
    assert use_up.count_line(6, 8) == "Uses 6 of the 8 things going off."
    assert use_up.count_line(8, 8) == "Uses all 8 things going off."


def test_a_disliked_item_is_never_handed_to_the_generator(household, stub_model):
    """CATCH — household rules win. Measured on a real draft (2026-10-26):
    a disliked cabbage in the list came back as a cabbage slaw. It is held
    back from the prompt, never tagged, and offered on the card instead."""
    tools.add_food_dislikes(["cabbage"])
    week = _monday(1)
    d, ids = _seed(week)
    days = _week_with(week, ids, d)
    # The model plans it anyway, and claims it.
    for e in days:
        if e["date"] == d[0] and e["slot"] == "lunch":
            e["meal_name"] = "Cabbage Slaw Bowl"
            e["derived_from"] = {"use_up_ids": [ids["cabbage"]]}
    plan_id, ctx = _draft(stub_model, week, days)
    assert ids["cabbage"] not in [c["id"] for c in ctx["use_up"]]
    menu = tools.get_week_menu(plan_id)
    assert all("cabbage" not in ((day.get("lunch") or {}).get("uses_tag") or "") for day in menu["days"])
    # It can never be planned, so it is not offered, shown or counted at
    # all (verify finding 5, 2026-10-27) — not even on the card.
    assert "Half a cabbage" not in [r["item"] for r in menu["use_up"]["couldnt_fit"]]
    assert menu["use_up"]["total"] == 8
    assert ids["cabbage"] not in [c["id"] for c in use_up.candidates(week, 7)]
