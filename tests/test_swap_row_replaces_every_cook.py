"""
Swapping a dish from the Meals list replaces it on EVERY day the row
lists — a batch and its made-ahead day AND a separate cook of the same
dish (Loop Board, Emily 2026-10-18, on her phone).

Plan › Meals › BREAKFASTS read "Veggie and Feta Scrambled Eggs … · Mon,
Wed, Fri": Monday cooked, Wednesday made ahead from Monday, Friday its own
cook. Swapped, it left "Boiled Eggs … · Mon, Wed" and "Veggie and Feta …
· Friday". The row's own Swap sheet already sent `whole_dish`; the door
that didn't was the dish's Meal step (the row's name), whose "Swap · I'll
pick" swapped the tapped cook and what it feeds (fed_days) — Mon and Wed.
Now that page, opened from a row standing for several days, swaps the
whole dish (swap_meal_in_place's `whole_dish` -> batch_days), and a Day /
Schedule door still swaps the one meal and its leftovers.

A second hole on the same road: a whole-dish swap to a dish that won't
keep treated every other day of the dish as a "fed" meal
(instead_of_the_leftovers), so a separate cook was opened or handed some
other breakfast instead of the new pick. Only a reheat is fed now.

No model is called: the picker and the asker are stubs.
"""
from __future__ import annotations

import datetime
import importlib
import json
import shutil

import pytest

import nodeharness
from conftest import household_today
from app import tools
from test_menu_swap_whole_dish import (  # noqa: F401  (home is a fixture)
    SHELL, _asker, _grocery, _id, _ledger, _meals, _pick, _rows, home,
)
from test_week_seven_tiles import _extract

sop = importlib.import_module("app.tools.swap_option_picks")

TODAY = household_today()
START = (TODAY - datetime.timedelta(days=1)).isoformat()
MON = (TODAY + datetime.timedelta(days=1)).isoformat()
WED = (TODAY + datetime.timedelta(days=3)).isoformat()
FRI = (TODAY + datetime.timedelta(days=5)).isoformat()
OLD = "Veggie and Feta Scrambled Eggs"
NEW = "Boiled Eggs with Multigrain Toast"


@pytest.fixture
def eggs(home):
    """Monday's batch, Wednesday made ahead from it, Friday its own cook."""
    tools.add_recipe(OLD, ingredients=[{"item": "Feta", "qty": "4 oz", "category": "dairy"}],
                     food_groups=["protein", "vegetable"], default_servings=2,
                     prep_time_minutes=5, cook_time_minutes=10, instructions=["Scramble."])
    tools.plan_meal(MON, OLD, slot="breakfast", weekly_plan_id=home)
    tools.plan_meal(WED, OLD, slot="breakfast", weekly_plan_id=home,
                    derived_from={"links_to": f"{MON}:breakfast", "cook_ahead": True})
    tools.repair_leftover_chains(home)
    tools.plan_meal(FRI, OLD, slot="breakfast", weekly_plan_id=home)
    wed = next(d for d in tools.get_week_menu(home)["days"] if d["date"] == WED)["breakfast"]
    assert wed["leftover_from"]["date"] == MON, "the shape on Emily's screen: Wednesday from Monday"
    return home


def _picker(name=NEW):
    return lambda context: _pick(name, protein="Eggs")


def test_the_dish_pages_swap_replaces_every_day_the_row_lists(eggs):
    out = tools.swap_meal_in_place(eggs, _id(eggs, MON, "breakfast"), picker=_picker(), whole_dish=True)
    assert out["status"] == "swapped"
    assert _meals(eggs, "breakfast") == {MON: NEW, WED: NEW, FRI: NEW}, "no Friday copy of the old dish"
    assert out["dates"] == [MON, WED, FRI]
    # The names come from the seeded dates, not the variable names: MON/FRI
    # are "tomorrow" and "five days out", which are Monday and Friday only
    # when the suite runs on a Sunday.
    first = datetime.date.fromisoformat(MON).strftime("%A")
    last = datetime.date.fromisoformat(FRI).strftime("%A")
    assert first in out["said"] and last in out["said"], "the toast says where it landed"
    titles = {d["date"]: (d.get("breakfast") or {}).get("leftover_from", {}).get("meal")
              or (d.get("breakfast") or {}).get("title") for d in tools.get_week_menu(eggs)["days"]}
    assert OLD not in titles.values()


def test_the_day_doors_swap_still_changes_one_cook_and_its_made_ahead_day(eggs):
    tools.swap_meal_in_place(eggs, _id(eggs, MON, "breakfast"), picker=_picker())
    assert _meals(eggs, "breakfast") == {MON: NEW, WED: NEW, FRI: OLD}, "Friday is its own cook"


def test_a_whole_dish_swap_to_a_dish_that_wont_keep_still_reaches_the_separate_cook(eggs):
    pick = dict(_pick(NEW, protein="Eggs"), keeps_as_leftovers=False)
    entry_id = _id(eggs, MON, "breakfast")
    tools.swap_options(eggs, entry_id, asker=_asker(pick), whole_dish=True)
    out = tools.choose_swap_option(eggs, entry_id, 0, whole_dish=True)
    assert out["status"] == "swapped"
    meals = _meals(eggs, "breakfast")
    assert meals[FRI] == NEW, "a fresh cook takes the pick — never opened, never left on the old dish"
    assert meals[WED] != OLD, "the made-ahead day leaves the batch (the pick won't keep)"


def test_an_approved_week_shops_for_every_day_of_the_new_dish(eggs):
    tools.approve_weekly_plan(eggs, "Alex")
    assert "Feta" in _grocery()
    tools.swap_meal_in_place(eggs, _id(eggs, MON, "breakfast"), picker=_picker(), whole_dish=True)
    on_list = _grocery()
    assert "Feta" not in on_list, "the old dish's shopping comes off for all three days"
    assert "Eggs" in on_list
    ids = {r["date"]: r["id"] for r in _rows(eggs, "breakfast")}
    assert _ledger(ids[MON]) and _ledger(ids[FRI]) and not _ledger(ids[WED]), \
        "two cooks shop; the made-ahead day eats Monday's"


def test_the_route_carries_whole_dish_through(signed_in, eggs, monkeypatch):
    real = tools.swap_meal_in_place
    monkeypatch.setattr(tools, "swap_meal_in_place", lambda plan_id, entry_id, avoid=None, **kw: real(
        plan_id, entry_id, avoid=avoid, picker=_picker(), **kw))
    res = signed_in.post(f"/api/week/{START}/swap-in-place",
                         json={"entry_id": _id(eggs, MON, "breakfast"), "whole_dish": True})
    assert res.status_code == 200 and res.json()["status"] == "swapped"
    assert _meals(eggs, "breakfast") == {MON: NEW, WED: NEW, FRI: NEW}


_needs_node = pytest.mark.skipif(shutil.which("node") is None, reason="node runs the row's own renderer")


@_needs_node
def test_only_a_row_standing_for_several_days_opens_its_page_as_the_whole_dish():
    fns = ["escapeHtml", "dayName", "wkMenuRowHtml", "wkMiniHtml", "wkDaysPhrase", "wkMenuDaysPhrase",
           "wkMenuFact", "wkRowMeta", "wkLeftoverSourcePhrase", "slotWord", "isSnackSlot"]
    js = (
        "var WK_ICONS = { swap: '', tweak: '', move: '', titleChev: '' };\n"
        "var WK_MENU_NOUNS = { breakfast: 'mornings' };\n"
        + "\n".join(_extract(f, SHELL) for f in fns) + "\n"
        "var days = ['2026-10-19','2026-10-20','2026-10-21','2026-10-22','2026-10-23'].map(function (d) { return { date: d }; });\n"
        "function e(extra) { return Object.assign({ title: 'Eggs', state: 'planned', entry_id: 1 }, extra || {}); }\n"
        "var many = { slot: 'breakfast', name: 'Eggs', days: [0, 2, 4].map(function (i) { return { date: days[i].date, index: i, key: 'breakfast', entry: e(), past: false }; }) };\n"
        "var one = { slot: 'breakfast', name: 'Eggs', days: [{ date: days[0].date, index: 0, key: 'breakfast', entry: e(), past: false }] };\n"
        "var reheat = { slot: 'lunch', name: 'Eggs', isLeftover: true, days: [1, 3].map(function (i) { return { date: days[i].date, index: i, key: 'lunch', entry: e({ leftover_from: { date: days[0].date, slot: 'breakfast' } }), past: false }; }) };\n"
        "console.log(JSON.stringify([wkMenuRowHtml(many, days), wkMenuRowHtml(one, days), wkMenuRowHtml(reheat, days)]));"
    )
    res = nodeharness.run_node(js, timeout=30)
    assert res.returncode == 0, res.stderr
    many, one, reheat = json.loads(res.stdout.strip())
    assert 'data-wk-meal-dish="1" data-wk-meal="breakfast"' in many
    assert "data-wk-meal-dish" not in one, "one day ahead is the one-meal page, as ever"
    assert "data-wk-meal-dish" not in reheat, "a leftovers row has no dish of its own to swap"


def test_the_pages_swap_sends_whole_dish_only_when_opened_as_the_dish():
    run = _extract("runSwapInPlace", SHELL)
    assert "weekState.step === 'meal' && weekState.mealWholeDish" in run and "whole_dish: true" in run
    go = _extract("goMealsStep", SHELL)
    assert "weekState.mealWholeDish = !!opts.wholeDish" in go, "every other way into a Meal step is one meal"
    assert "wholeDish: btn.hasAttribute('data-wk-meal-dish')" in SHELL
