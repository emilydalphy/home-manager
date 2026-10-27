"""
The independent verify of the inventory planning stack (2026-10-27,
VERIFY-inventory-planning-stack.md) — one test per finding fixed on
inventory/use-it-all-draft. Every test is a CATCH (red at b0f83bc) except the
night-tag one, which is the GUARD on the freezer-order pass.
"""
from __future__ import annotations

import datetime
import json

import pytest

from conftest import household_today

from app import agent, tools
from app.db import get_conn
from app.tools import swap_in_place as sip
from app.tools import typed_requests
from app.tools import use_up

from test_freezer_portion_planned import (  # noqa: F401  (fixtures used by name)
    _entries, _monday, _slot, household, stub_model,
)
from test_use_it_all_draft import _draft, _inv, _seed, _week_with


def _dinner(plan_id, date):
    return next(e for e in _entries(plan_id) if e["date"] == date and e["slot"] == "dinner")


# 1 -------------------------------------------------------------------

def test_a_swap_takes_the_tag_and_the_must_use_with_the_old_dish(household, stub_model):
    """Finding 1: Beef Tacos swapped in for the spinach pasta kept "Uses
    the spinach and feta", the count, and must_use for the recipe pass."""
    week = _monday(1)
    d, ids = _seed(week)
    plan_id, _ = _draft(stub_model, week, _week_with(week, ids, d))
    before = tools.get_week_menu(plan_id)["use_up"]
    pasta = _dinner(plan_id, d[0])
    sip.apply_pick(plan_id, sip._entry(plan_id, pasta["id"]),
                   {"meal_name": "Beef Tacos", "food_groups": [], "reason": "Something else."},
                   correct_title=False)
    menu = tools.get_week_menu(plan_id)
    monday = next(day for day in menu["days"] if day["date"] == d[0])
    assert monday["dinner"]["uses_tag"] == ""
    assert menu["use_up"]["used"] == before["used"] - 2
    back = [r["item"] for r in menu["use_up"]["couldnt_fit"]]
    assert "Baby spinach" in back and "Feta" in back
    tacos = _dinner(plan_id, d[0])
    must = typed_requests.plan_must_use(plan_id, tacos["recipe_id"])
    assert "spinach" not in must and "feta" not in must


def test_a_stale_stamp_from_any_replace_path_is_ignored(household, stub_model):
    """Finding 1, any door: a path that copies derived_from onto a
    different dish (a re-pick, a chat replace) cannot carry the tag."""
    week = _monday(1)
    d, ids = _seed(week)
    plan_id, _ = _draft(stub_model, week, _week_with(week, ids, d))
    pasta = _dinner(plan_id, d[0])
    conn = get_conn()
    conn.execute("UPDATE meal_plan_entries SET recipe_id = NULL, freeform_meal = 'Fish and chips' WHERE id = ?",
                 (pasta["id"],))
    conn.commit()
    conn.close()
    monday = next(day for day in tools.get_week_menu(plan_id)["days"] if day["date"] == d[0])
    assert not monday["dinner"].get("uses_tag")


# 2 -------------------------------------------------------------------

def test_planning_next_week_never_offers_food_that_turns_before_it(household, stub_model):
    """Finding 2: candidates kept anything turning between today and the
    period's end; the generator's inventory lists did too."""
    week = _monday(1)
    first = datetime.date.fromisoformat(week)
    lead = (first - household_today()).days
    early = _inv("Baby spinach", turns=(household_today() + datetime.timedelta(days=lead)).isoformat())  # turns ON Monday
    good = _inv("Feta", turns=(first + datetime.timedelta(days=2)).isoformat(), category="dairy")
    assert [c["id"] for c in use_up.candidates(week, 7)] == [good]
    tools.save_week_intake(week, moods=["Comfort food"])
    days = [_slot(dd, "dinner", n) for dd, n in zip(tools._week_dates(week),
            ["Lamb Stew", "Miso Cod", "Bibimbap", "Laksa", "Ramen", "Gumbo", "Dal"])]
    seen = stub_model(days)
    agent.generate_weekly_plan(week)
    ctx = seen["ctx"]
    for key in ("current_inventory", "near_expiring_inventory", "fresh_perishable_inventory"):
        assert early not in [r.get("id") for r in ctx[key]], key


def test_a_dish_that_names_food_past_its_date_by_then_is_said(household, stub_model):
    """Finding 2, telling is not preventing: when a stored dish names a
    kitchen row that turns on or before its day, the draft says so."""
    week = _monday(1)
    d = tools._week_dates(week)
    spinach = _inv("Baby spinach", turns=d[1])
    days = []
    for i, date in enumerate(d):
        name = "Spinach Scramble" if i == 3 else ["Lamb Stew", "Miso Cod", "Bibimbap", "", "Ramen", "Gumbo", "Dal"][i]
        extra = {"derived_from": {"inventory": ["Baby spinach"]}} if i == 3 else {}
        days.append(_slot(date, "dinner", name, **extra))
    plan_id, _ = _draft(stub_model, week, days)
    summary = tools.get_week_menu(plan_id)["use_up"]
    weekday = datetime.date.fromisoformat(d[3]).strftime("%A")
    assert summary["past_date"] == [f"The spinach turns before {weekday}’s dinner."]
    assert spinach  # the row itself is untouched


# 3 -------------------------------------------------------------------

def test_a_switched_off_item_is_not_in_any_list_the_generator_sees(household, stub_model):
    """Finding 3: Zucchini switched off still reached current_inventory
    and near_expiring_inventory, and became Zucchini Tacos."""
    week = _monday(1)
    d, ids = _seed(week)
    _, ctx = _draft(stub_model, week, _week_with(week, ids, d), off=[ids["feta"]])
    for key in ("use_up", "current_inventory", "near_expiring_inventory", "fresh_perishable_inventory"):
        assert ids["feta"] not in [r.get("id") for r in ctx[key]], key


# 4 -------------------------------------------------------------------

def test_freezer_food_is_eaten_oldest_first_in_the_nights(household, stub_model):
    """Finding 4: the 90-day thighs landed after the 30-day beef."""
    week = _monday(1)
    d = tools._week_dates(week)
    beef = _inv("Ground beef", location="freezer", category="meat/seafood", frozen_days_ago=30)
    thighs = _inv("Chicken thighs", location="freezer", category="meat/seafood", frozen_days_ago=90)
    names = ["Ground Beef Chili", "Miso Cod", "Bibimbap", "Chicken Thigh Traybake", "Ramen", "Gumbo", "Dal"]
    days = [_slot(dd, "dinner", n, **({"derived_from": {"use_up_ids": [beef]}} if i == 0 else
                                       {"derived_from": {"use_up_ids": [thighs]}} if i == 3 else {}))
            for i, (dd, n) in enumerate(zip(d, names))]
    plan_id, _ = _draft(stub_model, week, days)
    assert _dinner(plan_id, d[0])["meal"] == "Chicken Thigh Traybake"
    assert _dinner(plan_id, d[3])["meal"] == "Ground Beef Chili"
    menu = tools.get_week_menu(plan_id)
    tags = {day["date"]: day["dinner"]["uses_tag"] for day in menu["days"]}
    assert tags[d[0]] == "Uses the chicken thighs" and tags[d[3]] == "Uses the ground beef"


def test_the_freezer_order_never_moves_a_night_the_household_answered(household, stub_model):
    """Finding 4, safely: a tagged night keeps its dinner."""
    week = _monday(1)
    d = tools._week_dates(week)
    beef = _inv("Ground beef", location="freezer", category="meat/seafood", frozen_days_ago=30)
    thighs = _inv("Chicken thighs", location="freezer", category="meat/seafood", frozen_days_ago=90)
    names = ["Ground Beef Chili", "Miso Cod", "Bibimbap", "Chicken Thigh Traybake", "Ramen", "Gumbo", "Dal"]
    days = [_slot(dd, "dinner", n, **({"derived_from": {"use_up_ids": [beef]}} if i == 0 else
                                       {"derived_from": {"use_up_ids": [thighs]}} if i == 3 else {}))
            for i, (dd, n) in enumerate(zip(d, names))]
    tools.save_week_intake(week, night_tags={d[0]: ["unrushed"]})
    plan_id, _ = _draft(stub_model, week, days)
    assert _dinner(plan_id, d[0])["meal"] == "Ground Beef Chili"


# 5 -------------------------------------------------------------------

def test_allergen_and_disliked_food_is_never_a_candidate(household):
    """Finding 5: shrimp (shellfish allergy) and mushrooms (dislike) were
    switched-on chips and inflated the count; 'Mushroom caps' and
    'Portobellos' slipped past the dislike."""
    tools.add_food_dislikes(["mushrooms"])
    tools.add_member("Reid")
    tools.set_member_dietary_restrictions("Reid", ["shellfish allergy"])
    week = _monday(1)
    d = tools._week_dates(week)
    keep = _inv("Baby spinach", turns=d[3])
    for name in ("Shrimp", "Mushrooms", "Mushroom caps", "Portobellos", "Prawns"):
        _inv(name, turns=d[3])
    assert [c["id"] for c in use_up.candidates(week, 7)] == [keep]


# 6 -------------------------------------------------------------------

def test_freeze_it_is_not_a_second_apricot():
    """Finding 6 (DESIGN_SYSTEM rule 5): Approve is the draft's one apricot."""
    import re
    css = open("static/shell.css").read()
    block = css[css.index("/* ---------- Use it all"):]
    freeze = re.search(r"\.wk-unfit-act\.is-freeze[^{]*\{([^}]*)\}", block).group(1)
    assert "--apricot" not in freeze and "celadon" in freeze


# 7 -------------------------------------------------------------------

def test_whole_milk_is_the_milk_staple(household):
    """Finding 7: staples matched by exact name."""
    conn = get_conn()
    conn.execute("INSERT INTO staples (household_id, item, next_due_at) VALUES (1, 'Milk', '2099-01-01')")
    conn.commit()
    conn.close()
    week = _monday(1)
    d = tools._week_dates(week)
    for name in ("Whole milk", "Milk (1 L)"):
        _inv(name, turns=d[3], category="dairy")
    keep = _inv("Oat cookies", turns=d[3], category="pantry")
    assert [c["id"] for c in use_up.candidates(week, 7)] == [keep]


def test_unfreeze_restores_from_the_servers_own_record(household):
    """Finding 7: /api/inventory/unfreeze trusted the client's location
    and date."""
    item = _inv("Half a cabbage", turns=(household_today() + datetime.timedelta(days=3)).isoformat())
    before = get_conn().execute("SELECT expiration_date FROM inventory_items WHERE id = ?", (item,)).fetchone()[0]
    out = tools.freeze_item(item)
    forged = dict(out["undo"], location="pantry", expiration_date="2099-01-01")
    assert tools.unfreeze_item(forged)["undone"] is True
    row = get_conn().execute("SELECT location, expiration_date, freeze_undo_json FROM inventory_items WHERE id = ?",
                             (item,)).fetchone()
    assert row["location"] == "fridge" and row["expiration_date"] == before and row["freeze_undo_json"] is None
    # A row frozen some other way has no record to restore from.
    tools.set_inventory_location(item, "freezer")
    assert tools.unfreeze_item({"item_id": item})["undone"] is False


def test_disliked_and_allergen_stock_is_not_shown_to_the_generator(household, stub_model):
    """Real next-week draft, 2026-10-27: Portobellos in the fridge, a
    mushrooms dislike, and the model made a portobello fajita out of
    current_inventory. Rule-clashing rows are out of all three lists."""
    tools.add_food_dislikes(["mushrooms"])
    week = _monday(1)
    d = tools._week_dates(week)
    porto = _inv("Portobellos", turns=d[3])
    keep = _inv("Baby spinach", turns=d[3])
    tools.save_week_intake(week, moods=["Comfort food"])
    seen = stub_model([_slot(dd, "dinner", n) for dd, n in zip(d, ["Lamb Stew", "Miso Cod", "Bibimbap",
                                                                   "Laksa", "Ramen", "Gumbo", "Dal"])])
    agent.generate_weekly_plan(week)
    ids = [r.get("id") for r in seen["ctx"]["current_inventory"]]
    assert porto not in ids and keep in ids


# Re-verify (f2c37d2, 2026-10-27) -------------------------------------

def test_the_freezer_trade_never_breaks_a_nights_time_cap(household, stub_model):
    """Re-verify 1: a 120-minute braise traded onto a 30-minute weeknight
    for the sake of freezer order. The cap pass's rules hold the trade."""
    tools.edit_preference("weeknight_max_minutes", 30)
    week = _monday(1)
    d = tools._week_dates(week)
    beef = _inv("Ground beef", location="freezer", category="meat/seafood", frozen_days_ago=30)
    thighs = _inv("Chicken thighs", location="freezer", category="meat/seafood", frozen_days_ago=90)
    names = ["Lamb Stew", "Miso Cod", "Ground Beef Chili", "Laksa", "Ramen", "Chicken Thigh Braise", "Dal"]
    days = []
    for i, (dd, n) in enumerate(zip(d, names)):
        extra = {}
        if i == 2:
            extra = {"derived_from": {"use_up_ids": [beef]}, "prep_time_minutes": 10, "cook_time_minutes": 15}
        if i == 5:
            extra = {"derived_from": {"use_up_ids": [thighs]}, "prep_time_minutes": 20, "cook_time_minutes": 100}
        days.append(_slot(dd, "dinner", n, **extra))
    plan_id, _ = _draft(stub_model, week, days)
    assert _dinner(plan_id, d[2])["meal"] == "Ground Beef Chili"
    assert _dinner(plan_id, d[5])["meal"] == "Chicken Thigh Braise"


def test_undoing_a_swap_brings_the_tag_back(household, stub_model):
    """Re-verify 2: the swap popped use_up from the copy Undo restores."""
    week = _monday(1)
    d, ids = _seed(week)
    plan_id, _ = _draft(stub_model, week, _week_with(week, ids, d))
    before = tools.get_week_menu(plan_id)["use_up"]["used"]
    pasta = _dinner(plan_id, d[0])
    swapped = sip.apply_pick(plan_id, sip._entry(plan_id, pasta["id"]),
                             {"meal_name": "Beef Tacos", "food_groups": [], "reason": "Something else."},
                             correct_title=False)
    assert tools.get_week_menu(plan_id)["use_up"]["used"] == before - 2
    sip.undo_meal_swap(plan_id, swapped["entry_id"])
    menu = tools.get_week_menu(plan_id)
    monday = next(day for day in menu["days"] if day["date"] == d[0])
    assert monday["dinner"]["uses_tag"] == "Uses the spinach and feta"
    assert menu["use_up"]["used"] == before


def test_food_another_live_plan_has_on_a_dish_is_not_offered_again(household, stub_model):
    """Re-verify 3: this week's draft planned the thighs and beef next
    week's draft already had. A row a live plan has put on a dish on
    another day is that plan's."""
    week = _monday(1)
    d = tools._week_dates(week)
    thighs = _inv("Chicken thighs", location="freezer", category="meat/seafood", frozen_days_ago=90)
    spare = _inv("Ground beef", location="freezer", category="meat/seafood", frozen_days_ago=30)
    names = ["Chicken Thigh Traybake", "Miso Cod", "Bibimbap", "Laksa", "Ramen", "Gumbo", "Dal"]
    days = [_slot(dd, "dinner", n, **({"derived_from": {"use_up_ids": [thighs]}} if i == 0 else {}))
            for i, (dd, n) in enumerate(zip(d, names))]
    _draft(stub_model, week, days)
    other = _monday(2)
    assert [c["id"] for c in use_up.candidates(other, 7)] == [spare]
    stock = use_up.filter_stock(tools.get_inventory(), {}, other, 7)
    assert thighs not in [r["id"] for r in stock] and spare in [r["id"] for r in stock]
    # The same plan's own week is not "elsewhere": regenerating it still sees them.
    assert thighs in [c["id"] for c in use_up.candidates(week, 7)]


def test_toasts_say_were_for_a_plural_name(household, stub_model):
    """Re-verify 4: "Red peppers was moved…" — rule 3's own example is
    "Two items were moved"."""
    assert use_up._verb("Red peppers") == "were" and use_up._verb("Mushrooms") == "were"
    assert use_up._verb("Half a cabbage") == "was" and use_up._verb("Hummus") == "was"
    assert use_up._verb("Cilantro") == "was" and use_up._verb("Watercress") == "was"
    week = _monday(1)
    d = tools._week_dates(week)
    peppers = _inv("Red peppers", turns=d[2])
    days = [_slot(dd, "dinner", n) for dd, n in zip(d, ["Lamb Stew", "Miso Cod", "Bibimbap", "Laksa",
                                                        "Ramen", "Gumbo", "Dal"])]
    plan_id, _ = _draft(stub_model, week, days)
    out = use_up.resolve(plan_id, peppers, "freeze")
    assert out["toast"] == "Red peppers were moved to the freezer."


@pytest.mark.today("2026-10-27")
def test_a_day_two_periods_share_still_belongs_to_the_other_plan(household, stub_model):
    """Final check (a659503): this week (Mon 10-26, opened Tue 10-27) and
    next week (Mon 11-02) — the exact pair the real runs made. Next week's
    Monday thigh dish must keep the thighs off this week's list, whether
    the shared day comes from this week's end or not."""
    nxt = "2026-11-02"
    d = tools._week_dates(nxt)
    thighs = _inv("Chicken thighs", location="freezer", category="meat/seafood", frozen_days_ago=90)
    beef = _inv("Ground beef", location="freezer", category="meat/seafood", frozen_days_ago=30)
    names = ["Chicken Thigh Traybake", "Ground Beef Chili", "Bibimbap", "Laksa", "Ramen", "Gumbo", "Dal"]
    days = [_slot(dd, "dinner", n, **({"derived_from": {"use_up_ids": [thighs]}} if i == 0 else
                                       {"derived_from": {"use_up_ids": [beef]}} if i == 1 else {}))
            for i, (dd, n) in enumerate(zip(d, names))]
    _draft(stub_model, nxt, days)
    # This week, asked as the screens ask it (its Monday, 7 days) — and the
    # 7-days-from-today reading that ran it into Mon 11-02.
    for start, count in (("2026-10-26", 7), ("2026-10-27", 7)):
        offered = [c["id"] for c in use_up.candidates(start, count)]
        assert thighs not in offered and beef not in offered, (start, offered)
        stock = [r["id"] for r in use_up.filter_stock(tools.get_inventory(), {}, start, count)]
        assert thighs not in stock and beef not in stock, (start, stock)
    # Next week itself — the plan its own draft replaces — still sees both.
    assert {thighs, beef} <= {c["id"] for c in use_up.candidates(nxt, 7)}
