"""
The two public pages say the right date (defect hunt, 2026-10-22).

* The share link heads a period with ITS first day. week_start_date is only
  the Monday a plan is filed under, so a Thursday-to-Wednesday period (or a
  mid-week first week) read "Week of Monday, Oct 19" over a menu that
  starts on Thursday the 22nd.
* The member's own page shows when a note was left on the phone's clock.
  member_notes.created_at is SQLite's datetime('now') — UTC with no zone —
  and the page printed it as it came, so a 9pm note in Toronto read as 1am
  the next day.
"""
from __future__ import annotations

import datetime
import json
import os
import re

from conftest import household_today
from nodeharness import run_node

from app import tools

STATIC = os.path.join(os.path.dirname(__file__), "..", "static")


def _read(name: str) -> str:
    with open(os.path.join(STATIC, name), encoding="utf-8") as f:
        return f.read()


def _approved(start: datetime.date, *, content_start: datetime.date | None = None) -> int:
    tools.add_recipe("Thursday Tacos", [{"item": "Salt", "qty": "1 tsp"}])
    first = content_start or start
    plan_id = tools.create_weekly_plan(
        start.isoformat(),
        content_start_date=content_start.isoformat() if content_start else "",
        day_count=7 if content_start else 0,
    )["weekly_plan_id"]
    for i in range(7):
        tools.plan_meal((first + datetime.timedelta(days=i)).isoformat(), "Thursday Tacos",
                        slot="dinner", weekly_plan_id=plan_id)
    tools.approve_weekly_plan(plan_id, approved_by="Emily")
    return plan_id


def _shared(client) -> dict:
    token = tools.get_or_create_share_link()["token"]
    client.cookies.clear()
    return client.get(f"/api/share/{token}").json()


def test_a_period_that_starts_mid_week_is_headed_with_its_own_first_day(client):
    today = household_today()
    monday = today - datetime.timedelta(days=today.weekday())
    _approved(monday, content_start=today)
    plan = _shared(client)
    assert plan["week_start_date"] == monday.isoformat()
    assert plan["period_start_date"] == today.isoformat()
    assert plan["menu"][0]["date"] == today.isoformat()


def test_an_ordinary_week_still_starts_on_its_monday(client):
    today = household_today()
    monday = today - datetime.timedelta(days=today.weekday())
    _approved(monday)
    assert _shared(client)["period_start_date"] == monday.isoformat()


def test_the_share_page_heads_the_week_with_the_period_start():
    html = _read("share.html")
    line = next(l for l in html.splitlines() if "'Week of '" in l)
    assert "plan.period_start_date" in line, line


def _note_when(stamp: str, zone: str) -> str:
    html = _read("member-share.html")
    fn = re.search(r"function noteWhen\(stamp\) \{.*?\n\}\n", html, re.S).group(0)
    script = (
        f"process.env.TZ = {json.dumps(zone)};\n{fn}\n"
        f"process.stdout.write(noteWhen({json.dumps(stamp)}));\n"
    )
    out = run_node(script)
    assert out.returncode == 0, out.stderr
    return out.stdout


def test_a_note_left_at_9pm_in_toronto_reads_9pm_that_day():
    # 01:00 UTC on Oct 23 is 9:00 PM on Oct 22 in Toronto (EDT).
    assert _note_when("2026-10-23 01:00:00", "America/Toronto") == "Oct 22, 9:00 PM"


def test_a_note_with_no_stamp_shows_nothing():
    assert _note_when("", "America/Toronto") == ""


def test_the_page_never_prints_the_raw_stamp():
    assert "escapeHtml(n.created_at" not in _read("member-share.html")
