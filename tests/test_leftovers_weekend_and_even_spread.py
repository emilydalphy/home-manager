"""
Fewer dinner dishes than nights: each dish covers its share of them, and a
weekend lunch eats last night's dinner.

Emily, 2026-09-29, answering the two calls on the Loop Board card "The draft
doesn't make good use of leftovers":

  1. A weekend lunch comes from last night's dinner leftovers, not a frozen
     portion — when food safety (3 days from the cook) and "at most two meals
     in a row of one dish" allow. Friday dinner then Saturday lunch is two in
     a row, so Saturday's dinner is a different dish.
  2. N dinner dishes over M nights: each dish covers about M/N nights. Her
     week, 3 dinners over 6 nights, is each dish cooked once and eaten twice;
     when M/N isn't whole the dishes differ by at most one night.

Her week (the Sep 28 – Oct 3 shape): six days Mon–Sat, Dinners 3 ·
Breakfasts 1 · Lunches 2 · Snacks 2; weekday lunches prepped Sunday and
Tuesday (Mon–Thu), Friday's lunch "Leftovers from dinner", none cooked that
day; Saturday's lunch isn't on that screen. The model's Saturday lunch is a
repeat of Monday's soup, which the lunch count used to turn into "Leftovers
from the freezer — Monday's soup".

Her week has a conflict the two decisions can't both win, found building
this: Tuesday is a prep day, so Tuesday's dinner is the cook the Wednesday
and Thursday lunches eat; Friday's lunch is Thursday's dinner; and no dish
may be on three meals in a row. Then Friday's dinner can only be a dish
cooked Monday or Tuesday (four days before Saturday's lunch — too long in
the fridge) unless something is cooked a fourth time. So in her exact week
the dishes stay even and cooked once, and Saturday's lunch keeps its portion
from the freezer (no extra cooking either way). With Sunday as the only prep
day, Saturday's lunch is Friday's dinner. Both are pinned below.

Each route test fails on origin/main 7b5f7df. The model and the swap picker
are stubbed; nothing reaches the API.
"""
from __future__ import annotations

import pytest

from app import agent, tools
from app.tools import leftovers, leftovers_spread, meal_variety, weekday_lunches

from tests.test_draft_dish_counts_and_leftovers import (  # noqa: F401 — fixtures
    _assert_her_numbers, _cooks, _monday, _rows, _stub, _via_agent, _via_generate, _via_replan,
    _via_stream, _week, emily, picker,
)

# Saturday's lunch repeats Monday's soup — the one the lunch count froze.
HER_LUNCHES = ["Turkish-Style Lentil Soup", "Turkish-Style Lentil Soup", "Corn and Chicken Pancake",
               "Corn and Chicken Pancake", None, "Turkish-Style Lentil Soup"]

SHAPES = {
    "follows-the-number": ["Stew", "Stew", "Tacos", "Tacos", "Curry", "Curry"],
    "six-different": ["Stew", "Tacos", "Curry", "Pasta", "Chili", "Roast"],
    "alternating": ["Stew", "Tacos", "Curry", "Stew", "Tacos", "Curry"],
    "one-dish-three-nights": ["Stew", "Stew", "Stew", "Tacos", "Curry", "Pasta"],
}


def _sunday_prep_only(mon, dates):
    tools.save_week_intake(mon, night_tags={}, weekday_lunches={
        "prep_days": ["sunday"],
        "days": [{"date": d, "kind": "prepped"} for d in dates[:4]] + [{"date": dates[4], "kind": "leftovers"}],
    })


def _assert_her_week(plan_id: int, dates: list[str], saturday_eats_friday: bool):
    dinners, lunches = _rows(plan_id, "dinner"), _rows(plan_id, "lunch")
    chains = leftovers.plan_leftover_chains(plan_id)

    # Decision 2: three dishes, each cooked once, each on exactly two nights.
    cooks = _cooks(plan_id, "dinner")
    assert len(cooks) == 3, [(c["date"], c["meal"]) for c in cooks]
    assert len({c["meal"] for c in cooks}) == 3, "each dish cooked once"
    nights = [dinners[d]["meal"] for d in dates]
    assert sorted(nights.count(m) for m in set(nights)) == [2, 2, 2], nights
    cook_ids = {c["id"] for c in cooks}
    for d in dates:
        assert dinners[d]["slot_state"] == "planned"
        if dinners[d]["id"] not in cook_ids:
            source = chains["leftovers"][dinners[d]["id"]]["source"]
            assert source["meal"] == dinners[d]["meal"]
            assert 1 <= leftovers.days_apart(source["date"], d) <= leftovers.MAX_LEFTOVER_DAYS

    sat_lunch = lunches[dates[5]]
    if saturday_eats_friday:
        # Decision 1: Saturday's lunch is Friday's dinner, from the fridge.
        assert not leftovers.frozen_portion_on(sat_lunch["derived"]), sat_lunch["meal"]
        source = chains["leftovers"][sat_lunch["id"]]["source"]
        assert source["slot"] == "dinner" and sat_lunch["meal"] == dinners[dates[4]]["meal"]
        assert leftovers.days_apart(source["date"], dates[5]) <= leftovers.MAX_LEFTOVER_DAYS
        assert dinners[dates[5]]["meal"] != dinners[dates[4]]["meal"], "Saturday's dinner is a different dish"
        # …and no cook still freezes a portion for it.
        for rows in (dinners, lunches):
            for r in rows.values():
                assert f"{dates[5]}:lunch" not in (r["derived"].get(leftovers.FREEZER_EXTRA_KEY) or {}).get("for", [])
    else:
        # The conflict in the module docstring: nothing is cooked for it.
        assert leftovers.frozen_portion_on(sat_lunch["derived"]) or sat_lunch["id"] in chains["leftovers"]
        # Tuesday's prep batch is Tuesday's dinner, cooked, feeding
        # Wednesday's and Thursday's lunches.
        batches = {b["prep_date"]: b for b in weekday_lunches.prepped_batches(plan_id)}
        tuesday = batches[dates[1]]
        assert tuesday["cook_date"] == dates[1] and tuesday["slot"] == "dinner"
        assert tuesday["lunch_dates"] == [dates[2], dates[3]]
        assert tuesday["cook_entry_id"] in cook_ids

    # Friday's lunch is still last night's dinner ("Leftovers from dinner: 1").
    friday = chains["leftovers"][lunches[dates[4]]["id"]]["source"]
    assert friday["slot"] == "dinner" and friday["meal"] == dinners[dates[3]]["meal"]
    # The prepped lunches are still eaten within three days of their cook.
    for d in dates[:4]:
        reheat = chains["leftovers"].get(lunches[d]["id"])
        if reheat:
            assert leftovers.days_apart(reheat["source"]["date"], d) <= leftovers.MAX_LEFTOVER_DAYS
    assert leftovers.long_runs(leftovers.run_keys(plan_id)) == []
    _assert_her_numbers(plan_id)


ROUTES = dict(zip(["chat-tool", "generate", "stream", "re-plan"], [_via_agent, _via_generate, _via_stream, _via_replan]))


@pytest.mark.parametrize("dinners", list(SHAPES.values()), ids=list(SHAPES))
@pytest.mark.parametrize("route", list(ROUTES.values()), ids=list(ROUTES))
def test_her_week_on_every_route(route, dinners, emily, picker, monkeypatch, signed_in):
    """Prep Sunday and Tuesday: even, cooked once, Tuesday's batch intact."""
    mon, dates = emily
    tools.save_week_intake(mon, night_tags={})
    _stub(monkeypatch, _week(dates, dinners, HER_LUNCHES, dinner_minutes=25))
    _assert_her_week(route(mon, signed_in), dates, saturday_eats_friday=False)


@pytest.mark.parametrize("dinners", list(SHAPES.values()), ids=list(SHAPES))
@pytest.mark.parametrize("route", list(ROUTES.values()), ids=list(ROUTES))
def test_saturday_lunch_is_fridays_dinner_on_every_route(route, dinners, emily, picker, monkeypatch, signed_in):
    """Prep Sunday only: Saturday's lunch eats Friday's dinner too."""
    mon, dates = emily
    _sunday_prep_only(mon, dates)
    _stub(monkeypatch, _week(dates, dinners, HER_LUNCHES, dinner_minutes=25))
    _assert_her_week(route(mon, signed_in), dates, saturday_eats_friday=True)


def test_a_rush_night_in_the_week_reheats(emily, picker, monkeypatch):
    """A night short on time is never given a cook it has no time for: the
    model's alternating week (four cooks) is re-laid with Wednesday, a rush
    night, as a reheat."""
    mon, dates = emily
    tools.save_week_intake(mon, night_tags={dates[2]: ["rush"]})
    _stub(monkeypatch, _week(dates, SHAPES["alternating"], HER_LUNCHES, dinner_minutes=45))
    plan_id = agent.generate_weekly_plan(mon, day_count=6)["weekly_plan_id"]
    wednesday = _rows(plan_id, "dinner")[dates[2]]
    assert wednesday["id"] in leftovers.plan_leftover_chains(plan_id)["leftovers"], wednesday["meal"]
    assert len(_cooks(plan_id, "dinner")) == 3


# ==========================================================================
# The pass on its own
# ==========================================================================

def _plan(dinners, dates, derived=None):
    plan_id = tools.create_weekly_plan(dates[0])["weekly_plan_id"]
    for i, (d, name) in enumerate(zip(dates, dinners)):
        tools.plan_meal(meal_date=d, meal=name, slot="dinner", weekly_plan_id=plan_id,
                        derived_from=(derived or {}).get(i))
    return plan_id


def test_seven_nights_three_dishes_is_three_two_two():
    """M/N not whole: as even as possible, and each dish cooked once."""
    tools.add_member("Emily")
    dates = tools.period_dates(_monday(), 7)
    plan_id = _plan(["Stew", "Tacos", "Curry", "Stew", "Tacos", "Curry", "Stew"], dates)
    out = leftovers_spread.spread_dinners(plan_id, None, target=3)
    assert out["moved"], out
    nights = [_rows(plan_id, "dinner")[d]["meal"] for d in dates]
    assert sorted(nights.count(m) for m in set(nights)) == [2, 2, 3]
    assert len(_cooks(plan_id, "dinner")) == 3
    assert leftovers.long_runs(leftovers.run_keys(plan_id)) == []


def test_a_week_already_spread_is_not_touched():
    tools.add_member("Emily")
    dates = tools.period_dates(_monday(), 6)
    plan_id = _plan(["Stew", "Stew", "Tacos", "Tacos", "Curry", "Curry"], dates)
    meal_variety.enforce_distinct_count(plan_id, 3, slot="dinner", fill_up=False)
    before = _rows(plan_id, "dinner")
    out = leftovers_spread.spread_dinners(plan_id, None, target=3)
    assert out["skipped"] == "already as good", out
    assert _rows(plan_id, "dinner") == before


def test_no_dinner_number_means_no_spreading():
    tools.add_member("Emily")
    dates = tools.period_dates(_monday(), 6)
    plan_id = _plan(["Stew", "Tacos", "Curry", "Stew", "Tacos", "Curry"], dates)
    assert leftovers_spread.spread_dinners(plan_id, None, target=None)["skipped"] == "no dinner number set"
    assert [_rows(plan_id, "dinner")[d]["meal"] for d in dates] == ["Stew", "Tacos", "Curry"] * 2


def test_a_dish_they_asked_for_keeps_its_night():
    tools.add_member("Emily")
    dates = tools.period_dates(_monday(), 6)
    plan_id = _plan(["Stew", "Tacos", "Curry", "Stew", "Tacos", "Curry"], dates,
                    derived={3: {"freeform": "stew on Thursday"}})
    leftovers_spread.spread_dinners(plan_id, None, target=3)
    rows = _rows(plan_id, "dinner")
    assert rows[dates[3]]["meal"] == "Stew"
    assert len(_cooks(plan_id, "dinner")) == 3


def test_a_night_tagged_leftovers_stays_a_reheat():
    tools.add_member("Emily")
    dates = tools.period_dates(_monday(), 6)
    plan_id = _plan(["Stew", "Tacos", "Curry", "Stew", "Tacos", "Curry"], dates)
    intake = {"night_tags": {dates[2]: ["left"]}}
    leftovers_spread.spread_dinners(plan_id, intake, target=3)
    wednesday = _rows(plan_id, "dinner")[dates[2]]
    assert wednesday["id"] in leftovers.plan_leftover_chains(plan_id)["leftovers"]


def test_a_weekend_lunch_stays_frozen_when_last_nights_dinner_would_be_three_in_a_row():
    """Friday and Saturday dinner are both the Stew they asked for: Saturday's
    lunch of it would be a third meal in a row, so it keeps its portion."""
    tools.add_member("Emily")
    dates = tools.period_dates(_monday(), 6)
    plan_id = _plan(["Tacos", "Tacos", "Curry", "Curry", "Stew", "Stew"], dates,
                    derived={4: {"freeform": "stew friday"}, 5: {"freeform": "stew saturday"}})
    meal_variety.enforce_distinct_count(plan_id, 3, slot="dinner", fill_up=False)
    soup = tools.plan_meal(meal_date=dates[0], meal="Soup", slot="lunch", weekly_plan_id=plan_id)
    tools.plan_meal(meal_date=dates[5], meal=leftovers.freezer_night_name("Soup", dates[0]), slot="lunch",
                    weekly_plan_id=plan_id,
                    derived_from={leftovers.FROM_FREEZER_KEY: {"cook": f"entry_id:{soup['entry_id']}", "dish": "Soup"}})
    leftovers_spread.spread_dinners(plan_id, None, target=3)
    sat = _rows(plan_id, "lunch")[dates[5]]
    assert leftovers.frozen_portion_on(sat["derived"]) == "Soup"
    assert leftovers.long_runs(leftovers.run_keys(plan_id)) == []


def test_an_approved_week_is_never_re_laid():
    tools.add_member("Emily")
    dates = tools.period_dates(_monday(), 6)
    plan_id = _plan(["Stew", "Tacos", "Curry", "Stew", "Tacos", "Curry"], dates)
    from app.db import get_conn
    conn = get_conn()
    conn.execute("UPDATE weekly_plans SET status = 'approved' WHERE id = ?", (plan_id,))
    conn.commit()
    conn.close()
    assert leftovers_spread.spread_dinners(plan_id, None, target=3)["skipped"] == "approved"
