"""
Three small changes to the draft (Emily, 2026-09-26):

  1. The Swap sheet says, under its title, "If one’s close but not quite
     right, you can always tweak it after." — on a one-day and a
     whole-dish sheet, not on the move view and not on the Tweak sheet.
  2. A "What we're eating" row is the dish, its days and its time: no
     reason, no "Mexican, as asked". A dish whose days are all behind us
     is greyed and reads "Had Thu, Fri"; one with some behind names only
     the days still ahead.
  3. A blank day on Which days (left out on purpose, not gone by, not
     away) says "Nothing planned yet! Want to get a plan built?" with
     "Build a plan", which fills ONLY that day's empty meals
     (POST /api/week/{week}/fill-day → swap_in_place.fill_empty_day) — or,
     when nothing in the week is planned, opens the intake (replanWeek).
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
from test_week_seven_tiles import _extract, _extract_var
from test_plan_cards_2026_09_18 import _prelude, _entry, _day

sip = importlib.import_module("app.tools.swap_in_place")

REPO = Path(__file__).resolve().parent.parent
SHELL_JS = (REPO / "static" / "shell.js").read_text(encoding="utf-8")
SHELL_CSS = (REPO / "static" / "shell.css").read_text(encoding="utf-8")
_needs_node = pytest.mark.skipif(shutil.which("node") is None, reason="node runs the renderers")

TWEAK_LINE = "If one’s close but not quite right, you can always tweak it after."
BLANK_LINE = "Nothing planned yet! Want to get a plan built?"

# Mon 21 – Sun 27 Sept 2026, as the harness's dayName stub reads them.
_MON, _TUE, _WED, _THU, _FRI, _SAT, _SUN = (f"2026-09-{d}" for d in range(21, 28))


def _run(js: str):
    res = nodeharness.run_node(js, timeout=30)
    assert res.returncode == 0, res.stderr
    return json.loads(res.stdout.strip())


def _css_rule(selector: str) -> str:
    start = SHELL_CSS.index(selector + " {")
    return SHELL_CSS[start:SHELL_CSS.index("}", start)]


# ---------------------------------------------------------------------------
# 1. The Swap sheet's line
# ---------------------------------------------------------------------------

@_needs_node
def test_the_swap_sheet_says_you_can_tweak_it_after_on_one_day_and_whole_dish_sheets():
    base = {"date": _TUE, "slot": "dinner", "name": "Tacos", "view": "picks", "trouble": "",
            "options": [{"index": 0, "meal": "Fajitas", "reason": "same tortillas", "minutes": 25}]}
    whole = dict(base, dates=[_TUE, _WED])
    move = dict(base, view="move")
    trouble = dict(base, options=None, trouble="I couldn’t think of options just now.")
    out = _run(_prelude() + f"weekState.days = [];\nconsole.log(JSON.stringify(["
               f"swapSheetBodyHtml({json.dumps(base)}), swapSheetBodyHtml({json.dumps(whole)}),"
               f" swapSheetBodyHtml({json.dumps(move)}), swapSheetBodyHtml({json.dumps(trouble)})]));")
    one, dish, moving, no_picks = out
    assert TWEAK_LINE not in no_picks, "no picks came back: there is no one to be close"
    line = f'<p class="wk-swap-tweak-note">{TWEAK_LINE}</p>'
    for html in (one, dish):
        assert line in html
        # Under the title (and the whole dish's days line), above the picks.
        assert html.index('id="wk-swap-title"') < html.index(line) < html.index('class="wk-swap-picks"')
    assert dish.index("Swapping Tuesday and Wednesday’s dinner.") < dish.index(line)
    assert TWEAK_LINE not in moving, "the move view is a different question"


def test_the_tweak_sheet_does_not_carry_the_line_and_it_is_styled_as_supporting_copy():
    assert TWEAK_LINE not in _extract("tweakSheetBodyHtml", SHELL_JS)
    rule = _css_rule(".wk-swap-tweak-note")
    assert "color: var(--ink-secondary)" in rule and "font-size: 15px" in rule


# ---------------------------------------------------------------------------
# 2. What we're eating: title, days, time
# ---------------------------------------------------------------------------

def _menu(days):
    return _run(_prelude() + f"weekState.days = {json.dumps(days)};\n"
                f"console.log(JSON.stringify(wkMenuHtml({json.dumps(days)})));")


def _menu_row(html: str, name: str) -> str:
    start = html.rindex('<div class="wk-row wk-menu-row', 0, html.index(">" + name))
    nxt = html.find('<div class="wk-row wk-menu-row', start + 1)
    return html[start:nxt if nxt != -1 else len(html)]


@_needs_node
def test_a_menu_row_says_days_then_minutes_with_no_reason_and_no_asked_fact():
    days = [
        _day(_MON, dinner=_entry("Salmon and rice", entry_id=1, meta="30 min", asked="Mexican, as asked",
                                 reason="Mexican, as you asked")),
        _day(_TUE, dinner=_entry("Tofu stir-fry", entry_id=2, meta="20 min", reason="Lighter than the chops.")),
        _day(_WED, dinner=_entry("Pizza night", entry_id=3, meta=None, source="takeout")),
        _day(_THU, dinner=_entry("Soup", entry_id=4, meta=None)),
    ]
    html = _menu(days)
    assert ">Monday · 30 min</span>" in _menu_row(html, "Salmon and rice")
    assert ">Tuesday · 20 min</span>" in _menu_row(html, "Tofu stir-fry")
    assert "Mexican" not in html and "Lighter" not in html, "no asked fact, no reason"
    # No minutes: the minutes' place keeps takeout's word, else says nothing.
    assert ">Wednesday · takeout</span>" in _menu_row(html, "Pizza night")
    assert '<span class="wk-row-meta">Thursday</span>' in _menu_row(html, "Soup")


@_needs_node
def test_a_dish_all_behind_us_is_greyed_and_says_had_with_no_buttons():
    days = [
        _day(_MON, past=True, lunch=_entry("Chickpea jars", entry_id=1, meta="10 min")),
        _day(_TUE, past=True, lunch=_entry("Chickpea jars", entry_id=2, meta="10 min"),
             dinner=_entry("Tacos", entry_id=3, meta="25 min")),
        _day(_WED, iso_today=True, dinner=_entry("Soup", entry_id=4, meta="40 min")),
    ]
    html = _menu(days)
    jars, tacos, soup = (_menu_row(html, n) for n in ("Chickpea jars", "Tacos", "Soup"))
    assert 'class="wk-row wk-menu-row has-foot is-past"' in jars
    assert ">Had Mon, Tue · 10 min</span>" in jars
    assert ">Had Tuesday · 25 min</span>" in tacos and "is-past" in tacos
    for row in (jars, tacos):
        assert "data-wk-swap-sheet" not in row and "data-wk-tweak" not in row
    assert "is-past" not in soup and ">Wednesday · 40 min</span>" in soup and "data-wk-swap-sheet" in soup


@_needs_node
def test_a_dish_part_behind_us_names_only_the_days_ahead_and_is_not_greyed():
    days = [
        _day(_MON, past=True, breakfast=_entry("Oats", entry_id=1, meta="5 min")),
        _day(_TUE, past=True, breakfast=_entry("Oats", entry_id=2, meta="5 min")),
        _day(_WED, iso_today=True, breakfast=_entry("Oats", entry_id=3, meta="5 min")),
        _day(_THU, breakfast=_entry("Oats", entry_id=4, meta="5 min")),
        _day(_FRI, breakfast=_entry("Oats", entry_id=5, meta="5 min")),
    ]
    oats = _menu_row(_menu(days), "Oats")
    assert "is-past" not in oats
    assert ">Wed–Fri · 5 min</span>" in oats
    assert 'data-wk-swap-dish="2026-09-23,2026-09-24,2026-09-25"' in oats


def test_the_greyed_row_uses_the_done_ink():
    assert ".wk-menu-row.is-past .wk-row-meta { color: var(--ink-done); }" in SHELL_CSS


# ---------------------------------------------------------------------------
# 3. A blank day on Which days
# ---------------------------------------------------------------------------

def _skipped(entry_id):
    return {"title": "Not planned", "meta": None, "source": "empty", "state": "planned_empty",
            "reason": "Not planned — you left this day out.", "entry_id": entry_id, "skipped": True,
            "can_fill": True}


def _out(entry_id, **kw):
    e = {"title": "Out — nothing to cook", "meta": None, "source": "empty", "state": "planned_empty",
         "reason": "", "entry_id": entry_id, "skipped": False, "can_fill": False}
    e.update(kw)
    return e


def _blank(date, past=False, maker=_skipped):
    return _day(date, past=past, breakfast=maker(1), lunch=maker(2), dinner=maker(3))


def _cards(days, done=False):
    return _run(_prelude() + f"weekState.days = {json.dumps(days)};\nconsole.log(JSON.stringify("
                f"{json.dumps(days)}.map(function (d, i) {{ return wkDayCardHtml(d, i, {{ done: {json.dumps(done)}, swapLabel: 'Swap' }}); }})));")


@_needs_node
def test_a_blank_day_ahead_offers_build_a_plan_and_its_head_says_0_meals():
    [card] = _cards([_blank(_SAT)])
    assert f"<p>{BLANK_LINE}</p>" in card
    assert ('<button type="button" class="btn-primary wk-build-day" data-wk-build-day="2026-09-26">'
            'Build a plan</button>') in card
    assert '<span class="wk-card-count">0 meals</span>' in card
    assert "Not planned" not in card, "the head doesn't say it twice"


@_needs_node
def test_a_past_blank_day_an_away_day_and_an_out_day_get_no_button():
    away = _blank(_SAT, maker=lambda i: _out(i, need="away"))
    out = _blank(_SUN, maker=_out)
    past, away_card, out_card = _cards([_blank(_MON, past=True), away, out])
    for card in (past, away_card, out_card):
        assert "data-wk-build-day" not in card and BLANK_LINE not in card
    assert '<span class="wk-card-count">Not planned</span>' in past, "a past blank day reads as it did"
    assert "Away — nothing planned, nothing bought." in away_card


@_needs_node
def test_the_approved_roots_card_and_a_planned_day_get_no_button():
    [root] = _cards([_blank(_SAT)], done=True)
    assert "data-wk-build-day" not in root
    [planned] = _cards([_day(_SAT, dinner=_entry("Tacos", entry_id=9))])
    assert "data-wk-build-day" not in planned


def test_build_a_plan_fills_the_day_or_opens_the_intake_for_an_empty_week():
    run = _extract("runBuildDay", SHELL_JS)
    assert "if (!wkWeekHasMeals(weekState.days)) { replanWeek(); return; }" in run
    assert "'/fill-day'" in run and "JSON.stringify({ date: day.date })" in run
    assert "spliceSwappedDay(data.day)" in run and "await loadWeekMenu(panel)" in run
    assert "steps.querySelectorAll('[data-wk-build-day]')" in SHELL_JS
    assert _css_rule(".wk-card-blank p").count("var(--ink-secondary)") == 1


# ---------------------------------------------------------------------------
# 3. The server: fill-day
# ---------------------------------------------------------------------------

TODAY = household_today()
START = (TODAY - datetime.timedelta(days=1)).isoformat()
PAST = START
D1 = (TODAY + datetime.timedelta(days=1)).isoformat()
D2 = (TODAY + datetime.timedelta(days=2)).isoformat()


def _dish(name):
    return {
        "meal_name": name, "reason": "Easy on a quiet day.",
        "ingredients": [{"item": "Eggs", "qty": "1 dozen", "category": "dairy"},
                        {"item": "Spinach", "qty": "1 bag", "category": "produce"}],
        "instructions": ["Cook it."], "food_groups": ["protein", "vegetable", "carb"],
        "main_protein": "Eggs", "prep_time_minutes": 5, "cook_time_minutes": 10, "default_servings": 2,
    }


def _picker(seen):
    def pick(context):
        seen.append(context)
        return _dish(f"Filled {context['slot']}")
    return pick


def _skip_day(plan_id, day):
    for slot in ("breakfast", "lunch", "dinner"):
        tools.plan_slot_empty(weekly_plan_id=plan_id, meal_date=day, slot=slot,
                              reason=tools.SKIPPED_DAY_REASON,
                              derived_from={"constraint": tools.SKIPPED_DAY_CONSTRAINT})


def _day_rows(plan_id, day):
    conn = get_conn()
    rows = conn.execute(
        "SELECT mpe.slot, mpe.slot_state, COALESCE(r.name, mpe.freeform_meal) AS meal "
        "FROM meal_plan_entries mpe LEFT JOIN recipes r ON r.id = mpe.recipe_id "
        "WHERE mpe.weekly_plan_id = ? AND mpe.date = ? ORDER BY mpe.slot",
        (plan_id, day),
    ).fetchall()
    conn.close()
    return [(r["slot"], r["slot_state"], r["meal"]) for r in rows]


@pytest.fixture
def week():
    tools.add_member("Alex")
    plan_id = tools.create_weekly_plan(START)["weekly_plan_id"]
    tools.plan_meal(D1, "Tacos", slot="dinner", weekly_plan_id=plan_id)
    _skip_day(plan_id, D2)
    return plan_id


def test_the_menu_marks_a_left_out_day_as_skipped(week):
    day = next(d for d in tools.get_week_menu(week)["days"] if d["date"] == D2)
    assert all(day[s]["skipped"] is True for s in ("breakfast", "lunch", "dinner"))


def test_fill_day_plans_only_the_left_out_meals_and_leaves_the_rest_alone(week):
    before = _day_rows(week, D1)
    seen = []
    out = sip.fill_empty_day(week, D2, picker=_picker(seen))
    assert out["status"] == "filled"
    assert [f["slot"] for f in out["filled"]] == ["breakfast", "lunch", "dinner"]
    assert sorted(_day_rows(week, D2)) == [("breakfast", "planned", "Filled breakfast"),
                                           ("dinner", "planned", "Filled dinner"),
                                           ("lunch", "planned", "Filled lunch")]
    assert _day_rows(week, D1) == before, "Tacos on the other day untouched"
    assert out["day"]["date"] == D2 and out["day"]["dinner"]["title"] == "Filled dinner"
    # Each pick is told the rest of the week, including what was just filled.
    assert any("Tacos" in line for line in seen[0]["week_other_dishes"])
    assert any("Filled breakfast" in line for line in seen[-1]["week_other_dishes"])
    assert seen[0]["replacing"] == "" and "note" in seen[0]


def test_fill_day_refuses_a_planned_day_a_past_day_and_an_out_night(week):
    for day in (D1,):
        out = sip.fill_empty_day(week, day, picker=_picker([]))
        assert out == {"status": "refused", "message": sip.FILL_NOTHING}
    _skip_day(week, PAST)
    gone = sip.fill_empty_day(week, PAST, picker=_picker([]))
    assert gone["status"] == "refused" and _day_rows(week, PAST)[0][1] == "planned_empty"
    # An out night is a different planned_empty: an answer, not a gap.
    D3 = (TODAY + datetime.timedelta(days=3)).isoformat()
    tools.plan_slot_empty(weekly_plan_id=week, meal_date=D3, slot="dinner",
                          reason="You’re out.", derived_from={"tags": ["out"], "constraint": "nobody_home"})
    tools.plan_meal(D3, "Oats", slot="breakfast", weekly_plan_id=week)
    tools.plan_meal(D3, "Wraps", slot="lunch", weekly_plan_id=week)
    assert sip.fill_empty_day(week, D3, picker=_picker([]))["status"] == "refused"
    assert ("dinner", "planned_empty", None) in _day_rows(week, D3)


def test_the_fill_day_route_plans_the_day(week, signed_in, monkeypatch):
    monkeypatch.setattr(sip, "_pick_replacement", _picker([]))
    res = signed_in.post(f"/api/week/{START}/fill-day", json={"date": D2})
    assert res.status_code == 200 and res.json()["status"] == "filled"
    assert all(state == "planned" for _slot, state, _meal in _day_rows(week, D2))
    bad = signed_in.post(f"/api/week/{START}/fill-day", json={"date": "Sunday"})
    assert bad.status_code == 400


# ---------------------------------------------------------------------------
# Verifier's review (2026-09-26): zero-count meals, rowless slots, partial
# fills, a day outside the week
# ---------------------------------------------------------------------------

def test_a_meal_the_household_wants_none_of_is_never_filled_or_offered(week):
    tools.set_household_meal_preferences(breakfasts_per_week=0, lunches_per_week=0, mark_complete=False)
    day = next(d for d in tools.get_week_menu(week)["days"] if d["date"] == D2)
    assert [day[s]["can_fill"] for s in ("breakfast", "lunch", "dinner")] == [False, False, True]
    assert day["breakfast"]["skipped"] is True, "still reads as left out"
    out = sip.fill_empty_day(week, D2, picker=_picker([]))
    assert [f["slot"] for f in out["filled"]] == ["dinner"]
    assert sorted(_day_rows(week, D2)) == [("breakfast", "planned_empty", None),
                                           ("dinner", "planned", "Filled dinner"),
                                           ("lunch", "planned_empty", None)]


@_needs_node
def test_no_button_when_the_only_left_out_meals_are_ones_they_want_none_of():
    def none_wanted(i):
        return dict(_skipped(i), can_fill=False)
    [card] = _cards([_blank(_SAT, maker=none_wanted)])
    assert "data-wk-build-day" not in card


def test_a_slot_with_no_row_is_not_filled(week):
    D3 = (TODAY + datetime.timedelta(days=3)).isoformat()
    tools.plan_slot_empty(weekly_plan_id=week, meal_date=D3, slot="dinner",
                          reason=tools.SKIPPED_DAY_REASON,
                          derived_from={"constraint": tools.SKIPPED_DAY_CONSTRAINT})
    out = sip.fill_empty_day(week, D3, picker=_picker([]))
    assert [f["slot"] for f in out["filled"]] == ["dinner"]
    assert _day_rows(week, D3) == [("dinner", "planned", "Filled dinner")]


def test_a_slot_that_fails_part_way_leaves_what_filled_and_says_partial(week):
    def flaky(context):
        if context["slot"] == "lunch":
            raise RuntimeError("model fell over")
        return _dish(f"Filled {context['slot']}")

    out = sip.fill_empty_day(week, D2, picker=flaky)
    assert out["status"] == "filled" and out["partial"] is True
    assert [f["slot"] for f in out["filled"]] == ["breakfast", "dinner"]
    assert ("lunch", "planned_empty", None) in _day_rows(week, D2)
    assert out["day"]["breakfast"]["title"] == "Filled breakfast"

    def broken(context):
        raise RuntimeError("model fell over")
    D3 = (TODAY + datetime.timedelta(days=3)).isoformat()
    _skip_day(week, D3)
    with pytest.raises(RuntimeError):
        sip.fill_empty_day(week, D3, picker=broken)


def test_a_day_outside_the_week_is_refused_before_any_model_call(week):
    seen = []
    later = (TODAY + datetime.timedelta(days=30)).isoformat()
    out = sip.fill_empty_day(week, later, picker=_picker(seen))
    assert out == {"status": "refused", "message": sip.FILL_NOT_THIS_WEEK} and seen == []


def test_the_screen_reloads_after_a_failed_fill_and_names_a_partial_one():
    run = _extract("runBuildDay", SHELL_JS)
    catch = run[run.index("} catch (err) {"):]
    assert "await loadWeekMenu(panel)" in catch
    assert "data.partial" in run and "' planned for ' + dayWord" in run
    assert "e && e.can_fill" in _extract("wkDayCanBuild", SHELL_JS)
