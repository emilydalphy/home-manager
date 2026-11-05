"""
The Plan day bar runs on into the plans either side (Emily, 2026-10-31, on
Saturday the 31st — the last day of her week, with Nov 1–7 drafted: "I want
to be able to see the meal plan that starts tomorrow but I can't. It should
let me scroll through the dates that are already planned.").

  1. get_week_menu carries `adjacent_plans`: the next plan (draft or
     approved) and the previous one, each with only its own dates, on the
     household's clock. One per side; never an invented day; an expired
     draft and a retired plan are not neighbours.
  2. The day bar draws them in date order across a thin join — previous,
     this plan, next — with All first; their tiles carry the plan's id and
     the date, and say "draft" to a screen reader on a draft.
  3. Tapping one pins that plan by id and opens it on that day; the pin
     outlives the plan's approval (showPlanKeep) unless the plan holds
     today, which is the tab's own default anyway.
  4. On a plan's last day the bar opens with tomorrow's tile in view.
"""
from __future__ import annotations

import datetime as _dt
import json
import re
import shutil

import pytest

from app.db import get_conn
from app.tools import weekly_plan as _weekly_plan
from conftest import household_today
from tests import nodeharness
from test_week_seven_tiles import _extract
from test_plan_cards_2026_09_18 import _DAYNAME_STUB, SHELL_JS

_needs_node = pytest.mark.skipif(shutil.which("node") is None, reason="node is needed to run the renderers")


# ---------------------------------------------------------------------------
# 1. The server: adjacent_plans
# ---------------------------------------------------------------------------

def _plan(start: _dt.date, day_count: int, status: str = "approved") -> int:
    conn = get_conn()
    cur = conn.execute(
        "INSERT INTO weekly_plans (household_id, week_start_date, status, content_start_date, day_count) "
        "VALUES (1, ?, ?, ?, ?)",
        (start.isoformat(), status, start.isoformat(), day_count),
    )
    plan_id = cur.lastrowid
    for i in range(day_count):
        conn.execute(
            "INSERT INTO meal_plan_entries (household_id, weekly_plan_id, date, slot, freeform_meal) "
            "VALUES (1, ?, ?, 'dinner', 'Chili')",
            (plan_id, (start + _dt.timedelta(days=i)).isoformat()),
        )
    conn.commit()
    conn.close()
    return plan_id


def _dates(side: dict | None) -> list[str]:
    return [d["date"] for d in (side or {}).get("days", [])]


def test_the_week_ending_today_names_next_weeks_draft_and_its_days():
    today = household_today()
    # Emily's screen: a plan whose last day is today, a draft from tomorrow.
    current = _plan(today - _dt.timedelta(days=5), 6)
    draft = _plan(today + _dt.timedelta(days=1), 7, status="draft")
    menu = _weekly_plan.get_week_menu(current)
    adj = menu["adjacent_plans"]
    assert adj["next"]["weekly_plan_id"] == draft
    assert adj["next"]["status"] == "draft"
    assert _dates(adj["next"]) == [(today + _dt.timedelta(days=i)).isoformat() for i in range(1, 8)]
    first = adj["next"]["days"][0]
    assert first["is_today"] is False and first["is_past"] is False
    assert adj["previous"] is None, "no plan before this one: no invented days"


def test_the_next_plans_payload_names_this_one_as_its_previous():
    today = household_today()
    current = _plan(today - _dt.timedelta(days=5), 6)
    draft = _plan(today + _dt.timedelta(days=1), 7, status="draft")
    adj = _weekly_plan.get_week_menu(draft)["adjacent_plans"]
    assert adj["previous"]["weekly_plan_id"] == current
    assert adj["previous"]["status"] == "approved"
    last = adj["previous"]["days"][-1]
    assert last["date"] == today.isoformat() and last["is_today"] is True
    assert adj["next"] is None


def test_an_approved_next_week_is_a_neighbour_too_and_only_the_nearest_one_per_side():
    today = household_today()
    older = _plan(today - _dt.timedelta(days=14), 7)
    prev = _plan(today - _dt.timedelta(days=7), 7)
    current = _plan(today, 7)
    nxt = _plan(today + _dt.timedelta(days=7), 7)
    _plan(today + _dt.timedelta(days=14), 7)
    adj = _weekly_plan.get_week_menu(current)["adjacent_plans"]
    assert adj["next"]["weekly_plan_id"] == nxt
    assert adj["previous"]["weekly_plan_id"] == prev
    assert older not in (adj["next"]["weekly_plan_id"], adj["previous"]["weekly_plan_id"])
    assert all(d["is_past"] for d in adj["previous"]["days"])


def test_a_gap_between_plans_is_a_gap_not_empty_days():
    today = household_today()
    current = _plan(today, 3)
    later = _plan(today + _dt.timedelta(days=10), 4, status="draft")
    adj = _weekly_plan.get_week_menu(current)["adjacent_plans"]
    assert adj["next"]["weekly_plan_id"] == later
    assert _dates(adj["next"])[0] == (today + _dt.timedelta(days=10)).isoformat()
    assert len(adj["next"]["days"]) == 4


def test_retired_plans_and_expired_drafts_are_not_neighbours():
    today = household_today()
    current = _plan(today, 7)
    retired = _plan(today + _dt.timedelta(days=7), 7)
    conn = get_conn()
    conn.execute("UPDATE weekly_plans SET status = 'retired' WHERE id = ?", (retired,))
    conn.commit()
    conn.close()
    _plan(today - _dt.timedelta(days=7), 7, status="draft")  # all gone by
    adj = _weekly_plan.adjacent_plans(_weekly_plan.get_weekly_plan(current), today.isoformat())
    assert adj == {"previous": None, "next": None}


def test_a_draft_beats_an_approved_plan_starting_the_same_day_and_only_new_dates_are_carried():
    today = household_today()
    current = _plan(today, 4)
    _plan(today + _dt.timedelta(days=4), 7)
    # A draft drawn over this plan's last two days and on past it: only the
    # days after this plan are the bar's to add.
    draft = _plan(today + _dt.timedelta(days=2), 7, status="draft")
    adj = _weekly_plan.adjacent_plans(_weekly_plan.get_weekly_plan(current), today.isoformat())
    assert adj["next"]["weekly_plan_id"] == draft, "the earliest start wins"
    assert _dates(adj["next"])[0] == (today + _dt.timedelta(days=4)).isoformat()
    # And on a tie of start, the draft.
    tie = _plan(today + _dt.timedelta(days=4), 7)
    conn = get_conn()
    conn.execute("UPDATE weekly_plans SET status = 'retired' WHERE id = ?", (draft,))
    conn.commit()
    conn.close()
    tie_draft = _plan(today + _dt.timedelta(days=4), 7, status="draft")
    adj = _weekly_plan.adjacent_plans(_weekly_plan.get_weekly_plan(current), today.isoformat())
    assert adj["next"]["weekly_plan_id"] == tie_draft
    assert tie != tie_draft


def test_the_week_menu_route_carries_it(signed_in):
    today = household_today()
    current = _plan(today - _dt.timedelta(days=5), 6)
    draft = _plan(today + _dt.timedelta(days=1), 7, status="draft")
    res = signed_in.get(f"/api/week-menu?weekly_plan_id={current}")
    assert res.status_code == 200
    assert res.json()["adjacent_plans"]["next"]["weekly_plan_id"] == draft


# ---------------------------------------------------------------------------
# 2–4. The client
# ---------------------------------------------------------------------------

def _run(script: str):
    res = nodeharness.run_node(script, timeout=30)
    assert res.returncode == 0, f"node failed: {res.stderr}"
    return json.loads(res.stdout.strip().splitlines()[-1])


def _prelude() -> str:
    return (
        _extract("escapeHtml", SHELL_JS) + "\n" + _DAYNAME_STUB
        + "var weekState = { planDay: null, planDayDate: null, planBarAnchor: null, showPlanId: null,"
          " showPlanKeep: false, showWeekStart: '2026-01-05' };\n"
        + "var loads = 0; function loadWeekMenu() { loads++; }\n"
        + "function todayLocalStr() { return '2026-10-31'; }\n"
        + "\n".join(_extract(n, SHELL_JS) for n in (
            "planDayBarHtml", "planBarAnchorFor", "planOpenOtherDay", "planDayBarReveal"))
        + "\n"
    )


# Mon Oct 26 – Sat Oct 31 2026, today the Saturday; a draft Nov 1–7, and a
# plan the week before.
_CUR = [{"date": f"2026-10-{d}", "isPast": d < 31, "isToday": d == 31} for d in range(26, 32)]
_NEXT = {"weekly_plan_id": 42, "status": "draft",
         "days": [{"date": f"2026-11-0{d}", "is_past": False, "is_today": False} for d in range(1, 8)]}
# Past days are hidden (Emily, 2026-11-05), so the plan behind only shows on
# the bar while its days are still ahead: viewing a week that hasn't started.
_PREV = {"weekly_plan_id": 41, "status": "approved",
         "days": [{"date": f"2026-10-{d}", "is_past": False, "is_today": False} for d in range(19, 26)]}
_AHEAD = [dict(d, isPast=False, isToday=False) for d in _CUR]


def _bar(sel="null", adjacent=None, days=None) -> str:
    adj = json.dumps(adjacent if adjacent is not None else {"previous": _PREV, "next": _NEXT})
    days = days if days is not None else _AHEAD
    return _run(_prelude() + f"console.log(JSON.stringify(planDayBarHtml({json.dumps(days)}, {sel}, {adj})));")


@_needs_node
def test_past_days_are_hidden_on_the_bar_on_both_plans():
    gone = dict(_PREV, days=[dict(d, is_past=True) for d in _PREV["days"]])
    html = _bar(adjacent={"previous": gone, "next": _NEXT}, days=_CUR)
    dates = re.findall(r'data-date="([^"]*)"', html)
    assert dates == ["2026-10-31"] + [f"2026-11-0{d}" for d in range(1, 8)]
    # A past day opened from somewhere else keeps its segment.
    html = _bar(sel="1", adjacent={"previous": None, "next": None}, days=_CUR)
    assert re.findall(r'data-date="([^"]*)"', html) == ["2026-10-27", "2026-10-31"]
    # A plan with no day left still shows its days, or there'd be no bar.
    done = [dict(d, isPast=True, isToday=False) for d in _CUR]
    html = _bar(adjacent={"previous": None, "next": None}, days=done)
    assert len(re.findall(r'data-date="([^"]*)"', html)) == 6


@_needs_node
def test_the_bar_runs_previous_then_this_plan_then_next_in_date_order_with_all_first():
    html = _bar()
    dates = re.findall(r'data-date="([^"]*)"', html)
    assert dates == ([f"2026-10-{d}" for d in range(19, 32)] + [f"2026-11-0{d}" for d in range(1, 8)])
    assert html.index('data-plan-day="week"') < html.index('data-date="2026-10-19"'), "All comes first"
    assert html.count('class="wk-daybar-join"') == 2, "one quiet join at each seam"
    assert "is-long" in html, "it scrolls"
    # Only this plan's tiles are its days; the others carry their plan.
    assert re.findall(r'data-plan-day="(\d+)"', html) == ["0", "1", "2", "3", "4", "5"]
    assert html.count('data-plan-other="42"') == 7
    assert html.count('data-plan-other="41"') == 7
    assert re.search(r'data-plan-other="42"[^>]*aria-label="[^"]*, draft"', html), "a draft says so"
    assert not re.search(r'data-plan-other="41"[^>]*aria-label="[^"]*, draft"', html)


@_needs_node
def test_without_neighbours_the_bar_is_as_it_was():
    html = _bar(adjacent={"previous": None, "next": None})
    assert "wk-daybar-join" not in html and "data-plan-other" not in html
    assert "is-long" not in html, "six days and All fit without scrolling"


@_needs_node
def test_a_date_this_plan_already_draws_is_never_drawn_twice():
    overlap = dict(_NEXT, days=[{"date": "2026-10-31", "is_past": False, "is_today": True}] + _NEXT["days"])
    html = _bar(adjacent={"previous": None, "next": overlap})
    assert html.count('data-date="2026-10-31"') == 1


@_needs_node
def test_tapping_a_next_week_tile_pins_that_plan_and_opens_it_on_that_day():
    out = _run(_prelude() + """
      function tile(attrs, cls) {
        return { classList: { contains: function (c) { return (cls || '').indexOf(c) !== -1; } },
                 getAttribute: function (k) { return attrs[k] == null ? null : attrs[k]; },
                 closest: function () { return bar; } };
      }
      var todayInNext = false;
      var bar = { querySelector: function (q) { return todayInNext && q.indexOf('data-plan-other="42"') !== -1 ? {} : null; } };
      planOpenOtherDay(null, tile({ 'data-plan-other': '42', 'data-date': '2026-11-01' }));
      var a = JSON.parse(JSON.stringify(weekState));
      todayInNext = true;
      planOpenOtherDay(null, tile({ 'data-plan-other': '42', 'data-date': '2026-11-02' }));
      console.log(JSON.stringify({ a: a, b: weekState, loads: loads }));
    """)
    a, b = out["a"], out["b"]
    assert a["showPlanId"] == 42
    assert a["planDayDate"] == {"planId": 42, "date": "2026-11-01"}, "the day belongs to that plan"
    assert a["showPlanOn"] == "2026-10-31"
    assert a["showWeekStart"] is None, "a plan pin wins over the date pin"
    assert a["showPlanKeep"] is True, "stays on next week through its approval"
    assert b["showPlanKeep"] is False, "a plan holding today is the tab's default: let go once approved"
    assert out["loads"] == 2


def _reveal(script: str):
    # A fake bar: 390px wide with 16px padding, 46px tiles 4px apart, All
    # sticky at the left. Rects follow bar.scrollLeft, like the real thing.
    return _run(_prelude() + """
      function getComputedStyle() { return { paddingRight: '16px' }; }
      function el(cls, attrs, i) {
        var e = { cls: cls, attrs: attrs || {}, i: i,
          classList: { contains: function (c) { return (' ' + cls + ' ').indexOf(' ' + c + ' ') !== -1; } },
          getAttribute: function (k) { return e.attrs[k] == null ? null : e.attrs[k]; },
          getBoundingClientRect: function () {
            if (cls.indexOf('wk-daybar-week') !== -1) return { left: 16, right: 62 };
            var left = 16 + i * 50 - bar.scrollLeft;
            return { left: left, right: left + (cls.indexOf('join') !== -1 ? 1 : 46) };
          } };
        return e;
      }
      var kids = [];
      function build(html) {
        kids = []; var i = 0;
        html.replace(/<(button|span) type="button" class="([^"]*)"([^>]*)>|<span class="(wk-daybar-join)"/g,
          function (m, tag, cls, rest, join) {
            if (join) { kids.push(el('wk-daybar-join', {}, i++)); return m; }
            var attrs = {}; rest.replace(/(data-[a-z-]+)="([^"]*)"/g, function (_, k, v) { attrs[k] = v; });
            kids.push(el(cls, attrs, i++)); return m;
          });
        kids.forEach(function (k, n) { k.nextElementSibling = kids[n + 1] || null; });
      }
      function matches(k, q) {
        if (q === '.wk-daybar-week') return k.classList.contains('wk-daybar-week');
        if (q === '.wk-tile.is-selected[data-date]') return k.classList.contains('is-selected') && k.attrs['data-date'];
        if (q === '.wk-tile.is-today[data-plan-day]') return k.classList.contains('is-today') && k.attrs['data-plan-day'];
        if (q === '[data-plan-day="0"]') return k.attrs['data-plan-day'] === '0';
        var m = q.match(/data-date="([^"]*)"/); if (m) return k.attrs['data-date'] === m[1];
        return false;
      }
      var bar = { scrollLeft: 0,
        getBoundingClientRect: function () { return { left: 0, right: 390 }; },
        querySelector: function (q) { return kids.filter(function (k) { return matches(k, q); })[0] || null; } };
      var steps = { querySelector: function () { return bar; } };
      function visible(date) {
        var t = bar.querySelector('[data-date="' + date + '"]').getBoundingClientRect();
        return t.left >= 66 && t.right <= 374;
      }
    """ + script)


_CUR_NEXT = json.dumps({"previous": _PREV, "next": _NEXT})
# The same bar with the plan's earlier days still on it, so it has to scroll.
_FROM_PREV = [dict(d, isPast=False) for d in _CUR]


@_needs_node
def test_on_the_plans_last_day_tomorrows_tile_opens_in_view():
    out = _reveal(
        f"build(planDayBarHtml({json.dumps(_FROM_PREV)}, null, {_CUR_NEXT}));\n"
        "planDayBarReveal(steps);\n"
        "console.log(JSON.stringify({ today: visible('2026-10-31'), tomorrow: visible('2026-11-01'),"
        " scrolled: bar.scrollLeft > 0 }));"
    )
    assert out == {"today": True, "tomorrow": True, "scrolled": True}


@_needs_node
def test_a_tapped_tile_stays_under_the_thumb_across_the_redraw():
    # The bar scrolled to show Nov 3, which is tapped: the next plan's
    # redraw (a different set of tiles) keeps Nov 3 where it was.
    nxt_days = [{"date": f"2026-11-0{d}", "isPast": False, "isToday": False} for d in range(1, 8)]
    prev_as_adj = {"weekly_plan_id": 7, "status": "approved",
                   "days": [{"date": d["date"], "is_past": d["isPast"], "is_today": d["isToday"]} for d in _CUR]}
    out = _reveal(
        f"build(planDayBarHtml({json.dumps(_FROM_PREV)}, null, {_CUR_NEXT}));\n"
        "bar.scrollLeft = 700;\n"
        "var before = bar.querySelector('[data-date=\"2026-11-03\"]').getBoundingClientRect().left;\n"
        "weekState.planBarAnchor = { date: '2026-11-03', x: before };\n"
        f"build(planDayBarHtml({json.dumps(nxt_days)}, 2, {json.dumps({'previous': prev_as_adj, 'next': None})}));\n"
        "planDayBarReveal(steps);\n"
        "var after = bar.querySelector('[data-date=\"2026-11-03\"]').getBoundingClientRect().left;\n"
        "console.log(JSON.stringify({ before: before, after: after, anchor: weekState.planBarAnchor }));"
    )
    assert out["after"] == out["before"]
    assert out["anchor"] is None, "used once"


def test_the_draft_pin_still_lets_go_on_approval_but_a_day_bar_pin_does_not():
    src = _extract("loadWeekMenu", SHELL_JS)
    assert "data.status === 'approved' &&\n          !weekState.showPlanKeep" in src
    # ...and a kept pin is that day's look: overnight, Plan is its own again.
    assert "weekState.showPlanKeep && weekState.showPlanOn !== todayLocalStr()" in src
    nxt = _extract("planNextWeek", SHELL_JS)
    assert "weekState.showPlanKeep = false;" in nxt
