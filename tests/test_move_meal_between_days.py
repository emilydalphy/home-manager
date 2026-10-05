"""
Move a meal to another day (Emily, 2026-09-28 — Option A of the "swap
meals between days" mockups, decisions answered in chat that day).

    Tap Move on a meal → "Move the stew to which day?" → pick a day → the
    two meals trade places; a dinner's leftovers night moves with it,
    keeping the same gap. Dinners and lunches. Drafts and approved weeks.

tools/meal_move.py places the rows; weekly_plan._redate_plan_rows (the
nights swap's own writer) writes them. Every test here fails on main,
where neither meal_move nor the routes exist.
"""
from __future__ import annotations

import datetime
import json

import pytest

from app import tools
from app.db import get_conn
from app.tools import defrost, draft_flags, meal_move, prep_sessions


def _monday() -> datetime.date:
    from conftest import household_today
    today = household_today()
    return today - datetime.timedelta(days=today.weekday())


# Next week, so no day is "already gone" and a thaw date can move freely.
WEEK = (_monday() + datetime.timedelta(days=7)).isoformat()
DAYS = tools._week_dates(WEEK)
MON, TUE, WED, THU, FRI, SAT, SUN = DAYS


def _plan() -> int:
    return tools.create_weekly_plan(WEEK)["weekly_plan_id"]


def _recipe(name, minutes=30, meat=False):
    ingredients = [{"item": f"{name} onion", "qty": "1", "category": "produce"}]
    if meat:
        ingredients.append({"item": "Chicken Thighs", "qty": "1 lb", "category": "meat/seafood"})
    tools.add_recipe(name, ingredients=ingredients, prep_time_minutes=10,
                     cook_time_minutes=minutes - 10, default_servings=4)


def _meal(day: str, slot: str = "dinner") -> dict | None:
    conn = get_conn()
    row = conn.execute(
        "SELECT mpe.id, mpe.date, mpe.slot_state, mpe.cooked_status, mpe.derived_from_json, "
        "COALESCE(r.name, mpe.freeform_meal) AS meal "
        "FROM meal_plan_entries mpe LEFT JOIN recipes r ON r.id = mpe.recipe_id "
        "WHERE mpe.household_id = ? AND mpe.date = ? AND mpe.slot = ? ORDER BY mpe.id",
        (tools.household_id(), day, slot),
    ).fetchone()
    conn.close()
    if row is None:
        return None
    out = dict(row)
    out["derived"] = json.loads(out.pop("derived_from_json") or "{}")
    return out


def _grocery_snapshot() -> list[tuple]:
    conn = get_conn()
    rows = conn.execute(
        "SELECT item, quantity, status FROM grocery_items WHERE household_id = ? ORDER BY item, quantity",
        (tools.household_id(),),
    ).fetchall()
    links = conn.execute(
        "SELECT meal_plan_entry_id, grocery_item_id, quantity FROM meal_plan_grocery_links "
        "WHERE household_id = ? ORDER BY id", (tools.household_id(),),
    ).fetchall()
    conn.close()
    return [tuple(r) for r in rows] + [tuple(r) for r in links]


def _stew_week(approve: bool = False) -> dict:
    """The mockup's week: stew Monday with its leftovers Tuesday, then
    gnocchi, tacos, salmon bowls, chili, and a Sunday roast."""
    for name in ("Chicken Stew", "Sheet-Pan Gnocchi", "Black Bean Tacos", "Salmon Rice Bowls",
                 "Turkey Chili", "Sunday Roast"):
        _recipe(name, meat=(name == "Chicken Stew"))
    plan = _plan()
    ids = {
        "stew": tools.plan_meal(MON, "Chicken Stew", slot="dinner", weekly_plan_id=plan)["entry_id"],
        "left": tools.plan_meal(TUE, "Chicken Stew", slot="dinner", weekly_plan_id=plan,
                                derived_from={"links_to": f"{MON}:dinner"})["entry_id"],
        "gnocchi": tools.plan_meal(WED, "Sheet-Pan Gnocchi", slot="dinner", weekly_plan_id=plan)["entry_id"],
        "tacos": tools.plan_meal(THU, "Black Bean Tacos", slot="dinner", weekly_plan_id=plan)["entry_id"],
        "salmon": tools.plan_meal(FRI, "Salmon Rice Bowls", slot="dinner", weekly_plan_id=plan)["entry_id"],
        "chili": tools.plan_meal(SAT, "Turkey Chili", slot="dinner", weekly_plan_id=plan)["entry_id"],
        "roast": tools.plan_meal(SUN, "Sunday Roast", slot="dinner", weekly_plan_id=plan)["entry_id"],
    }
    tools.repair_leftover_chains(plan)
    assert tools.plan_leftover_chains(plan)["leftovers"], "the stew's chain should be confirmed first"
    if approve:
        tools.approve_weekly_plan(plan)
    return {"plan": plan, **ids}


# ---------------------------------------------------------------- the move

def test_a_dinner_and_its_leftovers_night_move_together_keeping_the_gap():
    """The mockup, exactly: stew Monday + leftovers Tuesday moved to
    Thursday → stew Thursday, leftovers Friday; tacos to Monday, salmon
    bowls to Tuesday. Four nights change; every row keeps its id."""
    w = _stew_week()

    out = meal_move.move_meal(w["plan"], w["stew"], THU)

    assert out["status"] == "moved"
    assert out["said"] == "Chicken Stew was moved to Thursday, leftovers to Friday"
    assert _meal(THU)["id"] == w["stew"]
    assert _meal(FRI)["id"] == w["left"]
    assert _meal(MON)["id"] == w["tacos"]
    assert _meal(TUE)["id"] == w["salmon"]
    assert _meal(WED)["id"] == w["gnocchi"]  # untouched
    chains = tools.plan_leftover_chains(w["plan"])
    assert chains["leftovers"][w["left"]]["source"]["entry_id"] == w["stew"]
    assert chains["leftovers"][w["left"]]["source"]["date"] == THU
    assert _meal(THU)["derived"]["make_double_for"] == [f"{FRI}:dinner"]
    assert sorted(d["date"] for d in out["days"]) == [MON, TUE, THU, FRI]


def test_a_plain_dinner_trades_with_the_day_picked_and_the_toast_names_both():
    w = _stew_week()

    out = meal_move.move_meal(w["plan"], w["gnocchi"], SAT)

    assert out["status"] == "moved"
    assert out["said"] == "Sheet-Pan Gnocchi was moved to Saturday, Turkey Chili to Wednesday"
    assert _meal(SAT)["id"] == w["gnocchi"] and _meal(WED)["id"] == w["chili"]


def test_the_grocery_list_stays_the_same_on_an_approved_week():
    w = _stew_week(approve=True)
    before = _grocery_snapshot()
    assert before

    assert meal_move.move_meal(w["plan"], w["stew"], THU)["status"] == "moved"

    assert _grocery_snapshot() == before


def test_undo_puts_every_row_back_and_the_chain_with_them():
    w = _stew_week()
    out = meal_move.move_meal(w["plan"], w["stew"], THU)

    back = meal_move.undo_meal_move(w["plan"], out["move_id"])

    assert back["status"] == "restored"
    assert back["said"] == "Chicken Stew was moved back to Monday"
    for key, day in (("stew", MON), ("left", TUE), ("tacos", THU), ("salmon", FRI)):
        assert _meal(day)["id"] == w[key], key
        assert "moved_from" not in _meal(day)["derived"]
    chains = tools.plan_leftover_chains(w["plan"])
    assert chains["leftovers"][w["left"]]["source"]["date"] == MON


def test_undo_refuses_once_a_moved_meal_has_moved_again():
    w = _stew_week()
    out = meal_move.move_meal(w["plan"], w["gnocchi"], SAT)
    meal_move.move_meal(w["plan"], w["gnocchi"], SUN)

    with pytest.raises(ValueError):
        meal_move.undo_meal_move(w["plan"], out["move_id"])
    assert _meal(SUN)["id"] == w["gnocchi"]


def test_lunches_move_too():
    for name in ("Lentil Soup", "Chicken Pancake"):
        _recipe(name, 20)
    plan = _plan()
    soup = tools.plan_meal(MON, "Lentil Soup", slot="lunch", weekly_plan_id=plan)["entry_id"]
    pancake = tools.plan_meal(WED, "Chicken Pancake", slot="lunch", weekly_plan_id=plan)["entry_id"]

    tools.plan_meal(FRI, "Lentil Soup", slot="lunch", weekly_plan_id=plan)
    sheet = meal_move.move_options(plan, soup)
    assert sheet["sub"] == "The two lunches trade places."
    days = {d["date"]: d for d in sheet["days"]}
    assert days[FRI]["ok"] is False and days[FRI]["reason"] == "Same dish"
    # Tuesday holds no lunch row. Card 7 (2026-10-05) INVERTS this: that
    # is a valid destination when somebody is home for it, and refusing it
    # is the bug the card was raised for ("only giving certain days").
    # This file's own claim — that lunches move, and that the same dish is
    # still refused — is untouched above.
    assert days[TUE]["ok"] is True and days[TUE]["meal"] == "Nothing planned"

    out = meal_move.move_meal(plan, soup, WED)

    assert out["status"] == "moved"
    assert _meal(WED, "lunch")["id"] == soup and _meal(MON, "lunch")["id"] == pancake


def test_a_dinner_whose_leftovers_are_the_next_days_lunch_takes_that_lunch_with_it():
    """Stew Monday feeds Tuesday's lunch. Moved to Thursday: Friday's
    lunch is the stew's now, and Friday's own lunch comes back to Tuesday."""
    for name in ("Chicken Stew", "Black Bean Tacos", "Lentil Soup", "Chicken Pancake"):
        _recipe(name)
    plan = _plan()
    stew = tools.plan_meal(MON, "Chicken Stew", slot="dinner", weekly_plan_id=plan)["entry_id"]
    left = tools.plan_meal(TUE, "Chicken Stew", slot="lunch", weekly_plan_id=plan,
                           derived_from={"links_to": f"{MON}:dinner"})["entry_id"]
    tacos = tools.plan_meal(THU, "Black Bean Tacos", slot="dinner", weekly_plan_id=plan)["entry_id"]
    fri_lunch = tools.plan_meal(FRI, "Chicken Pancake", slot="lunch", weekly_plan_id=plan)["entry_id"]
    tools.repair_leftover_chains(plan)
    assert tools.plan_leftover_chains(plan)["leftovers"]

    out = meal_move.move_meal(plan, stew, THU)

    assert out["status"] == "moved"
    assert _meal(THU)["id"] == stew and _meal(FRI, "lunch")["id"] == left
    assert _meal(MON)["id"] == tacos and _meal(TUE, "lunch")["id"] == fri_lunch
    chains = tools.plan_leftover_chains(plan)
    assert chains["leftovers"][left]["source"]["date"] == THU
    assert _meal(THU)["derived"]["make_double_for"] == [f"{FRI}:lunch"]


def test_a_breakfast_does_not_move():
    _recipe("Overnight Oats", 5)
    plan = _plan()
    oats = tools.plan_meal(MON, "Overnight Oats", slot="breakfast", weekly_plan_id=plan)["entry_id"]
    tools.plan_meal(TUE, "Overnight Oats", slot="breakfast", weekly_plan_id=plan)
    with pytest.raises(ValueError):
        meal_move.move_meal(plan, oats, TUE)
    with pytest.raises(ValueError):
        meal_move.move_options(plan, oats)


# ---------------------------------------------------------------- the picker

def _options(plan, entry_id) -> dict:
    return {d["date"]: d for d in meal_move.move_options(plan, entry_id)["days"]}


def test_the_picker_lists_every_other_day_and_dims_the_impossible_ones_with_a_reason():
    w = _stew_week()
    conn = get_conn()
    conn.execute("DELETE FROM meal_plan_entries WHERE id = ?", (w["salmon"],))
    conn.commit()
    conn.close()
    tools.plan_slot_empty(w["plan"], FRI, "dinner", "Out — nothing to cook")

    sheet = meal_move.move_options(w["plan"], w["stew"])
    days = {d["date"]: d for d in sheet["days"]}

    assert sheet["title"] == "Move the Chicken Stew to which day?"
    assert "Tuesday’s leftovers move with the Chicken Stew" in sheet["sub"]
    assert MON not in days and len(days) == 6
    # Its own leftovers night: shown, dimmed, said.
    assert days[TUE]["ok"] is False and days[TUE]["reason"] == ""
    assert days[TUE]["meal"] == "Chicken Stew leftovers"
    # Sunday: the leftovers would land on next Monday.
    assert days[SUN]["ok"] is False and "after this week" in days[SUN]["reason"]
    # Thursday: its leftovers would land on Friday, where nobody's home.
    assert days[THU]["ok"] is False
    assert days[THU]["reason"] == "Its leftovers would land on Friday, and nobody’s home"
    # Friday itself: nobody's home. The CLAIM is unchanged — Friday is
    # dimmed and says why — but card 7 (2026-10-05) moved the reason out of
    # the day's LABEL and into the line under it, for every blocked day
    # alike ("each shows its reason as a visible line under the day"), and
    # named the slot in it. The tripwire fired on where the sentence sits,
    # not on whether it is said.
    assert days[FRI]["ok"] is False and days[FRI]["meal"] == "Not planned"
    assert days[FRI]["reason"] == "Nobody’s home for dinner"
    assert days[WED]["ok"] is True and days[WED]["meal"] == "Sheet-Pan Gnocchi"


def test_a_cooked_meal_day_is_dimmed_and_a_cooked_meal_does_not_move():
    w = _stew_week()
    conn = get_conn()
    conn.execute("UPDATE meal_plan_entries SET cooked_status = 'done' WHERE id = ?", (w["chili"],))
    conn.commit()
    conn.close()

    days = _options(w["plan"], w["gnocchi"])
    assert days[SAT]["ok"] is False and days[SAT]["reason"] == "Already cooked"
    assert meal_move.move_meal(w["plan"], w["chili"], WED)["status"] == "refused"


def test_a_leftovers_night_itself_is_not_moved_on_its_own():
    w = _stew_week()
    out = meal_move.move_meal(w["plan"], w["left"], WED)
    assert out["status"] == "refused"
    assert _meal(TUE)["id"] == w["left"]


def test_no_dish_lands_on_a_third_meal_in_a_row():
    """Tuesday's lunch and dinner are both chili; moving Friday's chili to
    Monday dinner would make Monday dinner → Tuesday lunch → Tuesday dinner
    three in a row. Dimmed, said, and refused if tapped anyway."""
    for name in ("Black Bean Tacos", "Bean Chili"):
        _recipe(name)
    plan = _plan()
    tools.plan_meal(MON, "Black Bean Tacos", slot="dinner", weekly_plan_id=plan)
    tools.plan_meal(TUE, "Bean Chili", slot="lunch", weekly_plan_id=plan)
    tools.plan_meal(TUE, "Bean Chili", slot="dinner", weekly_plan_id=plan)
    fri = tools.plan_meal(FRI, "Bean Chili", slot="dinner", weekly_plan_id=plan)["entry_id"]

    days = _options(plan, fri)
    assert days[MON]["ok"] is False and days[MON]["reason"] == "That makes three Bean Chili meals in a row"
    out = meal_move.move_meal(plan, fri, MON)
    assert out["status"] == "refused"
    assert _meal(FRI)["id"] == fri


def test_a_displaced_chain_is_not_left_with_leftovers_too_old_to_eat():
    """Thursday's chili feeds Saturday. Moving Monday's stew (+ Tuesday's
    leftovers) to Thursday would send the chili to Monday with its
    leftovers still on Saturday — five days on. Refused, with the reason."""
    w = _stew_week()
    conn = get_conn()
    conn.execute("DELETE FROM meal_plan_entries WHERE id = ?", (w["chili"],))
    conn.commit()
    conn.close()
    tools.plan_meal(SAT, "Black Bean Tacos", slot="dinner", weekly_plan_id=w["plan"],
                    derived_from={"links_to": f"{THU}:dinner"})
    tools.repair_leftover_chains(w["plan"])
    assert len(tools.plan_leftover_chains(w["plan"])["leftovers"]) == 2

    days = _options(w["plan"], w["stew"])
    assert days[THU]["ok"] is False and "days old" in days[THU]["reason"]


def test_a_long_dish_may_go_to_a_short_on_time_day_and_the_row_says_so():
    tools.edit_preference("weeknight_max_minutes", 20)
    _recipe("Turkey Chili", 40)
    _recipe("Quick Tacos", 15)
    plan = _plan()
    chili = tools.plan_meal(SAT, "Turkey Chili", slot="dinner", weekly_plan_id=plan)["entry_id"]
    tools.plan_meal(MON, "Quick Tacos", slot="dinner", weekly_plan_id=plan)

    days = _options(plan, chili)
    assert days[MON]["ok"] is True
    assert days[MON]["note"] == "40 min on a short-on-time day"
    # And the other way round: the chili's own row, seen from Monday's tacos.
    tacos = _meal(MON)["id"]
    assert _options(plan, tacos)[SAT]["note"] == "40 min on short-on-time Monday"
    assert meal_move.move_meal(plan, chili, MON)["status"] == "moved"


# ---------------------------------------------------------------- what rides along

def test_a_thaw_that_now_starts_earlier_is_said_and_its_reminder_moves():
    _recipe("Chicken Skewers", meat=True)
    _recipe("Bean Chili")
    plan = _plan()
    skewers = tools.plan_meal(SAT, "Chicken Skewers", slot="dinner", weekly_plan_id=plan)["entry_id"]
    tools.plan_meal(WED, "Bean Chili", slot="dinner", weekly_plan_id=plan)
    created = defrost.confirm_frozen_items(plan, ["Chicken Thighs"])["created"]
    task_id = created[0]["prep_task_id"]
    conn = get_conn()
    old = conn.execute("SELECT task_date FROM prep_tasks WHERE id = ?", (task_id,)).fetchone()["task_date"]
    conn.close()

    out = meal_move.move_meal(plan, skewers, WED)

    conn = get_conn()
    new = conn.execute("SELECT task_date, description FROM prep_tasks WHERE id = ?", (task_id,)).fetchone()
    conn.close()
    assert (datetime.date.fromisoformat(old) - datetime.date.fromisoformat(new["task_date"])).days == 3
    assert "Wednesday" in new["description"]
    weekday = datetime.date.fromisoformat(new["task_date"]).strftime("%A")
    assert out["thaw_notes"] == [f"Move the Chicken Thighs to the fridge on {weekday}."]


def test_a_prep_cut_that_would_fall_after_its_meal_moves_with_it():
    _recipe("Lasagna", 60)
    _recipe("Quick Tacos", 15)
    plan = _plan()
    lasagna = tools.plan_meal(SAT, "Lasagna", slot="dinner", weekly_plan_id=plan)["entry_id"]
    tools.plan_meal(TUE, "Quick Tacos", slot="dinner", weekly_plan_id=plan)
    prep_sessions.add_prep_cut(plan, FRI, "Get Lasagna ready for Saturday", entry_ids=[lasagna])

    meal_move.move_meal(plan, lasagna, TUE)

    conn = get_conn()
    row = conn.execute(
        "SELECT task_date, description FROM prep_tasks WHERE meal_plan_entry_id = ? AND task_type = 'prep_cut'",
        (lasagna,),
    ).fetchone()
    conn.close()
    assert row["task_date"] == MON
    assert row["description"] == "Get Lasagna ready for Tuesday"

    # Undo puts the cut back on its own day, not just its sentence.
    token = _meal(TUE)["derived"]["moved_from"]
    meal_move.undo_meal_move(plan, token["move"])
    conn = get_conn()
    row = conn.execute(
        "SELECT task_date, description FROM prep_tasks WHERE meal_plan_entry_id = ? AND task_type = 'prep_cut'",
        (lasagna,),
    ).fetchone()
    conn.close()
    assert row["task_date"] == FRI
    assert row["description"] == "Get Lasagna ready for Saturday"


def test_a_moved_dish_she_asked_for_gets_its_short_on_time_line_on_the_draft():
    """The draft's snag lines are re-said after a move: a dish she asked
    for, moved onto a short-on-time night, gets the line there."""
    tools.edit_preference("weeknight_max_minutes", 20)
    _recipe("Lasagna", 60)
    _recipe("Quick Tacos", 15)
    plan = _plan()
    lasagna = tools.plan_meal(SAT, "Lasagna", slot="dinner", weekly_plan_id=plan,
                              derived_from={"freeform": "I want Lasagna this week"})["entry_id"]
    tools.plan_meal(WED, "Quick Tacos", slot="dinner", weekly_plan_id=plan)
    assert draft_flags.plan_flags(plan) == []

    meal_move.move_meal(plan, lasagna, WED)

    flags = draft_flags.plan_flags(plan)
    assert [f["text"] for f in flags] == ["Lasagna takes 60 minutes, and Wednesday is short on time."]

    # Moved back to Saturday, the line goes.
    meal_move.move_meal(plan, lasagna, SAT)
    assert draft_flags.plan_flags(plan) == []


# ---------------------------------------------------------------- the routes

def test_the_routes_offer_move_and_undo(signed_in):
    w = _stew_week()
    opts = signed_in.post(f"/api/week/{WEEK}/move-options", json={"entry_id": w["stew"]})
    assert opts.status_code == 200
    assert len(opts.json()["days"]) == 6

    res = signed_in.post(f"/api/week/{WEEK}/move-meal", json={"entry_id": w["stew"], "to_date": THU})
    assert res.status_code == 200 and res.json()["status"] == "moved"
    back = signed_in.post(f"/api/week/{WEEK}/move-meal-undo", json={"move_id": res.json()["move_id"]})
    assert back.status_code == 200 and back.json()["status"] == "restored"
    assert _meal(MON)["id"] == w["stew"]

    bad = signed_in.post(f"/api/week/{WEEK}/move-meal", json={"entry_id": w["stew"], "to_date": "2001-01-01"})
    assert bad.status_code == 400


# ---------------------------------------------------------------- the screen

from test_plan_cards_2026_09_18 import (  # noqa: E402 — the Plan screen's own harness
    SHELL_JS, _MON as _SMON, _TUE as _STUE, _day, _entry, _extract, _needs_node, _prelude, _run, _week,
)


def _move_prelude() -> str:
    start = SHELL_JS.index("var MOVE_SLOTS = ")
    return (_prelude() + SHELL_JS[start:SHELL_JS.index("\n", start)] + "\n"
            + _extract("wkCanMove", SHELL_JS) + "\n")


@_needs_node
def test_move_sits_on_lunch_and_dinner_rows_of_the_schedule_and_the_approved_root():
    days = _week()
    html = _run(_move_prelude() + f"weekState.days = {json.dumps(days)};\n"
                "console.log(JSON.stringify([wkDayCardHtml(weekState.days[0], 0, { done: false }),"
                " wkDayCardHtml(weekState.days[0], 0, { done: true }),"
                " wkDayCardHtml(weekState.days[1], 1, { done: false })]));")
    schedule, root, tuesday = html
    for card in (schedule, root):
        assert 'data-wk-move="lunch"' in card and 'data-wk-move="dinner"' in card
        assert 'data-wk-move="breakfast"' not in card, "breakfasts don't move"
        assert 'aria-label="Move — Lemon chicken &amp; orzo"' in card
    # The root's row: Done, Swap, Move.
    assert root.index('data-wk-done="dinner"') < root.index('data-wk-swap-sheet="dinner"') < root.index('data-wk-move="dinner"')
    # Tuesday's lunch is leftovers: it moves with its cook, not on its own.
    assert 'data-wk-move="lunch"' not in tuesday and 'data-wk-move="dinner"' in tuesday


@_needs_node
def test_no_move_on_a_past_day_or_a_cooked_meal():
    past = _day(_SMON, past=True, dinner=_entry("Lemon chicken & orzo", entry_id=13))
    cooked = _day(_STUE, dinner=_entry("Black bean tacos", entry_id=23, cooked=True))
    html = _run(_move_prelude() + f"weekState.days = {json.dumps([past, cooked])};\n"
                "console.log(JSON.stringify([wkDayCardHtml(weekState.days[0], 0, { done: true }),"
                " wkDayCardHtml(weekState.days[1], 1, { done: true })]));")
    assert all("data-wk-move" not in card for card in html)


@_needs_node
def test_the_meals_list_offers_move_on_a_dish_that_is_one_cook_ahead():
    days = _week()
    html = _run(_move_prelude() + f"weekState.days = {json.dumps(days)};\n"
                "console.log(JSON.stringify(wkMenuHtml(weekState.days)));")
    # Lemon chicken (one cook) and tacos: Move. Oats are a breakfast.
    assert html.count('data-wk-move="dinner"') == 2
    assert 'data-wk-move="breakfast"' not in html
    assert 'aria-label="Move — Chickpea salad jars"' in html


def test_the_swap_sheet_no_longer_carries_the_buried_move_line():
    """One way in (Emily, 2026-09-28): the quiet "Move the X to another
    day" at the foot of the Swap sheet is gone; so is its dinner-only
    mover on the old swap-nights route."""
    body = _extract("swapSheetBodyHtml", SHELL_JS)
    assert "to another day" not in body and "wk-swap-move" not in body
    assert "function runMoveNight(" not in SHELL_JS and "function swapMoveOptions(" not in SHELL_JS
    assert "/move-options" in SHELL_JS and "/move-meal'" in SHELL_JS and "/move-meal-undo" in SHELL_JS


def test_a_freezer_portion_is_not_judged_by_the_fridge_days_and_its_name_follows_the_cook():
    """Chili Monday with a portion frozen for Friday: the freezer night is
    ordered after the cook but has no three-day limit; when the chili moves
    to Tuesday the freezer night says Tuesday."""
    from app.tools import leftovers
    for name in ("Turkey Chili", "Sunday Roast", "Black Bean Tacos"):
        _recipe(name)
    plan = _plan()
    chili = tools.plan_meal(MON, "Turkey Chili", slot="dinner", weekly_plan_id=plan)["entry_id"]
    tools.plan_meal(TUE, "Black Bean Tacos", slot="dinner", weekly_plan_id=plan)
    frozen = tools.plan_meal(FRI, leftovers.freezer_night_name("Turkey Chili", MON), slot="dinner",
                             weekly_plan_id=plan,
                             derived_from={leftovers.FROM_FREEZER_KEY: {"cook": f"entry_id:{chili}",
                                                                         "dish": "Turkey Chili"}})["entry_id"]
    roast = tools.plan_meal(SUN, "Sunday Roast", slot="dinner", weekly_plan_id=plan)["entry_id"]

    assert _options(plan, roast)[FRI]["ok"] is True, "a frozen portion keeps; no fridge-days limit"
    assert _options(plan, roast)[MON]["ok"] is False, "nor can the portion come before its cook"
    assert _options(plan, chili)[SAT]["ok"] is False, "the cook can't go after its frozen portion"

    meal_move.move_meal(plan, chili, TUE)
    assert _meal(FRI)["id"] == frozen
    assert _meal(FRI)["meal"] == leftovers.freezer_night_name("Turkey Chili", TUE)
