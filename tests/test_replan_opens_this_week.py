"""
Re-plan opens this week's dates and speaks to me as me (Emily, 2026-09-27).

What she saw: on the draft Sun Sep 27–Fri Oct 2, a tap in the band opened
"This week so far" with Days "Sat 3 → Fri 9 · 7 days" and "Emily started
this 22 hours ago — I've carried on from where they got to." — said to
Emily, about her own answers.

Reproduced on a throwaway database (2026-09-27): the Re-plan pill opens
/plan-week?week=2026-09-27&days=6 — the draft's own period — every time,
even after a part-way "Plan the week" save for Oct 3. The screen she
describes is exactly what the band's OTHER button opens: "Plan the week"
(until that morning "Plan next week") plans the period after the draft,
Oct 3–9, and with yesterday's part-way answers for Oct 3 in flight the
page titled it "This week so far" and narrated her own answers back in
the third person. So the fixes are on that page:

  * the in-flight line says "You started this …" to the person who did
    (the prefill now says who is looking — `viewer`), the other adult's
    name to anyone else; the "Same as last week?" echo copies it;
  * a period that hasn't begun names its dates ("Oct 3–9 so far"), not
    "This week so far".

And Re-plan's own road is pinned so it stays the draft's dates.

The page-level tests fail on main 8e046a6 (no joinedLine, no sameTitle,
no `viewer` on the prefill).
"""
from __future__ import annotations

import json
import shutil
import nodeharness
from pathlib import Path

import pytest

from app import agent, tools

REPO = Path(__file__).resolve().parent.parent
PAGE = (REPO / "static" / "plan-week.html").read_text(encoding="utf-8")
SHELL_JS = (REPO / "static" / "shell.js").read_text(encoding="utf-8")

from test_same_as_last_week import _extract  # noqa: E402
from test_week_seven_tiles import _extract as _extract_js  # noqa: E402

_needs_node = pytest.mark.skipif(shutil.which("node") is None, reason="node is needed to run the page's own functions")


def _node(script: str):
    out = nodeharness.run_node(script).stdout
    return json.loads(out)


def _slot(date, slot, name):
    return {"date": date, "slot": slot, "meal_name": name, "is_new_recipe": True,
            "ingredients": [{"item": f"{name} stuff", "qty": "2 lb", "category": "pantry"}],
            "reasoning": f"{name} because", "food_groups": ["protein", "vegetable", "carb"],
            "prep_time_minutes": 10, "cook_time_minutes": 20}


@pytest.fixture
def adults():
    ids = {}
    for name in ("Emily", "Vineeth"):
        ids[name] = tools.add_member(name)["member_id"]
        tools.set_member_age_group(name, "adult")
    return ids


def _sign_in_as(client, member_id):
    res = client.post("/login", data={"password": "test-password", "next": "/"}, follow_redirects=False)
    assert res.status_code == 303
    assert client.post("/api/whoami/pick", json={"member_id": member_id}).status_code == 200


# ---------- the server: who is looking ----------

@pytest.mark.today("2026-09-27 10:00")
def test_the_prefill_says_who_is_looking_and_the_draft_keeps_its_own_period(client, adults, monkeypatch):
    _sign_in_as(client, adults["Emily"])
    dates = ["2026-09-27", "2026-09-28", "2026-09-29", "2026-09-30", "2026-10-01", "2026-10-02"]
    monkeypatch.setattr(agent, "generate_weekly_plan_llm",
                        lambda ctx: [_slot(d, s, f"{s} {d}") for d in dates for s in ("breakfast", "lunch", "dinner")])
    saved = tools.save_week_intake("2026-09-27", moods=["comfort"], created_by="Emily", day_count=6)
    agent.generate_weekly_plan("2026-09-27", day_count=6, intake_id=saved["intake_id"])
    # "Plan the week" on the draft, answered part-way and left.
    assert client.post("/api/week/2026-10-03/intake", json={"day_count": 7, "moods": ["quick"]}).status_code == 200

    menu = client.get("/api/week-menu").json()
    # What Re-plan reads (replanWeek): the draft's own period.
    assert (menu["period_start_date"], menu["day_count"]) == ("2026-09-27", 6)
    # What "Plan the week" reads: the period after it.
    assert menu["next_period"]["start_date"] == "2026-10-03"

    this_week = client.get("/api/week/2026-09-27/intake?day_count=6").json()
    assert this_week["in_flight"] is False and this_week["intake"]["week_start"] == "2026-09-27"
    next_week = client.get("/api/week/2026-10-03/intake?day_count=7").json()
    assert next_week["in_flight"] is True
    assert next_week["intake"]["created_by"] == "Emily"
    assert next_week["viewer"] == "Emily"


def test_the_prefill_viewer_is_the_other_adult_when_they_are_looking(client, adults):
    _sign_in_as(client, adults["Vineeth"])
    assert client.get("/api/week/2026-10-03/intake?day_count=7").json()["viewer"] == "Vineeth"


# ---------- the page ----------

@_needs_node
def test_the_in_flight_line_speaks_to_the_person_reading_it():
    out = _node(_extract("joinedLine") + """
console.log(JSON.stringify({
  mine: joinedLine('Emily', 'Emily', 'yesterday'),
  mineAnyCase: joinedLine('emily ', 'Emily', '22 hours ago'),
  theirs: joinedLine('Vineeth', 'Emily', '3 hours ago'),
  nobody: joinedLine('', 'Emily', 'yesterday'),
  unknownViewer: joinedLine('Vineeth', '', 'just now')
}));""")
    assert out["mine"] == "You started this yesterday. Your answers are still here."
    assert out["mineAnyCase"] == "You started this 22 hours ago. Your answers are still here."
    assert out["theirs"] == "Vineeth started this 3 hours ago. Their answers are still here."
    assert out["nobody"] == "The answers saved earlier are still here."
    assert out["unknownViewer"] == "Vineeth started this just now. Their answers are still here."


def test_the_page_uses_it_and_the_echo_copies_it():
    fetch = _extract("fetchPeriod")
    assert "joinedLine(data.intake.created_by, data.viewer || who," in fetch
    assert "I’ve carried on from where they got to" not in PAGE
    show = _extract("showSame")
    assert "$('same-joined').textContent = $('joined').textContent;" in show


@_needs_node
def test_a_period_that_has_not_begun_names_its_dates():
    out = _node(_extract("sameTitle") + """
console.log(JSON.stringify({
  next: sameTitle({ intake: {}, week_label: 'Oct 3–9' }, '2026-10-03', '2026-09-27'),
  current: sameTitle({ plan_exists: true, week_label: 'Sep 27–Oct 2' }, '2026-09-27', '2026-09-27'),
  first: sameTitle({ week_label: 'Oct 3–9' }, '2026-10-03', '2026-09-27')
}));""")
    assert out == {"next": "Oct 3–9 so far", "current": "This week so far", "first": "Same as last week?"}


def test_replan_opens_the_draft_on_screen_by_its_own_period():
    replan = _extract_js("replanWeek", SHELL_JS)
    assert "var dayCount = data.day_count ||" in replan
    assert "var start = data.period_start_date || data.week_start_date ||" in replan
    assert "next_period" not in replan
