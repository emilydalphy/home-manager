"""
The shop line across the turn of the week (defect hunt 2026-10-31, branch
overnight/hunt-sunday-1031). Two defects, one walk.

1. Two live weeks' lines for one thing are one item wherever the list is
counted, as they are one row on Shop.

Driven over the weekend Oct 31 - Nov 2: week Oct 26 - Nov 1 approved and
shopped except the beans for Sunday's chili, next week Nov 2 - 8 approved on
Saturday and wanting beans too. Shop drew "Beans · 3 tins this week · 2 tins
next week" as one row (week_lines.stamp_week_groups) — four rows in all —
while Today said "Shop for tonight · 5 items", its Shop line counted 5, and
the approval receipt said "one list of 5 ingredients". Every count read
list_grocery_list's raw lines; they now go through week_lines.as_shop_rows.

2. Sunday night knows Monday's shop. The shop rule weighed only the meals of
the plan covering today, so on a week's last evening a Monday dinner in next
week's plan waiting on the list was never "Shop before tomorrow", as every
other evening's tomorrow is (moves._meals_through_tomorrow).
"""
from __future__ import annotations

import datetime

from conftest import household_pin
from app import agent, tools

THIS_WEEK = "2026-10-26"
NEXT_WEEK = "2026-11-02"


def _recipes():
    tools.add_recipe("Chili", ingredients=[{"item": "beans", "qty": "1 tin"}, {"item": "onion", "qty": "1"}],
                     prep_time_minutes=10, cook_time_minutes=20)
    tools.add_recipe("Katsu", ingredients=[{"item": "chicken thighs", "qty": "1 lb"}],
                     prep_time_minutes=10, cook_time_minutes=20)
    tools.add_recipe("Tacos", ingredients=[{"item": "tortillas", "qty": "1 pack"}],
                     prep_time_minutes=10, cook_time_minutes=20)


def _days(start, dinners):
    return [
        {"date": day, "slot": slot, "is_new_recipe": False, "reasoning": "fits",
         "meal_name": dinners[i % len(dinners)] if slot == "dinner" else "Toast"}
        for i, day in enumerate(tools.period_dates(start, 7))
        for slot in tools.WEEK_SLOTS
    ]


def _approve(client, monkeypatch, start, dinners):
    monkeypatch.setattr(agent, "generate_weekly_plan_llm", lambda context: _days(start, dinners))
    plan = client.post(f"/api/week/{start}/generate", json={"day_count": 7}).json()
    res = client.post(f"/api/week/{start}/approve", json={"weekly_plan_id": plan["weekly_plan_id"]})
    assert res.status_code == 200, res.text
    return plan["weekly_plan_id"]


def _shop_rows(client):
    """What Shop draws: every needed line, a week group once."""
    seen, rows = set(), 0
    for section in client.get("/api/grocery-list").json()["sections"]:
        for it in section["items"]:
            group = it.get("week_group")
            key = ("g", group["lead_id"]) if group else ("i", it["id"])
            if key not in seen:
                seen.add(key)
                rows += 1
    return rows


def _two_weeks_live(client, monkeypatch, frozen_today):
    frozen_today(household_pin(10, on=datetime.date(2026, 10, 25)))
    _recipes()
    # Chili Mon/Thu/Sun this week — Sunday Nov 1 is chili night.
    _approve(client, monkeypatch, THIS_WEEK, ["Chili", "Katsu", "Tacos"])
    for section in client.get("/api/grocery-list").json()["sections"]:
        for it in section["items"]:
            if it["item"] != "beans":
                client.post(f"/api/grocery-list/{it['id']}/status", json={"status": "purchased"})
    frozen_today(household_pin(10, on=datetime.date(2026, 10, 31)))
    _approve(client, monkeypatch, NEXT_WEEK, ["Tacos", "Chili", "Katsu"])


def _shop_move(payload):
    return next(m for m in payload["moves"] if m["kind"] == "shop")


def test_sunday_today_counts_the_rows_shop_shows(signed_in, monkeypatch, frozen_today):
    """CATCH: red before the fix — Today said 5 items over four Shop rows."""
    _two_weeks_live(signed_in, monkeypatch, frozen_today)
    frozen_today(household_pin(16, on=datetime.date(2026, 11, 1)))

    rows = _shop_rows(signed_in)
    payload = signed_in.get("/api/today/moves").json()
    move = _shop_move(payload)

    assert rows == 4  # onion, chicken thighs, beans (both weeks), tortillas
    assert move["title"] == "Shop for tonight"
    assert move["detail"].startswith(f"{rows} items")
    assert sum(stop["count"] for stop in move["stops"]) == rows
    assert payload["shop"]["count"] == rows
    # Tonight's chili is still what the row names.
    assert move["stops"][0]["items"] == ["beans"]


def test_saturday_receipt_counts_the_rows_shop_shows(signed_in, monkeypatch, frozen_today):
    """CATCH: red before the fix — "one list of 5 ingredients"."""
    _two_weeks_live(signed_in, monkeypatch, frozen_today)

    menu = signed_in.get("/api/week-menu").json()
    assert menu["week_start_date"] == THIS_WEEK
    assert "one list of 4 ingredients" in menu["receipt"]["title"]
    assert _shop_rows(signed_in) == 4


def test_sunday_night_says_shop_before_tomorrow_for_next_weeks_monday(signed_in, monkeypatch, frozen_today):
    """CATCH: red before the fix — no shop move at all on Sunday night."""
    _two_weeks_live(signed_in, monkeypatch, frozen_today)
    frozen_today(household_pin(20, on=datetime.date(2026, 11, 1)))
    # Sunday's beans bought: only next week's lines are left, and nothing
    # today is waiting on them.
    beans = next(it for section in signed_in.get("/api/grocery-list").json()["sections"]
                 for it in section["items"] if it["item"] == "beans")
    this_weeks = next(p["id"] for p in beans["week_group"]["parts"] if p["label"] == "this week")
    signed_in.post(f"/api/grocery-list/{this_weeks}/status", json={"status": "purchased"})

    move = _shop_move(signed_in.get("/api/today/moves").json())

    assert move["title"] == "Shop before tomorrow"
    assert move["time_label"] == "by tomorrow"
    # Monday is Tacos: the row names what Monday waits on.
    assert move["stops"][0]["items"] == ["tortillas"]
    assert move["detail"].startswith(f"{_shop_rows(signed_in)} items")


def test_a_row_is_tonights_when_next_weeks_line_under_it_is(signed_in, monkeypatch, frozen_today):
    """EDGE: Sunday's chili cooked, so the waiting cook is Monday's chili —
    next week's beans line, which is not the row's lead. The row still names
    beans and the count is still the rows."""
    frozen_today(household_pin(10, on=datetime.date(2026, 10, 25)))
    _recipes()
    this_week = _approve(signed_in, monkeypatch, THIS_WEEK, ["Chili", "Katsu", "Tacos"])
    frozen_today(household_pin(10, on=datetime.date(2026, 10, 31)))
    # Next week opens on chili: Monday's dinner waits on next week's beans.
    _approve(signed_in, monkeypatch, NEXT_WEEK, ["Chili", "Katsu", "Tacos"])
    frozen_today(household_pin(19, 30, on=datetime.date(2026, 11, 1)))
    sunday = next(m for m in tools.get_weekly_plan(this_week)["meals"]
                  if m["date"] == "2026-11-01" and m["slot"] == "dinner")
    assert signed_in.post("/api/cooker/check-meal",
                          json={"entry_id": sunday["entry_id"], "status": "done"}).status_code == 200

    payload = signed_in.get("/api/today/moves").json()
    move = _shop_move(payload)

    assert move["title"] == "Shop before tomorrow"
    assert move["detail"].startswith(f"{_shop_rows(signed_in)} items")
    assert "beans" in move["stops"][0]["items"]
