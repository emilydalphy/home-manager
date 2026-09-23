"""
Swap on a "What we're eating" row changes the whole dish (Emily,
2026-09-22 ~22:05, on her phone).

The menu draws one row per dish — "Lebanese-Style Garlic Shrimp with
Herbed Rice · Thu, Fri" — and that row's Swap acted on the dish's FIRST day
only: the new dish landed on Thursday, the shrimp stayed on Friday, and she
had "two meals instead of 1". Now the row's Swap sends `whole_dish` and the
server works out the days itself (swap_in_place.dish_days, the screen's own
grouping), swaps every one still ahead in one transaction
(weekly_plan.replace_dish_on_days), keeps a cook + reheat shape a cook +
reheat shape, and Undo puts every day back. The Day step's Swap, and a row
with one day ahead, are the one-day swap they always were.

The model is never called: every pick here carries its steps, so choose
applies it as it is (swap_options.needs_write_out).
"""
from __future__ import annotations

import datetime
import importlib
import json
import re
import shutil
from pathlib import Path

import pytest

import nodeharness
from conftest import household_today
from app import tools
from app.db import get_conn
from app.tools._shared import use_household
from test_week_seven_tiles import _extract, _extract_var

sop = importlib.import_module("app.tools.swap_options")

TODAY = household_today()
START = (TODAY - datetime.timedelta(days=1)).isoformat()
PAST = START
D1 = (TODAY + datetime.timedelta(days=1)).isoformat()
D2 = (TODAY + datetime.timedelta(days=2)).isoformat()
D3 = (TODAY + datetime.timedelta(days=3)).isoformat()
SHRIMP = "Garlic Shrimp with Herbed Rice"
NEW = "Lemon Chicken Traybake"
SHELL = (Path(__file__).resolve().parent.parent / "static" / "shell.js").read_text(encoding="utf-8")
_needs_node = pytest.mark.skipif(shutil.which("node") is None, reason="node runs the sheet's own renderer")


def _pick(name=NEW, protein="Chicken thighs"):
    # 20 minutes: a weekday lunch cooked that day is 20 minutes or less
    # (Emily, 2026-09-23), and most of this file swaps lunches that are
    # separate cooks, on whatever days the suite runs. The cap tests below
    # use _long() on dinners.
    return {
        "meal_name": name, "reason": "Lighter, and nothing to thaw.",
        "ingredients": [{"item": protein, "qty": "1 lb", "category": "meat/seafood"},
                        {"item": "Lemons", "qty": "2", "category": "produce"}],
        "instructions": ["Roast it."], "food_groups": ["protein", "vegetable", "carb"],
        "main_protein": protein, "prep_time_minutes": 10, "cook_time_minutes": 10, "default_servings": 2,
    }


def _asker(*picks):
    return lambda context: [dict(p) for p in picks]


@pytest.fixture
def home():
    for name in ("Alex", "Sam"):
        tools.add_member(name)
    tools.add_recipe(SHRIMP, ingredients=[{"item": "Shrimp", "qty": "1 lb", "category": "meat/seafood"},
                                          {"item": "Parsley", "qty": "1 bunch", "category": "produce"}],
                     food_groups=["protein", "carb", "vegetable"], default_servings=2,
                     prep_time_minutes=10, cook_time_minutes=20)
    tools.add_recipe("Chili", ingredients=[{"item": "Beef", "qty": "1 lb", "category": "meat/seafood"}],
                     food_groups=["protein"], default_servings=2)
    sop._OPTIONS_CACHE.clear()
    return tools.create_weekly_plan(START)["weekly_plan_id"]


def _separate_cooks(plan_id, slot="lunch"):
    """Shrimp at lunch yesterday (gone by), tomorrow and the day after —
    three separate cooks — and Chili for dinner tomorrow. With
    slot="dinner" the two swap places: Shrimp for dinner, Chili at lunch."""
    for day in (PAST, D1, D2):
        tools.plan_meal(day, SHRIMP, slot=slot, weekly_plan_id=plan_id, reasoning="you like it")
    tools.plan_meal(D1, "Chili", slot="lunch" if slot == "dinner" else "dinner", weekly_plan_id=plan_id)


def _cook_and_reheat(plan_id):
    """Shrimp cooked for dinner tomorrow, reheated the next night and the
    one after — a confirmed chain, the shape the generator writes."""
    tools.plan_meal(D1, SHRIMP, slot="dinner", weekly_plan_id=plan_id)
    for day in (D2, D3):
        tools.plan_meal(day, SHRIMP, slot="dinner", weekly_plan_id=plan_id,
                        derived_from={"links_to": f"{D1}:dinner"})
    tools.repair_leftover_chains(plan_id)


def _rows(plan_id, slot):
    conn = get_conn()
    rows = conn.execute(
        "SELECT mpe.id, mpe.date, COALESCE(r.name, mpe.freeform_meal) AS meal, mpe.derived_from_json "
        "FROM meal_plan_entries mpe LEFT JOIN recipes r ON r.id = mpe.recipe_id "
        "WHERE mpe.weekly_plan_id = ? AND mpe.slot = ? ORDER BY mpe.date, mpe.id",
        (plan_id, slot),
    ).fetchall()
    conn.close()
    return [dict(r, derived=json.loads(r["derived_from_json"] or "{}")) for r in rows]


def _meals(plan_id, slot):
    return {r["date"]: r["meal"] for r in _rows(plan_id, slot)}


def _id(plan_id, day, slot):
    return next(r["id"] for r in _rows(plan_id, slot) if r["date"] == day)


def _grocery():
    rows = tools.list_grocery_list() + tools.list_grocery_list(status="spice")
    return {g["item"]: g["quantity"] for g in rows}


def _ledger(entry_id):
    conn = get_conn()
    rows = conn.execute("SELECT item FROM meal_plan_grocery_links WHERE meal_plan_entry_id = ?",
                        (entry_id,)).fetchall()
    conn.close()
    return {r["item"] for r in rows}


def _swap_whole(plan_id, day, slot, pick=None):
    entry_id = _id(plan_id, day, slot)
    opened = tools.swap_options(plan_id, entry_id, asker=_asker(pick or _pick()), whole_dish=True)
    return opened, tools.choose_swap_option(plan_id, entry_id, 0, whole_dish=True)


def _menu_day(plan_id, day):
    return next(d for d in tools.get_week_menu(plan_id)["days"] if d["date"] == day)


# ---------- the whole dish, every day ahead ----------


def test_the_rows_swap_replaces_the_dish_on_every_day_still_ahead(home):
    _separate_cooks(home)
    opened, out = _swap_whole(home, D1, "lunch")
    assert opened["dates"] == [D1, D2], "the sheet is told the days the write will change"
    assert out["status"] == "swapped"
    assert _meals(home, "lunch") == {PAST: SHRIMP, D1: NEW, D2: NEW}, "two days swapped, the gone one left"
    assert [d["date"] for d in out["days"]] == [D1, D2] and out["day"]["date"] == D1
    assert out["dates"] == [D1, D2] and len(out["entry_ids"]) == 2
    assert [len([r for r in _rows(home, "lunch") if r["date"] == d]) for d in (D1, D2)] == [1, 1], \
        "one lunch a day — never the old one beside the new"
    assert _meals(home, "dinner") == {D1: "Chili"}, "another dish is untouched"


def test_the_menu_reads_one_row_for_the_new_dish_afterwards(home):
    _separate_cooks(home)
    _swap_whole(home, D1, "lunch")
    titles = [_menu_day(home, d)["lunch"]["title"] for d in (D1, D2)]
    assert titles == [NEW, NEW]


def test_a_day_already_cooked_is_left_as_it_is(home):
    _separate_cooks(home)
    conn = get_conn()
    conn.execute("UPDATE meal_plan_entries SET cooked_status = 'done' WHERE id = ?", (_id(home, D2, "lunch"),))
    conn.commit()
    conn.close()
    _opened, out = _swap_whole(home, D1, "lunch")
    assert _meals(home, "lunch") == {PAST: SHRIMP, D1: NEW, D2: SHRIMP}
    assert "days" not in out, "one day left to change is the ordinary one-day swap"


def _veto_on(monkeypatch, day, dish=NEW):
    swap_in_place = importlib.import_module("app.tools.swap_in_place")
    real = swap_in_place.pick_gate
    monkeypatch.setattr(swap_in_place, "pick_gate", lambda pick, entry: (
        "Sam would rather not" if entry["date"] == day and pick.get("meal_name") == dish else real(pick, entry)))


def test_every_day_is_gated_not_only_the_tapped_one(home, monkeypatch):
    _separate_cooks(home)
    entry_id = _id(home, D1, "lunch")
    tools.swap_options(home, entry_id, asker=_asker(_pick()), whole_dish=True)
    # The house changed between the sheet opening and the tap.
    _veto_on(monkeypatch, D2)
    out = tools.choose_swap_option(home, entry_id, 0, whole_dish=True)
    assert out["status"] == "refused" and "Sam would rather not" in out["message"]
    assert _meals(home, "lunch") == {PAST: SHRIMP, D1: SHRIMP, D2: SHRIMP}, "refused means nothing changed"


def test_a_pick_one_of_the_days_cant_have_is_never_offered(home, monkeypatch):
    _separate_cooks(home)
    _veto_on(monkeypatch, D2)
    opened = tools.swap_options(home, _id(home, D1, "lunch"),
                                asker=_asker(_pick(), _pick("Fish Tacos", protein="Cod")), whole_dish=True)
    assert [o["meal"] for o in opened["options"]] == ["Fish Tacos"]


# ---------- the strictest of the days (Emily's standing rule, 2026-09-22) ----------


def _recording_asker(*picks):
    seen = []

    def ask(context):
        seen.append(context)
        return [dict(p) for p in picks]

    ask.contexts = seen
    return ask


def _quick(name="Ten-Minute Wraps"):
    return dict(_pick(name), prep_time_minutes=5, cook_time_minutes=10)


def _long(name=NEW):
    return dict(_pick(name), prep_time_minutes=10, cook_time_minutes=25)


def _rush():
    return importlib.import_module("app.tools.week_intake").RUSH_MAX_MINUTES


def test_a_rush_friday_holds_the_picks_to_the_rush_cap(home):
    _separate_cooks(home, slot="dinner")
    tools.save_week_intake(START, night_tags={D1: ["unrushed"], D2: ["rush"]})
    ask = _recording_asker(_quick(), _long())
    opened = tools.swap_options(home, _id(home, D1, "dinner"), asker=ask, whole_dish=True)
    context = ask.contexts[0]
    assert context["max_minutes"] == _rush(), "the lowest cap of the days — an unrushed day doesn't lift a rush one"
    assert context["night_tags"] == ["rush"], "'no cap tonight' is only said when it's true of every day"
    assert context["dates"] == [D1, D2]
    assert [o["meal"] for o in opened["options"]] == ["Ten-Minute Wraps"], "a 35-minute pick isn't offered"


def test_a_one_day_swap_on_the_unrushed_day_keeps_its_own_cap(home):
    _separate_cooks(home, slot="dinner")
    tools.save_week_intake(START, night_tags={D1: ["unrushed"], D2: ["rush"]})
    ask = _recording_asker(_long())
    tools.swap_options(home, _id(home, D1, "dinner"), asker=ask)
    assert ask.contexts[0]["max_minutes"] is None and ask.contexts[0]["night_tags"] == ["unrushed"]


def test_the_weeknight_limit_counts_as_a_cap(home):
    _separate_cooks(home, slot="dinner")
    tools.edit_preference("weeknight_max_minutes", 30)
    ask = _recording_asker(_long())
    tools.swap_options(home, _id(home, D1, "dinner"), asker=ask, whole_dish=True)
    on_a_weeknight = any(datetime.date.fromisoformat(d).weekday() < 5 for d in (D1, D2))
    assert ask.contexts[0]["max_minutes"] == (30 if on_a_weeknight else None)


def test_a_pick_over_fridays_cap_is_refused_and_nothing_is_written(home):
    _separate_cooks(home, slot="dinner")
    entry_id = _id(home, D1, "dinner")
    tools.swap_options(home, entry_id, asker=_asker(_long()), whole_dish=True)
    # The second day became a rush night after the sheet opened: the
    # 35-minute pick is still on offer, and the tap holds every day to its
    # own cap.
    tools.save_week_intake(START, night_tags={D2: ["rush"]})
    out = tools.choose_swap_option(home, entry_id, 0, whole_dish=True)
    weekday = datetime.date.fromisoformat(D2).strftime("%A")
    assert out["status"] == "refused"
    assert out["message"] == f"I left it as it was — {NEW} takes 35 minutes, and {weekday} only has {_rush()}."
    assert _meals(home, "dinner") == {PAST: SHRIMP, D1: SHRIMP, D2: SHRIMP}
    assert NEW not in {r["name"] for r in tools.list_recipes()}, "no recipe saved either"


def test_a_written_out_recipe_that_runs_long_is_refused_too(home):
    _separate_cooks(home, slot="dinner")
    tools.save_week_intake(START, night_tags={D2: ["rush"]})
    trimmed = {"meal_name": "Quick Noodles", "reason": "Fast.", "ingredients": ["Noodles"], "minutes": 15}
    entry_id = _id(home, D1, "dinner")
    opened = tools.swap_options(home, entry_id, asker=_asker(trimmed), whole_dish=True)
    assert [o["meal"] for o in opened["options"]] == ["Quick Noodles"]
    long = dict(_long("Quick Noodles"), prep_time_minutes=15, cook_time_minutes=30)
    out = tools.choose_swap_option(home, entry_id, 0, whole_dish=True, writer=lambda ctx, pick: long)
    assert out["status"] == "refused" and "takes 45 minutes" in out["message"]
    assert _meals(home, "dinner") == {PAST: SHRIMP, D1: SHRIMP, D2: SHRIMP}


def test_apply_pick_to_days_refuses_a_day_over_its_cap_as_the_backstop(home):
    _separate_cooks(home, slot="dinner")
    tools.save_week_intake(START, night_tags={D2: ["rush"]})
    swap_in_place = importlib.import_module("app.tools.swap_in_place")
    days = swap_in_place.dish_days(home, _id(home, D1, "dinner"))
    with pytest.raises(ValueError, match="only has"):
        swap_in_place.apply_pick_to_days(home, days, _long())
    assert _meals(home, "dinner") == {PAST: SHRIMP, D1: SHRIMP, D2: SHRIMP}


def test_the_picks_are_asked_for_everyone_at_any_of_the_tables(home):
    _separate_cooks(home)
    tools.add_member("Rae")
    tools.set_slot_attendance(D1, "lunch", present_member_ids=["Alex", "Sam"])
    tools.set_slot_attendance(D2, "lunch", present_member_ids=["Alex", "Rae"], guest_count=2)
    ask = _recording_asker(_pick())
    tools.swap_options(home, _id(home, D1, "lunch"), asker=ask, whole_dish=True)
    table = ask.contexts[0]["table"]
    assert sorted(table["present"]) == ["Alex", "Rae", "Sam"] and table["away"] == []
    assert table["guests"] == 2 and table["serves"] == 4


def test_picks_asked_for_one_day_are_not_used_for_the_whole_dish(home):
    _separate_cooks(home)
    entry_id = _id(home, D1, "lunch")
    tools.swap_options(home, entry_id, asker=_asker(_pick()))
    with pytest.raises(ValueError, match="tap Swap again"):
        tools.choose_swap_option(home, entry_id, 0, whole_dish=True)
    ask = _recording_asker(_pick())
    tools.swap_options(home, entry_id, asker=ask, whole_dish=True)
    assert ask.contexts, "the whole-dish sheet asks again rather than reusing one day's picks"


def test_a_failure_part_way_changes_no_day(home, monkeypatch):
    _separate_cooks(home)
    meal_plans = importlib.import_module("app.tools.meal_plans")
    real = meal_plans.plan_meal
    calls = []

    def flaky(*args, **kwargs):
        calls.append(1)
        if len(calls) == 2:
            raise RuntimeError("disk full")
        return real(*args, **kwargs)

    monkeypatch.setattr(meal_plans, "plan_meal", flaky)
    with pytest.raises(RuntimeError):
        _swap_whole(home, D1, "lunch")
    assert _meals(home, "lunch") == {PAST: SHRIMP, D1: SHRIMP, D2: SHRIMP}, "one transaction: all or nothing"


# ---------- cook once, eat twice ----------


def test_a_cook_and_its_reheats_stay_a_cook_and_its_reheats(home):
    _cook_and_reheat(home)
    _opened, out = _swap_whole(home, D1, "dinner")
    assert out["status"] == "swapped"
    rows = {r["date"]: r for r in _rows(home, "dinner")}
    assert {d: r["meal"] for d, r in rows.items()} == {D1: NEW, D2: NEW, D3: NEW}
    assert rows[D1]["derived"]["make_double_for"] == [f"{D2}:dinner", f"{D3}:dinner"]
    assert rows[D2]["derived"]["links_to"] == f"{D1}:dinner" and rows[D3]["derived"]["links_to"] == f"{D1}:dinner"
    for day in (D2, D3):
        slot = _menu_day(home, day)["dinner"]
        assert slot["source"] == "leftovers" and slot["leftover_from"]["meal"] == NEW, "still a reheat, of the new dish"


def test_an_approved_chain_buys_one_batch_of_the_new_dish(home):
    _cook_and_reheat(home)
    tools.approve_weekly_plan(home, "Alex")
    assert _grocery()["Shrimp"] == "3 lbs", "the starting batch: one cook for three nights"
    _swap_whole(home, D1, "dinner")
    on_list = _grocery()
    assert "Shrimp" not in on_list and "Parsley" not in on_list, "the old dish is taken back off"
    assert on_list["Chicken thighs"] == "3 lbs", "one cook, for all three nights"
    ids = {r["date"]: r["id"] for r in _rows(home, "dinner")}
    assert _ledger(ids[D1]) and not _ledger(ids[D2]) and not _ledger(ids[D3]), "a reheat buys nothing"


def test_an_approved_week_of_separate_cooks_buys_each_day(home):
    _separate_cooks(home)
    tools.approve_weekly_plan(home, "Alex")
    before = _grocery()
    assert before["Shrimp"] == "3 lbs"
    _swap_whole(home, D1, "lunch")
    on_list = _grocery()
    assert on_list["Shrimp"] == "1 lb", "yesterday's lunch keeps its shrimp"
    assert on_list["Chicken thighs"] == "2 lbs", "two cooks of the new dish"
    ids = {r["date"]: r["id"] for r in _rows(home, "lunch")}
    assert _ledger(ids[D1]) and _ledger(ids[D2])


def test_a_draft_list_is_left_alone(home):
    _separate_cooks(home)
    _swap_whole(home, D1, "lunch")
    assert _grocery() == {}


# ---------- undo ----------


def test_undo_puts_every_day_back(home):
    _separate_cooks(home)
    _opened, out = _swap_whole(home, D1, "lunch")
    back = tools.undo_meal_swap(home, out["entry_id"])
    assert back["status"] == "restored" and [d["date"] for d in back["days"]] == [D1, D2]
    assert _meals(home, "lunch") == {PAST: SHRIMP, D1: SHRIMP, D2: SHRIMP}
    assert all("swapped_from" not in r["derived"] and "swap_group" not in r["derived"] for r in _rows(home, "lunch"))
    assert [_menu_day(home, d)["lunch"]["reason"] for d in (D1, D2)] == ["you like it", "you like it"]


def test_undo_from_any_day_of_the_dish_puts_them_all_back(home):
    _separate_cooks(home)
    _swap_whole(home, D1, "lunch")
    tools.undo_meal_swap(home, _id(home, D2, "lunch"))
    assert _meals(home, "lunch") == {PAST: SHRIMP, D1: SHRIMP, D2: SHRIMP}


def test_undo_on_an_approved_chain_restores_the_chain_and_the_list(home):
    _cook_and_reheat(home)
    tools.approve_weekly_plan(home, "Alex")
    before = _grocery()
    _opened, out = _swap_whole(home, D1, "dinner")
    tools.undo_meal_swap(home, out["entry_id"])
    assert _meals(home, "dinner") == {D1: SHRIMP, D2: SHRIMP, D3: SHRIMP}
    assert _menu_day(home, D2)["dinner"]["leftover_from"]["meal"] == SHRIMP
    assert _grocery() == before


def test_a_day_swapped_on_its_own_afterwards_leaves_the_group(home):
    _separate_cooks(home)
    _swap_whole(home, D1, "lunch")
    d2 = _id(home, D2, "lunch")
    tools.swap_options(home, d2, asker=_asker(_pick("Fish Tacos", protein="Cod")))
    tools.choose_swap_option(home, d2, 0)
    tools.undo_meal_swap(home, _id(home, D1, "lunch"))
    assert _meals(home, "lunch") == {PAST: SHRIMP, D1: SHRIMP, D2: "Fish Tacos"}
    tools.undo_meal_swap(home, _id(home, D2, "lunch"))
    assert _meals(home, "lunch")[D2] == SHRIMP, "and its own Undo still goes back to the original"


# ---------- the one-day swap is unchanged ----------


def test_the_day_steps_swap_still_changes_one_day(home):
    _separate_cooks(home)
    entry_id = _id(home, D1, "lunch")
    tools.swap_options(home, entry_id, asker=_asker(_pick()))
    out = tools.choose_swap_option(home, entry_id, 0)
    assert out["status"] == "swapped" and "days" not in out
    assert _meals(home, "lunch") == {PAST: SHRIMP, D1: NEW, D2: SHRIMP}
    assert "swap_group" not in next(r for r in _rows(home, "lunch") if r["date"] == D1)["derived"]


def test_a_row_with_one_day_ahead_swaps_that_day(home):
    tools.plan_meal(D1, SHRIMP, slot="lunch", weekly_plan_id=home)
    opened, out = _swap_whole(home, D1, "lunch")
    assert opened["dates"] == [D1]
    assert out["status"] == "swapped" and "days" not in out
    assert _meals(home, "lunch") == {D1: NEW}


# ---------- scoping ----------


def test_another_households_dish_is_not_swappable(home):
    _separate_cooks(home)
    entry_id = _id(home, D1, "lunch")
    tools.swap_options(home, entry_id, asker=_asker(_pick()), whole_dish=True)
    with use_household(2):
        with pytest.raises(ValueError, match="No meal"):
            tools.swap_options(home, entry_id, asker=_asker(_pick()), whole_dish=True)
        with pytest.raises(ValueError, match="No meal"):
            tools.choose_swap_option(home, entry_id, 0, whole_dish=True)
    assert _meals(home, "lunch") == {PAST: SHRIMP, D1: SHRIMP, D2: SHRIMP}


def test_the_routes_carry_whole_dish_through(signed_in, home, monkeypatch):
    _separate_cooks(home)
    real = sop.swap_options
    monkeypatch.setattr(tools, "swap_options",
                        lambda plan_id, entry_id, avoid=None, whole_dish=False: real(
                            plan_id, entry_id, avoid=avoid, asker=_asker(_pick()), whole_dish=whole_dish))
    entry_id = _id(home, D1, "lunch")
    opened = signed_in.post(f"/api/week/{START}/swap-options", json={"entry_id": entry_id, "whole_dish": True})
    assert opened.status_code == 200 and opened.json()["dates"] == [D1, D2]
    chosen = signed_in.post(f"/api/week/{START}/swap-choose",
                            json={"entry_id": entry_id, "option": 0, "whole_dish": True})
    assert chosen.status_code == 200 and [d["date"] for d in chosen.json()["days"]] == [D1, D2]
    undone = signed_in.post(f"/api/week/{START}/swap-undo", json={"entry_id": chosen.json()["entry_id"]})
    assert undone.status_code == 200 and [d["date"] for d in undone.json()["days"]] == [D1, D2]
    assert _meals(home, "lunch") == {PAST: SHRIMP, D1: SHRIMP, D2: SHRIMP}


# ---------- the sheet (shell.js) ----------


def _sheet_harness(body: str) -> str:
    fns = ["escapeHtml", "dayName", "slotWord", "isSnackSlot", "joinList", "swapDaysLine",
           "swapPickHtml", "swapSheetBodyHtml", "swapMoveOptions", "swapSheetTitle", "dishShortName",
           "swapWaitLine", "swapWaitHtml", "wkMenuRowHtml", "wkMiniHtml", "wkDaysPhrase", "wkMenuFact",
           "wkRowMetaHtml", "wkRowMeta"]
    stubs = (
        "var WK_ICONS = { chev: '<svg></svg>', swap: '' };\n"
        "var weekState = { days: [] };\n"
        "function reviewDayIsClosed(){ return false; }\n"
        "function mealDisplayName(e){ return e.title; }\n"
    )
    src = "\n".join(_extract(f, SHELL) for f in fns)
    consts = "\n".join(_extract_var(v, SHELL) for v in
                       ("SWAP_WAIT_SECONDS", "SWAP_PLACEHOLDERS", "SWAP_WORKING", "SWAP_SLOT_PLURALS"))
    return stubs + src + "\n" + consts + "\n" + body


def _node(js: str):
    res = nodeharness.run_node(js, timeout=30)
    assert res.returncode == 0, res.stderr
    return json.loads(res.stdout.strip())


@_needs_node
def test_the_sheet_says_which_days_it_changes():
    out = _node(_sheet_harness(
        "var base = { date: '2026-09-24', slot: 'lunch', name: 'Shrimp', view: 'picks', options: [], trouble: '' };\n"
        "console.log(JSON.stringify([\n"
        "  swapSheetBodyHtml(Object.assign({}, base, { dates: ['2026-09-24', '2026-09-25'] })),\n"
        "  swapSheetBodyHtml(Object.assign({}, base, { dates: ['2026-09-21', '2026-09-23', '2026-09-25'] })),\n"
        "  swapSheetBodyHtml(Object.assign({}, base, { dates: ['2026-09-21', '2026-09-22', '2026-09-23', '2026-09-24', '2026-09-25'] })),\n"
        "  swapSheetBodyHtml(Object.assign({}, base, { dates: ['2026-09-24'] }))\n"
        "]));"
    ))
    two, three, five, one = out
    assert "Swapping Thursday and Friday’s lunch." in two
    assert "Swapping Monday, Wednesday and Friday’s lunch." in three
    assert "Swapping all 5 lunches." in five
    assert "Swapping" not in one and "Thursday · lunch" in one, "one day reads as it always has"


@_needs_node
def test_a_tapped_pick_shows_it_is_working_and_the_rest_wait():
    out = _node(_sheet_harness(
        "var st = { date: '2026-09-24', slot: 'lunch', name: 'Shrimp', view: 'picks', trouble: '', busy: true,\n"
        "  pending: 1, options: [{index: 0, meal: 'A', reason: 'r', minutes: 20}, {index: 1, meal: 'B', reason: 'r', minutes: 30}] };\n"
        "console.log(JSON.stringify(swapSheetBodyHtml(st)));"
    ))
    buttons = {m.group(2): (m.group(1) + m.group(3), m.group(4))
               for m in re.finditer(r'<button([^>]*)data-wk-swap-pick="(\d)"([^>]*)>(.*?)</button>', out)}
    (working_tag, working), (other_tag, other) = buttons["1"], buttons["0"]
    assert "is-working" in working_tag and "disabled" in working_tag
    assert "wk-swap-spinner" in working and "Adding it to the week…" in working and "wk-swap-pick-chev" not in working
    assert "disabled" in other_tag and "is-waiting" in other_tag and "Adding" not in other
    assert out.count("disabled") == 3, "every button in the sheet waits — both picks and the chat line"


def test_the_rows_swap_asks_for_the_whole_dish_and_the_day_steps_does_not():
    row = _extract("wkMenuRowHtml", SHELL)
    assert "data-wk-swap-dish" in row
    opener = _extract("openSwapSheet", SHELL)
    assert "whole_dish" in opener
    pick = _extract("runSwapPick", SHELL)
    assert "whole_dish: !!st.wholeDish" in pick and "st.pending = index" in pick
    assert "(out.days || [out.day]).forEach(spliceSwappedDay)" in pick
    dismiss = _extract("dismissSwapSheet", SHELL)
    assert "busy" in dismiss, "a sheet writing a pick can't be swiped away"
    assert "addEventListener('click', dismissSwapSheet)" in _extract("buildSwapSheet", SHELL)
    undo = _extract("runSwapUndo", SHELL)
    assert "(data.days || [data.day]).forEach(spliceSwappedDay)" in undo
